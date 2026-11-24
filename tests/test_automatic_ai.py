"""Hands-off exports use the same bounded photo/region suggestions as Guided."""
import json
import time
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from autoedit.decide import gemini
from autoedit.web import ai_styles, assist, jobs, service


def _photo(path):
    ramp = np.tile(np.arange(150, 230, dtype=np.uint8), (64, 1))
    pixels = np.stack([ramp - 30, ramp, ramp + 15], axis=-1)
    pixels[32:] //= 2
    Image.fromarray(pixels).save(path)
    return str(path)


def _wait(job):
    deadline = time.monotonic() + 30
    while job.status in ("running", "paused") and time.monotonic() < deadline:
        time.sleep(.01)
    assert job.status == "done" and all(i.out for i in job.items), jobs.snapshot(job)


def test_automatic_ai_requires_specific_consent_before_starting(tmp_path):
    file = _photo(tmp_path / "one.png")
    for opts in ({"ai_edit": {}}, {"ai_edit": {"allow_cloud": 1}},
                 {"local_only": True, "ai_edit": {"allow_cloud": True}},
                 {"ai_edit": {"allow_cloud": True, "style": "missing"}},
                 {"ai_edit": {"allow_cloud": True, "gemini_api_key": 100}}):
        with pytest.raises(ValueError):
            jobs.start(str(tmp_path), [file], str(tmp_path / "out"), opts)
    assert not (tmp_path / "out").exists()


def test_automatic_ai_applies_per_photo_deltas_and_finishes_locally_after_budget(tmp_path):
    files = [_photo(tmp_path / (name + ".png")) for name in ("first", "second", "private", "last")]
    service.approve_root(str(tmp_path))
    sent = []
    key = "ephemeral-test-key"
    style = "blue_hour"

    def transport(body, header_key):
        assert header_key == key and key.encode() not in body and str(tmp_path).encode() not in body
        request = json.loads(body)
        text = request["contents"][0]["parts"][0]["text"]
        assert ai_styles.prompt_for(style) in text
        names = request["generationConfig"]["responseJsonSchema"]["properties"]["changes"]["items"]["properties"]["target"]["enum"]
        assert "sky" in names
        sent.append(request)
        changes = [{"label": "Open shadows", "why": "Lift the subject into the light.",
                    "target": "photo", "ops": {"exposure": .6}},
                   {"label": "Hold the sky", "why": "Keep color in bright clouds.",
                    "target": "sky", "ops": {"highlights": -8}}] if len(sent) == 1 else []
        proposal = {"changes": changes, "explanation": "Preserve the light."}
        return json.dumps({"candidates": [{"finishReason": "STOP", "content":
                           {"parts": [{"text": json.dumps(proposal)}]}}]}).encode()

    base = {"backend": "stub", "concepts": ["sky"], "out_format": "png", "compose": False, "consistent": False}
    own = {files[0]: {"photo_settings": {"exposure": -.1}}, files[2]: {"local_only": True}}
    options = {**base, "local_only": False, "decider": "llm", "allow_cloud": True,
               "ai_edit": {"style": style, "allow_cloud": True, "gemini_api_key": key}}
    client = gemini.Gemini(transport=transport, budget=gemini.Budget(max_calls=2))
    with patch.object(assist, "_CLIENT", client):
        job = jobs.start(str(tmp_path), files, str(tmp_path / "ai"), options, photo_options=own)
        _wait(job)
    items = jobs.snapshot(job)["items"]
    assert [item["ai_status"] for item in items] == ["applied", "unchanged", "fallback", "fallback"], items
    assert "local" in items[2]["ai_reason"] and "limit" in items[3]["ai_reason"]
    assert len(sent) == 2
    assert "ai_edit" not in job.opts and key not in repr(job)
    assert job.opts["decider"] == "rules" and not job.opts["allow_cloud"] and job.opts["translator"] == "off"

    typed = {files[0]: {"photo_settings": {"exposure": .5}, "region_settings": {"sky": {"highlights": -8}}}}
    with patch.object(assist, "suggest", side_effect=AssertionError("local runs do not request AI")) as call:
        local = jobs.start(str(tmp_path), files, str(tmp_path / "expected"), base, photo_options=typed)
        _wait(local)
        call.assert_not_called()
    assert all("ai_status" not in row for row in jobs.snapshot(local)["items"])
    for name in ("first", "second", "private", "last"):
        assert (tmp_path / "ai" / (name + ".png")).read_bytes() == (tmp_path / "expected" / (name + ".png")).read_bytes()
        assert key not in (tmp_path / "ai" / (name + ".autoedit.json")).read_text()
    assert (tmp_path / "ai/first.png").read_bytes() != (tmp_path / "ai/second.png").read_bytes()
    first = json.loads((tmp_path / "ai/first.autoedit.json").read_text())
    assert first["audit"]["gemini_edit"] == {"style": style, "status": "applied"}
    assert any("sky.highlights -8" in n for n in first["notes"])
    assert any("Keep color in bright clouds" in n for n in first["notes"])
    assert any("local edit" in n for n in json.loads((tmp_path / "ai/last.autoedit.json").read_text())["notes"])


def test_automatic_ai_provider_error_exports_baseline_without_credentials(tmp_path):
    file = _photo(tmp_path / "photo.png")
    service.approve_root(str(tmp_path))
    key = "sensitive-test-key"
    with patch.object(assist, "suggest", side_effect=RuntimeError(key)):
        job = jobs.start(str(tmp_path), [file], str(tmp_path / "out"),
                         {"backend": "stub", "ai_edit": {"allow_cloud": True, "gemini_api_key": key}})
        _wait(job)
    snapshot = jobs.snapshot(job)
    assert snapshot["items"][0]["ai_status"] == "fallback" and snapshot["failed"] == 0
    assert key not in repr(snapshot) and key not in (tmp_path / "out/photo.autoedit.json").read_text()
