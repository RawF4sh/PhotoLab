# F16. More input formats

Status: spec (2026-11-11). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F16.

## Demand check (the plan asked for one)

Counted on 2026-11-11 in the local photo folders: no HEIC, HEIF or AVIF file among about 2,700
image files (JPEG, CR2, PNG and a few DNG). There is no demand from the owner's own material. Phone photos are the
usual source of HEIC, so the feature is still worth its small size, and it is built to cost
nothing when unused.

## Goal

AutoEdit reads `.heic`, `.heif` and `.avif` files.

- AVIF: Pillow 11.3 and later reads it out of the box. Both development environments can.
- HEIC and HEIF: through the optional package `pillow-heif`, imported only when such a file is
  opened. Without it the error says what to install.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is `pillow-heif` installed here? | No. Nothing is installed in an unattended run. The HEIC path is tested with a stand-in for the package; the real decode is listed as not verified | `pip install '.[heif]'`, then run the skipped test |
| Output in these formats | Not offered. Exports stay JPEG, TIFF, WebP and PNG | F10's format list |

## Files

| File | Change |
|---|---|
| `src/autoedit/imageio.py` | the three extensions in `_PIXEL_EXT`; `_ensure_heif()`; one call in `load_image` |
| `pyproject.toml` | optional extra `heif = ["pillow-heif>=0.16"]` |
| `src/autoedit/cli.py` | one line in `autoedit doctor` |
| `tests/test_input_formats.py` | new |

## `imageio.py`

```python
_HEIF_EXT = {".heic", ".heif"}
_PIXEL_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".avif"} | _HEIF_EXT
_heif_ready = False


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
```

In `load_image`, before the file is opened with Pillow:
`if os.path.splitext(path)[1].lower() in _HEIF_EXT: _ensure_heif()`.

Everything after the open is the existing path: EXIF orientation, ICC conversion to sRGB, the
EXIF priors. `export_exif` opens the source with Pillow too; it calls `_ensure_heif()` for these
extensions in the same way, inside its existing `try` (a missing package then means "no EXIF
written", never a failed photo).

An AVIF file on a Pillow without AVIF support fails in Pillow's own way; `load_image` turns
`PIL.UnidentifiedImageError` for an `.avif` path into
`RuntimeError("this Pillow cannot read AVIF; it needs Pillow 11.3 or later")`.

## `autoedit doctor`

One more block:

```
  input formats:
    avif: readable            (or: NOT readable, needs Pillow 11.3 or later)
    heic/heif: readable       (or: needs `pip install '.[heif]'`)
```

`avif` from `PIL.features.check("avif")` (False on any exception); `heic` from
`importlib.util.find_spec("pillow_heif") is not None`.

## Task

One Builder (Sonnet, medium). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_input_formats.py tests/test_imageio_fidelity.py tests/test_export_metadata.py tests/test_orientation.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

## Tests: `tests/test_input_formats.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_extensions_are_supported` | `is_supported` is true for `a.heic`, `a.HEIF`, `a.avif`; `is_raw` is false for them |
| 2 | `test_avif_roundtrip` | Skipped (return early with a printed note) when `PIL.features.check("avif")` is false. Otherwise: a synthetic image saved as AVIF at quality 95 loads through `load_image` with the same size and a mean within 3 levels of the source; `batch.run` on a folder with that file writes a JPEG and a sidecar |
| 3 | `test_heif_registers_once_and_only_when_needed` | With a stand-in module `pillow_heif` placed in `sys.modules` whose `register_heif_opener` counts its calls (both restored in `finally`, and `imageio._heif_ready` reset): loading a PNG does not call it; `_ensure_heif()` twice calls it once |
| 4 | `test_heif_without_the_package_says_what_to_install` | With `sys.modules["pillow_heif"] = None` (restored) and `_heif_ready` reset: `load_image("x.heic")` on an existing file raises `RuntimeError` whose text contains `pillow-heif`; `batch.run` on a folder with that file and one JPEG processes the JPEG and records the HEIC as a failure with that text |
| 5 | `test_real_heic_decode` | Skipped with a printed note when `pillow_heif` is not installed. Otherwise: an image saved as HEIC through Pillow loads with the same size |
| 6 | `test_doctor_reports_the_formats` | The doctor command's output contains `avif:` and `heic/heif:` |

## Acceptance checks (the session runs these)

1. Both test suites green; default renders byte-identical to the baseline.
2. Listed as open: the real HEIC decode (test 5 is skipped here, the package is not installed).

## Result

Built on 2026-11-11, in the unattended run. Status: done for AVIF; HEIC and HEIF are done as code
and wait for one check with the plugin installed.

- **Built** by one Builder (Sonnet, medium) from this spec: `.avif`, `.heic` and `.heif` are
  accepted as input; AVIF decodes with Pillow itself (11.3 or later); HEIC and HEIF need the
  optional extra `pip install 'autoedit[heif]'` (`pillow-heif`); without it a HEIC photo fails
  alone with a message that names the extra, and the rest of the batch runs. `autoedit doctor`
  lists both formats.
- **AVIF was tested for real** on both interpreters (Pillow 12.1 and 13.0), including a batch run.
- **Not verified in this run:** a real HEIC decode. `pillow-heif` is not installed here and this
  run installs nothing, so that test returned early. One command closes it:
  `pip install pillow-heif`, then `PYTHONPATH=src python3 tests/run_all.py`.
- **Default output:** JPEG, PNG, TIFF, WebP and RAW input take the same code path as before.
