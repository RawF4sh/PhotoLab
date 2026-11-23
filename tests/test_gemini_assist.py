"""Gemini's transport and suggestion contract, verified without a network call or real key."""

import base64
import copy
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
from PIL import Image

from autoedit.contract import GlobalStatsModel, MaskOps, RegionStatsModel, Result
from autoedit.decide import gemini
from autoedit.web import assist, service


def _jpeg(size=(400, 250), metadata=False):
    image = Image.new("RGB", size, (120, 145, 170))
    exif = Image.Exif()
    if metadata:
        exif[270] = "private-photo-name"
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def _response(proposal=None, **kwargs):
    proposal = proposal or {"global": {"exposure": .2}, "regions": {"sky": {"highlights": -12}},
                            "explanation": "Lift the foreground while preserving the sky."}
    return json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(proposal)}]}}],
                       **kwargs}).encode()


def _request(client, **overrides):
    args = {"thumbnail": _jpeg(), "prompt": "Make this more natural", "context": {"measurements": {"mean_lum": .4}},
            "bounds": service.editor_sliders()[0], "names": ["sky"], "api_key": "test-session-key", "allow_cloud": True}
    args.update(overrides)
    return client.suggest(**args)


def _raises(kind, function, reason=None):
    try:
        function()
    except kind as error:
        if reason:
            assert error.reason == reason
        return error
    raise AssertionError(f"expected {kind.__name__}")


def test_gemini_body_contains_only_bounded_image_context_and_schema():
    calls = []
    def transport(body, key):
        calls.append((body, key))
        return _response()
    client = gemini.Gemini(transport=transport)
    answer = _request(client, thumbnail=_jpeg(metadata=True))
    assert answer["status"] == "ok" and answer["proposal"]["global"] == {"exposure": .2}
    body, key = calls[0]
    assert key == "test-session-key" and key.encode() not in body
    assert b"private-photo-name" not in body and b"fileData" not in body and b'"tools"' not in body
    data = json.loads(body)
    config = data["generationConfig"]
    assert config["maxOutputTokens"] == 2048 and config["candidateCount"] == 1
    assert config["thinkingConfig"]["thinkingLevel"] == "MINIMAL"
    assert config["responseJsonSchema"]["properties"]["regions"]["additionalProperties"] is False
    image_part = data["contents"][0]["parts"][1]["inlineData"]
    with Image.open(io.BytesIO(base64.b64decode(image_part["data"]))) as image:
        assert max(image.size) <= 512 and not image.getexif()
    assert client.budget.status()["calls"] == 1


def test_gemini_requires_consent_key_and_small_inputs_before_transport():
    calls = []
    client = gemini.Gemini(transport=lambda *args: calls.append(args))
    for value in (False, None, 1, "true"):
        _raises(gemini.Unavailable, lambda: _request(client, allow_cloud=value), "consent")
    for value in (None, "", "bad\nheader", "bad key"):
        _raises(gemini.Unavailable, lambda: _request(client, api_key=value), "missing_key")
    _raises(ValueError, lambda: _request(client, thumbnail=_jpeg((1024, 800))))
    _raises(ValueError, lambda: _request(client, context={"source_path": "/private/photo.jpg"}))
    _raises(ValueError, lambda: _request(client, prompt="x" * 1201))
    assert calls == [] and client.budget.status()["calls"] == 0


def test_gemini_rejects_unknown_nonfinite_and_oversized_proposals():
    valid = {"global": {"exposure": .2}, "regions": {}, "explanation": "A small change."}
    invalid = []
    for op_value in ({"exposure": True}, {"exposure": float("nan")}, {"exposure": float("inf")},
                     {"exposure": 2.01}, {"unknown": 2}, {"temp": "4"}):
        invalid.append({**valid, "global": op_value})
    invalid.extend([{**valid, "regions": {"imaginary object": {"temp": 5}}},
                    {**valid, "extra": 1}, {**valid, "explanation": "x" * 601},
                    {**valid, "regions": []}])
    for proposal in invalid:
        client = gemini.Gemini(transport=lambda *_, p=proposal: _response(p))
        _raises(gemini.Unavailable, lambda: _request(client), "invalid_response")
    duplicated = b'{"candidates":[],"candidates":[]}'
    for response in (duplicated, b"x" * (gemini.MAX_RESPONSE + 1), b'{"promptFeedback":{"blockReason":"SAFETY"}}',
                     b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}'):
        _raises(gemini.Unavailable, lambda: _request(gemini.Gemini(transport=lambda *_: response)), "invalid_response")


def test_gemini_caps_calls_and_reserves_failed_requests():
    count = []
    def failed(*_):
        count.append(1)
        raise TimeoutError("secret test-session-key must not be reported")
    client = gemini.Gemini(budget=gemini.Budget(max_calls=1), transport=failed)
    error = _raises(gemini.Unavailable, lambda: _request(client), "timeout")
    assert "test-session-key" not in str(error)
    assert client.budget.status()["reserved_usd"] > 0
    _raises(gemini.Unavailable, lambda: _request(client), "budget")
    assert len(count) == 1
    tiny = gemini.Gemini(budget=gemini.Budget(limit_usd=.001), transport=failed)
    _raises(gemini.Unavailable, lambda: _request(tiny), "budget")
    assert tiny.budget.status()["calls"] == 0 and len(count) == 1


def test_gemini_concurrent_requests_share_one_call_limit():
    client = gemini.Gemini(budget=gemini.Budget(max_calls=2), transport=lambda *_: _response())
    def attempt(_):
        try:
            return _request(client)["status"]
        except gemini.Unavailable as error:
            return error.reason
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    assert results.count("ok") == 2 and results.count("budget") == 3
    assert client.budget.status()["calls"] == 2


def test_gemini_fixed_https_endpoint_header_and_response_size():
    made = []
    class Socket:
        def settimeout(self, value):
            assert 0 < value <= gemini.TIMEOUT
    class Response:
        status = 200
        def getheader(self, name):
            return None
        def read1(self, size):
            chunk = self.content[:size]
            self.content = self.content[size:]
            return chunk
    class Connection:
        def __init__(self, host, timeout):
            assert host == gemini.HOST and timeout == 30
            self.sock = Socket(); self.closed = False; self.response = Response()
            self.response.content = b"x" * (gemini.MAX_RESPONSE + 1)
            made.append(self)
        def connect(self):
            pass
        def request(self, method, path, body, headers):
            assert method == "POST" and path == gemini.ENDPOINT and "?" not in path
            assert headers["x-goog-api-key"] == "test-key" and body == b"{}"
        def getresponse(self):
            return self.response
        def close(self):
            self.closed = True
    with patch.object(gemini.http.client, "HTTPSConnection", Connection):
        _raises(gemini.Unavailable, lambda: gemini._transport(b"{}", "test-key"), "invalid_response")
        assert made[0].closed
        with patch.object(Response, "status", 302):
            _raises(gemini.Unavailable, lambda: gemini._transport(b"{}", "test-key"), "provider")
        assert len(made) == 2 and made[1].closed


def _render(path):
    stats = GlobalStatsModel(mean_lum=.4, mean_sat=.2, shadow_clip=.01, highlight_clip=.01, haze=.1)
    result = Result(image=str(path), scene="private scene text", confidence=.8, global_stats=stats,
                    masks=[MaskOps(target="sky", ops={"highlights": -10}, coverage=.4),
                           MaskOps(target="missing", ops={}, coverage=0),
                           MaskOps(target="mark:area:0,0,1,1", ops={}, coverage=.1)],
                    region_stats=[RegionStatsModel(name="sky", lum=.7, sat=.2, clip_low=0, clip_high=.01, coverage=.4)],
                    notes=["private note"], global_settings={"exposure": .2})
    return {"out": np.full((600, 900, 3), .5, dtype=np.float32), "result": result,
            "source_overlays": [{"name": "sky", "coverage": .4, "alpha": "local-mask-not-for-upload"},
                                {"name": "missing", "coverage": 0, "alpha": ""}]}


def test_assist_sends_local_edited_thumbnail_without_paths_or_metadata(tmp_path):
    file = tmp_path / "private-source.jpg"; file.write_bytes(_jpeg())
    reference = tmp_path / "private-reference.jpg"; reference.write_bytes(_jpeg())
    service.approve_root(str(tmp_path))
    sent = []
    def transport(body, key):
        sent.append(body)
        return _response()
    client = gemini.Gemini(transport=transport)
    original = {"assist_prompt": "Warm the sky", "gemini_api_key": "test-session-key", "allow_cloud": True,
                "decider": "llm", "api_key": "private-anthropic-key", "translator": "local", "reference": str(reference),
                "photo_settings": {"temp": 3}, "region_settings": {"sky": {"highlights": -4}}}
    opts = copy.deepcopy(original)
    def local(path, options, *_):
        assert path == str(file) and options["decider"] == "rules" and options["allow_cloud"] is False
        assert options["translator"] == "off" and "gemini_api_key" not in options and "api_key" not in options
        assert options["region_settings"] == original["region_settings"]
        return _render(file)
    with patch.object(assist, "_CLIENT", client), patch.object(service, "_edit", local):
        answer = assist.suggest(str(file), opts)
    assert opts == original and answer["status"] == "ok"
    body = sent[0]
    for private in (str(file), str(reference), "private scene text", "private note", "mark:area", "local-mask-not-for-upload", "private-anthropic-key", "test-session-key"):
        assert private.encode() not in body
    payload = json.loads(body)
    assert list(payload["generationConfig"]["responseJsonSchema"]["properties"]["regions"]["properties"]) == ["sky"]
    with Image.open(io.BytesIO(base64.b64decode(payload["contents"][0]["parts"][1]["inlineData"]["data"]))) as image:
        assert image.size == (512, 341) and not image.getexif()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["private-reference.jpg", "private-source.jpg"]


def test_assist_missing_consent_key_or_local_failure_keeps_edit_usable(tmp_path):
    file = tmp_path / "photo.jpg"; file.write_bytes(_jpeg())
    service.approve_root(str(tmp_path))
    client = gemini.Gemini(transport=lambda *_: (_ for _ in ()).throw(AssertionError("must not call network")))
    with patch.object(assist, "_CLIENT", client), patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
            patch.object(service, "_edit", side_effect=RuntimeError("private path and key")) as render:
        opts = {"assist_prompt": "Improve this"}
        assert assist.suggest(str(file), opts)["reason"] == "consent"
        assert assist.suggest(str(file), {**opts, "allow_cloud": True})["reason"] == "missing_key"
        assert render.call_count == 0
        result = assist.suggest(str(file), {**opts, "allow_cloud": True, "gemini_api_key": "test-key"})
        assert result["reason"] == "local_preview" and result["proposal"] is None
        assert "private" not in result["message"] and client.budget.status()["calls"] == 0


def test_assist_route_returns_reviewable_proposal_without_export(tmp_path):
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return
    from autoedit import paths
    from autoedit.web.app import create_app
    file = tmp_path / "photo.jpg"; file.write_bytes(_jpeg())
    service.approve_root(str(tmp_path))
    provider = gemini.Gemini(transport=lambda *_: _response())
    app = TestClient(create_app(paths.configs_dir()), base_url="http://localhost:7000")
    with patch.object(assist, "_CLIENT", provider), patch.object(service, "_edit", return_value=_render(file)):
        denied = app.post("/api/v1/assist", json={"file": str(file), "assist_prompt": "Improve this"})
        assert denied.json()["reason"] == "consent" and provider.budget.status()["calls"] == 0
        answer = app.post("/api/v1/assist", json={"file": str(file), "assist_prompt": "Improve this",
                                                  "allow_cloud": True, "gemini_api_key": "test-key"})
        assert answer.status_code == 200 and answer.json()["proposal"]["global"] == {"exposure": .2}
    assert [p.name for p in tmp_path.iterdir()] == ["photo.jpg"]


def test_assist_real_local_stub_render_needs_no_cloud_decider(tmp_path):
    file = tmp_path / "photo.jpg"; file.write_bytes(_jpeg())
    service.approve_root(str(tmp_path))
    proposal = {"global": {"temp": 2}, "regions": {}, "explanation": "Add a little warmth."}
    provider = gemini.Gemini(transport=lambda *_: _response(proposal))
    opts = {"assist_prompt": "Warm this slightly", "allow_cloud": True, "gemini_api_key": "test-key",
            "backend": "stub", "concepts": [], "compose": False, "decider": "llm", "translator": "off"}
    with patch.object(assist, "_CLIENT", provider):
        answer = assist.suggest(str(file), opts)
    assert answer["status"] == "ok" and answer["proposal"] == proposal
    assert opts["decider"] == "llm" and [p.name for p in tmp_path.iterdir()] == ["photo.jpg"]
