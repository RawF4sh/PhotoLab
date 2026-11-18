"""Teaching mode (F29): the steps of one preview's render, with their lessons. It reads what a render
reports and changes no pixel."""

import math

import numpy as np

from .concepts import meta_for
from .edit import banding, gates, ops
from .edit.reference import rgb_to_lab
from .imageio import Image, downscale_long_edge, resize_to
from .lessons import _lines, _pct

STEP_PX, STEP_PX_RANGE = 1280, (256, 2560)      # long edge of a step picture: the default, the limits
# ponytail: one picture for all regions and one for all after the checks; more needs more on_step calls with names.
_STAGES = {"candidate_decode": "source", "after_regions": "regions", "after_composition": "shapes",
           "after_palette": "palette", "after_chroma_guard": "checks", "final_float": "finish"}
_QUIET = ("regions", "shapes", "palette", "finish")   # no step of their own unless a pixel moved half a level
NOTES = {"palette": ("palette:",),
         "regions": ("night sky:",),
         "checks": ("highlight hold:", "oversaturation gate:", "banding gate:", "banding watch:", "⚠ needs review:"),
         "finish": ("reference match", "post-reference", "backdrop:", "lut:")}
GUARD_NOTE = "skin-safe guard:"
_TYPED = {"thing": "regions", "named": "regions", "area": "shapes", "place": "shapes", "photo": "tools"}


def check_steps(value) -> tuple[int, bool]:
    """The request's `steps` as (long edge of a step picture, lossless); (0, False) is no steps."""
    if value is None or value is False:
        return 0, False
    if value is True:
        return STEP_PX, False
    if not isinstance(value, dict) or set(value) - {"px", "lossless"}:
        raise ValueError('"steps" must be true, false or {"px": 256 to 2560, "lossless": true or false}')
    px, lossless = value.get("px", STEP_PX), value.get("lossless", False)
    if isinstance(px, bool) or not isinstance(px, int) or not STEP_PX_RANGE[0] <= px <= STEP_PX_RANGE[1]:
        raise ValueError(f'"steps.px" must be a whole number from {STEP_PX_RANGE[0]} to {STEP_PX_RANGE[1]}')
    if not isinstance(lossless, bool):
        raise ValueError('"steps.lossless" must be true or false')
    return px, lossless


def _tool(op, value, les):
    return {"op": op, "value": round(float(value), 4), **_lines(les, "op", op)}


class Steps:        # the pictures of one render: `stage` is on_stage, `step` is on_step, `encode` makes an answer's picture
    def __init__(self, encode):
        self.encode, self.items, self.first, self.last = encode, [], None, None

    def _add(self, kind, keys, rgb):
        d = None if self.last is None or rgb is self.last else np.abs(rgb - self.last)
        peak, moved = (0.0, 0.0) if d is None else (float(d.max()) * 255.0, float(d.mean()) * 255.0)
        same = self.last is not None and peak == 0.0        # the same picture gives the same bytes
        self.items.append({"kind": kind, "ops": list(keys), "peak": peak, "moved": round(moved, 3),
                           "after": self.items[-1]["after"] if same else self.encode(rgb)})
        self.first = rgb if self.last is None else self.first
        self.last = rgb

    def stage(self, name, rgb):
        if name == "after_regions" and len(self.items) > 1:     # the banding gate rendered again:
            del self.items[1:]                                  # the second render is the edit
            self.last = self.first
        if name in _STAGES:
            self._add(_STAGES[name], (), rgb)

    def step(self, keys, rgb):
        if keys:
            self._add("tools", keys, rgb)

    def done(self, result, les, guard_ops=(), walk=(), grained=None) -> list[dict]:
        items = list(self.items)
        if items[-1]["kind"] == "finish" and items[-1]["peak"] < 0.5:
            last = items.pop()                      # nothing to see: the picture stays, the step goes
            items[-1] = {**items[-1], "after": last["after"]}
        items = [it for it in items if it["kind"] not in _QUIET or it["peak"] >= 0.5]
        if grained:
            items.append({"kind": "grain", "ops": ["grain"], "moved": None, "after": grained})
        gs = result.global_settings
        out = []
        for it in items:
            kind = it["kind"]
            lut = kind == "finish" and gs.get("lut")
            keys = list(it["ops"]) if kind in ("tools", "grain") else ["lut"] if lut else []
            tools = [_tool(op, gs.get(op, 0.0), les) for op in keys]
            lesson = _lines(les, "guard", "gate") if kind == "checks" else \
                {} if kind in ("tools", "grain") else _lines(les, "step", kind)
            title = ", ".join(t.get("name", t["op"]) for t in tools) or lesson.get("name", kind)
            where = [{"target": m.target, "coverage": m.coverage,
                      "tools": [_tool(op, v, les) for op, v in m.ops.items() if v]}
                     for m in (result.masks if kind == "regions" else result.composition if kind == "shapes" else [])
                     if any(m.ops.values())]
            typed = [i for i, e in enumerate(walk) if _TYPED[e["mask"]["kind"]] == kind
                     and (kind != "tools" or {t["op"] for t in e["tools"]} & set(it["ops"]))]
            guarded = kind == "tools" and set(it["ops"]) & set(guard_ops)
            prefixes = NOTES.get(kind, ()) + ((GUARD_NOTE,) if guarded else ())
            out.append({"kind": kind, "title": title, "tools": tools,
                        **{k: lesson[k] for k in ("does", "when", "watch") if k in lesson},
                        "where": where, "typed": typed,
                        "notes": [n for n in result.notes if n.startswith(prefixes)] if prefixes else [],
                        "moved": it["moved"], "after": it["after"]})
        return out


class NoEdit:
    """The decider of a try (F29): it edits nothing, so only the user's sliders and sentences do."""
    name = "none"

    def decide(self, ctx):
        return {}, {}


def sliders(les) -> dict:
    """`[try.sliders]` of lessons.toml as {op: (lowest, highest, step)}, in file order."""
    table = {}
    for op, v in les.get("try", {}).get("sliders", {}).items():
        if op not in ops.OP_BOUNDS:
            raise ValueError(f'lessons.toml [try.sliders]: "{op}" is not an op')
        if not isinstance(v, (list, tuple)) or len(v) != 3 or any(
                isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
            raise ValueError(f'lessons.toml [try.sliders]: "{op}" must be [lowest, highest, step]')
        lo, hi, step = map(float, v)
        b = ops.OP_BOUNDS[op]
        if lo < b[0] or hi > b[1] or lo >= hi:
            raise ValueError(f'lessons.toml [try.sliders]: "{op}" has a range outside {b} or lowest at or above highest')
        if step <= 0:
            raise ValueError(f'lessons.toml [try.sliders]: "{op}" needs a step above 0')
        table[op] = (lo, hi, step)
    return table


def check_try(value, table) -> dict:
    """The request's `try` as {"sliders": {op: value} (only those that are not 0), "compare": bool}."""
    if not isinstance(value, dict) or set(value) - {"sliders", "compare"}:
        raise ValueError('"try" must be an object with "sliders" and "compare"')
    if not table:
        raise ValueError("lessons.toml has no [try.sliders] table; copy it from the packaged file")
    given, compare = value.get("sliders", {}), value.get("compare", False)
    if not isinstance(given, dict):
        raise ValueError('"try.sliders" must be an object')
    if not isinstance(compare, bool):
        raise ValueError('"try.compare" must be true or false')
    out = {}
    for op, v in given.items():
        if op not in table:
            raise ValueError(f'"try.sliders" has no slider "{op}"; the sliders are: {", ".join(table)}')
        lo, hi, _ = table[op]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f'"try.sliders.{op}" must be a number from {lo:g} to {hi:g}')
        if v != 0:
            out[op] = float(v)
    return {"sliders": out, "compare": compare}


def measure(source, out, proxy, union, pipe) -> dict:
    """What the sidecar does not hold: the banding score, and how far the mean colour of each protected region moved
    (CIELAB a and b). `source`, `out`: one size; `proxy`: the analysis picture of the source (an Image); `union`: its masks."""
    def small(rgb):
        return downscale_long_edge(Image(rgb=rgb), 1280).rgb        # the size the banding gate reads
    score, action = banding.assess(small(source), small(out), pipe.banding)[:2] if pipe.banding else (0.0, "pass")
    post = resize_to(downscale_long_edge(Image(rgb=out), pipe.cfg.analysis_long_edge), *proxy.rgb.shape[:2])
    colour = {}
    for name, mask in union.items():
        if meta_for(name, pipe.meta).guard_global and mask.any():
            (a0, b0), (a1, b1) = (rgb_to_lab(x.rgb[mask][None])[0, :, 1:].mean(axis=0) for x in (proxy, post))
            colour[name] = float(math.hypot(a1 - a0, b1 - b0))
    return {"banding": float(score), "banding_action": action, "colour": colour}


_FLAT_OPS = [k for step in ops.STEPS for k in step]
_SAID = NOTES["checks"] + (GUARD_NOTE, "policy[")


def _said(r) -> dict:
    return {"quality": r.quality_score, "needs_review": r.needs_review, "warnings": list(r.warnings),
            "notes": [n for n in r.notes if n.startswith(_SAID)]}


def compare(mine, theirs, mine_m, theirs_m, les, sliders=None, gate=None) -> dict:
    """The try (`mine`) beside the engine's edit (`theirs`): the values of both, what the checks measure, what was said."""
    sliders = sliders or {}
    tools = []
    def _ops_of(r, kind, target):
        return next((dict(m.ops) for m in getattr(r, kind) if m.target == target), {})
    targets = [("global", None)]
    for kind in ("masks", "composition"):
        seen = []
        for r in (mine, theirs):
            seen += [m.target for m in getattr(r, kind) if m.target not in seen]
        targets += [(t, kind) for t in seen]
    for target, kind in targets:
        y, e = (mine.global_settings, theirs.global_settings) if kind is None else (
            _ops_of(mine, kind, target), _ops_of(theirs, kind, target))
        for op in _FLAT_OPS:
            if y.get(op) or e.get(op) or (kind is None and sliders.get(op)):
                tools.append({"where": target, "op": op, **_lines(les, "op", op),
                              "set": sliders.get(op) if kind is None else None,
                              "yours": round(float(y.get(op, 0.0)), 4), "engine": round(float(e.get(op, 0.0)), 4)})
    t = {**gates.GATE_DEFAULTS, **(gate or {})}
    s, y, e = mine.global_stats, mine.post_edit_stats, theirs.post_edit_stats
    checks = []

    def row(key, where, source, yours, engine, flag, line, drop=False):
        if drop and max(abs(v) for v in (source, yours, engine)) < 0.0005:
            return
        checks.append({"key": key, "where": where, "source": source, "yours": yours, "engine": engine,
                       "flag": bool(flag), "line": line, **_lines(les, "check", key)})

    def clip(key, where, label, verb, budget, a, b, c):
        row(key, where, a, b, c, b - a > t[budget],
            f"Your version {verb} {_pct(b)} of the {label}. The source: {_pct(a)}. AutoEdit: {_pct(c)}.", drop=True)
    clip("blown", "frame", "frame", "clips", "clip_budget", s.highlight_clip, y.highlight_clip, e.highlight_clip)
    clip("crushed", "frame", "frame", "crushes", "shadow_clip_budget", s.shadow_clip, y.shadow_clip, e.shadow_clip)
    post_y = {r.name: r for r in mine.post_region_stats}
    post_e = {r.name: r for r in theirs.post_region_stats}
    for r in mine.region_stats:
        if r.name in post_y and r.name in post_e:
            clip("blown", r.name, r.name, "clips", "clip_budget", r.clip_high, post_y[r.name].clip_high, post_e[r.name].clip_high)
            clip("crushed", r.name, r.name, "crushes", "shadow_clip_budget", r.clip_low, post_y[r.name].clip_low, post_e[r.name].clip_low)
    a, b, c = s.rgb_highlight_clip_any, y.rgb_highlight_clip_any, e.rgb_highlight_clip_any
    row("channel", "frame", a, b, c, b - a > t["clip_budget"],
        f"In your version {_pct(b)} of the frame has a colour channel at its limit. The source: {_pct(a)}. AutoEdit: {_pct(c)}.",
        drop=True)
    by, be = mine_m["banding"], theirs_m["banding"]
    row("banding", "frame", None, by, be, mine_m["banding_action"] != "pass",
        f"Banding in smooth areas: {by:.2f} in your version ({mine_m['banding_action']}). AutoEdit: {be:.2f}.")
    for name, shift in mine_m["colour"].items():
        other = theirs_m["colour"].get(name, 0.0)
        row("colour", name, None, shift, other, False,
            f"Your version moved the colour of the {name} by {shift:.1f}. AutoEdit: {other:.1f}.")
    return {"tools": tools, "checks": checks, "said": {"yours": _said(mine), "engine": _said(theirs)}}
