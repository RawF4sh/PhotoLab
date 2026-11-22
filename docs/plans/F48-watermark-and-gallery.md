# F48. Watermark and gallery

Status: spec (2026-11-19). Size M. Approved by the owner in chat on 2026-11-19. Default output
stays byte-identical: no packaged preset carries a mark, and the gallery is a new command.

## Goal

1. **Watermark.** An export preset can lay a mark over one corner of the exported photo: one line
   of text, a PNG logo, or both. It is off unless a preset names it.
2. **Gallery.** `autoedit gallery FOLDER [-o DIR]` writes a static page of a run's exports:
   thumbnails that link to the full photos, with the file names as captions.

## What exists, and what is reused

| Exists | Used for |
|---|---|
| Export presets (F10): `exports.toml`, `exports.preset_kwargs`, applied by `cli.load_run_config` and `service.make_cfg` | The five watermark keys; nothing else sets them |
| `emit.write_image` lays grain over the export after the resize, right before the encoder (F23) | The mark goes directly after the grain |
| The sidecar's notes and `audit`; `declare._steps` (F42) | Recording the mark and declaring it |
| `audit.run_config` replayed by `autoedit adjust` (F3) | A re-render keeps the mark |
| `search.index` (F41): every sidecar under a folder, its export and its reveal findings | The gallery's list of photos |
| `search.keywords`, `search.skip_words` (F41): words that may travel with a photo, never a reveal kind | The thumbnails' alt text |
| `reveal._thumb` and the rules of `reveal.write_page` (F21): 640 px JPEG thumbnails, everything escaped, relative links, no script | Thumbnails and the page |

Not reusable: `curate._write_contact_sheet` writes absolute source paths into its page, and
`pick._sheet` is built around bursts.

Font: Pillow's bundled font, `ImageFont.load_default(size=px)`. It is scalable since Pillow 10.1;
checked here on Pillow 12.1 (system Python) and 13.0 (`.venv`), both with FreeType. The project
needs `Pillow>=10`, so Pillow 10.0 falls back to the small bitmap font, scaled up. Nothing is
downloaded.

## Design: the watermark

### Settings (`contract.py`, after `out_aspect`)

```python
    # A watermark (F48): set only by an export preset (exports.toml); drawn on the export alone,
    # after the resize and the grain.
    watermark: str | None = Field(default=None, max_length=100)  # one line of text laid over a corner; None = no text
    watermark_logo: str | None = Field(default=None, max_length=100)  # file name of a PNG in the profile folder (else the configs folder); never a folder
    watermark_corner: Literal["bottom_right", "bottom_left", "top_right", "top_left"] = "bottom_right"
    watermark_size: float = Field(default=3.0, ge=1.0, le=20.0)  # height of the mark in percent of the export's short side
    watermark_opacity: float = Field(default=0.7, ge=0.1, le=1.0)
```

```python
    @field_validator("watermark")
    @classmethod
    def _check_watermark(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("watermark must be one line of text, without control characters")
        return v

    @field_validator("watermark_logo")
    @classmethod
    def _check_watermark_logo(cls, v: str | None) -> str | None:
        if not v:
            return None
        if v.startswith(".") or not re.fullmatch(r"[^/\\:]{1,96}\.png", v, re.IGNORECASE):
            raise ValueError(f"watermark_logo {v!r} must be the file name of a PNG in your profile folder, "
                             "without a folder (for example logo.png)")
        return v
```

`exports._KEYS` gains `"watermark"`, `"watermark_logo"`, `"watermark_corner"`, `"watermark_size"`
and `"watermark_opacity"`, each mapped to the field of the same name. No CLI flag, no `[output]`
key and no web option (`service._ALLOWED`) sets them.

### `watermark.py` (new)

The drawing was run on both Pillow versions before this spec was written. The changed pixels sat
in the chosen corner, the text was 22 px high for a 24 px target, every pixel outside the mark
was unchanged, and a long text on a 90 by 60 picture was shrunk to fit.

```python
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
```

### `emit.py`

Import `watermark as watermark_mod` next to `geometry as geometry_mod`.

- `write_image(..., geometry=None, watermark: dict | None = None)`. Directly after the grain:
  `if watermark: rgb = watermark_mod.draw(rgb, **watermark)`. Add one sentence to the docstring:
  "`watermark` (see `watermark.args`, F48) is laid over the picture after the grain, right before
  the encoder."
- `write_outputs`, inside `if out_rgb is not None:`:

```python
        mark = watermark_mod.args(cfg)      # a missing logo raises: the photo fails, never an export without its mark
        note = write_image(out_rgb, image_out, cfg.out_format, cfg.quality,
                           source=source, metadata=cfg.metadata, long_edge=cfg.out_long_edge, grain=grain,
                           geometry=result.geometry, watermark=mark)
        ...                                 # the existing note and grain lines
        if mark:
            result.audit["watermark"] = watermark_mod.record(cfg)
            result.notes.append(f"watermark: laid over the {cfg.watermark_corner.replace('_', ' ')} corner of "
                                "the export after the checks, the resize and the grain; no measurement sees it")
```

No other caller of `write_image` passes a mark: merged frames, measurement copies and contact
sheets stay as they are.

### `declare.py`

```python
WATERMARK_NOTE = ("Exports listed with a watermark carry a mark that the photographer set in an export preset: "
                  "text or a logo laid over one corner, after every other step.")
```

- At the end of `_steps(r)`:

```python
    wm = a.get("watermark")
    if wm:
        what = " and ".join(p for p in ((f"the text {wm['text']!r}" if wm.get("text") else ""),
                                        (f"the logo {wm['logo']}" if wm.get("logo") else "")) if p)
        out.append(f"watermark: {what} laid over the {str(wm.get('corner', '')).replace('_', ' ')} corner of the export")
```

- `declaration()` returns `"watermark": r.audit.get("watermark")` as well.
- `_page`: right after the statement paragraph, `<p>{e(WATERMARK_NOTE)}</p>` when any declared
  photo has a watermark. `STATEMENT` itself is unchanged.

### `configs/settings.toml`

Insert after `[setting.out_aspect]`:

```toml
[setting.watermark]
label = "Watermark text"
group = "output"
help = "One line of text laid over a corner of the export. Set in an export preset. Empty: no mark."

[setting.watermark_logo]
label = "Watermark logo"
group = "output"
help = "The file name of a PNG in your profile folder, laid beside the watermark text. Empty: no logo."

[setting.watermark_corner]
label = "Watermark corner"
group = "output"
help = "The corner of the export the watermark sits in."

[setting.watermark_size]
label = "Watermark size"
group = "output"
help = "Height of the watermark in percent of the export's shorter side, 1 to 20."

[setting.watermark_opacity]
label = "Watermark opacity"
group = "output"
help = "How solid the watermark is, from 0.1 (faint) to 1 (solid)."
```

Then regenerate the snapshot:
`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`.

### `configs/exports.toml`

Append to the header comment, before `[full]`:

```toml
# A watermark (F48), off unless a preset names it: watermark (one line of text), watermark_logo
# (the file name of a PNG in your profile folder, no folder), watermark_corner ("bottom_right",
# "bottom_left", "top_right" or "top_left"; default bottom_right), watermark_size (height in
# percent of the export's short side, 1 to 20; default 3), watermark_opacity (0.1 to 1; default
# 0.7). No preset here carries one. Put your own into the exports.toml of your profile, e.g.:
# [marked]
# format = "jpeg"
# quality = 90
# long_edge = 2048
# name = "{stem}_marked"
# subdir = "marked"
# watermark = "© Your Name"
```

## Design: the gallery

### `gallery.py` (new)

```python
"""A static page of a run's exports (F48): thumbnails that link to the full photos, the file names as captions.

Built from the sidecars (search.index). No script, no outside resource, no absolute path. The full
photos are linked where they are, never copied or embedded. A photo with reveal findings (F21) keeps
its tile and gets a visible warning that names the kinds."""

from __future__ import annotations

import hashlib
import html
import os
import re
from urllib.parse import quote

from . import reveal, search

PAGE, THUMBS = "gallery.html", "gallery"


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def write(folder: str, out: str | None = None, min_coverage: float = 0.02, skip=None) -> dict:
    """Write gallery.html and gallery/<thumbnails> into `out` (default: the folder). Returns {"page",
    "photos", "warned", "skipped"}; skipped counts sidecars without an export and unreadable ones."""
    out = os.path.abspath(out or folder)
    entries, broken = search.index(folder)
    shown = [e for e in entries if e["image"] and os.path.isfile(e["image"])]
    skip = search.skip_words() if skip is None else skip
    os.makedirs(os.path.join(out, THUMBS), exist_ok=True)
    tiles, warned = [], 0
    for e in shown:
        image = os.path.abspath(e["image"])
        rel = os.path.relpath(image, out).replace(os.sep, "/")
        name = os.path.basename(image)
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(name)[0])[:60]
        # The same export keeps the same thumbnail name, so a second run overwrites it in place.
        thumb = f"{THUMBS}/{stem}-{hashlib.blake2b(rel.encode('utf-8'), digest_size=4).hexdigest()}.jpg"
        alt = ", ".join(search.keywords(e, min_coverage, skip)) or name
        pic = (f'<img src="{quote(thumb)}" alt="{_e(alt)}" loading="lazy">'
               if reveal._thumb(image, os.path.join(out, thumb)) else "<span>no preview</span>")
        warn = ""
        if e["reveal_labels"]:
            warned += 1
            warn = f'<p class="warn">Check before sharing: {_e(", ".join(e["reveal_labels"]))}</p>'
        tiles.append(f'<figure><a href="{quote(rel)}">{pic}</a><figcaption>{_e(name)}</figcaption>{warn}</figure>')
    title = _e(os.path.basename(os.path.abspath(folder)) or "Photos")
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title><style>"
            "body{margin:0;padding:1rem;font:15px/1.4 system-ui,sans-serif;background:#111;color:#eee}"
            "h1{font-size:1.2rem;font-weight:600}"
            "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,16rem),1fr));gap:1rem}"
            "figure{margin:0}img{display:block;width:100%;height:auto}a{color:inherit}"
            "figcaption{margin-top:.3rem;font-size:.85rem;color:#bbb;overflow-wrap:anywhere}"
            ".warn{margin:.3rem 0 0;padding:.2rem .4rem;background:#fc3;color:#111;font-size:.85rem}"
            f"</style></head><body><h1>{title}</h1><main>{''.join(tiles)}</main></body></html>\n")
    path = os.path.join(out, PAGE)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return {"page": path, "photos": len(shown), "warned": warned, "skipped": len(entries) - len(shown) + broken}
```

On Windows, `os.path.relpath` raises `ValueError` when `-o` is on another drive. The CLI reports it.

### `cli.py`

```python
def _cmd_gallery(args) -> int:
    from . import gallery, search

    if not os.path.isdir(args.folder):
        print(f"folder not found: {args.folder}", file=sys.stderr)
        return 2
    try:
        r = gallery.write(args.folder, args.out, _min_coverage(args),
                          search.skip_words(args.configs_dir, args.profile))
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(r["page"])
    print(f"{r['photos']} photos" + (f", {r['warned']} with a reveal warning on the tile" if r["warned"] else "")
          + (f"; {r['skipped']} sidecars without an export skipped" if r["skipped"] else ""))
    return 0
```

Register it after `declare`:

```python
    gal_p = sub.add_parser("gallery", help="write gallery.html: thumbnails of a run's exports that link to the full photos")
    gal_p.add_argument("folder")
    gal_p.add_argument("-o", "--out", help="where gallery.html and gallery/ go (default: the folder)")
    gal_p.add_argument("--configs-dir", dest="configs_dir")
    gal_p.add_argument("--profile", help="profile folder with your own config differences")
    gal_p.set_defaults(func=_cmd_gallery, min_coverage=None)
```

Module docstring, after the `declare` line:

```
  autoedit gallery FOLDER [-o DIR]         write a static page of a run's exports
```

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder (Sonnet, medium) | The watermark: `contract.py`, `exports.py`, `watermark.py`, `emit.py`, `declare.py`, `configs/settings.toml`, `configs/exports.toml`, the regenerated `tests/snapshots/settings_schema.json`, `tests/test_watermark.py` | `PYTHONPATH=src python3 -m pytest tests/test_watermark.py tests/test_export_presets.py tests/test_emit.py tests/test_declare.py tests/test_settings_schema.py tests/test_adjust.py -q` |
| T2 | Builder (Sonnet, medium) | The gallery: `gallery.py`, `cli.py`, `tests/test_gallery.py` | `PYTHONPATH=src python3 -m pytest tests/test_gallery.py tests/test_search.py tests/test_cli.py -q` |
| T3 | The session, inline | The README and CHANGELOG lines below | none |

T1 and T2 share no file and may run at the same time. T2 edits `cli.py`, so it does not run at the
same time as F47's T2.

README, in the "Use" block, after the `--export web` line:

```
autoedit run ./photos ./out --export marked                    # your own preset with watermark = "© Your Name" (exports.toml in your profile)
```

and after the `autoedit declare` line:

```
autoedit gallery ./out                                          # gallery.html: thumbnails that link to the full photos
```

CHANGELOG, under "Added":

```
- **A watermark in an export preset** (F48): `watermark = "© Your Name"`, and optionally
  `watermark_logo = "logo.png"` from your profile, lays a mark over one corner of the export after
  every other step. Off unless a preset names it; the sidecar and `autoedit declare` name it.
- **`autoedit gallery FOLDER [-o DIR]`** (F48): `gallery.html`, thumbnails that link to the full
  exports, with the file names as captions. No script, no outside resource, no absolute path. A
  photo with reveal findings (F21) keeps its tile and shows a warning.
```

## Tests

### `tests/test_watermark.py` (T1)

| # | Test | Asserts |
|---|---|---|
| 1 | `test_draw_places_the_mark_in_a_corner` | On a flat 0.4 grey 800 by 1200 picture, `draw(rgb, "© Test 2026")`: every changed pixel lies in the bottom-right quarter and at least 24 px (3 % of 800) from the right and bottom edges; the changed box is 12 to 27 px high; every other pixel is equal to the input; the result is float32 within 0 and 1. With `corner="top_left"` the changed box starts at least 24 px from the top and left edges |
| 2 | `test_opacity_and_fit` | `opacity=1.0` reaches at least 0.99 inside the mark; `opacity=0.3` on 0.4 grey stays at or below 0.6; a 100-character text on a 90 by 60 picture changes pixels only inside the picture, none in the outermost row or column |
| 3 | `test_logo` | A 40 by 20 red PNG `logo.png` in a profile folder: `args(RunConfig(watermark_logo="logo.png", profile_dir=p))` then `draw` gives red pixels (r > 0.6, g < 0.3) in the bottom-right corner; with `watermark="x"` too, both red and near-white pixels appear. `logo_file("nope.png", p)` raises `FileNotFoundError` naming the file. A JPEG saved as `logo.png` raises `ValueError` |
| 4 | `test_validation` | `RunConfig` rejects `watermark="a\nb"`, `watermark_size` 0.5 and 25, `watermark_opacity` 0 and 1.5, `watermark_corner="middle"`, and `watermark_logo` values `"../logo.png"`, `"a/logo.png"`, `"logo.jpg"`, `".png"`. `""` and `"  "` become `None`. `exports.preset_kwargs` passes the five keys of a preset through |
| 5 | `test_mark_on_the_export_only` | A stub `batch.run` of one synthetic photo (as `tests/test_adjust.py::_make`) with `out_format="png"`, once without and once with `watermark="© Test"`. The two exports differ, and only in the bottom-right quarter. `global_stats`, `post_edit_stats` and `quality_score` are equal in both sidecars. The marked sidecar has `audit.watermark.text == "© Test"` and a note starting with `watermark:`. `adjust.rerender` of the marked export is byte-identical. Through the CLI: a profile folder whose `exports.toml` holds `[marked]` with `format = "png"` and `watermark = "© Test"`, then `cli.main(["run", src, out, "--export", "marked", "--profile", prof])`, gives an export that differs from an unmarked one in the bottom-right quarter |
| 6 | `test_no_mark_by_default` | `watermark.args(RunConfig())` is None. A default run's sidecar has no `audit.watermark` and no note starting with `watermark:`. Every packaged preset leaves `watermark` and `watermark_logo` None |
| 7 | `test_declaration_lists_the_mark` | `declare.declaration` of the marked sidecar from test 5 has a step starting with `watermark:` and a `"watermark"` entry; `declare.write` puts `WATERMARK_NOTE` into the page. For an unmarked run, neither appears |

### `tests/test_gallery.py` (T2)

| # | Test | Asserts |
|---|---|---|
| 1 | `test_gallery_page` | A stub run of two synthetic photos into `out`, and a dry run (`do_apply=False`) of one photo into `out/dry`. `gallery.write(out)` gives `photos == 2` and `skipped == 1`. The page has two `<figure>`. Every `img src` names a file under `out/gallery/`. Every `a href`, unquoted and joined to `out`, is an export. The page holds no `http`, `<script`, `base64` or `str(tmp_path)`. A second `write` gives the same thumbnail file names |
| 2 | `test_gallery_escapes_and_warns` | A source named `x<&>.jpg`: its caption is escaped (`x&lt;&amp;&gt;.jpg`) and its `href` is percent-encoded. One sidecar edited by hand to hold `"reveal": {"picture": "checked", "findings": [{"kind": "plate", "label": "number plate", "word": "plate", "score": 0.9, "box": [0, 0, 0.1, 0.1], "share": 0.01}]}`: that tile has `class="warn"` with `number plate`, the other tile has none, and `warned == 1` |
| 3 | `test_gallery_elsewhere` | `gallery.write(out, tmp_path / "share")`: every `href` starts with `../` and resolves to an export; the thumbnails are under `share/gallery/` |
| 4 | `test_cli_gallery` | `cli.main(["gallery", out])` returns 0 and prints a line ending in `gallery.html` and a line starting with `2 photos`; a missing folder returns 2 |

## Acceptance checks (the session runs these)

1. Both suites green, on the system Python and on `.venv`.
2. The stub smoke run from `CLAUDE.md` completes with sidecars and a manifest.
3. A default render of the reference photos is byte-identical to the baseline. Sidecars gain the
   five watermark settings under `audit.run_config`.
4. A profile with a `[marked]` preset (`watermark = "© Test"`, and in a second run a
   `watermark_logo`): `autoedit run test-corpus OUT --export marked` puts the mark into the export's
   corner. `post_edit_stats` equal those of a run without the mark. `autoedit declare OUT` lists it.
   `autoedit adjust` on one export reproduces its bytes.
5. `autoedit gallery OUT`, opened in the Browser pane: the thumbnails show, a click opens the full
   photo, and the layout has no sideways scroll at phone width. The page holds no `http`, no
   `<script` and no home-folder path.
6. The Checker reviews F48 (size M, and the content-rule decision).

## Known limits

- The bundled font has Latin letters only; other scripts show as boxes. On Pillow 10.0 the text is
  the bitmap font scaled up, so it looks blocky.
- Studio's preview shows no mark. Only the export has it.
- A missing logo fails every photo of the run, each with the same message.
- The gallery links a TIFF export as it is; many browsers do not show TIFF.
- A tile warns about picture findings only. File metadata in the export (GPS with
  `metadata = "keep"`) is on `reveal.html`, not on the tile.
- Thumbnails of photos that are no longer in the folder stay in `gallery/` until deleted.
- Each run of `gallery` makes every thumbnail again. Skip a thumbnail newer than its export when
  large galleries are re-made often.
- The comment block changes `exports.toml`, so a re-render of an export made before F48 warns that
  the config files differ, as after any config change.

## Decisions of the owner

`FEATURE_PLAN.md` lists "watermarks laid over the photo" under the ideas that need an owner
decision under the content rule.

| Decision | Chosen | Reverse it by |
|---|---|---|
| May AutoEdit lay a mark over the photo (content rule)? | **The owner's yes**, given in chat on 2026-11-19, limited to a mark the owner sets themselves in an export preset. No packaged preset carries one | remove the five keys from `exports._KEYS` |

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Font | Pillow's bundled font only, nothing downloaded; Pillow 10.0 falls back to the bitmap font, scaled | `watermark._font` |
| Where the mark goes | On the export only: after the resize and the grain, right before the encoder. Never in the analysis, the stats, the gates or the reveal check. The sidecar records it (`audit.watermark`, a note) and `autoedit declare` lists it | `emit.write_image` |
| Look of the mark | Bottom right, 3 % of the export's short side, opacity 0.7, white with a thin dark outline, one mark height from both edges; a logo sits left of the text | the preset keys; `watermark.draw` |
| Logo | A PNG file name without a folder, looked up in the profile folder, then the configs folder; the sidecar stores the name only; a missing logo fails the photo, so no export goes out without the mark it asked for | `watermark.logo_file` |
| Photos with reveal findings in the gallery | Kept, with a visible warning on the tile naming the kinds; the command prints how many | `gallery.write` |
| Gallery files | `gallery.html` and `gallery/` thumbnails (640 px JPEG, no EXIF) in the folder or `-o`; the full photos are linked where they are, never copied or embedded | `gallery.write` |
| Captions | The export's file name; the alt text is the photo's keywords (F41, never a reveal kind) | `gallery.write` |
| The declaration's statement | Unchanged; one added sentence when an export carries a mark | `declare.WATERMARK_NOTE` |

## Result

Built on 2026-11-19 by Sonnet Builders, reviewed by the Checker (Opus, high): no must-fix. A hostile file name, concept name and reveal label were all escaped in the gallery; the page has no script, no outside URL, no base64 image and no absolute path. The watermark is drawn in `emit.write_image` only, after the analysis. Default output byte-identical; the sidecar's `run_config` carries the five watermark keys. Open nit: the one-line check of the mark rejects ASCII control characters only.
