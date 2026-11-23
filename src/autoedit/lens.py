"""Lens corrections of the capture: lateral chromatic aberration (F46) and, from the user's own
photos, the corner brightening the camera applies to its JPEGs (F52). Part of the decode
(imageio.load_image), at full resolution, before the analysis proxy is made."""

from __future__ import annotations

import io
import math
import os
import tomllib

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


# ---------------------------------------------------------------------------------------------
# F52: lens vignette profiles measured from the camera's own embedded JPEG (module constants, as
# above: the decode reads no rules file).
_VIG_GRID = 360         # long side of the comparison grid, in cells
_VIG_RINGS = 25         # rings of the half-diagonal, each 0.04 wide
_VIG_CENTRE = 0.25      # the centre, inside this share of the half-diagonal, is the reference
_VIG_MIN_CORR = 0.9     # the preview and the RAW must line up at least this well (high-pass correlation)
_VIG_MIN_FRAMES = 5     # photos a bucket needs before it becomes a profile row
_VIG_KNEE = 0.75        # linear value above which the gain is rolled off so 1.0 stays 1.0
_VIG_EDGES = np.linspace(0.0, 1.0, _VIG_RINGS + 1)
_VIG_MID = (_VIG_EDGES[:-1] + _VIG_EDGES[1:]) / 2
_VIG_FILE = "lens_profiles.toml"


def _rho(h: int, w: int) -> np.ndarray:
    """Distance from the frame centre as a share of the half-diagonal, float32 HxW."""
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    return np.hypot((np.arange(h, dtype=np.float32) - cy)[:, None],
                    (np.arange(w, dtype=np.float32) - cx)[None, :]) / math.hypot(cy, cx)


def lens_key(path: str):
    """(camera, lens, focal_mm, f_number) from the EXIF of `path`, or None when any is missing.
    Never reads a serial number."""
    try:
        with PILImage.open(path) as im:
            ex = im.getexif()
            ifd = ex.get_ifd(0x8769)
            cam, lens = str(ex.get(0x0110) or "").strip(), str(ifd.get(0xA434) or "").strip()
            f, n = float(ifd.get(0x920A) or 0), float(ifd.get(0x829D) or 0)
    except Exception:
        return None
    return (cam, lens, f, n) if cam and lens and f > 0 and n > 0 else None


def _bin(x: float) -> int:
    return math.floor(2 * math.log2(x) + 0.5)


def bucket(f: float, n: float) -> tuple[float, float, float, float]:
    """(focal from, to, f-number from, to), edges 2^((b -/+ 0.5)/2) to 2 decimals, b = floor(2 log2 x + 0.5):
    half a stop of focal length, one stop of aperture. An edge value falls in exactly one bucket."""
    e = lambda b: (round(2 ** ((b - 0.5) / 2), 2), round(2 ** ((b + 0.5) / 2), 2))
    return e(_bin(f)) + e(_bin(n))


def camera_gain(lin: np.ndarray, pv: np.ndarray):
    """EV the camera added per ring (_VIG_RINGS values, centre = 0) to its preview `pv` (sRGB float
    [0, 1]) over the RAW `lin` (linear float, same frame), or a reason string. Numpy only."""
    from .imageio import _LUMA, srgb_to_linear
    if max(pv.shape[:2]) < _VIG_GRID:
        return f"preview smaller than {_VIG_GRID} px"
    if abs(lin.shape[1] / lin.shape[0] - pv.shape[1] / pv.shape[0]) > 0.01:
        return "preview framed differently"
    w = _VIG_GRID if lin.shape[1] >= lin.shape[0] else round(_VIG_GRID * lin.shape[1] / lin.shape[0])
    h = round(w * lin.shape[0] / lin.shape[1])
    grid = lambda a: np.asarray(PILImage.fromarray(np.float32(a)).resize((w, h), PILImage.BOX))  # area mean
    ry, rc, py, pc = map(grid, (lin @ _LUMA, lin.max(-1) >= 0.95, srgb_to_linear(pv) @ _LUMA, pv.max(-1) >= 250 / 255))
    lx, lp = np.log2(np.maximum(ry, 1e-6)), np.log2(np.maximum(py, 1e-6))
    hp = lambda a: (a - (np.roll(a, 1, 0) + np.roll(a, -1, 0) + np.roll(a, 1, 1) + np.roll(a, -1, 1)) / 4)[2:-2, 2:-2]
    if np.corrcoef(hp(lx).ravel(), hp(lp).ravel())[0, 1] < _VIG_MIN_CORR:
        return "preview does not line up with the RAW"
    r, ok, gain = _rho(h, w), (rc == 0) & (pc == 0) & (ry > 1e-3) & (py > 2e-3), np.zeros(_VIG_RINGS)
    for it in range(3):   # the tone curve T on the centre, then on every cell with the ring gains taken out
        sel = ok & (r < _VIG_CENTRE) if it == 0 else ok
        if sel.sum() < 100:
            return "too little of the frame is usable"
        x, y = (lx + np.interp(r, _VIG_MID, gain))[sel], lp[sel]
        q = np.quantile(x, np.linspace(0, 1, 25))
        bins = [(x >= a) & (x <= b) for a, b in zip(q[:-1], q[1:])]
        if not all(m.any() for m in bins):
            return "too little of the frame is usable"
        tx = np.array([np.median(x[m]) for m in bins])
        ty = np.maximum.accumulate([np.median(y[m]) for m in bins]) + np.arange(len(bins)) * 1e-6
        inside = ok & (lp > ty[0]) & (lp < ty[-1])
        est = np.interp(lp, ty, tx) - lx                      # T^-1(preview) - RAW, EV per cell
        ring = np.array([np.median(est[m]) if m.sum() >= 20 else np.nan
                         for m in (inside & (r >= a) & (r < b) for a, b in zip(_VIG_EDGES[:-1], _VIG_EDGES[1:]))])
        c = inside & (r < _VIG_CENTRE)
        if not c.any():
            return "too little of the frame is usable"
        ring -= np.median(est[c])
        good = ~np.isnan(ring)
        if good.sum() < _VIG_RINGS // 2:
            return "too little of the frame is usable"
        gain = np.interp(_VIG_MID, _VIG_MID[good], ring[good])
    return gain


def measure(path: str):
    """The ring-gain curve (see camera_gain) of one RAW file against its embedded JPEG, or a reason.
    Never raises."""
    try:
        import rawpy
        with rawpy.imread(path) as raw:
            s = raw.sizes
            lin = raw.postprocess(no_auto_bright=True, gamma=(1, 1), output_bps=16, use_camera_wb=True,
                                  half_size=True, user_flip=0).astype(np.float32) / 65535.0
            th = raw.extract_thumb()
            if s.crop_width > 0:   # the camera's own frame inside LibRaw's output (half size)
                oy, ox = (s.crop_top_margin - s.top_margin) // 2, (s.crop_left_margin - s.left_margin) // 2
                lin = lin[oy:oy + s.crop_height // 2, ox:ox + s.crop_width // 2]
        if th.format != rawpy.ThumbFormat.JPEG:
            return "no JPEG preview"
        pil = PILImage.open(io.BytesIO(th.data))
        pil.draft("RGB", (2 * _VIG_GRID, 2 * _VIG_GRID * pil.height // pil.width))
        return camera_gain(lin, np.asarray(pil.convert("RGB"), np.float32) / 255.0)
    except Exception as e:
        return f"unreadable ({type(e).__name__})"


def _poly(rho, k):
    r2 = rho * rho
    return r2 * (k[0] + r2 * (k[1] + r2 * k[2]))


def build_profiles(paths):
    """(profiles, report). profiles = {camera: {lens: {"buckets": [[f0, f1, n0, n1, frames, k1, k2, k3], ...]}}}
    (numbers and the two model names only), a row per bucket of at least _VIG_MIN_FRAMES photos: the
    median ring curve, fitted by k1 rho^2 + k2 rho^4 + k3 rho^6. report = {"skipped": [(path, reason)],
    "short": [(camera, lens, edges, frames)], "rows": [(camera, lens, row, corner_ev)]}."""
    groups: dict = {}
    report = {"skipped": [], "short": [], "rows": []}
    for p in paths:
        key = lens_key(p)
        if key is None:
            report["skipped"].append((p, "no camera, lens, focal length or aperture in the EXIF"))
            continue
        c = measure(p)
        if isinstance(c, str):
            report["skipped"].append((p, c))
            continue
        groups.setdefault((key[0], key[1], _bin(key[2]), _bin(key[3])), []).append((key, c))
    profiles: dict = {}
    X = np.stack([_VIG_MID ** 2, _VIG_MID ** 4, _VIG_MID ** 6], 1)
    for (cam, lens, _, _), items in sorted(groups.items()):
        edges = bucket(items[0][0][2], items[0][0][3])
        if len(items) < _VIG_MIN_FRAMES:
            report["short"].append((cam, lens, edges, len(items)))
            continue
        k = np.linalg.lstsq(X, np.median([c for _, c in items], axis=0), rcond=None)[0]
        row = [*edges, len(items), *(round(float(v), 6) for v in k)]
        profiles.setdefault(cam, {}).setdefault(lens, {"buckets": []})["buckets"].append(row)
        report["rows"].append((cam, lens, row, round(float(max(0.0, _poly(0.86, k))), 3)))
    return profiles, report


def write_profiles(file: str, profiles: dict) -> None:
    """Write `profiles` to `file`; lenses already in the file but not in `profiles` keep their rows,
    those in both are replaced."""
    from .paths import dump_toml
    try:
        old = _read_profiles(file, cache=False)
    except Exception as e:   # never overwrite a file we cannot read: it may hold the user's other lenses
        raise ValueError(f"{file} exists but cannot be read ({type(e).__name__}); fix or move it, nothing was written") from e
    for cam, lenses in profiles.items():
        old.setdefault(cam, {}).update(lenses)
    os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
    with open(file, "w", encoding="utf-8") as fh:
        fh.write(dump_toml(old))


_PROFILE_CACHE: dict = {}


def _read_profiles(file: str, cache: bool = True) -> dict:
    try:
        mt = os.stat(file).st_mtime_ns
    except OSError:
        return {}
    hit = _PROFILE_CACHE.get(file)
    if cache and hit and hit[0] == mt:
        return hit[1]
    with open(file, "rb") as fh:
        data = tomllib.load(fh)
    _PROFILE_CACHE[file] = (mt, data)
    return data


_VIG_MAX_EV = 3.0   # the most any pixel is lifted


def _row_ok(r) -> bool:
    """A profile row [f0, f1, n0, n1, frames, k1, k2, k3]: eight finite numbers, positive edges."""
    return (isinstance(r, (list, tuple)) and len(r) >= 8
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in r[:8])
            and all(v > 0 for v in r[:4]) and all(abs(v) <= 20 for v in r[5:8]))


def _profile_damaged(data, cam, lens) -> bool:
    try:
        rows = data[cam][lens]["buckets"]
        return not all(_row_ok(r) for r in rows)
    except KeyError:
        return False
    except Exception:
        return True


def _rolloff(lin: np.ndarray, G: np.ndarray) -> np.ndarray:
    """`lin` (linear HxWx3) times the gain G (HxW, >= 1), the hue kept, the top rolled off above
    _VIG_KNEE (F44's shoulder with top = G): 1.0 stays 1.0, nothing below 1.0 reaches it, G = 1 is the identity."""
    m = lin.max(axis=-1) * G
    d = np.maximum(m - _VIG_KNEE, 0.0)
    c = (G - 1) / ((1 - _VIG_KNEE) * np.maximum(G - _VIG_KNEE, 1e-6))
    mm = np.where(m > _VIG_KNEE, _VIG_KNEE + d / (1 + d * c), m)
    scale = G * np.where(m > 0, mm / np.maximum(m, 1e-12), 1.0)
    return np.minimum(lin * scale[..., None], 1.0)


def correct_vignette(img, profiles_file):
    """(rgb, report): `img.rgb` with the corners brightened by the profile of its camera, lens, focal
    length and aperture, as the camera does to its own JPEGs. G = 2^max(0, k1 r^2 + k2 r^4 + k3 r^6)
    is applied on linear light with a roll-off, so 1.0 stays 1.0 and nothing darkens. Report
    {"applied": True, "bucket", "frames", "corner_ev"}, or {"applied": False, "reason"} with `img.rgb` itself."""
    from .imageio import libraw_curve, libraw_curve_inv, linear_to_srgb, srgb_to_linear
    no = lambda why: (img.rgb, {"applied": False, "reason": why})
    if not profiles_file:
        return no("no lens profile (autoedit lens-profile FOLDER)")
    try:
        data = _read_profiles(profiles_file)
    except Exception:
        return no("lens profile unreadable")
    key = lens_key(img.path) if img.path else None
    if key is None:
        return no("no camera, lens, focal length or aperture in the EXIF")
    cam, lens, f, n = key
    bf, bn = _bin(f), _bin(n)
    try:
        rows = data[cam][lens]["buckets"]
        row = next((r for r in rows if _row_ok(r) and _bin(math.sqrt(r[0] * r[1])) == bf
                    and _bin(math.sqrt(r[2] * r[3])) == bn), None)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):   # absent, or a shape we don't know
        row = None
    if row is None:
        bad = _profile_damaged(data, cam, lens)
        return no("lens profile damaged, left as decoded" if bad else "no profile for this lens at this focal length and aperture")
    transfer = img.decode_info.get("transfer")
    if transfer not in ("libraw-2.222-4.5", "srgb"):
        return no(f"unknown transfer {transfer}")
    k = row[5:8]
    rgb = img.rgb
    ev = np.clip(_poly(_rho(*rgb.shape[:2]), k), 0.0, _VIG_MAX_EV)   # capped: a wild profile never gives inf or NaN
    G = np.exp2(ev).astype(np.float32)
    lin = libraw_curve_inv(rgb) if transfer != "srgb" else srgb_to_linear(rgb)
    out = _rolloff(lin, G)
    out = libraw_curve(out) if transfer != "srgb" else linear_to_srgb(out)
    out = np.where((G > 1)[..., None], out, rgb).astype(np.float32)   # G = 1 stays bit for bit
    return out, {"applied": True, "bucket": [row[0], row[1], row[2], row[3]], "frames": int(row[4]),
                 "corner_ev": round(float(max(0.0, _poly(0.86, k))), 3)}
