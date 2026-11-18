"""Looks (port of the Rust looks.rs) — magnitude shaping over the rule baseline.

A look never adds new decisions; it scales/offsets existing ones, then the whole settings
block is scaled by `intensity`. Final = clamp(base * look_mul + look_add) * intensity.

A look's tonal mood should come from the multipliers (`contrast_mul`, `curve_mul`,
`exposure_mul`): they act on rule values that already carry the safety fades (highlight
headroom, dark sources), so a strong look cannot push a dark photo darker or a bright one into
clipping. `curve_add` is unconditional; keep it small.
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, fields

from .contract import Settings


@dataclass
class Look:
    contrast_mul: float = 1.0
    vibrance_add: float = 0.0
    saturation_add: float = 0.0  # uniform global saturation lift (vibrance protects already-saturated px; this doesn't)
    clarity_mul: float = 1.0
    dehaze_mul: float = 1.0
    exposure_mul: float = 1.0  # < 1 holds back the rules' exposure LIFT (keeps a scene's own mood); never scales a cut
    preserve_exposure: bool = False  # the look means to be dark: the review gate's darkness check is off
    curve_mul: float = 1.0     # scales the rules' own curve (depth that follows the lift) before curve_add
    # global-only finishing the style injects (split-tone + filmic curve)
    curve_add: float = 0.0
    grade_shadows_temp: float = 0.0
    grade_shadows_tint: float = 0.0
    grade_highlights_temp: float = 0.0
    grade_highlights_tint: float = 0.0
    grain: float = 0.0  # film grain on the export, 0..100 (F23); scaled by intensity, added by emit
    dehaze_add: float = 0.0  # global only: added to the rules' dehaze; below zero it lifts the blacks (F24)
    bw: float = 0.0  # black and white, 0..100 (F24); taken as written, the intensity does not scale it
    bw_red: float = 0.0  # the six colour weights, -100..100: how bright each colour comes out in grey
    bw_yellow: float = 0.0
    bw_green: float = 0.0
    bw_cyan: float = 0.0
    bw_blue: float = 0.0
    bw_magenta: float = 0.0


FIELDS = tuple(f.name for f in fields(Look))   # what a look table may contain (profile validation)

_GRADE_FIELDS = ("grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint")
_BW_FIELDS = ("bw", "bw_red", "bw_yellow", "bw_green", "bw_cyan", "bw_blue", "bw_magenta")


LookSet = dict[str, Look]


def load(path: str) -> LookSet:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out: LookSet = {}
    for name, body in data.items():
        out[name] = Look(
            contrast_mul=float(body.get("contrast_mul", 1.0)),
            vibrance_add=float(body.get("vibrance_add", 0.0)),
            saturation_add=float(body.get("saturation_add", 0.0)),
            clarity_mul=float(body.get("clarity_mul", 1.0)),
            dehaze_mul=float(body.get("dehaze_mul", 1.0)),
            exposure_mul=float(body.get("exposure_mul", 1.0)),
            preserve_exposure=bool(body.get("preserve_exposure", False)),
            curve_mul=float(body.get("curve_mul", 1.0)),
            curve_add=float(body.get("curve_add", 0.0)),
            grade_shadows_temp=float(body.get("grade_shadows_temp", 0.0)),
            grade_shadows_tint=float(body.get("grade_shadows_tint", 0.0)),
            grade_highlights_temp=float(body.get("grade_highlights_temp", 0.0)),
            grade_highlights_tint=float(body.get("grade_highlights_tint", 0.0)),
            grain=float(body.get("grain", 0.0)),
            dehaze_add=float(body.get("dehaze_add", 0.0)),
            **{k: float(body.get(k, 0.0)) for k in _BW_FIELDS},
        )
    return out


HABIT = "habit"             # a sub-table of a look, no field of `Look` (F27): habit.<scene>.<target>.<op>
MAX_HABIT_TARGETS = 12
_SCENE = re.compile(r"[a-z0-9_]{1,40}")
_TARGET = re.compile(r"global|place:[a-z0-9_]{1,40}|[a-z][a-z0-9 _-]{0,39}")


def check_habit(table, where: str) -> dict:
    """{scene: {target: {op: float}}} from a look's habit table; a ValueError starting with `where`
    for anything else. A mark is never a target; empty tables are dropped."""
    from .edit.ops import OP_BOUNDS, OP_NAMES    # inside: paths.py imports this module

    if not isinstance(table, dict):
        raise ValueError(f"{where}: {HABIT} must be a table of scenes")
    out: dict = {}
    count = 0
    for scene, targets in table.items():
        if not _SCENE.fullmatch(str(scene)):
            raise ValueError(f"{where}: {HABIT} scene {str(scene)[:40]!r}: use lowercase letters, digits and underscores")
        if not isinstance(targets, dict):
            raise ValueError(f"{where}: {HABIT}.{scene} must be a table of targets")
        for target, ops in targets.items():
            if not _TARGET.fullmatch(str(target)):
                raise ValueError(f"{where}: {HABIT}.{scene} target {str(target)[:40]!r} is not global, place:NAME or a concept name")
            if not isinstance(ops, dict):
                raise ValueError(f"{where}: {HABIT}.{scene}.{target} must be a table of ops")
            for op, value in ops.items():
                if op not in OP_NAMES:
                    raise ValueError(f"{where}: {HABIT}.{scene}.{target} has unknown op {str(op)[:40]!r}")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or (
                        isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError(f"{where}: {HABIT}.{scene}.{target}.{op} must be a finite number")
                lo, hi = OP_BOUNDS.get(op, (-math.inf, math.inf))
                # compared before any float(): a huge whole number would overflow there; NaN fails too
                if not lo <= value <= hi:
                    raise ValueError(f"{where}: {HABIT}.{scene}.{target}.{op} must be between {lo:g} and {hi:g}")
            count += bool(ops)
            if count > MAX_HABIT_TARGETS:
                raise ValueError(f"{where}: {HABIT} has more than {MAX_HABIT_TARGETS} targets")
            if ops:
                out.setdefault(scene, {})[target] = {op: float(v) for op, v in ops.items()}
    return out


def load_habits(path: str) -> dict[str, dict]:
    """Look name -> its checked habit (looks without one are left out)."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    habits = {}
    for name, body in data.items():
        if isinstance(body, dict) and HABIT in body:
            got = check_habit(body[HABIT], f"{path}: look {name}")
            if got:
                habits[name] = got
    return habits


def shape(settings: Settings, look: Look, intensity: float, is_global: bool = False) -> None:
    """In-place: apply the look's modifiers, then scale the whole block by intensity.

    The split-tone/curve finishing only applies to the global block (is_global=True) so it
    isn't double-applied per region.
    """
    if "contrast" in settings:
        settings["contrast"] *= look.contrast_mul
    if "clarity" in settings:
        settings["clarity"] *= look.clarity_mul
    if "dehaze" in settings:
        settings["dehaze"] *= look.dehaze_mul
    if settings.get("exposure", 0.0) > 0.0:   # only the lift: a rule that darkens keeps its value
        settings["exposure"] *= look.exposure_mul
    if "curve" in settings:
        settings["curve"] *= look.curve_mul
    if look.vibrance_add:
        settings["vibrance"] = settings.get("vibrance", 0.0) + look.vibrance_add
    if is_global:
        if look.saturation_add:  # global-only so the skin-safe guard (which strips saturation) protects faces
            settings["saturation"] = settings.get("saturation", 0.0) + look.saturation_add
        if look.curve_add:
            settings["curve"] = settings.get("curve", 0.0) + look.curve_add
        if look.dehaze_add:
            settings["dehaze"] = settings.get("dehaze", 0.0) + look.dehaze_add
        for k in _GRADE_FIELDS:
            v = getattr(look, k)
            if v:
                settings[k] = settings.get(k, 0.0) + v
    for k in settings:
        settings[k] *= float(intensity)
