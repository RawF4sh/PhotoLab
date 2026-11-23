# F52. Lens vignette profiles built from your own photos

Status: spec (2026-11-22), size M, off by default. This is F46's "Design B". The request's other
idea, reading the falloff from the picture content of many frames, was measured and is not built.

## Goal

A RAW decode keeps the lens falloff: corners 0.14–0.29 EV (at 0.86 of the half-diagonal; up to
0.5 EV at f/4) darker than the camera's own JPEG. `autoedit lens-profile FOLDER` stores, per body,
lens, focal length and aperture, the median over many of the user's RAW files of how much the camera
brightened each ring of its own embedded preview; `--lens-vignette` applies it at decode, RAW only.

## Measured (2026-11-22, scratch scripts outside the repo)

**Corpus.** `curated-dolomites-summer-2026/selected_raw`: 110 CR2, one Canon EOS 5D Mark III, one
EF24-105mm f/4L IS USM, 27 focal lengths, f/4 to f/14 (65 frames at f/11, 31 at 105 mm); only 5 of
the 50 exact focal/aperture pairs hold 5+ frames. The camera's peripheral illumination correction is
on in all; its strength (Canon maker note `PeripheralLightingValue`) is 70, 66, 64, 63, 56 and 53 at
ISO 100, 200, 320, 400, 1000 and 1250.

**Estimators** (EV of brightening in the 0.84–0.88 ring, "0.86"). **C1**, RAW alone: centre minus
ring median log-luminance. **C2**, RAW alone: F46's per-ring median outward horizontal
log-gradient, integrated. **P**: the RAW (linear, half size, camera white balance) against its own
embedded JPEG, a global tone curve and the ring gains fitted jointly; the scene cancels out.

**References.** (a) Adobe's RAW profile of this lens on an EOS-1Ds Mark III (same 36 × 24 mm),
read locally, never shipped: full correction at 0.86 of 0.72 EV (24 mm f/4), 0.41 (24 mm f/11),
0.31 (35 mm f/11), 0.19 (105 mm f/11). (b) The camera reference, 1.11 × Adobe × strength / 100
(median of 109 frames); P/Adobe is 0.62, 0.65, 0.71, 0.75 and 0.80 at strengths 56, 63, 64, 66 and
70. So P is the camera's own correction, a fixed share of the lens falloff.

| EV at 0.86 (C1, C2 vs Adobe full; P vs camera reference) | C1 | C2 | P |
|---|---|---|---|
| Per frame vs reference: median / p90 / max | 0.66 / 1.72 / 3.91 | 0.45 / 1.15 / 2.22 | 0.03 / 0.12 / 0.18 |
| Split-half, 105 mm f/11, 10 vs 10 frames: median / p90 | 0.25 / 0.53 | 0.09 / 0.29 | 0.003 / 0.010 |
| Median of 1 / 3 / 5 / 10 of those frames, p90 from all 20 | 1.71 / 0.61 / 0.52 / 0.29 | 0.77 / 0.41 / 0.39 / 0.15 | 0.07 / 0.02 / 0.01 / 0.01 |
| Six bucket medians (below) minus Adobe | −0.97 to +0.15 | −0.13 to +0.45 | camera's share |

**Content cannot be made reliable.** Ten frames per half still differ by 0.25 EV (C1) and by
0.29 EV at p90 (C2). The bias does not average out either: most landscapes are brighter at the top
than at the bottom, so bucket medians of 6 to 42 frames are up to 1 EV off. Not built.

**The preview is reliable.** Six buckets of half a stop of focal length by one stop of aperture,
each with at least 5 frames, cover 87 of the 109 measured frames:

| Focal length | Aperture | Frames | P at 0.86 / 0.94 | Split-half at 0.86, median / p90 | Camera ref. | Adobe full |
|---|---|---|---|---|---|---|
| 26.9–38.1 mm | f/9.5–13.5 | 11 | 0.29 / 0.36 | 0.022 / 0.027 | 0.26 | 0.33 |
| 38.1–53.8 mm | f/9.5–13.5 | 13 | 0.22 / 0.26 | 0.020 / 0.035 | 0.21 | 0.27 |
| 53.8–76.1 mm | f/6.7–9.5 | 6 | 0.17 / 0.20 | 0.013 / 0.032 | 0.17 | 0.24 |
| 53.8–76.1 mm | f/9.5–13.5 | 9 | 0.15 / 0.19 | 0.062 / 0.150 | 0.17 | 0.23 |
| 76.1–107.6 mm | f/6.7–9.5 | 6 | 0.14 / 0.18 | 0.003 / 0.039 | 0.15 | 0.21 |
| 76.1–107.6 mm | f/9.5–13.5 | 42 | 0.14 / 0.17 | 0.006 / 0.014 | 0.15 | 0.19 |

- Leave-one-out (profile from the rest of the bucket vs each frame's camera reference): median
  0.009 EV, p90 0.029 at 0.86, p90 0.046 over rings 0.3–0.98. Frames needed (p90 distance from
  the whole bucket): 0.13 / 0.09 / 0.05 / 0.04 / 0.02 EV with 1 / 2 / 3 / 5 / 8 frames.
- k1 ρ² + k2 ρ⁴ + k3 ρ⁶ fits every bucket median within 0.009 EV. Not covered, 22 frames: 24 mm
  (6 over 3 apertures), f/4–f/6.3 (10, at most 4 per bucket), 6 in 3 small buckets.
- End to end (profiles from half the frames applied as below to 12 of the other half): the camera
  gain left at 0.86 fell from median 0.17 EV (max 0.39) to 0.04 (max 0.21, a frame whose own
  preview is an outlier); no pixel newly clipped or darkened. Synthetic frames: a known 0.29 EV
  gain under an S-shaped or power tone curve comes back within 0.02 EV per ring (0.005 with none).
- Skipped: one frame (0078, snow), whose preview's fine detail correlates below 0.9. Time at a load
  average of 140: 3.3 s per photo to measure, 5.5 s to correct 22 MP in float64.

## Design

A profile is what the camera did to its own preview, so the decode gets the corners of the camera's
JPEG. A camera that does not correct previews measures about 0; a RAW with no large preview adds none.

**`src/autoedit/lens.py` (additions).** These are module constants, as in F46, because the decode
reads no rules file: `_VIG_GRID = 360`, `_VIG_RINGS = 25` (each 0.04 of the half-diagonal),
`_VIG_CENTRE = 0.25`, `_VIG_MIN_CORR = 0.9`, `_VIG_MIN_FRAMES = 5` and `_VIG_KNEE = 0.75` (linear).

- **`lens_key(path)`** returns `(camera, lens, focal_mm, f_number)` from Pillow EXIF 0x0110, 0xA434,
  0x920A and 0x829D, or None when any is missing. It never reads a serial number.
- **`bucket(f, n)`**: for each value x, `b = floor(2·log2 x + 0.5)`. The edges are `2^((b ∓ 0.5)/2)`
  to 2 decimals. The builder groups by it, and the decode matches on it.
- **`measure(path) -> curve | reason`**, never raising: rawpy `postprocess(no_auto_bright=True,
  gamma=(1, 1), output_bps=16, use_camera_wb=True, half_size=True, user_flip=0)` / 65535; if
  `sizes.crop_width > 0`, crop to the camera frame (offsets `(crop_top_margin − top_margin) // 2`,
  `(crop_left_margin − left_margin) // 2`, size `crop_height // 2 × crop_width // 2`); the JPEG
  `extract_thumb()` read after `draft("RGB", (2·_VIG_GRID, 2·_VIG_GRID·height // width))`; then
  `camera_gain`.
- **`camera_gain(lin, pv) -> curve | reason`**, numpy only. Skip a preview under 360 px or with an
  aspect more than 1 % off. Area-mean (Pillow `BOX`, float32) both luminances (`_LUMA`, the preview
  through `srgb_to_linear`) and both clip shares (RAW max ≥ 0.95, preview ≥ 250/255) onto a grid
  360 cells long; a cell counts with no clipped share, RAW > 1e-3 and preview > 2e-3. Skip when the
  4-neighbour high-passes of log2 luminance (2-cell border cut) correlate below 0.9. Then 3 passes:
  fit T = per-bin medians of (log2 RAW + ring gain so far, log2 preview) in 24 quantile bins
  (running max plus a 1e-6 ramp), on ρ < 0.25 first, then on every cell; per cell est =
  T⁻¹(log2 preview) − log2 RAW inside T's range; ring gain = median est per ring (≥ 20 cells, else
  interpolated) minus the median est at ρ < 0.25. Skip when fewer than 12 rings are usable.
- **`build_profiles(paths)`**: per camera, lens and bucket, the median per ring, a least-squares fit
  of k1 ρ² + k2 ρ⁴ + k3 ρ⁶, kept from 5 frames up.
- **`correct_vignette(img, profiles_file)`**: G = 2^max(0, k1 ρ² + k2 ρ⁴ + k3 ρ⁶), ρ as in
  `ca_prep`; linearise by `decode_info["transfer"]` (`libraw-2.222-4.5` → `libraw_curve_inv` /
  `libraw_curve`, `srgb` → `srgb_to_linear` / `linear_to_srgb`), roll off, re-encode, in float32
  (target under 2 s per 22 MP). Report `{"applied", "bucket", "frames", "corner_ev"}` (gain at
  ρ = 0.86) or `{"applied": False, "reason"}`.

**Roll-off** (F44's shoulder with top = G). Let m = G × the largest channel. Above the knee,
m' = knee + d / (1 + d·c), with d = m − knee and c = (G − 1) / ((1 − knee)(G − knee)). The output
is `min(lin · G·m'/m, 1)`. The shoulder lies above m/G, so 1.0 stays 1.0, nothing below 1.0 reaches
it, and nothing darkens. Hue is kept, the gain below the knee is exactly G, and G = 1 is the identity.

**Profile file.** `<profile>/lens_profiles.toml` is written with `paths.dump_toml` and read once per
file and mtime with `tomllib`. A row is: focal mm from, to; f-number from, to; frames; k1, k2, k3:

```toml
["Canon EOS 5D Mark III"."EF24-105mm f/4L IS USM"]
buckets = [[76.11, 107.63, 9.51, 13.45, 42, 0.141, 0.099, -0.039]]
```

Numbers and the two model names only: no file name, path, date, GPS or serial. A re-run replaces the
rows of the lenses it measured. It stays in the profile folder, never committed; tests are synthetic.

**Decode.** `imageio.load_image` applies the profile before the colour-fringe fix:

```python
    img = _load_image(path, decode)
    if decode and decode.get("lens_vignette") and is_raw(path):
        from . import lens
        img.rgb, img.decode_info["vignette"] = lens.correct_vignette(img, decode.get("lens_profiles"))
```

`_LENS_DEFAULTS` gains `"lens_vignette": False`. Only with it on does `raw_opts_from` add
`"lens_profiles": <cfg.profile_dir>/lens_profiles.toml` (None without a profile folder), so default
options do not change and every caller gets the same correction. Non-RAW input is left alone (the
camera corrected it); the mask cache is unchanged. Pipeline note: `lens: corners brightened +0.14 EV
(profile of 42 photos, 76-108 mm f/9.5-13)` or `lens: vignette left as decoded (<reason>)`.

**Settings.** `RunConfig.lens_vignette: bool = False`, wired like `lens_ca`: `[decode]` in
`defaults.toml`, `_dec_map`, the override map, `--lens-vignette`, `_ALLOWED` in `web/service.py`,
and `[setting.lens_vignette]` (label "Brighten dark corners", group "quality", `advanced = true`,
help "Brightens the corners of RAW photos as much as your camera does in its own JPEGs. Needs a
profile: autoedit lens-profile FOLDER.").

**Command.** `autoedit lens-profile FOLDER [FOLDER ...] [--profile DIR] [--dry-run]` reads the RAW
files of the folders (no subfolders) and writes `<profile>/lens_profiles.toml` (`--profile`, else
`paths.default_profile_dir()`, created as `looks save` does). It prints each skipped file with its
reason, each bucket written (models, ranges, photos, corner EV) and the buckets under 5 photos;
`--dry-run` writes nothing. Exit 0 when a bucket was (or would be) written, else 1. One process
(`# ponytail: a pool if catalogues of thousands are common`). `doctor` lists the file as known.

## Files

`src/autoedit/lens.py`, `imageio.py`, `contract.py`, `cli.py`, `web/service.py`, `pipeline.py`,
`configs/defaults.toml`, `configs/settings.toml`, `tests/snapshots/settings_schema.json`, the new
`tests/test_lens_vignette.py`, `CHANGELOG.md`, `docs/FEATURE_PLAN.md`. F22 and F50–F53 are editing
`cli.py`, `contract.py`, `settings.toml`, `service.py` and `pipeline.py` now: build T2 and T3 after
they land. T1–T3 share the test file, so they run one after another.

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder (Sonnet, medium) | `lens.py` except the command; tests 1–6 | `PYTHONPATH=src python3 -m pytest tests/test_lens_vignette.py tests/test_lens.py -q` |
| T2 | Builder (Sonnet, medium) | Wiring, note, snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1`); tests 7–8 | `PYTHONPATH=src python3 -m pytest tests/test_lens_vignette.py tests/test_settings_schema.py tests/test_cli.py tests/test_raw_develop.py tests/test_imageio_fidelity.py tests/test_workflows.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder (Sonnet, medium) | `lens-profile`, `doctor`; test 9 | `PYTHONPATH=src python3 -m pytest tests/test_lens_vignette.py tests/test_cli.py -q`, then `PYTHONPATH=src python3 -m autoedit.cli lens-profile curated-dolomites-summer-2026/selected_raw --profile .tmp/f52/profile --dry-run` |
| T4 | Tweaker (Haiku, low) | CHANGELOG lines; FEATURE_PLAN row F52 | none |
| T5 | Session | Acceptance | — |

## Tests (`tests/test_lens_vignette.py`)

1. **`camera_gain`, synthetic.** Scene 2880 × 1920, bright top 45 %, 400 random blocks; preview =
   sRGB of T(scene × falloff × 2^g) at 720 × 480, T a logistic in log2 light (slope 1.2, centred at
   0.25), g = 0.25 ρ² + 0.2 ρ⁴. Every ring from ρ = 0.1 out is within 0.03 EV of g (0.01 with g = 0);
   another aspect, same-size noise and a 300 × 200 preview each give their reason.
2. **`bucket`.** 105 mm f/11 → (76.11, 107.63, 9.51, 13.45). An edge value lands in one bucket only.
   f/10 shares a bucket with f/11; f/8 does not.
3. **Roll-off.** G = 1 is the identity. With values below 1 and G up to 1.6, nothing reaches 1.0,
   nothing darkens and 1.0 stays 1.0. Below the knee the gain is exactly G and ratios hold.
4. **`correct_vignette`** on a TIFF with Pillow-written EXIF: centre unchanged, a mid-grey corner up
   by the profile's EV (± 0.01); unmatched focal length, missing file or LensModel → `array_equal`
   pixels and a reason.
5. **`build_profiles`**, `measure` monkeypatched (7 frames in one bucket, 3 in another): one bucket
   written, the other reported, g back within 0.02 EV; no input path or name in the file; a re-run
   on another lens keeps the first lens's rows.
6. **Opt-in, real files.** With `AUTOEDIT_RAW_CORPUS` set (as in `tests/test_raw_recover.py`),
   `measure` on 6 RAW files gives curves or reasons, every curve between 0 and 1.5 EV at 0.86.
7. **Wiring**, as `test_cli_and_api_carry_the_setting`: flag, TOML key and `make_cfg` reach RunConfig
   (default False); `raw_opts_from` has no `lens_profiles` by default, and `X/lens_profiles.toml` when
   on with `profile_dir=X`.
8. **PNG.** With the setting on, a PNG decodes `array_equal` and has no `"vignette"`.
9. **CLI.** An empty folder exits 1. With `measure` monkeypatched, `--dry-run` prints and writes
   nothing; without it, the file appears.

## Acceptance (the session)

- Both suites (system Python and `.venv`) and the stub smoke run pass; a default render is
  byte-identical to the baseline.
- `PYTHONPATH=src python3 -m autoedit.cli lens-profile curated-dolomites-summer-2026/selected_raw
  --profile .tmp/f52/profile` gives the table's six buckets, each corner EV within 0.03 of P at
  0.86, and skips one file. The file has no file name, absolute path or serial.
- Tone gate: `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw
  --render --fail-on-regression --set lens_vignette=true --set profile_dir=.tmp/f52/profile` gives
  0 dark→darker and 0 pushed into clipping. A stub `run` of the corpus with `--lens-vignette
  --profile .tmp/f52/profile`: 87 sidecars note "corners brightened", the rest say why not.
- The Checker judges a before/after sheet, one covered photo per bucket at 1024 px: lighter
  corners, no ring, sky gradients intact. The time per 22 MP photo, on and off, on a quiet machine.

## Owner decisions (ask before anything is built)

| Decision | Proposed | Reverse it by |
|---|---|---|
| Build at all | Yes, from the camera preview only; the content estimate fails (above) | Drop F52; F46's "not built" stands |
| Strength | As the camera's JPEG (P at 100 %); this Canon applies 0.62–0.80 of Adobe's full correction, by ISO | An amount setting; about 1.25 matches Adobe at ISO 100 |
| Bucket and minimum | Half a stop of focal length × one stop of aperture; 5 photos | `bucket()`, `_VIG_MIN_FRAMES` |
| No bucket for a photo | Left as decoded with a note; no nearest bucket, no interpolation | Interpolate the two nearest focal buckets at the same aperture |
| Default | Off | `lens_vignette = true` in `defaults.toml` after the blind sheet and the owner's vote |
| Profile location | `lens_profiles.toml` in the profile folder; a re-run replaces that lens's rows | A `--lens-profiles FILE` option |
| Not used | A frame's own preview at decode (p90 0.12 EV vs 0.03, plus a second decode); Canon's strength tag (a maker-note parser for one brand); a public lens database (none on disk, new data to ship) | A feature of its own |

### Decided (owner, 2026-11-22)

Build it. Strength as the camera's JPEG (no amount setting). Opt-in, default byte-identical. Bucket of
half a stop by one stop, 5 photos minimum, a photo with no bucket left as decoded, the profile file in
the profile folder. The profile holds only camera and lens model names and numbers (no path, file name
or serial). Nothing is read from or shipped from Adobe's profiles. T1 built: `lens.py` (`lens_key`,
`bucket`, `camera_gain`, `measure`, `build_profiles`, `write_profiles`, `correct_vignette`) and
`tests/test_lens_vignette.py` tests 1-6 (the EXIF fixture is a PNG: this Pillow drops the Exif IFD when saving a TIFF).
