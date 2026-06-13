"""Edit-op primitives — each maps an op name + scalar amount to a pixel operation.

Operate on a gamma-encoded working RGB array (float32, HxWx3, ~[0,1]); light-like ops
(exposure, white balance) linearize internally. `apply_ops` runs the FROZEN order:
WB → exposure → tone → contrast → dehaze → clarity → vibrance/saturation.

Units: exposure in stops; everything else ~[-100,100]; temp/tint in the same range.
An empty/all-zero settings dict is a strict no-op (the identity test depends on this).
"""

from __future__ import annotations

import numpy as np

from ..imageio import linear_to_srgb, luma, srgb_to_linear
from .filters import gaussian_blur

_EPS = 1e-6

OP_NAMES = (
    "exposure", "contrast", "highlights", "shadows", "whites", "blacks",
    "clarity", "vibrance", "saturation", "dehaze", "temp", "tint",
)

OP_BOUNDS = {
    "exposure": (-3.0, 3.0),
    "contrast": (-100.0, 100.0),
    "highlights": (-100.0, 100.0),
    "shadows": (-100.0, 100.0),
    "whites": (-100.0, 100.0),
    "blacks": (-100.0, 100.0),
    "clarity": (-100.0, 100.0),
    "vibrance": (-100.0, 100.0),
    "saturation": (-100.0, 100.0),
    "dehaze": (-100.0, 100.0),
    "temp": (-100.0, 100.0),
    "tint": (-100.0, 100.0),
}


def _pixsat(rgb: np.ndarray) -> np.ndarray:
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    out = np.zeros_like(mx)
    nz = mx > _EPS
    out[nz] = (mx[nz] - mn[nz]) / mx[nz]
    return out


def exposure(rgb: np.ndarray, ev: float) -> np.ndarray:
    lin = srgb_to_linear(rgb) * (2.0**ev)
    return linear_to_srgb(lin)


def white_balance(rgb: np.ndarray, temp: float, tint: float) -> np.ndarray:
    lin = srgb_to_linear(rgb).copy()
    rg = 1.0 + 0.2 * (temp / 100.0)
    bg = 1.0 - 0.2 * (temp / 100.0)
    gg = 1.0 + 0.1 * (tint / 100.0)
    lin[..., 0] *= rg
    lin[..., 1] *= gg
    lin[..., 2] *= bg
    return linear_to_srgb(lin)


def _scale_luma(rgb: np.ndarray, new_l: np.ndarray, l: np.ndarray) -> np.ndarray:
    ratio = (new_l + _EPS) / (l + _EPS)
    return rgb * ratio[..., None]


def tone(rgb, highlights=0.0, shadows=0.0, whites=0.0, blacks=0.0) -> np.ndarray:
    l = luma(rgb)
    hi = np.clip((l - 0.5) / 0.5, 0, 1)
    sh = np.clip((0.5 - l) / 0.5, 0, 1)
    wh = np.clip((l - 0.7) / 0.3, 0, 1)
    bl = np.clip((0.3 - l) / 0.3, 0, 1)
    delta = 0.5 * (
        (highlights / 100.0) * hi
        + (shadows / 100.0) * sh
        + (whites / 100.0) * wh
        + (blacks / 100.0) * bl
    )
    return _scale_luma(rgb, l + delta, l)


def contrast(rgb: np.ndarray, amt: float) -> np.ndarray:
    f = 1.0 + amt / 100.0
    return (rgb - 0.5) * f + 0.5


def clarity(rgb: np.ndarray, amt: float) -> np.ndarray:
    l = luma(rgb)
    sigma = 0.01 * max(rgb.shape[0], rgb.shape[1]) + 1.0
    detail = l - gaussian_blur(l, sigma)
    mid = np.clip(1.0 - np.abs(l - 0.5) * 2.0, 0, 1)  # protect highlights/shadows
    return _scale_luma(rgb, l + (amt / 100.0) * detail * mid * 2.0, l)


def vibrance(rgb: np.ndarray, vib: float, sat: float) -> np.ndarray:
    l = luma(rgb)[..., None]
    chroma = rgb - l
    s = 1.0 + sat / 100.0
    psat = _pixsat(rgb)[..., None]
    v = 1.0 + (vib / 100.0) * (1.0 - psat)  # boost dull colors more (vibrance)
    return l + chroma * (s * v)


def dehaze(rgb: np.ndarray, amt: float) -> np.ndarray:
    # MVP approximation: midtone contrast + saturation lift. A dark-channel transmission
    # model is the Phase-3 upgrade (see plan risks: dehaze is quality-sensitive).
    out = contrast(rgb, amt * 0.5)
    return vibrance(out, 0.0, amt * 0.4)


def apply_ops(rgb: np.ndarray, settings: dict) -> np.ndarray:
    """Apply a settings bundle in the frozen order. Does not mutate the input."""
    out = rgb
    temp, tint = settings.get("temp", 0.0), settings.get("tint", 0.0)
    if temp or tint:
        out = white_balance(out, temp, tint)
    if settings.get("exposure", 0.0):
        out = exposure(out, settings["exposure"])
    if any(settings.get(k, 0.0) for k in ("highlights", "shadows", "whites", "blacks")):
        out = tone(out, settings.get("highlights", 0.0), settings.get("shadows", 0.0),
                   settings.get("whites", 0.0), settings.get("blacks", 0.0))
    if settings.get("contrast", 0.0):
        out = contrast(out, settings["contrast"])
    if settings.get("dehaze", 0.0):
        out = dehaze(out, settings["dehaze"])
    if settings.get("clarity", 0.0):
        out = clarity(out, settings["clarity"])
    if settings.get("vibrance", 0.0) or settings.get("saturation", 0.0):
        out = vibrance(out, settings.get("vibrance", 0.0), settings.get("saturation", 0.0))
    return out
