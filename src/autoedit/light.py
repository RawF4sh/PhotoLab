"""Where the main light in the frame is (the sun, a bright window), or None (F18).

First choice is the `sun` concept from the segmenter. Without it, the brightest compact blob
of the proxy counts, when it sits in or touches the sky. A whole bright sky, a frame with no
real core, and a sun outside the frame all give None: a glow placed on a guess looks worse than
no glow. Numpy only; every threshold comes from compose.toml [light].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .edit.mask import blobs

_GRID = 128   # the blob search runs on a grid of about this many cells on the long side


@dataclass
class Light:
    cx: float        # centre, fractions of width and height
    cy: float
    radius: float    # radius of the bright core, fraction of the long side
    source: str      # "concept" | "brightness"
    score: float     # concept: 1.0; brightness: mean luma of the core


def _dilate(m: np.ndarray, p: int) -> np.ndarray:
    """Grow a bool mask by p pixels in every direction (box)."""
    if p <= 0:
        return m
    out = m.copy()
    for axis in (0, 1):
        acc = out.copy()
        for k in range(1, p + 1):
            for sl_a, sl_b in ((slice(k, None), slice(None, -k)), (slice(None, -k), slice(k, None))):
                a = [slice(None)] * 2
                b = [slice(None)] * 2
                a[axis], b[axis] = sl_a, sl_b
                acc[tuple(a)] |= out[tuple(b)]
        out = acc
    return out


def _largest_blob(mask: np.ndarray) -> np.ndarray | None:
    """The biggest 8-connected group of True pixels, found on a coarse grid (a flood fill over
    at most _GRID x _GRID cells), returned as a full-size mask."""
    found = blobs(mask, _GRID)
    return found[0] if found else None


def _from_mask(core: np.ndarray, source: str, score: float) -> Light:
    h, w = core.shape
    ys, xs = np.nonzero(core)
    return Light(cx=float((xs.mean() + 0.5) / w), cy=float((ys.mean() + 0.5) / h),
                 radius=float(np.sqrt(core.sum() / np.pi) / max(h, w)), source=source, score=float(score))


def find_light(proxy_rgb: np.ndarray, union: dict[str, np.ndarray], cfg: dict) -> Light | None:
    h, w = proxy_rgb.shape[:2]
    max_cov = float(cfg["max_core_coverage"])

    sun = union.get("sun")
    if sun is not None and sun.any() and float(sun.mean()) < float(cfg.get("max_sun_coverage", max_cov)):
        return _from_mask(sun, "concept", 1.0)
    # Brightness alone is not a reliable sun detector: on a real shoot it marked snow on peaks and
    # bright cloud edges far more often than the sun, which SAM 3 found and the rest it rejected.
    # So it is off unless asked for (it is the only option with the stub segmenter).
    if not cfg.get("brightness_fallback", False):
        return None

    luma = (0.2126 * proxy_rgb[..., 0] + 0.7152 * proxy_rgb[..., 1] + 0.0722 * proxy_rgb[..., 2])
    bright = luma >= max(float(cfg["core_lum"]), float(np.percentile(luma, 99.8)))
    core = _largest_blob(bright)
    if core is None or float(core.mean()) >= max_cov:
        return None
    ys, xs = np.nonzero(core)
    if (xs.max() - xs.min() + 1) / w > float(cfg["max_core_width"]):
        return None
    sky = union.get("sky")
    # Without a sky there is no sun to find by brightness: a bright speck in a frame without sky
    # is a reflection, a lamp or snow (measured: such specks were the only "lights" found in
    # frames without sky). The concept path above still finds a sun SAM 3 sees.
    if sky is None or not sky.any() or not (_dilate(core, int(cfg["sky_touch_px"])) & sky).any():
        return None
    # A sun stands out from the sky around it. A blown overcast sky or a white cloud is as bright
    # around its "core" as in it (measured on a real shoot: 48 of 49 brightness hits were such
    # patches or snow, one was the sun). The ring just outside the core must be clearly darker.
    ring = _dilate(core, int(cfg["ring_px"])) & ~core
    if not ring.any() or float(np.median(luma[ring])) > float(cfg["ring_max_lum"]):
        return None
    return _from_mask(core, "brightness", luma[core].mean())
