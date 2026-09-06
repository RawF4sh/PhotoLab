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

import os
from dataclasses import dataclass

import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps

# Camera RAW formats — decoded via rawpy/libraw (the `accel` extra) at 16-bit.
_RAW_EXT = {".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2", ".srf", ".raf",
            ".orf", ".rw2", ".pef", ".srw", ".raw", ".3fr", ".mrw", ".dcr", ".kdc"}
_PIXEL_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
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

    @property
    def height(self) -> int:
        return self.rgb.shape[0]

    @property
    def width(self) -> int:
        return self.rgb.shape[1]

    @property
    def long_edge(self) -> int:
        return max(self.height, self.width)


def is_supported(path: str) -> bool:
    return any(path.lower().endswith(ext) for ext in _SUPPORTED)


def is_raw(path: str) -> bool:
    """True for a camera-RAW path (.dng/.cr2/.nef/.arw/…) — used to pair RAW+JPEG and to
    prefer the RAW as the edit source (more bit depth / latitude)."""
    return path.lower().endswith(tuple(_RAW_EXT))


def load_image(path: str, decode: dict | None = None) -> Image:
    """Decode any supported image to the working representation, preserving bit depth.

    `decode` holds the RAW development options (RunConfig `raw_*` fields, see `raw_opts_from`);
    it is ignored for non-RAW input, whose bytes are unchanged by this parameter.

    Order matters: read 16-bit data BEFORE any RGB conversion (Pillow's `convert("RGB")`
    collapses high-bit-depth data to 8-bit), and convert ICC → sRGB for 8-bit inputs so the
    working space is defined rather than assumed."""
    # 0) Camera RAW (.dng/.cr2/.nef/.arw/…) → demosaic at 16-bit via rawpy/libraw.
    raw = _decode_raw(path, decode)
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
                     decode_ev=ev)

    pil = PILImage.open(path)
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
    if arr.dtype == np.uint16:          # 48-bit RGB Pillow kept as uint16
        rgb, bits = arr.astype(np.float32) / 65535.0, 16
    else:
        rgb, bits = arr.astype(np.float32) / 255.0, 8
    return Image(rgb=np.ascontiguousarray(np.clip(rgb, 0.0, 1.0)), source_bits=bits, path=path, exif=exif)


# RAW development modes (RunConfig.raw_mode). "none" is the historical libraw render — camera WB,
# no auto-bright, plain gamma — and stays the default. Measured on 110 Dolomites CR2s it sits a
# median 1.4–1.8 EV under the camera's OWN embedded JPEG, so the other modes are candidates that
# lift the LINEAR demosaic before gamma; scripts/raw_benchmark.py ranks them, nothing flips the
# default automatically.
RAW_MODES = ("none", "libraw_auto", "preview_ev", "preview_ev_headroom", "tone_target")
_RAW_DEFAULTS = {"raw_mode": "none", "raw_max_ev": 2.5, "raw_clip_budget": 0.005,
                 "raw_tone_target_p50": 0.40, "raw_fallback_ev": 1.4}
# Headroom-capped modes stop this many stops short of the clip level, so the pipeline's own
# whites/shadows/sharpen (which run AFTER decode) don't push the top percentile into white.
# Mirrors the `headroom_ev - 0.15` margin the exposure rules use (configs/rules.toml).
_HEADROOM_MARGIN_EV = 0.15


def raw_opts_from(cfg) -> dict:
    """The RAW development options a RunConfig carries (its raw_* fields), shaped for
    `load_image(decode=...)`. Works on any object exposing those attributes."""
    return {k: getattr(cfg, k) for k in _RAW_DEFAULTS if hasattr(cfg, k)}


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
    headroom = max(0.0, float(np.log2(white / max(float(np.percentile(lum_lin, clip_q)), 1e-5)))
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
        else:
            pil = PILImage.fromarray(np.asarray(t.data)).convert("RGB")
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
    if like is not None and (like.shape[0] > like.shape[1]) != (rgb.shape[0] > rgb.shape[1]):
        rgb = np.rot90(rgb)
    return np.ascontiguousarray(rgb)


def _decode_raw(path: str, opts: dict | None = None):
    """Demosaic a camera RAW to 16-bit via rawpy/libraw. Returns (rgb[0,1], 16, decode_ev) or
    None when the path isn't a RAW. Camera white balance always. `opts` = RunConfig raw_* fields
    (see RAW_MODES); an extra `half_size` key is a benchmark speed knob (quarter-res demosaic).
    "none" reproduces the historical render byte-for-byte; the lifting modes decode LINEAR, apply
    `develop_ev` stops, then encode with the sRGB curve (our working space)."""
    if not path.lower().endswith(tuple(_RAW_EXT)):
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
    common = dict(output_bps=16, use_camera_wb=True, half_size=bool(o.get("half_size", False)))
    with rawpy.imread(path) as raw:
        if mode in ("none", "libraw_auto"):
            rgb16 = raw.postprocess(
                no_auto_bright=(mode == "none"),  # "none": feed the true exposure, no stretch
                gamma=(2.222, 4.5),                # sRGB-ish tone curve → our gamma working space
                **common,
            )
            return np.clip(rgb16.astype(np.float32) / 65535.0, 0.0, 1.0), 16, 0.0
        lin = raw.postprocess(no_auto_bright=True, gamma=(1.0, 1.0), **common)
        lin = lin.astype(np.float32) / 65535.0
        cam = _preview_rgb(raw) if mode.startswith("preview") else None
    ev = develop_ev(lin, cam, o)
    if ev:
        lin = lin * np.float32(2.0 ** ev)
    return linear_to_srgb(np.clip(lin, 0.0, 1.0)), 16, ev


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
        return np.clip(arr.astype(np.float32) / 65535.0, 0.0, 1.0), 16
    if arr.dtype == np.uint8:
        return np.clip(arr.astype(np.float32) / 255.0, 0.0, 1.0), 8
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
    """Pull a small, normalized prior dict from EXIF: {'iso': float, 'exposure_ev': float}.
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
    return out


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
                 decode_ev=img.decode_ev)


def save_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92) -> None:
    """Write the working RGB (float, sRGB gamma, [0,1]) to disk.

    Atomic: encode to a sibling temp file (same extension, so PIL/tifffile infer the format
    identically to a direct write), then os.replace into place. A crash mid-write leaves the
    old file or nothing — never a half-written image a resume would mistake for complete."""
    rgb = np.clip(rgb, 0.0, 1.0)
    tmp = f"{path}.part{os.path.splitext(path)[1]}"      # e.g. out.jpg.part.jpg
    if fmt == "tiff":
        if _save_tiff16(rgb, tmp):
            os.replace(tmp, path)
            return
        # fall through to 8-bit if tifffile is unavailable
    pil = PILImage.fromarray(_to_u8(rgb), mode="RGB")
    if fmt in ("jpg", "jpeg"):
        pil.save(tmp, quality=quality, subsampling=0)
    else:
        pil.save(tmp)
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


def _save_tiff16(rgb01: np.ndarray, path: str) -> bool:
    try:
        import tifffile  # optional (extras: accel)
    except Exception:
        return False
    data = np.clip(rgb01, 0.0, 1.0)
    data = np.clip(np.floor(data * 65535.0 + 0.5), 0, 65535).astype(np.uint16)
    tifffile.imwrite(path, data, photometric="rgb")
    return True
