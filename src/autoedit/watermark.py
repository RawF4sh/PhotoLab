"""A watermark on the export (F48): one line of text and/or a PNG logo laid over one corner.

Set only in an export preset (exports.toml). emit.write_image draws it after the resize and the
grain, right before the encoder, so no analysis, stat, gate or reveal check sees it. Pillow's
bundled font; nothing is downloaded."""

from __future__ import annotations

import os

import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from . import paths


def _font(px: int):
    """Pillow's bundled font at `px` pixels (Pillow 10.1 and later); Pillow 10.0 has only the small bitmap font."""
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def _text(text: str, height: int) -> PILImage.Image:
    """White text with a thin dark outline on a transparent ground, about `height` pixels high."""
    font = _font(height)
    stroke = max(1, height // 16)
    left, top, right, bottom = font.getbbox(text, stroke_width=stroke)
    im = PILImage.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((-left, -top), text, font=font, fill=(255, 255, 255, 255),
                            stroke_width=stroke, stroke_fill=(0, 0, 0, 160))
    if not isinstance(font, ImageFont.FreeTypeFont):     # the bitmap font has one size: scale the text
        im = im.resize((max(1, round(im.width * height / im.height)), height), PILImage.LANCZOS)
    return im


def mark(text: str, logo: PILImage.Image | None, height: int) -> PILImage.Image:
    """The mark alone (RGBA): the logo scaled to `height`, then the text, a third of `height` apart."""
    parts = []
    if logo is not None:
        parts.append(logo.resize((max(1, round(logo.width * height / logo.height)), height), PILImage.LANCZOS))
    if text:
        parts.append(_text(text, height))
    gap = height // 3
    out = PILImage.new("RGBA", (sum(p.width for p in parts) + gap * (len(parts) - 1),
                                max(p.height for p in parts)), (0, 0, 0, 0))
    x = 0
    for p in parts:
        out.alpha_composite(p, (x, (out.height - p.height) // 2))
        x += p.width + gap
    return out


def draw(rgb: np.ndarray, text: str = "", logo: PILImage.Image | None = None, corner: str = "bottom_right",
         size: float = 3.0, opacity: float = 0.7) -> np.ndarray:
    """`rgb` (float, 0..1, H x W x 3) with the mark laid over one corner. The mark is `size` percent of
    the short side high and one mark height from both edges; a mark too big for the picture is shrunk
    to fit. Every pixel outside the mark's box comes back unchanged."""
    if not text and logo is None:
        return rgb
    h, w = rgb.shape[:2]
    height = max(8, round(min(h, w) * size / 100.0))
    m = mark(text, logo, height)
    room_w, room_h = max(1, w - 2 * height), max(1, h - 2 * height)
    if m.width > room_w or m.height > room_h:
        k = min(room_w / m.width, room_h / m.height)
        m = m.resize((max(1, int(m.width * k)), max(1, int(m.height * k))), PILImage.LANCZOS)
    x0 = max(0, height if corner.endswith("left") else w - height - m.width)
    y0 = max(0, height if corner.startswith("top") else h - height - m.height)
    a = np.asarray(m, dtype=np.float32) / 255.0
    alpha = a[..., 3:4] * float(opacity)
    out = rgb.copy()
    box = out[y0:y0 + m.height, x0:x0 + m.width]
    out[y0:y0 + m.height, x0:x0 + m.width] = box * (1.0 - alpha) + a[..., :3] * alpha
    return out


def logo_file(name: str, profile_dir: str | None) -> str:
    """The logo a preset names: a file of that name in the profile folder, else in the configs folder."""
    for folder in (profile_dir, paths.configs_dir()):
        if folder and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    raise FileNotFoundError(f"watermark logo {name!r} not found: put the PNG file into your profile folder")


def args(cfg) -> dict | None:
    """`draw`'s arguments for a run config, or None when it asks for no mark."""
    if not (cfg.watermark or cfg.watermark_logo):
        return None
    logo = None
    if cfg.watermark_logo:
        with PILImage.open(logo_file(cfg.watermark_logo, cfg.profile_dir)) as im:
            if im.format != "PNG":
                raise ValueError(f"watermark logo {cfg.watermark_logo!r} is not a PNG file")
            logo = im.convert("RGBA")
    return {"text": cfg.watermark or "", "logo": logo, "corner": cfg.watermark_corner,
            "size": cfg.watermark_size, "opacity": cfg.watermark_opacity}


def record(cfg) -> dict:
    """What the sidecar keeps about the mark: the settings and the logo's file name, never a folder."""
    return {"text": cfg.watermark or "", "logo": cfg.watermark_logo, "corner": cfg.watermark_corner,
            "size": cfg.watermark_size, "opacity": cfg.watermark_opacity}
