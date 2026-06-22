"""Quality gates — measure the edit against the source and pull it back where it goes
unnatural, instead of trusting every op to behave (AutoEdit Pipeline Fixes v0.3, P0).

Today: the **chroma (oversaturation) gate**. After the full edit, no pixel may be more
colorful than the source allows — we cap each pixel's chroma *increase* relative to the
original. Near-white highlights and any `protect` region (snow/skin) are held to almost no
increase (that's where oversaturation and colour fringing read worst). The cap desaturates
toward the pixel's own luma, so hue and brightness are preserved — it only removes the excess
saturation, which is exactly the 'oversaturated / colour cast' failure.

This is a *final safety pass*, not per-op dampening: it catches oversaturation no matter which
op (vibrance, HSL, grade, dehaze, contrast) produced it.
"""

from __future__ import annotations

import numpy as np

from ..imageio import luma


def _chroma(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel colorfulness in [0,1]: max channel − min channel (HSV-style chroma)."""
    return rgb.max(axis=-1) - rgb.min(axis=-1)


def chroma_guard(
    source: np.ndarray,
    edited: np.ndarray,
    max_increase: float = 0.35,
    protect: np.ndarray | None = None,
    protect_increase: float = 0.05,
    highlight_lum: float = 0.90,
) -> tuple[np.ndarray, float]:
    """Limit how much more saturated `edited` may be than `source`.

    source/edited: float HxWx3 gamma RGB in [0,1] (same shape).
    max_increase:   a pixel's chroma may grow by at most this fraction (0.35 → +35%), plus a
                    tiny additive so near-neutral pixels can still gain a little life.
    protect:        float HxW [0,1] mask (snow/skin) held to `protect_increase` instead.
    highlight_lum:  pixels brighter than this keep ~zero chroma gain (no fringing on whites).

    Returns (guarded_rgb, fraction_of_pixels_pulled_back).
    """
    src_c = _chroma(source)
    out_c = _chroma(edited)

    ceiling = src_c * (1.0 + max_increase) + 0.02
    if protect is not None:
        p = protect > 0.5
        ceiling = np.where(p, src_c * (1.0 + protect_increase) + 0.008, ceiling)

    hi = luma(edited) > highlight_lum
    ceiling = np.where(hi, np.minimum(ceiling, src_c * 1.02 + 0.004), ceiling)

    over = out_c > ceiling
    if not over.any():
        return edited.astype(np.float32), 0.0

    # Scale chroma down to the ceiling by pulling the pixel toward its own luma (gray axis),
    # which preserves brightness and hue and only sheds the excess saturation.
    l = luma(edited)[..., None]
    scale = np.ones_like(out_c)
    np.divide(ceiling, out_c, out=scale, where=over)
    scale = np.clip(scale, 0.0, 1.0)[..., None]
    guarded = l + (edited - l) * scale
    return np.clip(guarded, 0.0, 1.0).astype(np.float32), float(over.mean())
