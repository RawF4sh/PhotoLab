"""F22 hide on request: the last pixel step of apply, only for the kinds the user named.

Content rule: AutoEdit never invents content. Nothing here generates, copies or fills in picture
content; a region is covered with one flat colour, made into blocks or blurred, and every hidden
region is reported. The mask is grown by a margin and has a hard edge (never feathered inward), so
a pixel outside the grown mask is never touched. Works on the full-size picture, on a copy."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image as PILImage
from PIL import ImageFilter

TRACE = {"cover": False, "mosaic": True, "blur": True}   # does the method keep a trace of what was there?
METHODS = tuple(TRACE)
_GREY = 0.5      # a cover with no surroundings at all (the region is the whole frame)


def _grow(m: np.ndarray, r: int) -> np.ndarray:
    """Grow a mask by r pixels in x and y (a square, which is exact for a plate and a little generous for a blob).
    ponytail: square, not round; a disc costs a distance transform that numpy alone does not have."""
    def one(a: np.ndarray) -> np.ndarray:    # along axis 0, with a running sum
        n = a.shape[0]
        c = np.zeros((n + 2 * r + 1,) + a.shape[1:], np.int32)
        c[r + 1:r + n + 1] = np.cumsum(a, axis=0, dtype=np.int32)
        c[r + n + 1:] = c[r + n]
        return (c[2 * r + 1:2 * r + 1 + n] - c[:n]) > 0
    return m if r <= 0 else one(one(m).T).T


def _crop_mask(mask: np.ndarray, full_hw: tuple[int, int]):
    """(y0, y1, x0, x1, sub): the area of an analysis-size mask in full-size pixels, and the mask there (nearest)."""
    ph, pw = mask.shape
    H, W = full_hw
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    fy0, fy1 = int(math.floor(y0 * H / ph)), min(H, int(math.ceil(y1 * H / ph)))
    fx0, fx1 = int(math.floor(x0 * W / pw)), min(W, int(math.ceil(x1 * W / pw)))
    fy1, fx1 = max(fy1, min(H, fy0 + 1)), max(fx1, min(W, fx0 + 1))     # a region smaller than a pixel is still one pixel
    sub = PILImage.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255).resize((fx1 - fx0, fy1 - fy0), PILImage.NEAREST)
    return fy0, fy1, fx0, fx1, np.asarray(sub) > 0


def apply(rgb: np.ndarray, masks: list[np.ndarray], conf, method: str):
    """A copy of `rgb` (float, HxWx3) with each mask's grown area hidden by `method`, and per mask the
    box of the area hidden (x0, y0, x1, y1 as shares of the frame). `masks` are bool, at the analysis size;
    `conf` is `reveal.Hide`. With nothing to hide the picture comes back untouched (the same array)."""
    if not masks:
        return rgb, []
    if method not in METHODS:
        raise ValueError(f"hide method {method!r}: choose one of {', '.join(METHODS)}")
    H, W = rgb.shape[:2]
    ph = masks[0].shape[0]
    plan = []     # per region: (canvas y0, y1, x0, x1, grown mask on the canvas, shorter side, margin px)
    union = np.zeros((H, W), bool)
    for m in masks:
        fy0, fy1, fx0, fx1, sub = _crop_mask(m, (H, W))
        short = max(1, min(fy1 - fy0, fx1 - fx0))
        r = max(int(round(conf.margin * short)), int(math.ceil(conf.margin_min_px * H / ph)))
        ring = max(1, int(round(conf.ring * short))) if method == "cover" else 0
        sigma = conf.sigma * short if method == "blur" else 0.0
        pad = r + max(ring, int(math.ceil(3 * sigma)))
        cy0, cy1, cx0, cx1 = max(0, fy0 - pad), min(H, fy1 + pad), max(0, fx0 - pad), min(W, fx1 + pad)
        canvas = np.zeros((cy1 - cy0, cx1 - cx0), bool)
        canvas[fy0 - cy0:fy1 - cy0, fx0 - cx0:fx1 - cx0] = sub
        grown = _grow(canvas, r)
        union[cy0:cy1, cx0:cx1] |= grown
        plan.append((cy0, cy1, cx0, cx1, grown, short, ring, sigma))
    out = rgb.copy()
    boxes = []
    for cy0, cy1, cx0, cx1, grown, short, ring, sigma in plan:
        base = rgb[cy0:cy1, cx0:cx1]          # always the picture before any hiding
        view = out[cy0:cy1, cx0:cx1]
        if method == "cover":
            # Only pixels outside every grown area count: no pixel under a hidden area is copied into the fill.
            around = _grow(grown, ring) & ~union[cy0:cy1, cx0:cx1]
            view[grown] = base[around].mean(axis=0) if around.any() else _GREY
        else:
            y, x = np.nonzero(grown)
            ty0, ty1, tx0, tx1 = y.min(), y.max() + 1, x.min(), x.max() + 1
            tile = PILImage.fromarray(np.clip(np.rint(base * 255.0), 0, 255).astype(np.uint8))
            if method == "blur":
                tile = tile.filter(ImageFilter.GaussianBlur(max(1.0, sigma)))
            else:
                block = max(1, int(round(short / conf.blocks)))
                part = tile.crop((tx0, ty0, tx1, ty1))
                part = part.resize((max(1, -(-part.width // block)), max(1, -(-part.height // block))), PILImage.BOX)
                tile = tile.copy()
                tile.paste(part.resize((tx1 - tx0, ty1 - ty0), PILImage.NEAREST), (tx0, ty0))
            view[grown] = (np.asarray(tile, dtype=np.float32) / 255.0)[grown].astype(out.dtype)
        ys, xs = np.nonzero(grown)
        boxes.append((round((cx0 + xs.min()) / W, 4), round((cy0 + ys.min()) / H, 4),
                      round((cx0 + xs.max() + 1) / W, 4), round((cy0 + ys.max() + 1) / H, 4)))
    return out, boxes
