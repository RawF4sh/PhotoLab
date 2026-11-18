"""Style from your edits (F27): name the changes the history shows you keep making, and on a yes
write them into a new look of the profile (a `habit` table). Reads the history, writes nothing
except in `add_to_look`."""

from __future__ import annotations

import os
import re
import statistics
from collections import Counter
from dataclasses import asdict

from . import history, looks, paths
from .instructions import GLOBAL, PLACE, load_vocab

_NAME = re.compile(r"[a-z0-9_]+")


def load_config(profile: str | None = None, configs_dir: str | None = None) -> dict:
    """style.toml (a profile file of the same name merged over it) as
    {"suggest": {...}, "words": {op: (lower, higher)}, "scenes": {scene: text}}."""
    base = os.path.join(configs_dir or paths.configs_dir(), "style.toml")
    if not os.path.exists(base):   # a configs folder that predates the file gets the packaged one
        base = os.path.join(paths._packaged(), "style.toml")
    data = paths.read_config(base, profile)
    s = data.get("suggest", {})
    return {"suggest": {"min_photos": int(s["min_photos"]), "min_days": int(s["min_days"]),
                        "min_share": float(s["min_share"])},
            "words": {op: (str(w[0]), str(w[1])) for op, w in data.get("words", {}).items()},
            "scenes": {k: str(v) for k, v in data.get("scenes", {}).items()}}


def _could(photos: list[dict], target: str) -> tuple[list[dict], bool]:
    """(the photos the change could have been made on, whether the run looks for the target itself)."""
    if target == GLOBAL or target.startswith(PLACE):
        return photos, True
    if not any(target in r["has"] for r in photos):
        return photos, False       # only known where an instruction named it: the whole scene counts
    return [r for r in photos if target in r["has"] or target in r["changes"]], True


def suggest(rows: list[dict], look: str, conf: dict, limits: dict[str, float]) -> list[dict]:
    rule, words = conf["suggest"], conf["words"]
    scenes: dict[str, list[dict]] = {}
    for r in rows:
        if r["look"] == look and r["scene"]:
            scenes.setdefault(r["scene"], []).append(r)
    out = []
    for scene, photos in sorted(scenes.items()):
        keys = sorted({(t, op) for r in photos for t, ops in r["changes"].items() for op in ops
                       if op in words and op in limits})
        for target, op in keys:
            could, found = _could(photos, target)
            vals = [(r["changes"].get(target, {}).get(op, 0.0), r["day"]) for r in could]
            down, up = sum(v < 0 for v, _ in vals), sum(v > 0 for v, _ in vals)
            if down == up:
                continue
            sign, same = (-1 if down > up else 1), max(down, up)
            days = {d for v, d in vals if v * sign > 0}
            if same < rule["min_photos"] or len(days) < rule["min_days"] or same < rule["min_share"] * len(could):
                continue
            size = statistics.median_low([v * sign for v, _ in vals])   # a value that was typed
            if size <= 0:               # only with min_share at or below a half
                continue
            out.append({"id": f"{scene}/{target}/{op}", "look": look, "scene": scene, "target": target,
                        "op": op, "value": round(sign * min(size, limits[op]), 4), "photos": same,
                        "of": len(could), "days": len(days), "found": found})
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def sentence(s: dict, conf: dict) -> tuple[str, str]:
    """(the question, the counted facts) of one suggestion."""
    t = s["target"]
    what = "the photo" if t == GLOBAL else f"the {t[len(PLACE):] if t.startswith(PLACE) else t}"
    word = conf["words"][s["op"]][0 if s["value"] < 0 else 1]
    kind = conf["scenes"].get(s["scene"], f"{s['scene']} photos")
    if t == GLOBAL or t.startswith(PLACE):
        counted = f"all {s['of']} {kind} edited with the look {s['look']}"
    elif s["found"]:
        counted = f"the {s['of']} {kind} where {what} was found"
    else:
        counted = f"all {s['of']} {kind}, because {what} is only looked for where an instruction names it"
    ask = f"You made {what} {word} on {s['photos']} of {s['of']} {kind}. Add it to your look?"
    fact = (f"Counted: {counted}; {s['photos']} of them on {_plural(s['days'], 'day')}. "
            f"The look would add {s['op']} {s['value']:+g} to {what} on {kind}.")
    return ask, fact


def _limits(profile: str | None, configs_dir: str | None) -> dict:
    path = paths.config_paths(configs_dir, profile).get("instructions_path") \
        or os.path.join(paths._packaged(), "instructions.toml")
    return load_vocab(path).limits


def overview(profile: str | None, configs_dir: str | None = None, look: str | None = None) -> dict:
    """What the history shows, and what it suggests. Writes nothing."""
    rows, bad = history.load(profile)
    used = Counter(r["look"] for r in rows if r["look"])
    look = look or (min(used, key=lambda n: (-used[n], n)) if used else "")
    conf = load_config(profile, configs_dir)
    sugg = suggest(rows, look, conf, _limits(profile, configs_dir)) if look else []
    for s in sugg:
        s["ask"], s["fact"] = sentence(s, conf)
    return {"on": history.wanted(configs_dir, profile), "photos": len(rows),
            "days": len({r["day"] for r in rows}), "unreadable": bad, "looks": dict(sorted(used.items())),
            "look": look, "suggestions": sugg, "suggest": conf["suggest"],
            "left_out": {k: sum(r["left_out"].get(k, 0) for r in rows) for k in ("mark", "word")}}


def add_to_look(profile: str | None, name: str, ids: list[str] | None = None, look: str | None = None,
                configs_dir: str | None = None) -> dict:
    """The yes: a new look = the base look plus the suggested habits. ValueError, nothing written,
    when the name or the ids will not do."""
    from .cli import write_look

    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f"look name {name!r}: use lowercase letters, digits and underscores")
    ov = overview(profile, configs_dir, look)
    sugg, base = ov["suggestions"], ov["look"]
    if ids is not None:
        unknown = [i for i in ids if i not in {s["id"] for s in sugg}]
        if unknown:
            raise ValueError(f"no such suggestion: {', '.join(map(str, unknown))}")
        sugg = [s for s in sugg if s["id"] in ids]
    if not sugg:
        raise ValueError("there is no suggestion to add")
    if name == base:
        raise ValueError(f"{name!r} is the look your history was made with; choose a new name")
    packaged = looks.load(os.path.join(configs_dir or paths.configs_dir(), "looks.toml"))
    if name in packaged:
        raise ValueError(f"{name!r} is a packaged look; choose a new name")
    looks_path = paths.config_paths(configs_dir, profile)["looks_path"]
    merged = looks.load(looks_path)
    if base not in merged:
        raise ValueError(f"the look {base!r} of your history no longer exists")
    habit = {sc: {t: dict(ops) for t, ops in ts.items()}
             for sc, ts in looks.load_habits(looks_path).get(base, {}).items()}
    for s in sugg:
        ops = habit.setdefault(s["scene"], {}).setdefault(s["target"], {})
        ops[s["op"]] = round(ops.get(s["op"], 0.0) + s["value"], 4)
    habit = looks.check_habit(habit, f"look {name}")
    where = history.folder(profile)
    mine = os.path.join(where, "looks.toml")
    replaced = os.path.isfile(mine) and name in paths.read_config(mine)
    path, _ = write_look(where, name, asdict(merged[base]), habit=habit)
    return {"name": name, "base": base, "path": path, "replaced": replaced,
            "added": [s["id"] for s in sugg], "habit": habit}
