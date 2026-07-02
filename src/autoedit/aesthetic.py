"""Trainable aesthetic scoring from cheap photo features.

This is the first "critic" layer: datasets like AVA/Flickr-AES provide finished-photo
quality scores, not before/after edits. We use those scores to learn a lightweight
ranker that can cull large folders, filter generated/reference edits, and prioritize
human review. The model is intentionally JSON + NumPy so the core stays portable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable

import numpy as np


FEATURE_NAMES = (
    "lum_mean",
    "lum_std",
    "lum_p10",
    "lum_p90",
    "warmth",
    "green",
    "blue_top",
    "top_lum",
    "dark_frac",
    "bright_frac",
    "snow_frac",
    "aspect",
    "edge_mean",
    "edge_p90",
)


@dataclass
class AestheticModel:
    feature_names: list[str]
    mean: list[float]
    scale: list[float]
    weights: list[float]
    bias: float
    score_min: float = 0.0
    score_max: float = 1.0
    kind: str = "ridge_linear_aesthetic_v1"


def vectorize(features: dict[str, float], feature_names: Iterable[str] = FEATURE_NAMES) -> np.ndarray:
    return np.array([float(features.get(name, 0.0)) for name in feature_names], dtype=np.float32)


def normalize_score(value: float) -> float:
    """Map common aesthetic-score ranges to 0..1.

    AVA/Flickr-style scores are often 1..10 or 0..10. User labels may already be 0..1.
    """
    v = float(value)
    if v > 1.0:
        v = v / 10.0
    return float(max(0.0, min(1.0, v)))


def train(samples: list[tuple[dict[str, float], float]], ridge: float = 1e-3) -> AestheticModel:
    if not samples:
        raise ValueError("no aesthetic training samples")
    feature_names = list(FEATURE_NAMES)
    x = np.stack([vectorize(features, feature_names) for features, _score in samples]).astype(np.float32)
    y = np.array([normalize_score(score) for _features, score in samples], dtype=np.float32)
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.where(scale < 1e-6, 1.0, scale)
    xn = (x - mean) / scale
    xb = np.concatenate([xn, np.ones((xn.shape[0], 1), dtype=np.float32)], axis=1)
    reg = np.eye(xb.shape[1], dtype=np.float32) * float(ridge)
    reg[-1, -1] = 0.0
    coef = np.linalg.solve(xb.T @ xb + reg, xb.T @ y)
    return AestheticModel(
        feature_names=feature_names,
        mean=mean.astype(float).tolist(),
        scale=scale.astype(float).tolist(),
        weights=coef[:-1].astype(float).tolist(),
        bias=float(coef[-1]),
        score_min=float(y.min()),
        score_max=float(y.max()),
    )


def predict(features: dict[str, float], model: AestheticModel | dict) -> float:
    if isinstance(model, dict):
        model = model_from_dict(model)
    x = vectorize(features, model.feature_names)
    mean = np.array(model.mean, dtype=np.float32)
    scale = np.array(model.scale, dtype=np.float32)
    weights = np.array(model.weights, dtype=np.float32)
    score = float(((x - mean) / np.maximum(scale, 1e-6)) @ weights + model.bias)
    return max(0.0, min(1.0, score))


def model_to_dict(model: AestheticModel) -> dict:
    return {
        "kind": model.kind,
        "feature_names": model.feature_names,
        "mean": model.mean,
        "scale": model.scale,
        "weights": model.weights,
        "bias": model.bias,
        "score_min": model.score_min,
        "score_max": model.score_max,
    }


def model_from_dict(data: dict) -> AestheticModel:
    return AestheticModel(
        feature_names=list(data["feature_names"]),
        mean=[float(v) for v in data["mean"]],
        scale=[float(v) for v in data["scale"]],
        weights=[float(v) for v in data["weights"]],
        bias=float(data["bias"]),
        score_min=float(data.get("score_min", 0.0)),
        score_max=float(data.get("score_max", 1.0)),
        kind=str(data.get("kind", "ridge_linear_aesthetic_v1")),
    )


def save_model(model: AestheticModel, path: str, metadata: dict | None = None) -> None:
    payload = model_to_dict(model)
    payload["metadata"] = metadata or {}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)


def load_model(path: str) -> AestheticModel:
    with open(path) as fh:
        return model_from_dict(json.load(fh))
