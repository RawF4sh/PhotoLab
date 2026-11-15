"""Follow-up edits (F19): per-photo instruction clauses on top of the edit, in the preview, the
batch and `autoedit adjust`. Stub backend, so no GPU or network."""

import base64
import io
import json
import os
import time

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust
from autoedit.web import jobs, service

OPTS = {"backend": "stub", "style": "natural"}


def _img(p, gain=1.0):
    a = np.zeros((64, 64, 3), np.float32)
    a[:32] = [100, 140, 200]
    a[32:] = [90, 110, 80]
    PILImage.fromarray(np.clip(a * gain, 0, 255).astype(np.uint8), "RGB").save(str(p), quality=95)


def _mean(url):
    raw = base64.b64decode(url.split(",", 1)[1])
    return float(np.asarray(PILImage.open(io.BytesIO(raw)).convert("RGB"), np.float32).mean())


def _preview(p, **extra):
    return service.preview(str(p), {**OPTS, **extra})


def _after(res):
    return _mean(res["after"])


def _wait(job, timeout=30):
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job


def test_preview_applies_a_follow_up(tmp_path):
    _img(tmp_path / "a.jpg")
    base = _preview(tmp_path / "a.jpg")
    res = _preview(tmp_path / "a.jpg", photo_edit="brighten the photo")
    assert _after(res) > _after(base)
    adj, = res["result"]["adjustments"]
    assert adj["scope"] == "photo" and adj["status"] == "applied" and adj["source"] == "brighten the photo"
    assert res["instruction_problems"] == []


def test_follow_ups_add_up(tmp_path):
    _img(tmp_path / "a.jpg")
    one = _after(_preview(tmp_path / "a.jpg", photo_edit="brighten the photo"))
    two = _after(_preview(tmp_path / "a.jpg", photo_edit="brighten the photo. brighten the photo"))
    assert two > one
    many = _preview(tmp_path / "a.jpg", photo_edit=". ".join(["brighten the photo"] * 5))
    # five clauses add up to more than the 0.75 EV ceiling: the sum stops there
    assert "clamped" in {a["status"] for a in many["result"]["adjustments"]}


def test_run_wide_and_photo_edits_combine(tmp_path):
    _img(tmp_path / "a.jpg")
    res = _preview(tmp_path / "a.jpg", edit="warm the photo", photo_edit="brighten the photo")
    got = {(a["scope"], a["status"]) for a in res["result"]["adjustments"]}
    assert got == {("run", "applied"), ("photo", "applied")}


def test_not_understood_is_reported(tmp_path):
    _img(tmp_path / "a.jpg")
    base = _preview(tmp_path / "a.jpg")
    res = _preview(tmp_path / "a.jpg", photo_edit="make it pop like a movie")
    assert any("make it pop like a movie" in p for p in res["instruction_problems"])
    assert res["after"] == base["after"]


def test_batch_uses_photo_edits(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    for n in ("a.jpg", "b.jpg"):
        _img(d / n)
    files = [str(d / "a.jpg"), str(d / "b.jpg")]

    def run(out, **kw):
        job = _wait(jobs.start(str(d), files, str(tmp_path / out), dict(OPTS), **kw))
        assert job.status == "done" and all(i.status != "failed" for i in job.items), [i.error for i in job.items]
        return [i.out for i in job.items]

    plain = run("plain")
    edited = run("edited", photo_edits={files[0]: "brighten the photo"})
    raw = lambda p: open(p, "rb").read()          # noqa: E731
    assert raw(plain[0]) != raw(edited[0])
    assert raw(plain[1]) == raw(edited[1])
    side = json.load(open(os.path.splitext(edited[0])[0] + ".autoedit.json"))
    assert [(a["scope"], a["source"]) for a in side["adjustments"]] == [("photo", "brighten the photo")]


def test_photo_edits_are_validated(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _img(d / "a.jpg")
    f = str(d / "a.jpg")
    bad = [{str(d / "other.jpg"): "brighten the photo"}, {f: "x" * 2001}, {f: 5},
           {f + str(i): "brighten the photo" for i in range(10_001)}, ["brighten"]]
    for pe in bad:
        with pytest.raises(ValueError):
            jobs.start(str(d), [f], str(tmp_path / "o"), dict(OPTS), photo_edits=pe)
    with pytest.raises(ValueError):
        _preview(f, photo_edit="x" * 2001)

    try:
        from fastapi.testclient import TestClient
    except Exception:
        return                                   # no [ui] extra: the API half cannot run
    from autoedit import paths
    from autoedit.web import app as webapp
    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service.approve_root(str(tmp_path))
    try:
        for pe in bad[:3]:
            r = client.post("/api/v1/jobs", json={**OPTS, "files": [f], "out_dir": str(tmp_path / "o"),
                                                  "photo_edits": pe})
            assert r.status_code == 400 and r.json()["code"] == "bad_request", (pe, r.text)
    finally:
        service._ALLOWED_ROOTS.clear()


def test_adjust_keeps_the_follow_ups(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _img(d / "a.jpg")
    src = str(d / "a.jpg")
    job = _wait(jobs.start(str(d), [src], str(tmp_path / "out"), dict(OPTS),
                           photo_edits={src: "brighten the photo"}))
    assert job.status == "done", job.items[0].error
    out = job.items[0].out
    before = open(out, "rb").read()
    res = adjust.rerender(out, source=src)
    assert [a["scope"] for a in res["adjustments"]] == ["photo"]
    assert open(out, "rb").read() == before
