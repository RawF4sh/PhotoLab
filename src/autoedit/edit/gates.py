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


# Default post-edit gate thresholds. Overridable via rules.toml [quality_gate] (rules-as-data;
# the pipeline threads that block through as `thr`) — these are the fallbacks.
GATE_DEFAULTS = {
    "highlight_clip": 0.02,   # post-edit highlight clipping above this → warn + penalty
    "shadow_clip": 0.06,      # post-edit shadow clipping above this → warn + penalty
    "sat_delta": 0.10,        # mean saturation rise vs source above this → warn + penalty
    "review_score": 0.85,     # quality score below this → needs review
    "review_highlight": 0.05,  # hard review triggers regardless of score
    "review_shadow": 0.12,
    "clip_budget": 0.005,    # added clipped fraction vs source; a hard review trigger
    "clip_penalty": 0.25,
    # Source-relative darkness regression: a source whose (sky-excluded) foreground median or
    # bottom band sits below `dark_source` must not come out more than `dark_regress` darker.
    "dark_source": 0.30,
    "dark_regress": 0.01,
}


def assess(pre, post, chroma_limited: float = 0.0,
           thr: dict | None = None, preserve_exposure: bool = False) -> tuple[float, bool, str, list[str]]:
    """Judge the EDITED output against the source (both GlobalStatsModel). Returns
    (quality_score 0..1, needs_review, review_reason, warnings).

    This is the gate the review queue must read — it inspects post-edit clipping, the
    saturation delta vs source, and how hard the oversaturation gate had to work. `pre`/`post`
    are duck-typed (anything with highlight_clip / shadow_clip / mean_sat). `thr` overrides
    GATE_DEFAULTS per key (rules.toml [quality_gate])."""
    t = {**GATE_DEFAULTS, **(thr or {})}
    warnings: list[str] = []
    score = 1.0

    hi, lo = float(post.highlight_clip), float(post.shadow_clip)
    # A small newly clipped area can destroy snow/cloud detail while the absolute clipped
    # fraction is still below the old warning threshold. Check the source-relative increase
    # independently of score, including single-channel clipping that luminance can hide.
    introduced_clipping = False
    for label, key in (("highlight", "highlight_clip"),
                       ("RGB highlight", "rgb_highlight_clip_any")):
        before = float(getattr(pre, key, 0.0))
        after = float(getattr(post, key, 0.0))
        if after - before > t["clip_budget"]:
            introduced_clipping = True
            warnings.append(f"{label} clipping increased +{(after - before) * 100:.2f}% "
                            f"({before * 100:.2f}% → {after * 100:.2f}%)")
    if introduced_clipping:
        score -= t["clip_penalty"]
    if hi > t["highlight_clip"]:
        warnings.append(f"highlight clipping {hi * 100:.1f}%")
        score -= min(0.35, (hi - t["highlight_clip"]) * 4.0)
    if lo > t["shadow_clip"]:
        warnings.append(f"shadow clipping {lo * 100:.1f}%")
        score -= min(0.30, (lo - t["shadow_clip"]) * 3.0)

    dsat = float(post.mean_sat) - float(pre.mean_sat)
    if dsat > t["sat_delta"]:
        warnings.append(f"saturation rose +{dsat * 100:.0f}%")
        score -= min(0.25, (dsat - t["sat_delta"]) * 2.0)

    if chroma_limited > 0.25:
        warnings.append(f"oversaturation gate worked hard ({chroma_limited * 100:.0f}% of frame)")
        score -= 0.05  # the gate already corrected it; flag for a look, don't fail hard

    # Darkness regression (dark-RAW diagnosis): a dark source made materially darker is the one
    # failure the old gate could not see — it scored a dark→darker edit 0.94. RELATIVE to the
    # source, never an absolute brightness: a night frame left alone passes, a bright frame is
    # untouched, and an explicit moody/preserve intent (`preserve_exposure`) opts out. Only for
    # freshly measured stats (pro_stats_valid) — zero-filled legacy models must not trip it.
    if not preserve_exposure and float(getattr(pre, "pro_stats_valid", 0.0)) > 0:
        for label, a, b in (
            ("foreground median", getattr(pre, "fg_p50", 0.0), getattr(post, "fg_p50", 0.0)),
            ("bottom-band median", getattr(pre, "band_bottom_p50", 0.0), getattr(post, "band_bottom_p50", 0.0)),
        ):
            a, b = float(a), float(b)
            if a < t["dark_source"] and b < a - t["dark_regress"]:
                warnings.append(f"dark source made darker ({label} {a:.2f} → {b:.2f})")
                score -= 0.25
                break

    score = max(0.0, round(score, 3))
    needs_review = (introduced_clipping or score < t["review_score"]
                    or hi > t["review_highlight"] or lo > t["review_shadow"])
    reason = "; ".join(warnings) if needs_review else ""
    return score, needs_review, reason, warnings


def chroma_guard(
    source: np.ndarray,
    edited: np.ndarray,
    max_increase: float = 0.35,
    neutral_allowance: float = 0.02,
    protect: np.ndarray | None = None,
    protect_increase: float = 0.05,
    highlight_lum: float = 0.90,
) -> tuple[np.ndarray, float]:
    """Limit how much more saturated `edited` may be than `source`.

    source/edited: float HxWx3 gamma RGB in [0,1] (same shape).
    max_increase:   a pixel's chroma may grow by at most this fraction (0.35 → +35%), plus a
                    tiny additive so near-neutral pixels can still gain a little life.
    neutral_allowance:
                    additive chroma budget for near-neutral pixels. Defaults conservative; an
                    explicit palette can raise it for intentional cool/warm grading.
    protect:        float HxW [0,1] mask (snow/skin) held to `protect_increase` instead.
    highlight_lum:  pixels brighter than this keep ~zero chroma gain (no fringing on whites).

    Returns (guarded_rgb, fraction_of_pixels_pulled_back).
    """
    src_c = _chroma(source)
    out_c = _chroma(edited)

    allowance = max(0.0, float(neutral_allowance))
    ceiling = src_c * (1.0 + max_increase) + allowance
    if protect is not None:
        # Feathered protection: lerp toward the tight ceiling by the mask's alpha, so soft
        # skin/snow edges tighten gradually instead of flipping at an arbitrary 0.5 cut.
        p = np.clip(protect.astype(np.float32), 0.0, 1.0)
        protected = np.minimum(ceiling, src_c * (1.0 + protect_increase) + 0.008)
        ceiling = ceiling * (1.0 - p) + protected * p

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
