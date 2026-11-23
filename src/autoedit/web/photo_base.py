"""Validated, local-only adjustments imported from a selectively synced export."""

import math

from ..contract import Adjustment
from ..edit.ops import OP_BOUNDS
from ..segment.base import MARK


def _number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a finite number from {lo:g} to {hi:g}")
    return float(value)


def check(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"adjustments", "exposure_bias", "wb", "wb_bias", "selected_regions"}:
        raise ValueError("photo_base must contain adjustments, exposure_bias, wb, wb_bias and/or selected_regions")
    given = value.get("adjustments", [])
    if not isinstance(given, list) or len(given) > 200:
        raise ValueError("photo_base.adjustments must contain at most 200 adjustments")
    out = {"adjustments": []}
    if "selected_regions" in value:
        from ..segment.refine import names
        out["selected_regions"] = names(value["selected_regions"])
    for item in given:
        if not isinstance(item, dict) or set(item) - set(Adjustment.model_fields):
            raise ValueError("photo_base adjustment has unsupported fields")
        target, source = item.get("target"), item.get("source", "")
        if (not isinstance(target, str) or not 1 <= len(target) <= 200 or target != target.strip()
                or any(ord(c) < 32 for c in target) or target.startswith(MARK)):
            raise ValueError("photo_base target must be a short concept, place or global, without a mark")
        if not isinstance(source, str) or len(source) > 2000:
            raise ValueError("photo_base adjustment source must be text of at most 2000 characters")
        if item.get("scope") != "photo":
            raise ValueError("photo_base accepts only photo-scope adjustments")
        ops = item.get("ops")
        if not isinstance(ops, dict) or set(ops) - set(OP_BOUNDS):
            raise ValueError("photo_base adjustment contains unsupported operations")
        checked = {key: _number(v, "photo_base." + key, *OP_BOUNDS[key]) for key, v in ops.items()}
        adj = Adjustment.model_validate({**item, "ops": checked, "status": ""})
        out["adjustments"].append(adj.model_dump())
    if "exposure_bias" in value:
        out["exposure_bias"] = _number(value["exposure_bias"], "photo_base.exposure_bias", -3, 3)
    for key in ("wb", "wb_bias"):
        if key not in value:
            continue
        wb = value[key]
        if key == "wb" and wb is None:
            out[key] = None
            continue
        if not isinstance(wb, list) or len(wb) != 2:
            raise ValueError(f"photo_base.{key} must be [temp, tint]" + (" or null" if key == "wb" else ""))
        out[key] = [_number(v, "photo_base." + key, -100, 100) for v in wb]
    return out


def adjustments(base):
    return [Adjustment.model_validate(a) for a in base.get("adjustments", [])]


def options(opts):
    base = check(opts.get("photo_base"))
    # Sync's measured white balance replaces the recipient's old neutral pick. A new neutral
    # pick clears base.wb in the client; later manual temp/tint adjustments still add normally.
    return {**opts, "wb": base["wb"], "neutral": None} if base.get("wb") is not None else opts
