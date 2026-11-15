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
from .filters import box_min, gaussian_blur, guided_filter

_EPS = 1e-6

OP_NAMES = (
    "exposure", "highlight_rolloff", "shadows_ev", "contrast", "highlights", "shadows", "whites",
    "blacks", "curve", "clarity", "black_point", "white_point", "vibrance", "saturation", "dehaze", "temp", "tint",
    # color grading (split-tone) — tint shadows/highlights independently
    "grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint",
    # targeted HSL — adjust specific hue bands
    "hsl_skin_sat", "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat",
    # noise reduction (F7), then output sharpening (unsharp mask) — applied last
    "denoise", "sharpen",
)

_GRADE_KEYS = ("grade_shadows_temp", "grade_shadows_tint", "grade_highlights_temp", "grade_highlights_tint")
_HSL_KEYS = ("hsl_skin_sat", "hsl_green_sat", "hsl_green_lum", "hsl_blue_sat")

OP_BOUNDS = {
    "exposure": (-3.0, 3.0),
    "highlight_rolloff": (0.0, 100.0),
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
    "denoise": (0.0, 100.0),
    "sharpen": (-100.0, 100.0),
}

# Ops that read more than their own pixel: neighbours (blurs) or the whole frame (dehaze airlight).
SPATIAL_OPS = frozenset({"dehaze", "clarity", "sharpen", "black_point", "white_point", "denoise"})

# The frozen order as data: one tuple per step of apply_ops, in order. A settings bundle may be
# cut between two steps and applied in two calls with the same result (apply.py relies on it).
STEPS = (
    ("temp", "tint", "exposure", "highlight_rolloff"),
    ("shadows_ev",),
    ("highlights", "shadows", "whites", "blacks"),
    ("contrast",),
    ("curve",),
    ("dehaze",),
    ("clarity",),
    ("black_point", "white_point"),
    ("vibrance", "saturation"),
    _HSL_KEYS,
    _GRADE_KEYS,
    ("denoise",),
    ("sharpen",),
)
_TONE_KEYS = frozenset(k for step in STEPS[:5] for k in step)
_COLOUR_KEYS = frozenset(k for step in STEPS if {"vibrance", _HSL_KEYS[0], _GRADE_KEYS[0]} & set(step) for k in step)

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


def _resize_float(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    pil = PILImage.fromarray(a.astype(np.float32), mode="F")
    return np.asarray(pil.resize(size, PILImage.BILINEAR)).astype(np.float32)


# Linear value of the clip threshold the stats/gates use (luma 0.98 in gamma). The knee's
# asymptote sits HERE, not at 1.0, so a kneed highlight never counts as clipped.
_WHITE_LIN = float(srgb_to_linear(np.float32(0.98)))


def highlight_rolloff(lin: np.ndarray, amount: float) -> np.ndarray:
    """Soft highlight knee in LINEAR light — what a camera's tone curve does that a bare exposure
    multiply cannot. With W = the clip level (0.98 gamma) and knee point kp = W·(1 − amount/200)
    (amount 40 → kp ≈ 0.76 linear ≈ 0.88 gamma), values above kp follow
    kp + (W−kp)·(1 − exp(−(x−kp)/(W−kp))): continuous with slope 1 at kp, monotonic, asymptote W,
    so anything an exposure lift pushes past white compresses into [kp, W) instead of clipping.
    Pixels below kp are untouched; amount 0 is the identity."""
    if amount <= 0.0:
        return lin
    kp = _WHITE_LIN * (1.0 - 0.5 * min(amount, 100.0) / 100.0)
    over = lin > kp
    if not over.any():
        return lin
    out = lin.copy()
    x = (lin[over] - kp) / (_WHITE_LIN - kp)
    out[over] = kp + (_WHITE_LIN - kp) * (1.0 - np.exp(-x))
    return out


def light(rgb: np.ndarray, temp: float = 0.0, tint: float = 0.0, ev: float = 0.0,
          rolloff: float = 0.0) -> np.ndarray:
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
    return linear_to_srgb(highlight_rolloff(lin, rolloff))


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


_DENOISE_FULL_GRAIN = 1.0 / 255.0   # measured grain from which the chroma filter works at full weight


def _grain_sigma(l: np.ndarray) -> float:
    """Robust estimate of the fine grain of a luma plane: 1.4826 times the median absolute
    deviation of (luma - a 1 px blur), on every second pixel."""
    s = l[::2, ::2] if min(l.shape) >= 64 else l
    d = s - gaussian_blur(s, 1.0)
    return float(1.4826 * np.median(np.abs(d - np.median(d))))


def denoise(rgb: np.ndarray, amt: float) -> np.ndarray:
    """Noise reduction, chroma first (F7). `amt` 0..100.

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
    return out


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


def _tone_steps(out: np.ndarray, settings: dict) -> np.ndarray:
    """The per-pixel steps before dehaze: light, shadow lift, tone, contrast, curve."""
    temp, tint = settings.get("temp", 0.0), settings.get("tint", 0.0)
    ev, rolloff = settings.get("exposure", 0.0), settings.get("highlight_rolloff", 0.0)
    if temp or tint or ev or rolloff:  # WB → exposure → knee, one linear pass (frozen order kept)
        out = light(out, temp, tint, ev, rolloff)
    if settings.get("shadows_ev", 0.0):  # black-pinned lift, before the additive tone sliders
        out = shadow_lift(out, settings["shadows_ev"])
    if any(settings.get(k, 0.0) for k in ("highlights", "shadows", "whites", "blacks")):
        out = tone(out, settings.get("highlights", 0.0), settings.get("shadows", 0.0),
                   settings.get("whites", 0.0), settings.get("blacks", 0.0))
    if settings.get("contrast", 0.0):
        out = contrast(out, settings["contrast"])
    if settings.get("curve", 0.0):
        out = curve(out, settings["curve"])
    return out


def _colour_steps(out: np.ndarray, settings: dict) -> np.ndarray:
    """The per-pixel steps after clarity: vibrance and saturation, targeted HSL, split-tone."""
    if settings.get("vibrance", 0.0) or settings.get("saturation", 0.0):
        out = vibrance(out, settings.get("vibrance", 0.0), settings.get("saturation", 0.0))
    if any(settings.get(k, 0.0) for k in _HSL_KEYS):
        out = targeted_hsl(out, settings.get("hsl_skin_sat", 0.0), settings.get("hsl_green_sat", 0.0),
                           settings.get("hsl_green_lum", 0.0), settings.get("hsl_blue_sat", 0.0))
    if any(settings.get(k, 0.0) for k in _GRADE_KEYS):
        out = color_grade(out, settings.get("grade_shadows_temp", 0.0), settings.get("grade_shadows_tint", 0.0),
                          settings.get("grade_highlights_temp", 0.0), settings.get("grade_highlights_tint", 0.0))
    return out


def apply_ops(rgb: np.ndarray, settings: dict) -> np.ndarray:
    """Apply a settings bundle in the frozen order (`STEPS`). Does not mutate the input. The
    per-pixel stretches run on row strips in threads (edit/par.py), which changes no value."""
    out = rgb
    if any(settings.get(k, 0.0) for k in _TONE_KEYS):
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
        out = denoise(out, settings["denoise"])
    if settings.get("sharpen", 0.0):  # output sharpening, last
        out = sharpen(out, settings["sharpen"])
    return out
