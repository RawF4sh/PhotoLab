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

# Auto-picker policy — the thresholds/caps behind auto_palette_for_image. Rules-as-data: this is
# a stats→decision mapping (which palette fires, how strongly), the same category rules.toml
# expressions govern, so it's tunable from `configs/palettes.toml` (a [auto.<palette>] table) —
# not a code change. These are the shipped defaults, used verbatim when the TOML has no [auto]
# section (older configs keep working) and as the per-key fallback for a partial override.
AUTO_DEFAULTS: dict[str, dict[str, float]] = {
    "cool_blue": {
        # Hard veto: skip entirely at/above this measured warmth (blind-test losses were
        # +0.015/+0.023 warm scenes that got a wrongly-cool grade).
        "warmth_veto": 0.008,
        "cool_bonus_threshold": -0.006,   # extra score credit only when GENUINELY cool, not merely neutral
        "haze_threshold": 0.38,
        "chroma_threshold": 0.035,
        "sat_threshold": 0.12,
        "score_threshold": 0.58,
        "strength_base": 0.25,
        "strength_cap": 0.40,             # inferred grades whisper; explicit picks aren't capped
    },
    "warm_gold": {
        "warmth_threshold": 0.030,
        "mean_lum_threshold": 0.42,
        "sky_lum_threshold": 0.55,
        "strength_base": 0.40,
        "strength_cap": 0.55,
    },
    "forest_olive": {
        "chroma_threshold": 0.035,
        "score_threshold": 0.48,
        "strength_base": 0.45,
        "strength_cap": 0.55,
    },
    "muted_film": {
        "chroma_threshold": 0.022,
        "sat_threshold": 0.10,
        "strength": 0.45,
    },
}


def load_auto_palette_config(path: str) -> dict[str, dict[str, float]]:
    """Load the `[auto.<palette>]` policy tables from palettes.toml — the tunable thresholds/caps
    behind the stats-based auto-picker. Missing file/section/key → the shipped default for that
    key, so the picker degrades safely and a partial override only touches what it names."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError):
        data = {}
    auto = data.get("auto", {})
    return {
        name: {k: float(auto.get(name, {}).get(k, v)) for k, v in defaults.items()}
        for name, defaults in AUTO_DEFAULTS.items()
    }


def load_palettes(path: str) -> PaletteSet:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return {}
    out: PaletteSet = {}
    for name, body in data.items():
        if name == "auto":  # [auto.<palette>] holds auto-picker policy, not a palette definition
            continue
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


def auto_palette_for_image(scene: str, gstats, present: set[str], palettes: PaletteSet,
                           auto_cfg: dict[str, dict[str, float]] | None = None
                           ) -> tuple[str | None, float, str]:
    """Choose a palette from image stats when there is no explicit/prompt colour intent.

    Returns (palette_name, confidence/strength scale, reason). Confidence is deliberately
    conservative: uncertain images get no palette, and automatic grades are weaker than explicit
    user selections. `auto_cfg` (from `load_auto_palette_config` / `configs/palettes.toml`
    `[auto.*]`) supplies the thresholds/caps; omitted keys/sections fall back to AUTO_DEFAULTS.
    """
    if not palettes:
        return None, 0.0, ""
    cfg = auto_cfg or AUTO_DEFAULTS

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
    # RESTRAINT (blind-test finding): an UNREQUESTED grade must whisper, and it must never fight
    # the scene's measured warmth — both blind-test losses were warm scenes (+0.015/+0.023) that
    # got a 72% cool grade. Hard warmth veto; real coolness required for a full score; strength
    # capped (explicit user picks keep full strength — only inferred grades are capped).
    c = cfg["cool_blue"]
    if "cool_blue" in palettes and landscape and has_cool_context and warmth < c["warmth_veto"]:
        cool_score = 0.0
        cool_score += 0.30 if haze > c["haze_threshold"] else 0.0
        cool_score += 0.28 if chroma < c["chroma_threshold"] or mean_sat < c["sat_threshold"] else 0.0
        cool_score += 0.20 if warmth < c["cool_bonus_threshold"] else 0.0  # genuinely cool, not merely neutral
        cool_score += 0.14 if present & {"snow", "water"} else 0.0
        if cool_score >= c["score_threshold"]:
            strength = min(c["strength_cap"], c["strength_base"] + (cool_score - 0.55))
            return "cool_blue", strength, "cool low-chroma landscape"

    # Golden-hour / warm-scene read: already-warm highlights, bright sky, not a snowy/cool scene.
    c = cfg["warm_gold"]
    if "warm_gold" in palettes and warmth > c["warmth_threshold"] and mean_lum > c["mean_lum_threshold"]:
        warm_score = c["strength_base"] + min(0.15, (warmth - c["warmth_threshold"]) * 3.0)
        if sky_lum > c["sky_lum_threshold"]:
            warm_score += 0.05
        if "snow" not in present:
            return "warm_gold", min(c["strength_cap"], warm_score), "warm bright scene"

    # Green-heavy frames benefit from a restrained olive film palette; skip when a broad sky/water
    # context suggests the colour story should be blue instead.
    c = cfg["forest_olive"]
    if "forest_olive" in palettes and has_green_context and not (present & {"sky", "water"}):
        green_score = c["strength_base"] + min(0.10, max(0.0, chroma - c["chroma_threshold"]) * 3.0)
        if green_score >= c["score_threshold"]:
            return "forest_olive", min(c["strength_cap"], green_score), "green-dominant scene"

    # Low-colour non-winter frames get only a very gentle film mute.
    c = cfg["muted_film"]
    if ("muted_film" in palettes and chroma < c["chroma_threshold"] and mean_sat < c["sat_threshold"]
            and not has_cool_context):
        return "muted_film", c["strength"], "low-colour scene"

    return None, 0.0, ""
