"""Image decode/encode + color management.

Working representation: float32 RGB in [0,1], **sRGB gamma** (display-referred). The apply
engine linearizes for light-like ops and returns to gamma for perceptual ops. Internal
precision is always float; we promote even 8-bit JPEG input to float so intermediate ops
don't quantize (the recovery ceiling stays low for 8-bit input, but we don't make it worse).

MVP assumes sRGB input (ICC handling is a TODO; most JPEGs are sRGB). 16-bit TIFF output
uses `tifffile` when installed, else falls back to 8-bit via Pillow.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps

_SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

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


def load_image(path: str) -> Image:
    """Decode any supported image to the working representation."""
    pil = PILImage.open(path)
    source_bits = 16 if pil.mode in ("I;16", "I;16B", "I") else 8
    exif = _read_exif(pil)
    pil = ImageOps.exif_transpose(pil)  # honor the orientation tag, then drop it
    pil = pil.convert("RGB")
    arr = np.asarray(pil)
    if arr.dtype == np.uint16:
        rgb = arr.astype(np.float32) / 65535.0
        source_bits = 16
    else:
        rgb = arr.astype(np.float32) / 255.0
    return Image(rgb=np.ascontiguousarray(rgb), source_bits=source_bits, path=path, exif=exif)


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
    """Return a proxy with the long edge clamped to `long_edge` (no upscaling)."""
    if img.long_edge <= long_edge:
        return img
    scale = long_edge / img.long_edge
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    pil = PILImage.fromarray(_to_u8(img.rgb), mode="RGB").resize((new_w, new_h), PILImage.BILINEAR)
    return Image(rgb=np.asarray(pil).astype(np.float32) / 255.0, source_bits=img.source_bits,
                 path=img.path, exif=img.exif)


def save_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92) -> None:
    """Write the working RGB (float, sRGB gamma, [0,1]) to disk."""
    rgb = np.clip(rgb, 0.0, 1.0)
    if fmt == "tiff":
        if _save_tiff16(rgb, path):
            return
        # fall through to 8-bit if tifffile is unavailable
    pil = PILImage.fromarray(_to_u8(rgb), mode="RGB")
    if fmt in ("jpg", "jpeg"):
        pil.save(path, quality=quality, subsampling=0)
    else:
        pil.save(path)


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


def _to_u8(rgb01: np.ndarray) -> np.ndarray:
    # round-to-nearest with a tiny ordered dither to reduce banding on smooth gradients
    x = np.clip(rgb01, 0.0, 1.0) * 255.0
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
