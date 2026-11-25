"""Forum collection, retention and independent screening gates, using only invented pictures."""
import base64
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import auto_pairs as pairs


class Network:
    def __init__(self):
        self.data = {}
        self.calls = []

    def open(self, url):
        self.calls.append(url)
        stream = io.BytesIO(self.data[url])
        stream.headers = {"Content-Length": str(len(self.data[url]))}
        return stream


def approval(count):
    return {"max_pairs": count, "max_bytes": 1_000_000, "sources": {"forum": ["forum.example"]},
            "screening": {"requested": "gemini", "eligible": True}}


def plan(net, count):
    rows = []
    for i in range(count):
        view = np.random.default_rng(i).integers(10, 180, (10, 10, 3), dtype=np.uint8)
        arr = np.concatenate([view, view + 10], axis=1)
        url = f"https://forum.example/edit{i}.png"
        blob = io.BytesIO()
        Image.fromarray(arr).save(blob, "PNG")
        net.data[url] = blob.getvalue()
        rows.append({"source": "forum", "scene": "mist", "page": "https://forum.example/edits",
                     "files": [{"url": url, "licence": "CC BY 4.0", "role": "horizontal",
                                "licence_url": "https://creativecommons.org/licenses/by/4.0/",
                                "author": "Invented test contributor"}]})
    return rows


def test_forum_pair_budget_and_reuse_rights_before_download(tmp_path):
    net = Network()
    entries = plan(net, 2)
    invalid = json.loads(json.dumps(entries))
    invalid[1]["files"][0]["licence"] = "research only"
    with pytest.raises(ValueError, match="licence"):
        pairs.collect(tmp_path / "pairs", invalid, approval(2), {"forum": net})
    assert net.calls == []
    root = tmp_path / "pairs"
    pairs.collect(root, entries, approval(1), {"forum": net})
    pairs.collect(root, entries, approval(1), {"forum": net})
    assert len(net.calls) == 1 and len(pairs.public_pairs(root)) == 1
    report = pairs.read(root / "fetch-report.json")
    assert report["pairs_started"] == 1 and report["bytes_downloaded"] == len(next(iter(net.data.values())))
    row = pairs.web.manifest(root)[0]
    (root / row["file"]).write_bytes(b"a different photograph")
    with pytest.raises(ValueError, match="differs"):
        pairs.screen(root, lambda _: None)


def test_pair_screen_resumes_and_stale_blind_audit_is_rejected(tmp_path):
    root = tmp_path / "pairs"
    net = Network()
    pairs.collect(root, plan(net, 65), approval(65), {"forum": net})
    answers = []
    def judge(images):
        assert len(images) == 2 and all(image.shape == (10, 10, 3) for image in images)
        result = {"scene": "mist", "quality": "good" if len(answers) < 45 else "weak",
                  "unedited": True, "same_photo": True}
        answers.append(result)
        return result
    assert pairs.screen(root, judge) == {"mist": 45}
    assert pairs.screen(root, judge) == {"mist": 45}
    assert len(answers) == 65
    with pytest.raises(ValueError, match="independent audit"):
        pairs.eligible(root, "mist")
    pairs.audit(root, "mist")
    directory = root / "audit/mist"
    blind = pairs.read(directory / "blank.json")
    form = (directory / "review.html").read_text()
    embedded = form.split('<script id="template" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded) == {field: blind[field] for field in ("screen_digest", "sample")}
    data = pairs.read(root / "screen.json")
    for i, key in enumerate(blind["sample"], 1):
        blind["answers"][str(i)] = data[key]["answer"]
    decisions = directory / "checker.json"
    pairs.save(decisions, blind)
    assert pairs.audit(root, "mist", decisions)["passed"]
    assert len(pairs.eligible(root, "landscape")) == len(pairs.eligible(root, "mist")) == 45
    for i in [i for i, val in blind["answers"].items() if val["quality"] == "good"][:4]:
        blind["answers"][i]["same_photo"] = False
    pairs.save(decisions, blind)
    assert pairs.audit(root, "mist", decisions)["kept_errors"] == 4
    with pytest.raises(ValueError, match="independent audit"):
        pairs.eligible(root, "mist")
    blind["screen_digest"] = "from another run"
    pairs.save(decisions, blind)
    with pytest.raises(ValueError, match="different screening"):
        pairs.audit(root, "mist", decisions)


def test_screening_transmits_only_stripped_public_thumbnails_and_validates_model_reply():
    pixels = np.zeros((600, 640, 3), np.float32)
    expected = {"scene": "daylight", "quality": "good", "unedited": True, "same_photo": True}
    def respond(payload, key):
        parts = json.loads(payload)["contents"][0]["parts"]
        assert len(parts) == 3 and key == "invented-key"
        for p in parts[1:]:
            img = Image.open(io.BytesIO(base64.b64decode(p["inlineData"]["data"])))
            assert max(img.size) <= 512 and img.format == "JPEG" and not img.getexif()
        return json.dumps({"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": json.dumps(expected)}]}}]}).encode()
    with patch.object(pairs.gemini, "_transport", respond):
        assert pairs.gemini_screen([pixels, pixels], "invented-key") == expected
    with pytest.raises(ValueError, match="free-tier"):
        pairs.gemini_screen([pixels, pixels], None)
    with pytest.raises(ValueError, match="invalid screening"):
        pairs.judgment(dict(expected, unedited="yes"))


def test_local_vision_judges_the_original_alone_before_comparing_stripped_thumbnails():
    digest = "a" * 64
    expected = {"scene":"mist", "quality":"good", "unedited":True, "same_photo":True}
    calls, attachments = [], []
    appearance = {"appearance":"camera_rendering", "evidence":"Low-contrast cloud detail."}
    info = {"capabilities": ["vision"], "model_info":{"general.parameter_count": 9_000_000_000}}
    def route(path, payload=None):
        calls.append(path)
        if path == "/api/tags":
            return {"models": [{"name": pairs.LOCAL_MODEL, "digest": digest}]}
        if path == "/api/show":
            return info
        assert path == "/api/chat" and payload["model"] == pairs.LOCAL_MODEL
        assert payload["stream"] is False
        message = payload["messages"][0]
        images = message["images"]
        attachments.append(images)
        for data in images:
            im = Image.open(io.BytesIO(base64.b64decode(data)))
            assert im.format == "JPEG" and max(im.size) <= 512 and not im.getexif()
        if payload["format"] == pairs.ORIGINAL_SCHEMA:
            assert message["content"] == pairs.ORIGINAL_PROMPT and len(images) == 1
            answer = appearance
        else:
            assert payload["format"] == pairs.SCREEN_SCHEMA and message["content"] == pairs.PROMPT
            assert len(images) == 2 and attachments[-2] == images[:1] and images[0] != images[1]
            answer = dict(expected, unedited=False)
        return {"done":True, "done_reason":"stop", "model":pairs.LOCAL_MODEL,
                "message":{"content":json.dumps(answer)}}
    rgb = np.zeros((4, 7, 3), np.float32)
    with patch.object(pairs, "local_request", route):
        for original in pairs.APPEARANCES:
            appearance["appearance"] = original
            result = pairs.local_screen([rgb, rgb + .4], digest)
            assert result == dict(expected, unedited=original == "camera_rendering", original_appearance=appearance)
        assert calls == ["/api/tags", "/api/show", "/api/chat"] * 6
        with pytest.raises(ValueError, match="changed"):
            pairs.local_screen([rgb, rgb], "b" * 64)
        info["remote_host"] = "elsewhere.example"
        with pytest.raises(ValueError, match="on this computer"):
            pairs.local_screen([rgb, rgb], digest)
        assert calls.count("/api/chat") == 6


def test_approved_local_ledger_has_its_own_audit_and_eligibility(tmp_path):
    root = tmp_path / "pairs"
    net = Network()
    settings = approval(65)
    settings["screening"] = {"requested":"local", "eligible":True}
    pairs.collect(root, plan(net, 65), settings, {"forum": net})
    answers = []
    def judge(images):
        result = {"scene": "mist", "same_photo": True, "unedited": True,
                  "quality": "good" if len(answers) < 45 else "weak"}
        answers.append(result)
        return dict(result, original_appearance={"appearance":"camera_rendering","evidence":"Normal local contrast."})
    kwargs = {"provider":"local", "weights":"a" * 64}
    with pytest.raises(ValueError, match="owner-approved"):
        pairs.screen(root, judge)
    assert pairs.screen(root, judge, limit=8, **kwargs) == {"mist":8}
    assert pairs.screen(root, judge, **kwargs) == {"mist":45}
    assert pairs.screen(root, judge, **kwargs) == {"mist":45}
    assert len(answers) == 65 and not (root / "screen.json").exists()
    records = pairs.read(root / "screen-local.json")
    assert all(r["provider"] == "local" and r["weights"] == kwargs["weights"] for r in records.values())
    assert all(r["original_appearance"]["appearance"] == "camera_rendering" for r in records.values())
    key = next(iter(records))
    for invalid in (None, {"appearance": "indeterminate", "evidence": "Insufficient detail."}):
        pairs.save(root / "screen-local.json", {**records, key: {**records[key], "original_appearance": invalid}})
        assert key not in pairs.screenings(root, pairs.public_pairs(root))
    pairs.save(root / "screen-local.json", records)
    pairs.audit(root, "mist")
    blank = pairs.read(root / "audit-local/mist/blank.json")
    blank["answers"] = {str(i):records[k]["answer"] for i,k in enumerate(blank["sample"],1)}
    decisions = root / "labels.json"
    pairs.save(decisions, blank)
    result = pairs.audit(root, "mist", decisions)
    assert result["provider"] == "local" and result["passed"] and len(pairs.eligible(root, "mist")) == 45
    assert not (root / "audit/mist/result.json").exists()
    pairs.screen(root, judge, provider="local", weights="b"*64, limit=1)
    with pytest.raises(ValueError, match="unscreened"):
        pairs.eligible(root,"mist")
    settings["screening"]["eligible"] = False
    pairs.save(root / "approval.json", settings)
    with pytest.raises(ValueError, match="owner-approved"):
        pairs.screen(root, judge, **kwargs)
