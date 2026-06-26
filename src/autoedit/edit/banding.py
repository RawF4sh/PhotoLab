"""Banding / posterization detector for smooth regions (spec Phase 1).

Heavy tone moves (dehaze, contrast, curve) on a low-texture area with a narrow tonal range — sky,
mist, smooth water — spread its few 8-bit levels into visible contour steps. This detector measures
that risk on the *smooth* part of the frame only (textured forest/rock naturally has tonal edges and
must not be punished), and is **source-relative** so we only flag banding the edit introduced.

Score combines tonal occupancy (too few levels used) and plateau strength (long near-equal runs).
The pipeline uses it to auto-remediate: when banding is detected it reduces the stretch ops and
re-applies. Pure measurement — no pixels are painted.
"""

from __future__ import annotations

import numpy as np

from ..imageio import luma
from .filters import gaussian_blur

# Default thresholds (overridable via rules.toml [banding]); from the spec's §11 table.
DEFAULTS = {"warning": 0.12, "reduce_ops": 0.22, "hard_fail": 0.35, "min_coverage": 0.03,
            "absolute_weight": 0.65}


def smooth_region_mask(lum: np.ndarray, hf_thresh: float = 0.012, sigma: float = 9.0) -> np.ndarray:
    """Boolean mask of low-texture pixels (|detail| below threshold) — the banding-prone areas."""
    detail = np.abs(lum - gaussian_blur(lum, sigma))
    return detail < hf_thresh


def _plateau_strength(q: np.ndarray, smooth: np.ndarray) -> float:
    """Fraction of horizontally-adjacent smooth pixels whose quantized luma is equal — long flat
    runs are exactly the visible bands. (Captures 2D posterized plateaus.)"""
    eq = np.abs(np.diff(q.astype(np.int16), axis=1)) < 1
    m = smooth[:, 1:] & smooth[:, :-1]
    if m.sum() < 16:
        return 0.0
    return float(eq[m].mean())


def _level_density(q: np.ndarray, smooth: np.ndarray) -> float:
    """occupied codes / spanned code range, over the smooth region. 1.0 = every code in the range
    is used (a clean smooth gradient); low = posterized (few levels) or stretched (gaps between
    levels). This is the signal that distinguishes 'banded' from merely 'narrow-range smooth'."""
    vals = q[smooth]
    if vals.size < 16:
        return 1.0
    rng = float(vals.max() - vals.min())
    if rng < 1.0:
        return 1.0
    return float(np.unique(vals).size) / (rng + 1.0)


def banding_score(src_lum: np.ndarray, edt_lum: np.ndarray, smooth: np.ndarray) -> float:
    """0 (clean) … 1 (severe). SOURCE-RELATIVE: density the edit lost (posterize/gaps) + new flat
    plateaus it introduced. A clean smooth sky scores ~0 even with few levels."""
    if smooth.mean() < 0.005:
        return 0.0
    eq = np.clip(np.round(edt_lum * 255.0), 0, 255)
    sq = np.clip(np.round(src_lum * 255.0), 0, 255)
    density_loss = max(0.0, _level_density(sq, smooth) - _level_density(eq, smooth))
    new_plateau = max(0.0, _plateau_strength(eq, smooth) - _plateau_strength(sq, smooth))
    return float(np.clip(0.65 * density_loss + 0.45 * new_plateau, 0.0, 1.0))


def absolute_banding_score(edt_lum: np.ndarray, smooth: np.ndarray) -> float:
    """Absolute posterization risk in the edited smooth region.

    The source-relative score is great for catching newly introduced bands, but it misses images
    that arrive already quantized or screenshot/JPEG-derived. For those, a smooth sky with sparse
    occupied luma levels is still risky after edits, so score low tonal density directly.
    """
    if smooth.mean() < 0.005:
        return 0.0
    q = np.clip(np.round(edt_lum * 255.0), 0, 255)
    vals = q[smooth]
    if vals.size < 16:
        return 0.0
    span = float(vals.max() - vals.min())
    if span < 12.0:
        return 0.0
    density = _level_density(q, smooth)
    return float(np.clip((0.72 - density) / 0.72, 0.0, 1.0))


def assess(src_rgb: np.ndarray, edt_rgb: np.ndarray, thr: dict | None = None
           ) -> tuple[float, str, float]:
    """Return (score, action, smooth_coverage). action ∈ pass | warn | reduce | fail."""
    t = {**DEFAULTS, **(thr or {})}
    smooth = smooth_region_mask(luma(src_rgb))
    cov = float(smooth.mean())
    if cov < t["min_coverage"]:
        return 0.0, "pass", cov
    rel = banding_score(luma(src_rgb), luma(edt_rgb), smooth)
    abs_score = absolute_banding_score(luma(edt_rgb), smooth) * t["absolute_weight"]
    score = max(rel, abs_score)
    if score > t["hard_fail"]:
        action = "fail"
    elif score > t["reduce_ops"]:
        action = "reduce"
    elif score > t["warning"]:
        action = "warn"
    else:
        action = "pass"
    return score, action, cov
