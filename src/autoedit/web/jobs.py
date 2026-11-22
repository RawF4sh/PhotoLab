"""Background batch jobs — run the pipeline per-photo in a worker thread and expose live
progress so the UI can poll. In-memory registry (single-process uvicorn).

A job builds the Pipeline once (one model load), then iterates photos: editing → finished /
review / failed, writing the baked image + sidecar to an output folder. Per-image failures are
isolated (one bad photo never aborts the job). Pause/resume/cancel are checked between photos.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass

from .. import consistency, emit
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
    photo_edits: dict | None = None  # abspath of a photo -> its follow-up text (F19)
    photo_marks: dict | None = None  # abspath of a photo -> its checked marks (F37)


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()
_JOB_CAP = 50                        # keep the registry from growing without bound over a session


def _prune_locked() -> None:
    """Evict the oldest terminal jobs once the registry exceeds the cap. Running/paused jobs are
    never dropped. Caller must hold _LOCK."""
    if len(_JOBS) <= _JOB_CAP:
        return
    terminal = sorted((j for j in _JOBS.values() if j.status in ("done", "cancelled", "error")),
                      key=lambda j: j.created)
    for j in terminal[:len(_JOBS) - _JOB_CAP]:
        _JOBS.pop(j.id, None)


def _safety(result) -> tuple[str, str]:
    """'Needs review' verdict from the post-edit gate (measured on the EDITED output, not the
    source). The pipeline already recomputes output stats + a quality score; trust that."""
    if getattr(result, "needs_review", False):
        return "review", getattr(result, "review_reason", "") or "quality gate"
    # fallback for results that predate the post-edit gate
    post = getattr(result, "post_edit_stats", None) or getattr(result, "global_stats", None)
    if post is not None:
        if getattr(post, "highlight_clip", 0.0) > 0.06:
            return "review", "highlight clipping"
        if getattr(post, "shadow_clip", 0.0) > 0.12:
            return "review", "shadow clipping"
    return "finished", ""


MAX_PHOTO_EDITS = 10_000            # entries in one job's photo_edits map (F19)


def _check_photo_edits(photo_edits, srcs) -> dict[str, str]:
    """Validate the per-photo follow-up map: {path of one of the job's photos: text}."""
    if photo_edits in (None, {}):
        return {}
    from .service import MAX_PHOTO_EDIT
    if not isinstance(photo_edits, dict):
        raise ValueError("photo_edits must be an object of path to text")
    if len(photo_edits) > MAX_PHOTO_EDITS:
        raise ValueError(f"photo_edits has more than {MAX_PHOTO_EDITS} entries")
    known = {os.path.abspath(s) for s in srcs}
    out: dict[str, str] = {}
    for path, text in photo_edits.items():
        if not isinstance(text, str):
            raise ValueError("photo_edits values must be text")
        if len(text) > MAX_PHOTO_EDIT:
            raise ValueError(f"photo_edits text is longer than {MAX_PHOTO_EDIT} characters")
        if not isinstance(path, str) or os.path.abspath(path) not in known:
            raise ValueError(f"photo_edits names a photo that is not in the job: {path}")
        if text.strip():
            out[os.path.abspath(path)] = text
    return out


def _check_photo_marks(photo_marks, srcs, opts, configs_dir, profile) -> dict[str, list]:
    """Validate the per-photo marks map: {path of one of the job's photos: list of marks} (F37)."""
    if photo_marks in (None, {}):
        return {}
    from .. import instructions
    from .service import check_marks, make_cfg
    if not isinstance(photo_marks, dict):
        raise ValueError("photo_marks must be an object of path to marks")
    if len(photo_marks) > MAX_PHOTO_EDITS:
        raise ValueError(f"photo_marks has more than {MAX_PHOTO_EDITS} entries")
    known = {os.path.abspath(s) for s in srcs}
    vocab = instructions.load_vocab(make_cfg(opts, configs_dir, profile).instructions_path)
    out: dict[str, list] = {}
    for path, marks in photo_marks.items():
        if not isinstance(path, str) or os.path.abspath(path) not in known:
            raise ValueError(f"photo_marks names a photo that is not in the job: {path}")
        checked = check_marks(marks, vocab)
        if checked:
            out[os.path.abspath(path)] = checked
    return out


def start(input_dir, files, out_dir, opts, configs_dir=None, profile=None, photo_edits=None,
          photo_marks=None) -> Job:
    # An omitted selection means scan the folder; an explicit [] means no photos selected.
    srcs = list(files) if files is not None else _list_images(input_dir)
    if not srcs:                     # empty/bad dir → surface an error (was a silent 0-item "done")
        raise ValueError("no supported images found to process")
    edits = _check_photo_edits(photo_edits, srcs)
    marks = _check_photo_marks(photo_marks, srcs, opts, configs_dir, profile)
    if not out_dir:
        base = input_dir or os.path.dirname(srcs[0])
        out_dir = os.path.join(base, "AutoEdit Exports")
    job = Job(id=uuid.uuid4().hex[:12], out_dir=out_dir, opts=dict(opts),
              items=[Item(name=os.path.basename(s), src=s) for s in srcs], created=time.time())
    job.photo_edits = edits
    job.photo_marks = marks
    with _LOCK:
        _JOBS[job.id] = job
        _prune_locked()
    threading.Thread(target=_run, args=(job, configs_dir, profile), daemon=True).start()
    return job


def _note_input_dir(out_dir: str, srcs: list[str]) -> None:
    """Write a minimal run.json (just `input_dir`) so `adjust`, `sync` and `declare` find the sources of a
    UI batch, as they do for a CLI run. Never replaces a run.json that is there; skipped when the photos
    come from more than one folder."""
    path = os.path.join(out_dir, "run.json")
    dirs = {os.path.dirname(os.path.abspath(s)) for s in srcs}
    if len(dirs) != 1 or os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"input_dir": dirs.pop(), "written_by": "autoedit ui"}, fh, indent=2)


def _run(job: Job, configs_dir, profile=None) -> None:
    try:
        from .service import INFERENCE_LOCK, build_pipeline, make_cfg, mark_adjustments, photo_adjustments  # local import avoids a cycle

        cfg = make_cfg(job.opts, configs_dir, profile)
        os.makedirs(job.out_dir, exist_ok=True)
        _note_input_dir(job.out_dir, [it.src for it in job.items])
        pipe = build_pipeline(cfg)       # resident model, shared with the preview endpoint
        pipe.use_mask_cache(job.out_dir)

        # Shoot consistency (default on): same plan as the CLI batch path.
        consistent = bool(job.opts.get("consistent", True))
        biases = {}
        if consistent:
            with INFERENCE_LOCK:         # bursts mode runs the model on the frames it compares
                biases = consistency.plan([it.src for it in job.items], cfg, pipe)

        durs: list[float] = []
        taken: set[str] = set()          # de-dupe same-stem outputs across the job's source list
        sources = emit.source_ids(it.src for it in job.items)   # no export may land on an input
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
                bias = biases.get(item.src, consistency.Bias())
                key = os.path.abspath(item.src)
                photo_adjs, _ = photo_adjustments(pipe, (job.photo_edits or {}).get(key))
                photo_adjs += mark_adjustments(pipe, (job.photo_marks or {}).get(key, []))[0]
                with INFERENCE_LOCK:
                    out_rgb, result = pipe.process_one(item.src, do_apply=True, exposure_bias=bias.exposure,
                                                       batch_group=bias.group, wb_bias=(bias.temp, bias.tint),
                                                       adjustments=photo_adjs or None)
                image_out, sidecar = emit.output_paths(
                    job.out_dir, item.src, cfg.out_format, taken, name=cfg.out_name,
                    subdir=cfg.out_subdir, fields={"style": cfg.style, "n": i + 1},
                    sources=sources)
                emit.write_outputs(out_rgb, result, image_out, sidecar, cfg, item.src)
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
    # Controls only apply to a live job. Acting on a done/cancelled/error job would flip its
    # status back to "paused"/"running" forever (the worker has already exited and can't correct
    # it), so the UI would poll a finished batch as eternally running.
    if job.status not in ("running", "paused"):
        return
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
