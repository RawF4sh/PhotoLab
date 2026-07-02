"""Background batch jobs — threaded per-photo runner with live status (stub backend)."""

import os
import time

import numpy as np
from PIL import Image as PILImage

from autoedit.web import jobs


def _img(p):
    a = np.zeros((48, 48, 3), np.uint8)
    a[:24] = [100, 140, 200]   # sky-ish top → stub detects sky → landscape scene
    a[24:] = [90, 110, 80]
    PILImage.fromarray(a, "RGB").save(str(p))


def _wait(job, timeout=20):
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job


def test_job_runs_to_done_and_writes_outputs(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    for n in ("a.jpg", "b.jpg"):
        _img(d / n)
    out = tmp_path / "out"
    job = jobs.start(str(d), None, str(out), {"backend": "stub", "style": "natural"})
    _wait(job)
    snap = jobs.snapshot(job)
    assert snap["status"] == "done"
    assert snap["total"] == 2 and snap["processed"] == 2 and snap["failed"] == 0
    assert snap["done"] + snap["review"] == 2
    for it in snap["items"]:                         # outputs written + recorded
        assert it["out"] and os.path.exists(it["out"])
    assert os.path.isdir(str(out))


def test_job_respects_output_format(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    out = tmp_path / "out"
    job = jobs.start(str(d), None, str(out), {"backend": "stub", "out_format": "tiff"})
    _wait(job)
    snap = jobs.snapshot(job)

    assert snap["status"] == "done"
    assert snap["items"][0]["out"].endswith(".tif")
    assert os.path.exists(snap["items"][0]["out"])


def test_job_isolates_a_bad_image(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "good.jpg")
    (d / "bad.jpg").write_bytes(b"not an image")    # supported extension, undecodable
    job = jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"})
    _wait(job)
    snap = jobs.snapshot(job)
    assert snap["status"] == "done" and snap["total"] == 2
    assert snap["failed"] == 1                       # bad.jpg fails, good.jpg succeeds
    assert any(i["status"] == "failed" and i["error"] for i in snap["items"])


def test_start_on_empty_dir_raises(tmp_path):
    import pytest

    d = tmp_path / "empty"; d.mkdir()
    with pytest.raises(ValueError):                  # was a silent 0-item "done"
        jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"})


def test_control_is_noop_on_finished_job(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    job = jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"})
    _wait(job)
    assert job.status == "done"
    jobs.control(job, "pause")                       # must not resurrect a finished batch
    assert job.status == "done"
    jobs.control(job, "resume")
    assert job.status == "done"


def test_registry_prunes_terminal_jobs(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    saved = dict(jobs._JOBS)
    try:
        jobs._JOBS.clear()
        for _ in range(jobs._JOB_CAP + 5):
            _wait(jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"}))
        assert len(jobs._JOBS) <= jobs._JOB_CAP      # oldest terminal jobs evicted
    finally:
        jobs._JOBS.clear()
        jobs._JOBS.update(saved)
