"""Fit ordinary settings to a range of measured photographic features on the analysis proxy."""

from __future__ import annotations

import math
import tomllib

import numpy as np

from ..imageio import luma
from .ops import OP_BOUNDS, apply_ops
from .reference import _format_moves, reference_features

# Each control moves its associated feature upwards as the control increases.
CONTROLS = {"p50": "exposure", "p10": "shadows_ev", "p90": "highlights", "lstd": "contrast",
            "sat": "saturation", "chroma": "vibrance", "warmth": "temp", "tint": "tint",
            "detail": "clarity", "green_sat": "hsl_green_sat", "green_lum": "hsl_green_lum",
            "blue_sat": "hsl_blue_sat"}


def load(path, name):
    with open(path, "rb") as f:
        targets = tomllib.load(f)
    if name not in targets:
        raise ValueError(f"no local style target {name!r} in targets.toml")
    result = {}
    for region, body in targets[name].items():
        if region == "meta":
            continue
        if not isinstance(body, dict):
            raise ValueError(f"{name}.{region} must be a table")
        for feature, bounds in body.items():
            if feature == "hold" and type(bounds) is bool:
                continue
            if (feature not in CONTROLS or not isinstance(bounds, list) or len(bounds) != 2
                    or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds)
                    or bounds[0] > bounds[1]):
                raise ValueError(f"invalid style target {name}.{region}.{feature}")
        result[region] = body
    return result


def check_limits(style_name, target, limits):
    """Refuse a target that moves a control the style gives no limit for."""
    for table in target.values():
        for measure in table:
            if measure != "hold" and CONTROLS[measure] not in limits:
                raise ValueError(f"{style_name}: the target moves {CONTROLS[measure]} (through {measure}) "
                                 "and the style sets no limit for it")


def _aimed(value, lo, hi, aim):
    """Where a measure is pulled: inside its range it stays, outside it aims `aim` of the width in from the edge."""
    if value < lo:
        return lo + aim * (hi - lo)
    if value > hi:
        return hi - aim * (hi - lo)
    return value


def clipping_ok(source, candidate, cfg, mask=None):
    """Use the native quality gate's newly clipped RGB and luma pixel budgets on this proxy."""
    if mask is not None:
        source, candidate = source[mask], candidate[mask]
    for a, b in ((source, candidate), (luma(source), luma(candidate))):
        for compare, level, budget in ((np.greater, cfg["highlight_level"], cfg["clip_budget"]),
                                       (np.less, cfg["shadow_level"], cfg["shadow_clip_budget"])):
            new = compare(b, level) & ~compare(a, level)
            if a.ndim == source.ndim and new.shape[-1] == 3:
                new = new.any(axis=-1)
            if np.mean(new) > budget:
                return False
    return True


def _loss(features, goals):
    return sum(abs(features[k] - value) for k, value in goals.items())


def held_settings(settings, frame_ev, base_ev, ceiling=None):
    """Take back an additional frame lift, leaving a lamp's original highlight rolloff in place."""
    before = settings.get("exposure", 0.0)
    held = before - max(0.0, frame_ev - base_ev)
    if ceiling is not None:
        held = min(held, ceiling - frame_ev)
    if held == before:
        return settings
    return {**settings, "exposure": max(OP_BOUNDS["exposure"][0], held)}


def over_budget(regions, frame_ev, base_ev, cap, held=()):
    """One lift budget for the frame and a region (F59: 2 EV on a JPEG or an 8-bit preview).

    `fit` keeps its own search inside the budget. This finds what it cannot see: a lift the rules
    gave a region before the style raised the frame from `base_ev` to `frame_ev`. Such a region
    may keep `cap - frame_ev`, and never loses more than the frame gained, so the rules' own
    result is the floor. `held` regions are left to `held_settings`.
    Returns {region: (exposure now, exposure allowed)} for the regions to reduce."""
    lift = frame_ev - base_ev
    if cap is None or lift <= 0:
        return {}
    found = {}
    for name, settings in regions.items():
        ev = settings.get("exposure", 0.0)
        allowed = max(cap - frame_ev, ev - lift)
        if name not in held and ev > allowed:
            found[name] = (ev, allowed)
    return found


def fit(source, base, ranges, strength, limits, safety, mask=None, global_after=None, held_regions=(),
        caps=None, forbidden=(), aim=0.0):
    """Search the existing edit controls toward the nearest edges. A successful step must
    improve the requested measurements without crossing its target or the clipping budget.

    `caps` ({op: largest magnitude}) and `forbidden` (op names) are the region's own rails from
    concepts.toml. The search stays inside them, so what it measures and reports is what the
    region policy lets through afterwards.
    """
    if not ranges or ranges.get("hold") or strength <= 0:
        return base, []
    # Use a deterministic subsample for repeated evaluations. Check final clipping and
    # direction on the entire proxy before accepting, including single-pixel lights.
    stride = max(1, math.ceil(max(source.shape[:2]) / 160))
    small = source[::stride, ::stride]
    sample_mask = mask[::stride, ::stride] if mask is not None else None
    if sample_mask is not None and sample_mask.sum() < max(16, sample_mask.size * .002):
        return base, []

    def render(rgb, settings):
        local = rgb
        for full_alpha, region_base in held_regions:
            alpha = (full_alpha if rgb is source else full_alpha[::stride, ::stride])[..., None]
            held = apply_ops(rgb, held_settings(region_base, settings.get("exposure", 0.0),
                                               base.get("exposure", 0.0), limits.get("lights_ev")))
            local = local + alpha * (held - rgb)
        edited = apply_ops(local, settings)
        return apply_ops(edited, global_after) if global_after else edited

    best = dict(base)
    first = reference_features(render(small, best), sample_mask)
    goals = {k: first[k] + strength * (_aimed(first[k], lo, hi, aim) - first[k])
             for k, interval in ranges.items() if k in CONTROLS for lo, hi in [interval]}
    if not goals or _loss(first, goals) < 1e-6:
        return base, []
    features = first
    for key, goal in goals.items():
        control = CONTROLS[key]
        start = features[key]
        if abs(goal - start) < 1e-5:
            continue
        if control in forbidden:
            continue
        lower, upper = OP_BOUNDS[control]
        if caps and control in caps:
            lower, upper = max(lower, -caps[control]), min(upper, caps[control])
        if control in limits:
            frame_amount = (global_after or {}).get(control, 0.0)
            lower = max(lower, -limits[control] - frame_amount)
            upper = min(upper, limits[control] - frame_amount)
        initial = best.get(control, 0.0)
        bound = upper if goal > start else lower
        if (bound - initial) * (goal - start) <= 0:
            continue
        near, far = 0.0, 1.0
        before = dict(best)
        error = _loss(features, goals)
        for _ in range(8):
            fraction = (near + far) / 2
            trial = {**before, control: initial + (bound - initial) * fraction}
            rgb = render(small, trial)
            measured = reference_features(rgb, sample_mask)
            crossed = (measured[key] - goal) * (goal - start) > 0
            if crossed or not clipping_ok(small, rgb, safety, sample_mask):
                far = fraction
            else:
                near = fraction
                loss = _loss(measured, goals)
                if loss < error:
                    best, features, error = trial, measured, loss

    if best == base:
        return base, []
    # Sampling can miss lamps and dark edges. Scale the full plan back if necessary.
    original = reference_features(render(source, base), mask)
    full_goals = {k: original[k] + strength * (_aimed(original[k], *ranges[k], aim) - original[k]) for k in goals}
    for factor in (1, .5, .25, .125):
        candidate = {k: base.get(k, 0) + factor * (value - base.get(k, 0)) for k, value in best.items()}
        rgb = render(source, candidate)
        if not clipping_ok(source, rgb, safety, mask):
            continue
        measured = reference_features(rgb, mask)
        if any((measured[k] - goal) * np.sign(goal - original[k]) > 1e-5
               for k, goal in full_goals.items() if abs(goal - original[k]) > 1e-5):
            continue
        if any(ranges[k][0] <= original[k] <= ranges[k][1]
               and not ranges[k][0] <= measured[k] <= ranges[k][1] for k in full_goals):
            continue
        if _loss(measured, full_goals) >= _loss(original, full_goals):
            continue
        moves = _format_moves(base, candidate)
        if "shadows_ev" in candidate and candidate["shadows_ev"] != base.get("shadows_ev", 0):
            moves.append(f"shadow lift {candidate['shadows_ev'] - base.get('shadows_ev', 0):+.2f} EV")
        explanation = [f"{k} {original[k]:.3f} → {measured[k]:.3f} (range {ranges[k][0]:g}..{ranges[k][1]:g})"
                       for k in goals if abs(original[k] - measured[k]) > .001]
        return candidate, [*moves, *explanation] or ["adjusted toward measured range"]
    return base, []
