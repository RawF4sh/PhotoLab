# F7. Noise

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F7.
Comes after [F5](F5-black-white-point.md) (same files, same pattern for an opt-in op).

## Step one: the measurement (done, 2026-11-11)

The plan asked to measure before building. Eight photos at ISO 10,000 and 12,800 from the
Switzerland corpus, default settings, full resolution. Noise is the standard deviation of the
fine grain (the plane minus a 2 px blur) in flat dark patches of the source (48 px, mean luma 0.03
to 0.25, no structure), in 8-bit levels:

| Photo | ISO | Patches | Luma noise, source to edit | Chroma noise, source to edit | Patch brightness |
|---|---|---|---|---|---|
| 0542 (RAW) | 12,800 | 640 | 7.6 to 17.9 | 17.1 to 24.7 | x2.59 |
| 0757 (camera JPEG) | 12,800 | 27 | 4.4 to 8.2 | 0.7 to 1.6 | x2.28 |
| 0540 (camera JPEG) | 10,000 | 26 | 5.4 to 8.0 | 0.7 to 1.2 | x1.64 |

Five more photos (snow scenes) have no flat dark patch. The edit applied `exposure +0.80` and
`shadows_ev` up to `+0.84` to these frames. At 100 % the RAW shows dense colour speckle after the
lift; the camera JPEGs show grain and colour blotches. **Noise is visible at 100 %, so the op is
built.**

A prototype of the op below, on the same two ISO 12,800 photos, at strength 100:

| Photo | Luma noise | Chroma noise | Mid-frequency detail kept in textured patches |
|---|---|---|---|
| 0542 (RAW) | 17.2 to 4.9 | 24.7 to 0.1 | 95 % |
| 0757 (JPEG) | 7.8 to 2.9 | 1.6 to 0.02 | 97 % |

Edges and point lights (stars, lamps) survive in the 100 % crops.

## Goal

A noise-reduction op that works chroma first and scales with ISO and with the lift, placed
directly before sharpening. It changes pixels on high-ISO photos, so it ships **off**
(`denoise = false`). The owner's blind vote decides.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is noise reduction on by default? | No. `--denoise` or `[run] denoise = true` | Set it in `defaults.toml` after the blind vote |
| From which ISO does the rule act? | From 800, full from 6,400 | The `denoise` rule in `rules.toml` |

## The op

`denoise`, a strength from 0 to 100, in `ops.py`, between the split-tone step and `sharpen`.

1. **Measure the grain.** `sigma` is a robust estimate of the luma grain of the op's input: 1.4826
   times the median absolute deviation of (luma minus a 1 px blur), on every second pixel.
2. **Chroma first.** The three chroma planes (`rgb - luma`) are filtered at half resolution with
   the guided filter, guided by a slightly blurred luma, so colour does not bleed across real
   edges and grain in the guide is not read as an edge. The filtered chroma replaces the original
   with weight `min(1, 1.6 * strength)`: chroma reaches full effect first.
3. **Luma second, and less.** The luma is filtered with the self-guided filter, radius 2, with
   `eps = (2 * sigma)^2`: variations of the size of the grain are smoothed, edges much larger than
   the grain are kept. The weight is `strength`, reduced to 40 % of it from mid-tones up (luma 0.30
   to 0.70), so texture in brighter tones keeps its grain.
4. Recombine: `luma + chroma`.

The op adapts to what it finds: on a clean frame `sigma` is tiny and the luma filter changes
almost nothing. Strength 0 returns the input object.

It reads neighbours and a whole-frame statistic, so it is in `SPATIAL_OPS` and never runs on
strips.

## Files

| File | Change |
|---|---|
| `src/autoedit/edit/ops.py` | `denoise` and `_grain_sigma`; the name in `OP_NAMES`, `OP_BOUNDS`, `STEPS`, `SPATIAL_OPS`; one line in `apply_ops` |
| `src/autoedit/contract.py` | `RunConfig.denoise: bool = False` |
| `src/autoedit/pipeline.py` | `"denoise"` in `_OPT_IN_RULES`; drop the op after `decide` unless `cfg.denoise` |
| `src/autoedit/cli.py`, `src/autoedit/web/service.py`, `src/autoedit/configs/defaults.toml` | `--denoise`, `[run] denoise`, `"denoise"` in `_ALLOWED` |
| `src/autoedit/configs/rules.toml` | the opt-in rule set `[opt_in.denoise.*]` |
| `tests/test_denoise.py` | new |

## `edit/ops.py`

Protected file. Add below `sharpen`. Imports: `guided_filter` and `gaussian_blur` are already
imported from `.filters`.

```python
def _grain_sigma(l: np.ndarray) -> float:
    """Robust estimate of the fine grain of a luma plane: 1.4826 times the median absolute
    deviation of (luma - a 1 px blur), on every second pixel."""
    s = l[::2, ::2] if min(l.shape) >= 64 else l
    d = s - gaussian_blur(s, 1.0)
    return float(1.4826 * np.median(np.abs(d - np.median(d))))


def denoise(rgb: np.ndarray, amt: float) -> np.ndarray:
    """Noise reduction, chroma first (F7). `amt` 0..100.

    Colour noise is coarse and ugly, luma grain is fine and tolerable, so the two get different
    treatment. Chroma (rgb - luma) is filtered at half resolution with the guided filter, guided
    by a lightly blurred luma: colour stays inside real edges, and grain in the guide is not read
    as an edge. Luma is filtered with the self-guided filter whose threshold follows the grain
    measured on the input (`_grain_sigma`), at reduced weight from the mid-tones up. A clean
    frame measures a tiny grain and is left almost alone. 0 is the identity."""
    a = min(max(amt, 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    l = luma(rgb)
    h, w = l.shape
    sigma = _grain_sigma(l)
    small = (max(1, w // 2), max(1, h // 2))
    guide = gaussian_blur(_resize_float(l, small), 1.0)
    radius = max(2, int(round(0.0016 * max(h, w))))
    weight_c = min(1.0, 1.6 * a)
    out = np.empty(rgb.shape, dtype=np.float32)
    for i in range(3):
        c = rgb[..., i] - l
        smooth = _resize_float(guided_filter(guide, _resize_float(c, small), radius, eps=0.05 * 0.05), (w, h))
        out[..., i] = c + weight_c * (smooth - c)
    l_smooth = guided_filter(l, l, 2, eps=max((2.0 * sigma) ** 2, 1e-7))
    weight_l = a * (1.0 - 0.6 * np.clip((l - 0.30) / 0.40, 0.0, 1.0))
    out += (l + weight_l * (l_smooth - l))[..., None]
    return out
```

Registrations:

- `OP_NAMES`: `"denoise"` before `"sharpen"`. `OP_BOUNDS`: `(0.0, 100.0)`.
- `STEPS`: a new step `("denoise",)` directly before `("sharpen",)`.
- `SPATIAL_OPS`: add `"denoise"`.
- `apply_ops`, directly before the `sharpen` line:

```python
    if settings.get("denoise", 0.0):  # before sharpening, so the sharpener does not see the grain
        out = denoise(out, settings["denoise"])
```

## `pipeline.py`

- `_OPT_IN_RULES = ("levels", "denoise")`: the rule set joins the rules only when `cfg.denoise`
  is true.
- The block that drops opt-in ops a decider may still propose (the LLM decider can name any op)
  gets a second part, next to the `levels` one:

```python
        if not cfg.denoise:  # noise reduction is opt-in until it has passed a blind vote (F7)
            for s in (global_settings, *region_settings.values()):
                s.pop("denoise", None)
```

## `rules.toml`

The rule is an opt-in rule set (the mechanism F5 introduced: `rules.load(path, enable=[...])`
merges `[opt_in.NAME.<scene>.<block>]` into the scene blocks only when the run setting NAME is
on). Add after the `levels` tables at the end of the file:

```toml
# Noise reduction (F7, run setting `denoise`). Nothing below ISO 800, full from ISO 6400; 40 % of
# that for the ISO alone, the rest with the exposure lift, because the lift is what makes shadow
# noise visible. The op measures the grain itself.
[opt_in.denoise.people.global]
denoise = "if(pro_stats_valid > 0, 100 * clamp((iso - 800) / 5600, 0, 1) * clamp(0.4 + 0.75 * planned_exposure, 0.4, 1), 0)"

[opt_in.denoise.landscape.global]
denoise = "if(pro_stats_valid > 0, 100 * clamp((iso - 800) / 5600, 0, 1) * clamp(0.4 + 0.75 * planned_exposure, 0.4, 1), 0)"

[opt_in.denoise.other.global]
denoise = "if(pro_stats_valid > 0, 100 * clamp((iso - 800) / 5600, 0, 1) * clamp(0.4 + 0.75 * planned_exposure, 0.4, 1), 0)"
```

## Tasks

One Builder (Sonnet, high: `ops.py` is a protected file).

| Task | Content | Proving command |
|---|---|---|
| T1 | everything in the Files table, tests 1 to 10 | `PYTHONPATH=src python3 -m pytest tests/test_denoise.py tests/test_parallel.py tests/test_ops.py tests/test_levels.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |

## Tests: `tests/test_denoise.py`

Use a fixed random seed. "Grain" below is the standard deviation of (plane minus a 2 px
`gaussian_blur`).

| # | Test | Asserts |
|---|---|---|
| 1 | `test_identity` | `denoise(x, 0) is x`; `apply_ops(x, {"denoise": 0.0}) is x` |
| 2 | `test_reduces_noise_on_a_flat_frame` | A 256 by 256 frame of constant `(0.20, 0.18, 0.16)` plus independent Gaussian noise of sigma 0.03 per channel: at strength 100 the luma grain drops by more than 55 % and the grain of `R - G` by more than 90 %; the mean of each channel moves by less than 0.003; at strength 50 the luma grain lies between the values for 0 and 100 |
| 3 | `test_keeps_an_edge` | The same noise on a frame whose left half is 0.15 and right half 0.55: after strength 100 the mean of each half moves by less than 0.004 and the difference between the two columns next to the edge (averaged over the rows) is still more than 85 % of 0.40 |
| 4 | `test_leaves_a_clean_frame_almost_alone` | A smooth gradient frame without noise changes by less than 0.004 anywhere at strength 100 |
| 5 | `test_brighter_tones_get_less_luma_smoothing` | With the same noise on a flat 0.15 frame and on a flat 0.75 frame, the relative drop of the luma grain is larger on the dark one |
| 6 | `test_shapes_and_small_frames` | Frames of 16 by 16, 33 by 70 and 301 by 127 come back with the same shape, dtype float32 and finite values |
| 7 | `test_registered_before_sharpen` | The name is in `OP_NAMES`, `OP_BOUNDS` and `SPATIAL_OPS`; `STEPS[-2] == ("denoise",)`; `apply_ops(x, {"denoise": 60, "sharpen": 10})` equals `sharpen(denoise(x, 60), 10)` (`array_equal`) |
| 8 | `test_rule_golden` | A plain `rules.load(path)` has no `denoise` rule in any scene; `rules.load(path, enable=["denoise"])` has it in the three global blocks. With `rules.eval_expr`: ISO 100 gives 0; ISO 6400 with `planned_exposure = 0.8` gives 100; ISO 12800 with `planned_exposure = 0` gives 40; ISO 3600 with `planned_exposure = 0.8` gives 50; `pro_stats_valid = 0` gives 0. The three global blocks hold the same expression |
| 9 | `test_setting_gates_the_rule` | A noisy synthetic JPEG saved with the EXIF ISO tag set to 6400 (Pillow `Exif`, tag 0x8827 in the Exif sub-directory), stub backend: the default sidecar has no `denoise` in `global_settings`; with `denoise=True` it has a positive value and the luma grain of the output is lower than in the default output |
| 10 | `test_cli_and_api_carry_the_setting` | `--denoise`, `[run] denoise = true` in a `--config` file and `service.make_cfg({"denoise": True})` each set `cfg.denoise` |

## Acceptance checks (the session runs these)

1. Both test suites green; default renders byte-identical to the baseline (the setting is off).
2. The measurement of step one again with `denoise` on: noise in the flat dark patches and detail
   kept in textured patches, for the three photos of the table.
3. 100 % crops of the ISO 12,800 photos without and with the op, judged by the Checker.
4. Tone gate with `--set denoise=true`: 0 and 0.
5. Checker review of `ops.py`.

## Known limits

- On a noisy frame, colour detail finer than a few pixels that has no brightness difference is
  averaged away: the chroma is filtered at half resolution, guided by the luma. On a photo that
  needs noise reduction this detail is mostly noise. A clean frame keeps it (the filter's weight
  follows the measured grain).
- The rule keys on EXIF ISO. A file without EXIF gets no noise reduction from the rule; an
  explicit `--set global.denoise=60` works on any file.

## Result

Built on 2026-11-11, in the unattended run. Status: done, off by default (`--denoise`).

- **Built** by one Builder (Sonnet, high) from this spec.
- **Measured** with `denoise` on, on the patches of step one, in 8-bit levels
  (source / edit without the op / edit with the op):

  | Photo | ISO | Luma noise | Chroma noise |
  |---|---|---|---|
  | 0542 (RAW) | 12,800 | 7.6 / 17.9 / 7.5 | 17.1 / 24.7 / 0.08 |
  | 0757 (camera JPEG) | 12,800 | 4.4 / 8.2 / 4.0 | 0.7 / 1.6 / 0.03 |
  | 0540 (camera JPEG) | 10,000 | 5.4 / 8.0 / 3.8 | 0.7 / 1.2 / 0.03 |

  The lifted photo ends with no more grain than its source had, and the colour speckle is gone.
- **Found and fixed by the session:** the oversaturation gate compares the edit with the source
  pixel by pixel. With a noisy source that stamped the colour noise back onto the result (7.0
  levels with the gate, 0.4 without it). With `denoise` on, the gate now compares against a
  denoised copy of the source: 0.08 levels. Test:
  `test_the_colour_gate_does_not_bring_the_noise_back`.
- **Review** (Checker, Opus, high): no blocking finding. Fixed after it:
  - The chroma filter did not follow the measured grain, so a clean frame lost fine colour
    detail (3 px colour stripes of equal brightness lost 31 % of their chroma at strength 40).
    Its weight now fades out below a grain of one 8-bit level; the stripes keep more than 98 %.
  - The denoised copy of the source that the oversaturation gate compares against is made once
    per photo and reused by the banding re-render and by the gate after a reference match (that
    second gate still compared against the noisy source). One call costs 1.1 to 1.8 s at 22 MP.
  - New tests: clean colour detail, the handed-in gate reference, the same result for one and
    four threads.
- **Known costs, not changed:** with a skin guard mask the global pass renders twice, so the op
  runs twice; a denoise given for one region only (`--set sky.denoise=60`) gets no denoised gate
  reference.
- **Tone gate** with `--set denoise=true`: 0 dark to darker, 0 clipping, 0 shadow clipping, no
  photo newly pulled back. The corpus is mostly low ISO: the op was active on 8 of the 110 photos.
- **Default output:** byte-identical to the baseline renders; both test suites green.
- **Evidence:** `out-features/F7/` (100 % crops without and with the op).
- **Open for the owner:** look at the crops and decide whether `denoise` becomes the default.
  There is no blind gallery: at gallery size noise is not visible.
