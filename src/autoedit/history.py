"""The history of your own edits (F27): one short line per exported photo, in your profile folder.

Off by default (`history = true` under `[run]`). A line says what the photo was like and what
was typed on top of the edit. It never holds a path, a file or folder name, pixels, EXIF, the typed
sentence, a mark or its box, or a word the config files do not define."""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import sys

from . import looks as looks_mod, paths, recipe
from .edit.ops import OP_NAMES
from .concepts import load_concepts
from .instructions import GLOBAL, PLACE
from .segment.base import MARK
from .styles import load_styles, style_for

FILE, VERSION = "history.jsonl", 1
MAX_BYTES = 8_000_000        # about 23,000 photos; above it the oldest half is dropped
KEYS = ("v", "day", "scene", "look", "features", "has", "changes", "left_out")
_NAME = re.compile(r"[a-z0-9_]{1,40}")
_TARGET = looks_mod._TARGET     # global, place:NAME, or a concept name
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_TYPED, _MADE = ("run", "photo"), ("applied", "clamped")   # scope "look" is the look's own habit
_SAID: set[str] = set()


def line(result, cfg, day: str) -> dict:
    presets, meta = load_concepts(cfg.concepts_path)
    known = set(meta) | {c for cs in presets.values() for c in cs}
    look = cfg.look or style_for(cfg.style, load_styles(cfg.styles_path)).look
    if look not in looks_mod.load(cfg.looks_path):    # a typed word no look file defines is never written
        look = ""
    changes, left = {}, {"mark": 0, "word": 0}
    for a in result.adjustments:
        if a.scope not in _TYPED or a.status not in _MADE:
            continue
        t = a.target
        if t.startswith(MARK):
            left["mark"] += 1          # one spot on one photo: no look can repeat it
        elif t == GLOBAL or t in known or (t.startswith(PLACE) and _NAME.fullmatch(t[len(PLACE):])):
            acc = changes.setdefault(t, {})
            for op, delta in a.ops.items():
                if op not in OP_NAMES:
                    continue
                acc[op] = acc.get(op, 0.0) + float(delta)
        else:
            left["word"] += 1          # a word the user typed: never written down
    changes = {t: {op: round(v, 4) for op, v in ops.items() if abs(v) >= 1e-9} for t, ops in changes.items()}
    return {"v": VERSION, "day": day,
            "scene": result.scene if _NAME.fullmatch(result.scene or "") else "",
            "look": look if _NAME.fullmatch(look or "") else "",
            "features": {k: round(v, 4) for k, v in recipe.features_from_stats(result.global_stats).items()},
            "has": sorted(r.name for r in result.region_stats if r.name in known),
            "changes": {t: ops for t, ops in changes.items() if ops}, "left_out": left}


def folder(profile: str | None) -> str:
    """Where the history lives: the profile folder (a ValueError when profiles are off and none is given)."""
    return profile or paths.default_profile_dir()


def wanted(configs_dir: str | None = None, profile: str | None = None) -> bool:
    """`[run] history` of defaults.toml (with the profile's file merged over it)."""
    path = os.path.join(configs_dir or paths.configs_dir(), "defaults.toml")
    return paths.read_config(path, profile).get("run", {}).get("history", False) is True


def _warn(msg: str) -> None:
    if msg not in _SAID:
        _SAID.add(msg)
        print(f"history: not written ({msg})", file=sys.stderr)


def record(result, cfg, day: str | None = None) -> None:
    """Append one line for an exported photo. Never raises: a history that cannot be
    written must not fail an export."""
    if not getattr(cfg, "history", False) or getattr(result, "global_stats", None) is None:
        return
    try:
        where = folder(getattr(cfg, "profile_dir", None))
        row = line(result, cfg, day or datetime.date.today().isoformat())
        os.makedirs(where, exist_ok=True)
        path = os.path.join(where, FILE)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            # ponytail: one short write is not mixed with another process's on a local disk;
            # a lock file is the way out if a network drive ever matters
            os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode("ascii"))
            big = os.fstat(fd).st_size > MAX_BYTES
        finally:
            os.close(fd)
        if big:
            _trim(path)
    except Exception as e:     # never fails an export
        _warn(str(e))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ok(row) -> bool:
    try:
        return (isinstance(row, dict) and row.get("v") == VERSION
                and isinstance(row["day"], str) and bool(_DAY.fullmatch(row["day"]))
                and all(isinstance(row[k], str) and (not row[k] or _NAME.fullmatch(row[k]))
                        for k in ("scene", "look"))
                and isinstance(row["features"], dict) and all(_num(v) for v in row["features"].values())
                and isinstance(row["has"], list)
                and all(isinstance(s, str) and _TARGET.fullmatch(s) for s in row["has"])
                and isinstance(row["changes"], dict)
                and all(_TARGET.fullmatch(t) and isinstance(ops, dict)
                        and all(op in OP_NAMES and _num(v) for op, v in ops.items())
                        for t, ops in row["changes"].items())
                and isinstance(row["left_out"], dict)
                and all(isinstance(v, int) and not isinstance(v, bool) for v in row["left_out"].values()))
    except (KeyError, TypeError):
        return False


def load(profile: str | None) -> tuple[list[dict], int]:
    """(the readable lines, one per photo and in file order; how many lines were unreadable)."""
    path = os.path.join(folder(profile), FILE)
    if not os.path.isfile(path):
        return [], 0
    photos: dict[str, dict] = {}
    bad = 0
    with open(path, "rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                row = None
            if not _ok(row):
                bad += 1
                continue
            key = json.dumps(row["features"], sort_keys=True)
            photos.pop(key, None)            # the later line replaces the earlier, and goes last
            photos[key] = row
    return list(photos.values()), bad


def _trim(path: str) -> None:
    with open(path, "rb") as fh:
        lines = fh.readlines()
    keep, size = [], 0
    for raw in reversed(lines):
        size += len(raw)
        if size > MAX_BYTES // 2:
            break
        keep.append(raw)
    tmp = f"{path}.{os.getpid()}.part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.writelines(reversed(keep))
    os.replace(tmp, path)


def status(profile: str | None) -> dict:
    path = os.path.join(folder(profile), FILE)
    rows, bad = load(profile)
    days = sorted({r["day"] for r in rows})
    return {"path": path, "bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
            "photos": len(rows), "days": len(days),
            "first": days[0] if days else "", "last": days[-1] if days else "", "unreadable": bad}


def clear(profile: str | None) -> bool:
    try:
        os.remove(os.path.join(folder(profile), FILE))
        return True
    except FileNotFoundError:
        return False
