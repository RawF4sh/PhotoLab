"""CLI-only features as jobs for the page (U6, docs/plans/UI-minimal.md "API for the tools").

TOOLS maps a name to (runner, slow). A runner is fn(payload, configs_dir, profile, log) -> dict and raises
ValueError for a bad body. Jobs live in memory, run in a daemon thread, and are polled, like curate_jobs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from .. import paths
from . import service

# ponytail: one heavy job at a time; per-kind locks if that ever hurts
_SLOW = threading.Lock()
_JOBS: dict[str, "ToolJob"] = {}
_LOCK = threading.Lock()

# Keys that name a path: each is checked against the approved folders before a job starts.
PATH_KEYS = ("folder", "target", "path", "out", "file", "photos", "dataset", "out_model", "input",
             "aesthetic_model", "reference", "recipe_model", "lut", "image")


@dataclass
class ToolJob:
    id: str
    name: str
    status: str = "running"          # running | done | error
    result: dict | None = None
    error: str = ""
    log: list = field(default_factory=list)
    created: float = 0.0


def _text(payload, key, required=False):
    v = payload.get(key)
    if v in (None, ""):
        if required:
            raise ValueError(f"missing required field: {key}")
        return None
    if not isinstance(v, str):
        raise ValueError(f"{key} must be a string")
    return v


def _flag(payload, key, default=False):
    v = payload.get(key, default)
    if not isinstance(v, bool):
        raise ValueError(f"{key} must be true or false")
    return v


def _num(payload, key):
    v = payload.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(v)


def _folder(payload) -> str:
    f = _text(payload, "folder", True)
    if not os.path.isdir(f):
        raise FileNotFoundError(f"folder not found: {f}")
    return f


def _printed(fn, *a, **kw):
    """Run a function that prints; return (its value, what it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def _cfg(configs_dir, profile, **kw):
    from .. import cli

    return cli.load_run_config(argparse.Namespace(configs_dir=configs_dir, profile=profile, **kw))


def _min_cov(payload, configs_dir, profile) -> float:
    v = _num(payload, "min_coverage")
    if v is not None:
        return v
    rules = paths.read_config(paths.config_paths(configs_dir, profile)["rules_path"])
    return float((rules.get("search") or {}).get("min_coverage", 0.02))


# ── runners ──
def find(p, configs_dir, profile, log):
    from .. import search

    words = p.get("words")
    if not isinstance(words, list) or not words or not all(isinstance(w, str) for w in words):
        raise ValueError("words must be a non-empty list of strings")
    text, _ = search.cmd_find(_folder(p), words, _min_cov(p, configs_dir, profile), True)
    return {"hits": json.loads(text)}


def keywords(p, configs_dir, profile, log):
    from .. import search

    return {"text": search.cmd_keywords(_folder(p), _min_cov(p, configs_dir, profile), _flag(p, "dry_run"),
                                        configs_dir, profile)}


def declare(p, configs_dir, profile, log):
    from .. import declare as declare_mod

    text = declare_mod.cmd_declare(_folder(p), None)
    return {"text": text, "files": text.splitlines()[:2]}


def gallery(p, configs_dir, profile, log):
    from .. import gallery as gallery_mod
    from .. import search

    return gallery_mod.write(_folder(p), None, _min_cov(p, configs_dir, profile),
                             search.skip_words(configs_dir, profile))


def check(p, configs_dir, profile, log):
    from .. import batch

    folder = _folder(p)
    out = os.path.join(folder, "autoedit-check")
    cfg = _cfg(configs_dir, profile, reveal_check=True, backend=_text(p, "backend"))
    m = batch.run(cfg, folder, out, do_apply=False, consistent=False, workers=cfg.workers)
    return {"report": os.path.join(out, "reveal.html"), "reveal": (m or {}).get("reveal")}


def sync(p, configs_dir, profile, log):
    from .. import sync as sync_mod

    inputs = {k: _text(p, k) for k in ("reference", "recipe_model", "lut")}
    r = sync_mod.run(_text(p, "target", True), _text(p, "folder"), dry_run=_flag(p, "dry_run"),
                     minutes=_num(p, "minutes"), configs_dir=configs_dir, profile=profile, **inputs)
    return {**r, "lines": sync_mod.lines(r)}


def merge(p, configs_dir, profile, log):
    from .. import merge as merge_mod

    folder = _folder(p)
    return merge_mod.run(folder, os.path.join(folder, "AutoEdit Merged"), mode=_text(p, "mode") or "auto",
                         dry_run=_flag(p, "dry_run"), configs_dir=configs_dir, profile=profile, say=log)


def pick(p, configs_dir, profile, log):
    from .. import pick as pick_mod

    folder = _folder(p)
    return pick_mod.run(folder, os.path.join(folder, "AutoEdit Picks"), _cfg(configs_dir, profile),
                        model_path=_text(p, "aesthetic_model"), copy=_flag(p, "copy"),
                        configs_dir=configs_dir, profile=profile, log=log)


def exports_(p, configs_dir, profile, log):
    from .. import exports as exports_mod

    path = paths.config_paths(configs_dir, profile).get("exports_path") or paths.config_path(
        "exports.toml", packaged_fallback=True)
    return {"presets": [{**{("pattern" if k == "name" else k): x for k, x in v.items()}, "name": n}
                         for n, v in exports_mod.load(path).items()]}   # a preset's own "name" key is its file-name pattern


# looks
def _looks(configs_dir, profile):
    from .. import cli
    from .. import looks as looks_mod

    lp = paths.config_paths(configs_dir, profile)["looks_path"]
    packaged = cli._read_toml(os.path.join(configs_dir or paths.configs_dir(), "looks.toml"))
    return looks_mod, packaged, looks_mod.load(lp), cli._profile_looks(profile), looks_mod.load_habits(lp)


def looks_list(p, configs_dir, profile, log):
    looks_mod, packaged, merged, own, habits = _looks(configs_dir, profile)
    defaults = asdict(looks_mod.Look())
    rows = []
    for name, look in merged.items():
        origin = ("packaged, changed by the profile" if name in packaged else "profile") if name in own else "packaged"
        rows.append({"name": name, "origin": origin, "habit": habits.get(name),
                     "changes": {k: v for k, v in asdict(look).items() if v != defaults[k]}})
    return {"looks": rows}


def looks_save(p, configs_dir, profile, log):
    from .. import cli

    looks_mod, _, merged, _, habits = _looks(configs_dir, profile)
    name, base = _text(p, "name", True), _text(p, "base") or "natural"
    if not cli._LOOK_NAME.fullmatch(name):
        raise ValueError("look name: use lowercase letters, digits and underscores")
    if base not in merged:
        raise ValueError(f"look {base!r} not found (known: {', '.join(merged)})")
    fields, sets = asdict(merged[base]), p.get("set") or {}
    if not isinstance(sets, dict):
        raise ValueError("set must be an object of FIELD: value")
    for k, v in sets.items():
        if k not in looks_mod.FIELDS:
            raise ValueError(f"set {k!r}: not a look field ({', '.join(looks_mod.FIELDS)})")
        ok = isinstance(v, bool) if k == "preserve_exposure" else (isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                   and abs(v) != float("inf") and v == v)
        if not ok:
            raise ValueError(f"set {k!r}: the value must be a finite number (true or false for preserve_exposure)")
        fields[k] = v
    path, existed = cli.write_look(profile or paths.default_profile_dir(), name, fields, habit=habits.get(base))
    return {"path": path, "rewritten": existed}


def looks_delete(p, configs_dir, profile, log):
    from .. import cli

    _, packaged, _, own, _ = _looks(configs_dir, profile)
    name = _text(p, "name", True)
    if name not in own:
        raise ValueError(f"look {name!r} is " + ("packaged and the profile does not define it; it cannot be deleted"
                                                if name in packaged else "not in the profile"))
    del own[name]
    with open(os.path.join(profile, "looks.toml"), "w", encoding="utf-8") as fh:
        fh.write(paths.dump_toml(own))
    return {"deleted": name}


def looks_lut(p, configs_dir, profile, log):
    from .. import lut as lut_mod

    _, _, merged, _, _ = _looks(configs_dir, profile)
    name, out = _text(p, "name", True), _text(p, "out", True)
    if name not in merged:
        raise ValueError(f"look {name!r} not found (known: {', '.join(merged)})")
    table, comments = lut_mod.look_cube(name, merged[name])
    lut_mod.write_cube(out, table, f"AutoEdit {name}", comments)
    return {"path": out, "points": int(table.shape[0]), "text": comments[1].strip()}


def looks_export(p, configs_dir, profile, log):
    from .. import lookfile

    name = _text(p, "name", True)
    text, notes = lookfile.export(name, configs_dir, profile, _text(p, "palette"),
                                  _text(p, "author") or "", _text(p, "licence") or "")
    out = _text(p, "out", True)
    lookfile.write(out, text)
    return {"path": out, "notes": notes}


def looks_import(p, configs_dir, profile, log):
    from .. import lookfile

    lf = lookfile.read(_text(p, "file", True))
    r = lookfile.install(lf, profile or paths.default_profile_dir(), _text(p, "new_name"),
                         _flag(p, "force"), configs_dir)
    return {**r, "author": lf.author, "licence": lf.licence}


def looks_card(p, configs_dir, profile, log):
    from .. import lookcard

    name, photos = _text(p, "name", True), p.get("photos") or []
    if not isinstance(photos, list) or not all(isinstance(x, str) for x in photos):
        raise ValueError("photos must be a list of paths")
    cfg = _cfg(configs_dir, profile, backend=_text(p, "backend") or "stub", look=name, palette=_text(p, "palette"))
    return lookcard.render(name, photos, _text(p, "out", True), cfg, log=log)


def history_status(p, configs_dir, profile, log):
    from .. import history

    return history.status(profile)


def history_clear(p, configs_dir, profile, log):
    from .. import history

    return {"cleared": history.clear(profile)}


def calibrate_start(p, configs_dir, profile, log):
    from .. import calibrate

    return calibrate.start(_text(p, "photos", True), _text(p, "out", True), count=_num(p, "count"),
                           rounds=_num(p, "rounds"), seed=_num(p, "seed"), backend=_text(p, "backend"),
                           style=_text(p, "style"), look=_text(p, "look"), name=_text(p, "name") or "calibrated",
                           profile=profile, configs_dir=configs_dir, log=log)


def calibrate_advance(p, configs_dir, profile, log):
    from .. import calibrate

    return calibrate.advance(_text(p, "out", True), photos=_text(p, "photos"), finish=_flag(p, "finish"),
                             profile=profile, configs_dir=configs_dir, log=log)


def models_status(p, configs_dir, profile, log):
    from .. import models

    return models.status()


def models_download(p, configs_dir, profile, log):
    from .. import models

    return {"path": models.download()}


def models_remove(p, configs_dir, profile, log):
    from .. import models

    return {"freed_bytes": models.remove()}


def doctor(p, configs_dir, profile, log):
    from .. import cli

    _, text = _printed(cli._cmd_doctor, argparse.Namespace(configs_dir=configs_dir, profile=profile))
    return {"text": text}


def rank_variants(p, configs_dir, profile, log):
    from .. import batch

    src = _text(p, "input", True)
    images = batch.list_images(src) if os.path.isdir(src) else [src]
    opts = {"aesthetic_model": p["aesthetic_model"]} if _text(p, "aesthetic_model") else {}
    out, errors = {}, {}
    for i in images:
        try:
            out[os.path.basename(i)] = service.make_variants(i, opts, configs_dir, with_images=False, profile=profile)
        except Exception as e:
            errors[os.path.basename(i)] = str(e)
    return {"photos": out, "errors": errors}


def train_recipe(p, configs_dir, profile, log):
    from .. import recipe

    return recipe.train_from_dataset(_text(p, "dataset", True), _text(p, "out_model", True),
                                     ridge=1.0 if _num(p, "ridge") is None else _num(p, "ridge"))


def reveal(p, configs_dir, profile, log):
    path = os.path.abspath(_text(p, "path", True))
    if not os.path.exists(path):
        raise FileNotFoundError(f"not found: {path}")
    if sys.platform == "darwin":
        cmd = ["open", "-R", path]
    elif sys.platform.startswith("win"):
        cmd = f'explorer /select,"{path}"'
    else:
        cmd = ["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)]
    subprocess.Popen(cmd)
    return {}


TOOLS = {
    "find": (find, False), "keywords": (keywords, False), "declare": (declare, False),
    "gallery": (gallery, True), "check": (check, True), "sync": (sync, True), "merge": (merge, True),
    "pick": (pick, True), "exports": (exports_, False),
    "looks_list": (looks_list, False), "looks_save": (looks_save, False), "looks_delete": (looks_delete, False),
    "looks_lut": (looks_lut, False), "looks_export": (looks_export, False), "looks_import": (looks_import, False),
    "looks_card": (looks_card, True),
    "history_status": (history_status, False), "history_clear": (history_clear, False),
    "calibrate_start": (calibrate_start, True), "calibrate_advance": (calibrate_advance, True),
    "models_status": (models_status, False), "models_download": (models_download, True),
    "models_remove": (models_remove, True), "doctor": (doctor, True),
    "rank_variants": (rank_variants, True), "train_recipe": (train_recipe, True), "reveal": (reveal, False),
}

# Fields a tool cannot run without: checked before the job starts, so a bad body is a 400, not a failed job.
REQUIRED = {"find": ("folder", "words"), "keywords": ("folder",), "declare": ("folder",), "gallery": ("folder",),
            "check": ("folder",), "sync": ("target",), "merge": ("folder",), "pick": ("folder",),
            "looks_save": ("name",), "looks_delete": ("name",), "looks_lut": ("name", "out"),
            "looks_export": ("name", "out"), "looks_import": ("file",), "looks_card": ("name", "out"),
            "calibrate_start": ("photos", "out"), "calibrate_advance": ("out",), "rank_variants": ("input",),
            "train_recipe": ("dataset", "out_model"), "reveal": ("path",)}


_STR = PATH_KEYS[:5] + PATH_KEYS[6:] + ("name", "backend", "style", "look", "palette", "new_name", "mode",
                                         "licence", "base", "author")
_NUM = ("seed", "rounds", "ridge", "minutes", "count")
_BOOL = ("dry_run", "force", "finish", "copy")


def _check_types(payload: dict) -> None:
    for k, v in payload.items():
        if v is None:
            continue
        if k in _STR and not isinstance(v, str):
            raise ValueError(f"{k} must be a string")
        if k in _NUM and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise ValueError(f"{k} must be a number")
        if k in _BOOL and not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false")
        if k == "words" and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise ValueError("words must be a list of strings")
        if k == "photos" and not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))):
            raise ValueError("photos must be a string or a list of strings")


def start(name: str, payload, configs_dir: str, profile: str | None) -> ToolJob:
    """KeyError: unknown tool. ValueError: bad body. PermissionError: a path outside the approved folders."""
    if name not in TOOLS:
        raise KeyError(name)
    if not isinstance(payload, dict):
        raise ValueError("the body must be a JSON object")
    _check_types(payload)
    missing = [k for k in REQUIRED.get(name, ()) if not payload.get(k)]
    if missing:
        raise ValueError("missing required field(s): " + ", ".join(missing))
    ps = [payload[k] for k in PATH_KEYS if isinstance(payload.get(k), str) and payload[k]]
    ps += [x for x in (payload.get("photos") or []) if isinstance(x, str)] if isinstance(payload.get("photos"), list) else []
    service.ensure_allowed(*ps)
    job = ToolJob(id=uuid.uuid4().hex[:12], name=name, created=time.time())
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, payload, configs_dir, profile), daemon=True).start()
    return job


def _run(job: ToolJob, payload, configs_dir, profile) -> None:
    runner, slow = TOOLS[job.name]
    def log(m=""):
        job.log = (job.log + [str(m)])[-50:]

    try:
        if slow:
            with _SLOW:
                job.result = runner(payload, configs_dir, profile, log)
        else:
            job.result = runner(payload, configs_dir, profile, log)
        job.status = "done"
    except BaseException as e:      # SystemExit from a CLI helper must not leave the job "running"
        job.status, job.error = "error", str(e) or type(e).__name__


def get(job_id: str) -> ToolJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def snapshot(job: ToolJob) -> dict:
    return {"id": job.id, "name": job.name, "status": job.status, "result": job.result,
            "error": job.error, "log": list(job.log)}
