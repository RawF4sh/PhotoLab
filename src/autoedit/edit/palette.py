"""Regional split-tone grade in OKLab — the palette pixel op.

Pushes shadow / midtone / highlight colour toward the palette's per-zone hue+chroma targets while
leaving OKLab L (perceptual lightness) untouched — so it changes *colour*, not brightness. Whites
and bright highlights resist tinting (neutral protection), and an optional `protect` alpha (the
skin-safe guard mask) holds snow/skin fully neutral. Strength 0 (or an all-zero palette) is a
strict no-op, so the default path stays byte-identical.
"""

from __future__ import annotations

import numpy as np

from ..imageio import linear_to_srgb, srgb_to_linear
from ..palette import Palette
from .oklab import linear_to_oklab, oklab_to_linear


def _ab(hue_deg: float, chroma: float) -> tuple[float, float]:
    r = np.deg2rad(hue_deg)
    return chroma * np.cos(r), chroma * np.sin(r)


def palette_grade(rgb: np.ndarray, pal: Palette, strength: float,
                  protect: np.ndarray | None = None) -> np.ndarray:
    """rgb: gamma sRGB HxWx3 [0,1]. Returns the graded image (same shape/space)."""
    if strength <= 0.0 or pal is None:
        return rgb
    lin0 = srgb_to_linear(rgb)
    lab = linear_to_oklab(lin0)
    L = lab[..., 0]

    # tonal-zone weights from perceptual lightness; a triangular midtone peak between them
    sh = np.clip((0.5 - L) / 0.5, 0.0, 1.0)
    hi = np.clip((L - 0.5) / 0.5, 0.0, 1.0)
    mid = np.clip(1.0 - sh - hi, 0.0, 1.0)

    sa, sb = _ab(pal.shadow_hue, pal.shadow_chroma)
    ma, mb = _ab(pal.mid_hue, pal.mid_chroma)
    ha, hb = _ab(pal.highlight_hue, pal.highlight_chroma)
    ta = sh * sa + mid * ma + hi * ha          # blended target offset in the OKLab a/b plane
    tb = sh * sb + mid * mb + hi * hb

    # neutral protection: keep highlights/whites near-neutral so snow/clouds don't tint
    push = strength * (1.0 - pal.neutral_protection * hi)
    if protect is not None:
        push = push * (1.0 - protect)          # guard mask (skin/snow) → no tint there

    lab[..., 1] = lab[..., 1] + ta * push      # shift colour; L (lab[...,0]) is left untouched
    lab[..., 2] = lab[..., 2] + tb * push
    lin = oklab_to_linear(lab)

    # Gamut soft-roll: where the grade pushes a pixel out of [0,1] linear, blend back toward the
    # ungraded colour proportionally to the overflow — a smooth rolloff instead of a hard per-channel
    # clip, which posterizes smooth gradients (banded/"pixelated" skies). In-gamut pixels are untouched.
    over = np.clip(np.maximum(lin.max(-1) - 1.0, -lin.min(-1)), 0.0, None)[..., None]
    w = 1.0 / (1.0 + 8.0 * over)
    lin = lin0 * (1.0 - w) + lin * w
    return np.clip(linear_to_srgb(lin), 0.0, 1.0).astype(np.float32)
