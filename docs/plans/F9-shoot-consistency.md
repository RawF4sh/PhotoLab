# F9. Shoot consistency v2

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F9.

## Step one: the measurement (done, 2026-11-11)

A dry run of the Switzerland corpus (800 photos, three days) collected the decisions of every
photo. A *burst* is a run of consecutive frames at most 30 s apart whose small grey thumbnails
correlate at 0.9 or more: the same framing, shot again. The spread inside a burst (largest minus
smallest value):

| | Bursts | Source brightness, median / p90 | After the decided lift, median / p90 | Temp, median / p90 | Tint, median / p90 |
|---|---|---|---|---|---|
| JPEG | 38 (85 photos) | 0.36 / 1.00 EV | 0.24 / 0.72 EV | 1.0 / 4.5 | 0.5 / 1.9 |
| RAW | 16 (32 photos) | 0.38 / 0.80 EV | 0.18 / 0.50 EV | 0.7 / 5.2 | 0.5 / 3.1 |

What this says:

- Frames of the same framing differ in exposure at the source (bracketing, changing light). The
  rules remove about a third of that; **a quarter of a stop remains at the median and half a stop
  to three quarters in one burst out of ten**. Side by side, that is visible.
- White balance differs little at the median, and by 4 to 5 units in one burst out of ten.
- Today's grouping (`consistency.py`) cannot help here: it clusters the whole folder by brightness,
  so bracketed frames of one framing land in *different* groups, and it nudges by at most 0.15.

So the drift is real, and v2 is built.

## Goal

Frames of one burst come out at one brightness and one white balance, within safe bounds. The
mode is opt-in (`consistency = "bursts"`); the default stays today's brightness grouping, because
the mode changes pixels.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is the new mode the default? | No. `--consistency bursts` or `[run] consistency = "bursts"` | Set it in `defaults.toml` after the blind vote |
| What is a burst? | Consecutive frames by capture time, at most 30 s apart, thumbnail correlation 0.9 or more | `[consistency]` in `rules.toml` |
| How far may a frame be moved? | Exposure by at most 0.5 EV, temp and tint by at most 4 units | The same block |

## Design

Two passes, only for frames that are in a burst:

1. **Find bursts** (`consistency.find_bursts`). For every photo: capture time (EXIF
   `DateTimeOriginal`; a file without it never joins a burst) and a 24 by 16 grey thumbnail,
   normalised to zero mean and unit variance. Sort by time. A frame joins the burst of the frame
   before it when the gap is at most `max_gap_s` and the mean product of the two thumbnails (their
   correlation) is at least `min_similarity`. The normalisation makes the comparison blind to
   brightness, so bracketed frames match.
2. **Decide once, without pixels** (`Pipeline.process_one(path, do_apply=False)`) for every frame
   of a burst with two or more frames. From the result: `level = 2.2 * log2(fg_p50) + exposure +
   0.5 * shadows_ev` (the foreground median after the decided lift, in stops), `temp`, `tint`,
   `exposure`, `headroom_ev`. With a model backend the masks land in the disk cache, so the render
   pass does not segment again.
3. **Bias toward the burst's median**, bounded:
   - `exposure_bias = clamp(median(level) - level, -max_ev, +max_ev)`, then two safety bounds:
     never below `-max(exposure, 0)` (a lift may be cancelled, never turned into a cut, so a dark
     source cannot come out darker than it would alone), and never above
     `max(0, headroom_ev - 0.15 - exposure)` (no push into clipping).
   - `temp_bias = clamp(median(temp) - temp, -max_wb, +max_wb)`, the same for tint.
4. **Render** each frame with its bias. `process_one` gets `wb_bias=(temp, tint)` next to the
   existing `exposure_bias`; it adds them to the global settings where the exposure bias is added
   today and writes one note.

Frames outside a burst get no bias in this mode.

The sidecar records the burst id in `batch_group` (`b1`, `b2`, ...), the exposure bias in
`exposure_bias` (as F3 added) and the white-balance bias in a new field `wb_bias: [temp, tint]`,
so `autoedit adjust` reproduces the frame.

## Files

| File | Change |
|---|---|
| `src/autoedit/consistency.py` | `find_bursts`, `burst_biases`, `plan` (one entry point for both modes) |
| `src/autoedit/pipeline.py` | `wb_bias` parameter of `process_one`; `result.wb_bias` |
| `src/autoedit/contract.py` | `RunConfig.consistency`, `Result.wb_bias` |
| `src/autoedit/batch.py`, `src/autoedit/web/jobs.py` | call `consistency.plan` instead of their own copies of pass 1 |
| `src/autoedit/adjust.py` | pass the stored `wb_bias` |
| `src/autoedit/cli.py`, `src/autoedit/configs/defaults.toml`, `src/autoedit/configs/rules.toml` | `--consistency`, `[run] consistency`, the `[consistency]` block |
| `tests/test_consistency.py` | new tests (the file exists) |

## `consistency.py`

```python
@dataclass
class Bias:
    group: str = ""
    exposure: float = 0.0
    temp: float = 0.0
    tint: float = 0.0


BURST_DEFAULTS = {"max_gap_s": 30.0, "min_similarity": 0.9, "max_ev": 0.5, "max_wb": 4.0}


def capture_time(path: str) -> float | None:
    """EXIF DateTimeOriginal as seconds, or None."""
    try:
        with PILImage.open(path) as pil:
            stamp = pil.getexif().get_ifd(0x8769).get(0x9003)
        return datetime.strptime(str(stamp)[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def thumb_signature(path: str) -> np.ndarray | None:
    """A 24 by 16 grey thumbnail with zero mean and unit variance: what a frame looks like,
    whatever its exposure. Uses the embedded preview of a RAW when there is one (cheap)."""
    try:
        img = downscale_long_edge(load_image(path), 96)
        pil = PILImage.fromarray((np.clip(luma(img.rgb), 0, 1) * 255).astype(np.uint8))
        g = np.asarray(pil.resize((24, 16), PILImage.BILINEAR), dtype=np.float32)
    except Exception:
        return None
    return (g - g.mean()) / (g.std() + 1e-6)


def find_bursts(paths: list[str], max_gap_s: float, min_similarity: float) -> list[list[str]]:
    """Runs of consecutive frames (by capture time) that are close in time and look alike.
    Only runs of two or more are returned."""
    timed = sorted(((t, p) for p in paths if (t := capture_time(p)) is not None))
    sigs = {p: thumb_signature(p) for _t, p in timed}
    bursts, cur = [], []
    for t, p in timed:
        if cur and sigs[p] is not None and sigs[cur[-1][1]] is not None \
                and t - cur[-1][0] <= max_gap_s \
                and float((sigs[p] * sigs[cur[-1][1]]).mean()) >= min_similarity:
            cur.append((t, p))
        else:
            if len(cur) > 1:
                bursts.append([q for _t, q in cur])
            cur = [(t, p)]
    if len(cur) > 1:
        bursts.append([q for _t, q in cur])
    return bursts


def burst_biases(decided: list[dict], max_ev: float, max_wb: float) -> list[tuple[float, float, float]]:
    """Per frame of one burst: (exposure, temp, tint) bias toward the burst's median. `decided`
    holds, per frame: fg_p50, exposure, shadows_ev, temp, tint, headroom_ev."""
    level = [2.2 * math.log2(max(d["fg_p50"], 1e-3)) + d["exposure"] + 0.5 * d["shadows_ev"] for d in decided]
    mid, t_mid, n_mid = _median(level), _median([d["temp"] for d in decided]), _median([d["tint"] for d in decided])
    out = []
    for d, lv in zip(decided, level):
        ev = max(-max_ev, min(max_ev, mid - lv))
        ev = max(ev, -max(d["exposure"], 0.0))                                   # cancel a lift, never cut
        ev = min(ev, max(0.0, d["headroom_ev"] - 0.15 - d["exposure"]))          # never into clipping
        out.append((ev, max(-max_wb, min(max_wb, t_mid - d["temp"])), max(-max_wb, min(max_wb, n_mid - d["tint"]))))
    return out


def plan(images: list[str], cfg, pipeline, thr: dict | None = None) -> dict[str, Bias]:
    """The consistency bias of every image, for the mode in `cfg.consistency`.
    "brightness": today's behaviour (brightness clusters, a nudge of at most 0.15).
    "bursts": frames of one framing shot close together are brought to one level and one white
    balance. A photo that fails to measure simply gets no bias."""
```

`plan` in mode `brightness` reproduces exactly what `batch.run` does today (`quick_lum`,
`plan_groups`, `exposure_bias`) and returns `Bias(group=gid, exposure=bias)` per image. In mode
`bursts` it runs the design above; a frame whose dry decision raises is left out of its burst.
`thr` is the `[consistency]` block of the rules file merged over `BURST_DEFAULTS`.

Imports needed: `math`, `datetime`, `dataclass`, `PILImage`.

## `pipeline.py`

`process_one(..., wb_bias: tuple[float, float] = (0.0, 0.0))`. In the existing
`if exposure_bias:` block's neighbourhood:

```python
        if wb_bias[0] or wb_bias[1]:
            global_settings["temp"] = global_settings.get("temp", 0.0) + wb_bias[0]
            global_settings["tint"] = global_settings.get("tint", 0.0) + wb_bias[1]
            policy_notes.append(f"batch normalize: white balance {wb_bias[0]:+.1f} temp, "
                                f"{wb_bias[1]:+.1f} tint toward group {batch_group or '?'}")
```

and at the end `result.wb_bias = [float(wb_bias[0]), float(wb_bias[1])]`.

## `contract.py`, `cli.py`, config

- `RunConfig.consistency: Literal["brightness", "bursts"] = "brightness"`.
- `Result.wb_bias: list[float] = Field(default_factory=lambda: [0.0, 0.0])`.
- `--consistency brightness|bursts`; `[run] consistency`; `--no-consistent` still turns
  consistency off in both modes.
- `rules.toml`:

```toml
# Shoot consistency, mode "bursts" (F9, opt-in: `consistency = "bursts"`). A burst is a run of
# consecutive frames at most max_gap_s apart whose thumbnails correlate at min_similarity or more.
# Inside a burst each frame is moved toward the burst's median: exposure by at most max_ev stops,
# temp and tint by at most max_wb units.
[consistency]
max_gap_s = 30
min_similarity = 0.9
max_ev = 0.5
max_wb = 4
```

## Callers

`batch.run` (one worker and several) and `web/jobs._run` replace their pass-1 code by
`biases = consistency.plan(images, cfg, pipeline, thr)` when consistency is on, and read
`biases.get(path, Bias())`. `process_one` gets `exposure_bias`, `wb_bias` and `batch_group` from
it. With several workers the plan is made in the parent (it holds the pipeline with the model).
`adjust.rerender` passes `wb_bias=tuple(old.wb_bias)`.

## Task

One Builder (Sonnet, medium). Proving command: `PYTHONPATH=src python3 -m pytest tests/test_consistency.py tests/test_grouping.py tests/test_jobs.py tests/test_parallel.py tests/test_adjust.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

## Tests (add to `tests/test_consistency.py`)

| # | Test | Asserts |
|---|---|---|
| 1 | `test_brightness_mode_is_unchanged` | For a folder of five synthetic photos of mixed brightness, `plan(..., mode brightness)` gives the same groups and biases as the old three calls (`quick_lum`, `plan_groups`, `exposure_bias`), and `batch.run` writes the same image bytes as a copy of the old loop would (compare with a run made through the three old functions directly, or with stored expectations of group ids and biases) |
| 2 | `test_find_bursts` | Six synthetic JPEGs with EXIF capture times (Pillow `Exif`, tag 0x9003 in the Exif sub-directory): three of the same picture at different exposure 5 s apart, one different picture 5 s later, two of that picture 100 s later 2 s apart. The result is two bursts: the first three, and the last two. A file without a capture time is in no burst |
| 3 | `test_burst_biases_are_bounded` | For a burst with levels far apart, every exposure bias is within `max_ev`; a frame with `exposure = 0.1` gets no bias below -0.1; a frame with `headroom_ev = 0.2, exposure = 0.05` gets no positive bias; temp and tint biases are within `max_wb`; a burst whose frames agree gets zeros |
| 4 | `test_bursts_mode_brings_a_burst_together` | Three synthetic photos of the same picture at 0.6, 1.0 and 1.5 times the brightness, capture times 3 s apart, stub backend, `consistency="bursts"`: the spread of the mean output luma is smaller than with `--no-consistent`, each sidecar has `batch_group` starting with `b`, and the notes of a moved frame contain `batch normalize` |
| 5 | `test_wb_bias_is_applied_and_recorded` | `process_one(path, wb_bias=(3.0, -2.0))` has `temp` and `tint` shifted by those amounts against the run without it, a note, and `result.wb_bias == [3.0, -2.0]`; `adjust.rerender` of a batch output made in bursts mode is byte-identical |
| 6 | `test_cli_and_config` | `--consistency bursts` and `[run] consistency = "bursts"` reach `RunConfig`; an unknown value is rejected |

## Acceptance checks (the session runs these)

1. Both test suites green; default renders byte-identical to the baseline (the default mode is
   unchanged).
2. The measurement of step one again in bursts mode, on the Switzerland corpus and on the
   Dolomites corpus: spread of the level and of temp inside bursts, before and after.
3. A contact sheet of one burst, default against bursts mode.
4. Tone gate with `--set consistency=bursts` is not applicable (the report renders photos one by
   one); instead, the dry-run sidecars of the bursts run must show no frame whose net exposure
   went below what the rules decided when that was a cut.

## Changed after the first run on a real shoot

The feature was built to this spec and passed its tests on synthetic bursts. Run on the 800-photo
Switzerland shoot, and with one "burst" looked at as a contact sheet, four things were wrong. All
four are fixed in `consistency.py`; the text above describes the first design.

| What was wrong | What it did | The fix |
|---|---|---|
| A thumbnail was made for every photo, from a full decode | The plan kept one core busy for more than 25 minutes before the first photo rendered | Only a shot with a neighbour within `max_gap_s` gets a thumbnail; it comes from a reduced JPEG decode or from the RAW file's embedded preview. The search over 800 photos takes 27 s |
| A RAW file and the camera's JPEG of the same shot counted as two frames | 758 of the 800 photos were "burst photos", and each was decoded and decided a second time | The same capture second with one RAW and one other file is one shot; by itself it is no burst |
| Plain thumbnails were compared (`min_similarity = 0.9`) | Nearly every outdoor photo is bright sky over darker ground, so different pictures taken seconds apart correlated above 0.9: a hut and a meadow were one burst, and so were an upright and a level frame. 55 "bursts" were found | Each row's mean is removed from the thumbnail before the comparison, the orientation must be the same, and the threshold is 0.8. On the pairs of the sheet: the one true repeat of a framing is at 0.91, the three false ones at 0.16, 0.44 and 0.58. The shoot has 12 bursts with 34 files |
| One median for all files of a burst | A RAW decode starts about a stop and a half below the JPEG of the same shot, so the nudge was spent on that gap: JPEGs were pulled down and RAW files pushed up by the full bound | RAW files are evened out among themselves and the other files among themselves (`kind_biases`) |

The measurement in "Step one" above used the plain comparison, so most of its 38 and 16 bursts
were not bursts. Its numbers describe consecutive photos of a shoot, not repeats of one framing.

## Result

Built on 2026-11-11, in the unattended run. Status: done, off by default
(`--consistency bursts`). The default mode, `brightness`, is unchanged.

- **Built** by one Builder (Sonnet, medium) from this spec; reviewed by a Checker (Opus, high)
  with F8, F10 and F11 ("Review round 1 (F8 to F11)" in the
  [F11 spec](F11-user-config-layer.md): a limit on a burst's span, a plan that never calls the
  LLM, two tests that passed with their feature removed). Then changed by the session after the
  run on a real shoot, see the section above.
- **Measured** on the 800-photo Switzerland shoot (dry run, stub backend): 12 bursts with 34
  files. Inside a burst, among files of one kind (12 groups, 25 files), the spread of the decided
  level in EV, median and largest:

  | | Median | Largest |
  |---|---|---|
  | Source | 0.77 | 2.39 |
  | Default mode | 0.64 | 1.88 |
  | Bursts mode | 0.28 | 1.76 |

  The level narrowed in 11 of the 12 groups and widened in none (the twelfth had no spread). The
  spread of the white balance setting went from 0.4 (largest 3.4) to 0. The largest exposure
  nudge was 0.35 EV.
- **Rendered,** three real bursts of two frames each: the difference in mean brightness between
  the two frames went from 44 to 39, from 25 to 12 and from 23 to 15 levels of 255
  (`out-features/F9/three_real_bursts.jpg`). A nudge never cuts a frame below what the rules
  decided and never lifts one into clipping, so a burst with one bright unlifted frame closes
  only a little.
- **Cost:** the search for bursts took 27 s on the 800 photos; each burst photo is then decoded
  and decided once more before the first render (34 here). The whole dry run took 296 s with four
  workers; the same run in the default mode took 229 s. The plan runs in the parent process and
  prints its progress.
- **Known limits:** in the web app the plan holds the inference lock, so a preview waits and a
  cancel is seen only after the plan; the comparison recognises repeats of one framing, not "the
  same scene from another angle"; a file without a capture time is never in a burst.
- **Final check** (Checker, Opus, high), fixed in "Review round 2" in the [F11 spec](F11-user-config-layer.md): a HEIC photo got no capture
  time, because the plugin was not registered before the file was opened for its EXIF data.
  Confirmed correct: mixed orientations are rejected, three files in one second form a burst,
  files without a capture time are left out, the plan never calls the LLM, and the default mode
  is untouched.
- **Default output:** byte-identical to the baseline renders; both test suites green.
- **Open for the owner:** try it on an uncurated shoot, where repeats of a framing are common
  (this shoot is a selection, so it has few), and decide whether it becomes the default.
