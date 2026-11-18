"""Night sky (F31): is a photo a night frame, and the two corrections of the capture the
pipeline makes to the decoded photo before the edit. The pixel math is in edit/ops.py; the
thresholds are data, [night_sky] in rules.toml."""

from __future__ import annotations

import dataclasses

import numpy as np

from .concepts import meta_for
from .edit import build_alpha, ops
from .imageio import Image, luma

OPS = ("hot_pixels", "sky_gradient", "denoise_stars", "sharpen_stars")

DEFAULTS = {"max_median": 0.25, "min_iso": 1600, "tile": 0.03125, "min_share": 0.5,
            "degree": 2, "clip": 2.5, "level": 5}
RANGES = {"max_median": (0.0, 1.0), "min_iso": (0, 1_000_000), "tile": (0.005, 0.25),
          "min_share": (0.05, 1.0), "degree": (1, 4), "clip": (1.0, 10.0), "level": (0, 100)}

_LUMA = np.array([0.2126, 0.7152, 0.0722])


def is_night(g, cfg) -> bool:
    """A night frame: dark (median luma) and shot at a high ISO (no EXIF: ISO 0, never night)."""
    return g.lum_p50 <= cfg["max_median"] and g.iso >= cfg["min_iso"]


def _terms(x, y, degree):
    return [x ** i * y ** j for i in range(degree + 1) for j in range(degree + 1 - i)]


def surface(coef, h, w, degree) -> np.ndarray:
    """The polynomial of `coef` (terms, 3) on the h x w grid of pixel centres in -1..1, HxWx3 float32."""
    y = ((np.arange(h) + 0.5) / h * 2.0 - 1.0).astype(np.float32)[:, None]
    x = ((np.arange(w) + 0.5) / w * 2.0 - 1.0).astype(np.float32)[None, :]
    out = np.zeros((h, w, 3), np.float32)
    for t, c in zip(_terms(x, y, degree), coef):
        for k in range(3):
            out[..., k] += np.float32(c[k]) * t
    return out


def fit(rgb, sky, cfg) -> tuple[np.ndarray | None, int]:
    """(coefficients (terms, 3), tiles used) of the sky's background, or (None, tiles) with fewer than
    3 tiles per term. The median of each square tile in which at least `min_share` of the pixels are
    sky and off every star, at the tile's centre in -1..1; tiles further than `clip` robust sigmas
    from the fit of their luma are dropped and the fit made again, three times at most."""
    h, w = rgb.shape[:2]
    degree = int(cfg["degree"])
    good = sky & (ops.star_weight(luma(rgb)) <= 0.0)
    n = max(4, int(round(cfg["tile"] * max(h, w))))
    xs, ys, vals = [], [], []
    for y0 in range(0, h - n // 2, n):
        for x0 in range(0, w - n // 2, n):
            g = good[y0:y0 + n, x0:x0 + n]
            if g.mean() >= cfg["min_share"]:
                vals.append(np.median(rgb[y0:y0 + n, x0:x0 + n][g], axis=0))
                ys.append((y0 + g.shape[0] / 2.0) / h * 2.0 - 1.0)
                xs.append((x0 + g.shape[1] / 2.0) / w * 2.0 - 1.0)
    k = len(_terms(0.0, 0.0, degree))
    if len(vals) < 3 * k:
        return None, len(vals)
    X = np.stack(_terms(np.asarray(xs), np.asarray(ys), degree), axis=1)
    V = np.asarray(vals, np.float64)
    L, keep = V @ _LUMA, np.ones(len(V), bool)
    for _ in range(3):
        c, *_ = np.linalg.lstsq(X[keep], L[keep], rcond=None)
        res = L - X @ c
        new = np.abs(res) <= cfg["clip"] * (1.4826 * np.median(np.abs(res[keep] - np.median(res[keep]))) + 1e-6)
        if new.sum() < 3 * k or np.array_equal(new, keep):
            break
        keep = new
    coef, *_ = np.linalg.lstsq(X[keep], V[keep], rcond=None)
    return coef, int(keep.sum())


def correct(img: Image, proxy: Image, regions, settings: dict, cfg: dict, meta) -> tuple[Image, list[str]]:
    """The two corrections of the capture, made to the decoded photo: stuck pixels, then the sky's
    gradient. Never changes `img`; returns it as it is when nothing changed."""
    rgb, notes = img.rgb, []
    amt = settings.get("hot_pixels", 0.0)
    if amt > 0:
        new = ops.hot_pixels(rgb, amt)
        n = int((new != rgb).any(axis=2).sum()) if new is not rgb else 0
        notes.append(f"night sky: {n} stuck pixels replaced by their neighbours" if n
                     else "night sky: no stuck pixel found")
        rgb = new
    amt = settings.get("sky_gradient", 0.0)
    if amt > 0:
        sky = next((r for r in regions if r.name == "sky" and r.mask.any()), None)
        if sky is None:
            notes.append("night sky: no sky found, its gradient is left")
        else:
            degree = int(cfg["degree"])
            coef, tiles = fit(proxy.rgb, sky.mask, cfg)
            if coef is None:
                notes.append(f"night sky: too little clear sky to fit its gradient ({tiles} tiles), it is left")
            else:
                h, w = rgb.shape[:2]
                ph, pw = proxy.rgb.shape[:2]
                on_sky = surface(coef, ph, pw, degree)[sky.mask]
                level = np.percentile(on_sky, cfg["level"], axis=0).astype(np.float32)
                offset = surface(coef, h, w, degree) - level
                alpha = build_alpha(sky.mask, (h, w), meta_for("sky", meta).feather_px, guide=luma(rgb))
                rgb = ops.sky_gradient(rgb, amt, offset, alpha)
                span = float((on_sky @ _LUMA.astype(np.float32)).max() - float(level @ _LUMA.astype(np.float32)))
                notes.append(f"night sky: the sky's gradient taken off, {span * 255:.0f} levels at most "
                             f"(fitted on {tiles} tiles)")
    return (img if rgb is img.rgb else dataclasses.replace(img, rgb=rgb)), notes
