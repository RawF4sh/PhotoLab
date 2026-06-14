"""Compositional moves — place radial/linear gradient masks by reasoning about the scene.

This is the "intentional composition" layer: a vignette that pulls the eye to the subject,
a graduated darken on the sky. Geometry is derived from the understood regions (heuristic
placement today; the LLM decider can override center/coverage/ops later). Output is a list
of (name, full-res alpha, settings) that the apply engine blends independently of the
segmentation regions.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

import numpy as np

from .contract import Settings
from .edit import linear_alpha, radial_alpha


@dataclass
class Move:
    name: str
    when: str
    shape: str  # "radial" | "linear"
    ops: Settings = field(default_factory=dict)
    target: str = "inside"  # radial: inside|outside
    feather: float = 0.18
    radius_scale: float = 1.4
    frm: str = "top"  # linear: top|bottom|left|right
    coverage: float = 0.5


def load_compose(path: str) -> list[Move]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    moves: list[Move] = []
    for name, body in data.items():
        moves.append(
            Move(
                name=name,
                when=str(body.get("when", "")),
                shape=str(body.get("shape", "radial")),
                ops={k: float(v) for k, v in body.get("ops", {}).items()},
                target=str(body.get("target", "inside")),
                feather=float(body.get("feather", 0.18)),
                radius_scale=float(body.get("radius_scale", 1.4)),
                frm=str(body.get("from", "top")),
                coverage=float(body.get("coverage", 0.5)),
            )
        )
    return moves


def _bbox_norm(mask: np.ndarray) -> tuple[float, float, float, float]:
    """Return (cx, cy, half_w, half_h) in 0..1 from a bool mask, centroid-centered."""
    ys, xs = np.where(mask)
    if xs.size == 0:
        return 0.5, 0.5, 0.5, 0.5
    h, w = mask.shape
    cx, cy = float(xs.mean()) / w, float(ys.mean()) / h
    hx = max((xs.max() - xs.min()) / 2.0 / w, 0.05)
    hy = max((ys.max() - ys.min()) / 2.0 / h, 0.05)
    return cx, cy, hx, hy


def settings_for(moves: list[Move], union: dict[str, np.ndarray], intensity: float) -> dict[str, Settings]:
    """Just the (name -> settings) the moves would apply — cheap, no alpha (for sidecars/dry-run)."""
    return {
        m.name: {k: v * float(intensity) for k, v in m.ops.items()}
        for m in moves
        if m.when in union
    }


def plan(
    moves: list[Move],
    union: dict[str, np.ndarray],
    full_hw: tuple[int, int],
    intensity: float,
) -> list[tuple[str, np.ndarray, Settings]]:
    out: list[tuple[str, np.ndarray, Settings]] = []
    for m in moves:
        if m.when not in union:
            continue
        if m.shape == "radial":
            cx, cy, hx, hy = _bbox_norm(union[m.when])
            alpha = radial_alpha(
                full_hw, cx, cy, hx * m.radius_scale, hy * m.radius_scale,
                feather=m.feather, inside=(m.target == "inside"),
            )
        else:
            alpha = linear_alpha(full_hw, frm=m.frm, coverage=m.coverage)
        settings = {k: v * float(intensity) for k, v in m.ops.items()}
        out.append((m.name, alpha, settings))
    return out
