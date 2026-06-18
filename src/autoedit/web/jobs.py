"""Background batch jobs — run the pipeline per-photo in a worker thread and expose live
progress so the UI can poll. In-memory registry (single-process uvicorn).

A job builds the Pipeline once (one model load), then iterates photos: editing → finished /
review / failed, writing the baked image + sidecar to an output folder. Per-image failures are
isolated (one bad photo never aborts the job). Pause/resume/cancel are checked between photos.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass

from .. import emit
from ..imageio import is_supported


def _list_images(d: str) -> list[str]:
    if not d or not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if os.path.isfile(os.path.join(d, n)) and is_supported(n)]


@dataclass
class Item:
    name: str
    src: str
    status: str = "waiting"          # waiting | editing | finished | review | failed
    out: str | None = None
    scene: str = ""
    warn: str = ""
    error: str = ""
    ms: float = 0.0


@dataclass
class Job:
    id: str
    out_dir: str
    opts: dict
    items: list[Item]
    status: str = "running"          # running | paused | done | cancelled | error
    cur: int = 0
    paused: bool = False
    cancelled: bool = False
    avg_ms: float = 0.0
    created: float = 0.0


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _safety(result) -> tuple[str, str]:
    """Heuristic 'needs review' flag from global stats — clipping is the common risk."""
    g = getattr(result, "global_stats", None)
    if g is None:
        return "finished", ""
    if getattr(g, "highlight_clip", 0.0) > 0.06:
        return "review", "highlight clipping"
    if getattr(g, "shadow_clip", 0.0) > 0.12:
        return "review", "shadow clipping"
    return "finished", ""


def start(input_dir, files, out_dir, opts, configs_dir=None) -> Job:
    srcs = list(files) if files else _list_images(input_dir)
    if not out_dir:
        base = input_dir or (os.path.dirname(srcs[0]) if srcs else os.getcwd())
        out_dir = os.path.join(base, "AutoEdit Exports")
    job = Job(id=uuid.uuid4().hex[:12], out_dir=out_dir, opts=dict(opts),
              items=[Item(name=os.path.basename(s), src=s) for s in srcs], created=time.time())
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, configs_dir), daemon=True).start()
    return job


def _run(job: Job, configs_dir) -> None:
    try:
        from .service import INFERENCE_LOCK, build_pipeline, make_cfg  # local import avoids a cycle

        cfg = make_cfg(job.opts, configs_dir)
        os.makedirs(job.out_dir, exist_ok=True)
        pipe = build_pipeline(cfg)       # resident model, shared with the preview endpoint
        durs: list[float] = []
        for i, item in enumerate(job.items):
            if job.cancelled:
                job.status = "cancelled"
                return
            while job.paused and not job.cancelled:
                time.sleep(0.15)
            if job.cancelled:
                job.status = "cancelled"
                return
            job.cur = i
            item.status = "editing"
            t0 = time.time()
            try:
                with INFERENCE_LOCK:
                    out_rgb, result = pipe.process_one(item.src, do_apply=True)
                image_out, sidecar = emit.output_paths(job.out_dir, item.src, cfg.out_format)
                emit.write_image(out_rgb, image_out, cfg.out_format, cfg.quality)
                emit.write_sidecar(result, sidecar)
                item.out, item.scene = image_out, result.scene
                item.status, item.warn = _safety(result)
            except Exception as e:       # isolate per-image failures
                item.status, item.error = "failed", str(e)
            item.ms = (time.time() - t0) * 1000.0
            durs.append(item.ms)
            job.avg_ms = sum(durs) / len(durs)
        job.status = "done"
    except Exception as e:               # model load / config error → fail the whole job
        job.status = "error"
        for it in job.items:
            if it.status in ("waiting", "editing"):
                it.status, it.error = "failed", str(e)


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def control(job: Job, action: str) -> None:
    if action == "pause":
        job.paused, job.status = True, "paused"
    elif action == "resume":
        job.paused, job.status = False, "running"
    elif action == "cancel":
        job.cancelled = True


def snapshot(job: Job) -> dict:
    done = sum(1 for i in job.items if i.status == "finished")
    review = sum(1 for i in job.items if i.status == "review")
    failed = sum(1 for i in job.items if i.status == "failed")
    processed = done + review + failed
    remaining = len(job.items) - processed
    eta = int(remaining * job.avg_ms / 1000.0) if job.avg_ms else None
    return {
        "id": job.id, "status": job.status, "out_dir": job.out_dir,
        "total": len(job.items), "processed": processed,
        "done": done, "review": review, "failed": failed,
        "current": job.cur, "eta_s": eta,
        "items": [{"name": i.name, "status": i.status, "src": i.src, "out": i.out,
                   "scene": i.scene, "warn": i.warn, "error": i.error} for i in job.items],
    }
