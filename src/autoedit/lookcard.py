"""Look cards (F28): one before-and-after sheet (PNG and HTML) that shows what a look does.

The edit is the product's own (`Pipeline.process_one` with the look, rules deciding), on a copy of the
photo at 1600 px. A card holds pictures, the look's name, `before`, `after` and one line with the version
and the segmenter: no file name and no value of the look.
"""

from __future__ import annotations

import base64
import html
import io
import os

import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from . import __version__, batch, emit, lookfile
from .edit import ops
from .imageio import Image, _to_u8, downscale_long_edge, is_supported, load_image, raw_opts_from, resize_long_edge

MAX_PHOTOS = 12
RENDER_LONG_EDGE = 1600     # the edit is rendered at this size, as `autoedit calibrate` renders its pairs
PAGE_LONG_EDGE = 1200       # a picture in the HTML page
CELL = (480, 360)           # the box of a picture on the PNG sheet
_COLOURS = np.array([[1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 1, 1], [0, 0, 1], [1, 0, 1]], np.float32)


def sample_frame(w: int = 1200, h: int = 800) -> np.ndarray:
    """A drawn frame for a card without photos. Nothing in it is near white or black: no packaged look is flagged on it."""
    yy, xx = np.mgrid[0:h, 0:w]
    y, x = (yy / h).astype(np.float32), (xx / w).astype(np.float32)
    t = np.clip(y / 0.55, 0.0, 1.0)[..., None]
    a = (1 - t) * np.float32([0.22, 0.40, 0.72]) + t * np.float32([0.55, 0.66, 0.78])            # sky
    g = np.clip((y - 0.55) / 0.33, 0.0, 1.0)[..., None]
    a = np.where((y >= 0.55)[..., None], (1 - g) * np.float32([0.35, 0.50, 0.22]) + g * np.float32([0.14, 0.26, 0.10]), a)
    a[((x - 0.5) / 0.13) ** 2 + ((y - 0.52) / 0.2) ** 2 <= 1.0] = [0.80, 0.60, 0.48]              # a skin tone
    a = a + np.random.RandomState(7).normal(0.0, 0.012, (h, w, 1))                                 # a little texture
    ramp = np.repeat((0.04 + 0.71 * np.clip(x / 0.5, 0.0, 1.0))[..., None], 3, axis=-1)
    patches = 0.15 + 0.45 * _COLOURS[np.clip(((x - 0.5) * 12).astype(int), 0, 5)]
    a = np.where((y >= 0.88)[..., None], np.where((x < 0.5)[..., None], ramp, patches), a)   # a grey ramp, six patches
    return np.clip(a, 0.0, 1.0).astype(np.float32)


def pick(photos: list[str]) -> list[str]:
    """The photos named (files, and the photos of folders), at most MAX_PHOTOS, spread over the list."""
    files: list[str] = []
    for p in photos:
        if os.path.isdir(p):
            files += batch.list_images(p)
        elif os.path.isfile(p) and is_supported(p):
            files.append(p)
        else:
            raise ValueError(f"not a photo or a folder: {p}")
    if photos and not files:
        raise ValueError("no photo in the folders that were named")
    n = len(files)
    if n > MAX_PHOTOS:
        files = [files[i * (n - 1) // (MAX_PHOTOS - 1)] for i in range(MAX_PHOTOS)]
    return files


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _sheet(name: str, pairs: list, foot: str) -> PILImage.Image:
    per_row = 1 if len(pairs) <= 3 else 2
    rows = -(-len(pairs) // per_row)
    sheet = PILImage.new("RGB", (12 + per_row * 984, 60 + rows * 396 + 24), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    ink = (225, 225, 225)
    draw.text((12, 14), name, fill=ink, font=_font(28))
    for i, pair in enumerate(pairs):
        x0, y0 = 12 + (i % per_row) * 984, 60 + (i // per_row) * 396
        for k, (arr, label) in enumerate(zip(pair, ("before", "after"))):
            im = PILImage.fromarray(arr, "RGB")
            im.thumbnail(CELL, PILImage.LANCZOS)
            bx = x0 + k * 492
            sheet.paste(im, (bx + (CELL[0] - im.width) // 2, y0 + (CELL[1] - im.height) // 2))
            draw.text((bx, y0 + CELL[1] + 4), label, fill=ink, font=_font(14))
    draw.text((12, sheet.height - 24), foot, fill=(150, 150, 150), font=_font(13))
    return sheet


def _jpeg_uri(arr: np.ndarray) -> str:
    buf = io.BytesIO()
    PILImage.fromarray(arr, "RGB").save(buf, "JPEG", quality=88, subsampling=0)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _page(name: str, pairs: list, foot: str) -> str:
    figs = []
    for i, (before, after) in enumerate(pairs, 1):
        figs.append(f'<figure><div><img alt="Photo {i}, before" src="{_jpeg_uri(before)}">'
                    f'<img alt="Photo {i}, with the look" src="{_jpeg_uri(after)}"></div>'
                    "<figcaption><span>before</span><span>after</span></figcaption></figure>")
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; "
            "style-src 'unsafe-inline'\">"
            f"<title>{html.escape(name)}</title><style>"
            "body{background:#121214;color:#e1e1e1;font:16px system-ui,sans-serif;margin:16px}"
            "figure{margin:0 0 24px}figure div{display:grid;grid-template-columns:1fr 1fr;gap:8px}"
            "img{width:100%;height:auto}figcaption{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:14px}"
            "footer{color:#969696;font-size:13px}</style></head><body>"
            f"<h1>{html.escape(name)}</h1>" + "".join(figs) + f"<footer>{html.escape(foot)}</footer></body></html>\n")


def render(name: str, photos: list[str], out_dir: str, cfg, log=None) -> dict:
    """Write <out_dir>/<name>.png and <name>.html for look `name` of `cfg`'s library. Returns
    {"png", "html", "photos", "flagged": [(photo number, the review gate's reason)], "backend"}."""
    from .pipeline import Pipeline
    lookfile.check_name(name)       # it becomes a file name
    # The rules decide, whatever the profile says: a card can be made again, and no photo leaves the machine.
    pipe = Pipeline.build(cfg.model_copy(update={"look": name, "decider": "rules"}))
    if name not in pipe.looks:
        raise ValueError(f"look {name!r} not found (known: {', '.join(pipe.looks)})")
    files = pick(photos)
    pairs, flagged = [], []
    for i, path in enumerate(files or [None], 1):
        if log:
            log(f"card: photo {i} of {max(len(files), 1)}")
        img = (downscale_long_edge(load_image(path, decode=raw_opts_from(pipe.cfg)), RENDER_LONG_EDGE)
               if path else Image(rgb=sample_frame()))
        rgb, res = pipe.process_one(path or "sample", image=img)
        grain = emit.grain_args(res, pipe.cfg)
        after = resize_long_edge(rgb, PAGE_LONG_EDGE)
        pairs.append((_to_u8(resize_long_edge(img.rgb, PAGE_LONG_EDGE)),
                      _to_u8(ops.grain(after, *grain) if grain else after)))
        if res.needs_review:
            flagged.append((i, res.review_reason))
    os.makedirs(out_dir, exist_ok=True)
    foot = f"AutoEdit {__version__}, segmenter: {pipe.cfg.backend}"
    out = {"png": os.path.join(out_dir, name + ".png"), "html": os.path.join(out_dir, name + ".html"),
           "photos": len(files), "flagged": flagged, "backend": pipe.cfg.backend}
    _sheet(name, pairs, foot).save(out["png"])
    with open(out["html"], "w", encoding="utf-8") as fh:
        fh.write(_page(name, pairs, foot))
    return out
