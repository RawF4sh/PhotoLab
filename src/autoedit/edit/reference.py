"""Reference-style color matching — "edit like this photo".

Transfers the *palette and tonality* of a reference image onto the target using Reinhard
color transfer in CIELAB: match the per-channel mean + standard deviation of L*, a*, b*.
This is the no-training "pick a look by example" path — give it a hero edit (or any image
with a look you like) and the whole batch is graded toward it, blended by `strength`.

numpy-only (core dep). It grades the *real* pixels — it never fabricates content (no sky
replacement); it only shifts color/tone distribution.
"""

from __future__ import annotations

import numpy as np

from ..imageio import linear_to_srgb, luma, srgb_to_linear
from .ops import OP_BOUNDS, apply_ops

# linear sRGB <-> XYZ (D65)
_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                     [0.2126729, 0.7151522, 0.0721750],
                     [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_XYZ2RGB = np.linalg.inv(_RGB2XYZ).astype(np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)  # D65
_EPS = np.float32(216.0 / 24389.0)   # (6/29)^3
_KAPPA = np.float32(24389.0 / 27.0)  # (29/3)^3


def _f(t: np.ndarray) -> np.ndarray:
    return np.where(t > _EPS, np.cbrt(t), (_KAPPA * t + 16.0) / 116.0)


def _f_inv(t: np.ndarray) -> np.ndarray:
    t3 = t ** 3
    return np.where(t3 > _EPS, t3, (116.0 * t - 16.0) / _KAPPA)


def rgb_to_lab(rgb01: np.ndarray) -> np.ndarray:
    """sRGB-gamma [0,1] HxWx3 -> CIELAB (L in ~[0,100], a/b ~[-128,128])."""
    lin = srgb_to_linear(np.clip(rgb01, 0.0, 1.0))
    xyz = lin.reshape(-1, 3) @ _RGB2XYZ.T
    fr = _f(xyz / _WHITE)
    fx, fy, fz = fr[:, 0], fr[:, 1], fr[:, 2]
    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=1)
    return lab.reshape(rgb01.shape).astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB HxWx3 -> sRGB-gamma [0,1]."""
    flat = lab.reshape(-1, 3)
    fy = (flat[:, 0] + 16.0) / 116.0
    fx = fy + flat[:, 1] / 500.0
    fz = fy - flat[:, 2] / 200.0
    xyz = np.stack([_f_inv(fx), _f_inv(fy), _f_inv(fz)], axis=1) * _WHITE
    lin = xyz @ _XYZ2RGB.T
    rgb = linear_to_srgb(np.clip(lin, 0.0, 1.0))
    return rgb.reshape(lab.shape).astype(np.float32)


def _stats(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flat = lab.reshape(-1, 3)
    return flat.mean(axis=0), flat.std(axis=0)


def _pixsat(rgb: np.ndarray) -> np.ndarray:
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    return np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0).astype(np.float32)


def _hue_masks(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    d = mx - mn
    h = np.zeros_like(mx, dtype=np.float32)
    nz = d > 1e-6
    red = (mx == r) & nz
    green = (mx == g) & nz
    blue = (mx == b) & nz
    h[red] = (60.0 * ((g - b)[red] / d[red])) % 360.0
    h[green] = (60.0 * ((b - r)[green] / d[green]) + 120.0) % 360.0
    h[blue] = (60.0 * ((r - g)[blue] / d[blue]) + 240.0) % 360.0

    sat = _pixsat(rgb)

    def band(center: float, width: float) -> np.ndarray:
        dist = np.abs((h - center + 180.0) % 360.0 - 180.0)
        return np.clip(1.0 - dist / width, 0.0, 1.0) * sat

    return band(120.0, 70.0).astype(np.float32), band(220.0, 70.0).astype(np.float32)


def _mean_weighted(values: np.ndarray, weights: np.ndarray) -> float:
    denom = float(weights.sum())
    if denom <= 1e-6:
        return 0.0
    return float((values * weights).sum() / denom)


def reference_features(rgb01: np.ndarray, mask: np.ndarray | None = None) -> dict[str, float]:
    """Compact look descriptor used by the reference feedback tuner.

    These are scene-level statistics, not pixel correspondence, so the reference can be a
    different crop or photo. The loop intentionally learns broad edit direction: tone depth,
    contrast, color cast, saturation, greenery/sky color, and perceived local detail.
    """
    rgb = np.clip(rgb01, 0.0, 1.0).astype(np.float32)
    lum = luma(rgb)
    if mask is not None and mask.shape == lum.shape:
        sample = mask.astype(np.float32) > 0.05
        if int(sample.sum()) < max(16, int(sample.size * 0.002)):
            sample = np.ones_like(lum, dtype=bool)
    else:
        sample = np.ones_like(lum, dtype=bool)
    lab = rgb_to_lab(rgb)
    sat = _pixsat(rgb)
    green_w, blue_w = _hue_masks(rgb)
    detail = np.zeros_like(lum, dtype=np.float32)
    if lum.shape[1] > 1:
        d = np.abs(np.diff(lum, axis=1))
        detail[:, 1:] += d
        detail[:, :-1] += d
    if lum.shape[0] > 1:
        d = np.abs(np.diff(lum, axis=0))
        detail[1:, :] += d
        detail[:-1, :] += d
    chroma = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    lum_s = lum[sample]
    sat_s = sat[sample]
    lab_s = lab[sample]
    chroma_s = chroma[sample]
    detail_s = detail[sample]
    green_s = green_w[sample]
    blue_s = blue_w[sample]

    return {
        "p10": float(np.percentile(lum_s, 10)),
        "p50": float(np.percentile(lum_s, 50)),
        "p90": float(np.percentile(lum_s, 90)),
        "lstd": float(lum_s.std()),
        "sat": float(sat_s.mean()),
        "chroma": float(chroma_s.mean() / 100.0),
        "warmth": float(lab_s[:, 2].mean() / 100.0),
        "tint": float(lab_s[:, 1].mean() / 100.0),
        "detail": float(detail_s.mean()),
        "green_sat": _mean_weighted(sat_s, green_s),
        "green_lum": _mean_weighted(lum_s, green_s),
        "blue_sat": _mean_weighted(sat_s, blue_s),
    }


_LOSS_WEIGHTS = {
    "p10": 1.0,
    "p50": 1.4,
    "p90": 1.0,
    "lstd": 1.6,
    "sat": 1.0,
    "chroma": 0.8,
    "warmth": 0.8,
    "tint": 0.5,
    "detail": 0.9,
    "green_sat": 0.7,
    "green_lum": 0.4,
    "blue_sat": 0.4,
}


def reference_loss(candidate_rgb01: np.ndarray, reference_rgb01: np.ndarray) -> float:
    """Weighted distance between two image looks. Lower means closer to the reference look."""
    cand = reference_features(candidate_rgb01)
    ref = reference_features(reference_rgb01)
    return _feature_loss(cand, ref)


def masked_reference_loss(candidate_rgb01: np.ndarray, reference_rgb01: np.ndarray,
                          mask: np.ndarray) -> float:
    """Reference-look loss inside a mask. Used by the regional feedback loop."""
    cand = reference_features(candidate_rgb01, mask)
    ref = reference_features(reference_rgb01, mask)
    return _feature_loss(cand, ref)


def _feature_loss(candidate: dict[str, float], reference: dict[str, float]) -> float:
    total = 0.0
    for key, weight in _LOSS_WEIGHTS.items():
        total += weight * abs(candidate.get(key, 0.0) - reference.get(key, 0.0))
    return float(total)


def _bounded_add(settings: dict[str, float], key: str, delta: float) -> None:
    if abs(delta) < 1e-6:
        return
    lo, hi = OP_BOUNDS[key]
    settings[key] = float(np.clip(settings.get(key, 0.0) + delta, lo, hi))


def _format_moves(before: dict[str, float], after: dict[str, float]) -> list[str]:
    moves = []
    for key in (
        "exposure", "contrast", "curve", "shadows", "whites", "blacks", "dehaze",
        "clarity", "vibrance", "saturation", "temp", "tint",
        "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat",
    ):
        delta = after.get(key, 0.0) - before.get(key, 0.0)
        if abs(delta) >= 0.05:
            suffix = "EV" if key == "exposure" else ""
            moves.append(f"{key} {delta:+.1f}{suffix}")
    return moves


def tune_settings_to_reference(source_rgb01: np.ndarray, reference_rgb01: np.ndarray,
                               base_settings: dict[str, float] | None = None,
                               strength: float = 0.7, passes: int = 5
                               ) -> tuple[dict[str, float], dict[str, object]]:
    """Iteratively nudge normal edit settings so the source render resembles a reference look.

    This is deliberately not a content generator. Each pass renders `source` with ordinary
    AutoEdit ops, compares broad look features to the reference, nudges bounded sliders, and
    keeps the lowest-loss setting bundle. It generalizes to batches because it learns edit
    intent (darker/brighter, cooler/warmer, greener, punchier, etc.) rather than copied pixels.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    base = {k: float(v) for k, v in (base_settings or {}).items() if abs(float(v)) > 1e-9}
    if strength <= 0.0:
        cand = apply_ops(source_rgb01, base)
        loss = reference_loss(cand, reference_rgb01)
        return base, {"before_loss": loss, "after_loss": loss, "passes": 0, "moves": []}

    ref = reference_features(reference_rgb01)
    current = dict(base)
    best = dict(current)
    best_img = apply_ops(source_rgb01, best)
    best_loss = _feature_loss(reference_features(best_img), ref)
    before_loss = best_loss

    # Decay prevents the loop from seesawing on already-close references.
    for i in range(max(1, int(passes))):
        cand = apply_ops(source_rgb01, current)
        feat = reference_features(cand)
        step = strength * (0.72 ** i)
        trial = dict(current)

        # Tone depth and contrast.
        _bounded_add(trial, "exposure", np.clip((ref["p50"] - feat["p50"]) * 1.15, -0.22, 0.22) * step)
        _bounded_add(trial, "contrast", np.clip((ref["lstd"] - feat["lstd"]) * 95.0, -8.0, 8.0) * step)
        _bounded_add(trial, "curve", np.clip((ref["lstd"] - feat["lstd"]) * 70.0, -6.0, 6.0) * step)
        _bounded_add(trial, "shadows", np.clip((ref["p10"] - feat["p10"]) * 85.0, -9.0, 9.0) * step)
        _bounded_add(trial, "whites", np.clip((ref["p90"] - feat["p90"]) * 60.0, -8.0, 8.0) * step)
        # If the reference has deeper blacks than the candidate, lower blacks; if it is softer,
        # lift them. This helps moody landscape references without crushing every image.
        _bounded_add(trial, "blacks", np.clip((ref["p10"] - feat["p10"]) * 55.0, -7.0, 7.0) * step)

        # Color cast and colorfulness.
        _bounded_add(trial, "temp", np.clip((ref["warmth"] - feat["warmth"]) * 160.0, -7.0, 7.0) * step)
        _bounded_add(trial, "tint", np.clip((ref["tint"] - feat["tint"]) * 110.0, -5.0, 5.0) * step)
        color_delta = 0.55 * (ref["sat"] - feat["sat"]) + 0.45 * (ref["chroma"] - feat["chroma"])
        _bounded_add(trial, "vibrance", np.clip(color_delta * 80.0, -7.0, 7.0) * step)
        _bounded_add(trial, "saturation", np.clip(color_delta * 45.0, -5.0, 5.0) * step)

        # Landscape-specific but still general: separate foliage and blue/cyan haze response.
        _bounded_add(trial, "hsl_green_sat", np.clip((ref["green_sat"] - feat["green_sat"]) * 55.0, -7.0, 7.0) * step)
        _bounded_add(trial, "hsl_green_lum", np.clip((ref["green_lum"] - feat["green_lum"]) * 35.0, -5.0, 5.0) * step)
        _bounded_add(trial, "hsl_blue_sat", np.clip((ref["blue_sat"] - feat["blue_sat"]) * 45.0, -5.0, 5.0) * step)

        # Detail/dehaze: capped tightly because smooth skies are fragile.
        detail_delta = ref["detail"] - feat["detail"]
        _bounded_add(trial, "clarity", np.clip(detail_delta * 180.0, -5.0, 5.0) * step)
        _bounded_add(trial, "dehaze", np.clip(detail_delta * 120.0, -4.0, 4.0) * step)

        trial_img = apply_ops(source_rgb01, trial)
        trial_loss = _feature_loss(reference_features(trial_img), ref)
        if trial_loss <= best_loss:
            best, best_loss = trial, trial_loss
            current = trial
        else:
            # Half-step retry: useful when one feature is already close and the full nudge overshoots.
            retry = dict(current)
            for key, value in trial.items():
                if key in OP_BOUNDS:
                    _bounded_add(retry, key, (value - current.get(key, 0.0)) * 0.5)
            retry_img = apply_ops(source_rgb01, retry)
            retry_loss = _feature_loss(reference_features(retry_img), ref)
            if retry_loss <= best_loss:
                best, best_loss = retry, retry_loss
                current = retry

    moves = _format_moves(base, best)
    return best, {
        "before_loss": float(before_loss),
        "after_loss": float(best_loss),
        "passes": int(max(1, int(passes))),
        "moves": moves,
    }


def tune_regions_to_reference(source_rgb01: np.ndarray, reference_rgb01: np.ndarray,
                              region_settings: dict[str, dict[str, float]],
                              masks: dict[str, np.ndarray],
                              strength: float = 0.7, passes: int = 3
                              ) -> tuple[dict[str, dict[str, float]], list[dict[str, object]]]:
    """Nudge per-region settings toward the reference look where masks are available.

    The caller should pass the source already rendered with global settings. Each region is
    tuned independently against the same spatial mask in the reference, which works best for
    "make this version look like that edited version of the same photo" and degrades to no-op
    when dimensions or masks do not line up.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    tuned = {name: dict(settings) for name, settings in region_settings.items()}
    if strength <= 0.0 or source_rgb01.shape[:2] != reference_rgb01.shape[:2]:
        return tuned, []

    reports: list[dict[str, object]] = []
    for name, mask in masks.items():
        if name not in tuned or mask is None or mask.shape != source_rgb01.shape[:2]:
            continue
        coverage = float((mask > 0.05).mean())
        if coverage < 0.01:
            continue

        current = dict(tuned[name])
        best = dict(current)
        ref = reference_features(reference_rgb01, mask)
        before_img = apply_ops(source_rgb01, best)
        before_loss = _feature_loss(reference_features(before_img, mask), ref)
        best_loss = before_loss

        for i in range(max(1, int(passes))):
            cand_img = apply_ops(source_rgb01, current)
            feat = reference_features(cand_img, mask)
            step = strength * (0.78 ** i)
            trial = dict(current)

            _bounded_add(trial, "exposure", np.clip((ref["p50"] - feat["p50"]) * 0.95, -0.25, 0.25) * step)
            _bounded_add(trial, "highlights", np.clip((ref["p90"] - feat["p90"]) * 70.0, -10.0, 10.0) * step)
            _bounded_add(trial, "shadows", np.clip((ref["p10"] - feat["p10"]) * 60.0, -8.0, 8.0) * step)
            _bounded_add(trial, "contrast", np.clip((ref["lstd"] - feat["lstd"]) * 85.0, -8.0, 8.0) * step)
            _bounded_add(trial, "curve", np.clip((ref["lstd"] - feat["lstd"]) * 60.0, -6.0, 6.0) * step)
            _bounded_add(trial, "temp", np.clip((ref["warmth"] - feat["warmth"]) * 130.0, -6.0, 6.0) * step)
            _bounded_add(trial, "tint", np.clip((ref["tint"] - feat["tint"]) * 90.0, -5.0, 5.0) * step)

            color_delta = 0.6 * (ref["sat"] - feat["sat"]) + 0.4 * (ref["chroma"] - feat["chroma"])
            _bounded_add(trial, "vibrance", np.clip(color_delta * 70.0, -7.0, 7.0) * step)
            _bounded_add(trial, "saturation", np.clip(color_delta * 40.0, -5.0, 5.0) * step)
            _bounded_add(trial, "hsl_green_sat", np.clip((ref["green_sat"] - feat["green_sat"]) * 55.0, -7.0, 7.0) * step)
            _bounded_add(trial, "hsl_green_lum", np.clip((ref["green_lum"] - feat["green_lum"]) * 35.0, -5.0, 5.0) * step)
            _bounded_add(trial, "hsl_blue_sat", np.clip((ref["blue_sat"] - feat["blue_sat"]) * 45.0, -5.0, 5.0) * step)

            detail_delta = ref["detail"] - feat["detail"]
            _bounded_add(trial, "clarity", np.clip(detail_delta * 160.0, -5.0, 5.0) * step)
            _bounded_add(trial, "dehaze", np.clip(detail_delta * 110.0, -4.0, 4.0) * step)

            trial_loss = _feature_loss(reference_features(apply_ops(source_rgb01, trial), mask), ref)
            if trial_loss <= best_loss:
                best, best_loss = trial, trial_loss
                current = trial

        tuned[name] = best
        moves = _format_moves(current if best_loss > before_loss else region_settings.get(name, {}), best)
        if best_loss < before_loss and moves:
            reports.append({
                "name": name,
                "coverage": coverage,
                "before_loss": float(before_loss),
                "after_loss": float(best_loss),
                "moves": moves,
            })

    return tuned, reports


def top_gradient_to_reference(source_rgb01: np.ndarray, reference_rgb01: np.ndarray,
                              strength: float = 0.7) -> tuple[dict[str, float], dict[str, float]]:
    """A conservative graduated-sky nudge learned from a reference.

    If the reference's upper frame is darker or more contrasty than the candidate, return
    ordinary ops for a top linear gradient. This helps stormy landscape looks while staying
    photographic: it only darkens/contrasts existing sky pixels and never adds cloud structure.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0 or source_rgb01.shape[:2] != reference_rgb01.shape[:2]:
        return {}, {"before_top": 0.0, "reference_top": 0.0}

    h = source_rgb01.shape[0]
    cut = max(8, int(round(h * 0.42)))
    src = reference_features(source_rgb01[:cut])
    ref = reference_features(reference_rgb01[:cut])
    mid_delta = ref["p50"] - src["p50"]
    hi_delta = ref["p90"] - src["p90"]
    detail_delta = ref["detail"] - src["detail"]
    contrast_delta = ref["lstd"] - src["lstd"]

    settings: dict[str, float] = {}
    if mid_delta < -0.012:
        settings["exposure"] = float(np.clip(mid_delta * 1.35, -0.42, -0.02) * strength)
    if hi_delta < -0.010:
        settings["highlights"] = float(np.clip(hi_delta * 95.0, -22.0, -2.0) * strength)
    if contrast_delta > 0.010:
        settings["contrast"] = float(np.clip(contrast_delta * 80.0, 2.0, 14.0) * strength)
        settings["curve"] = float(np.clip(contrast_delta * 55.0, 1.0, 9.0) * strength)
    if detail_delta > 0.003:
        settings["clarity"] = float(np.clip(detail_delta * 120.0, 1.0, 8.0) * strength)

    settings = {k: v for k, v in settings.items() if abs(v) >= 0.05}
    return settings, {
        "before_top": float(src["p50"]),
        "reference_top": float(ref["p50"]),
        "detail_delta": float(detail_delta),
    }


def color_transfer(target_rgb01: np.ndarray, reference_rgb01: np.ndarray,
                   strength: float = 0.7, ratio_clamp: tuple[float, float] = (0.4, 2.5)) -> np.ndarray:
    """Grade `target` toward the look of `reference` (Reinhard LAB mean/std match).

    strength: 0 = no change, 1 = full match. ratio_clamp bounds the per-channel std
    stretch so a near-flat target can't explode into wild color. Returns sRGB [0,1]."""
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        return np.clip(target_rgb01, 0.0, 1.0).astype(np.float32)

    t_lab = rgb_to_lab(target_rgb01)
    tm, ts = _stats(t_lab)
    rm, rs = _stats(reference_rgb01 if reference_rgb01.ndim == 2 else rgb_to_lab(reference_rgb01))

    out = t_lab.copy()
    for c in range(3):
        if ts[c] < 1e-4:
            # no variation to rescale; just shift the channel mean toward the reference
            out[..., c] = t_lab[..., c] + strength * (rm[c] - tm[c])
            continue
        gain = float(np.clip(rs[c] / ts[c], ratio_clamp[0], ratio_clamp[1]))
        matched = (t_lab[..., c] - tm[c]) * gain + rm[c]
        out[..., c] = t_lab[..., c] + strength * (matched - t_lab[..., c])
    return np.clip(lab_to_rgb(out), 0.0, 1.0).astype(np.float32)
