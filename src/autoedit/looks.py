"""Looks (port of the Rust looks.rs) — magnitude shaping over the rule baseline.

A look never adds new decisions; it scales/offsets existing ones, then the whole settings
block is scaled by `intensity`. Final = clamp(base * look_mul + look_add) * intensity.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from .contract import Settings


@dataclass
class Look:
    contrast_mul: float = 1.0
    vibrance_add: float = 0.0
    clarity_mul: float = 1.0
    dehaze_mul: float = 1.0
    # global-only finishing the style injects (split-tone + filmic curve)
    curve_add: float = 0.0
    grade_shadows_temp: float = 0.0
    grade_shadows_tint: float = 0.0
    grade_highlights_temp: float = 0.0
    grade_highlights_tint: float = 0.0


_GRADE_FIELDS = ("grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint")


LookSet = dict[str, Look]


def load(path: str) -> LookSet:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out: LookSet = {}
    for name, body in data.items():
        out[name] = Look(
            contrast_mul=float(body.get("contrast_mul", 1.0)),
            vibrance_add=float(body.get("vibrance_add", 0.0)),
            clarity_mul=float(body.get("clarity_mul", 1.0)),
            dehaze_mul=float(body.get("dehaze_mul", 1.0)),
            curve_add=float(body.get("curve_add", 0.0)),
            grade_shadows_temp=float(body.get("grade_shadows_temp", 0.0)),
            grade_shadows_tint=float(body.get("grade_shadows_tint", 0.0)),
            grade_highlights_temp=float(body.get("grade_highlights_temp", 0.0)),
            grade_highlights_tint=float(body.get("grade_highlights_tint", 0.0)),
        )
    return out


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
    if look.vibrance_add:
        settings["vibrance"] = settings.get("vibrance", 0.0) + look.vibrance_add
    if is_global:
        if look.curve_add:
            settings["curve"] = settings.get("curve", 0.0) + look.curve_add
        for k in _GRADE_FIELDS:
            v = getattr(look, k)
            if v:
                settings[k] = settings.get(k, 0.0) + v
    for k in settings:
        settings[k] *= float(intensity)
