"""Background aesthetic-critic training jobs (M4) — feature extraction over a labelled image set
can take a while, so training runs in a worker thread the UI polls. In-memory registry, mirroring
curate_jobs. Read-only on the image set; only writes the model JSON the user asked for.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass


@dataclass
class TrainJob:
    id: str
    opts: dict
    status: str = "running"          # running | done | error
    result: dict | None = None
    error: str = ""
    created: float = 0.0


_JOBS: dict[str, TrainJob] = {}
_LOCK = threading.Lock()


def start(opts: dict) -> TrainJob:
    job = TrainJob(id=uuid.uuid4().hex[:12], opts=dict(opts), created=time.time())
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def _run(job: TrainJob) -> None:
    try:
        from .service import aesthetic_train

        job.result = aesthetic_train(job.opts)
        job.status = "done"
    except Exception as e:
        job.status, job.error = "error", str(e)


def get(job_id: str) -> TrainJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def snapshot(job: TrainJob) -> dict:
    r = job.result or {}
    return {"id": job.id, "status": job.status, "error": job.error,
            "model_path": r.get("model_path"), "samples": r.get("samples", 0),
            "skipped": r.get("skipped", 0)}
