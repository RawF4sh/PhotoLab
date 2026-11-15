# F12. Taste calibration

Status: spec (2026-11-11). Size L. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F12.
Builds on [F11](F11-user-config-layer.md): the fitted look is written into the user's profile.

## Goal

The improvement loop tuned the default look by hand from blind comparisons. `autoedit calibrate`
runs the same idea for any user on their own photos: it shows blind pairs that differ along one
dimension of the look, reads the votes, and fits a personal look.

```
autoedit calibrate ./my-photos --out ./calib          # round 1: pick photos, render pairs, write a page
#   open ./calib/round-1/index.html, vote, save results.csv into ./calib/round-1/
autoedit calibrate --continue ./calib                 # read the votes; next round, or the finished look
```

## What an unattended run can and cannot show

- It can show that the method recovers a known preference: a simulated voter with a hidden target
  look, in a test.
- It cannot show that a real person's calibrated look wins a fresh blind test against `natural`.
  That needs the owner's votes and is listed as open.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| The dimensions and their step sizes | Four: lift, depth, colour, warmth, as data in `configs/calibrate.toml` | Edit the file |
| Photos and rounds | 8 photos, 3 rounds, 4 dimensions: 32 pairs per round | `--count`, `--rounds`, or the file |
| Where the look is saved | The profile's `looks.toml`, as `calibrated` (or `--name`) | `--name`, `--profile` |
| Is the calibrated look applied automatically? | No. The command prints how to use it (`--look calibrated`) | A profile `defaults.toml` with `look = "calibrated"` |

## Method

**Dimensions.** A dimension is a direction in the space of look values, with a step (the numbers
below are the ones in the file after the session enlarged them, see the Result):

```toml
# configs/calibrate.toml
[run]
count = 8            # photos per round
rounds = 3
shrink = 0.7         # each round's step is this fraction of the round before
min_votes = 4        # a dimension with fewer decisive votes does not move
dead_zone = 0.2      # |share for plus - share for minus| below this: no move
render_long_edge = 1600

[dimension.lift]     # how much of the exposure lift the look keeps
label = "brighter or darker"
exposure_mul = 0.4

[dimension.depth]    # contrast and the filmic curve
label = "more or less depth"
contrast_mul = 0.35
curve_add = 12

[dimension.colour]
label = "more or less colour"
vibrance_add = 16
saturation_add = 8

[dimension.warmth]   # split-tone warmth in highlights and shadows
label = "warmer or cooler"
grade_highlights_temp = 16
grade_shadows_temp = 8
```

**A pair.** For a photo and a dimension: the base look minus the step, and the base look plus the
step. Both are rendered through the real pipeline (same masks, same everything else) at
`render_long_edge`. On the page the two sit side by side in a random order; nothing on the page
says which is which.

**The update.** After a round, for each dimension: `plus` and `minus` are the counts of decisive
votes (ties are left out), `n = plus + minus`, `d = (plus - minus) / n`. When `n >= min_votes` and
`|d| >= dead_zone`, the base moves by `d * step` along the dimension; otherwise it stays. Then the
step shrinks by `shrink`. Every look value stays inside the bounds in `[bounds]` of the same file
(`exposure_mul` 0.4 to 1.4, `contrast_mul` 0.5 to 1.8, `curve_add` -20 to 30, `vibrance_add` -15
to 25, `saturation_add` -15 to 20, the two grade temps -20 to 20).

A full preference for "plus" in every round moves a value by `step * (1 + 0.7 + 0.49) = 2.19`
steps; mixed votes converge on the point where the voter no longer has a preference.

**Photo choice.** The existing diverse selection (`curate.select_diverse` on its proxy features)
picks `count` photos from the folder, with a fixed seed, so the same folder gives the same sample.
Each round uses the same photos.

**The look.** After the last round (or `--finish` after any round) the base look is written as a
table into the profile's `looks.toml` with F11's writer, and the command prints the values, the
votes per dimension, and `use it with: autoedit run <in> <out> --look <name>`.

## Files

| File | Change |
|---|---|
| `src/autoedit/calibrate.py` | new: sampling, pairs, page, state, update, finish |
| `src/autoedit/configs/calibrate.toml` | new |
| `src/autoedit/cli.py` | the `calibrate` command |
| `tests/test_calibrate.py` | new |

## State and files of a calibration folder

```
calib/
  state.json            base look, steps, round number, photo list (file names), settings, seed
  round-1/
    index.html          the voting page (self-contained: no network, no external script)
    pairs.json          per pair: id, photo, dimension, which slot shows "plus"   (not shown on the page)
    p01_a.jpg, p01_b.jpg, ...
    results.csv         written by the page's Download button: gallery_id,pair,choice   (choice: 1, 2 or tie)
  round-2/ ...
```

- `state.json` holds no absolute path: the photo folder is given again on `--continue` only when
  the folder moved; otherwise it is stored relative to the calibration folder.
- `results.csv` must carry the round's `gallery_id` (a hash of the pairs); a file from another
  round is rejected with a clear message. A pair voted twice, or an unknown pair id, is an error.

## Core functions (reference)

```python
def update(base: dict[str, float], steps: dict[str, dict[str, float]],
           votes: dict[str, tuple[int, int]], cfg: dict, bounds: dict) -> tuple[dict, dict]:
    """One round's update. votes[dimension] = (plus, minus). Returns (new base, new steps)."""
    new = dict(base)
    for dim, step in steps.items():
        plus, minus = votes.get(dim, (0, 0))
        n = plus + minus
        d = (plus - minus) / n if n else 0.0
        if n >= cfg["min_votes"] and abs(d) >= cfg["dead_zone"]:
            for field, size in step.items():
                lo, hi = bounds.get(field, (-1e9, 1e9))
                new[field] = min(hi, max(lo, new.get(field, _DEFAULT[field]) + d * size))
    shrunk = {dim: {f: s * cfg["shrink"] for f, s in step.items()} for dim, step in steps.items()}
    return new, shrunk


def variant(base: dict[str, float], step: dict[str, float], sign: int, bounds: dict) -> Look:
    """The look at base + sign * step, inside the bounds."""
```

`_DEFAULT` are the `Look` dataclass defaults. Rendering a variant: build one `Pipeline` for the
run's settings, wrap its segmenter in `CachingSegmenter`, and for each variant set
`pipe.looks[<the run's look name>] = variant(...)` before `process_one(path, image=proxy)`.

## The page

One HTML file per round, written by `calibrate.py` from a template string in the module:

- the pairs one under another, each with its two images side by side, three choices (left,
  right, no difference) and the dimension's question in plain words ("Which do you prefer?");
  the label of the dimension is NOT shown, so the voter does not know what differs;
- a counter of answered pairs and a "Download results.csv" button that is enabled when all pairs
  are answered;
- no network request, no external font or script; works from `file://`;
- a `<title>` and plain styling that follows the user's light or dark setting.

## Task

One Builder (Sonnet, high: a new subsystem). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_calibrate.py tests/test_profile.py tests/test_cli.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

## Tests: `tests/test_calibrate.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_update_math` | With step `exposure_mul = 0.25`: votes (8, 0) move the base by +0.25; (6, 2) by +0.125; (5, 3) by +0.0625; (4, 4) and (1, 0) (below `min_votes`) by 0; a result outside the bounds is clamped; every step shrinks by 0.6 |
| 2 | `test_simulated_voter_recovers_a_preference` | A voter with a hidden target look (base `natural`; target `exposure_mul` +0.3, `vibrance_add` +8, the depth fields unchanged, warmth -4 on the highlights) who always prefers the variant whose value is nearer the target along the pair's dimension, with 10 % random flips from a fixed seed, 8 photos, 4 rounds, no rendering (the test drives `update` with the voter's choices): the fitted values end within 40 % of each step size of the target, and the depth dimension stays within one shrunk step of the base |
| 3 | `test_start_writes_a_round` | `calibrate.start(folder, out, count=3, backend="stub")` on six small synthetic photos: `state.json` and `round-1/` exist; `pairs.json` has 3 photos times 4 dimensions; each pair has two image files that differ; the page contains every pair id and no dimension label, look field name or the words "plus" and "minus"; `state.json` contains no absolute path |
| 4 | `test_sample_is_deterministic` | Two starts on the same folder with the same seed choose the same photos |
| 5 | `test_continue_reads_votes_and_advances` | With a `results.csv` written by the test (the choice that shows "plus" for every lift pair, ties elsewhere): `calibrate.advance(out)` raises the base `exposure_mul`, leaves the other dimensions, writes `round-2/`, and shrinks the steps in `state.json` |
| 6 | `test_results_are_validated` | A `results.csv` with another `gallery_id`, an unknown pair, a pair twice, or a choice other than 1, 2 or tie raises `ValueError` with a message that names the problem; a missing file says where to save it |
| 7 | `test_finish_writes_the_look_into_the_profile` | After the last round `advance` writes `[calibrated]` into `<profile>/looks.toml` (a `tmp_path` profile), keeps another look that was already in that file, and a pipeline built with that profile and `look="calibrated"` uses the fitted values |
| 8 | `test_cli_calibrate` | `cli.main(["calibrate", folder, "--out", out, "--count", "3", "--backend", "stub"])` returns 0; `cli.main(["calibrate", "--continue", out])` without `results.csv` returns 2 with the hint; with it, 0 |

## Acceptance checks (the session runs these)

1. Both test suites green.
2. A real first round on twelve corpus photos with SAM 3: the page opens, the pairs differ
   visibly along their dimension at page size (checked by the session on the rendered files).
3. Listed as open for the owner: voting through the rounds, and a fresh blind test of the result
   against `natural`.

## Result

Built on 2026-11-11, in the unattended run. Status: done as a tool; whether a calibrated look wins
a blind test is open (it needs the owner's votes).

- **Built** by one Builder (Sonnet, high) from this spec: `autoedit calibrate`, `calibrate.py`,
  `configs/calibrate.toml`, the self-contained voting page, the state file.
- **Deviations accepted:** pair images are named `q01_a.jpg` and `q01_b.jpg` with shuffled ids, so
  a file name says nothing about the dimension; the photo choice is deterministic by itself and
  the seed only shuffles pair order and sides; `looks save` and `calibrate` share one writer
  (`cli.write_look`); photos under the calibration folder are skipped when the photo folder is
  scanned; a `results.csv` that covers only some pairs is accepted.
- **Changed by the session: the step sizes.** With the steps of this spec (for example
  `vibrance_add = 6`) the two versions of a pair differed by about one 8-bit level on average and
  could not be told apart at page size. A vote on such a pair is noise. The first-round steps are
  now `exposure_mul 0.4`, `contrast_mul 0.35` with `curve_add 12`, `vibrance_add 16` with
  `saturation_add 8`, and the two grade temperatures `16` and `8`; `shrink` is 0.7. Measured on
  four test photos, the mean difference inside a pair is now 5 to 26 levels for brightness, 5 for
  depth, 1.2 to 3.5 for colour and 1.3 to 2.0 for warmth. Colour and warmth stay subtle on scenes
  with little colour: the look's own bounds are narrow. The tests read the packaged numbers
  instead of pinning them.
- **Checked by the session** with the stub backend on six photos: the round folder, 16 pairs for
  4 photos, no absolute path in `state.json`, no network reference and no dimension name on the
  page.
- **A real round with SAM 3** on twelve holdout photos (acceptance check 2): 8 photos chosen, 32
  pairs, none identical. Mean difference inside a pair, in 8-bit levels: brightness 1.2 to 17.5
  (median 15.8), depth 4.5 to 6.1, colour 1.6 to 3.5, warmth 0.8 to 1.8. The page was opened in a
  browser from a local server: all images load, the counter reaches "32 of 32 answered", the
  download button enables, and the file it produces (`gallery_id,pair,choice` with 32 rows) is
  accepted by `--continue`, which moved the look as the votes said and rendered round 2.
- **Known limit:** a pair can render identically (the brightness dimension on a photo the rules
  do not lift). The voter then picks "no difference", which counts as a tie.
- **Final check** (Checker, Opus, high), fixed in "Review round 2" in the [F11 spec](F11-user-config-layer.md): `--continue` without
  `--profile` could continue with another profile and write the look there (the state file now
  carries a fingerprint of the profile folder and refuses a mismatch); a hand-edited state file
  could name photos outside the photo folder; a packaged look name is refused as `--name`.
- **Open for the owner:** vote through the rounds on your own photos, then run a fresh blind test
  of the calibrated look against `natural`. That is the measure of whether the method works.
