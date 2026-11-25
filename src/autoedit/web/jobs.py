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
from dataclasses import dataclass, field

from .. import consistency, emit
from ..imageio import is_supported
from . import edit_steps


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
    quality_warn: str = ""
    object_checks: dict[str, bool] = field(default_factory=dict)
    ai_status: str = ""
    ai_reason: str = ""
    auto_shoot: dict | None = None
    reopen: dict = field(default_factory=dict)


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
    photo_hide: dict | None = None   # abspath of a photo -> {"hide": [kinds], "hide_method": m}: what the Hide control asked for (F22)
    default_change_notes: tuple = ()
    photo_change_notes: dict | None = None
    photo_options: dict | None = None  # persisted look, neutral/reference and slider offsets per photo


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


def _check_photo_hide(photo_hide, srcs, opts, configs_dir, profile) -> dict[str, dict]:
    """Validate the per-photo hide map {path of one of the job's photos: {"hide": [kinds], "hide_method": m}} (F22).
    The kinds are the whole list for that photo (the run's own included); the job's options apply to the other photos.
    A bad kind or method refuses the job here, not half way through it."""
    if photo_hide in (None, {}):
        return {}
    from .. import reveal
    from .service import make_cfg
    if not isinstance(photo_hide, dict):
        raise ValueError("photo_hide must be an object of path to {hide, hide_method}")
    if len(photo_hide) > MAX_PHOTO_EDITS:
        raise ValueError(f"photo_hide has more than {MAX_PHOTO_EDITS} entries")
    known = {os.path.abspath(s) for s in srcs}
    out: dict[str, dict] = {}
    for path, req in photo_hide.items():
        if not isinstance(path, str) or os.path.abspath(path) not in known:
            raise ValueError(f"photo_hide names a photo that is not in the job: {path}")
        if not isinstance(req, dict) or set(req) - {"hide", "hide_method"}:
            raise ValueError("photo_hide values are objects with hide and hide_method")
        cfg = make_cfg({**opts, **req}, configs_dir, profile)          # checks the names and the method
        if cfg.hide:
            reveal.check_hide(reveal.load(cfg.reveal_path), cfg.hide)
        out[os.path.abspath(path)] = {k: v for k, v in req.items() if v is not None}
    return out


def _check_photo_options(value, srcs, opts, configs_dir, profile, table=None):
    from . import photo_base
    from .service import check_photo_settings, check_region_settings, editor_sliders, ensure_allowed, make_cfg
    if value is None:
        return {}, {}
    if table is None:
        table, _ = editor_sliders()
    if not isinstance(value, dict) or len(value) > MAX_PHOTO_EDITS:
        raise ValueError("photo_options must be an object with at most 10000 photos")
    known = {os.path.abspath(s) for s in srcs}
    allowed = {"style", "scene", "look", "intensity", "neutral", "reference", "reference_strength", "photo_settings", "photo_base", "hide", "hide_method",
               "region_settings", "mask_refinements", "photo_geometry", "local_only", "edit_steps"}
    checked, change_notes = {}, {}
    for path, req in value.items():
        if not isinstance(path, str) or os.path.abspath(path) not in known:
            raise ValueError("photo_options names a photo that is not in the job")
        if not isinstance(req, dict) or set(req) - allowed:
            raise ValueError("photo_options contains unsupported options")
        req = dict(req)
        if "edit_steps" in req or "edit_steps" in opts:
            # Inheritance happens before folding: overriding a manual slider or replacing
            # the folder's change list must not reuse its already folded totals.
            controls = {k: req.get(k, opts.get(k)) for k in ("photo_settings", "region_settings", "edit_steps")}
            folded, limited = edit_steps.fold(controls, table)
            req.pop("edit_steps", None)
            req.update(folded)
            change_notes[os.path.abspath(path)] = edit_steps.notes(controls["edit_steps"], limited)
        if "photo_settings" in req:
            req["photo_settings"] = check_photo_settings(req["photo_settings"])
        if "photo_base" in req:
            req["photo_base"] = photo_base.check(req["photo_base"])
        if "region_settings" in req:
            req["region_settings"] = check_region_settings(req["region_settings"])
        if req.get("reference"):
            ensure_allowed(req["reference"])
        make_cfg({**opts, **req}, configs_dir, profile)
        checked[os.path.abspath(path)] = req
    return checked, change_notes


@dataclass
class _AutomaticAI:
    """Private to one worker. The registry, export config and sidecars never receive its key."""

    style: str
    prompt: str
    api_key: str = field(repr=False)
    unavailable: str = ""

    @classmethod
    def from_request(cls, request, local_only):
        from .ai_styles import prompt_for
        if not isinstance(request, dict) or request.keys() - {"style", "allow_cloud", "gemini_api_key"}:
            raise ValueError("ai_edit must specify a style, preview-sharing permission and optionally a Gemini key")
        if request.get("allow_cloud") is not True or local_only is not False:
            raise ValueError("AI editing requires explicit preview-sharing permission and local_only must be false")
        key = request.get("gemini_api_key", "")
        if not isinstance(key, str) or len(key) > 512:
            raise ValueError("invalid Gemini API key")
        style = request.get("style", "standard")
        return cls(style, prompt_for(style), key)

    def refine(self, path, options, table, configs_dir, profile):
        """Ask about this photo's current local edit and add accepted deltas exactly once."""
        from . import assist

        reason = self.unavailable
        if options.get("local_only"):
            reason = "This photo is set to local editing only."
        if not reason:
            try:
                request = {**options, "allow_cloud": True, "assist_prompt": self.prompt, "gemini_api_key": self.api_key}
                answer = assist.suggest(path, request, configs_dir, profile)
                if answer["status"] == "ok":
                    if answer.get("budget", {}).get("remaining_calls") == 0:
                        self.unavailable = "The Gemini session request limit was reached."
                    return self._apply(options, answer["proposal"]["changes"], table)
                reason = answer["message"]
                if answer["reason"] in ("budget", "missing_key"):
                    self.unavailable = reason  # avoid preparing more thumbnails for this job
            except Exception:
                reason = "Gemini could not prepare a usable edit."
        return options, "fallback", reason, []

    @staticmethod
    def _apply(options, changes, table):
        steps = []
        for i, change in enumerate(changes):
            target, ops = change["target"], change["ops"]
            steps.append({"id": f"gemini-{i}", "label": change["label"], "origin": "suggested",
                          "on": True, "amount": 1, "global": ops if target == "photo" else {},
                          "regions": {} if target == "photo" else {target: ops}})
        updated, limited = edit_steps.fold({**options, "edit_steps": steps}, table)
        changed = any((updated.get(k) or {}) != (options.get(k) or {}) for k in ("photo_settings", "region_settings"))
        notes = edit_steps.notes(steps, limited)
        notes.extend(f"{c['label']}: {c['why']}" for c in changes if c["why"])
        return updated, "applied" if changed else "unchanged", "", notes


def start(input_dir, files, out_dir, opts, configs_dir=None, profile=None, photo_edits=None,
          photo_marks=None, photo_hide=None, photo_options=None) -> Job:
    # An omitted selection means scan the folder; an explicit [] means no photos selected.
    srcs = list(files) if files is not None else _list_images(input_dir)
    if not srcs:                     # empty/bad dir → surface an error (was a silent 0-item "done")
        raise ValueError("no supported images found to process")
    opts = dict(opts)
    ai = None
    opts.pop("gemini_api_key", None)
    opts.pop("assist_prompt", None)
    if "ai_edit" in opts:
        ai = _AutomaticAI.from_request(opts.pop("ai_edit"), opts.get("local_only", False))
        # Only the explicit suggestion call may use a cloud service in this mode.
        opts.pop("api_key", None)
        opts.update(decider="rules", allow_cloud=False, translator="off")
    # Reject invalid options and unapproved reference/LUT paths before a worker is started.
    from .service import editor_sliders, make_cfg
    table, _ = editor_sliders()
    folded, limited = edit_steps.fold(opts, table)
    default_notes = edit_steps.notes(opts.get("edit_steps", []), limited)
    make_cfg(folded, configs_dir, profile)
    edits = _check_photo_edits(photo_edits, srcs)
    marks = _check_photo_marks(photo_marks, srcs, opts, configs_dir, profile)
    hides = _check_photo_hide(photo_hide, srcs, opts, configs_dir, profile)
    options, notes = _check_photo_options(photo_options, srcs, opts, configs_dir, profile, table)
    if not out_dir:
        base = input_dir or os.path.dirname(srcs[0])
        out_dir = os.path.join(base, "AutoEdit Exports")
    job = Job(id=uuid.uuid4().hex[:12], out_dir=out_dir, opts=dict(folded),
              default_change_notes=tuple(default_notes), photo_change_notes=notes,
              items=[Item(name=os.path.basename(s), src=s) for s in srcs], created=time.time())
    job.photo_edits = edits
    job.photo_marks = marks
    job.photo_hide = hides
    job.photo_options = options
    with _LOCK:
        _JOBS[job.id] = job
        _prune_locked()
    threading.Thread(target=_run, args=(job, configs_dir, profile, ai), daemon=True).start()
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


def _run(job: Job, configs_dir, profile=None, ai=None) -> None:
    try:
        from . import photo_base, projects
        from .service import INFERENCE_LOCK, build_pipeline, make_cfg, mark_adjustments, photo_adjustments, settings_adjustments, region_adjustments  # local import avoids a cycle

        cfg = make_cfg(job.opts, configs_dir, profile)
        os.makedirs(job.out_dir, exist_ok=True)
        _note_input_dir(job.out_dir, [it.src for it in job.items])
        pipe = build_pipeline(cfg)       # resident model, shared with the preview endpoint
        pipe.use_mask_cache(job.out_dir)
        if ai:
            from .service import editor_sliders
            table, _ = editor_sliders()

        by_options: dict = {}            # distinct configurations share the resident segmenter

        def own(key):
            req = {k: v for k, v in (job.photo_options or {}).get(key, {}).items() if k != "photo_settings"}
            req.update((job.photo_hide or {}).get(key, {}))
            if not req:
                return pipe, cfg
            sig = json.dumps(req, sort_keys=True)
            if sig not in by_options:
                c = make_cfg({**job.opts, **req}, configs_dir, profile)
                p = build_pipeline(c)
                p.use_mask_cache(job.out_dir)
                by_options[sig] = (p, c)
            return by_options[sig]

        biases = {}
        if job.opts.get("consistent", True):
            # Photos with a saved Auto style or scene need the plan for those effective settings.
            # All other photos keep the existing folder-wide Simple consistency calculation.
            all_paths = [item.src for item in job.items]
            by_config = {}
            for path in all_paths:
                request = (job.photo_options or {}).get(os.path.abspath(path), {})
                if request.get("style", cfg.style) == "auto":
                    try:
                        engine, chosen = own(os.path.abspath(path))
                    except (OSError, ValueError):
                        # Report the configuration problem against this photo in the render loop.
                        continue
                    by_config.setdefault(chosen.model_dump_json(), (engine, chosen, []))[2].append(path)
            with INFERENCE_LOCK:
                auto_paths = {path for _, _, group in by_config.values() for path in group}
                if len(auto_paths) < len(all_paths):
                    normal = pipe if cfg.style != "auto" else build_pipeline(
                        make_cfg({**job.opts, "style": "standard"}, configs_dir, profile))
                    biases = consistency.plan(all_paths, normal.cfg, normal)
                for engine, chosen, group in by_config.values():
                    biases.update(consistency.plan(group, chosen, engine))

        durs: list[float] = []
        taken: set[str] = set()          # de-dupe same-stem outputs across the job's source list
        sources = emit.source_ids(it.src for it in job.items)   # no export may land on an input
        manual = set()
        for folder in {os.path.dirname(os.path.abspath(it.src)) for it in job.items}:
            manual.update(projects.manual_sources(folder, profile))
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
                projects.require_automatic_export(item.src, manual)
                bias = biases.get(item.src, consistency.Bias())
                key = os.path.abspath(item.src)
                pipe_i, cfg_i = own(key)
                opts = {**job.opts, **(job.photo_options or {}).get(key, {}), **(job.photo_hide or {}).get(key, {})}
                base = photo_base.check(opts.get("photo_base"))
                group_args = photo_base.render_args(base, cfg_i, bias)
                photo_adjs, _ = photo_adjustments(pipe_i, (job.photo_edits or {}).get(key))
                photo_adjs = photo_base.adjustments(base) + photo_adjs
                photo_adjs += mark_adjustments(pipe_i, (job.photo_marks or {}).get(key, []))[0]
                ai_status, ai_reason, ai_notes = "", "", []
                if ai:
                    # Match the exact current photo, including its own overrides and shoot bias.
                    preview_base = {**base, "exposure_bias": group_args["exposure_bias"],
                                    "wb_bias": list(group_args["wb_bias"])}
                    if group_args.get("auto_shoot"):
                        preview_base["auto_shoot"] = group_args["auto_shoot"]
                    preview = {**opts, "photo_edit": (job.photo_edits or {}).get(key),
                               "photo_marks": (job.photo_marks or {}).get(key, []),
                               "photo_base": preview_base}
                    opts, ai_status, ai_reason, ai_notes = ai.refine(item.src, preview, table, configs_dir, profile)
                    if job.cancelled:
                        item.status, job.status = "waiting", "cancelled"
                        return
                photo_adjs += settings_adjustments(opts.get("photo_settings"))
                region_offsets = opts.get("region_settings") or {}
                photo_adjs += region_adjustments(region_offsets)
                with INFERENCE_LOCK:
                    out_rgb, result = pipe_i.process_one(item.src, do_apply=True, **group_args,
                                                       adjustments=photo_adjs or None)
                if ai:
                    if ai_status == "applied" and (opts.get("photo_settings") or {}) == (preview.get("photo_settings") or {}):
                        prior = preview.get("region_settings") or {}
                        changed_regions = {target for target in prior.keys() | region_offsets.keys()
                                           if prior.get(target) != region_offsets.get(target)}
                        blocked = {adj.target for adj in result.adjustments if adj.resolved_only
                                   and adj.status in ("blocked", "not_found")}
                        if changed_regions <= blocked:
                            ai_status = "unchanged"
                    decision = f"used the local edit. {ai_reason}" if ai_status == "fallback" else ai_status
                    ai_notes.insert(0, f"Gemini ({ai.style}): {decision}")
                    result.audit["gemini_edit"] = {"style": ai.style, "status": ai_status}
                image_out, sidecar = emit.output_paths(
                    job.out_dir, item.src, cfg_i.out_format, taken, name=cfg_i.out_name,
                    subdir=cfg_i.out_subdir, fields={"style": cfg_i.style, "n": i + 1},
                    sources=sources)
                change_notes = [*(job.photo_change_notes or {}).get(key, job.default_change_notes), *ai_notes]
                result.notes.extend(change_notes)
                item.auto_shoot = photo_base.shoot_receipt(result)
                if item.auto_shoot:
                    item.reopen = {k: opts[k] for k in ("look", "reference", "reference_strength") if opts.get(k) is not None}
                emit.write_outputs(out_rgb, result, image_out, sidecar, cfg_i, item.src)
                item.out, item.scene = image_out, result.scene
                item.ai_status, item.ai_reason = ai_status, ai_reason
                item.status, item.quality_warn = _safety(result)
                item.warn = item.quality_warn
                if change_notes:
                    item.object_checks = {adj.target: adj.status != "not_found" for adj in result.adjustments
                                          if adj.resolved_only and adj.status and adj.target in region_offsets}
                    missing = sorted(name for name, found in item.object_checks.items() if not found)
                    if missing:
                        warning = "Object not found: " + ", ".join(missing)
                        item.status = "review"
                        item.warn = "; ".join(filter(None, (item.warn, warning)))
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
                   "scene": i.scene, "warn": i.warn, "quality_warn": i.quality_warn,
                   "object_checks": i.object_checks, "error": i.error,
                   **({"auto_shoot": i.auto_shoot, "reopen": i.reopen} if i.auto_shoot else {}),
                   **({"ai_status": i.ai_status} if i.ai_status else {}),
                   **({"ai_reason": i.ai_reason} if i.ai_reason else {})} for i in job.items],
    }
