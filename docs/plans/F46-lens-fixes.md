# F46. Lens fixes: lateral chromatic aberration (vignetting not built)

Status: spec (2026-11-19). Size S. Off by default. The approved request had two parts; the
measurements below keep one.

## Goal

1. **Lateral chromatic aberration (built).** Red and blue fringes along edges toward the corners:
   the lens makes the red and blue images a little larger or smaller than the green one. Scale red
   and blue about the frame centre so they line up with green. The scale is found per photo, by a
   small search on its edges. No lens profile, no new dependency.
2. **Vignetting without a lens profile (not built).** A radial falloff fitted on the frame alone was
   measured against the camera's own correction and is off by up to 0.8 EV on landscapes, as much as
   the vignette itself. See "Measured" and the decisions table.

## Measured (2026-11-19)

Dolomites corpus: Canon EOS 5D Mark III, EF 24-105mm f/4L, 110 CR2 files, default decode
(5796 × 3870). "Fringe" is the misalignment of a channel against green at the frame corner, in
pixels: `(s − 1) × R`, with `s` the scale found and `R` the half-diagonal.

**Lateral chromatic aberration (the search of this spec, run on 17 photos)**

| Focal length | Photos | Red fringe (px) | Blue fringe (px) |
|---|---|---|---|
| 24 mm | 0032, 0034, 0060, 0080, 0086 | +2.2 to +2.8 | +0.2 to +0.8 |
| 28 mm | 0001, 0091, 0093 | +1.2 to +2.7 | +0.6 to +0.8 |
| 35 mm | 0048 | +1.1 | +0.3 |
| 45 mm | 0013 | +0.9 | +0.3 |
| 50 to 58 mm | 0005, 0020 | +0.3 to +0.5 | −0.1 to −0.2 |
| 73 mm | 0002 | 0.0 | −0.1 |
| 105 mm | 0004, 0007, 0008, 0012 | −1.1 to −1.9 | −0.3 to −1.1 |

- The sign flips from wide to tele, as lateral CA of a zoom does, and frames of one focal length
  agree within about ±0.3 px.
- The correlation of the high-passed red with green rises from 0.36–0.90 as decoded to 0.65–0.92
  at the scale found (the 16 photos with a fringe; 0002 stays at 0.31).
- At 100 % the red fringes along dark cracks in a 24 mm corner (0060) are gone after the
  correction (crop made by the planner, not committed).
- Three camera JPEGs (EF 70-200mm at 70 mm, `out-f36/panel/3D6A1613`, `1639`, `2191`): fringes of
  +0.02 to +0.06 px. The camera corrected them already; the 0.3 px threshold leaves them alone.
- Synthetic frames (800 × 1200), a known fringe of −0.7, +0.14, +0.9, +3.0 and −5.0 px: found
  within 0.03 px.
- Time on a 22 MP frame: 1.3 to 1.7 s for both channels (search and resample), with OpenCV
  installed; without it `box_blur` runs its numpy path and takes longer.

**Vignetting**

The camera's own correction was measured from the JPEG preview inside each CR2: a tone curve
fitted on the centre (radius below 0.25 of the half-diagonal) maps the RAW to the preview; the gain
the preview needs beyond that curve, per ring, is what the camera added. A fit on the frame alone
(the approved idea: per ring, the median outward horizontal log-gradient, fitted as
`a·r² + b·r⁴`) was run on the same photos. Correction at 0.88 of the half-diagonal, in EV:

| Photo | Focal, f | Camera | Fit on the frame |
|---|---|---|---|
| 0001 | 28 mm f/4 | 0.62 | 0.34 |
| 0032 | 24 mm f/7.1 | 0.48 | 0.42 |
| 0034 | 24 mm f/7.1 | 0.34 | 0.48 |
| 0050 | 24 mm f/6.3 | 0.21 | 0.96 |
| 0066 | 50 mm f/4 | 0.50 | −0.06 |
| 0029 | 35 mm f/5 | 0.34 | 1.17 |
| 0002 | 73 mm f/5 | 0.18 | 0.49 |
| 0004 | 105 mm f/8 | 0.26 | 0.16 |
| 0008 | 105 mm f/11 | 0.11 | −0.01 |

- The fit on the frame is off by −0.56 to +0.83 EV (mean absolute error 0.35 EV, as large as the
  vignette itself, mean 0.34). A landscape falls off toward the top (sky) and the bottom
  (foreground) by itself, and the fit reads that as vignetting. On 0029 it would brighten the
  corners by 1.2 EV where the camera adds 0.3.
- The camera's correction at 0.9 of the half-diagonal (fitted curve), measured on all 110 photos:
  within 0.07 EV across the frames of four lens settings (24 mm f/7.1, 28 mm f/11, 35 mm f/11,
  73 mm f/11), but the 20 frames at 105 mm f/11 range from 0.10 to 0.78 EV (median 0.19), and 4
  frames fit a darkening. The camera's own tone processing leaks into a single frame's measurement.
  The median over the frames of one lens setting would be reliable; a single frame is not.

## Design (chromatic aberration only)

### Where it runs

Stage 1 of the pipeline, decode. `imageio.load_image` applies it to the decoded picture, after the
format's decode (and after the RAW development of `_decode_raw`), at full resolution. Then
`Pipeline.process_one` makes the analysis proxy from the corrected picture as before. The order of
the stages does not change.

Why there:

- The fringe is a fault of the capture. Every later stage should see the corrected picture: SAM 3
  on the proxy, the stats (fringes are saturated colour at edges), the gates (they compare the
  edit with `img.rgb`), and sharpening and clarity, which would widen the fringes.
- It needs no mask. F31 makes its capture corrections in apply only because the sky gradient is
  fitted inside the sky mask.
- Full resolution, because a 2.7 px fringe is 0.7 px on the 1536 px proxy.
- A creative vignette (compose.toml `frame_vignette`, `subject_vignette`, an instruction's "add a
  vignette", F18) is applied in stage 7 (apply). Lateral CA correction changes no brightness, so it
  cannot undo one in any case.
- Every caller that decodes with `decode=raw_opts_from(cfg)` (the pipeline, `presegment`, the web
  preview, the tone report's render) gets the same correction, so a render worker's masks and the
  render agree.

### `src/autoedit/lens.py` (new)

Reference code, checked on the synthetic frames and the real photos above:

```python
"""Lens corrections of the capture (F46): lateral chromatic aberration. Part of the decode
(imageio.load_image), at full resolution, before the analysis proxy is made."""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from .edit.filters import box_blur

# Constants of the correction, like the op constants in edit/ops.py (the decode reads no rules file).
_CA_MAX_PX = 6.0        # largest fringe searched, either way, in pixels at the frame corner
_CA_STEP_PX = 0.25      # search step at the corner; a parabola through the best three refines it
_CA_MIN_PX = 0.3        # a channel whose fringe at the corner is smaller is left as it is
_CA_SAMPLES = 200_000   # the strongest green edges that are compared
_CA_INNER = 0.5         # edges from this share of the half-diagonal outward (lateral CA grows with the radius)
_CA_HP = 2              # radius of the box blur the high-pass subtracts


def scale_channel(ch: np.ndarray, s: float) -> np.ndarray:
    """`ch` resampled so that out(p) = ch(c + s (p - c)), c the frame centre, bilinear (Pillow).
    The channel is padded with its own edge values first, so the border repeats its pixels instead
    of turning black. s == 1.0 returns `ch` itself."""
    if s == 1.0:
        return ch
    k = int(np.ceil(_CA_MAX_PX)) + 2
    p = np.pad(ch, k, mode="edge")
    h, w = p.shape
    im = PILImage.fromarray(np.ascontiguousarray(p, dtype=np.float32), mode="F")
    t = im.transform((w, h), PILImage.AFFINE, data=(s, 0.0, w / 2 * (1 - s), 0.0, s, h / 2 * (1 - s)),
                     resample=PILImage.BILINEAR)
    return np.asarray(t, dtype=np.float32)[k:-k, k:-k]


def _bilinear(a: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """`a` at float index coordinates (pixel centres at integers), clamped to the frame."""
    h, w = a.shape
    ys = np.clip(ys, 0.0, h - 1.001)
    xs = np.clip(xs, 0.0, w - 1.001)
    y0, x0 = ys.astype(np.int64), xs.astype(np.int64)
    fy, fx = (ys - y0).astype(np.float32), (xs - x0).astype(np.float32)
    p, q, r, t = a[y0, x0], a[y0, x0 + 1], a[y0 + 1, x0], a[y0 + 1, x0 + 1]
    return p + (q - p) * fx + (r - p) * fy + (p - q - r + t) * fx * fy


def ca_scale(green: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    """(s, R): the scale about the frame centre at which `other` lines up best with `green`, so that
    other(c + s (p - c)) matches green(p); R is the half-diagonal in pixels, and (s - 1) * R the
    fringe at the corner. Compared: the high-passes (minus a box blur) at the _CA_SAMPLES strongest
    green edges beyond _CA_INNER of the half-diagonal, by correlation, for fringes from -_CA_MAX_PX
    to +_CA_MAX_PX in steps of _CA_STEP_PX. Returns s = 1.0 when the best is at an end of the range
    or there are fewer than 1000 candidate pixels."""
    # ponytail: one scale per channel (the linear term of lateral CA); a radial polynomial if a lens shows wavy fringes
    h, w = green.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    R = float(np.hypot(cy, cx))
    hg = green - box_blur(green, _CA_HP)
    ho = (other - box_blur(other, _CA_HP)).astype(np.float32)
    gy, gx = np.gradient(green)
    mag = np.hypot(gy, gx)
    rho = np.hypot((np.arange(h, dtype=np.float32) - cy)[:, None],
                   (np.arange(w, dtype=np.float32) - cx)[None, :]) / R
    cand = (rho > _CA_INNER) & (rho < 0.98)
    vals = mag[cand]
    if vals.size < 1000:
        return 1.0, R
    k = min(_CA_SAMPLES, vals.size)
    thr = np.partition(vals, vals.size - k)[vals.size - k]
    ys, xs = np.nonzero(cand & (mag >= thr))
    a = hg[ys, xs]
    a = (a - a.mean()) / (a.std() + 1e-9)
    ys, xs = ys.astype(np.float32), xs.astype(np.float32)
    step = _CA_STEP_PX / R
    n = int(round(_CA_MAX_PX / _CA_STEP_PX))
    scales = 1.0 + step * np.arange(-n, n + 1)
    corr = np.empty(scales.size)
    for i, s in enumerate(scales):
        b = _bilinear(ho, cy + s * (ys - cy), cx + s * (xs - cx))
        corr[i] = float(np.mean(a * (b - b.mean()) / (b.std() + 1e-9)))
    i = int(np.argmax(corr))
    if i in (0, scales.size - 1):
        return 1.0, R
    c0, c1, c2 = corr[i - 1], corr[i], corr[i + 1]
    den = c0 - 2.0 * c1 + c2
    off = 0.5 * (c0 - c2) / den if den < 0 else 0.0
    return float(scales[i] + off * step), R


def correct_ca(rgb: np.ndarray) -> tuple[np.ndarray, dict]:
    """Red and blue scaled about the centre onto green (float HxWx3 in [0, 1]). A channel whose fringe
    at the corner is below _CA_MIN_PX keeps its values bit for bit; when both do, `rgb` itself is
    returned. Report: {"red": {"scale", "corner_px", "applied"}, "blue": {...}}."""
    green = np.ascontiguousarray(rgb[..., 1], dtype=np.float32)
    out, report = None, {}
    for name, c in (("red", 0), ("blue", 2)):
        ch = np.ascontiguousarray(rgb[..., c], dtype=np.float32)
        s, R = ca_scale(green, ch)
        px = (s - 1.0) * R
        applied = bool(abs(px) >= _CA_MIN_PX)
        report[name] = {"scale": round(s, 6), "corner_px": round(px, 2), "applied": applied}
        if applied:
            if out is None:
                out = rgb.copy()
            out[..., c] = scale_channel(ch, s)
    return (rgb if out is None else out), report
```

Notes for the Builder:

- Bilinear resampling stays inside the range of its neighbours, so no value leaves [0, 1] and no
  clipping is added.
- `ca_scale` with `s` found means "other is larger by s"; `scale_channel(other, s)` undoes it. The
  synthetic test below fixes this direction.
- Pillow's `AFFINE` with `data=(s, 0, W/2·(1 − s), 0, s, H/2·(1 − s))` equals `_bilinear` at
  `c + s·(p − c)` with `c = ((W − 1)/2, (H − 1)/2)` in index coordinates (checked to 1.5e-5).

### `src/autoedit/imageio.py`

- `_LENS_DEFAULTS = {"lens_ca": False}` beside `_RAW_DEFAULTS`.
- `raw_opts_from(cfg)` returns the keys of both dicts:
  `{k: getattr(cfg, k) for k in (*_RAW_DEFAULTS, *_LENS_DEFAULTS) if hasattr(cfg, k)}`.
- `load_image`: rename today's body to `_load_image(path, decode)` without any change, and make
  `load_image` call it, then:

```python
    img = _load_image(path, decode)
    if decode and decode.get("lens_ca"):
        from . import lens   # here, not at the top: lens imports edit.filters, and edit imports this module
        img.rgb, img.decode_info["lens"] = lens.correct_ca(img.rgb)
    return img
```

- The docstring of `load_image`: the decode options are the RAW development options and the lens
  correction (F46); the lens correction also applies to JPEG, TIFF and the other formats.
- With `lens_ca` off nothing new runs: default output stays byte-identical.

### Settings

`RunConfig.lens_ca: bool = False` (comment: `# scale red and blue onto green about the centre
(F46); off → decoded as it is`), wired as `highlight_hold` is:

- `configs/defaults.toml`: `lens_ca = false` under `[decode]`, with a one-line comment.
- `cli.py`: `"lens_ca": "lens_ca"` in `_dec_map`; `--lens-ca` (`store_true`, `default=None`,
  help: "line up red and blue with green to remove colour fringes toward the corners (opt-in)");
  `"lens_ca": getattr(args, "lens_ca", None)` in the override map of `load_run_config`.
- `web/service.py`: `"lens_ca"` in `_ALLOWED`.
- `configs/settings.toml`: `[setting.lens_ca]`, label "Remove colour fringes", group "quality",
  help "Lines up the red and blue parts of the picture with the green to remove colour fringes
  toward the corners."; then the snapshot: `AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m
  pytest tests/test_settings_schema.py -q`.
- `pipeline.py`, next to the `raw develop` note:

```python
        lens_rep = (img.decode_info or {}).get("lens")
        if lens_rep:
            parts = [f"{c} {r['corner_px']:+.1f} px" + ("" if r["applied"] else " (left, below 0.3 px)")
                     for c, r in lens_rep.items()]
            notes.append("lens: colour fringe at the frame corner " + ", ".join(parts))
```

### The mask cache

Nothing changes in `segment/cache.py`. Both caches key on the proxy's pixels (`DiskMaskCache` on
`pixel_digest`, `CachingSegmenter` on path, shape, mtime and a pixel digest), so a corrected photo
is segmented once more and then cached, and a photo where nothing was applied hits the old entry.

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder (Sonnet, medium) | `lens.py`, the `imageio.py` changes, tests 1 to 6 | `PYTHONPATH=src python3 -m pytest tests/test_lens.py tests/test_raw_develop.py tests/test_imageio_fidelity.py tests/test_workflows.py -q` |
| T2 | Builder (Sonnet, medium) | the settings wiring, the pipeline note, test 7, the snapshot | `PYTHONPATH=src python3 -m pytest tests/test_lens.py tests/test_settings_schema.py tests/test_cli.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Tweaker (Haiku, low) | a CHANGELOG line | none |
| T4 | Session | the acceptance below | — |

T1 and T2 share `tests/test_lens.py`: run them one after the other. F44 also changes `imageio.py`:
when both are scheduled, build F44 first.

## Tests (`tests/test_lens.py`)

A synthetic scene with hard edges everywhere, used by tests 1 to 6:

```python
def _scene(h=800, w=1200, seed=0):
    rng = np.random.default_rng(seed)
    g = np.full((h, w), 0.3, np.float32)
    for _ in range(300):
        y0, x0 = rng.integers(0, h), rng.integers(0, w)
        g[y0:y0 + rng.integers(5, 80), x0:x0 + rng.integers(5, 80)] = rng.random()
    return gaussian_blur(g, 0.8)          # from autoedit.edit.filters
```

A red channel with a fringe of `px` pixels at the corner: `scale_channel(g, 1 / (1 + px / R))`, with
`R = hypot((h − 1)/2, (w − 1)/2)`.

1. `scale_channel`: `s = 1.0` returns the same object; a flat 0.5 frame scaled by 1.003 is 0.5 to
   1e-6 everywhere, border included (no black rim); for the scene `g` and `s = 1.003`, in the
   interior (10 px in) the result equals `_bilinear(g, cy + s·(y − cy), cx + s·(x − cx))` within 5e-5
   (measured 1.5e-5).
2. Round trip: `scale_channel(scale_channel(g, s), 1/s)` is within 0.002 of `g` in mean absolute
   difference in the interior, for `s = 1.001`.
3. `ca_scale` finds fringes of −0.7, +0.9, +3.0 and −5.0 px within 0.05 px.
4. `correct_ca` on (red fringe +0.9 px, green, blue fringe +0.14 px): red applied, its interior mean
   absolute difference to green at least 50 % lower; blue not applied and `array_equal` to its input;
   the report gives `corner_px` 0.9 ± 0.05 for red.
5. A grey frame (the scene in all three channels): `correct_ca` returns the input object, both
   channels not applied.
6. `load_image` on a PNG written to `tmp_path` (a PNG, so no compression blurs the fringe) with a red
   fringe of +2 px: with `decode={"lens_ca": True}` the decode_info has `lens.red.applied` true and
   red lines up with green better than without; with `decode=None` and with `decode={"lens_ca":
   False}` the pixels are `array_equal` to the plain decode and `decode_info` has no `"lens"`.
7. Wiring, following `test_cli_and_api_carry_the_setting` in `tests/test_denoise.py`: `--lens-ca`,
   `[decode] lens_ca = true`, `service.make_cfg({"lens_ca": True})` each reach `RunConfig`; the
   default is False; `raw_opts_from(RunConfig(lens_ca=True))["lens_ca"]` is True.

## Acceptance (the session)

- Both suites (system Python and `.venv`); the stub smoke run; a default render byte-identical to
  the baseline.
- On the Dolomites corpus with `lens_ca` on: the red and blue fringes per photo, grouped by focal
  length (from EXIF), against the table above; the time per photo.
- A sheet of 100 % corner crops, before and after, of one 24 mm, one 50 mm and one 105 mm photo,
  judged by the Checker.
- Tone gate with the setting on: `PYTHONPATH=src python3 scripts/tone_report.py
  curated-dolomites-summer-2026/selected_raw --render --fail-on-regression --set lens_ca=true`:
  0 dark to darker and 0 pushed into clipping, and the per-photo `safety_fallback` the same as in
  the default run (the correction changes no tone).

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Vignetting | Not built: the fit on the frame is off by up to 0.8 EV on landscapes, and a single frame's preview by up to 0.6 EV (measured above) | Design B, a feature of its own (M): before the batch, measure the camera's correction from each RAW's preview, take the median per lens, focal length and aperture from EXIF, and apply that median; single frames and JPEGs are left alone. Needs the owner's yes and a blind vote |
| Default | Off (`--lens-ca`, `[decode] lens_ca`) | `lens_ca = true` under `[decode]` in `defaults.toml`, after the crops and the owner's blind vote |
| Inputs | Every format; camera JPEGs come out at 0.02 to 0.06 px and are left alone by the threshold | Apply only to RAW: `is_raw(path)` in `load_image` |
| Threshold | 0.3 px at the corner | `_CA_MIN_PX` in `lens.py` |
| Where the constants live | Module constants in `lens.py`, like the op constants (the decode reads no rules file) | RunConfig fields |
| Longitudinal (axial) CA, purple fringing on blown edges | Out of scope: a scale cannot fix it | A feature of its own |

## Result

Built on 2026-11-19 by a Sonnet Builder, reviewed by the Checker (Opus, high). Corner crops: on a 24 mm photo (red +2.8 px, blue +0.8 px) the red fringes along the cracks are gone; at 50 mm and 105 mm no visible change and nothing new. Green is bit-identical, mean RGB moves at most 0.04 level. About 1.6 to 2.0 s more per 22 MP photo. Tone gate with `lens_ca` on: 0 dark made darker, 0 pushed into clipping. One photo's safety fallback differs from the default run: 0103 (snow) keeps 75 % of its edit instead of all of it and is flagged for review (median 0.619 to 0.608). Default output byte-identical. Open nits: the benchmark decode cache keys on `imageio.py` only (`experiments.py`), and pick and blind-test decodes at half size judge the 0.3 px threshold in half-size pixels.
