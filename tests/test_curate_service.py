"""M1 culling backend: service.curate_run wraps curate.curate (copy-only), and the background
job registry runs it and reports a manifest. No FastAPI import — core stays dependency-light."""

import os
import time
import tempfile

import numpy as np
from PIL import Image

from autoedit.web import curate_jobs, service


def _corpus(n=6):
    d = tempfile.mkdtemp()
    rng = np.random.default_rng(0)
    for i in range(n):
        a = (rng.uniform(0.1, 0.9, (64, 96, 3)) * 255).astype("uint8")
        Image.fromarray(a, "RGB").save(os.path.join(d, f"shot_{i:02d}.jpg"), quality=90)
    return d


def test_curate_run_copies_subset_and_writes_manifest():
    src = _corpus(6)
    out = tempfile.mkdtemp()
    before = set(os.listdir(src))
    m = service.curate_run(src, out, {"mode": "diverse", "count": 3})

    assert m["total_scanned"] == 6
    assert m["selected"] <= 3 and m["selected"] >= 1
    assert os.path.isdir(os.path.join(out, "selected"))
    assert os.path.isfile(os.path.join(out, "contact-sheet.html"))
    assert os.path.isfile(os.path.join(out, "candidates.json"))
    # copy-only: every original is still present and untouched
    assert set(os.listdir(src)) == before
    assert len(os.listdir(os.path.join(out, "selected"))) == m["selected"]


def test_scan_limit_and_best_mode():
    src = _corpus(8)
    out = tempfile.mkdtemp()
    m = service.curate_run(src, out, {"mode": "best", "count": 2, "scan_limit": 4})
    assert m["total_scanned"] == 4          # scan_limit honored
    assert m["selected"] <= 2


def test_curate_job_lifecycle():
    src = _corpus(5)
    out = tempfile.mkdtemp()
    job = curate_jobs.start(src, out, {"mode": "diverse", "count": 2})
    for _ in range(100):                    # poll up to ~5s
        if curate_jobs.get(job.id).status != "running":
            break
        time.sleep(0.05)
    snap = curate_jobs.snapshot(curate_jobs.get(job.id))
    assert snap["status"] == "done", snap.get("error")
    assert snap["selected"] <= 2 and snap["total_scanned"] == 5
    assert snap["contact_sheet"] and os.path.isfile(snap["contact_sheet"])
    assert all("source" in it for it in snap["items"])


def test_curate_job_reports_error_on_bad_dir():
    job = curate_jobs.start("/no/such/folder", None, {"mode": "diverse"})
    for _ in range(60):
        if curate_jobs.get(job.id).status != "running":
            break
        time.sleep(0.05)
    snap = curate_jobs.snapshot(curate_jobs.get(job.id))
    # empty/missing input → either an error status or a zero-selection done; never a crash
    assert snap["status"] in ("done", "error")
    assert snap["selected"] == 0
