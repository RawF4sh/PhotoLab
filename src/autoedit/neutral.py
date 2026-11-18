"""Neutral pick (F34): a spot that is grey or white, and the white balance that makes it neutral.

`measure` reads one spot of the analysis picture and solves, in the units of `ops.light`, the temp and
tint that turn it grey. A spot the op cannot make neutral is not used (a pick is met exactly or not
at all). `for_run` measures once for a whole run (`--neutral-from`) and returns plain numbers.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import numpy as np

from . import rules as rules_mod
from .edit.ops import OP_BOUNDS
from .imageio import downscale_long_edge, linear_to_srgb, load_image, raw_opts_from, srgb_to_linear
from .stats import wb_correction

DEFAULTS = {"patch": 0.02, "min_level": 0.08, "max_level": 0.98}
RANGES = {"patch": (0.002, 0.25), "min_level": (0.01, 1.0), "max_level": (0.0, 1.0)}


@dataclass(frozen=True)
class Pick:
    temp: float
    tint: float
    xy: tuple[float, float] | None = None
    source: tuple[float, float, float] | None = None    # the spot's channels before the pick, of 255


def parse(text: str) -> list[float]:
    """"0.42,0.61" -> [0.42, 0.61]: X from the left, Y from the top, as shares of the frame."""
    try:
        v = [float(p) for p in str(text).split(",")]
        if len(v) == 2 and all(0.0 <= n <= 1.0 for n in v):     # NaN fails the comparison
            return v
    except ValueError:
        pass
    raise ValueError(f"--neutral needs X,Y as shares of the frame, each from 0 to 1 (for example 0.42,0.61), got {text!r}")


def box(shape, xy, patch: float) -> tuple[int, int, int, int]:
    """(y0, y1, x0, x1) of the square round the spot, cut at the frame."""
    h, w = shape
    r = max(1, round(patch * max(h, w) / 2.0))
    cx, cy = min(w - 1, int(xy[0] * w)), min(h - 1, int(xy[1] * h))
    return max(cy - r, 0), min(cy + r + 1, h), max(cx - r, 0), min(cx + r + 1, w)


def spot_mean(rgb: np.ndarray, xy, patch: float) -> np.ndarray:
    """The box's mean per channel, as a gamma value (the mean is taken in linear light)."""
    y0, y1, x0, x1 = box(rgb.shape[:2], xy, patch)
    lin = srgb_to_linear(rgb[y0:y1, x0:x1].reshape(-1, 3)).astype(np.float64).mean(axis=0)
    return linear_to_srgb(lin)


def measure(rgb: np.ndarray, xy, t: dict = DEFAULTS) -> tuple[Pick | None, str]:
    """(pick, "") for the spot `xy` of the gamma picture `rgb`, or (None, why it is not used). `t`: `rules.block`."""
    y0, y1, x0, x1 = box(rgb.shape[:2], xy, t["patch"])
    spot = rgb[y0:y1, x0:x1].reshape(-1, 3)
    if float(np.percentile(spot.max(axis=1), 99)) >= t["max_level"]:
        return None, "the spot is blown out: pick something grey or white that still has tone"
    if float(spot.mean(axis=0).min()) < t["min_level"]:
        return None, "the spot is too dark to measure: pick something lighter"
    r, g, b = (float(v) for v in srgb_to_linear(spot).astype(np.float64).mean(axis=0))
    temp, tint = wb_correction(r, g, b, limit=math.inf)
    if abs(temp) > OP_BOUNDS["temp"][1] or abs(tint) > OP_BOUNDS["tint"][1]:
        return None, (f"the spot needs temp {temp:+.0f} and tint {tint:+.0f}, the white balance reaches "
                      f"{OP_BOUNDS['temp'][1]:g}: it is not grey, or the colour cast is too strong")
    src = tuple(round(float(v) * 255.0, 1) for v in spot_mean(rgb, xy, t["patch"]))
    return Pick(temp, tint, (float(xy[0]), float(xy[1])), src), ""


def for_run(cfg, path: str):
    """For `--neutral-from FILE`: measure the spot `cfg.neutral` on that photo once. Returns (cfg with the
    two numbers in `wb` and no `neutral`, one line to print). A ValueError names the file and says why."""
    name = os.path.basename(path)
    if cfg.neutral is None:
        raise ValueError(f"--neutral-from {name}: it needs --neutral X,Y, the spot of that photo that is grey or white")
    if not os.path.isfile(path):
        raise ValueError(f"--neutral-from {name}: no such file")
    t = rules_mod.block(rules_mod.load(cfg.rules_path), "neutral", DEFAULTS, RANGES)
    try:
        img = load_image(path, decode=raw_opts_from(cfg))
    except Exception as e:      # an unreadable file is a message, not a traceback
        raise ValueError(f"--neutral-from {name}: {e}") from None
    pick, why = measure(downscale_long_edge(img, cfg.analysis_long_edge).rgb, cfg.neutral, t)
    if pick is None:
        raise ValueError(f"--neutral-from {name}: neutral pick at {cfg.neutral[0]:.3f}, {cfg.neutral[1]:.3f}: not used, {why}")
    line = (f"neutral pick in {name} at {cfg.neutral[0]:.3f}, {cfg.neutral[1]:.3f}: white balance "
            f"temp {pick.temp:+.1f}, tint {pick.tint:+.1f} for every photo of this run")
    return cfg.model_copy(update={"neutral": None, "wb": [round(pick.temp, 3), round(pick.tint, 3)]}), line
