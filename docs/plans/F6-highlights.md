# F6. Highlights

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F6.

## Goal

A blown sky comes out light grey today, about 232 of 255. A clipped area should stay white, with a
smooth shoulder into the tones below it. For RAW files, LibRaw's other highlight modes get a
setting so they can be evaluated.

Both parts change pixels, so both ship **off**. The owner's blind vote decides.

## Measured before the design (2026-11-11)

Two panel photos, SAM 3 masks, default settings. Mean 8-bit value of the pixels whose darkest
channel is at or above 0.985 in the source, after each apply stage:

| Stage | 3D6A1639 (4.2 % of the frame blown) | 3D6A2036 (near-white 15.8 %) |
|---|---|---|
| source | 254.1 | 253.2 |
| after the region edits | 253.9 | 250.8 |
| after the compositional moves (`sky_grad`) | 248.9 | 245.3 |
| after the global pass | 231.0 | 234.7 |
| final | 231.1 | 234.7 |

The global pass costs most: the highlight knee (its asymptote is 0.98, so nothing stays above
250), `highlights -8`, `whites -3.2`, and dehaze. `sky_grad` costs another 5 levels. No single
rule is wrong: each pull is right for a highlight that still has detail, and wrong for one that
has none.

## Design

### 1. Highlight hold (JPEG and RAW)

A finishing pass that compares the edit with the source, like the chroma guard does:

- Where the source is near white in **all three** channels, the edit may not darken the pixel.
- The result is raised back toward the source: fully where the source's darkest channel is at or
  above `full` (0.985), not at all below `start` (0.80), with a smoothstep between the two.
- It never lowers a value and never touches a pixel whose source is below `start`. A saturated
  bright colour (one channel clipped) is not held.

`start` and `full` are data: a `[highlight_hold]` block in `rules.toml`.

The first build used `start = 0.94`. Measured on the panel photos, that made skies blotchy and
stepped: the tones just under the hold were stretched about fourfold to meet the held white. With
`start = 0.80` the transition is smooth, so 0.80 is the value in the file.

Effect on the gates: a held pixel was already clipped in the source, so the *added* clipping is
0 and the tone gate is not affected. The absolute clipping of the output rises to the source's
level; a frame whose source is more than 5 % blown is then flagged for review, as its source would
be. `gates.assess` is not changed.

### 2. `raw_highlight` (RAW only)

`raw_highlight = "clip" | "blend"`, passed to LibRaw as `highlight_mode` 0 and 2. (Mode 5,
"rebuild", was built and measured, then removed: see the Result.)
`clip` is today's behaviour and stays the default; with `clip` the decode call is unchanged.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is the highlight hold on by default? | No. `--highlight-hold` or `[run] highlight_hold = true` | Set it in `defaults.toml` after the blind vote |
| Default `raw_highlight` | `clip`, as today | `[decode] highlight` in `defaults.toml` |
| Should the review gate ignore clipping that the source already had? | Not changed. With the hold on, a frame that is more than 5 % blown is flagged for review | A source-relative check in `gates.assess` (protected file, owner's call) |

## Files

| File | Change |
|---|---|
| `src/autoedit/edit/gates.py` | new function `highlight_hold` |
| `src/autoedit/edit/apply.py` | optional `highlight_hold` argument; the pass runs after the final clip and before the chroma guard |
| `src/autoedit/pipeline.py` | read `[highlight_hold]`, pass it when `cfg.highlight_hold`, one sidecar note |
| `src/autoedit/imageio.py` | `raw_highlight` in the RAW options and in `_decode_raw` |
| `src/autoedit/contract.py` | `RunConfig.highlight_hold: bool = False`, `RunConfig.raw_highlight` |
| `src/autoedit/cli.py`, `src/autoedit/web/service.py`, `src/autoedit/configs/defaults.toml` | `--highlight-hold`, `--raw-highlight`, `[run] highlight_hold`, `[decode] highlight`, `_ALLOWED` |
| `src/autoedit/configs/rules.toml` | the `[highlight_hold]` block |
| `tests/test_highlight_hold.py` | new |

## `edit/gates.py`

Protected file. Add below `chroma_guard`; nothing else changes.

```python
def highlight_hold(source: np.ndarray, edited: np.ndarray, start: float = 0.80,
                   full: float = 0.985) -> tuple[np.ndarray, float]:
    """Keep blown highlights white (F6).

    A highlight that is clipped in the source has no detail to recover, so pulling it down only
    turns it grey. Where the SOURCE is near white in all three channels, the edit may not darken
    the pixel: the result is raised back toward the source, fully where the source's darkest
    channel is at or above `full`, not at all below `start`, with a smoothstep between. Never
    lowers a value; a pixel whose source is below `start` is not touched.

    source/edited: float HxWx3 gamma RGB in [0,1]. Returns (rgb, fraction of pixels raised by more
    than half an 8-bit level). When no source pixel is near white, `edited` is returned as is."""
    lo = source.min(axis=-1)
    if not (lo >= start).any():
        return edited, 0.0
    t = np.clip((lo - start) / max(full - start, 1e-6), 0.0, 1.0)
    h = (t * t * (3.0 - 2.0 * t))[..., None]
    out = (edited + h * np.maximum(source - edited, 0.0)).astype(np.float32)
    raised = float(((out - edited).max(axis=-1) > 0.5 / 255.0).mean())
    return out, raised
```

## `edit/apply.py`

`apply_image(..., highlight_hold: tuple[float, float] | None = None)`. After
`out = np.clip(out, 0.0, 1.0)` and before the chroma guard:

```python
    # Keep blown highlights white: a clipped source area has no detail for the pulls to recover.
    if highlight_hold is not None:
        out, held = gates.highlight_hold(full_rgb.astype(np.float32), out, *highlight_hold)
        if report is not None:
            report["highlight_held"] = held
```

`None` (the default) runs no new code.

## `pipeline.py`

- `Pipeline` gets a field `highlight_hold: tuple[float, float] | None = None`. In `build`:
  when `cfg.highlight_hold` is true, it is `(start, full)` from the `[highlight_hold]` block of
  the rules file, with the defaults `(0.80, 0.985)` for a rules file without the block; otherwise
  `None`.
- Both `apply_image(...)` calls pass `highlight_hold=self.highlight_hold`.
- After the first call: when `gate_report.get("highlight_held", 0.0) > 0.005`, add the note
  `highlight hold: kept N% of the frame white (blown in the source)`.

## `rules.toml`

```toml
# Highlight hold (F6, opt-in: `highlight_hold` in the run settings). Where the SOURCE is near
# white in all three channels, the edit may not darken the pixel: fully from `full` up, not at all
# below `start`, a smoothstep between. The values are the darkest channel of the source pixel.
[highlight_hold]
start = 0.80
full = 0.985
```

## `imageio.py`

- `_RAW_DEFAULTS` gets `"raw_highlight": "clip"`.
- `_decode_raw`: `hl = {"blend": 2}.get(o["raw_highlight"])`; an unknown value other
  than `"clip"` raises `ValueError`. When `hl` is not `None`, `common["highlight_mode"] = hl`.
  With `"clip"` the dictionary is exactly what it is today. `info` records `highlight=<value>`.

## Tasks

One Builder (Sonnet, high: `gates.py` is a protected file).

| Task | Content | Proving command |
|---|---|---|
| T1 | everything in the Files table, tests 1 to 9 | `PYTHONPATH=src python3 -m pytest tests/test_highlight_hold.py tests/test_gates.py tests/test_raw_develop.py tests/test_quality_gate.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |

## Tests: `tests/test_highlight_hold.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_blown_pixels_stay_white` | Source: a frame with a block at 1.0, a block at 0.96 and the rest at 0.5; edited: the source times 0.9. After the hold the first block equals the source, the second lies strictly between the edited and the source value, the rest equals the edited frame. The returned fraction is the share of the first two blocks |
| 2 | `test_never_lowers_and_ignores_coloured_highlights` | For random source and edited frames: `out >= edited` everywhere. A pixel with source `(1.0, 1.0, 0.5)` is unchanged. Where the edit is brighter than the source the pixel is unchanged |
| 3 | `test_ramp_is_monotonic` | For a source that is a grey ramp from 0.90 to 1.0 and an edit 0.1 darker, the output is non-decreasing along the ramp and continuous at `start` and `full` (no step larger than the ramp's own) |
| 4 | `test_identity_without_near_white` | With a source that never reaches `start`, the returned array is the `edited` object and the fraction is 0.0 |
| 5 | `test_apply_image_runs_the_hold_only_when_asked` | `apply_image` with `highlight_hold=None` equals the call without the argument (`array_equal`); with `(0.80, 0.985)` and global settings that darken (`{"exposure": -0.5}`), a white block in the source stays above 0.98 and `report["highlight_held"]` is positive |
| 6 | `test_setting_gates_the_hold` | A synthetic JPEG with a white sky block and a mid-grey ground, stub backend: the default run and a run with `highlight_hold=False` are `array_equal`; with `highlight_hold=True` the white block's mean is higher than in the default output and at least 0.98, the ground is unchanged within 1e-6, and the notes contain `highlight hold` |
| 7 | `test_thresholds_come_from_the_rules_file` | A rules file copy whose block says `start = 0.5, full = 0.6` (written to `tmp_path`, passed as `rules_path`) gives `Pipeline.build(...).highlight_hold == (0.5, 0.6)`; a copy without the block gives `(0.80, 0.985)`; with the setting off the field is `None` |
| 8 | `test_raw_highlight_reaches_libraw` | Following the way `tests/test_raw_develop.py` replaces `rawpy`: with `raw_highlight="clip"` the keyword arguments of `postprocess` have no `highlight_mode`; `"blend"` passes 2; `"rebuild"` (removed) and any unknown value raise `ValueError` |
| 9 | `test_cli_and_api_carry_the_settings` | `--highlight-hold`, `--raw-highlight blend`, `[run] highlight_hold = true`, `[decode] highlight = "blend"` and `service.make_cfg({"highlight_hold": True, "raw_highlight": "blend"})` each reach `RunConfig` |

## Acceptance checks (the session runs these)

1. Both test suites green; default renders byte-identical to the baseline (both settings off).
2. Tone gate on the Dolomites corpus with `--set highlight_hold=true`: 0 and 0.
3. The blown pixels of the panel photos 1639, 2036 and 2191 and of the holdout photo 0110: mean
   value before and after, and a contact sheet with 100 % crops, judged by the Checker.
4. A table for `raw_highlight` on the RAW files with the most blown area: share of clipped pixels
   and colour of the near-white tones per mode, with crops.
5. Checker review of `gates.py`.

## Result

Built on 2026-11-11, in the unattended run. Status: the highlight hold is done, off by default
(`--highlight-hold`). The `raw_highlight` option was built, measured and removed again.

**Highlight hold**

- **Built** by one Builder (Sonnet, high) from this spec.
- **Threshold changed by the session:** `start` 0.94 gave blotchy, stepped skies; 0.80 is smooth
  (sheets of both in `out-features/F6/`).
- **Measured** on the blown areas of the panel photos and the holdout photo, mean value before
  and after: 231 to 254, 235 to 253, 234 to 254, and 230 to 255 on the holdout photo 0110.
- **Tone gate** with `--set highlight_hold=true`, 110 RAW files: 0 dark to darker, 0 pushed into
  clipping, 0 shadow clipping, no photo newly pulled back by the safety check.
- **What it costs.**
  - The soft shoulder below the blown area reaches down to 0.80, so on a bright frame the hold
    eases about a third of the pixels back toward their source brightness: bright clouds and
    mist keep more of it, not only the blown patch.
  - A batch run flags a photo for review when more than 5 % of it is white. With the hold on, 12
    of the 110 corpus photos pass that mark (their sources do too); with the default edit none
    does, because it turns the blown areas grey. The check lives in a protected file and was not
    changed.
- **Review** (Checker, Opus, high): no blocking finding. Fixed after it: the sidecar note counted
  every eased pixel as "blown" (36 % on a frame that is 4 % blown) and now gives both numbers;
  the hold is applied again after a reference match, which could grey the held whites.
- **Default output:** byte-identical to the baseline renders; both test suites green.
- **For the owner's vote:** the blind gallery `out-features/galleries/highlight-hold/`.

**`raw_highlight` (removed)**

LibRaw's highlight modes were wired in as `raw_highlight = clip | blend | rebuild` and measured
on the RAW files with the largest blown area (`out-features/F6/raw_highlight_modes.jpg` and
`.txt`):

- `rebuild` (mode 5) turns recovered skies purple. Removed.
- `blend` (mode 2) does bring back real cloud structure where the default decode shows flat
  white: the RAW data holds it. But LibRaw then scales the whole frame down. In the tone gate
  with `--set raw_highlight=blend` 109 of the 110 photos came out darker, by 23 levels of 255 at
  the median; the distance to the camera's own render grew from 1.33 to 2.11 EV; the count of
  photos with a dark foreground rose from 39 to 73. The gate's own counts stayed at 0, because
  it compares with the dark reference decode, which is why the comparison with the default
  output is given here.
- A recovery that is worth having needs the exposure put back after the decode, with a shoulder
  so that the recovered tones do not clip again. That is a RAW development feature of its own,
  not an option, so the option was taken out of the code again. It is listed as a proposal in
  [FEATURE_PLAN.md](../FEATURE_PLAN.md).
