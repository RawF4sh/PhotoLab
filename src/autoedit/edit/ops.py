"""Edit-op primitives — each maps an op name + scalar amount to a pixel operation.

Operate on a gamma-encoded working RGB array (float32, HxWx3, ~[0,1]); light-like ops
(exposure, white balance) linearize internally. `apply_ops` runs the FROZEN order:
WB → exposure → shadow lift → tone → contrast → dehaze → clarity → vibrance/saturation.

Units: exposure in stops; everything else ~[-100,100]; temp/tint in the same range.
An empty/all-zero settings dict is a strict no-op (the identity test depends on this).
"""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from ..imageio import linear_to_srgb, luma, srgb_to_linear
from . import par
from .filters import box_blur, box_min, gaussian_blur, guided_filter

_EPS = 1e-6

OP_NAMES = (
    # night sky (F31): corrections of the capture, made by the pipeline to the decoded photo
    "hot_pixels", "sky_gradient",
    "exposure", "highlight_rolloff", "highlight_detail", "shadows_ev", "burn_ev", "contrast", "highlights", "shadows", "whites",
    "blacks", "curve", "clarity", "black_point", "white_point", "vibrance", "saturation", "dehaze", "temp", "tint",
    # color grading (split-tone) — tint shadows/highlights independently
    "grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint",
    # targeted HSL — adjust specific hue bands
    "hsl_skin_sat", "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat",
    # black and white (F24): an amount and a weight per colour, the last colour step
    "bw", "bw_red", "bw_yellow", "bw_green", "bw_cyan", "bw_blue", "bw_magenta",
    # noise reduction (F7), then output sharpening (unsharp mask) — applied last
    "denoise", "denoise_stars", "sharpen", "sharpen_stars",
    # a LUT file (F25): how much of it is laid over the finished picture, by the pipeline
    "lut",
    # film grain (F23): a value in the bundle, added by emit.py to the written picture
    "grain",
)

_GRADE_KEYS = ("grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint")
_HSL_KEYS = ("hsl_skin_sat", "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat")
BW_KEYS = ("bw", "bw_red", "bw_yellow", "bw_green", "bw_cyan", "bw_blue", "bw_magenta")

OP_BOUNDS = {
    "exposure": (-3.0, 3.0),
    "highlight_rolloff": (0.0, 100.0),
    "highlight_detail": (0.0, 100.0),
    "burn_ev": (0.0, 2.0),
    "shadows_ev": (-2.5, 2.5),  # stops at black; the lift stays monotonic below 2.88
    "contrast": (-100.0, 100.0),
    "highlights": (-100.0, 100.0),
    "shadows": (-100.0, 100.0),
    "whites": (-100.0, 100.0),
    "blacks": (-100.0, 100.0),
    "curve": (-100.0, 100.0),
    "clarity": (-100.0, 100.0),
    "black_point": (0.0, 100.0),
    "white_point": (0.0, 100.0),
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
    "bw": (0.0, 100.0),
    "bw_red": (-100.0, 100.0),
    "bw_yellow": (-100.0, 100.0),
    "bw_green": (-100.0, 100.0),
    "bw_cyan": (-100.0, 100.0),
    "bw_blue": (-100.0, 100.0),
    "bw_magenta": (-100.0, 100.0),
    "denoise": (0.0, 100.0),
    "denoise_stars": (0.0, 100.0),
    "sharpen": (-100.0, 100.0),
    "sharpen_stars": (0.0, 100.0),
    "hot_pixels": (0.0, 100.0),
    "sky_gradient": (0.0, 100.0),
    "lut": (0.0, 100.0),
    "grain": (0.0, 100.0),
}

# Ops that read more than their own pixel: neighbours (blurs) or the whole frame (dehaze airlight).
SPATIAL_OPS = frozenset({"dehaze", "clarity", "sharpen", "black_point", "white_point", "denoise", "grain",
                         "hot_pixels", "sky_gradient", "denoise_stars", "sharpen_stars", "highlight_detail"})

# The frozen order as data: one tuple per step of apply_ops, in order. A settings bundle may be
# cut between two steps and applied in two calls with the same result (apply.py relies on it).
# The first step (F31: stuck pixels and the sky's gradient, made by the pipeline to the decoded photo)
# and the last two steps are values in the bundle that apply_ops does not render: the pipeline lays the
# LUT over the finished picture, after the gates, and emit.py adds the grain to the picture it
# writes, after the resize.
STEPS = (
    ("hot_pixels", "sky_gradient"),
    ("temp", "tint", "exposure", "highlight_rolloff", "highlight_detail"),
    ("shadows_ev", "burn_ev"),
    ("highlights", "shadows", "whites", "blacks"),
    ("contrast",),
    ("curve",),
    ("dehaze",),
    ("clarity",),
    ("black_point", "white_point"),
    ("vibrance", "saturation"),
    _HSL_KEYS,
    _GRADE_KEYS,
    BW_KEYS,
    ("denoise", "denoise_stars"),
    ("sharpen", "sharpen_stars"),
    ("lut",),
    ("grain",),
)
_TONE_KEYS = frozenset(k for step in STEPS[1:6] for k in step)
_COLOUR_KEYS = frozenset(k for step in STEPS if {"vibrance", _HSL_KEYS[0], _GRADE_KEYS[0], BW_KEYS[0]} & set(step) for k in step)

_DEHAZE_PROXY_EDGE = 1400


def _pixsat(rgb: np.ndarray) -> np.ndarray:
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    out = np.zeros_like(mx)
    nz = mx > _EPS
    out[nz] = (mx[nz] - mn[nz]) / mx[nz]
    return out


def _resize_rgb(rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    # float per-channel resize (no uint8 round-trip) — the dehaze proxy feeds airlight /
    # transmission estimation, where 8-bit quantization would coarsen the haze model
    chans = [
        np.asarray(
            PILImage.fromarray(np.ascontiguousarray(rgb[..., c]).astype(np.float32), mode="F")
            .resize(size, PILImage.BILINEAR),
            dtype=np.float32)
        for c in range(3)
    ]
    return np.stack(chans, axis=-1)


def _resize_float(a: np.ndarray, size: tuple[int, int], resample=PILImage.BILINEAR) -> np.ndarray:
    pil = PILImage.fromarray(a.astype(np.float32), mode="F")
    return np.asarray(pil.resize(size, resample)).astype(np.float32)


# Linear value of the clip threshold the stats/gates use (luma 0.98 in gamma). The knee's
# asymptote sits HERE, not at 1.0, so a kneed highlight never counts as clipped.
_WHITE_LIN = float(srgb_to_linear(np.float32(0.98)))


# Highlight detail (F50). Constants of the op, like the levels and grain constants.
_DETAIL_SIGMA = 0.01    # the base: the bright pixels within about this share of the long edge (Gaussian sigma)
_DETAIL_FROM = 0.70     # gamma luma where a pixel starts to count as bright ...
_DETAIL_FULL = 0.85     # ... and counts fully
_DETAIL_RATIO = 1.1     # largest base/pixel ratio (linear) a pixel is held by; beyond it, it is structure
_DETAIL_TOP = float(srgb_to_linear(np.float32(0.975)))   # kept detail ends under 0.975: room for the sharpening


def detail_ratio(rgb: np.ndarray) -> np.ndarray:
    """F50: per pixel, base / pixel in linear light; the base is the mean luma of the BRIGHT pixels around
    it, so a sky beside a dark ridge takes its base from the sky. Clipped to [1/_DETAIL_RATIO,
    _DETAIL_RATIO], faded to 1 on pixels that are not bright. Reads the whole frame: once, before the strips."""
    y = luma(rgb).astype(np.float32)
    m = np.clip((y - _DETAIL_FROM) / (_DETAIL_FULL - _DETAIL_FROM), 0.0, 1.0)
    sigma = _DETAIL_SIGMA * max(y.shape)
    base = gaussian_blur(y * m, sigma) / np.maximum(gaussian_blur(m, sigma), 1e-4)
    r = srgb_to_linear(base) / np.maximum(srgb_to_linear(y), 1e-6)
    return np.exp(m * np.log(np.clip(r, 1.0 / _DETAIL_RATIO, _DETAIL_RATIO))).astype(np.float32)


def _shoulder(v, a, w):
    """v up to a; above it a + (w - a)(1 - exp(-(v - a) / (w - a))): slope 1 at a, under w (a < w)."""
    d = w - a
    return np.where(v > a, a + d * (1.0 - np.exp(-np.maximum(v - a, 0.0) / d)), v)


def highlight_rolloff(lin: np.ndarray, amount: float, ratio: np.ndarray | None = None,
                      detail: float = 0.0) -> np.ndarray:
    """Soft highlight knee in LINEAR light — what a camera's tone curve does that a bare exposure
    multiply cannot. With W = the clip level (0.98 gamma) and knee point kp = W·(1 − amount/200)
    (amount 40 → kp ≈ 0.76 linear ≈ 0.88 gamma), values above kp follow
    kp + (W−kp)·(1 − exp(−(x−kp)/(W−kp))): continuous with slope 1 at kp, monotonic, asymptote W,
    so anything an exposure lift pushes past white compresses into [kp, W) instead of clipping.
    Pixels below kp are untouched; amount 0 is the identity.

    F50: with `ratio` (from `detail_ratio`, base / pixel per pixel) and `detail` > 0 (percent), a bright
    pixel is held by the level of the bright area around it, not by its own: at or under its base it
    takes the base's gain, so texture on it keeps its ratio to its surroundings; above its base the
    same gain under a soft cap that ends at 0.975. A smooth area (ratio 1) is held exactly as before."""
    if amount <= 0.0:
        return lin
    kp = _WHITE_LIN * (1.0 - 0.5 * min(amount, 100.0) / 100.0)
    s = min(max(float(detail), 0.0), 100.0) / 100.0 if ratio is not None else 0.0
    over = lin > kp
    if not over.any() and s == 0.0:
        return lin
    out = lin.copy()
    x = (lin[over] - kp) / (_WHITE_LIN - kp)
    out[over] = kp + (_WHITE_LIN - kp) * (1.0 - np.exp(-x))
    if s == 0.0:
        return out
    r = np.broadcast_to(ratio[..., None], lin.shape)
    b = lin * r                                    # each channel at its base level
    act = np.maximum(lin, b) > kp                  # elsewhere the plain knee's values stay, bit for bit
    r, b, o = r[act], b[act], out[act]
    kb = _shoulder(b, kp, _WHITE_LIN)              # the knee at the base
    top = np.float32(_DETAIL_TOP)
    cap = _shoulder(kb / r, np.minimum(kb, top * np.float32(0.9999)), top)
    local = np.where(r >= 1.0, kb / r, np.maximum(o, cap))   # at/below the base: its gain; above: capped
    out[act] = o + np.float32(s) * (local - o)
    return out


def light(rgb: np.ndarray, temp: float = 0.0, tint: float = 0.0, ev: float = 0.0,
          rolloff: float = 0.0, ratio: np.ndarray | None = None, detail: float = 0.0) -> np.ndarray:
    """White balance + exposure + highlight knee in ONE linear pass (one decode, one encode).
    The separate `white_balance`/`exposure` ops are kept for tests and callers; apply_ops uses
    this because each gamma round-trip on a 22 MP frame costs ~0.5 s. No intermediate clip
    between WB and exposure — a channel WB pushes past white is still lifted/kneed as light."""
    lin = srgb_to_linear(rgb)
    if temp or tint:
        lin = lin.copy()
        lin[..., 0] *= 1.0 + 0.2 * (temp / 100.0)
        lin[..., 1] *= 1.0 + 0.1 * (tint / 100.0)
        lin[..., 2] *= 1.0 - 0.2 * (temp / 100.0)
    if ev:
        lin = lin * (2.0 ** ev)
    return linear_to_srgb(highlight_rolloff(lin, rolloff, ratio, detail))


def exposure(rgb: np.ndarray, ev: float, rolloff: float = 0.0) -> np.ndarray:
    """Exposure in stops (linear multiply); `rolloff` > 0 adds the highlight knee BEFORE the
    values are re-encoded, which is the only point where a lift can be compressed rather than
    clipped (`linear_to_srgb` clips to [0, 1])."""
    return light(rgb, ev=ev, rolloff=rolloff)


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


def shadow_lift(rgb: np.ndarray, ev: float) -> np.ndarray:
    """Open the shadows by up to `ev` stops with BLACK PINNED: a luminance gain 2**(ev·w) with
    w = (1 − l/0.5)², 1 at black fading smoothly to 0 at mid-grey. The additive `shadows` slider
    in `tone` adds its whole lift to pure black, so a strong setting raises the black floor into a
    grey veil and multiplies near-black pixels without limit (speckle). A gain keeps 0 at 0, keeps
    local contrast in the deep shadows, is bounded by 2**ev, and leaves everything at or above
    mid-grey untouched. Monotonic for |ev| < 2.88 (OP_BOUNDS keeps it inside); 0 is the identity."""
    if ev == 0.0:
        return rgb
    w = np.clip(1.0 - luma(rgb) / 0.5, 0.0, 1.0)
    return rgb * np.exp2(ev * w * w)[..., None].astype(np.float32)


def burn(rgb: np.ndarray, ev: float) -> np.ndarray:
    """Reduce mid and bright luma with a smooth log ramp, leaving deep blacks and hue intact."""
    if not ev:
        return rgb
    l = luma(rgb)
    u = np.clip(np.log(np.maximum(l, 0.03) / 0.03) / np.log(0.50 / 0.03), 0.0, 1.0)
    return rgb * np.exp2(-min(2.0, max(0.0, ev)) * u * u * (3 - 2 * u))[..., None].astype(np.float32)


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


def sharpen(rgb: np.ndarray, amt: float, stars: float = 0.0) -> np.ndarray:
    """Unsharp mask on luminance — fine-edge sharpening (vs clarity's broad local contrast).
    A small-radius high-pass added back to luma; color is preserved via _scale_luma.
    `stars` 0..100 (F31) spares the stars of the input by that share of their weight; 0 is the op as before, bit for bit."""
    l = luma(rgb)
    sigma = 0.0007 * max(rgb.shape[0], rgb.shape[1]) + 0.8  # small radius → edges, not local contrast
    detail = l - gaussian_blur(l, sigma)
    if stars > 0.0:
        detail = detail * (1.0 - star_weight(l) * np.float32(min(float(stars), 100.0) / 100.0))
    return _scale_luma(rgb, l + (amt / 100.0) * 1.6 * detail, l)


_DENOISE_FULL_GRAIN = 1.0 / 255.0   # measured grain from which the chroma filter works at full weight


def _grain_sigma(l: np.ndarray) -> float:
    """Robust estimate of the fine grain of a luma plane: 1.4826 times the median absolute
    deviation of (luma - a 1 px blur), on every second pixel."""
    s = l[::2, ::2] if min(l.shape) >= 64 else l
    d = s - gaussian_blur(s, 1.0)
    return float(1.4826 * np.median(np.abs(d - np.median(d))))


def denoise(rgb: np.ndarray, amt: float, stars: float = 0.0) -> np.ndarray:
    """Noise reduction, chroma first (F7). `amt` 0..100. `stars` 0..100 (F31) spares the stars of the
    input by that share of their weight; 0 is the op as before, bit for bit.

    Colour noise is coarse and ugly, luma grain is fine and tolerable, so the two get different
    treatment. Chroma (rgb - luma) is filtered at half resolution with the guided filter, guided
    by a lightly blurred luma: colour stays inside real edges, and grain in the guide is not read
    as an edge. Luma is filtered with the self-guided filter whose threshold follows the grain
    measured on the input (`_grain_sigma`), at reduced weight from the mid-tones up. Both
    filters follow that measurement: a clean frame measures a tiny grain and is left almost
    alone, fine colour detail included. 0 is the identity."""
    a = min(max(amt, 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    l = luma(rgb)
    h, w = l.shape
    sigma = _grain_sigma(l)
    small = (max(1, w // 2), max(1, h // 2))
    guide = gaussian_blur(_resize_float(l, small), 1.0)
    radius = max(2, int(round(0.0016 * max(h, w))))
    # The chroma filter is full from a grain of one 8-bit level and fades out below it. Without
    # this a clean frame lost its fine colour detail (measured: 3 px colour stripes of equal
    # brightness lost 31 % of their chroma at strength 40).
    weight_c = min(1.0, 1.6 * a) * min(1.0, sigma / _DENOISE_FULL_GRAIN)
    out = np.empty(rgb.shape, dtype=np.float32)
    for i in range(3):
        c = rgb[..., i] - l
        smooth = _resize_float(guided_filter(guide, _resize_float(c, small), radius, eps=0.05 * 0.05), (w, h))
        out[..., i] = c + weight_c * (smooth - c)
    l_smooth = guided_filter(l, l, 2, eps=max((2.0 * sigma) ** 2, 1e-7))
    weight_l = a * (1.0 - 0.6 * np.clip((l - 0.30) / 0.40, 0.0, 1.0))
    out += (l + weight_l * (l_smooth - l))[..., None]
    if stars > 0.0:
        out += (star_weight(l) * np.float32(min(float(stars), 100.0) / 100.0))[..., None] * (rgb - out)
    return out


# Night sky (F31). Constants of the ops, like the levels and grain constants. Sizes follow the
# frame's long edge, so a preview and an export find the same stars.
_STAR_EDGE = 1.0 / 1000.0   # a star is narrower than a square of 2r + 1 px, r this share of the long edge
_STAR_K = 5.0               # a star stands this many noise sigmas above its local background ...
_STAR_MIN = 3.0 / 255.0     # ... and at least this much
_HOT_K = 6.0                # a stuck pixel stands this many noise sigmas above all 8 neighbours ...
_HOT_MIN = 24.0 / 255.0     # ... and at least this much, in one channel
_HOT_SUPPORT = 0.25         # its 4 nearest neighbours rise less than this share of its height over the ring around it
_HOT_COLOUR = 0.5           # and its weakest channel stands less than this share of that: a white point is a star
_NB8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_NB4 = ((-1, 0), (0, -1), (0, 1), (1, 0))
_RING = tuple((dy, dx) for dy in range(-2, 3) for dx in range(-2, 3) if max(abs(dy), abs(dx)) == 2)


def _box_max(a, radius):
    return -box_min(-a, radius)


def _robust_sigma(d):
    s = d[::2, ::2] if min(d.shape[:2]) >= 64 else d
    return float(1.4826 * np.median(np.abs(s - np.median(s))))


def star_weight(l: np.ndarray) -> np.ndarray:
    """Where the stars are (F31): 0..1 per pixel of a luma plane, 1 on a core. Background: an opening
    with a square of 2r + 1 px, which removes everything narrower. A star stands above it by more than
    _STAR_K robust sigmas and _STAR_MIN; 1 at twice that, grown by 1 px. The Milky Way, snow: 0."""
    h, w = l.shape[:2]
    r = max(1, int(round(_STAR_EDGE * max(h, w))))
    s = box_blur(l, 1)
    d = (s - box_blur(_box_max(box_min(s, r), r), r)).astype(np.float32)
    med = float(np.median(d[::2, ::2] if min(h, w) >= 64 else d))
    t = max(_STAR_K * _robust_sigma(d), _STAR_MIN)
    return _box_max(np.clip((d - med - t) / t, 0.0, 1.0).astype(np.float32), 1)


def hot_pixels(rgb: np.ndarray, amt: float) -> np.ndarray:
    """Stuck pixels (F31): a pixel above all 8 neighbours in channel c by _HOT_MIN and _HOT_K sigmas,
    alone (_HOT_SUPPORT) and coloured (_HOT_COLOUR), moves toward the median of its 8 neighbours in c
    by amt/100; 0 is the identity. Only the candidates are looked at closely."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    h, w = rgb.shape[:2]
    if a == 0.0 or h < 5 or w < 5:
        return rgb
    p = np.pad(rgb, ((2, 2), (2, 2), (0, 0)), mode="edge")
    hi = None
    for dy, dx in _NB8:
        v = p[2 + dy:2 + dy + h, 2 + dx:2 + dx + w]
        hi = v.copy() if hi is None else np.maximum(hi, v, out=hi)
    sig = np.asarray([_robust_sigma(rgb[..., c] - box_blur(rgb[..., c], 1)) for c in range(3)], np.float32)
    ys, xs, cs = np.nonzero((rgb - hi) > np.maximum(_HOT_MIN, _HOT_K * sig))
    if ys.size == 0:
        return rgb
    py, px, val = ys + 2, xs + 2, rgb[ys, xs, cs]
    ring = np.median(np.stack([p[py + dy, px + dx, cs] for dy, dx in _RING]), axis=0)
    near = np.mean(np.stack([p[py + dy, px + dx, cs] for dy, dx in _NB4]), axis=0)
    e = rgb[ys, xs] - hi[ys, xs]
    alone = ((near - ring) < _HOT_SUPPORT * (val - ring)) & (e.min(axis=1) < _HOT_COLOUR * e[np.arange(ys.size), cs])
    ys, xs, cs, py, px, val = ys[alone], xs[alone], cs[alone], py[alone], px[alone], val[alone]
    if ys.size == 0:
        return rgb
    med = np.median(np.stack([p[py + dy, px + dx, cs] for dy, dx in _NB8]), axis=0)
    out = rgb.copy()
    out[ys, xs, cs] = val + np.float32(a) * (med - val)
    return out


def sky_gradient(rgb: np.ndarray, amt: float, offset: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """The sky's gradient taken off (F31): rgb - amt/100 * alpha * offset in 0..1; offset (HxWx3) is the
    smooth fitted background minus its level (night.py), alpha the feathered sky. 0 is the identity."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    return par.rows(lambda x, o, m: np.clip(x - o * (np.float32(a) * m)[..., None], 0.0, 1.0).astype(np.float32),
                    rgb, offset, alpha)


def vibrance(rgb: np.ndarray, vib: float, sat: float) -> np.ndarray:
    l = luma(rgb)[..., None]
    chroma = rgb - l
    s = 1.0 + sat / 100.0
    psat = _pixsat(rgb)[..., None]
    v = 1.0 + (vib / 100.0) * (1.0 - psat)  # boost dull colors more (vibrance)
    return l + chroma * (s * v)


def dehaze(rgb: np.ndarray, amt: float) -> np.ndarray:
    """Physical dehaze via the dark-channel prior (He et al. 2009).

    Haze is the atmospheric-scattering model I = J·t + A·(1−t): the captured pixel I is the
    true scene radiance J attenuated by transmission t, plus airlight A. We estimate A from the
    haziest pixels, recover per-pixel t from the dark channel, refine t with the guided filter
    (so it follows real edges, no halos), then invert the model to recover J. `amt` (0..100)
    blends original→dehazed, so small rule values stay gentle and the look's dehaze_mul scales up.

    Negative amt adds a light atmospheric veil (the inverse) rather than removing haze."""
    a = amt / 100.0
    if a == 0.0:
        return rgb
    if a < 0.0:                                   # add haze: lift blacks toward a soft veil
        return rgb * (1.0 + a * 0.5) + (-a * 0.5) * 0.85

    h, w = rgb.shape[0], rgb.shape[1]
    long_edge = max(h, w)
    if long_edge > _DEHAZE_PROXY_EDGE:
        scale = _DEHAZE_PROXY_EDGE / long_edge
        work_w = max(1, round(w * scale))
        work_h = max(1, round(h * scale))
        work = _resize_rgb(rgb, (work_w, work_h))
    else:
        work = rgb
        work_h, work_w = h, w

    patch = max(1, int(round(0.006 * max(work_h, work_w))))  # ~dark-channel patch radius
    omega = 0.92                                    # how much haze the *model* removes (fixed); amt blends
    t0 = 0.12                                       # transmission floor — guards the divide in dense haze

    dark = box_min(work.min(axis=2), patch)         # dark channel of the input
    # atmospheric light A: mean colour of the brightest ~0.1% of dark-channel pixels (≥50 px)
    flat = dark.reshape(-1)
    k = max(50, int(flat.size * 0.001))
    idx = np.argpartition(flat, -k)[-k:]
    pix = work.reshape(-1, 3)[idx]
    A = np.clip(pix.mean(axis=0), 0.5, 1.0).astype(np.float32)  # bounded so dark scenes don't blow up

    # transmission from the normalised dark channel, refined edge-aware against luma
    t = 1.0 - omega * box_min((work / A).min(axis=2), patch)
    t = guided_filter(luma(work), t.astype(np.float32), max(patch * 4, 8), eps=1e-3)
    t = np.clip(t, t0, 1.0)

    # Smooth, bright, low-saturation areas (foggy skies, mist, glassy water highlights) often
    # have very few real source levels. A full dark-channel dehaze stretches those levels into
    # visible contour lines, so taper the blend only in those fragile regions while leaving
    # textured mountains/trees and darker water at full strength.
    lum = luma(work)
    sigma = 0.006 * max(work_h, work_w) + 1.0
    detail = np.abs(lum - gaussian_blur(lum, sigma))
    smooth = np.clip((0.018 - detail) / 0.018, 0.0, 1.0)
    bright = np.clip((lum - 0.50) / 0.30, 0.0, 1.0)
    neutral = np.clip((0.22 - _pixsat(work)) / 0.22, 0.0, 1.0)
    fragile = smooth * bright * neutral
    blend = a * (1.0 - 0.94 * fragile)

    if work is not rgb:
        t = _resize_float(t, (w, h))
        blend = _resize_float(blend, (w, h))

    def invert(x, t_, blend_):                      # per pixel: runs on row strips, same values
        t3 = np.clip(t_, t0, 1.0)[..., None]
        recovered = (x - A) / t3 + A                # invert the scattering model
        out = x + blend_[..., None] * (np.clip(recovered, 0.0, 1.0) - x)
        return out.astype(np.float32)

    return par.rows(invert, rgb, t, blend)


def curve(rgb: np.ndarray, strength: float) -> np.ndarray:
    """Filmic S-curve on luminance — smoother contrast that rolls off the ends."""
    l = luma(rgb)
    s = strength / 100.0
    smooth = l * l * l * (l * (l * 6.0 - 15.0) + 10.0)  # smootherstep(l)
    return _scale_luma(rgb, l + s * (smooth - l), l)


# Black and white point (F5). The targets sit inside the clip levels the stats and the gates use
# (a channel below 0.02 or above 0.98 counts as clipped), so the op cannot add clipping.
_BLACK_FLOOR = 0.03
_WHITE_CEIL = 0.94
_LEVELS_PIVOT = 0.5      # the curve is the identity on the far side of mid-grey
_LEVELS_TAIL = 0.1       # percent of the pixels allowed beyond a measured point
_LEVELS_BODY = 10.0      # percent: only the darkest (brightest) tenth of the frame is reshaped
_LEVELS_SLOPE = 0.3      # caps the amount so the slope at a measured point stays <= 1.6
_CLIP_LO, _CLIP_HI = 0.0205, 0.9795
_STAT_LO, _STAT_HI = 0.02, 0.98     # what stats.py counts as clipped


def _levels_points(l: np.ndarray) -> tuple[float, float, float, float]:
    """Measured points of a luma plane: black and white (the 0.1th and 99.9th percentile) and
    where the body of the picture begins and ends (the 10th and 90th), on every fourth pixel in
    both directions (1.4 M samples of a 22 MP frame)."""
    s = l[::4, ::4] if l.ndim == 2 and min(l.shape) >= 256 else l
    lo, hi, body_lo, body_hi = np.percentile(
        s, (_LEVELS_TAIL, 100.0 - _LEVELS_TAIL, _LEVELS_BODY, 100.0 - _LEVELS_BODY))
    return float(lo), float(hi), float(body_lo), float(body_hi)


def _levels_curve(l: np.ndarray, f: float, b: float, w: float, a: float,
                  p: float = _LEVELS_PIVOT, q: float = _LEVELS_PIVOT) -> np.ndarray:
    """The luma map. Black side: remove `b` at the floor `f`, fading to nothing at the pivot `p`,
    with a power toe below `f` that keeps 0 at 0. White side: add `a` at the white `w`, the mirror
    image with its pivot `q` measured down from white. Monotonic, continuous slope, the identity
    when b == a == 0."""
    y = l
    if b > 0.0:
        yf, m = f - b, 1.0 + 2.0 * b / (p - f)
        mid = l - b * np.square(np.clip((p - l) / (p - f), 0.0, None))
        toe = yf * np.power(np.clip(l / f, 0.0, 1.0), m * f / yf)
        y = np.where(l < f, toe, np.where(l < p, mid, l))
    if a > 0.0:
        u, uf = 1.0 - l, 1.0 - w
        vf, m = uf - a, 1.0 + 2.0 * a / (q - uf)
        mid = u - a * np.square(np.clip((q - u) / (q - uf), 0.0, None))
        toe = vf * np.power(np.clip(u / uf, 0.0, 1.0), m * uf / vf)
        y = np.where(u < uf, 1.0 - toe, np.where(u < q, 1.0 - mid, y))
    return y.astype(np.float32)


def _levels_apply(rgb: np.ndarray, f: float, b: float, w: float, a: float,
                  p: float = _LEVELS_PIVOT, q: float = _LEVELS_PIVOT) -> np.ndarray:
    l = luma(rgb)
    ratio = (_levels_curve(l, f, b, w, a, p, q) + _EPS) / (l + _EPS)
    lo, hi = rgb.min(axis=-1), rgb.max(axis=-1)
    # A channel that is inside the levels the statistics count as clipped (0.02 and 0.98) stays
    # inside, and so does the luma, which is what those statistics measure: a dark pixel with one
    # channel already at 0 has no channel to hold it (measured: 0.06 % of a frame crossed).
    # A lower bound is at most 1 and an upper bound at least 1, so they cannot contradict.
    for v in (lo, l):
        ratio = np.where(v >= _STAT_LO, np.maximum(ratio, np.minimum(1.0, _CLIP_LO / np.maximum(v, _EPS))), ratio)
    for v in (hi, l):
        ratio = np.where(v <= _STAT_HI, np.minimum(ratio, np.maximum(1.0, _CLIP_HI / np.maximum(v, _EPS))), ratio)
    return (rgb * ratio[..., None]).astype(np.float32)


def levels(rgb: np.ndarray, black: float = 0.0, white: float = 0.0) -> np.ndarray:
    """Black and white point, each a strength 0..100. The op measures where the darkest and the
    brightest 0.1 % of ITS INPUT begin and pulls those points toward a true black (0.03) and a
    clean white (0.94). A frame that already has them is not touched; 0 and 0 is the identity.

    Only the darkest and the brightest tenth of the frame are reshaped: the curve is the identity
    between the frame's own 10th and 90th percentile. So the median of any area that covers a
    quarter of the frame or more does not move at all, which is what keeps a dark photo from
    coming out darker (measured: with the pivot at mid-grey the bottom third of dark photos lost
    up to 0.03). A frame whose darkest tenth is flat (fog) has nothing to anchor and is left
    alone.

    The measurement reads the whole frame, so this op never runs on a strip or a crop; the
    per-pixel map does."""
    if black <= 0.0 and white <= 0.0:
        return rgb
    f, w, body_lo, body_hi = _levels_points(luma(rgb))
    p, q = min(_LEVELS_PIVOT, body_lo), min(_LEVELS_PIVOT, 1.0 - body_hi)
    b = max(0.0, min(min(black, 100.0) / 100.0 * (f - _BLACK_FLOOR), _LEVELS_SLOPE * (p - f))) if black > 0.0 else 0.0
    a = max(0.0, min(min(white, 100.0) / 100.0 * (_WHITE_CEIL - w), _LEVELS_SLOPE * (q - (1.0 - w)))) if white > 0.0 else 0.0
    if b <= 0.0 and a <= 0.0:
        return rgb
    return par.rows(lambda x: _levels_apply(x, f, b, w, a, p, q), rgb)


# Grain (F23). Constants of the op, like the levels constants above.
_GRAIN_SIGMA = 0.05          # luma grain at amount 100 at mid grey: 12.75 of 255
_GRAIN_CELL = 1.0 / 2000.0   # size 1: one grain clump is this share of the frame's long edge
_GRAIN_CUT = 3.0             # the pattern is cut at this many standard deviations


def grain(rgb: np.ndarray, amt: float, size: float = 1.0, seed: int = 0) -> np.ndarray:
    """Film grain (F23): a seeded luma pattern added to the frame. `amt` 0..100.

    One value per pixel is added to R, G and B alike, so no colour is added. The pattern is white
    noise drawn on a grid of grain cells and enlarged to the frame, so a clump is `size`
    two-thousandths of the long edge wide on any frame: 3 px on a 6000 px export, 1 px on a
    2048 px one. A cell finer than a pixel cannot be drawn; the grain is then weakened in
    proportion, which is what shrinking a full-size picture does to its grain. The weight is 1 at
    mid grey and 0 at the levels the statistics count as clipped, and the pattern is cut at 3
    sigma, so the luma of a pixel never crosses one of those levels. The same frame size, size
    and seed give the same pattern (`RandomState` is a frozen stream). 0 is the identity.

    Not called by `apply_ops`: the pattern belongs to one frame size, so it is added to the
    picture that is written or shown, after its resize (emit.py, web/service.py)."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    h, w = rgb.shape[:2]
    cell = float(size) * max(h, w) * _GRAIN_CELL            # one clump, in pixels of this frame
    gh, gw = (max(1, round(n / max(cell, 1.0))) for n in (h, w))
    n = np.random.RandomState(int(seed) % 2**32).standard_normal((gh, gw)).astype(np.float32)
    if (gh, gw) != (h, w):
        n = _resize_float(n, (w, h), PILImage.BICUBIC)
    n = n - n.mean()
    n = np.clip(n / max(float(n.std()), _EPS), -_GRAIN_CUT, _GRAIN_CUT)
    t = np.clip((luma(rgb) - _CLIP_LO) / (_CLIP_HI - _CLIP_LO), 0.0, 1.0)
    delta = (a * _GRAIN_SIGMA * min(1.0, cell)) * (4.0 * t * (1.0 - t)) * n
    return np.clip(rgb + delta[..., None], 0.0, 1.0).astype(np.float32)


def lut_map(rgb: np.ndarray, shift: np.ndarray, amt: float,
            lo=(0.0, 0.0, 0.0), hi=(1.0, 1.0, 1.0)) -> np.ndarray:
    """A 3D LUT (F25): every colour is moved as the table says, by `amt` of 100.

    `shift` is the table as offsets (`lut.Lut.shift`: each entry minus the colour of its own grid
    point), `(n, n, n, 3)` indexed [blue, green, red]; `lo` and `hi` are the file's input range.
    The values are the working values of this module, sRGB-encoded, as LUT files for display
    pictures expect. The result is the input plus `amt` of the offset found for it, so a table
    that changes nothing gives the picture back bit for bit, and a value outside the range is
    looked up at its edge and keeps its distance from it. The offset is interpolated
    tetrahedrally: the cell around a colour is cut into six tetrahedra along its grey diagonal
    and the offset is mixed from the four corners of the one the colour lies in (the cell's
    origin, the far corner, and the two reached by stepping along the axis of the largest
    fraction and then the middle one). A grey ramp stays grey under a table that keeps greys
    grey, which trilinear interpolation does not do, and it reads four entries per pixel, not
    eight. Per pixel, so it runs on row strips; 0 is the identity.

    Not called by `apply_ops`: a LUT is laid over the finished picture once, after the gates
    (pipeline.py)."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    n = shift.shape[0]
    flat = shift.reshape(-1, 3)
    lo = np.asarray(lo, dtype=np.float32)
    hi = np.asarray(hi, dtype=np.float32)
    x = (np.clip(rgb, lo, hi) - lo) * (np.float32(n - 1) / (hi - lo))
    cell = np.clip(x.astype(np.int32), 0, n - 2)              # the last cell ends at n - 1
    f = x - cell.astype(np.float32)                           # position inside the cell, 0..1
    one, row, plane = np.int32(1), np.int32(n), np.int32(n * n)   # table steps along red, green, blue
    base = cell[..., 0] + row * cell[..., 1] + plane * cell[..., 2]
    del x, cell
    fr, fg, fb = f[..., 0], f[..., 1], f[..., 2]
    big = np.maximum(np.maximum(fr, fg), fb)
    small = np.minimum(np.minimum(fr, fg), fb)
    mid = np.maximum(np.minimum(fr, fg), np.minimum(np.maximum(fr, fg), fb))   # the median, exact
    step_big = np.where(fr >= big, one, np.where(fg >= big, row, plane))
    step_small = np.where(fb <= small, plane, np.where(fg <= small, row, one))
    far = one + row + plane
    d = np.take(flat, base, axis=0) * (np.float32(1.0) - big)[..., None]
    d += np.take(flat, base + step_big, axis=0) * (big - mid)[..., None]
    d += np.take(flat, base + (far - step_small), axis=0) * (mid - small)[..., None]
    d += np.take(flat, base + far, axis=0) * small[..., None]
    if a < 1.0:
        d *= np.float32(a)
    return (rgb + d).astype(np.float32, copy=False)


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


# Black and white (F24). Rec.709 luma of the six pure colours, in the order of the weights in
# BW_KEYS: red, yellow, green, cyan, blue, magenta. A weight of 0 leaves a colour at this value.
_BW_LUMA = (0.2126, 0.9278, 0.7152, 0.7874, 0.0722, 0.2848)
_BW_DARK = 0.30   # the gates' dark level: a weight never darkens a pixel at or below it, nor takes one below it
_BW_FULL = 2.0    # a weight darkens by this times the share of the luma above _BW_DARK: fully from 0.60 up


def _bw_coeff(weight: float, own: float) -> float:
    """How bright a pure colour comes out in grey: its own luma at 0, white at +100, black at -100."""
    w = min(max(float(weight), -100.0), 100.0) / 100.0
    return own + w * (1.0 - own) if w >= 0.0 else own * (1.0 + w)


def bw(rgb: np.ndarray, amt: float, red: float = 0.0, yellow: float = 0.0, green: float = 0.0,
       cyan: float = 0.0, blue: float = 0.0, magenta: float = 0.0) -> np.ndarray:
    """Black and white (F24) with a weight per colour. `amt` 0..100, each weight -100..100.

    Bands, no hue angle: a pixel is exactly `lo * white + (mid - lo) * secondary + (hi - mid) *
    primary` (smallest channel as white, what the two larger channels share as yellow, cyan or
    magenta, what the largest has alone as red, green or blue). The grey value is the white part
    plus each coloured part times how bright that colour comes out.
    Weights: 0 leaves a colour at its Rec.709 luma, +100 makes it as bright as white, -100 as
    dark as black, linear between. A weight acts on a coloured part, so grey input is unchanged
    bit for bit with any weights; all weights 0 gives the luma of `imageio.luma`.
    Band edges: the part that changes band is zero there, so the grey value is continuous.
    Dark protection: what a weight takes away is scaled by twice the share of the pixel's luma
    above 0.30 (at most 1), so nothing at 0.30 or below is darkened and no pixel goes below 0.30.
    Clip guard: the grey value lies between the smallest and largest channel; a channel at 0.02
    or more is not pushed under it, and 0.98 is passed only where the luma already was.
    Order: the last colour step, after the split-tone. 0 is the identity; 100 gives a neutral
    picture, between is a mix with the colour original."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    own = np.asarray(_BW_LUMA, np.float32)
    add = np.asarray([_bw_coeff(w, o) for w, o in zip((red, yellow, green, cyan, blue, magenta), _BW_LUMA)],
                     np.float32) - own                                # what each weight adds to its colour
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lo, hi = rgb.min(axis=-1), rgb.max(axis=-1)
    mid = np.maximum(np.minimum(r, g), np.minimum(np.maximum(r, g), b))   # the median, exact
    p = np.where(hi == r, 0, np.where(hi == g, 2, 4))    # the primary: red, green or blue
    s = np.where(lo == b, 1, np.where(lo == r, 3, 5))    # the secondary: yellow, cyan or magenta
    l = lo + (mid - lo) * own[s] + (hi - mid) * own[p]   # the pixel's luma
    e = (mid - lo) * add[s] + (hi - mid) * add[p]        # what the weights add to it
    e = np.where(e < 0.0, e * np.minimum(1.0, _BW_FULL * np.maximum(l - _BW_DARK, 0.0) / np.maximum(l, _EPS)), e)
    # Lower bound: the dark level (or the pixel's own luma below it), and 0.02 for a pixel that
    # has a channel there. Upper bound: 0.98, or the pixel's own luma above it. Each bound is the
    # pixel's own value where that is nearer, so it is continuous and a grey pixel is never moved.
    y = np.clip(l + e, np.maximum(np.minimum(l, _BW_DARK), np.minimum(hi, _CLIP_LO)),
                np.maximum(l, _CLIP_HI)).astype(np.float32)
    if a == 1.0:
        return np.repeat(y[..., None], 3, axis=-1)
    return (rgb + np.float32(a) * (y[..., None] - rgb)).astype(np.float32)


def _tone_steps(out: np.ndarray, settings: dict, ratio: np.ndarray | None = None) -> np.ndarray:
    """The per-pixel steps before dehaze: light, shadow lift, tone, contrast, curve. `ratio` (F50) is the
    knee's base / pixel map of the whole frame, for `highlight_detail`."""
    temp, tint = settings.get("temp", 0.0), settings.get("tint", 0.0)
    ev, rolloff = settings.get("exposure", 0.0), settings.get("highlight_rolloff", 0.0)
    if temp or tint or ev or rolloff:  # WB → exposure → knee, one linear pass (frozen order kept)
        out = light(out, temp, tint, ev, rolloff, ratio, settings.get("highlight_detail", 0.0))
    if settings.get("shadows_ev", 0.0):  # black-pinned lift, before the additive tone sliders
        out = shadow_lift(out, settings["shadows_ev"])
    if settings.get("burn_ev", 0.0):
        out = burn(out, settings["burn_ev"])
    if any(settings.get(k, 0.0) for k in ("highlights", "shadows", "whites", "blacks")):
        out = tone(out, settings.get("highlights", 0.0), settings.get("shadows", 0.0),
                   settings.get("whites", 0.0), settings.get("blacks", 0.0))
    if settings.get("contrast", 0.0):
        out = contrast(out, settings["contrast"])
    if settings.get("curve", 0.0):
        out = curve(out, settings["curve"])
    return out


def _colour_steps(out: np.ndarray, settings: dict) -> np.ndarray:
    """The per-pixel steps after clarity: vibrance and saturation, targeted HSL, split-tone, black and white."""
    if settings.get("vibrance", 0.0) or settings.get("saturation", 0.0):
        out = vibrance(out, settings.get("vibrance", 0.0), settings.get("saturation", 0.0))
    if any(settings.get(k, 0.0) for k in _HSL_KEYS):
        out = targeted_hsl(out, settings.get("hsl_skin_sat", 0.0), settings.get("hsl_green_sat", 0.0),
                           settings.get("hsl_green_lum", 0.0), settings.get("hsl_blue_sat", 0.0))
    if any(settings.get(k, 0.0) for k in _GRADE_KEYS):
        out = color_grade(out, settings.get("grade_shadows_temp", 0.0), settings.get("grade_shadows_tint", 0.0),
                          settings.get("grade_highlights_temp", 0.0), settings.get("grade_highlights_tint", 0.0))
    if settings.get("bw", 0.0):  # the last colour step: nothing in a bundle adds colour after it
        out = bw(out, settings["bw"], *(settings.get(k, 0.0) for k in BW_KEYS[1:]))
    return out


def apply_ops(rgb: np.ndarray, settings: dict) -> np.ndarray:
    """Apply a settings bundle in the frozen order (`STEPS`). Does not mutate the input. The
    per-pixel stretches run on row strips in threads (edit/par.py), which changes no value.
    The first step (`hot_pixels`, `sky_gradient`: the pipeline corrects the decoded photo, F31) and the last two steps, `lut` and `grain`, are not rendered here: the pipeline lays the LUT over the finished picture and emit.py adds the grain to the picture it writes."""
    out = rgb
    if any(settings.get(k, 0.0) for k in _TONE_KEYS):
        if settings.get("highlight_detail", 0.0) and settings.get("highlight_rolloff", 0.0):
            # F50: the base reads the whole frame, so it is made once, before the strips
            out = par.rows(lambda a, r: _tone_steps(a, settings, r), out, detail_ratio(out))
        else:
            out = par.rows(lambda a: _tone_steps(a, settings), out)
    if settings.get("dehaze", 0.0):
        out = dehaze(out, settings["dehaze"])
    if settings.get("clarity", 0.0):
        out = clarity(out, settings["clarity"])
    if settings.get("black_point", 0.0) or settings.get("white_point", 0.0):
        out = levels(out, settings.get("black_point", 0.0), settings.get("white_point", 0.0))
    if any(settings.get(k, 0.0) for k in _COLOUR_KEYS):
        out = par.rows(lambda a: _colour_steps(a, settings), out)
    if settings.get("denoise", 0.0):  # before sharpening, so the sharpener does not see the grain
        out = denoise(out, settings["denoise"], settings.get("denoise_stars", 0.0))
    if settings.get("sharpen", 0.0):  # output sharpening, last
        out = sharpen(out, settings["sharpen"], settings.get("sharpen_stars", 0.0))
    return out
