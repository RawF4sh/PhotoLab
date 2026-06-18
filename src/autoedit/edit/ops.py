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
    "curve", "clarity", "vibrance", "saturation", "dehaze", "temp", "tint",
    # color grading (split-tone) — tint shadows/highlights independently
    "grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint",
    # targeted HSL — adjust specific hue bands
    "hsl_skin_sat", "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat",
    # output sharpening (unsharp mask) — applied last
    "sharpen",
)

OP_BOUNDS = {
    "exposure": (-3.0, 3.0),
    "contrast": (-100.0, 100.0),
    "highlights": (-100.0, 100.0),
    "shadows": (-100.0, 100.0),
    "whites": (-100.0, 100.0),
    "blacks": (-100.0, 100.0),
    "curve": (-100.0, 100.0),
    "clarity": (-100.0, 100.0),
    "vibrance": (-100.0, 100.0),
    "saturation": (-100.0, 100.0),
    "dehaze": (-100.0, 100.0),
    "temp": (-100.0, 100.0),
    "tint": (-100.0, 100.0),
    "grade_shadows_temp": (-100.0, 100.0),
    "grade_shadows_tint": (-100.0, 100.0),
    "grade_highlights_temp": (-100.0, 100.0),
    "grade_highlights_tint": (-100.0, 100.0),
    "hsl_skin_sat": (-100.0, 100.0),
    "hsl_green_sat": (-100.0, 100.0),
    "hsl_green_lum": (-100.0, 100.0),
    "hsl_blue_sat": (-100.0, 100.0),
    "sharpen": (-100.0, 100.0),
}

_GRADE_KEYS = ("grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint")
_HSL_KEYS = ("hsl_skin_sat", "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat")


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


def sharpen(rgb: np.ndarray, amt: float) -> np.ndarray:
    """Unsharp mask on luminance — fine-edge sharpening (vs clarity's broad local contrast).
    A small-radius high-pass added back to luma; color is preserved via _scale_luma."""
    l = luma(rgb)
    sigma = 0.0007 * max(rgb.shape[0], rgb.shape[1]) + 0.8  # small radius → edges, not local contrast
    detail = l - gaussian_blur(l, sigma)
    return _scale_luma(rgb, l + (amt / 100.0) * 1.6 * detail, l)


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


def curve(rgb: np.ndarray, strength: float) -> np.ndarray:
    """Filmic S-curve on luminance — smoother contrast that rolls off the ends."""
    l = luma(rgb)
    s = strength / 100.0
    smooth = l * l * l * (l * (l * 6.0 - 15.0) + 10.0)  # smootherstep(l)
    return _scale_luma(rgb, l + s * (smooth - l), l)


def color_grade(rgb, sh_temp=0.0, sh_tint=0.0, hi_temp=0.0, hi_tint=0.0) -> np.ndarray:
    """Split-tone: tint shadows and highlights toward different colors (the 'graded' look)."""
    l = luma(rgb)[..., None]
    ws = np.clip((0.5 - l) / 0.5, 0.0, 1.0)  # shadow weight
    wh = np.clip((l - 0.5) / 0.5, 0.0, 1.0)  # highlight weight

    def offset(temp, tint):
        t, n, k = temp / 100.0, tint / 100.0, 0.08
        # warm temp → +R/-B ; +tint → magenta (+R/+B/-G)
        return np.array([t + 0.5 * n, -n, -t + 0.5 * n], dtype=np.float32) * k

    return rgb + ws * offset(sh_temp, sh_tint) + wh * offset(hi_temp, hi_tint)


def _rgb_to_hsv(rgb: np.ndarray):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > _EPS
    i = (mx == r) & nz
    h[i] = (60.0 * ((g - b)[i] / d[i])) % 360.0
    i = (mx == g) & nz
    h[i] = (60.0 * ((b - r)[i] / d[i]) + 120.0) % 360.0
    i = (mx == b) & nz
    h[i] = (60.0 * ((r - g)[i] / d[i]) + 240.0) % 360.0
    s = np.where(mx > _EPS, d / np.maximum(mx, _EPS), 0.0)
    return h, s.astype(np.float32), mx.astype(np.float32)


def _hsv_to_rgb(h, s, v):
    c = v * s
    hp = h / 60.0
    x = c * (1.0 - np.abs(hp % 2.0 - 1.0))
    m = v - c
    z = np.zeros_like(h)
    sectors = [hp < 1, hp < 2, hp < 3, hp < 4, hp < 5, hp < 6]
    r = np.select(sectors, [c, x, z, z, x, c], default=c)
    g = np.select(sectors, [x, c, c, x, z, z], default=z)
    b = np.select(sectors, [z, z, x, c, c, x], default=z)
    return np.clip(np.stack([r + m, g + m, b + m], axis=-1), 0.0, 1.0).astype(np.float32)


def targeted_hsl(rgb, skin_sat=0.0, green_sat=0.0, green_lum=0.0, blue_sat=0.0) -> np.ndarray:
    """Adjust saturation/luminance of specific hue bands (skin/orange, green, blue)."""
    h, s, v = _rgb_to_hsv(rgb)

    def band(center, width):
        dist = np.abs((h - center + 180.0) % 360.0 - 180.0)
        return np.clip(1.0 - dist / width, 0.0, 1.0) * s  # weight by saturation → neutrals safe

    bskin, bgreen, bblue = band(30.0, 35.0), band(120.0, 70.0), band(220.0, 70.0)
    s = s * (1.0 + (skin_sat / 100.0) * bskin + (green_sat / 100.0) * bgreen + (blue_sat / 100.0) * bblue)
    v = v + (green_lum / 100.0) * 0.3 * bgreen
    return _hsv_to_rgb(h, np.clip(s, 0.0, 1.0), np.clip(v, 0.0, 1.0))


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
    if settings.get("curve", 0.0):
        out = curve(out, settings["curve"])
    if settings.get("dehaze", 0.0):
        out = dehaze(out, settings["dehaze"])
    if settings.get("clarity", 0.0):
        out = clarity(out, settings["clarity"])
    if settings.get("vibrance", 0.0) or settings.get("saturation", 0.0):
        out = vibrance(out, settings.get("vibrance", 0.0), settings.get("saturation", 0.0))
    if any(settings.get(k, 0.0) for k in _HSL_KEYS):
        out = targeted_hsl(out, settings.get("hsl_skin_sat", 0.0), settings.get("hsl_green_sat", 0.0),
                           settings.get("hsl_green_lum", 0.0), settings.get("hsl_blue_sat", 0.0))
    if any(settings.get(k, 0.0) for k in _GRADE_KEYS):
        out = color_grade(out, settings.get("grade_shadows_temp", 0.0), settings.get("grade_shadows_tint", 0.0),
                          settings.get("grade_highlights_temp", 0.0), settings.get("grade_highlights_tint", 0.0))
    if settings.get("sharpen", 0.0):  # output sharpening, last
        out = sharpen(out, settings["sharpen"])
    return out
