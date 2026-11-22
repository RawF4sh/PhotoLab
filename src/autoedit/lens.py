"""Lens corrections of the capture (F46): lateral chromatic aberration. Part of the decode
(imageio.load_image), at full resolution, before the analysis proxy is made."""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from .edit.filters import box_blur

# Constants of the correction, like the op constants in edit/ops.py (the decode reads no rules file).
_CA_MAX_PX = 6.0        # largest fringe searched, either way, in pixels at the frame corner
_CA_STEP_PX = 0.25      # search step at the corner; a parabola through the best three refines it
_CA_MIN_PX = 0.3        # a channel whose fringe at the corner is smaller is left as it is
_CA_SAMPLES = 200_000   # the strongest green edges that are compared
_CA_INNER = 0.5         # edges from this share of the half-diagonal outward (lateral CA grows with the radius)
_CA_HP = 2              # radius of the box blur the high-pass subtracts


def scale_channel(ch: np.ndarray, s: float) -> np.ndarray:
    """`ch` resampled so that out(p) = ch(c + s (p - c)), c the frame centre, bilinear (Pillow).
    The channel is padded with its own edge values first, so the border repeats its pixels instead
    of turning black. s == 1.0 returns `ch` itself."""
    if s == 1.0:
        return ch
    k = int(np.ceil(_CA_MAX_PX)) + 2
    p = np.pad(ch, k, mode="edge")
    h, w = p.shape
    im = PILImage.fromarray(np.ascontiguousarray(p, dtype=np.float32), mode="F")
    t = im.transform((w, h), PILImage.AFFINE, data=(s, 0.0, w / 2 * (1 - s), 0.0, s, h / 2 * (1 - s)),
                     resample=PILImage.BILINEAR)
    return np.asarray(t, dtype=np.float32)[k:-k, k:-k]


def _bilinear(a: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """`a` at float index coordinates (pixel centres at integers), clamped to the frame."""
    h, w = a.shape
    ys = np.clip(ys, 0.0, h - 1.001)
    xs = np.clip(xs, 0.0, w - 1.001)
    y0, x0 = ys.astype(np.int64), xs.astype(np.int64)
    fy, fx = (ys - y0).astype(np.float32), (xs - x0).astype(np.float32)
    i = y0 * w + x0          # one flat index instead of four 2-D fancy lookups (same elements)
    af = a.reshape(-1)
    p, q, r, t = af[i], af[i + 1], af[i + w], af[i + w + 1]
    return p + (q - p) * fx + (r - p) * fy + (p - q - r + t) * fx * fy


def ca_prep(green: np.ndarray):
    """What `ca_scale` shares between channels, from green alone: (R, hg-at-edges normalised, ys, xs),
    or (R, None, None, None) when there are fewer than 1000 candidate pixels."""
    h, w = green.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    R = float(np.hypot(cy, cx))
    hg = green - box_blur(green, _CA_HP)
    gy, gx = np.gradient(green)
    mag = np.hypot(gy, gx)
    rho = np.hypot((np.arange(h, dtype=np.float32) - cy)[:, None],
                   (np.arange(w, dtype=np.float32) - cx)[None, :]) / R
    cand = (rho > _CA_INNER) & (rho < 0.98)
    vals = mag[cand]
    if vals.size < 1000:
        return R, None, None, None
    k = min(_CA_SAMPLES, vals.size)
    thr = np.partition(vals, vals.size - k)[vals.size - k]
    ys, xs = np.nonzero(cand & (mag >= thr))
    a = hg[ys, xs]
    a = (a - a.mean()) / (a.std() + 1e-9)
    return R, a, ys.astype(np.float32), xs.astype(np.float32)


def ca_scale(green: np.ndarray, other: np.ndarray, prep=None) -> tuple[float, float]:
    """(s, R): the scale about the frame centre at which `other` lines up best with `green`, so that
    other(c + s (p - c)) matches green(p); R is the half-diagonal in pixels, and (s - 1) * R the
    fringe at the corner. Compared: the high-passes (minus a box blur) at the _CA_SAMPLES strongest
    green edges beyond _CA_INNER of the half-diagonal, by correlation, for fringes from -_CA_MAX_PX
    to +_CA_MAX_PX in steps of _CA_STEP_PX. Returns s = 1.0 when the best is at an end of the range
    or there are fewer than 1000 candidate pixels. `prep` = ca_prep(green), to share it between channels."""
    # ponytail: one scale per channel (the linear term of lateral CA); a radial polynomial if a lens shows wavy fringes
    h, w = green.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    R, a, ys, xs = prep if prep is not None else ca_prep(green)
    if a is None:
        return 1.0, R
    ho = (other - box_blur(other, _CA_HP)).astype(np.float32)
    step = _CA_STEP_PX / R
    n = int(round(_CA_MAX_PX / _CA_STEP_PX))
    scales = 1.0 + step * np.arange(-n, n + 1)
    corr = np.empty(scales.size)
    for i, s in enumerate(scales):
        b = _bilinear(ho, cy + s * (ys - cy), cx + s * (xs - cx))
        corr[i] = float(np.mean(a * (b - b.mean()) / (b.std() + 1e-9)))
    i = int(np.argmax(corr))
    if i in (0, scales.size - 1):
        return 1.0, R
    c0, c1, c2 = corr[i - 1], corr[i], corr[i + 1]
    den = c0 - 2.0 * c1 + c2
    off = 0.5 * (c0 - c2) / den if den < 0 else 0.0
    return float(scales[i] + off * step), R


def correct_ca(rgb: np.ndarray) -> tuple[np.ndarray, dict]:
    """Red and blue scaled about the centre onto green (float HxWx3 in [0, 1]). A channel whose fringe
    at the corner is below _CA_MIN_PX keeps its values bit for bit; when both do, `rgb` itself is
    returned. Report: {"red": {"scale", "corner_px", "applied"}, "blue": {...}}."""
    green = np.ascontiguousarray(rgb[..., 1], dtype=np.float32)
    out, report = None, {}
    prep = ca_prep(green)
    for name, c in (("red", 0), ("blue", 2)):
        ch = np.ascontiguousarray(rgb[..., c], dtype=np.float32)
        s, R = ca_scale(green, ch, prep)
        px = (s - 1.0) * R
        applied = bool(abs(px) >= _CA_MIN_PX)
        report[name] = {"scale": round(s, 6), "corner_px": round(px, 2), "applied": applied}
        if applied:
            if out is None:
                out = rgb.copy()
            out[..., c] = scale_channel(ch, s)
    return (rgb if out is None else out), report
