# F1. Export keeps metadata and colour profile

Spec for feature F1 of [FEATURE_PLAN.md](../FEATURE_PLAN.md), written on 2026-11-10 and built
through the loop in [AGENT_ROUTING.md](../AGENT_ROUTING.md).

## Goal

An edited photo carries the camera data of its source (capture time, camera, lens, exposure
settings, artist, copyright) and an embedded sRGB colour profile. Before F1 an export has neither,
so edited photos stop sorting by capture time in other tools and colour-managed viewers have to
guess the colour space.

A run setting chooses how much is carried:

| `metadata` | EXIF in the export |
|---|---|
| `keep` | Everything in the allow-list below, including the GPS block and the serial numbers. |
| `shareable` (default) | The same without the GPS block and without the serial numbers of the camera and the lens. |
| `none` | No EXIF at all. |

The sRGB profile is embedded in every product export, whatever the setting.

## Owner decision

On 2026-11-10 the owner chose to keep everything but the location. The check then pointed out that
the serial numbers of the camera and the lens would stay in the file, and on 2026-11-11 the owner
chose to drop those by default too. The default mode was called `no_location` in the first commit
and is `shareable` since the second. No decision is open.

## Measured before the design (2026-11-10)

- Pillow 12.1 and 13.0 both carry EXIF from a real camera JPEG and a real CR2 into a new JPEG:
  capture time, model, ISO and f-number survive, and the block is about 1.1 KB.
- The maker note is 7.8 KB in the camera JPEG and 68 KB in the CR2. A JPEG holds at most 65,533
  bytes of EXIF, so the maker note cannot be copied from a RAW. It also contains offsets that
  break when the block moves.
- A CR2's first directory holds the storage tags of the RAW itself (strip offsets, compression,
  an XMP packet). Copying the directory as it is would put wrong tags into the export.
- `ImageCms.createProfile("sRGB")` gives a 588-byte version 4.4 profile. Its bytes differ between
  calls only in the creation date. With the date fixed, LittleCMS 2.17 and 2.19 produce identical
  bytes.
- Converting sRGB to sRGB through LittleCMS is the identity on 8-bit data (maximum difference 0),
  and costs 0.29 s on a 22 MP image. Re-reading a tagged export therefore returns the same pixels
  as before.
- `tifffile` cannot write the Exif and GPS sub-directories (it filters tags 34665 and 34853). It
  can write the profile and the text tags of the first directory. Its own JSON occupies the
  description tag (270).
- Sidecars hold the file name only, not a path. The plan's claim that they hold absolute paths was
  wrong. Only `run.json` holds the input and output folder.

## Design

1. **EXIF is read from the source file when the export is written**, not carried through
   `imageio.Image`. Every caller of `emit.write_image` already has the source path. This keeps
   `load_image`, `Image` and `Pipeline.process_one` untouched, so F1 changes no decode, tone or
   exposure code.
2. **An allow-list for the first directory.** Only tags that describe the photograph are copied.
   Everything else there describes how the source file stores its pixels.
3. **The Exif sub-directory is copied whole**, except the maker note and the interoperability
   pointer.
4. **Tags that would be wrong after the edit are corrected**: orientation is 1 (the pixels are
   already upright), the pixel size is the size of the export, the colour space is sRGB, and
   `Software` names AutoEdit. `DateTime` keeps the source value, so the same input always gives
   the same file.
5. **The GPS block and the serial numbers are copied only under `keep`.** The serial numbers are
   the Exif tags `0xA431` (camera body), `0xA435` (lens) and `0xFDE9` (written by Adobe Camera
   Raw). Names the photographer entered (artist, copyright, owner name) stay under `shareable`.
6. **Metadata is opt-in per call.** `emit.write_image` writes metadata only when it is given
   `source`. Measurement, contact-sheet and gallery exports pass no `source` and stay
   byte-identical to today.
7. **A failure to read or build the EXIF never fails the photo.** The export is written without
   EXIF, and the sidecar says so.
8. **16-bit TIFF carries the profile and the basic tags** (make, model, capture time, artist,
   copyright, software). The 8-bit TIFF fallback and JPEG carry the full block.

## Task T1: code and tests (Builder)

### Files

`src/autoedit/imageio.py`, `src/autoedit/emit.py`, `src/autoedit/contract.py`,
`src/autoedit/configs/defaults.toml`, `src/autoedit/cli.py`, `src/autoedit/batch.py`,
`src/autoedit/web/service.py`, `src/autoedit/web/jobs.py`, and the tests named below.

Not to be touched: `load_image`, `_decode_raw`, `_to_srgb` and the `Image` dataclass in
`imageio.py`; anything under `edit/`, `decide/`, `segment/`; `pipeline.py`; `quality.py`;
`configs/quality.toml`; the docs and the changelog (task T2 edits those at the same time).

### `imageio.py`

Add `import functools` and `import struct`. Add below `_read_exif`:

```python
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
```

`save_image` gains two optional parameters, `exif: bytes | None = None` and
`icc_profile: bytes | None = None`, and passes them on:

- JPEG and the Pillow fallback: `pil.save(tmp, ..., **extra)` where `extra` holds `exif` and
  `icc_profile` only when they are not None. With both None the call is exactly today's call.
- 16-bit TIFF: `_save_tiff16(rgb, tmp, exif, icc_profile)`.

`_save_tiff16(rgb01, path, exif=None, icc_profile=None)`:

```python
    kw: dict = {}
    tags = [(34675, 7, len(icc_profile), icc_profile, True)] if icc_profile else []
    if exif:
        # ponytail: tifffile cannot write the Exif/GPS sub-directories (it filters tags 34665 and
        # 34853), so a 16-bit TIFF carries the first directory's text tags only. Exposure, lens
        # and GPS would need a hand-written sub-directory appender; add it when a TIFF user asks.
        src = PILImage.Exif()
        src.load(exif)
        text = {t: src.get(t) for t in (0x010F, 0x0110, 0x0132, 0x013B, 0x8298)}
        text[0x0132] = src.get_ifd(_EXIF_IFD).get(0x9003) or text[0x0132]  # capture time wins
        tags += [(t, 2, 0, v, True) for t, v in text.items() if isinstance(v, str) and v]
        if isinstance(src.get(0x0131), str):
            kw["software"] = src[0x0131]
    if tags:
        kw["extratags"] = tags
    tifffile.imwrite(path, data, photometric="rgb", **kw)
```

With `exif` and `icc_profile` both None, `kw` is empty and the file is byte-identical to today's.

### `emit.py`

```python
def write_image(rgb: np.ndarray, path: str, fmt: str = "jpeg", quality: int = 92,
                source: str | None = None, metadata: str = "none") -> str:
    """Write the baked image and return a one-line note for the sidecar ("" when there is
    nothing to say).

    With `source` (the photo this export was made from) the file is a product export: it gets
    the sRGB profile and, as `metadata` says, the source's EXIF. Without `source` the bytes are
    what they were before F1; measurement and contact-sheet exports rely on that."""
    if source is None:
        save_image(rgb, path, fmt=fmt, quality=quality)
        return ""
    exif = export_exif(source, metadata, (rgb.shape[1], rgb.shape[0]),
                       software=f"AutoEdit {__version__}")
    save_image(rgb, path, fmt=fmt, quality=quality, exif=exif, icc_profile=srgb_profile())
    if metadata == "none":
        return ""
    if exif:
        limit = "; a 16-bit TIFF holds the basic tags only" if fmt == "tiff" else ""
        return f"export: EXIF kept{limit} (metadata = {metadata})"
    return (f"export: no EXIF written, the source has none or it could not be read "
            f"(metadata = {metadata})")
```

### The three product export sites

`batch.py` (in `run`), `web/jobs.py` (the job loop) and `web/service.py` (`run_one`) each call
`emit.write_image(out_rgb, image_out, cfg.out_format, cfg.quality)` today. Each becomes:

```python
note = emit.write_image(out_rgb, image_out, cfg.out_format, cfg.quality,
                        source=<the source path>, metadata=cfg.metadata)
if note:
    result.notes.append(note)
```

The source path is `path` in `batch.py`, `item.src` in `jobs.py` and `image_path` in
`service.py`. The note must be appended before `emit.write_sidecar`. In `batch.py` keep the
existing `if do_apply and out_rgb is not None:` condition around it.

No other caller of `emit.write_image` or `save_image` changes (`quality_runner.py`, `curate.py`,
`variants.py`, `experiments.py`, `scripts/`).

### The setting

- `contract.py`, in the output group after `quality`:
  `metadata: Literal["keep", "shareable", "none"] = "shareable"` with a one-line comment.
- `configs/defaults.toml`, in `[output]`: `metadata = "shareable"` with a comment naming the
  three values.
- `cli.py`: `_flatten` reads `metadata` from `[output]`; `load_run_config` adds
  `"metadata": getattr(args, "metadata", None)` to the overrides; `_add_run_flags` adds
  `--metadata` with `choices=["keep", "shareable", "none"]` and a help line.
- `web/service.py`: add `"metadata"` to `_ALLOWED`.

### Tests

New file `tests/test_export_metadata.py`, written like the other test files (plain `test_*`
functions that take `tmp_path`, runnable by `tests/run_all.py` and collectable by pytest). All
fixtures are synthetic and built with Pillow in the test; no test reads a photo from disk outside
`tmp_path`.

Fixture: a small JPEG whose EXIF has make, model, orientation 6, `DateTime`, artist and copyright
in the first directory, plus one tag outside the allow-list (for example `0x02BC` with bytes); an
Exif sub-directory with `DateTimeOriginal` (a different value from `DateTime`), ISO, an f-number
as `IFDRational(28, 10)`, an exposure bias of `IFDRational(-1, 3)`, a maker note, pixel
dimensions, an owner name and the body and lens serial numbers; and a GPS block with latitude and
longitude.

| Test | Asserts |
|---|---|
| `test_keep_copies_camera_data_and_gps` | Under `keep`: make, model, capture time, ISO, f-number 2.8 and bias −1/3 equal the source; GPS latitude and both serial numbers present; orientation 1; pixel dimensions equal the `size` passed; colour space 1; `Software` equals the value passed; no maker note; the non-allow-listed tag is absent. |
| `test_shareable_drops_gps_and_serial_numbers` | Under `shareable`: the GPS block is empty and both serial numbers are gone; capture time, make, model, artist, copyright and owner name are still there. |
| `test_none_and_missing_sources_write_nothing` | `export_exif` returns None for mode `none`, for `source=None`, for a JPEG without EXIF, for a missing file and for a text file. |
| `test_unknown_mode_raises` | `ValueError`. |
| `test_oversized_exif_is_dropped` | A TIFF source with a 70,000-byte `UserComment` (`0x9286`) gives None; the same source without it gives a block (control). |
| `test_save_image_default_writes_no_metadata` | `save_image(rgb, path)` gives a JPEG with no ICC profile and an empty EXIF. |
| `test_metadata_does_not_change_pixels` | The same array saved as JPEG with and without `exif` + `icc_profile` decodes (plain Pillow) to identical pixels; the tagged file holds `srgb_profile()` and the capture time. |
| `test_srgb_profile_is_stable` | Two calls return the same bytes; bytes 24..36 are the fixed date; `ImageCms` can open it. Returns early when `srgb_profile()` is None. |
| `test_tagged_export_reloads_identically` | `load_image` returns exactly the same array for the tagged and the untagged JPEG. This pins the measured fact that sRGB to sRGB through LittleCMS is the identity. |
| `test_tiff_export_keeps_basic_tags` | `save_image(..., "tiff", exif=..., icc_profile=...)`: Pillow reads make and model back, the capture time is in tag 306 or in the Exif sub-directory, and the ICC profile is present. Must pass with and without `tifffile` installed. |
| `test_write_image_product_export` | With `source` and `shareable`: the note names `shareable`, the file has the profile, the capture time and no GPS, and `Software` starts with `AutoEdit `. Without `source`: the note is `""` and the file has no metadata. With `metadata="none"`: note `""`, profile present, EXIF empty. A TIFF export's note names the 16-bit limit. |
| `test_batch_run_carries_metadata` | A stub `batch.run` over a folder holding the fixture (orientation 6): the output's size is the transposed size, its orientation tag is 1, its pixel dimensions match the output, capture time is kept, GPS and the serial numbers are gone, and the sidecar's notes contain the export note. |
| `test_metadata_setting_flows` | `RunConfig().metadata == "shareable"`; the packaged `defaults.toml` flattens to the same value; `load_run_config` with `metadata="none"` gives `none` (follow `tests/test_cli.py`); `service.make_cfg({"metadata": "keep"}).metadata == "keep"`. |

Where an existing test in `tests/test_jobs.py` or `tests/test_web_service.py` already runs a stub
job or `run_one` to completion, add one assertion there that the output file has an ICC profile.
Do not build a new job harness for it.

### Proving commands

```bash
PYTHONPATH=src python3 tests/run_all.py
PYTHONPATH=src .venv/bin/python tests/run_all.py
PYTHONPATH=src python3 -m autoedit.cli run ./test-corpus ./out-smoke-f1 --backend stub
```

The first interpreter has no `tifffile` (8-bit TIFF fallback), the second has it (16-bit path).
All three must succeed. Delete `./out-smoke-f1` afterwards.

## Task T2: docs (Tweaker)

Exact insertions into `CHANGELOG.md`, `APP_GUIDE.md`, `ARCHITECTURE.md` and `README.md`; the
strings are in the task brief. No code.

## Acceptance checks (Checker, then the session)

1. Both test runs and the stub smoke run pass.
2. The diff matches this spec. It touches none of: `load_image`, `_decode_raw`, `_to_srgb`,
   `Image`, `edit/`, `decide/`, `segment/`, `pipeline.py`, `quality.py`, `configs/quality.toml`.
   The tone gate in `CLAUDE.md` is therefore not required: no exposure, tone or decode code
   changes.
3. Callers that pass no `source` produce the same bytes as before.
4. No absolute local path and no personal detail in any changed file.
5. The session renders the same four photos (two JPEG, two CR2) before and after the change with
   the stub backend and compares the decoded pixels. The maximum difference must be 0. It then
   checks one export of each kind: capture time equal to the source, orientation 1, no GPS
   coordinates, profile present.

## Result (2026-11-10)

- Tests: 464 passed with and without `tifffile` (451 before F1, 13 new).
- Four real photos (two CR2, one camera JPEG, one JPEG without EXIF) rendered before and after
  with the stub backend: the maximum pixel difference is 0 on all four. The three with source EXIF
  keep capture time, camera, ISO and lens, with orientation 1, the export's pixel size, sRGB, no
  GPS block and no maker note. The fourth gets the profile only, and its sidecar says so. An
  export grows by 1,730 bytes.
- A 16-bit TIFF of a real photo carries make, model, capture time, software and the profile.
- Callers that pass no `source` write the same bytes as before: hashes compared against the
  pre-change package under both interpreters.
- Check: pass with six minor findings, all resolved. The TIFF note names its limit, the oversize
  test has a control, three doc sentences were corrected, and the question about serial numbers
  went to the owner.
- Follow-up on 2026-11-11, after the owner's second decision: the default mode is `shareable` and
  also drops the serial numbers. 464 tests pass with and without `tifffile`. The same four photos
  are still pixel-identical to the renders from before F1. The three with source EXIF now carry 34
  Exif tags instead of 36, because both serial numbers are gone; under `keep` both are present.
  The session verified this follow-up with the tests and the real photos. It did not go through a
  second Checker pass.

## Known limits

- `shareable` keeps the names the photographer entered: the artist, the copyright and an owner name
  set in the camera. `none` removes them.
- XMP and IPTC blocks (ratings, keywords, captions) are not copied.
- The maker note is not copied, so vendor-only fields are lost. Lens model and the standard
  exposure fields live in the Exif sub-directory and are kept.
- A 16-bit TIFF carries the profile and the basic tags only.
- RAW formats that Pillow cannot open as TIFF (for example CR3 and RAF) export without EXIF; the
  sidecar note says so.
- The embedded thumbnail of the source is not copied, and no new one is written.
