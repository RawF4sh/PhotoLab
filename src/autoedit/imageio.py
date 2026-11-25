"""Image decode/encode + color management.

Working representation: float32 RGB in [0,1], **sRGB gamma** (display-referred). The apply
engine linearizes for light-like ops and returns to gamma for perceptual ops. Internal
precision is always float; we promote even 8-bit JPEG input to float so intermediate ops
don't quantize (the recovery ceiling stays low for 8-bit input, but we don't make it worse).

Decode preserves bit depth: 16-bit TIFFs are read at 16-bit (via `tifffile` when installed,
else Pillow's native 16-bit modes) instead of being collapsed to 8-bit by an early RGB
conversion. Embedded ICC profiles are converted to sRGB so the working space is well-defined.
16-bit TIFF output uses `tifffile` when installed, else falls back to 8-bit via Pillow.
"""

from __future__ import annotations

import functools
import os
import struct
from dataclasses import dataclass, field

import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps

# Camera RAW formats — decoded via rawpy/libraw (the `accel` extra) at 16-bit.
_RAW_EXT = {".dng", ".crw", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2", ".srf", ".raf",
            ".orf", ".rw2", ".pef", ".srw", ".raw", ".3fr", ".mrw", ".dcr", ".kdc", ".erf", ".fff", ".mos", ".iiq", ".rwl"}
_HEIF_EXT = {".heic", ".heif"}
_PIXEL_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".avif"} | _HEIF_EXT
_heif_ready = False
_SUPPORTED = _PIXEL_EXT | _RAW_EXT
_GRAY16 = {"I;16", "I;16B", "I;16L", "I"}  # Pillow's 16-bit single-channel modes

# EXIF tag numbers we read (from the Exif sub-IFD). Kept tiny + standard — nothing proprietary.
_TAG_ISO = 34855          # ISOSpeedRatings
_TAG_EXPOSURE_BIAS = 37380  # ExposureBiasValue (rational stops)


@dataclass
class Image:
    """A decoded image in the working representation."""

    rgb: np.ndarray  # float32, HxWx3, sRGB gamma, [0,1]
    source_bits: int = 8
    path: str | None = None
    exif: dict | None = None  # small normalized metadata dict (iso, exposure_ev) — {} if none
    decode_ev: float = 0.0    # RAW development lift applied at decode (see RAW_MODES)
    decode_info: dict = field(default_factory=dict)  # decoder/mode/fallback provenance

    @property
    def height(self) -> int:
        return self.rgb.shape[0]

    @property
    def width(self) -> int:
        return self.rgb.shape[1]

    @property
    def long_edge(self) -> int:
        return max(self.height, self.width)


def picker_extensions() -> list[str]:
    """Patterns used by native file pickers, including camera RAW formats."""
    return ["*" + suffix for suffix in sorted(_SUPPORTED)]


def is_supported(path: str) -> bool:
    return any(path.lower().endswith(ext) for ext in _SUPPORTED)


@functools.lru_cache(maxsize=256)
def _camera_tiff(path, device, inode, size, modified):
    """Recognize sensor data in a TIFF container without decoding its embedded thumbnail."""
    try:
        with open(path, "rb") as stream:
            header = stream.read(16)
        if header[:4] not in (b"II*\x00", b"MM\x00*"):
            return False
        # Early Canon EOS-1D TIFF RAW and Phase One IIQ have private container signatures.
        if header[8:12] == bytes.fromhex("ba b0 ac bb") or header[8:16] in (b"IIIICwaR", b"MMMMRawC"):
            return True
        with PILImage.open(path) as image:
            tags = image.tag_v2
            if tags.get(262) in (32803, 34892):  # CFA or LinearRaw, not a developed RGB TIFF
                return True
            # Kodak DCS RAWs expose a reduced first IFD and proprietary sensor metadata.
            if not (int(tags.get(254, 0)) & 1 and str(tags.get(271, "")).strip().lower()=="kodak"):
                return False
            return 33405 in tags and any(tag in tags for tag in (33423,37398,65535))
    except (AttributeError, OSError, ValueError, TypeError, PILImage.DecompressionBombError):
        return False


def is_raw(path: str) -> bool:
    """Recognize camera RAW suffixes and camera originals saved in TIFF containers."""
    path = os.fspath(path)
    if path.lower().endswith(tuple(_RAW_EXT)):
        return True
    if not path.lower().endswith((".tif", ".tiff")):
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return _camera_tiff(os.path.abspath(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _to_unit(arr: np.ndarray, top: float) -> np.ndarray:
    """An unsigned-integer array as float32 arr / top, `top` being its maximum: converted and divided
    in place (no float64 or second full-size temporary), and already in [0, 1], so no clip."""
    out = arr.astype(np.float32)
    out /= np.float32(top)
    return out


def _ensure_heif() -> None:
    """Register the HEIC/HEIF reader of the optional `pillow-heif` package, once. Imported only
    when such a file is opened, so the core install does not need it."""
    global _heif_ready
    if _heif_ready:
        return
    try:
        from pillow_heif import register_heif_opener
    except Exception as exc:
        raise RuntimeError(
            "HEIC and HEIF files need the pillow-heif package: pip install '.[heif]'") from exc
    register_heif_opener()
    _heif_ready = True


def register_reader(path: str) -> None:
    """Make Pillow able to open `path` with a plain `PILImage.open`: registers the HEIC reader for
    a .heic/.heif file and does nothing for any other. Raises RuntimeError without pillow-heif;
    callers that only read metadata catch it with the rest of their open errors."""
    if os.path.splitext(str(path))[1].lower() in _HEIF_EXT:
        _ensure_heif()


def load_image(path: str, decode: dict | None = None) -> Image:
    """Decode any supported image to the working representation, preserving bit depth.

    `decode` holds the RAW development options (RunConfig `raw_*` fields, see `raw_opts_from`)
    and the lens correction (F46, `lens_ca`); the RAW options are ignored for non-RAW input,
    the lens correction also applies to JPEG, TIFF and the other formats.
    """
    img = _load_image(path, decode)
    half = bool(decode and decode.get("half_size") and is_raw(path) and img.decode_info.get("half_size"))
    if half and decode.get("lens_ca"):   # F51: the fringe is measured in pixels of the full development; the vignette is not
        img.decode_info["lens_skipped"] = "half-size development"
    if decode and decode.get("lens_vignette") and is_raw(path):   # F52: before the fringe fix
        from . import lens
        img.rgb, img.decode_info["vignette"] = lens.correct_vignette(img, decode.get("lens_profiles"))
    if decode and decode.get("lens_ca") and not half:
        from . import lens   # here, not at the top: lens imports edit.filters, and edit imports this module
        img.rgb, img.decode_info["lens"] = lens.correct_ca(img.rgb)
    return img


def _load_image(path: str, decode: dict | None = None) -> Image:
    """The decode proper (see `load_image`).

    Order matters: read 16-bit data BEFORE any RGB conversion (Pillow's `convert("RGB")`
    collapses high-bit-depth data to 8-bit), and convert ICC → sRGB for 8-bit inputs so the
    working space is defined rather than assumed."""
    # 0) Camera RAW (.dng/.cr2/.nef/.arw/…) → demosaic at 16-bit via rawpy/libraw.
    decode_info: dict = {}
    raw = _decode_raw(path, decode, report=decode_info)
    if raw is not None:
        rgb, bits, ev = raw
        # RAW is the preferred edit source, so EXIF priors (ISO → restrained sharpening)
        # matter most here. Many RAWs are TIFF-containers Pillow can read metadata from;
        # rawpy already outputs upright pixels, so only the ISO/EV tags are wanted.
        try:
            exif = _read_exif(PILImage.open(path))
        except Exception:
            exif = {}
        return Image(rgb=np.ascontiguousarray(rgb), source_bits=bits, path=path, exif=exif,
                     decode_ev=ev, decode_info=decode_info)

    if os.path.splitext(path)[1].lower() in _HEIF_EXT:
        _ensure_heif()
    try:
        pil = PILImage.open(path)
    except PILImage.UnidentifiedImageError as exc:
        if path.lower().endswith(".avif"):
            raise RuntimeError(
                "this Pillow cannot read AVIF; it needs Pillow 11.3 or later") from exc
        raise
    exif = _read_exif(pil)

    # 1) 16-bit TIFF via tifffile (preserves 48-bit RGB that Pillow truncates to 24-bit).
    #    tifffile returns raw pixels, so the EXIF orientation must be applied here by hand —
    #    otherwise a rotated TIFF loads sideways only when the accel extra is installed.
    tiff = _decode_tiff16(path)
    if tiff is not None:
        rgb, bits = tiff
        rgb = _apply_exif_orientation(rgb, _orientation_of(pil))
        return Image(rgb=np.ascontiguousarray(rgb), source_bits=bits, path=path, exif=exif)

    pil = ImageOps.exif_transpose(pil)  # honor the orientation tag, then drop it

    # 2) Pillow's native 16-bit single-channel modes — read the uint16 array directly.
    if pil.mode in _GRAY16:
        arr = np.asarray(pil).astype(np.float32)
        g = np.clip(arr / 65535.0, 0.0, 1.0)
        rgb = np.repeat(g[..., None], 3, axis=2)
        return Image(rgb=np.ascontiguousarray(rgb), source_bits=16, path=path, exif=exif)

    # 3) 8-bit path: convert any embedded ICC profile to sRGB, then to RGB.
    pil = _to_srgb(pil)
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    arr = np.asarray(pil)
    bits = 16 if arr.dtype == np.uint16 else 8      # 48-bit RGB Pillow kept as uint16
    return Image(rgb=np.ascontiguousarray(_to_unit(arr, 65535.0 if bits == 16 else 255.0)),
                 source_bits=bits, path=path, exif=exif)


# RAW development modes (RunConfig.raw_mode). "none" is the historical libraw render — camera WB,
# no auto-bright, plain gamma — and stays the default. Measured on 110 Dolomites CR2s it sits a
# median 1.4–1.8 EV under the camera's OWN embedded JPEG, so the other modes are candidates that
# lift the LINEAR demosaic before gamma; scripts/raw_benchmark.py ranks them, nothing flips the
# default automatically.
RAW_MODES = ("none", "linear_srgb", "libraw_auto", "preview_ev", "preview_ev_headroom", "tone_target")
_RAW_DEFAULTS = {"raw_mode": "none", "raw_max_ev": 2.5, "raw_clip_budget": 0.005,
                 "raw_tone_target_p50": 0.40, "raw_fallback_ev": 0.0, "raw_recover": False}
_LENS_DEFAULTS = {"lens_ca": False, "lens_vignette": False}   # F52: corners brightened from a lens profile. F46: scale red and blue onto green; off → decoded as it is
# Headroom-capped modes stop this many stops short of the clip level, so the pipeline's own
# whites/shadows/sharpen (which run AFTER decode) don't push the top percentile into white.
# Mirrors the `headroom_ev - 0.15` margin the exposure rules use (configs/rules.toml).
_HEADROOM_MARGIN_EV = 0.15


def raw_opts_from(cfg) -> dict:
    """The RAW development options a RunConfig carries (its raw_* fields), shaped for
    `load_image(decode=...)`. Works on any object exposing those attributes."""
    o = {k: getattr(cfg, k) for k in (*_RAW_DEFAULTS, *_LENS_DEFAULTS) if hasattr(cfg, k)}
    if o.get("lens_vignette"):
        from .lens import _VIG_FILE
        pd = getattr(cfg, "profile_dir", None)
        o["lens_profiles"] = os.path.join(pd, _VIG_FILE) if pd else None
    return o


def analysis_opts(cfg, path: str) -> dict | None:
    """F51: the decode options of a RAW file's half-size analysis picture, or None (mode off, not a RAW)."""
    if getattr(cfg, "fast_analysis", False) and is_raw(path):
        return {**raw_opts_from(cfg), "half_size": True}
    return None


def develop_ev(lin_rgb: np.ndarray, camera_rgb: np.ndarray | None, opts: dict | None) -> float:
    """Stops of linear lift a development mode asks for. Pure math (testable without a RAW).

    lin_rgb     the LINEAR demosaic (float [0,1]); camera_rgb the camera's gamma-encoded preview
                or None.
    preview_ev            match the preview's linear median (raw_fallback_ev without a preview)
    preview_ev_headroom   same, but never past the highlight headroom (minus a 0.15 EV margin) —
                          at most `raw_clip_budget` of pixels may reach white. The sky is protected by construction; the
                          residual foreground deficit is left to the shadows / region rules.
    tone_target           ignore the camera: put the median at `raw_tone_target_p50` (gamma),
                          headroom-capped.
    linear_srgb           0 — linear demosaic encoded with the exact sRGB transfer function.
    none / libraw_auto    0 — libraw's own render, with or without its 1%-clip auto-bright.
    Never negative (a mode never darkens), never above raw_max_ev."""
    o = {**_RAW_DEFAULTS, **(opts or {})}
    mode = o["raw_mode"]
    if mode not in RAW_MODES:
        raise ValueError(f"unknown raw_mode {mode!r}; choose one of {RAW_MODES}")
    if mode not in ("preview_ev", "preview_ev_headroom", "tone_target"):
        return 0.0
    lum_lin = luma(lin_rgb)
    raw_p50 = max(float(np.percentile(lum_lin, 50)), 1e-5)
    clip_q = 100.0 * (1.0 - float(o["raw_clip_budget"]))
    white = float(srgb_to_linear(np.float32(0.98)))
    # A luminance percentile misses saturated red/blue highlights. The budget applies to
    # pixels clipping ANY channel, consistently with stats.compute's headroom definition.
    brightest = np.max(lin_rgb, axis=-1)
    headroom = max(0.0, float(np.log2(white / max(float(np.percentile(brightest, clip_q)), 1e-5)))
                   - _HEADROOM_MARGIN_EV)
    if mode == "tone_target":
        target = float(srgb_to_linear(np.float32(o["raw_tone_target_p50"])))
        ev = min(float(np.log2(target / raw_p50)), headroom)
    elif camera_rgb is None:
        ev = float(o["raw_fallback_ev"])
    else:
        cam_p50 = max(float(np.percentile(luma(srgb_to_linear(camera_rgb)), 50)), 1e-5)
        ev = float(np.log2(cam_p50 / raw_p50))
    if mode == "preview_ev_headroom":
        ev = min(ev, headroom)
    return float(np.clip(ev, 0.0, float(o["raw_max_ev"])))


def _preview_rgb(raw, max_px: int = 1024) -> np.ndarray | None:
    """The camera-rendered JPEG preview embedded in an open rawpy RAW, as float gamma RGB with
    the long edge ≤ max_px, in stored orientation. None when absent or unreadable."""
    try:
        import io

        import rawpy

        t = raw.extract_thumb()
        if t.format == rawpy.ThumbFormat.JPEG:
            pil = PILImage.open(io.BytesIO(t.data)).convert("RGB")
        elif t.format == rawpy.ThumbFormat.BITMAP:
            bitmap = np.asarray(t.data)
            if bitmap.dtype == np.uint16:
                bitmap = np.rint(bitmap.astype(np.float32) / 257.0).astype(np.uint8)
            if bitmap.ndim not in (2, 3):
                return None
            if bitmap.ndim == 3 and bitmap.shape[-1] == 1:
                bitmap = bitmap[..., 0]
            pil = PILImage.fromarray(bitmap).convert("RGB")
        else:
            return None
        if min(pil.size) < 8:
            return None
        pil.thumbnail((max_px, max_px))
        return np.asarray(pil).astype(np.float32) / 255.0
    except Exception:
        return None


def camera_preview(path: str, max_px: int = 1600, like: np.ndarray | None = None) -> np.ndarray | None:
    """The camera's own render of a RAW file (its embedded JPEG preview), upright, float gamma
    RGB — the free calibration target every RAW carries, no paired JPG needed. `like` (an HxW…
    array of the decoded RAW) fixes a preview whose stored orientation disagrees with the
    demosaic. None for non-RAW input or when the file has no preview."""
    if not is_raw(path):
        return None
    try:
        import rawpy

        with rawpy.imread(path) as raw:
            rgb = _preview_rgb(raw, max_px)
    except Exception:
        return None
    if rgb is None:
        return None
    try:
        rgb = _apply_exif_orientation(rgb, _orientation_of(PILImage.open(path)))
    except Exception:
        pass
    if like is not None and not _compatible_preview(rgb, like, orientation_sensitive=True):
        # Do not guess a 90-degree direction or stretch a differently cropped thumbnail.
        # Histogram-only development may still use a compatible stored-orientation preview.
        return None
    return np.ascontiguousarray(rgb)


def _compatible_preview(preview: np.ndarray | None, rgb: np.ndarray,
                        orientation_sensitive: bool = False) -> bool:
    """Reject absent/invalid or differently cropped previews; allow resize rounding only.

    Development compares histograms, so stored rotation is immaterial. Spatial consumers
    require the same orientation and aspect after metadata orientation has been applied.
    No assumption is made that matching aspect proves pixel registration.
    """
    if preview is None or preview.ndim != 3 or preview.shape[-1] != 3:
        return False
    if min(preview.shape[:2]) < 8 or not np.isfinite(preview).all():
        return False
    ph, pw = preview.shape[:2]
    rh, rw = rgb.shape[:2]
    if not orientation_sensitive:
        ph, pw = sorted((ph, pw))
        rh, rw = sorted((rh, rw))
    expected = ph * rw / max(rh, 1)
    return abs(pw - expected) <= 2.0


# RAW highlight recovery (F44). Constants of the decode, like _HEADROOM_MARGIN_EV (the decode reads
# no rules file).
_RECOVER_KNEE = 0.75        # linear; a pixel whose largest channel is at or below it is never changed (221 of 255)
_RECOVER_FADE = 0.25        # rebuilt colour fades to neutral over this much above the old white (linear)
_RECOVER_EDGE = 0.02        # share of the long side: the colour fade reaches full strength this far inside a clipped area
_RECOVER_MIN_SHARE = 0.002  # a frame with less of its area clipped is left as decoded
_RECOVER_TOP_Q = 99.9       # percentile of the rebuilt pixels that the shoulder maps to white


def _libraw_gamma(pwr: float = 1 / 2.222, ts: float = 4.5) -> tuple[float, float]:
    """(g2, g4) of LibRaw's gamma_curve(pwr, ts), found by the same 48-step bisection LibRaw runs:
    g2 is where the straight toe (slope ts) meets the power segment, on the encoded side; g4 is the
    offset of the power segment."""
    lo, hi = 0.0, 1.0
    g2 = 0.0
    for _ in range(48):
        g2 = (lo + hi) / 2.0
        if ((g2 / ts) ** (-pwr) - 1.0) / pwr - 1.0 / g2 > -1.0:
            hi = g2
        else:
            lo = g2
    return g2, g2 * (1.0 / pwr - 1.0)


_BT709_PWR, _BT709_TS = 1 / 2.222, 4.5
_BT709_G2, _BT709_G4 = _libraw_gamma(_BT709_PWR, _BT709_TS)   # 0.081226, 0.099258


def libraw_curve(lin):
    """LibRaw's output curve for gamma=(2.222, 4.5), the transfer of the default decode: lin * 4.5
    below lin = g2 / 4.5, else lin ** (1/2.222) * (1 + g4) - g4. Float32, input clipped to [0, 1]."""
    lin = np.clip(np.asarray(lin, dtype=np.float64), 0.0, 1.0)
    return np.where(lin < _BT709_G2 / _BT709_TS, lin * _BT709_TS,
                    lin ** _BT709_PWR * (1.0 + _BT709_G4) - _BT709_G4).astype(np.float32)


def libraw_curve_inv(v):
    """The inverse of libraw_curve."""
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    return np.where(v < _BT709_G2, v / _BT709_TS,
                    ((v + _BT709_G4) / (1.0 + _BT709_G4)) ** (1.0 / _BT709_PWR)).astype(np.float32)


def shoulder(m, knee: float, top: float):
    """F44's shoulder on linear values: m itself up to the knee; above it knee + d / (1 + d * c) with
    d = m - knee and c = (top - 1) / ((1 - knee) * (top - knee)). Slope 1 at the knee, increasing,
    exactly 1 at top. top <= 1 returns m itself."""
    if top <= 1.0:
        return m
    c = (top - 1.0) / ((1.0 - knee) * (top - knee))
    d = np.maximum(m - knee, 0.0)
    return np.where(m > knee, knee + d / (1.0 + d * c), m)


def unshoulder(y: float, knee: float, top: float) -> float:
    """The inverse of `shoulder` for one linear value, capped at 1 (the default decode's white)."""
    if top <= 1.0 or y <= knee:
        return min(y, 1.0)
    e = y - knee
    den = 1.0 - e * (top - 1.0) / ((1.0 - knee) * (top - knee))
    return 1.0 if den <= 0.0 else min(1.0, knee + e / den)


def recover_highlights(rgb, rebuilt, clipped, knee=_RECOVER_KNEE, fade=_RECOVER_FADE, top_q=_RECOVER_TOP_Q,
                       edge=_RECOVER_EDGE):
    """F44's rebuild, testable without a RAW file. rgb: the default decode (LibRaw's curve, [0, 1]);
    clipped: HxW bool, its pixels with a channel at 1.0; rebuilt: (N, 3) linear values of those pixels,
    in the same order, from LibRaw's blend decode with the exposure put back (they may exceed 1).
    A clipped pixel takes the luminance of `rebuilt` and the colour of the default decode, faded towards
    neutral (the camera renders blown highlights white) by w = (how far the largest rebuilt channel is
    past 1.0, full at 1 + fade) x (how far the pixel is inside the clipped area, from a box blur of
    `clipped`: 0 at its edge, full at edge x the long side in). So a clipped sky meets the unclipped
    pixels around it in their own colour, with no rim and no blotch, and only the inside of a large
    clipped area goes neutral. One shoulder on the largest channel then maps [knee, top] onto [knee, 1],
    top = the top_q percentile of the rebuilt pixels; all three channels get the same factor, so hue and
    saturation stay. A pixel whose largest channel is at or below the knee keeps its value bit for bit.
    Returns (rgb, top); when nothing rebuilt passes the old white, (rgb itself, 1.0)."""
    from .edit.filters import box_blur   # here, not at the top: edit imports this module
    old = libraw_curve_inv(rgb[clipped])
    y_new = rebuilt @ _LUMA
    y_old = np.maximum(old @ _LUMA, 1e-6)
    r = max(1, round(edge * max(clipped.shape)))
    inside = np.clip(2.0 * box_blur(clipped.astype(np.float32), r)[clipped] - 1.0, 0.0, 1.0)
    w = (np.clip((rebuilt.max(axis=-1) - 1.0) / fade, 0.0, 1.0) * inside)[:, None]
    rec = y_new[:, None] * ((1.0 - w) * old / y_old[:, None] + w)
    top = max(1.0, float(np.percentile(rec.max(axis=-1), top_q)))
    if top <= 1.0:
        return rgb, 1.0
    hi = clipped | (rgb.max(axis=-1) > float(libraw_curve(knee)))
    lin = libraw_curve_inv(rgb[hi])
    lin[clipped[hi]] = rec               # boolean indexing keeps row-major order: the same pixels as rgb[clipped]
    m = lin.max(axis=-1)
    scale = shoulder(m, knee, top) / np.maximum(m, 1e-9)
    out = rgb.copy()
    out[hi] = np.where((m > knee)[:, None], libraw_curve(np.minimum(lin * scale[:, None], 1.0)), rgb[hi])
    return out, top


def _recover(raw, rgb, common: dict, info: dict):
    """F44 on an open rawpy file and its default decode: asks LibRaw for the blend decode only when
    enough of the frame is clipped, puts its exposure back, and rebuilds. Records what it did in `info`."""
    clipped = rgb.max(axis=-1) >= 1.0
    share = float(clipped.mean())
    info.update(highlight="clip", recover_clipped=round(share, 4))
    if getattr(raw, "num_colors", 3) == 1:
        info["recover_skipped"] = "the monochrome sensor has no independent color channels"
        return rgb
    if share < _RECOVER_MIN_SHARE:
        info["recover_skipped"] = f"{share * 100:.2f}% of the frame is clipped, less than {_RECOVER_MIN_SHARE * 100:g}%"
        return rgb
    wb = [float(v) for v in raw.camera_whitebalance[:3]]
    if min(wb) <= 0.0:
        info["recover_skipped"] = "the file has no camera white balance"
        return rgb
    blend = raw.postprocess(no_auto_bright=True, gamma=(1.0, 1.0), highlight_mode=2, **common)
    rebuilt = blend[clipped].astype(np.float32) * np.float32(max(wb) / min(wb) / 65535.0)
    out, top = recover_highlights(rgb, rebuilt, clipped)
    if top > 1.0:
        info.update(highlight="recover", recover_top=round(top, 3))
    return out


def _mark_half(info: dict, common: dict, raw, rgb) -> None:
    """info["half_size"] only when LibRaw really returned a smaller picture (a linear DNG comes back full size)."""
    if common["half_size"] and rgb.shape[1] < raw.sizes.width:
        info["half_size"] = True


def _raw_rgb(samples: np.ndarray) -> np.ndarray:
    """LibRaw returns one channel for monochrome sensors and film scans."""
    if samples.ndim == 2:
        samples = samples[..., None]
    if samples.shape[-1] == 1:
        return np.broadcast_to(samples, samples.shape[:2] + (3,))
    return samples


def _decode_raw(path: str, opts: dict | None = None, report: dict | None = None):
    """Demosaic a camera RAW to 16-bit via rawpy/libraw. Returns (rgb[0,1], 16, decode_ev) or
    None when the path isn't a RAW. Camera white balance always. `opts` = RunConfig raw_* fields
    (see RAW_MODES); an extra `half_size` key is a benchmark speed knob (quarter-res demosaic).
    "none" reproduces the historical render byte-for-byte; the lifting modes decode LINEAR, apply
    `develop_ev` stops, then encode with the sRGB curve (our working space)."""
    if not is_raw(path):
        return None
    try:
        import rawpy
    except Exception as exc:
        raise RuntimeError(
            "RAW files (.dng/.cr2/.nef/.arw/…) need the rawpy decoder — install the accel "
            "extra: pip install '.[accel]'"
        ) from exc
    o = {**_RAW_DEFAULTS, **(opts or {})}
    mode = o["raw_mode"]
    if mode not in RAW_MODES:
        raise ValueError(f"unknown raw_mode {mode!r}; choose one of {RAW_MODES}")
    info = report if report is not None else {}
    info.update(decoder="rawpy/libraw", mode=mode, source_format=os.path.splitext(path)[1].lower(),
                transfer="libraw-2.222-4.5" if mode in ("none", "libraw_auto") else "srgb",
                preview_used=False, fallback_reason=None)
    if o["raw_recover"] and mode != "none":
        # ponytail: recovery for raw_mode none only; in the linear modes the shoulder would replace their final clip
        info.update(highlight="clip", recover_skipped=f"raw_mode {mode} keeps its own highlights")
    common = dict(output_bps=16, use_camera_wb=True, half_size=bool(o.get("half_size", False)))
    with rawpy.imread(path) as raw:
        if mode in ("none", "libraw_auto"):
            rgb16 = raw.postprocess(
                no_auto_bright=(mode == "none"),  # "none": feed the true exposure, no stretch
                gamma=(2.222, 4.5),                # sRGB-ish tone curve → our gamma working space
                **common,
            )
            rgb = _to_unit(_raw_rgb(rgb16), 65535.0)
            _mark_half(info, common, raw, rgb)
            if o["raw_recover"] and mode == "none":
                rgb = _recover(raw, rgb, common, info)   # F44: a second decode of the open file
            return rgb, 16, 0.0
        lin16 = _raw_rgb(raw.postprocess(no_auto_bright=True, gamma=(1.0, 1.0), **common))
        lin = _to_unit(lin16, 65535.0)
        _mark_half(info, common, raw, lin)
        cam = _preview_rgb(raw) if mode.startswith("preview") else None
        if mode.startswith("preview") and not _compatible_preview(cam, lin):
            cam = None
            info["fallback_reason"] = "missing, invalid, or differently cropped camera preview; no inferred lift"
        info["preview_used"] = cam is not None
    ev = develop_ev(lin, cam, o)
    info["decode_ev"] = ev
    if mode.startswith("preview") and cam is None and ev:
        info["fallback_reason"] = "explicit configured fallback exposure (not inferred from another camera)"
    # The lift and the sRGB encode as a table over the 65536 possible values: they are elementwise, so
    # the result is bit for bit what running them on every pixel gives, at a fifth of the time.
    lut = _to_unit(np.arange(65536, dtype=np.uint16), 65535.0)
    if ev:
        lut = lut * np.float32(2.0 ** ev)
    return linear_to_srgb(np.clip(lut, 0.0, 1.0))[lin16], 16, ev


def _orientation_of(pil: PILImage.Image) -> int:
    """EXIF/TIFF orientation tag (274), defaulting to 1 (upright) on any failure."""
    try:
        return int(pil.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def _apply_exif_orientation(arr: np.ndarray, orientation: int) -> np.ndarray:
    """Apply an EXIF orientation (tag 274, values 1-8) to an HxW[xC] array — the numpy
    equivalent of ImageOps.exif_transpose, for decode paths that bypass Pillow (tifffile).
    The mapping mirrors Pillow's: 2=FLIP_LEFT_RIGHT, 3=ROTATE_180, 4=FLIP_TOP_BOTTOM,
    5=TRANSPOSE, 6=ROTATE_270, 7=TRANSVERSE, 8=ROTATE_90."""
    if orientation == 2:
        return arr[:, ::-1]
    if orientation == 3:
        return arr[::-1, ::-1]
    if orientation == 4:
        return arr[::-1]
    if orientation == 5:
        return np.swapaxes(arr, 0, 1)
    if orientation == 6:
        return np.rot90(arr, k=-1)
    if orientation == 7:
        return np.swapaxes(arr[::-1, ::-1], 0, 1)
    if orientation == 8:
        return np.rot90(arr, k=1)
    return arr


def _decode_tiff16(path: str):
    """Decode a TIFF at full bit depth via tifffile (optional). Returns (rgb[0,1], bits) or
    None to fall back to Pillow. tifffile is the only reliable 48-bit-RGB TIFF reader here."""
    if not path.lower().endswith((".tif", ".tiff")):
        return None
    try:
        import tifffile
    except Exception:
        return None
    try:
        arr = np.asarray(tifffile.imread(path))
    except Exception:
        return None
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=2)
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        arr = arr[..., :3]
    else:
        return None
    if arr.dtype == np.uint16:
        return _to_unit(arr, 65535.0), 16
    if arr.dtype == np.uint8:
        return _to_unit(arr, 255.0), 8
    if np.issubdtype(arr.dtype, np.floating):
        return np.clip(arr.astype(np.float32), 0.0, 1.0), 16
    return None


def _to_srgb(pil: PILImage.Image) -> PILImage.Image:
    """Convert an embedded ICC profile to sRGB (e.g. AdobeRGB / Display-P3 JPEGs). No profile →
    assume sRGB and return unchanged. Best-effort: any ICC failure degrades to the original."""
    icc = pil.info.get("icc_profile")
    if not icc:
        return pil
    try:
        import io

        from PIL import ImageCms

        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        try:
            # Transform the ORIGINAL mode. Pre-converting a CMYK JPEG to RGB first would
            # mismatch the embedded CMYK profile's colour space and throw (then silently fall
            # back to Pillow's naive CMYK→RGB, which is wrong for Adobe/inverted CMYK).
            return ImageCms.profileToProfile(pil, src, dst, outputMode="RGB")
        except Exception:
            rgb = pil if pil.mode in ("RGB", "RGBA") else pil.convert("RGB")
            return ImageCms.profileToProfile(rgb, src, dst, outputMode="RGB")
    except Exception:
        return pil


def _read_exif(pil: PILImage.Image) -> dict:
    """Read ISO, exposure compensation, and the daylight exposure value if all tags exist.
    Camera metadata is messy and optional — any failure just yields {} (neutral defaults)."""
    out: dict = {}
    try:
        ifd = pil.getexif().get_ifd(0x8769)  # the Exif sub-IFD (where ISO / exposure live)
    except Exception:
        return out
    iso = ifd.get(_TAG_ISO)
    if isinstance(iso, (list, tuple)):
        iso = iso[0] if iso else None
    try:
        if iso is not None and float(iso) > 0:
            out["iso"] = float(iso)
    except (TypeError, ValueError):
        pass
    ev = ifd.get(_TAG_EXPOSURE_BIAS)
    try:
        if ev is not None:
            out["exposure_ev"] = float(ev)  # PIL IFDRational casts cleanly to float
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        seconds, aperture = float(ifd[0x829A]), float(ifd[0x829D])
        if seconds > 0 and aperture > 0 and out.get("iso", 0) > 0:
            ev100 = np.log2((aperture ** 2 / seconds) * (100.0 / out["iso"]))
            if np.isfinite(ev100):
                out["capture_ev100"] = float(ev100)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        pass
    return out


# ── export metadata (F1, docs/plans/F1-export-metadata.md) ─────────────────────
METADATA_MODES = ("keep", "shareable", "none")
_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
# First-directory tags that describe the photograph: ImageDescription, Make, Model, X/Y
# resolution and its unit, DateTime, Artist, Copyright. Every other tag there describes how the
# SOURCE file stores its pixels (strip offsets, compression, preview pointers, XMP, DNG/CR2
# private tags) and would be wrong in a re-encoded file, so this is an allow-list.
_EXIF_KEEP_IFD0 = (0x010E, 0x010F, 0x0110, 0x011A, 0x011B, 0x0128, 0x0132, 0x013B, 0x8298)
# Never copied from the Exif sub-directory: the maker note (its internal offsets break when the
# block moves, and at 68 KB in a CR2 it exceeds what a JPEG can hold) and the interoperability
# pointer (it names the source's colour space; an export is always sRGB).
_EXIF_DROP = (0x927C, 0xA005)
# Dropped as well under "shareable", together with the GPS block: the tags that identify the
# physical camera and lens (BodySerialNumber, LensSerialNumber, and the SerialNumber tag Adobe
# Camera Raw writes). Names the photographer entered (artist, copyright, owner name) stay.
_EXIF_SERIALS = (0xA431, 0xA435, 0xFDE9)
_EXIF_MAX_BYTES = 65_000  # a JPEG APP1 segment holds at most 65,533 bytes


@functools.lru_cache(maxsize=1)
def srgb_profile() -> bytes | None:
    """The sRGB ICC profile embedded in exports (588 bytes, built by LittleCMS), or None when
    this Pillow build has no LittleCMS. The creation date in the header is fixed, so the same
    pixels always produce the same file."""
    try:
        from PIL import ImageCms

        icc = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    except Exception:
        return None
    icc[24:36] = struct.pack(">6H", 2026, 1, 1, 0, 0, 0)  # header dateTimeNumber
    return bytes(icc)


def export_exif(source: str | None, mode: str, size: tuple[int, int],
                software: str | None = None) -> bytes | None:
    """The EXIF block for an export of `source` at `size` (width, height), or None when nothing
    is to be written: mode "none", no source, a source without EXIF, an unreadable source, or a
    block too large for a JPEG. Never raises for a bad source: metadata must not fail a photo."""
    if mode not in METADATA_MODES:
        raise ValueError(f"unknown metadata mode {mode!r}; choose one of {METADATA_MODES}")
    if mode == "none" or not source:
        return None
    try:
        if os.path.splitext(source)[1].lower() in _HEIF_EXT:
            _ensure_heif()
        with PILImage.open(source) as pil:
            src = pil.getexif()
            base = {t: src[t] for t in _EXIF_KEEP_IFD0 if t in src}
            drop = _EXIF_DROP if mode == "keep" else _EXIF_DROP + _EXIF_SERIALS
            sub = {t: v for t, v in src.get_ifd(_EXIF_IFD).items() if t not in drop}
            gps = dict(src.get_ifd(_GPS_IFD)) if mode == "keep" else {}
        if not (base or sub or gps):
            return None
        out = PILImage.Exif()
        for t, v in base.items():
            out[t] = v
        out[0x0112] = 1                      # Orientation: the pixels are already upright
        if software:
            out[0x0131] = software
        sub[0xA001] = 1                      # ColorSpace: sRGB
        sub[0xA002], sub[0xA003] = int(size[0]), int(size[1])   # PixelX/YDimension
        out[_EXIF_IFD] = sub
        if gps:
            out[_GPS_IFD] = gps
        data = out.tobytes()
    except Exception:
        return None
    return data if len(data) <= _EXIF_MAX_BYTES else None


def downscale_long_edge(img: Image, long_edge: int) -> Image:
    """Return a proxy with the long edge clamped to `long_edge` (no upscaling).

    Resizes the float channels directly (PIL "F" mode) — never through uint8 — so a 16-bit
    source keeps its precision in the analysis proxy and no dither noise leaks into the stats.
    BILINEAR is deliberate: the analysis proxy wants stable area-average behaviour, and a
    sharper filter (LANCZOS) would ring on edges and shift every stat-derived golden test."""
    if img.long_edge <= long_edge:
        return img
    scale = long_edge / img.long_edge
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    chans = [
        np.asarray(
            PILImage.fromarray(np.ascontiguousarray(img.rgb[..., c]), mode="F")
            .resize((new_w, new_h), PILImage.BILINEAR),
            dtype=np.float32)
        for c in range(3)
    ]
    rgb = np.clip(np.stack(chans, axis=-1), 0.0, 1.0)
    return Image(rgb=rgb, source_bits=img.source_bits, path=img.path, exif=img.exif,
                 decode_ev=img.decode_ev, decode_info=dict(img.decode_info))


def resize_to(img: Image, height: int, width: int) -> Image:
    """Return `img` at exactly (height, width), resampled as `downscale_long_edge` does it.

    For a second analysis-size picture of one photo that must have the first one's shape."""
    if img.rgb.shape[:2] == (height, width):
        return img
    chans = [
        np.asarray(
            PILImage.fromarray(np.ascontiguousarray(img.rgb[..., c]), mode="F")
            .resize((width, height), PILImage.BILINEAR),
            dtype=np.float32)
        for c in range(3)
    ]
    rgb = np.clip(np.stack(chans, axis=-1), 0.0, 1.0)
    return Image(rgb=rgb, source_bits=img.source_bits, path=img.path, exif=img.exif,
                 decode_ev=img.decode_ev, decode_info=dict(img.decode_info))


def resize_long_edge(rgb: np.ndarray, long_edge: int) -> np.ndarray:
    """Downscale a float RGB image so its long edge is `long_edge` (never upscales). Lanczos on
    the float channels: an export wants a clean, sharp reduction; the analysis proxy keeps its
    own bilinear `downscale_long_edge`."""
    h, w = rgb.shape[:2]
    if max(h, w) <= long_edge:
        return rgb
    scale = long_edge / max(h, w)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    chans = [np.asarray(PILImage.fromarray(np.ascontiguousarray(rgb[..., c], dtype=np.float32), mode="F")
                        .resize(size, PILImage.LANCZOS), dtype=np.float32) for c in range(3)]
    return np.clip(np.stack(chans, axis=-1), 0.0, 1.0)


def save_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92,
               exif: bytes | None = None, icc_profile: bytes | None = None) -> None:
    """Write the working RGB (float, sRGB gamma, [0,1]) to disk.

    Atomic: encode to a sibling temp file (same extension, so PIL/tifffile infer the format
    identically to a direct write), then os.replace into place. A crash mid-write leaves the
    old file or nothing — never a half-written image a resume would mistake for complete."""
    rgb = np.clip(rgb, 0.0, 1.0)
    tmp = f"{path}.part{os.path.splitext(path)[1]}"      # e.g. out.jpg.part.jpg
    if fmt == "tiff":
        if _save_tiff16(rgb, tmp, exif, icc_profile):
            os.replace(tmp, path)
            return
        # fall through to 8-bit if tifffile is unavailable
    pil = PILImage.fromarray(_to_u8(rgb), mode="RGB")
    extra = {k: v for k, v in (("exif", exif), ("icc_profile", icc_profile)) if v is not None}
    if fmt in ("jpg", "jpeg"):
        pil.save(tmp, quality=quality, subsampling=0, **extra)
    elif fmt == "webp":
        pil.save(tmp, quality=quality, method=4, **extra)
    elif fmt == "png":
        pil.save(tmp, compress_level=6, **extra)
    else:
        pil.save(tmp, **extra)
    os.replace(tmp, path)


# ── color helpers ───────────────────────────────────────────────────────────

def srgb_to_linear(s: np.ndarray) -> np.ndarray:
    s = np.clip(s, 0.0, 1.0)
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(l: np.ndarray) -> np.ndarray:
    l = np.clip(l, 0.0, 1.0)
    return np.where(l <= 0.0031308, l * 12.92, 1.055 * (l ** (1.0 / 2.4)) - 0.055).astype(np.float32)


# Rec.709 luma on the gamma-encoded working values (matches the old Rust stats convention).
_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def luma(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel Rec.709 luminance of a HxWx3 (or Nx3) gamma RGB array, in [0,1]."""
    return rgb @ _LUMA


def _hash_unit(yy: np.ndarray, xx: np.ndarray, a: int, b: int) -> np.ndarray:
    """Deterministic per-pixel value in [0,1) from integer coords — a cheap hash (no RNG, so
    output stays reproducible for golden tests)."""
    v = ((xx.astype(np.uint64) * a) ^ (yy.astype(np.uint64) * b)) * np.uint64(2654435761)
    return (v & np.uint64(0xFFFFFFFF)).astype(np.float64) / 4294967296.0


def _dither(h: int, w: int) -> np.ndarray:
    """Triangular-PDF dither in ~[-1, 1) LSB from two decorrelated coordinate hashes. Spreading the
    rounding by ±1 LSB turns hard quantization steps (banded/contoured skies) into imperceptible
    noise — the standard fix for 8-bit gradient banding. Deterministic (coordinate-hashed)."""
    yy, xx = np.mgrid[0:h, 0:w]
    return _hash_unit(yy, xx, 73856093, 19349663) + _hash_unit(yy, xx, 83492791, 2839231) - 1.0


def _to_u8(rgb01: np.ndarray, dither: bool = True) -> np.ndarray:
    # round-to-nearest with ±1 LSB triangular dither to break up banding on smooth gradients
    x = np.clip(rgb01, 0.0, 1.0) * 255.0
    if dither and x.ndim == 3:
        x = x + _dither(x.shape[0], x.shape[1])[..., None]
    return np.clip(np.floor(x + 0.5), 0, 255).astype(np.uint8)


def _tiff_text_tags(exif: bytes) -> tuple[dict[int, str], str | None]:
    """The text tags a 16-bit TIFF carries from an export's EXIF block, and its software string."""
    src = PILImage.Exif()
    src.load(exif)
    text = {t: src.get(t) for t in (0x010F, 0x0110, 0x0132, 0x013B, 0x8298)}
    text[0x0132] = src.get_ifd(_EXIF_IFD).get(0x9003) or text[0x0132]  # capture time wins
    text = {t: v for t, v in text.items() if isinstance(v, str) and v}
    sw = src.get(0x0131)
    return text, sw if isinstance(sw, str) else None


_META_GROUPS = {
    "serial": _EXIF_SERIALS,
    "names": (0x013B, 0x8298, 0xA430),
    "time": (0x0132, 0x9003, 0x9004),
    "camera": (0x010F, 0x0110, 0xA434),
    "description": (0x010E, 0x9286),
}


def _present(exif: PILImage.Exif, tiff_only: set[int] | None = None) -> dict[str, bool]:
    tags = set(exif.keys()) | set(exif.get_ifd(_EXIF_IFD).keys())
    if tiff_only is not None:
        tags = set(tiff_only)   # exactly the tags the 16-bit TIFF writes (its DateTime can come from the capture time)
    out = {"gps": bool(exif.get_ifd(_GPS_IFD)) and tiff_only is None}
    out.update({g: bool(tags & set(t)) for g, t in _META_GROUPS.items()})
    return out


def metadata_report(source: str | None, mode: str, fmt: str) -> dict[str, dict[str, bool]]:
    """For each group of tags: is it in the source, and will this export carry it? Presence only,
    never a value. The export side is read from the bytes the export would write. Never raises
    for a bad source."""
    none = {g: {"source": False, "export": False} for g in ("gps", *_META_GROUPS)}
    if not source:
        export_exif(source, mode, (1, 1))     # still refuses an unknown mode
        return none
    try:
        if os.path.splitext(source)[1].lower() in _HEIF_EXT:
            _ensure_heif()
        with PILImage.open(source) as pil:
            src = pil.getexif()
            had = _present(src)
    except Exception:
        return none
    data = export_exif(source, mode, (1, 1))
    out_present = dict.fromkeys(had, False)
    if data:
        try:
            ex = PILImage.Exif()
            ex.load(data)
            only = None
            if fmt == "tiff":
                try:
                    import tifffile  # noqa: F401  (without it the TIFF falls back to 8-bit and keeps the block)
                    only = set(_tiff_text_tags(data)[0])
                except ImportError:
                    pass
            out_present = _present(ex, only)
        except Exception:
            pass
    return {g: {"source": had[g], "export": out_present[g]} for g in had}


def _save_tiff16(rgb01: np.ndarray, path: str, exif: bytes | None = None,
                 icc_profile: bytes | None = None) -> bool:
    try:
        import tifffile  # optional (extras: accel)
    except Exception:
        return False
    data = np.clip(rgb01, 0.0, 1.0)
    data = np.clip(np.floor(data * 65535.0 + 0.5), 0, 65535).astype(np.uint16)
    kw: dict = {}
    tags = [(34675, 7, len(icc_profile), icc_profile, True)] if icc_profile else []
    if exif:
        # ponytail: tifffile cannot write the Exif/GPS sub-directories (it filters tags 34665 and
        # 34853), so a 16-bit TIFF carries the first directory's text tags only. Exposure, lens
        # and GPS would need a hand-written sub-directory appender; add it when a TIFF user asks.
        text, software = _tiff_text_tags(exif)
        tags += [(t, 2, 0, v, True) for t, v in text.items()]
        if software:
            kw["software"] = software
    if tags:
        kw["extratags"] = tags
    tifffile.imwrite(path, data, photometric="rgb", **kw)
    return True
