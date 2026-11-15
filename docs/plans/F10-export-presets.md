# F10. Export presets

Status: spec (2026-11-11). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F10.

## Goal

An export can be resized, written as WebP or PNG, named by a template and put into a subfolder.
Named presets bundle these choices: `autoedit run in out --export web`. Nothing changes for a run
that uses none of this.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Which presets ship | `full`, `web`, `social`, `archive`, `webp`, in `configs/exports.toml` | Edit the file |
| Resize filter | Lanczos on the float image, before the 8-bit conversion; never upscales; no sharpening after it | `imageio.resize_long_edge` |
| PNG bit depth | 8-bit | A 16-bit path in `save_image` |
| Where the sidecar goes with a subfolder | Next to its image, in the subfolder. `run.json` and the mask cache stay in the output folder | `emit.output_paths` |

## Settings

| `RunConfig` field | CLI | `defaults.toml` | Meaning |
|---|---|---|---|
| `out_format` gains `"webp"`, `"png"` | `--format` | `[output] format` | file type |
| `out_long_edge: int \| None = None` (64 to 20000) | `--long-edge N` | `[output] long_edge` | longest side of the export in pixels; never upscales |
| `out_name: str = "{stem}"` | `--name TEMPLATE` | `[output] name` | file name without extension. Fields: `{stem}` (source name), `{style}`, `{n}` (position in the batch, from 1; takes a format such as `{n:04d}`) |
| `out_subdir: str = ""` | `--subdir NAME` | `[output] subdir` | subfolder of the output folder |
| `export: str \| None = None` | `--export NAME` | `[output] export` | a preset from `exports.toml`; explicit settings win over it |

Validation in `RunConfig` (a `field_validator` each, clear messages):

- `out_name`: formatting it with `stem="x", style="s", n=1` must work (an unknown field is an
  error), and the result must not be empty and must not contain `/`, `\\` or `..`.
- `out_subdir`: relative, no `..`, no leading `/` or drive, no `{` or `}` (the folder is not a
  template; only the file name is), at most 100 characters.

## `configs/exports.toml`

```toml
# AutoEdit export presets: `--export NAME` (or [output] export = "NAME" in a run file).
# Keys: format ("jpeg" | "tiff" | "webp" | "png"), quality, long_edge (pixels; left out = full
# size), name (file name template: {stem}, {style}, {n}), subdir, metadata ("keep" | "shareable"
# | "none"). A flag on the command line wins over the preset.

[full]
format = "jpeg"
quality = 95

[web]
format = "jpeg"
quality = 85
long_edge = 2048
name = "{stem}_web"
subdir = "web"

[social]
format = "jpeg"
quality = 88
long_edge = 1350
name = "{stem}_social"
subdir = "social"
metadata = "none"

[archive]
format = "tiff"
metadata = "keep"
subdir = "archive"

[webp]
format = "webp"
quality = 82
long_edge = 2560
subdir = "webp"
```

`RunConfig.exports_path` (with the other config paths; `paths._CONFIG_FILES` gains `"exports"`;
like `instructions`, a configs folder without the file uses the packaged one).

Applying a preset is one function, used by the CLI and by the web service:

```python
# src/autoedit/exports.py
"""Export presets (F10): named bundles of output settings in configs/exports.toml."""

from __future__ import annotations

import tomllib

_KEYS = {"format": "out_format", "quality": "quality", "long_edge": "out_long_edge",
         "name": "out_name", "subdir": "out_subdir", "metadata": "metadata"}


def load(path: str) -> dict[str, dict]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, OSError):
        return {}


def preset_kwargs(name: str, path: str) -> dict:
    """RunConfig arguments of the preset `name`. Unknown preset or key: ValueError."""
    presets = load(path)
    if name not in presets:
        raise ValueError(f"unknown export preset {name!r}; choose one of {sorted(presets)}")
    bad = sorted(k for k in presets[name] if k not in _KEYS)
    if bad:
        raise ValueError(f"exports.toml: preset {name!r} has unknown keys {bad}")
    return {_KEYS[k]: v for k, v in presets[name].items()}
```

`cli.load_run_config`: after the defaults and the `--config` file are read and before the CLI
overrides are applied, when an `export` name is set (flag, else run file), its kwargs are merged
in. `service.make_cfg` does the same for `opts["export"]`. `autoedit exports` lists the presets
(name and values), like `autoedit styles`.

## `imageio.py`

```python
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
```

`save_image`: `"webp"` saves with `quality=quality, method=4`; `"png"` with `compress_level=6`;
both pass `exif` and `icc_profile` like JPEG. `_PIXEL_EXT` already reads both.

## `emit.py`

- `output_paths(out_dir, image_name, fmt, taken=None, name="{stem}", subdir="", fields=None)`:
  the stem is `name.format(stem=<source stem>, **(fields or {"style": "", "n": 0}))`; the folder
  is `os.path.join(out_dir, subdir)` (created); the extension is `jpg`, `tif`, `webp` or `png`;
  the collision suffix `-2`, `-3` works as today. With the defaults the result is exactly
  today's.
- `write_image(..., long_edge=None)`: resizes before saving; the EXIF pixel size is the size
  written.
- `write_outputs` passes `cfg.out_long_edge`.

## Callers

`batch.run` (both loops), `web/jobs._run`, `web/service.run_one` call
`emit.output_paths(out_dir, path, cfg.out_format, taken, name=cfg.out_name, subdir=cfg.out_subdir,
fields={"style": cfg.style, "n": i})` with `i` the 1-based position of the photo.

`adjust.rerender`: the run folder is the sidecar's folder when it holds `run.json`, else its
parent when that holds `run.json`, else the sidecar's folder. `run.json` and the mask cache are
looked up in the run folder; the image and the sidecar stay where they are.

## Task

One Builder (Sonnet, medium). Proving command: `PYTHONPATH=src python3 -m pytest tests/test_export_presets.py tests/test_export_metadata.py tests/test_emit.py tests/test_adjust.py tests/test_cli.py tests/test_jobs.py tests/test_web_service.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`, then the smoke run
`rm -rf .tmp/f10 && PYTHONPATH=src python3 -m autoedit.cli run ./test-corpus .tmp/f10 --export web && ls -R .tmp/f10`
(two `*_web.jpg` and two sidecars under `web/`, `run.json` in the root; delete `.tmp/f10` after).

## Tests: `tests/test_export_presets.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_default_paths_and_bytes_are_unchanged` | `emit.output_paths(out, "a/IMG_1.CR2", "jpeg")` gives `out/IMG_1.jpg` and `out/IMG_1.autoedit.json`; `write_image` without `long_edge` writes the same bytes as before (compare with a `save_image` call) |
| 2 | `test_long_edge` | A 300 by 200 image with `long_edge=120` is written 120 by 80; its EXIF `PixelXDimension` (when the source has EXIF) is 120; `long_edge=1000` leaves 300 by 200; the mean colour of a flat image is unchanged within 1 level |
| 3 | `test_webp_and_png` | Both formats are written with the right extension, open with Pillow, carry the sRGB profile, and (for a source with EXIF, mode `keep`) the capture time |
| 4 | `test_name_template` | `"{stem}_web"`, `"{style}-{stem}"` and `"{n:04d}_{stem}"` give the expected names; two sources with the same stem still get `-2`; the sidecar follows the image name |
| 5 | `test_subdir` | `batch.run` with `out_subdir="web"`: images and sidecars are under `out/web/`, `run.json` is in `out/`; a second run with `resume=True` skips them; `adjust.rerender(<image in web/>)` finds the source through `out/run.json` and rewrites the file in `web/` |
| 6 | `test_validation` | `out_name` values `""`, `"a/b"`, `"../x"`, `"{nope}"` and `out_subdir` values `"../x"`, `"/abs"` are rejected by `RunConfig` |
| 7 | `test_presets` | `exports.preset_kwargs("web", <packaged file>)` gives the five values of the preset; an unknown name raises `ValueError` naming the known presets; a preset with an unknown key raises. Every packaged preset builds a valid `RunConfig` |
| 8 | `test_cli_preset_and_overrides` | `--export web` sets format, quality, long edge, name and subfolder; `--export web --quality 70 --subdir x` keeps the preset's other values and takes the two flags; `[output] export = "social"` in a `--config` file works; `service.make_cfg({"export": "webp"})` works |
| 9 | `test_batch_with_a_preset` | `batch.run` with the `web` preset on two synthetic 400 by 300 photos, stub backend: two files `*_web.jpg` in `web/`, long edge 2048 not exceeded (the sources are smaller, so 400 by 300), sidecar `audit.output` is the new file name |

## Result

Built on 2026-11-11, in the unattended run. Status: done.

- **Built** by one Builder (Sonnet, medium) from this spec, with no deviation in behaviour:
  five presets in `configs/exports.toml` (`full`, `web`, `social`, `archive`, `webp`), `--export`,
  `--long-edge`, `--name`, `--subdir`, `--format webp|png`, and `autoedit exports`.
- **Checked by the session** on the smoke corpus: `--export web` and `--export archive` write
  images and sidecars into the preset's subfolder and `run.json` into the output root; JPEG, TIFF
  and WebP carry the sRGB profile; a name pattern `{stem}-{style}-{n}` is filled in; an unknown
  preset name ends with a clear message and exit code 2.
- **Fixed by the session:** `--subdir "out/{style}"` created a folder literally named `{style}`.
  The folder is not a template, so braces in `out_subdir` are now rejected with a message that
  says so.
- **Default output:** byte-identical to the baseline renders (no preset, no new option); both test
  suites green.
- **Review** (Checker, Opus, high), one blocking finding and four more, all fixed (the table
  "Review round 1 (F8 to F11)" in the [F11 spec](F11-user-config-layer.md) has the details):
  a style name could carry `../` into the file name through `{style}` and write outside the
  output folder; an export could replace its own source photo; `adjust` could pick the wrong run
  folder; a preset beat explicit values of the same run file; a template such as `{n[0]}` passed
  validation and failed at run time.
- **Order of precedence now:** packaged defaults, then the preset, then explicit values in the
  profile's `defaults.toml` or the run file, then command-line flags. So a run file with
  `quality = 70` keeps 70 under `--export web`.
- **Final check** (a second Checker, on the complete tree), two more blocking findings, fixed in
  "Review round 2" in the [F11 spec](F11-user-config-layer.md): the "is this export its own source" check compared path strings, so on a
  case-insensitive volume a folder exported into itself still replaced `IMG_1.JPG`; and an export
  could land on another source of the same run (`b.jpg` exported as PNG over `b.png`). The check
  now compares file identities against every input of the run. Also fixed: a subfolder that is a
  symlink out of the output folder, `{n:c}` in a name pattern, and a name problem that ended a
  batch instead of failing one photo.
- **Sidecars** record the new settings under `audit.run_config`. `autoedit adjust` on an image in
  a preset subfolder finds the run through `run.json` one level up.
