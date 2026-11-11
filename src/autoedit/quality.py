"""Deterministic, source-relative audit shared by experiments and unattended exports.

This is a safety instrument, not an aesthetic score. Full-resolution clipping/finite
checks are chunked; global statistics and banding use a deterministic <=1024px sample.
Encoded texture uses corresponding native patches and rounded RGB8 values, so float
differences or JPEG noise cannot masquerade as retained photographic information.
"""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from dataclasses import dataclass
import tomllib
from numbers import Integral, Real

import numpy as np

from .imageio import Image, luma
from . import stats, tonemetrics


_INTEGER_FIELDS = {"schema_version", "max_metric_edge", "chunk_rows", "texture_min_pairs",
                   "artifact_patch_size", "artifact_grid"}
_NUMERIC_FIELDS = _INTEGER_FIELDS | {
    "shadow_level", "highlight_level", "clip_budget", "shadow_clip_budget", "dark_source",
    "dark_regress", "sat_delta", "chroma_delta", "banding_fail", "texture_high_min",
    "texture_high_max", "texture_max_step_codes", "texture_min_retention",
    "texture_max_new_plateau", "halo_delta", "halo_percentile", "neutral_chroma_max",
    "neutral_luma_min", "neutral_luma_max", "finite_tolerance",
}


def _validate_quality_config(cfg: dict) -> dict:
    """Reject invalid safety configuration before any comparisons can fail open.

    TOML accepts nan/inf, and comparisons against NaN are always false. Keep valid
    values unchanged: this validates the existing thresholds, it does not tune them.
    """
    for key, value in cfg.items():
        if key == "preserve_exposure":
            if not isinstance(value, bool):
                raise ValueError("quality preserve_exposure must be a boolean")
            continue
        if key == "remediation_factors":
            if not isinstance(value, (list, tuple)):
                raise ValueError("quality remediation_factors must be a list of numbers")
            if any(isinstance(v, bool) or not isinstance(v, Real) or not np.isfinite(v)
                   or not 0 <= v <= 1 for v in value):
                raise ValueError("quality remediation_factors must contain finite numbers in [0, 1]")
            continue
        if key not in _NUMERIC_FIELDS:
            raise ValueError(f"unknown quality configuration key: {key}")
        if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
            raise ValueError(f"quality {key} must be a finite number")
        if value < 0:
            raise ValueError(f"quality {key} must be nonnegative")
        if key in _INTEGER_FIELDS and (not isinstance(value, Integral) or value < 1):
            raise ValueError(f"quality {key} must be a positive integer")
    if cfg["schema_version"] != 1:
        raise ValueError("unsupported quality configuration schema_version")
    for low, high in (("shadow_level", "highlight_level"),
                      ("texture_high_min", "texture_high_max"),
                      ("neutral_luma_min", "neutral_luma_max")):
        if not 0 <= cfg[low] < cfg[high] <= 1:
            raise ValueError(f"quality {low}/{high} must be ordered within [0, 1]")
    if cfg["halo_percentile"] > 100:
        raise ValueError("quality halo_percentile must be within [0, 100]")
    return cfg


def load_quality_config(path=None) -> dict:
    default = Path(__file__).with_name("configs") / "quality.toml"
    with default.open("rb") as fh:
        cfg = tomllib.load(fh)
    if path is not None:
        with Path(path).open("rb") as fh:
            cfg.update(tomllib.load(fh))
    return _validate_quality_config(cfg)


def _rgb(value):
    return np.asarray(getattr(value, "rgb", value))


def _sample(source, masks, cfg):
    stride = max(1, int(np.ceil(max(source.shape[:2]) / cfg["max_metric_edge"])))
    steps = tuple(min(stride, max(1, size // 3)) for size in source.shape[:2])
    sample = source[::steps[0], ::steps[1]].astype(np.float32)
    sampled_masks, failures = {}, []
    for name, mask in (masks or {}).items():
        mask = np.asarray(mask)
        if mask.ndim != 2 or not mask.size or not np.isfinite(mask).all():
            failures.append(f"invalid_mask:{name}")
            continue
        yy = np.minimum(np.arange(sample.shape[0]) * steps[0] * mask.shape[0] // source.shape[0], mask.shape[0]-1)
        xx = np.minimum(np.arange(sample.shape[1]) * steps[1] * mask.shape[1] // source.shape[1], mask.shape[1]-1)
        sampled_masks[name] = mask[np.ix_(yy, xx)] > 0.5
    return sample, sampled_masks, failures, steps


@dataclass(frozen=True)
class PreparedQualitySource:
    """Cache of source-only statistics; assess validates sampled pixels/masks/config before reuse."""
    shape: tuple
    thresholds: dict
    sample: np.ndarray
    masks: dict
    stats: object
    regions: dict
    luminance: np.ndarray
    neutral: np.ndarray
    smooth: np.ndarray


def prepare_quality_source(source, masks=None, thresholds=None) -> PreparedQualitySource:
    """Prepare repeat assessments of one source without changing any safety thresholds.

    Only sampled source facts are cached. Full-pixel finite/clipping scans and encoded
    native-patch measurements still run for every candidate. Incompatible reuse raises.
    """
    from .edit.banding import smooth_region_mask
    cfg = _validate_quality_config({**load_quality_config(), **(thresholds or {})})
    source = _rgb(source)
    if source.ndim != 3 or source.shape[-1] != 3 or min(source.shape[:2]) < 3 or not np.isfinite(source).all():
        raise ValueError("prepared source must be a finite HxWx3 array")
    sample, sampled_masks, failures, _ = _sample(source, masks, cfg)
    if failures:
        raise ValueError("; ".join(failures))
    measured, regions = stats.compute(Image(rgb=sample), sampled_masks)
    lum = luma(sample)
    neutral = ((sample.max(axis=-1)-sample.min(axis=-1) < cfg["neutral_chroma_max"])
               & (lum > cfg["neutral_luma_min"]) & (lum < cfg["neutral_luma_max"]))
    return PreparedQualitySource(source.shape, deepcopy(cfg), sample, sampled_masks, measured,
                                 regions, lum, neutral, smooth_region_mask(lum))


def _texture(source, output, cfg, masks=None, sampled_luma=None):
    """Source-correlated encoded differences in bounded corresponding native patches."""
    h, w = source.shape[:2]
    size = int(cfg["artifact_patch_size"])
    ys = np.unique(np.linspace(0, max(0, h-size), int(cfg["artifact_grid"]), dtype=int))
    xs = np.unique(np.linspace(0, max(0, w-size), int(cfg["artifact_grid"]), dtype=int))
    positions = {(int(y), int(x)) for y in ys for x in xs}
    for mask in (masks or {}).values():
        yy, xx = np.nonzero(mask)
        if not yy.size:
            continue
        centers = [(int(np.median(yy)), int(np.median(xx)))]
        if sampled_luma is not None:
            high = mask & (sampled_luma >= cfg["texture_high_min"]) & (sampled_luma <= cfg["texture_high_max"])
            if high.any():
                detail = np.zeros_like(sampled_luma)
                detail[:, 1:] += np.abs(np.diff(sampled_luma, axis=1))
                detail[1:] += np.abs(np.diff(sampled_luma, axis=0))
                centers.append(np.unravel_index(np.argmax(np.where(high, detail, -1)), high.shape))
                centers.append(np.unravel_index(np.argmax(np.where(high, sampled_luma, -1)), high.shape))
        for cy, cx in centers:
            positions.add((int(np.clip(cy*h/mask.shape[0]-size/2, 0, max(0,h-size))),
                           int(np.clip(cx*w/mask.shape[1]-size/2, 0, max(0,w-size)))))
    reports = []
    for y, x in sorted(positions):
        a = luma(source[y:y+size, x:x+size])
        aq = luma(np.rint(source[y:y+size, x:x+size] * 255))
        bq = luma(np.rint(output[y:y+size, x:x+size] * 255))
        high = (a >= cfg["texture_high_min"]) & (a <= cfg["texture_high_max"])
        # A sub-code ramp moves its quantization transitions after even a benign
        # affine edit. Comparing only those adjacent transitions mistakes phase
        # changes for destruction. Choose the shortest dyadic lag that spans a
        # real source code of contrast, independently of either quantized phase.
        # Fine source detail stays at lag one; shallow ramps use larger spans.
        lag = 1
        aa = bb = np.empty(0, np.float32)
        while lag < max(a.shape):
            da, db = [], []
            for axis in (0, 1):
                if a.shape[axis] <= lag:
                    continue
                lower, upper = [slice(None), slice(None)], [slice(None), slice(None)]
                lower[axis], upper[axis] = slice(None, -lag), slice(lag, None)
                lower, upper = tuple(lower), tuple(upper)
                source_delta = (a[upper] - a[lower]) * 255
                delta_a, delta_b = aq[upper] - aq[lower], bq[upper] - bq[lower]
                mask = high[upper] & high[lower]
                mask &= ((np.abs(source_delta) >= 1)
                         & (np.abs(source_delta) <= cfg["texture_max_step_codes"]))
                da.append(delta_a[mask]); db.append(delta_b[mask])
            aa, bb = np.concatenate(da), np.concatenate(db)
            if aa.size >= cfg["texture_min_pairs"]:
                break
            lag *= 2
        if aa.size < cfg["texture_min_pairs"]:
            continue
        retention = float(np.dot(aa, bb) / max(float(np.dot(aa, aa)), 1e-12))
        plateau = float((np.abs(bb) < 1).mean())
        reports.append({"x": int(x), "y": int(y), "pairs": int(aa.size), "lag_pixels": lag,
                        "retention": retention, "new_plateau": plateau})
    return {"available": bool(reports), "patches": reports, "patches_examined": len(positions),
            "sampling": "grid plus mask centers and sampled highlight/detail extrema; not exhaustive",
            "measurement": "shortest dyadic lag with source contrast >= one code; RGB8 endpoint differences",
            "plateau_review": any(r["new_plateau"] > cfg["texture_max_new_plateau"] for r in reports),
            "minimum_retention": min((r["retention"] for r in reports), default=None),
            "maximum_new_plateau": max((r["new_plateau"] for r in reports), default=None)}


def assess_pixels(source, output, masks=None, thresholds=None, prepared=None) -> dict:
    """Return {passed, failures, metrics, scopes}; never modify either RGB input.

    Inputs are matching HxWx3 float sRGB arrays (or Image objects). Masks may be
    native size or proxy size; they are sampled by nearest neighbor. Explicit
    preserve_exposure suppresses only the relative darkness check, never clipping.
    Invalid pixels or shapes fail closed without feeding NaNs into statistics.
    """
    cfg = _validate_quality_config({**load_quality_config(), **(thresholds or {})})
    a, b = _rgb(source), _rgb(output)
    failures = []
    metrics = {"source_shape": list(a.shape), "output_shape": list(b.shape)}
    result = {"passed": False, "failures": failures, "metrics": metrics,
              "scopes": {"clipping": "all pixels, chunked", "global": "deterministic spatial sample",
                         "texture": "native patches, RGB8 luminance differences",
                         "banding": "source-relative sampled smooth regions",
                         "halo": "sampled mask-boundary curvature; unavailable without masks"}}
    if a.shape != b.shape or a.ndim != 3 or a.shape[-1] != 3 or min(a.shape[:2]) < 3:
        failures.append("invalid_shape")
        return result
    total = a.shape[0] * a.shape[1]
    counts = {key: 0 for key in ("source_invalid", "output_invalid", "source_out_of_range", "output_out_of_range")}
    clip = {f"{phase}_{kind}_{end}": 0 for phase in ("source", "output", "new")
            for kind in ("luma", "rgb") for end in ("high", "low")}
    for y in range(0, a.shape[0], int(cfg["chunk_rows"])):
        aa, bb = a[y:y+int(cfg["chunk_rows"])], b[y:y+int(cfg["chunk_rows"])]
        for label, values in (("source", aa), ("output", bb)):
            counts[f"{label}_invalid"] += int((~np.isfinite(values).all(axis=-1)).sum())
            counts[f"{label}_out_of_range"] += int(((values < -cfg["finite_tolerance"]) |
                                                    (values > 1 + cfg["finite_tolerance"])).any(axis=-1).sum())
        for kind in ("luma", "rgb"):
            x, z = (luma(aa), luma(bb)) if kind == "luma" else (aa, bb)
            for end, op, level in (("high", np.greater, cfg["highlight_level"]),
                                   ("low", np.less, cfg["shadow_level"])):
                pre, post = op(x, level), op(z, level)
                # Count per-channel crossings before collapsing to a pixel fraction:
                # an already clipped green channel does not excuse newly clipping red.
                new = post & ~pre
                if kind == "rgb":
                    pre, post, new = pre.any(axis=-1), post.any(axis=-1), new.any(axis=-1)
                for phase, mask in (("source", pre), ("output", post), ("new", new)):
                    clip[f"{phase}_{kind}_{end}"] += int(mask.sum())
    metrics.update(counts)
    metrics["clipping"] = {k: v / total for k, v in clip.items()}
    c = metrics["clipping"]
    for kind in ("luma", "rgb"):
        for end in ("high", "low"):
            c[f"delta_{kind}_{end}"] = c[f"output_{kind}_{end}"] - c[f"source_{kind}_{end}"]
            budget = cfg["clip_budget"] if end == "high" else cfg["shadow_clip_budget"]
            if c[f"new_{kind}_{end}"] > budget:
                failures.append(f"introduced_{kind}_{end}_clipping")
    for label, count in counts.items():
        if count:
            failures.append(label)
    if any(counts.values()):
        return result
    sa, sampled_masks, mask_failures, steps = _sample(a, masks, cfg)
    failures.extend(mask_failures)
    sb = b[::steps[0], ::steps[1]].astype(np.float32)
    if prepared is not None:
        if (prepared.shape != a.shape or prepared.thresholds != cfg or mask_failures
                or not np.array_equal(prepared.sample, sa) or set(prepared.masks) != set(sampled_masks)
                or any(not np.array_equal(prepared.masks[name], mask) for name, mask in sampled_masks.items())):
            raise ValueError("prepared quality source does not match source, masks, or thresholds")
        pre, regions_pre = prepared.stats, prepared.regions
    else:
        pre, regions_pre = stats.compute(Image(rgb=sa), sampled_masks)
    post, regions_post = stats.compute(Image(rgb=sb), sampled_masks)
    metrics.update(source=tonemetrics.from_global(pre), output=tonemetrics.from_global(post),
                   sample_shape=list(sa.shape), regions={name: {"source": rp.model_dump(),
                   "output": regions_post[name].model_dump()} for name, rp in regions_pre.items()})
    for label, measured in (("source", pre), ("output", post)):
        metrics[label].update(mean_chroma=float(measured.mean_chroma), warmth=float(measured.warmth),
                              mean_sat=float(measured.mean_sat), mean_lum=float(measured.mean_lum),
                              detail_rms=float(measured.detail_rms))
    comparison = tonemetrics.compare(metrics["source"], metrics["output"],
                                    dark_source=cfg["dark_source"], dark_regress=cfg["dark_regress"],
                                    clip_budget=cfg["clip_budget"], shadow_clip_budget=cfg["shadow_clip_budget"])
    metrics["comparison"] = comparison
    if comparison["dark_made_darker"] and not cfg.get("preserve_exposure", False):
        failures.append("dark_made_darker")
    metrics["saturation_delta"] = float(post.mean_sat - pre.mean_sat)
    metrics["chroma_delta"] = float(post.mean_chroma - pre.mean_chroma)
    metrics["warmth_delta"] = float(post.warmth - pre.warmth)
    neutral = prepared.neutral if prepared is not None else (
        (sa.max(axis=-1)-sa.min(axis=-1) < cfg["neutral_chroma_max"])
        & (luma(sa) > cfg["neutral_luma_min"]) & (luma(sa) < cfg["neutral_luma_max"]))
    metrics["neutral_wb_delta"] = (np.median((sb - sb.mean(axis=-1, keepdims=True))[neutral]
                                                   - (sa - sa.mean(axis=-1, keepdims=True))[neutral], axis=0).tolist()
                                   if neutral.any() else None)
    if metrics["saturation_delta"] > cfg["sat_delta"] or metrics["chroma_delta"] > cfg["chroma_delta"]:
        failures.append("excessive_color_increase")
    from .edit.banding import banding_score, smooth_region_mask
    al, bl = prepared.luminance if prepared is not None else luma(sa), luma(sb)
    smooth = prepared.smooth if prepared is not None else smooth_region_mask(al)
    metrics["banding_increase"] = banding_score(al, bl, smooth)
    if metrics["banding_increase"] > cfg["banding_fail"]:
        failures.append("severe_new_banding")
    texture = _texture(a, b, cfg, sampled_masks, al)
    metrics["highlight_texture"] = texture
    if texture["available"] and any(r["retention"] < cfg["texture_min_retention"] for r in texture["patches"]):
        failures.append("encoded_highlight_texture_loss")
    boundary = np.zeros(al.shape, bool)
    for mask in sampled_masks.values():
        boundary[1:] |= mask[1:] != mask[:-1]
        boundary[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    residual = bl - al
    curvature = np.zeros(al.shape, np.float32)
    curvature[1:-1, 1:-1] = np.abs(4*residual[1:-1, 1:-1] - residual[:-2, 1:-1]
                                  - residual[2:, 1:-1] - residual[1:-1, :-2] - residual[1:-1, 2:])
    metrics["boundary_curvature_p99"] = float(np.percentile(curvature[boundary], cfg["halo_percentile"])) if boundary.any() else None
    if boundary.any() and metrics["boundary_curvature_p99"] > cfg["halo_delta"]:
        failures.append("severe_region_boundary_artifact")
    result["passed"] = not failures
    return result
