"""Clean backdrop (F34): a light background is evened out and taken to a set white.

No model is asked. The backdrop is what is bright, near neutral and reaches the frame edge through such
pixels, found on the analysis-size copy of the finished picture. A smooth surface (a polynomial per
channel) is fitted to it and the picture is multiplied by `white / surface` where the backdrop is, with a
soft edge: outside that edge not one pixel changes. Nothing is cut out and nothing is filled in.
"""

from __future__ import annotations

import math

import numpy as np

from .edit import build_alpha, par
from .imageio import Image, downscale_long_edge, luma

DEFAULTS = {"white": 0.96, "min_level": 0.55, "max_chroma": 0.12, "min_coverage": 0.10, "degree": 4,
            "max_gain": 1.6, "feather": 0.004, "keep": ["subject", "person", "skin"]}
RANGES = {"white": (0.5, 1.0), "min_level": (0.0, 1.0), "max_chroma": (0.0, 1.0), "min_coverage": (0.0, 1.0),
          "degree": (1, 6), "max_gain": (1.0, 4.0), "feather": (0.0, 0.05)}
_GRID = 256                   # the backdrop is traced from the frame edge on a grid of at most this many cells a side
_CLIP, _CLIP_FLOOR = 2.5, 2.0 / 255.0   # the fit leaves out what lies further than 2.5 robust deviations, never under 2 levels
_ALPHA_FLOOR, _SAMPLES = 1.0 / 255.0, 60000   # a weight under one 8-bit level counts as none; pixels the fit reads


def _grown(c: np.ndarray) -> np.ndarray:
    """A cell, or one of its eight neighbours."""
    p = np.pad(c, 1)
    h, w = c.shape
    out = np.zeros_like(c)
    for dy in range(3):
        for dx in range(3):
            out |= p[dy:dy + h, dx:dx + w]
    return out


def from_edge(cand: np.ndarray, grid: int = _GRID) -> np.ndarray:
    """The part of the bool map `cand` that reaches the frame edge through `cand`. Square cells of
    `ceil(max(h, w) / grid)` pixels count when at least half of them is `cand`; the counting cells on the
    edge are grown through counting cells (8-connected). One more ring of cells is kept, so the rim along
    a product's outline stays."""
    h, w = cand.shape
    b = max(1, math.ceil(max(h, w) / grid))
    ch, cw = -(-h // b), -(-w // b)
    padded = np.zeros((ch * b, cw * b), bool)
    padded[:h, :w] = cand
    cells = padded.reshape(ch, b, cw, b).mean(axis=(1, 3)) >= 0.5
    seen = np.zeros_like(cells)
    seen[0], seen[-1], seen[:, 0], seen[:, -1] = True, True, True, True
    seen &= cells
    while True:
        nxt = _grown(seen) & cells
        if nxt.sum() == seen.sum():
            break
        seen = nxt
    big = np.kron(_grown(seen), np.ones((b, b), bool))[:h, :w]
    return cand & big


def find(rgb: np.ndarray, t: dict = DEFAULTS, keep: np.ndarray | None = None) -> np.ndarray:
    """The backdrop of the gamma picture `rgb` as a bool map: light, near neutral, reaching the frame edge."""
    cand = (luma(rgb) >= t["min_level"]) & (rgb.max(axis=-1) - rgb.min(axis=-1) <= t["max_chroma"])
    mask = from_edge(cand)
    return mask & ~keep if keep is not None else mask


def surface(coef: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """With `u` of shape (1, w) and `v` of shape (rows, 1): the float32 array (rows, w, 3) of the sum over
    i, j of coef[i, j] * u**i * v**j."""
    coef = coef.astype(np.float32)
    n = coef.shape[0]
    out = 0.0
    for i in range(n):
        poly = sum(coef[i, j] * v[..., None] ** j for j in range(n - i))
        out = out + poly * u[..., None] ** i
    return np.asarray(out, np.float32)


def fit(rgb: np.ndarray, mask: np.ndarray, degree: int, clip: float = _CLIP) -> np.ndarray | None:
    """A smooth surface through the backdrop: per channel a polynomial of `degree` in x and y (both -1..1 over the frame),
    fitted three times without the pixels further than `clip` robust deviations from it (a shadow, a speck).
    Returns (degree + 1, degree + 1, 3), [i, j] for x**i * y**j, or None when too little is left."""
    h, w = mask.shape
    pw = [(i, j) for i in range(degree + 1) for j in range(degree + 1 - i)]
    step = max(1, int(np.sqrt(mask.sum() / _SAMPLES)))
    ys, xs = (v * step for v in np.nonzero(mask[::step, ::step]))
    if ys.size < 50 * len(pw):
        return None
    u, v = xs / max(w - 1, 1) * 2.0 - 1.0, ys / max(h - 1, 1) * 2.0 - 1.0
    a = np.stack([u ** i * v ** j for i, j in pw], axis=1)
    val = rgb[ys, xs].astype(np.float64)
    keep = np.ones(ys.size, bool)
    for _ in range(3):
        sol = np.linalg.lstsq(a[keep], val[keep], rcond=None)[0]
        res = luma(val - a @ sol)
        dev = 1.4826 * np.median(np.abs(res[keep] - np.median(res[keep])))
        keep = np.abs(res) <= clip * max(dev, _CLIP_FLOOR)
        if keep.sum() < 50 * len(pw):
            return None
    coef = np.zeros((degree + 1, degree + 1, 3))
    for (i, j), row in zip(pw, sol):
        coef[i, j] = row
    return coef


def even(rgb: np.ndarray, t: dict = DEFAULTS, long_edge: int = 1536, keep: np.ndarray | None = None,
         small: np.ndarray | None = None) -> tuple[np.ndarray, dict, np.ndarray | None]:
    """(picture, report, weight). `rgb`: the finished picture, float gamma in 0..1; `t`: `rules.block`. Where the
    weight is 0 the picture comes back bit for bit; when nothing is done it is `rgb` itself, the weight is None and
    `report["why"]` says why. `small`: the analysis-size copy when the caller has one (with the shape of `keep`)."""
    if small is None:
        small = downscale_long_edge(Image(rgb=rgb), long_edge).rgb
    mask = find(small, t, keep)
    cover = float(mask.mean())
    report = {"applied": False, "coverage": round(cover, 4), "white": float(t["white"])}
    coef = fit(small, mask, int(t["degree"])) if cover >= t["min_coverage"] else None
    if coef is None:
        report["why"] = (f"no light backdrop found ({cover * 100:.0f}% of the frame is bright, near neutral and reaches "
                         f"the frame edge; {t['min_coverage'] * 100:.0f}% is needed)" if cover < t["min_coverage"]
                         else "the backdrop is too patchy to fit a surface to")
        return rgb, report, None
    h, w = rgb.shape[:2]
    white, top = np.float32(t["white"]), np.float32(t["max_gain"])
    axis = lambda n: np.arange(n, dtype=np.float32) / np.float32(max(n - 1, 1)) * 2.0 - 1.0     # noqa: E731
    gain_of = lambda s: np.clip(white / np.maximum(s, 1e-3), 1.0 / top, top)                    # noqa: E731
    alpha = build_alpha(mask, (h, w), float(t["feather"]), guide=luma(rgb))
    alpha = np.clip((alpha - _ALPHA_FLOOR) / (1.0 - _ALPHA_FLOOR), 0.0, 1.0).astype(np.float32)
    if keep is not None:      # the soft edge never reaches into what a model found: those pixels stay as they are
        from PIL import Image as PILImage
        kept = np.asarray(PILImage.fromarray(keep.astype(np.uint8) * 255).resize((w, h), PILImage.NEAREST)) > 0
        alpha[kept] = 0.0
    u = axis(w)[None, :]

    def strip(x, a, ys):      # per pixel: its value, its weight, its place
        gain = gain_of(surface(coef, u, ys[:, :1] / np.float32(max(h - 1, 1)) * 2.0 - 1.0))
        out = x * (1.0 + a[..., None] * (gain - 1.0))
        return np.where(a[..., None] > 0.0, np.clip(out, 0.0, 1.0), x).astype(np.float32)

    out = par.rows(strip, rgb, alpha, np.broadcast_to(np.arange(h, dtype=np.float32)[:, None], (h, w)))
    s = surface(coef, axis(mask.shape[1])[None, :], axis(mask.shape[0])[:, None])[mask]   # for the report, at analysis size
    g = gain_of(s)
    report.update(applied=True, level=[round(float(s.min()), 4), round(float(s.max()), 4)],
                  gain=[round(float(g.min()), 4), round(float(g.max()), 4)],
                  limited=bool(g.max() >= top or g.min() <= 1.0 / top))
    return out, report, alpha
