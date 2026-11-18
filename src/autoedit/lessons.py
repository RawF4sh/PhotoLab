"""The walk-through of one photo's own changes (F37): what each typed change reached, what held it
back, and what each tool and guard is. Pure data out of the plan; it changes no pixel."""

import os
import tomllib

from . import instructions
from .segment.base import parse_mark

_RANK = ["not_found", "blocked", "clamped", "applied"]
TABLES = ("op", "mask", "guard", "step", "check", "try")
_LINES = ("name", "does", "when", "watch")
_WHY = {"nothing": "No {word} was found in the oval.",
        "small": "The {word} is too small in this photo.",
        "bigger": "The {word} is bigger than the oval: it is part of something larger."}


def load(path: str) -> dict:
    """lessons.toml as one dict per name of `TABLES`. A missing file gives empty
    tables: a walk-through then has its facts and no lesson lines."""
    data = {}
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = tomllib.load(f)
    return {k: dict(data.get(k, {})) for k in TABLES}


def _lines(lessons: dict, table: str, key: str) -> dict:
    entry = lessons.get(table, {}).get(key)
    return {k: entry[k] for k in _LINES if k in entry} if entry else {}


def _label_kind(target: str) -> tuple[str, str]:
    if target == instructions.GLOBAL:
        return "the whole photo", "photo"
    if instructions.is_place(target):
        return "the " + target[len(instructions.PLACE):], "place"
    mark = parse_mark(target)
    if mark:
        return ("the marked area", "area") if mark[0] == "area" else ("the marked " + mark[2], "thing")
    return "the " + target, "named"


_NO_REGION = "outside every region"     # a cell of a target that lies in none of the run's regions


def _cell_label(cell: str, target: str) -> str:
    if cell in (target, target + "@background"):
        return _NO_REGION
    if cell.startswith(target + "/"):
        return cell[len(target) + 1:].split("@")[0]
    if cell.startswith(target + "@"):
        return cell[len(target) + 1:]
    return cell


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %" if x < 0.1 else f"{x * 100:.0f} %"


def _mask(target, kind, label, found, plan, lessons) -> dict:
    mark = parse_mark(target)
    word = mark[2] if mark else ""
    why = ""
    if not found:
        if kind == "thing":
            why = _WHY[plan.why.get(target, "nothing")].format(word=word)
        elif target == instructions.LIGHT_TARGET:
            why = "No light source was found in this photo."
        elif kind == "place":
            why = f"No {label[4:]} was found in this photo."
        else:
            why = (f"The {target} is too small in this photo." if target in plan.present
                   else f"No {target} was found in this photo.")
    m = {"kind": kind, "target": target, "found": found, "why": why, "line": why}
    if kind in ("thing", "area"):
        m["box"] = [float(v) for v in mark[1]]
    if kind == "thing":
        m["word"] = word
    det = next((d for d in plan.detected if d.name == target), None) if kind in ("thing", "named") else None
    if det:
        m.update(score=float(det.score), coverage=float(det.coverage), instances=int(det.instance_count))
    if found:
        if kind == "thing":
            n = int(det.instance_count) if det else 1
            m["line"] = (f'Looked for "{word}" and found {"one" if n == 1 else n} with its centre in your oval: '
                         f"{_pct(det.coverage)} of the frame, score {det.score:.2f}." if det
                         else f'Looked for "{word}" in your oval.')
        else:
            m["line"] = {"area": "The oval itself, soft at the edge.",
                         "named": f'Everything found under the name "{target}".',
                         "place": f"A fixed part of the frame: the {label[4:]}.",
                         "photo": "The whole photo."}[kind]
    return {**m, **_lines(lessons, "mask", kind)}


def _parts(target, kind, label, cells, plan) -> list[dict]:
    """The parts the change reached: label, share, and the cells (or the held settings of a layer)."""
    if kind == "photo":
        return [{"label": label, "share": 1.0, "cells": []}]
    if kind in ("area", "place"):
        layer = target if kind == "area" else target[len(instructions.PLACE):]
        g = plan.guarded.get(layer)
        if g:
            return [{"label": " and ".join(g[2]), "share": float(g[1]), "cells": [], "held": g[0]},
                    {"label": "the rest of it", "share": 1 - float(g[1]), "cells": []}]
        return [{"label": "the oval" if kind == "area" else label, "share": 1.0, "cells": []}]
    px = {c.name: int(c.mask.sum()) for c in plan.cells}
    parts: dict[str, dict] = {}
    for c in cells:
        parts.setdefault(_cell_label(c, target), {"cells": [], "px": 0})
        parts[_cell_label(c, target)]["cells"].append(c)
        parts[_cell_label(c, target)]["px"] += px.get(c, 0)
    total = sum(p["px"] for p in parts.values()) or 1
    # The part no region owns reads "all of it" when it is the only part, else "the rest of it".
    rest = "all of it" if len(parts) == 1 else "the rest of it"
    return [{"label": rest if k == _NO_REGION else k, "share": p["px"] / total, "cells": p["cells"],
             "big": max(p["cells"], key=lambda c: px.get(c, 0))}
            for k, p in parts.items()]


def _item(part, op, asked, target, plan, before, after) -> dict:
    if "big" in part:
        c = part["big"]
        was, now = before.get(c, {}), after.get(c, {})
        shared = sum(c in cs for cs in plan.hit.values()) > 1
        value = now.get(op, 0.0) if shared else now.get(op, 0.0) - (was.get(op, 0.0) - asked)
        blocked = op in was and op not in now
        full = (now.get(op, 0.0) == was.get(op, 0.0)) if shared else round(value, 6) == round(asked, 6)
    elif "held" in part:
        value = part["held"].get(op, 0.0)
        blocked, shared, full = op not in part["held"], False, round(value, 6) == round(asked, 6)
    else:
        value, blocked, shared, full = asked, False, False, True
    value = 0.0 if blocked else round(float(value), 6)
    item = {"part": part["label"], "value": value, "state": "blocked" if blocked else "full" if full else "held"}
    return {**item, "shared": True} if shared else item


def walk(adjs, plan, before, after, vocab, lessons) -> list[dict]:
    """The walk-through of one photo's own changes: one entry per target of a photo-scope
    adjustment, in order. `before` and `after` are the cell settings before and after the
    policies ran."""
    mine: dict[str, list] = {}
    for a in adjs:
        if a.scope == "photo":
            mine.setdefault(a.target, []).append(a)
    out = []
    for target, group in mine.items():
        label, kind = _label_kind(target)
        cells = plan.hit.get(target, [])
        found = bool(cells)
        sources = list(dict.fromkeys(a.source for a in group if a.source))
        statuses = [a.status for a in group if a.status in _RANK]
        parts = _parts(target, kind, label, cells, plan) if found else []
        tools, held = [], []
        for op, asked in plan.asked.get(target, {}).items():
            items = [_item(p, op, float(asked), target, plan, before, after) for p in parts]
            tools.append({"op": op, "asked": round(float(asked), 6), "applied": items, **_lines(lessons, "op", op)})
            for it in items:
                if it["state"] == "held":
                    held.append(f"{it['part']} keeps its limits: {op} {asked:+g} became {it['value']:+g}")
                elif it["state"] == "blocked":
                    held.append(f"{it['part']} takes no {op}: its policy forbids it")
            if found and (target, op) in plan.limited:
                held.append(f"the limit for typed changes holds {op} at {asked:+g}")
        kinds = (["policy"] if any(i["state"] != "full" for t in tools for i in t["applied"]) else []) + \
                (["limit"] if found and any((target, t["op"]) in plan.limited for t in tools) else []) + ["gate"]
        guards = [{"kind": k, **_lines(lessons, "guard", k)} for k in kinds if k in lessons.get("guard", {})]
        out.append({"target": target, "sources": sources, "label": label,
                    "status": min(statuses, key=_RANK.index) if statuses else "",
                    "mask": _mask(target, kind, label, found, plan, lessons),
                    "parts": [{"label": p["label"], "share": float(p["share"])} for p in parts],
                    "tools": tools, "held": held, "guards": guards})
    return out
