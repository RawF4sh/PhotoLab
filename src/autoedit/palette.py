"""Palettes — regional colour grading targets (the universal "make it blue/teal/gold" layer).

A palette is *data*: per-zone (shadow / midtone / highlight) hue + chroma targets in OKLab, plus a
neutral-protection strength that keeps whites/snow from tinting. The pixel op lives in
`edit/palette.py`; this module is just the model + loader + a small prompt→palette guess so typing
"cool blue" or "sunset" in the intent box selects the right palette, plus a conservative
stats-based auto picker for when the user gives no colour direction. Add a palette here (or in
configs/palettes.toml), no pixel-op code change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass


@dataclass
class Palette:
    name: str
    # OKLab hue (degrees, atan2(b,a)) + chroma per tonal zone. chroma ~0.02–0.08 is natural; >0.12 is bold.
    shadow_hue: float = 0.0
    shadow_chroma: float = 0.0
    mid_hue: float = 0.0
    mid_chroma: float = 0.0
    highlight_hue: float = 0.0
    highlight_chroma: float = 0.0
    neutral_protection: float = 0.85  # 0..1 — how strongly highlights/whites resist tinting
    protected: tuple[str, ...] = ()   # concept names to hold near-neutral (future: per-region mask)


PaletteSet = dict[str, Palette]


def load_palettes(path: str) -> PaletteSet:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return {}
    out: PaletteSet = {}
    for name, body in data.items():
        out[name] = Palette(
            name=name,
            shadow_hue=float(body.get("shadow_hue", 0.0)),
            shadow_chroma=float(body.get("shadow_chroma", 0.0)),
            mid_hue=float(body.get("mid_hue", 0.0)),
            mid_chroma=float(body.get("mid_chroma", 0.0)),
            highlight_hue=float(body.get("highlight_hue", 0.0)),
            highlight_chroma=float(body.get("highlight_chroma", 0.0)),
            neutral_protection=float(body.get("neutral_protection", 0.85)),
            protected=tuple(body.get("protected", [])),
        )
    return out


# Words that select a palette when they appear in the intent prompt. Longer phrases first so
# "cool blue" beats "blue". Matched as substrings (lowercased).
_PROMPT_MAP = [
    ("teal orange", "teal_orange"), ("teal-orange", "teal_orange"), ("cinematic", "teal_orange"),
    ("cool blue", "cool_blue"), ("blue hour", "cool_blue"), ("winter blue", "cool_blue"),
    ("golden hour", "warm_gold"), ("sunset", "warm_gold"), ("golden", "warm_gold"),
    ("warm gold", "warm_gold"), ("teal", "teal_orange"), ("forest", "forest_olive"),
    ("olive", "forest_olive"), ("muted", "muted_film"), ("faded", "muted_film"),
    ("blue", "cool_blue"),
]


def palette_for_prompt(prompt: str | None, palettes: PaletteSet) -> str | None:
    """Best-guess palette name from a free-text intent, or None. Explicit selection wins upstream."""
    if not prompt:
        return None
    text = prompt.lower()
    for phrase, name in _PROMPT_MAP:
        if phrase in text and name in palettes:
            return name
    return None


def auto_palette_for_image(scene: str, gstats, present: set[str], palettes: PaletteSet
                           ) -> tuple[str | None, float, str]:
    """Choose a palette from image stats when there is no explicit/prompt colour intent.

    Returns (palette_name, confidence/strength scale, reason). Confidence is deliberately
    conservative: uncertain images get no palette, and automatic grades are weaker than explicit
    user selections.
    """
    if not palettes:
        return None, 0.0, ""

    present = set(present or ())
    mean_lum = float(getattr(gstats, "mean_lum", 0.0))
    mean_sat = float(getattr(gstats, "mean_sat", 0.0))
    haze = float(getattr(gstats, "haze", 0.0))
    warmth = float(getattr(gstats, "warmth", 0.0))
    chroma = float(getattr(gstats, "mean_chroma", 0.0))
    sky_lum = float(getattr(gstats, "sky_lum", 0.0))

    landscape = scene == "landscape"
    has_cool_context = bool(present & {"sky", "water", "mountain", "snow"})
    has_green_context = bool(present & {"grass", "foliage", "trees"})

    # Winter / blue-hour read: low colour, cool/neutral cast, haze or snow/sky/water context.
    if "cool_blue" in palettes and landscape and has_cool_context:
        cool_score = 0.0
        cool_score += 0.30 if haze > 0.38 else 0.0
        cool_score += 0.28 if chroma < 0.035 or mean_sat < 0.12 else 0.0
        cool_score += 0.18 if warmth < 0.012 else 0.0
        cool_score += 0.14 if present & {"snow", "water"} else 0.0
        if cool_score >= 0.58:
            return "cool_blue", min(0.85, cool_score), "cool low-chroma landscape"

    # Golden-hour / warm-scene read: already-warm highlights, bright sky, not a snowy/cool scene.
    if "warm_gold" in palettes and warmth > 0.030 and mean_lum > 0.42:
        warm_score = 0.55 + min(0.25, (warmth - 0.030) * 4.0)
        if sky_lum > 0.55:
            warm_score += 0.10
        if "snow" not in present:
            return "warm_gold", min(0.80, warm_score), "warm bright scene"

    # Green-heavy frames benefit from a restrained olive film palette; skip when a broad sky/water
    # context suggests the colour story should be blue instead.
    if "forest_olive" in palettes and has_green_context and not (present & {"sky", "water"}):
        green_score = 0.50 + min(0.20, max(0.0, chroma - 0.035) * 3.0)
        if green_score >= 0.55:
            return "forest_olive", min(0.70, green_score), "green-dominant scene"

    # Low-colour non-winter frames get only a very gentle film mute.
    if "muted_film" in palettes and chroma < 0.022 and mean_sat < 0.10 and not has_cool_context:
        return "muted_film", 0.45, "low-colour scene"

    return None, 0.0, ""
