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
from .edit import build_alpha, glow_alpha, linear_alpha, radial_alpha
from .light import Light


@dataclass
class Move:
    name: str
    when: str
    shape: str  # "radial" | "linear" | "glow" | "band"
    ops: Settings = field(default_factory=dict)
    target: str = "inside"  # radial: inside|outside
    feather: float = 0.18
    radius_scale: float = 1.4
    frm: str = "top"  # linear: top|bottom|left|right
    coverage: float = 0.5
    min_coverage: float = 0.0  # the `when` concept must cover at least this frame fraction
    at: str = ""  # "light": placed on the light finder's result, fires only when one is found
    requires: str = ""  # RunConfig bool that must be true for the move to exist at all
    within: tuple[str, ...] = ()  # fires inside the joined masks of these regions (any one present)
    height: float = 0.12  # band width as a share of image height
    min_radius: float = 0.0  # glow: floor of the radius (fraction of the long side)


# Sections of compose.toml that are settings for the light finder, not moves.
_NOT_MOVES = ("light",)


def load_light_config(path: str) -> dict:
    """The [light] thresholds of the light finder (light.find_light)."""
    with open(path, "rb") as fh:
        return dict(tomllib.load(fh).get("light", {}))


def load_compose(path: str, run=None, overrides=None) -> list[Move]:
    """The moves of compose.toml. A move with `requires` is left out unless that setting is true
    on `run` (a RunConfig); without a `run` every such move is left out."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    data.update(overrides or {})
    moves: list[Move] = []
    for name, body in data.items():
        if name in _NOT_MOVES:
            continue
        shape = str(body.get("shape", "radial"))
        if shape not in ("radial", "linear", "glow", "band"):
            # fail loudly: a typo'd shape would otherwise silently fall into the linear branch
            raise ValueError(f"compose move {name!r}: unknown shape {shape!r} (radial | linear | glow | band)")
        at = str(body.get("at", ""))
        if at not in {"glow": ("light",), "band": ("horizon", "waterline")}.get(shape, ("",)):
            raise ValueError(f"compose move {name!r}: at = {at!r} is invalid for shape {shape!r} (glow requires light, band requires horizon or waterline)")
        within = body.get("within", ())
        if isinstance(within, str):     # one name; an empty one means no region, as it always did
            within = (within,) if within else ()
        if not isinstance(within, (tuple, list)) or not all(isinstance(n, str) and n for n in within):
            raise ValueError(f"compose move {name!r}: within must be a region name or a list of region names")
        if shape == "band" and not (0 < body.get("height", .12) <= 1 and 0 < body.get("feather", .18) <= 1):
            raise ValueError(f"compose move {name!r}: height and feather must be in (0, 1]")
        requires = str(body.get("requires", ""))
        if requires:
            if run is not None and not hasattr(run, requires):
                raise ValueError(f"compose move {name!r}: requires {requires!r} is not a run setting")
            if not (run is not None and getattr(run, requires, False) is True):
                continue
        frm = str(body.get("from", "top"))
        if frm not in ("top", "bottom", "left", "right"):
            raise ValueError(f"compose move {name!r}: unknown from {frm!r} (top|bottom|left|right)")
        moves.append(
            Move(
                name=name,
                when=str(body.get("when", "")),
                shape=shape,
                ops={k: float(v) for k, v in body.get("ops", {}).items()},
                target=str(body.get("target", "inside")),
                feather=float(body.get("feather", 0.18)),
                radius_scale=float(body.get("radius_scale", 1.4)),
                frm=frm,
                coverage=float(body.get("coverage", 0.5)),
                min_coverage=float(body.get("min_coverage", 0.0)),
                at=at, within=tuple(within), height=float(body.get("height", .12)),
                requires=requires,
                min_radius=float(body.get("min_radius", 0.0)),
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


def _joined(m: Move, union: dict[str, np.ndarray], region_masks=None) -> np.ndarray | None:
    """The or of the named regions that are present, or None when none has a pixel."""
    src = union if region_masks is None else region_masks
    found = [src[n] for n in m.within if n in src and src[n].any()]
    return np.logical_or.reduce(found) if found else None


def _triggers(m: Move, union: dict[str, np.ndarray], light: Light | None = None, horizon=None, region_masks=None,
              waterline=None) -> bool:
    """A move fires when its `when` concept is present (covering ≥ `min_coverage` of the frame),
    or unconditionally for `when="always"`. A 3% sliver of sky must not earn a half-frame grad.
    A move placed `at = "light"` also needs a light to have been found."""
    if m.within and _joined(m, union, region_masks) is None:
        return False
    if m.at == "horizon" and horizon is None:
        return False
    if m.at == "waterline" and waterline is None:
        return False
    if m.at == "light" and light is None:
        return False
    if m.when == "always":
        return True
    mask = union.get(m.when)
    return mask is not None and (m.min_coverage <= 0.0 or float(mask.mean()) >= m.min_coverage)


def settings_for(moves: list[Move], union: dict[str, np.ndarray], intensity: float,
                 light: Light | None = None, horizon=None, region_masks=None, waterline=None) -> dict[str, Settings]:
    """Just the (name -> settings) the moves would apply — cheap, no alpha (for sidecars/dry-run)."""
    return {
        m.name: {k: v * float(intensity) for k, v in m.ops.items()}
        for m in moves
        if _triggers(m, union, light, horizon, region_masks, waterline)
    }


def plan(
    moves: list[Move],
    union: dict[str, np.ndarray],
    full_hw: tuple[int, int],
    intensity: float,
    light: Light | None = None,
    horizon=None, feather_px=None, guide=None, region_masks=None, waterline=None,
) -> list[tuple[str, np.ndarray, Settings]]:
    out: list[tuple[str, np.ndarray, Settings]] = []
    for m in moves:
        if not _triggers(m, union, light, horizon, region_masks, waterline):
            continue
        if m.shape == "band":
            # The anchor is the fraction of image height at each side of the trusted line.
            h, w = full_hw
            line = np.linspace(*(waterline if m.at == "waterline" else horizon), w, dtype=np.float32)
            distance = np.abs((np.arange(h, dtype=np.float32)[:, None] + .5) / h - line)
            radius = m.height / 2
            u = np.clip((radius - distance) / (radius * m.feather), 0, 1)
            alpha = u * u * (3 - 2 * u)
        elif m.shape == "glow":
            alpha = glow_alpha(full_hw, light.cx, light.cy, max(m.min_radius, m.radius_scale * light.radius))
        elif m.shape == "radial":
            # frame-centered for an unconditional move; otherwise derive from the region
            cx, cy, hx, hy = (0.5, 0.5, 0.5, 0.5) if m.when not in union else _bbox_norm(union[m.when])
            alpha = radial_alpha(
                full_hw, cx, cy, hx * m.radius_scale, hy * m.radius_scale,
                feather=m.feather, inside=(m.target == "inside"),
            )
        else:
            alpha = linear_alpha(full_hw, frm=m.frm, coverage=m.coverage)
        if m.within:
            # joined first, feathered once: feathering each would leave half weight where two touch
            src = union if region_masks is None else region_masks
            wide = max((feather_px or {}).get(n, 0) for n in m.within if n in src and src[n].any())
            alpha *= build_alpha(_joined(m, union, region_masks), full_hw, wide, guide=guide)
        settings = {k: v * float(intensity) for k, v in m.ops.items()}
        out.append((m.name, alpha, settings))
    return out
