"""Background culling jobs (M1) — run curate.curate in a worker thread and expose status so the
Studio UI can poll. Copy-only: originals are never moved/renamed/deleted. In-memory registry,
mirroring web/jobs.py for the edit batch path.

Curation is one scan+select+copy pass (no natural per-item progress), so a job is simply
running → done | error, carrying the manifest (selected count, scanned total, mode, items) when
finished.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass


@dataclass
class CurateJob:
    id: str
    input_dir: str
    out_dir: str
    opts: dict
    status: str = "running"          # running | done | error
    manifest: dict | None = None
    error: str = ""
    created: float = 0.0


_JOBS: dict[str, CurateJob] = {}
_LOCK = threading.Lock()


def default_out_dir(input_dir: str) -> str:
    return os.path.join(input_dir or os.getcwd(), "AutoEdit Cull")


def start(input_dir: str, out_dir: str | None, opts: dict) -> CurateJob:
    job = CurateJob(id=uuid.uuid4().hex[:12], input_dir=input_dir,
                    out_dir=out_dir or default_out_dir(input_dir), opts=dict(opts),
                    created=time.time())
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def _run(job: CurateJob) -> None:
    try:
        from .service import curate_run  # local import avoids importing the heavy edit pipeline

        job.manifest = curate_run(job.input_dir, job.out_dir, job.opts)
        job.status = "done"
    except Exception as e:
        job.status, job.error = "error", str(e)


def get(job_id: str) -> CurateJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def snapshot(job: CurateJob) -> dict:
    m = job.manifest or {}
    items = m.get("items", [])
    return {
        "id": job.id,
        "status": job.status,
        "out_dir": job.out_dir,
        "error": job.error,
        "mode": job.opts.get("mode", "diverse"),
        "selected": m.get("selected", 0),
        "total_scanned": m.get("total_scanned", 0),
        "contact_sheet": os.path.join(job.out_dir, "contact-sheet.html") if job.status == "done" else None,
        "items_truncated": len(items) > 400,   # so the UI can say "400 of N" not just "400"
        "items": [
            {"source": it.get("source_path"), "thumb": it.get("thumb_path"),
             "bucket": it.get("bucket", ""), "rank": it.get("rank")}
            for it in items[:400]
        ],
    }
