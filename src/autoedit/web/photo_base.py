"""Validated, local-only adjustments imported from a selectively synced export."""

import math

from ..contract import Adjustment
from ..edit.ops import OP_BOUNDS
from ..segment.base import MARK
from ..scene import LANDSCAPES


def _number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a finite number from {lo:g} to {hi:g}")
    return float(value)


def check_shoot(shoot):
    """Only numerical fit controls and the settings needed to replay a finished Auto shoot."""
    fields = {"scene", "median", "batch_group", "intensity", "scene_override", "exposure_bias", "wb_bias"}
    scenes = {"landscape", *LANDSCAPES}
    if not isinstance(shoot, dict) or shoot.keys() - fields or not isinstance(shoot.get("scene"), str) or shoot["scene"] not in scenes:
        raise ValueError("Invalid Auto shoot scene or fields")
    median = shoot.get("median", {})
    if not isinstance(median, dict) or len(median) > 150:
        raise ValueError("Invalid Auto shoot median")
    out = {"scene": shoot["scene"], "median": {}}
    for name, controls in median.items():
        if not isinstance(name, str) or not name or len(name) > 200 or any(ord(ch) < 32 for ch in name):
            raise ValueError("Invalid Auto shoot region")
        if not isinstance(controls, dict) or controls.keys() - OP_BOUNDS.keys():
            raise ValueError("Invalid Auto shoot control")
        out["median"][name] = {k: _number(v, k, *OP_BOUNDS[k]) for k, v in controls.items()}
    if "intensity" in shoot:
        out["intensity"] = _number(shoot["intensity"], "Auto shoot strength", 0, 1.5)
    if "scene_override" in shoot:
        if shoot["scene_override"] is not None and (not isinstance(shoot["scene_override"], str) or shoot["scene_override"] not in scenes):
            raise ValueError("Invalid Auto shoot override")
        out["scene_override"] = shoot["scene_override"]
    if "batch_group" in shoot:
        if not isinstance(shoot["batch_group"], str) or len(shoot["batch_group"]) > 300 or any(ord(ch) < 32 for ch in shoot["batch_group"]):
            raise ValueError("Invalid Auto shoot group")
        out["batch_group"] = shoot["batch_group"]
    if "exposure_bias" in shoot:
        out["exposure_bias"] = _number(shoot["exposure_bias"], "Auto shoot exposure", -3, 3)
    if "wb_bias" in shoot:
        values = shoot["wb_bias"]
        if not isinstance(values, (list,tuple)) or len(values) != 2:
            raise ValueError("Invalid Auto shoot white balance")
        out["wb_bias"] = [_number(v, "Auto shoot white balance", -100, 100) for v in values]
    return out


def shoot_receipt(result):
    info = result.audit.get("auto", {})
    shoot = info.get("shoot")
    if not shoot or info.get("scene") not in {"landscape", *LANDSCAPES}:
        return None
    cfg = result.audit["run_config"]
    return check_shoot({**shoot, "batch_group": result.batch_group, "intensity": cfg["intensity"],
                        "scene_override": cfg.get("scene"), "exposure_bias": result.exposure_bias,
                        "wb_bias": list(result.wb_bias)})


def render_args(base, cfg, bias=None):
    """A saved matching shoot can be replayed even when its other photos are absent."""
    shoot = base.get("auto_shoot") if cfg.style == "auto" else None
    if shoot and (shoot.get("intensity", cfg.intensity) != cfg.intensity
                  or ("scene_override" in shoot and shoot["scene_override"] != cfg.scene)
                  or (cfg.scene and cfg.scene != shoot["scene"])):
        shoot = None
    newly_planned = getattr(bias, "auto_shoot", None) if cfg.style == "auto" else None
    match = shoot or newly_planned
    values = shoot or {}
    result = {"exposure_bias": base.get("exposure_bias", values.get("exposure_bias", bias.exposure if bias else 0)),
              "wb_bias": tuple(base.get("wb_bias", values.get("wb_bias", (bias.temp, bias.tint) if bias else (0, 0))))}
    if match:
        result["auto_shoot"] = {"scene": match["scene"], "median": match["median"]}
    if bias or values.get("batch_group"):
        result["batch_group"] = values.get("batch_group", bias.group if bias else "")
    return result


def check(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"adjustments", "exposure_bias", "wb", "wb_bias", "selected_regions", "auto_shoot"}:
        raise ValueError("photo_base must contain adjustments, exposure_bias, wb, wb_bias, selected_regions and/or auto_shoot")
    given = value.get("adjustments", [])
    if not isinstance(given, list) or len(given) > 200:
        raise ValueError("photo_base.adjustments must contain at most 200 adjustments")
    out = {"adjustments": []}
    if "auto_shoot" in value:
        out["auto_shoot"] = check_shoot(value["auto_shoot"])
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
