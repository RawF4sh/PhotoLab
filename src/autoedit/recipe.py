"""Learned recipe predictor (M6) — the first learned, NON-GENERATIVE editor.

It predicts AutoEdit *settings* (global sliders), never pixels: input is the image's measured
stats, output is a bounded edit recipe. Training labels come from approved (original → target)
pairs (M3): we reuse the reference tuner (M5) to derive the bounded settings that turn each
original into its target, then learn stats → settings so at runtime the recipe is predicted in
one shot — no reference image needed. Multi-output ridge regression, numpy-only (no torch).

Safety: outputs are clamped to OP_BOUNDS; a missing/invalid model means the runtime falls back to
the rules baseline. The model can only move the same bounded sliders the rules engine already uses,
so the oversaturation / banding / skin gates still backstop everything.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

from .edit.ops import OP_BOUNDS

# Inputs: measured global stats (all present on GlobalStatsModel). Outputs: the global recipe ops.
RECIPE_FEATURES = ("mean_lum", "mean_sat", "shadow_clip", "highlight_clip", "haze",
                   "lum_p10", "lum_p50", "lum_p90", "mean_chroma", "warmth")
RECIPE_OPS = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks", "curve",
              "dehaze", "clarity", "vibrance", "saturation", "temp", "tint")


@dataclass
class RecipeModel:
    feature_names: list[str]
    op_names: list[str]
    mean: list[float]
    scale: list[float]
    weights: list[list[float]]   # n_features × n_ops
    bias: list[float]            # n_ops


def features_from_stats(gstats) -> dict[str, float]:
    """The model's input vector, read straight off a GlobalStatsModel (same at train + inference)."""
    return {k: float(getattr(gstats, k, 0.0) or 0.0) for k in RECIPE_FEATURES}


def target_recipe(orig_rgb: np.ndarray, target_rgb: np.ndarray, strength: float = 1.0,
                  passes: int = 6) -> dict[str, float]:
    """The bounded global settings that turn `orig` into `target`'s look (training label). Reuses
    the M5 reference tuner — dimension-independent, so orig/target can differ in size/crop."""
    from .edit.reference import tune_settings_to_reference

    settings, _info = tune_settings_to_reference(orig_rgb, target_rgb, strength=strength, passes=passes)
    return {k: float(v) for k, v in settings.items() if k in OP_BOUNDS}


def train(samples: list[tuple[dict, dict]], ridge: float = 1.0) -> RecipeModel:
    """samples = [(features_dict, settings_dict), …] → multi-output ridge model."""
    if not samples:
        raise ValueError("no recipe training samples")
    x = np.array([[f.get(k, 0.0) for k in RECIPE_FEATURES] for f, _ in samples], dtype=np.float64)
    y = np.array([[s.get(op, 0.0) for op in RECIPE_OPS] for _, s in samples], dtype=np.float64)
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.where(scale < 1e-6, 1.0, scale)
    xn = (x - mean) / scale
    xb = np.concatenate([xn, np.ones((xn.shape[0], 1))], axis=1)
    reg = np.eye(xb.shape[1]) * float(ridge)
    reg[-1, -1] = 0.0
    # lstsq (not solve) so degenerate/near-singular feature sets don't crash — min-norm solution.
    w, *_ = np.linalg.lstsq(xb.T @ xb + reg, xb.T @ y, rcond=None)   # (n_feat+1) × n_ops
    return RecipeModel(feature_names=list(RECIPE_FEATURES), op_names=list(RECIPE_OPS),
                       mean=mean.tolist(), scale=scale.tolist(),
                       weights=w[:-1].tolist(), bias=w[-1].tolist())


def predict(features: dict, model: RecipeModel | dict) -> dict[str, float]:
    """Predict a bounded settings dict from image features. Near-zero ops are dropped (no-op)."""
    m = model if isinstance(model, RecipeModel) else model_from_dict(model)
    x = np.array([features.get(k, 0.0) for k in m.feature_names], dtype=np.float64)
    xn = (x - np.array(m.mean)) / np.maximum(np.array(m.scale), 1e-6)
    y = xn @ np.array(m.weights) + np.array(m.bias)
    out: dict[str, float] = {}
    for op, val in zip(m.op_names, y):
        lo, hi = OP_BOUNDS[op]
        v = float(np.clip(val, lo, hi))
        if abs(v) >= 0.01:
            out[op] = v
    return out


def model_to_dict(model: RecipeModel) -> dict:
    return {"kind": "autoedit-recipe", "feature_names": model.feature_names,
            "op_names": model.op_names, "mean": model.mean, "scale": model.scale,
            "weights": model.weights, "bias": model.bias}


def model_from_dict(data: dict) -> RecipeModel:
    return RecipeModel(feature_names=list(data["feature_names"]), op_names=list(data["op_names"]),
                       mean=list(data["mean"]), scale=list(data["scale"]),
                       weights=[list(r) for r in data["weights"]], bias=list(data["bias"]))


def save_model(model: RecipeModel, path: str, metadata: dict | None = None) -> None:
    d = model_to_dict(model)
    if metadata:
        d["metadata"] = metadata
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(d, fh, indent=2)


def load_model(path: str) -> RecipeModel:
    with open(path) as fh:
        return model_from_dict(json.load(fh))


def train_from_dataset(dataset_dir: str, out_model: str, long_edge: int = 384,
                       ridge: float = 1.0) -> dict:
    """Train from an M3 workspace: pair originals/ with targets/ by stem, derive each pair's recipe
    (features of the original → settings that reach the target), and fit the model."""
    from . import stats as stats_mod
    from .imageio import downscale_long_edge, load_image

    o_dir, t_dir = os.path.join(dataset_dir, "originals"), os.path.join(dataset_dir, "targets")
    if not (os.path.isdir(o_dir) and os.path.isdir(t_dir)):
        raise ValueError(f"not an AutoEdit dataset (need originals/ and targets/): {dataset_dir}")
    targets = {os.path.splitext(f)[0]: os.path.join(t_dir, f) for f in os.listdir(t_dir)}

    samples, skipped = [], 0
    for name in sorted(os.listdir(o_dir)):
        tpath = targets.get(os.path.splitext(name)[0])
        if not tpath:
            skipped += 1
            continue
        try:
            o = downscale_long_edge(load_image(os.path.join(o_dir, name)), long_edge)
            t = downscale_long_edge(load_image(tpath), long_edge)
            g, _ = stats_mod.compute(o, {})
            samples.append((features_from_stats(g), target_recipe(o.rgb, t.rgb)))
        except Exception:
            skipped += 1
    if not samples:
        raise ValueError("no usable original/target pairs in dataset")
    model = train(samples, ridge=ridge)
    save_model(model, out_model, metadata={"dataset": os.path.abspath(dataset_dir),
                                           "samples": len(samples), "skipped": skipped})
    return {"model_path": os.path.abspath(out_model), "samples": len(samples), "skipped": skipped}
