# F62. Auto: edit each photo toward a goal for its scene

Status: Auto runs on provisional goals. See the validation and release results below. The words follow
[GLOSSARY.md](../../GLOSSARY.md); the two decisions behind it are
[ADR 0001](../adr/0001-landscapes-may-be-made-darker.md) and
[ADR 0002](../adr/0002-goals-from-measured-before-and-after-pairs.md).

## Why

Three rounds of named styles (F59, F61) produced edits the owner could not tell from Standard: in
the blind vote of 2026-11-25 all 24 Blue hour pairs looked the same. The causes are known. The
amounts are timid (vibrance +3, clarity +2), the regions are hardly edited, the goal was the FiveK
retouchers' corrections, and the darkness and clipping checks pull back every bold edit. The
owner's own examples of a good edit move a landscape much further: a darker, warmer sky, deep
shadows, a glow behind the peaks.

## Goal

1. **Auto**, a built-in style that recognises a photo's **scene** and moves it toward that scene's
   **goal**. Bold for landscapes, subtle for portraits (portraits come in a later round).
2. Goals are **measurements** taken from before-and-after pairs by good editors, not slider
   values. Each photo is moved by as much as it needs.
3. **Simple** (today's Standard, renamed) stays beside Auto and stays the default until Auto wins
   the owner's blind vote, one scene at a time.

The demo: a folder of landscapes from one trip (sunset, twilight, a clear day, a misty morning).
Auto names each photo's scene, the edits differ clearly from Simple, the photos of one shoot look
alike, and the owner prefers Auto on at least 7 of 10 photos in a blind vote.

## Decisions (owner, 2026-11-25, from the design interview)

| Decision | Answer |
|---|---|
| Scenes | people, landscape, other. Landscape is first, with four finer scenes: sunset, twilight, daylight, overcast or mist |
| What a goal is | Measurements (brightness, contrast, sky and shadow colour, saturation, how far the edit moved the photo) |
| Where goals come from | Before-and-after pairs from photo-editing forums, screened by an AI model; only the measurements are kept (ADR 0002) |
| How bold | Landscapes aim at the bolder third of good edits; portraits at the middle |
| Darker landscapes | Allowed under Auto; the darkness and clipping checks stay for Simple and portraits; fault checks stay for all (ADR 0001) |
| Offline | Auto runs on the computer. Typed tuning uses the existing word list, with the optional AI as today |
| Strength | One slider, 0 to 150 %, 100 % is the goal |
| One shoot | Photos of the same shoot get the same scene and look alike |
| When unsure | Fall back to the broader scene at a lower strength |
| Retire | Blue hour, Sunset glow and Moody landscape |
| Release | Per scene: a visible-difference minimum first, then the owner's blind vote (7 of 10 or better), and no faults |
| Saved styles | A **Style** learned from the user's own edits, and **Recipes**, come after Auto (see "Left out") |

## What the prototype showed

The prototype (branch `prototype/auto-goal-fit`, script `scripts/prototype_auto_goal_fit.py`) gave
the engine each of the owner's three examples and let it find the best settings with today's
controls. Reproduced share of the editor's change:

| Example | Whole-photo controls | + sky and land apart | + shaped light |
|---|---|---|---|
| Bridge, twilight | 69 % | 70 % | 71 % |
| Dolomites, mist | 40 % | 50 % | 56 % |
| Tree, sunset | 36 % | 37 % | 38 % |

The examples are small compressed web images, so even a perfect copy would not reach 100 %; the
bridge and the tree look close by eye. What follows for this spec:

- **The controls are mostly enough; the amounts are not.** The fits used vibrance +20 to +45,
  whites −60 to −80, curve ±40 to 60. Today's styles use about +3.
- **Warm highlight grading ran into its limit** (+100) on all three photos. The grading range is
  too narrow for these looks.
- **Sky and land apart matter most on mist** (the Dolomites sky went 1.7 stops darker on its own).
- **A strong sky edit on a soft mask leaves a light halo** along the ridge. Auto needs the guided
  sky edge and an automatic halo check.
- **Out of reach today:** painted mist, and cloud texture kept inside a darkened sky.
- **Each fit knew the answer.** Auto must choose amounts from the scene alone; that is what the
  goals are for.

## Design

Auto is built from parts that exist. F59 and F61 already fit the edit controls to measured ranges
(`edit/style_target.py::fit`, ranges in `configs/targets.toml`), shape light with moves
(`compose.py`: linear, radial, band and a glow around the detected light), and switch off the
darkness check for a style that asks for it (`quality.py`, `preserve_exposure`). What is new is
choosing the ranges per photo by scene, ranges measured from good edits, and limits wide enough to
use them.

### M1. Scenes

- `scene.py::classify` keeps its three answers and adds a finer landscape scene: `sunset`,
  `twilight`, `daylight` or `mist`, with a confidence. The inputs are measures the pipeline already
  has: sky coverage, sky brightness and warmth, global brightness and contrast, the detected light
  (`light.py`), `night.py::is_night`, and the atmospheric haze measure (`veil` in `contract.py`).
- The thresholds are data, in a new `[scenes]` table in `configs/auto.toml`. No number in Python.
- Below the confidence threshold the photo uses the broader scene (`landscape`) at a lower
  strength (`fallback_strength`, first value 0.6). An `other` photo under Auto gets Simple.
- The scene and the reason are written to the sidecar `notes`, shown on the photo, and can be
  corrected by the user. A new `--scene` option and the app's scene menu pass the existing
  `override`.
- **One shoot, one scene.** `consistency.py::plan_groups` already groups a shoot. Within a group,
  every photo gets the most common scene, unless the group is split by time of day (a sunset
  sequence that turns into twilight keeps both).

Check: the Checker labels 120 landscape originals by eye (FiveK originals already on disk and the
in-repo Dolomites corpus), without seeing the classifier's answer. The classifier must agree on
85 % or more of them, and must never call a daylight photo twilight or the reverse.

### M2. Goals from before-and-after pairs

Owner steps first (see "Owner steps"): which forums, how many downloads, and the screening model.

1. **Collect.** Pairs posted as before-and-after in editing forums, by scene, with the F60 web
   loop's budgets and fetch log. Files stay in the private `curated-web/` folder and are never
   committed. Side-by-side images are split and aligned (the prototype's edge-shift alignment).
2. **Screen.** An AI model sees each pair and answers in a fixed form: the scene, whether the
   before is an unedited photo, and whether the after is a weak, good or overdone edit. Only
   "good" pairs of the right scene are kept. The answer is stored beside the measurements, not the
   images.
3. **Measure.** `scripts/style_target.py` grows a pairs mode: for each kept pair it measures
   the `reference_features` of the before and the after, per region (global, sky, land, water,
   mountain), and the move between them.
4. **Pick the bolder third.** For landscapes, the goal's ranges come from the third of the pairs
   that moved the photo most. Each range is the middle 80 % of that third. The move's 90th
   percentile becomes the scene's limit for each control (how far a good editor goes).
5. **Write.** One table per scene in `configs/targets.toml` (`auto_sunset`, `auto_twilight`,
   `auto_daylight`, `auto_mist`, and `auto_landscape` from all landscape pairs), with
   `photos = N` in `meta`. Only these numbers enter the repository.

Minimum: 45 kept pairs per finer scene, so the bolder third holds 15. A scene with fewer stays on
the broader goal until it has them.

Until M2 is done, M3 runs on provisional goals (`photos = 0`) drawn from the three examples and
the F61 twilight originals, so M3 can be built and checked in parallel.

### M3. Auto at run time

- A style `auto` in `configs/styles.toml` with `targets` per scene instead of one `target`. The
  pipeline picks the table for the photo's scene and calls `fit` as it does for any style.
- **Limits per scene**, from M2, in `configs/auto.toml`: much larger than any style's today, still
  inside `OP_BOUNDS`.
- **Wider colour grading.** The grading ops' bounds go from ±100 to ±200. Values up to ±100 must
  render exactly as before: a math test proves it, and the identity test still holds. This touches
  `edit/ops.py`, so it gets its own math test and the tone report.
- **Light moves for landscapes**, using the shapes that exist: the sky darkened from the top
  (`linear`, `within = ["sky", "cloud"]`), a frame vignette (`radial`), and a warm glow around the
  detected light (`glow`) on sunset scenes. Their amounts come from the goal: a move's amount
  is scaled by how far the photo is from the goal, never more than its limit.
- **Guided sky edge.** Sky and cloud edits use the guided feather that `within` masks already
  have, so a strong sky edit follows the ridge.
- **Checks under Auto.** Landscapes: the darkness check is off (`preserve_exposure`, as for
  styles that ask for darker) and the clipping budget is the scene's own, from `auto.toml`. Every
  scene keeps the fault checks: banding, a blown light source, a wrong skin colour, and a new
  **halo** check: a light band along the sky edge that the edit made brighter than both sides,
  measured on the proxy. A photo that fails a fault check is pulled back to the strength that
  passes, and the reason goes to `notes`.
- **Strength 0 to 150 %.** The existing `--intensity` and the app's strength slider. 100 % moves the
  photo to its goal. Above 100 % the move goes further along the same direction, still inside the
  limits and checks.
- **One shoot.** After the fit, each photo's settings are pulled toward its group's median by
  `[auto] shoot_pull` (first value 0.5), so neighbouring frames look alike.
- **Typed tuning** keeps working as today: the word list (`intent.py`) nudges the result, and the
  optional AI is unchanged.
- **Names.** Standard becomes **Simple** wherever the user reads it: the app, `APP_GUIDE.md`,
  README, help text. The internal value `standard` stays, so saved projects still open.
- **Retire** Blue hour, Sunset glow and Moody landscape: their blocks leave `styles.toml` and
  `targets.toml`, and the interface stops offering them. The machinery they used stays (Auto uses
  it).
- **Released or not.** Each scene has `released = false` in `auto.toml` until it passes M4. Under
  Auto, a photo of an unreleased scene gets Simple with a note saying so; `--auto-unreleased` (for
  the release rounds only) runs the goal anyway.

Default pixels do not change: nothing chooses Auto unless the user does, and Simple is untouched.

### M4. Release, one scene at a time

On held-back originals of the scene, split by shoot, never used for M1 to M3:

| Bar | Value |
|---|---|
| Visible difference from Simple, before any vote | Median OKLab distance 5 or more (×100), and 3 or more on 80 % of the photos. First values; the owner may change them after the first sheet |
| Faults, by the Checker's panel and the automatic checks | No halo, banding, blotch, blown light source or wrong skin colour on any photo |
| Review marks | None new against Simple |
| The owner's blind vote, 10 or more photos | Auto preferred on 7 of 10 or better. Decides. Only then `released = true` |

A scene that misses the visible-difference bar does not go to the vote: its limits or goal are
revisited first. The owner decides, after the four landscape scenes, whether Auto becomes what the
app gives when nothing is chosen.

## Validation and release results

The full regression suite passed all 1,424 tests. The publication check passed for the repository and the files prepared for this change.

### Scene recognition

The 120 distinct visually labelled originals yielded 97 correct decisions (80.8%) with no
daylight/twilight swaps. Daylight scored 58/64, twilight 18/23, sunset 11/13 and mist/overcast
10/20. The target is 102/120. Bright overcast was the main source of errors. Tickets 02 and
03 remain open.

### Development comparison

The 12-photo sheet used six mountain RAW originals and six FiveK originals for research
evaluation. Each photo was rendered from the same original using Simple and provisional Auto.
The final classifier named 10 of the 12 correctly. The comparison used three originals per
scene and was excluded from the held-out rounds.

| Check | Result |
|---|---|
| Median visible difference from Simple | 3.88 against the target of 5 |
| Photos at distance ≥3 | 7 of 12 (58%) against the target of 80% |
| Sky boundary | All 12 passed. The strongest introduced rim scored 0.0569 against a limit of 0.12 |
| Review | No newly introduced pixel faults. One twilight frame returned to Simple at zero strength and gained a review mark for its existing highlight texture loss |
| Compatibility with the previous revision | All 110 full-resolution Canon RAW exports and both smoke-test exports matched byte for byte. The separate export from each of four saved RAW edit records also matched |
| Default RAW tone | 110 of 110 completed, zero dark photos made darker and zero new highlight or shadow clipping failures |

The sunset retained a pink band above the mountains, and woodland shadows opened. In the
overcast park, the sky became duller. Two twilight edits were near Simple. The next goal
calibration should examine those cases before a held-out round.

### Scene release rounds

| Scene | Checked reference pairs | Held-out photos evaluated | Median distance | Photos at distance ≥3 | Fault panel | Owner votes | Released |
|---|---:|---:|---:|---:|---|---:|---|
| Twilight | 0 | 0 | — | — | Pending | 0 | No |
| Sunset | 0 | 0 | — | — | Pending | 0 | No |
| Daylight | 0 | 0 | — | — | Pending | 0 | No |
| Mist or overcast | 0 | 0 | — | — | Pending | 0 | No |

Free on-device screening kept 30 of the 96 licensed Play Raw pairs (192 files, 3.03 GB). It kept 20 of 51 daylight, 2 of 17 twilight, 2 of 18 mist or overcast and 6 of 10 sunset examples. Daylight has a blind 40-pair sample ready for an independent reviewer. No scene has the 45 kept originals needed for a measured goal. Ticket 12 records the collection and calibration results. All four scenes use
Simple in the public app. The development previews use the explicit unreleased option. Dates,
source hashes and shoot groups for development photos are recorded privately so the photos and
their shoots can be excluded from eventual release rounds.

## Tasks

Tiers as in [AGENT_ROUTING.md](../AGENT_ROUTING.md). M1 and M3 can be built while the owner's M2
steps are pending. The tickets are one file each in [F62-tickets/](F62-tickets/), 01 to 24, each
with its `Blocked by:` line; ticket 01 is the thin end-to-end path the others plug into. Tickets 21
to 24 came after the first development sheet and deal with Auto's fault checks: 21 and 22 end
pull-backs for faults Auto does not add or need not make, 23 names a fault no check saw, and 24
(on hold) lets the fit stop before a fault.

| Task | Tier | What | Proof |
|---|---|---|---|
| M1-T1 | Builder (Sonnet, medium) | Finer landscape scenes, confidence, fallback, `auto.toml [scenes]`, `--scene`, sidecar note, one scene per shoot | a new `tests/test_scene.py` with synthetic stats per scene; run_all |
| M1-T2 | Checker (Opus, high) | Label 120 originals blind; measure agreement | 85 % or more, no daylight/twilight swaps |
| M2-T1 | Builder (Sonnet, medium) | Pairs mode in `scripts/style_target.py`: split, align, measure, bolder third, limits | `tests/test_style_target.py`: synthetic pairs with a known move |
| M2-T2 | Builder (Sonnet, medium), after the owner's yes | Collect and screen pairs per scene inside the approved budget | the fetch log and the kept counts |
| M2-T3 | Checker (Opus, high) | Look at 20 kept and 20 refused pairs per scene; confirm the screen | findings |
| M3-T1 | Builder (Sonnet, medium) | The `auto` style, per-scene targets and limits, released flags, retire the three styles | `tests/test_styles.py`, `tests/test_local_styles.py`; run_all |
| M3-T2 | Builder (Sonnet, medium) | Grading bounds ±200 with the math test; halo check with its test | math and golden tests; tone report |
| M3-T3 | Builder (Sonnet, medium) | Light moves scaled by the goal, guided sky edge, shoot pull, strength to 150 % | tests; a sheet of 12 originals |
| M3-T4 | Builder (Sonnet, medium) | Simple in the interface and docs; scene shown and correctable on the photo | browser check |
| M3-T5 | Checker (Opus, high) | Review: identity, Simple unchanged byte for byte, halo check catches the prototype's halo | findings as `file:line` |
| M4 | Session, then the owner | Per scene: the sheet, the visible-difference bar, the panel, the blind gallery | the bar table filled in below |
| Docs | Tweaker (Haiku, low) | CHANGELOG, this row in FEATURE_PLAN, `APP_GUIDE.md` | none |

## Acceptance

For every task:

- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, and its images are byte-identical
  to the previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes.
- The tone report on the in-repo RAW corpus reports 0 dark→darker and 0 pushed into clipping for
  the default run. Auto's own runs are exempt from those two counts on landscapes (ADR 0001) and
  are judged by M4 instead.

## Owner steps

| Item | Proposed |
|---|---|
| O1 Forums to collect pairs from | PIXLS.US Play Raw, approved 2026-11-25 |
| O2 Download budget | Up to 300 pairs in all for the four landscape scenes |
| O3 Screening model | Free on-device vision screening of verified public pairs, approved 2026-11-25. See ticket 12 for results |
| O4 The visible-difference bar | 5 median, 3 on 80 %, revisited after the first sheet |
| O5 Blind votes | One gallery per landscape scene, 10 or more photos each |
| O6 Auto as the default | After the four landscape scenes, the owner decides |

## Running the private pair and release rounds

The source and budget are recorded in ticket 11. Keep the approval, candidate list, downloaded photographs,
screening decisions and checker sheets under `curated-web/auto-pairs/`. Each candidate needs a
before and an after (or a split image), its scene and documented source. The fetcher enforces
the approved source list and budgets. For Play Raw, review the photographer’s grant for each file.
The category asks for freely licensed originals and the forum defaults to a noncommercial licence.
A public reply alone does not establish matching reuse rights for its edit.
[Play Raw guidance](https://discuss.pixls.us/t/about-the-play-raw-category/11956) · [Forum terms](https://discuss.pixls.us/tos)

Run from the repository root. The private selection records each source post, chosen
attachments, expected scene and licence wording. The owner selected the installed local model for this round. It records the model and its weights with each judgment, and keeps both thumbnails on this computer.

```sh
.venv/bin/python -I scripts/playraw_pairs.py plan --selection curated-web/auto-pairs/selection.json
.venv/bin/python -I scripts/auto_pairs.py fetch --approval curated-web/auto-pairs/approval.json --plan curated-web/auto-pairs/plan.json
.venv/bin/python -I scripts/auto_pairs.py screen-local
.venv/bin/python -I scripts/auto_pairs.py audit --scene daylight
```

The independent reviewer opens `curated-web/auto-pairs/audit-local/daylight/review.html`, completes all 40 comparisons and saves the exported `checker.json` in the same folder. The first command below scores the review. Run the second once the scene has passed its independent check and has at least 45 kept originals.

```sh
.venv/bin/python -I scripts/auto_pairs.py audit --scene daylight --decisions curated-web/auto-pairs/audit-local/daylight/checker.json
.venv/bin/python -I scripts/style_target.py --pairs curated-web/auto-pairs --scene daylight --out src/autoedit/configs/targets.toml --limits-out src/autoedit/configs/auto.toml --styles-out src/autoedit/configs/styles.toml
```

Repeat for eligible scenes. Use `--scene landscape` to pool checked landscape pairs. Preserve
the separate development and held-out shoots in the comparison manifest. After rendering both
arms, `scripts/auto_release.py measure` reports mean per-pixel OKLab distance ×100 for each
photo and the median across the scene. The `gallery` command checks measured-goal provenance,
automatic results and the checker's per-render verdicts before publishing a blind gallery.
Pass the gallery manifest and the owner's downloaded results to `vote`. A tie counts in the
denominator. The tool reports the result. The scene release flag is a separate recorded decision.

## Left out, and what brings each back

| Left out | Bring it back when |
|---|---|
| Portraits (subtle Auto, the middle third of good edits) | Landscape Auto is released for one scene; then its own round with portrait pairs |
| A saved **Style** learned from the user's own edits | Auto is released for one scene; it reuses the goal format |
| **Recipes** (fixed copies of one photo's settings) | They exist as F56 recipes; renaming only, when Style lands |
| Painted mist | A scene's release fails on mist photos for want of it; it needs a new op and its own spec |
| Cloud texture kept in a darkened sky | The panel names flat skies as a fault; a sky-only detail move is the first try |
| Finer scenes under people and other | After portraits |
