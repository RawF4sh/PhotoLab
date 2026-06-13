"""StubSegmenter — heuristic, GPU-free, network-free segmentation.

Lets the whole pipeline run end-to-end without SAM 3 (dev, tests, CI). It only "knows"
a handful of concepts (sky, subject, skin, foliage, water); anything else comes back
absent — which is exactly the open-vocab behavior the rest of the pipeline must tolerate.
Swap in Sam3Segmenter (same protocol) for real open-vocabulary masks.
"""

from __future__ import annotations

import numpy as np

from ..imageio import Image, luma
from .base import RegionMask

_SCORE = 0.6  # heuristic confidence


def _sky(rgb: np.ndarray) -> np.ndarray:
    h, w, _ = rgb.shape
    yy = np.arange(h)[:, None]
    upper = yy < int(0.55 * h)
    bright = rgb.mean(axis=2) > 0.47
    bluish = rgb[:, :, 2] >= rgb[:, :, 0]
    return upper & bright & bluish


def _subject(rgb: np.ndarray) -> np.ndarray:
    h, w, _ = rgb.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2.0, h / 2.0
    rx, ry = w * 0.30, h * 0.40
    return (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1.0


def _skin(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return (r > 0.25) & (r > g) & (g > b) & ((r - b) > 0.05) & ((r - b) < 0.45) & ((r - g) < 0.25)


def _foliage(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return (g > r) & (g > b) & (g > 0.15)


def _water(rgb: np.ndarray) -> np.ndarray:
    h, _, _ = rgb.shape
    yy = np.arange(h)[:, None]
    lower = yy > int(0.55 * h)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return lower & (b >= g) & (g >= r) & (luma(rgb) < 0.6)


_HEURISTICS = {
    "sky": _sky,
    "subject": _subject,
    "skin": _skin,
    "foliage": _foliage,
    "water": _water,
    # crude aliases so presets don't all come back empty in stub mode
    "person": _subject,
    "dress": _subject,
    "suit": _subject,
    "flowers": _foliage,
}


class StubSegmenter:
    name = "stub"

    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        out: list[RegionMask] = []
        for c in concepts:
            fn = _HEURISTICS.get(c)
            if fn is None:
                continue  # unknown concept -> absent (open-vocab tolerance)
            mask = fn(img.rgb)
            if not mask.any():
                continue
            out.append(
                RegionMask(name=c, mask=mask, score=_SCORE, coverage=float(mask.mean()), instance_count=1)
            )
        return out
