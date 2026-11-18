"""Geometry (F32): straighten a clear waterline or horizon, and crop to a shape.

Decided in `pipeline.process_one` from the masks (a `Geometry` record), done to the finished
picture where it leaves the engine (`emit.write_image`, the preview). Off by default.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image as PILImage

from .contract import Geometry

LEVEL = ("water", "sky")     # the masks straighten reads, in this order
DEFAULTS = {"max_angle": 5.0, "min_angle": 0.3, "tolerance": 0.004, "water_span": 0.5, "water_inliers": 0.8,
            "sky_span": 0.6, "sky_inliers": 0.9, "subject": ("subject", "person")}
_SCAN, _STEP = 8.0, 0.05     # degrees tried on each side of level, and between two tries
_MIN_DEPTH = 0.02            # a column counts when the mask is this share of the height deep there

def config(rules: dict) -> dict:
    """`DEFAULTS` with the `[geometry]` block of `rules` laid over it (an older rules file has none)."""
    out = dict(DEFAULTS)
    for k, v in (rules.get("geometry") or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"[geometry] {k}: not a key of the block (keys: {', '.join(DEFAULTS)})")
        if k == "subject":
            if not isinstance(v, list) or not all(isinstance(n, str) and n for n in v):
                raise ValueError(f"[geometry] subject must be a list of words, not {v!r}")
            out[k] = tuple(v)
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f"[geometry] {k} must be a number of 0 or more, not {v!r}")
        out[k] = float(v)
    if not 0 < out["min_angle"] < out["max_angle"] <= _SCAN or out["tolerance"] <= 0 or \
            not all(0 < out[k] <= 1 for k in ("water_span", "water_inliers", "sky_span", "sky_inliers")):
        raise ValueError("[geometry]: 0 < min_angle < max_angle <= 8, tolerance above 0, spans and inliers in (0, 1]")
    return out


def _edge(mask: np.ndarray, top: bool) -> tuple[np.ndarray, np.ndarray]:
    """(x, y) of the mask's upper (`top`) or lower edge: one point per column that has one inside the frame."""
    h = mask.shape[0]
    y = mask.argmax(axis=0) if top else h - mask[::-1].argmax(axis=0)      # the first masked row, or one past the last
    ok = (mask.sum(axis=0) >= max(2, _MIN_DEPTH * h)) & ((y >= 2) if top else (y <= h - 2))
    x = np.nonzero(ok)[0]
    return x + 0.5, y[x].astype(np.float64)

def _fit(x: np.ndarray, y: np.ndarray, tol: float) -> tuple[float, int]:
    """(slope in degrees, points on it) of the straight line most points lie on, within `tol` pixels.
    No random numbers: every slope is tried, and the best is refined by least squares over its points."""
    best, on = 0, None
    for a in np.arange(-_SCAN, _SCAN + _STEP / 2, _STEP):
        r = y - math.tan(math.radians(a)) * x
        hit = np.abs(r - np.median(r)) <= tol
        if int(hit.sum()) > best:
            best, on = int(hit.sum()), hit
    if best < 2:
        return 0.0, best
    return math.degrees(math.atan(float(np.polyfit(x[on], y[on], 1)[0]))), best

def horizon(masks: dict[str, np.ndarray], cfg: dict) -> tuple[float, str, float, str]:
    """(angle, line, confidence, why). `angle`: degrees the line slopes, positive when it falls to the right.
    `line`: "waterline", "horizon", or "" when no line is trusted; `why` then says what stood in the way."""
    why = "no water and no sky found"
    for name in LEVEL:
        mask = masks.get(name)
        if mask is None or not mask.any():
            continue
        h, w = mask.shape
        line = "horizon" if name == "sky" else "waterline"
        span, inliers = (cfg["sky_span"], cfg["sky_inliers"]) if name == "sky" else (cfg["water_span"], cfg["water_inliers"])
        x, y = _edge(mask, top=name != "sky")
        if x.size < span * w:
            why = f"the {line} covers too little of the width"
            continue
        angle, n = _fit(x, y, max(1.0, cfg["tolerance"] * max(h, w)))
        if n < span * w or n < inliers * x.size:
            why = f"the {line} is not one straight line (a ridge or a shore, not a horizon)"
        elif abs(angle) > cfg["max_angle"]:
            why = f"the {line} slopes {abs(angle):.1f} degrees, more than the limit of {cfg['max_angle']:g}"
        else:
            return angle, line, n / x.size, ""
    return 0.0, "", 0.0, why

def frame(angle: float, ratio: float | None, hw: tuple[int, int],
          centre: tuple[float, float] | None = None) -> tuple[float, float, float, float]:
    """(cx, cy, w, h) in shares of the photo: the largest upright rectangle of `ratio` (width over height;
    None: the photo's own) inside a photo of `hw` turned by `angle`, its middle on `centre` as far as it can be."""
    H, W = hw
    r = ratio or W / H
    c, s = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    b = min((W / 2) / (r * c + s), (H / 2) / (r * s + c))      # half the height
    a = r * b                                                  # half the width
    free_x, free_y = W / 2 - (a * c + b * s), H / 2 - (a * s + b * c)    # how far the middle may move
    tx, ty = (0.0, 0.0) if centre is None else ((centre[0] - 0.5) * W, (centre[1] - 0.5) * H)
    return (0.5 + min(max(tx, -free_x), free_x) / W, 0.5 + min(max(ty, -free_y), free_y) / H,
            2 * a / W, 2 * b / H)

def decide(masks: dict[str, np.ndarray], hw: tuple[int, int], straighten: bool, aspect: str | None,
           cfg: dict) -> tuple[Geometry | None, list[str]]:
    """(what to do or None, notes for the sidecar). `masks`: name -> bool mask at analysis size.
    `hw`: (height, width) of the photo. `aspect`: "W:H" as RunConfig checked it, or None."""
    notes: list[str] = []
    angle, line, conf = 0.0, "", 0.0
    if straighten:
        found, line, conf, why = horizon(masks, cfg)
        # A rise of one pixel over the width of the masks cannot be told from level.
        level = max(cfg["min_angle"], math.degrees(2.0 / max(m.shape[1] for m in masks.values()))) if masks else 0.0
        if not line:
            notes.append(f"straighten: left alone, {why}")
        elif abs(found) < level:
            notes.append(f"straighten: left alone, the {line} is level (within {level:.2g} degrees)")
            line, conf = "", 0.0
        else:
            angle = found
    ratio = None
    if aspect:
        aw, ah = (int(n) for n in aspect.split(":"))
        ratio = aw / ah
        if not angle and abs(round(hw[0] * ratio) - hw[1]) <= 1:
            notes.append(f"crop: left alone, the photo already has the shape {aspect}")
            ratio = None
    if not angle and ratio is None:
        return None, notes
    # ponytail: the middle of all subject pixels, so two far apart put the frame between them;
    # search for the frame that keeps the most of them if that shows on real photos.
    centre = None
    subjects = [masks[n] for n in cfg["subject"] if n in masks and masks[n].any()] if ratio else []
    if subjects:
        ys, xs = np.nonzero(np.logical_or.reduce(subjects))
        centre = ((xs.mean() + 0.5) / subjects[0].shape[1], (ys.mean() + 0.5) / subjects[0].shape[0])
    cx, cy, w, h = frame(angle, ratio, hw, centre)
    geo = Geometry(angle=round(angle, 3), line=line, confidence=round(conf, 3), aspect=aspect if ratio else "",
                   cx=round(cx, 6), cy=round(cy, 6), w=round(w, 6), h=round(h, 6), kept=round(w * h, 4))
    if angle:
        notes.append(f"straighten: turned {abs(angle):.2f} degrees {'counter-clockwise' if angle > 0 else 'clockwise'} "
                     f"to level the {line} ({conf * 100:.0f} % of it lies on one line)")
    if ratio:
        notes.append(f"crop: to {aspect}, placed {'on the subject' if centre else 'in the middle'}")
    notes.append(f"geometry: {geo.kept * 100:.1f} % of the photo is kept, {(1 - geo.kept) * 100:.1f} % is cut; "
                 "done to the export after the checks, nothing is added")
    return geo, notes

def size(geo: Geometry, hw: tuple[int, int]) -> tuple[int, int]:
    """(height, width) in pixels of the frame `geo` on a picture of `hw`."""
    inset = 2 if geo.angle else 0       # a turned frame stays a pixel inside the picture on every side
    return max(1, round(geo.h * hw[0]) - inset), max(1, round(geo.w * hw[1]) - inset)

def warp(rgb: np.ndarray, geo: Geometry | None) -> np.ndarray:
    """The frame `geo` of the picture `rgb` (float, HxWxC); None returns `rgb` itself. A crop alone is
    a cut on whole pixels; a turn is one bicubic resampling, clipped to 0..1."""
    if geo is None:
        return rgb
    H, W = rgb.shape[:2]
    h, w = size(geo, (H, W))
    cx, cy = geo.cx * W, geo.cy * H
    if not geo.angle:
        x0 = min(max(round(cx - w / 2), 0), W - w)
        y0 = min(max(round(cy - h / 2), 0), H - h)
        return rgb[y0:y0 + h, x0:x0 + w]
    c, s = math.cos(math.radians(geo.angle)), math.sin(math.radians(geo.angle))
    # Pillow's affine: the middle of output pixel (x, y) is read at (a x + b y + c, d x + e y + f).
    coeffs = (c, -s, cx - c * w / 2 + s * h / 2, s, c, cy - s * w / 2 - c * h / 2)
    chans = [np.asarray(PILImage.fromarray(np.ascontiguousarray(rgb[..., i], dtype=np.float32), mode="F")
                        .transform((w, h), PILImage.AFFINE, coeffs, resample=PILImage.BICUBIC), dtype=np.float32)
             for i in range(rgb.shape[2])]
    return np.clip(np.stack(chans, axis=-1), 0.0, 1.0)
