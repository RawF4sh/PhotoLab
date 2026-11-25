# F61 · Style round two: unedited originals and four engine changes

**Status on 2026-11-24.** Built, and the first of three rounds run. The spec follows the first
round of [F59](F59-local-styles.md), which the loop of [F60](F60-web-photo-loop.md) ran and all
three candidates lost. The owner answered four of its six decisions on 2026-11-24 (O1, O3, O4,
O5) and asked for the engine changes and the loop tools to be built. T1 to T8 are
[built and checked](#built-and-checked-2026-11-24). On the owner's word the FiveK originals were
fetched the same day and the [Blue hour round](#the-blue-hour-round-2026-11-24) ran: five
iterations on 16 night originals. The owner kept its second iteration (O7), so two changes went
into the packaged candidate. The round is stopped for one more decision of the owner (O8), and
its held-back photos are still shut.

## Goal

One more round for Blue hour, Moody landscape and Sunset glow, this time on photos a style is
meant for: frames as they came out of the camera. A style is released when it passes the bars
below on held-back photos of its kind and the owner's blind vote.

## What the first round taught

Measured on 2026-11-24 with the packaged candidates. The runs on tuning photos used the
approximate (stub) regions at strength 0.8, which the frame's own search does not depend on. The
owner's three examples ran with SAM 3 regions. Brightness is the median on a scale of 0 to 1.

| Finding | Measured |
| --- | --- |
| **The round tuned and judged on finished photos.** Every tuning and held-back photo came from Wikimedia Commons, where photographers publish their finished pictures. On a finished picture the right edit is close to none, so the style that does least wins. | On its 16 tuning photos Blue hour's frame exposure differs from Standard's by a median of 0.00 EV (−0.49 to +0.12). Sunset glow: 0.00 EV (−0.51 to +0.21). Against Standard on the held-back photos, Sunset glow tied on 19 of 24. |
| **Tuning there moved the styles toward doing nothing.** Two of Moody's three kept changes made a move smaller. | Sky burn 1.0 to 0.25 EV, band at the far shore +1.0 to +0.2 EV. |
| **On the owner's own examples the candidates now barely differ from Standard**, except on the one truly dark frame. Distance from the photographer's result in stops, over 36 cells: | Bridge at blue hour: Standard 0.56, Blue hour 0.52. Dolomites lake: Standard 0.71, Moody 0.70. Lake at sunset: Standard 1.25, Sunset glow 0.86. |
| **A pull stops at the nearest edge of its range.** A range is the middle half of the finished photos, so a dark frame ends darker than three quarters of them, and that only at full strength. | On the bridge the frame goes from 0.382 to 0.388, toward a range of 0.392 to 0.49. The photographer's finished side is at 0.429, near the middle of that range. |
| **Moody's target does not describe the look the owner showed.** Its ranges hold the "before" of the Dolomites example, so the style finds nothing to do, and its sky range asks for a bright sky. | Shadows: range 0.07 to 0.14, before 0.107, the photographer's result 0.159. Colour: range 0.09 to 0.18, before 0.127, result 0.213. Sky: range 0.71 to 0.90, measured on Commons photos of mist and overcast; the result's top third is at 0.355. |
| **A range measured on Commons photos is the taste of many photographers.** | Their finished blue-hour frames sit at 0.259 to 0.366. The owner's bridge is finished at 0.429, above that whole range, so the measured target of round one would have darkened it. |
| **The frame's search has no limit for most controls.** | Moody changed vibrance by −103 on one tuning photo and by +85 on one RAW original, to bring colour into its range. |
| **Moody loses on colour and weight more than on defects.** | Of the checker's 21 reasons for preferring Standard, 13 name a blue or cool cast, 11 a dark, heavy or grey picture, 3 a visible defect. |
| **Blue hour loses where it greys the sky.** | Of 10 reasons for preferring Standard, 5 praise a deeper or cleaner sky behind the subject. One names a pale rim and spots above a snowy ridge, where the sky's own lift met its mask. |
| **Two defects of Moody come from where a move is placed.** | Dark islands: the burn works inside the sky's mask, which has a hole wherever a cloud was found as its own region. A bright stripe: the band sat on a "horizon" that was the lower edge of the sky, not a waterline. |
| **The burn op is not what adds the shadow-clipping marks.** | On the 16 Moody tuning photos, the corner burn of 0.6 EV adds a channel under the clipping level on at most 0.11 % of the pixels, and a burn of 0.25 EV from the top on at most 0.21 %. The budget is 0.5 %. A shadow pull of −1.0 EV alone adds a median of 0.61 %, and is over the budget on 9 of the 16. |
| **Standard itself is marked for review on low-key photos.** | On 6 of the 24 held-back blue-hour photos in the first round. With stub regions on the tuning photos: 3 of 16 blue hour, 4 of 16 moody, 5 of 16 sunset. Each style had fewer marks than Standard there. |

## What this spec decides

1. **Test photos are unedited originals.** RAW files, with a retoucher's or the owner's finished
   version beside them where one exists.
2. **A target is measured on finished photos that have the look the owner means.** The owner
   picks them (O4). Until then each style keeps the ranges drawn around the owner's examples.
3. **A round starts from the brief again.** The first candidate of each style carries the sizes of
   the owner's examples, not the sizes the first round shrank to.
4. **A style aims inside its range, not at its edge** (T1).
5. **Every control the search may move has a limit** (T2). A style whose target drives a control
   without one is refused when it is loaded.
6. **A move may work inside several regions at once** (T3), so a sky is burned as a whole, clouds
   included.
7. **A band is placed on a waterline only** (T4).
8. **The burn op keeps its math.** The measurement above clears it. Its guarantee stays the one in
   its docstring: luma. The alternative weight, read from a pixel's darkest channel, was measured
   too: no new clipping at all, the same darkening within 0.01 EV, and at most 3 % more fine
   detail energy. It is kept for the day a sheet shows a mark made by a burn.
9. **The release bar counts decided comparisons** (O3).

## The photos of round two

| Kind | Tuning and held-back photos | Finished photos for the target |
| --- | --- | --- |
| Blue hour | FiveK RAW originals labelled **night**, outdoors, each with its retoucher's version as the reference. The owner's own dusk and night RAW files, if any. | The owner's pick (O4) |
| Sunset glow | FiveK RAW originals labelled **dawn or dusk**, outdoors. The owner's own. | The owner's pick (O4) |
| Moody landscape | The owner's own RAW files of overcast, mist and shade (O2). The Dolomites set has about 20 dark, neutral and flat frames to choose from. The tool takes no FiveK frame for Moody, because the index cannot tell an overcast day from a sunny one. With fewer than 40 originals Moody waits. | The owner's pick (O4) |
| Control | 16 FiveK originals labelled **day**, 3 of them already here: Standard must stay as it is on them | none |

Rules:

- **Sizes:** 16 tuning and 24 held-back photos per kind, as in round one. A style with fewer than
  40 originals waits.
- **Split by shoot.** A shoot is one camera on one day, read from the file. No shoot is on both
  sides, and no shoot gives more than two photos to one panel.
- **A kind is assigned before any result exists**, from the label and a look at the original. A
  pair may be moved between Blue hour and Sunset glow, or left out, by a line of
  `out-webloop/round-2/kinds.txt` (`ID KIND`, or `ID none`), until the split is frozen. The ledger
  says why.
- **Work to do.** For each held-back photo the report says whether the style changed it at all.
  A photo the style rightly leaves alone is a tie, and ties no longer count against it (O3).
- **The first round's photos** are kept as a panel for defects: its 16 tuning and 24 held-back
  Commons photos per kind. On them a style must show no halo, blotch, banding or new clipping.
  How far a finished photo should change is not judged there, and nothing is tuned on them.
- **Licences are as in F60.** FiveK is for research and never feeds a target. Its use as tuning and
  held-back photos is decision O1. Nothing fetched is committed.
- **Budget:** 100 FiveK pairs in the plan: 44 for Blue hour, 40 for Sunset glow and 16 for the
  control. Blue hour takes four more than its split needs, because a shoot gives at most two
  photos to a side: the first 40 night pairs came to 39 usable ones. With the 9 pairs of round
  one, 101 pairs and 6.1 GB are here (fetched 2026-11-24), inside the approved 150 pairs and
  12 GB. No new Commons files are needed.

One photographer of round one sat in two groups under two spellings. Round two does not use the
group that crossed, so that decision is no longer needed.

## Engine changes before the round

None changes what a default run renders. Each ends with the stub comparison of the definition of
done.

### T1. A style aims inside its range

- `styles.py`: a new key `aim`, from 0 to 0.5, default 0. At 0 a measure outside its range is
  pulled to the nearest edge, as today. At 0.5 it is pulled to the middle.
- `edit/style_target.py::fit` takes `aim`. For a measure below its range the goal is
  `lo + aim × (hi − lo)`, above it `hi − aim × (hi − lo)`. A measure inside its range is left
  alone as before, and nothing else in the search changes: a step still may not cross its goal or
  the clipping budget.
- `pipeline.py` passes `style.aim` in the two calls to `fit`.
- Tests: with `aim = 0` the result of today's test is unchanged to the last digit. With
  `aim = 0.5` a dark frame's median ends between the edge and the middle at a strength below 1,
  and at the middle at strength 1 when the budget allows. A measure inside its range does not
  move.

### T2. A limit for every control the search moves

- `styles.py`: `limits` accepts every control of `edit/style_target.py::CONTROLS` (`highlights`,
  `contrast`, `saturation`, `vibrance`, `temp`, `tint`, `clarity`, the three HSL sliders), each
  from 0 to the size of its range in `ops.OP_BOUNDS`. The four keys of today stay.
- `edit/style_target.py`: a new `check_limits(target, limits)`. For every measure in every table
  of the target, the control it drives must have a limit, or it raises "STYLE: the target moves
  CONTROL (through MEASURE) and the style sets no limit for it". `Pipeline.build` calls it where
  it loads the target. `fit` needs no change: it already keeps frame and region together inside
  `limits[control]`.
- `configs/styles.toml`, the seeds: Sunset glow `temp = 8`; Moody landscape `vibrance = 30`.
- Tests: a style whose target moves an unlimited control is refused at build. On a colourful
  frame with a low colour range, the search stops at the limit. The packaged styles load.

### T3. A move inside several regions

- `compose.py`: `within` takes a name or a list of names. The regions that are present are joined
  first and feathered once, with the largest feather of the joined regions. Joining after
  feathering would leave a seam of half weight between a sky and its cloud. The move fires when
  at least one named region is present.
- `configs/styles.toml`: Moody's `sky_grad` gets `within = ["sky", "cloud"]`.
- Tests: with a sky and a cloud side by side, the weight is at least 0.99 across their border.
  With `within = "sky"` it is zero inside the cloud. A list of absent regions does not fire.

### T4. A band on a waterline only

- `geometry.py`: `horizon_position(masks, cfg, only=None)`. With `only="waterline"` it answers
  `None` unless the trusted line is a waterline.
- `compose.py`: a band may be placed `at = "waterline"`. `settings_for` and `plan` take a
  `waterline` beside `horizon`. `at = "horizon"` keeps its meaning.
- `pipeline.py`, the one place that finds the line: it also asks for the waterline when a move
  wants one. The order of the stages does not change. A Checker reviews this hunk.
- `configs/styles.toml`: Moody's `far_shore` gets `at = "waterline"`.
- Tests: a sky over a mountain with no water gives no band. Water under a far shore gives a band
  on the water's upper edge.

## Loop tools before the round

### T5. FiveK pairs by time of day

`scripts/web_corpus.py`: `discover_fivek` reads the time and subject of every row of the index,
which is one request for a description and no photo. Night becomes `blue_hour`, dawn or dusk
becomes `sunset`, day stays `control`. `plan` prints how many pairs each time of day has and takes
up to 44 pairs for Blue hour (four spare, see the budget), 40 for Sunset glow and 16 for the
control, in a fixed order that is not the page's. Pairs that are already here are kept. It says so when a kind has fewer than 40.

### T6. The split of round two

`scripts/web_corpus.py split --round 2` writes its split and its lists into
`out-webloop/round-2/` and leaves the files of round one alone. It groups by shoot (camera and
capture day from the file), takes at most two photos of a shoot into one panel, and accepts the
owner's originals from `curated-web/owner/originals/<kind>/`. A kind joins the split once it has
enough photos, and its assignment never changes after that. A kind with too few photos waits and
is named; it does not hold up the others. A line of `round-2/kinds.txt` gives a photo another
kind or leaves it out; a photo that is already in the frozen split is refused. Tests with made-up files: no shoot on both sides, the
cap of two, a kind that waits and joins later, and round one's files untouched.

### T7. The bar as a command

`scripts/web_round.py bar DIR` reads the held-back verdicts and the key and prints: decided
(better plus worse), the share better, the clear losses, the new review marks, how many photos
the style changed at all, the median distance from the reference for both sides when the sheets
were made with references, and the counts by shoot. It answers pass or fail by the first three
bars of this spec. The panel for defects and the owner's vote stay judgements. The round tool
also reads a round-two folder: its own list of pairs per kind, and a gallery of FiveK originals
or of the owner's own files.

### T8. A target from a pick

`scripts/style_target.py`: the list given with `--manifest` may be a part of the frozen target
group, with at least 12 photos. Today it must be the whole group. Every other check stays: the
role, the licence, the checksum, and no photographer of a tuning or held-back set. Test: a part
of 12 is taken, a part of 11 is refused, a photo from outside the group is refused.

## The round

As F60 describes it: one change per iteration, both arms rendered with SAM 3, a Checker judges
blind, the keep rule decides, three kept changes are promoted. Three things differ.

- The sheets show **Original | A | B | Reference** for every pair, and the distance to the
  reference is measured on pairs of the style's own kind.
- The held-back photos of a style are opened once, at the end of its round.
- The panel for defects is rendered at the end of each round.

Order: Blue hour, which came closest; then Moody landscape; then Sunset glow.

| Style | Round start | First hypotheses, one per iteration |
| --- | --- | --- |
| Blue hour | The packaged style. Its range, 0.392 to 0.49, was drawn around the owner's bridge. With a pick of 12 or more photos: the target measured on them. | 1. `aim = 0.5`: lift the frame to the middle of its range. 2. No target for sky and water: lift the frame, hold the lamps, and leave the sky its depth. 3. If lamps are missed on more than 2 of 10 photos, the compact-spot rule of F59 S3. 4. Colour 20 % stronger, as in the owner's bridge. |
| Moody landscape | The target from the owner's pick, without a range for the sky. Without a pick: ranges drawn around the finished side of the Dolomites example, shadows 0.13 to 0.19 and colour 0.17 to 0.25. The brief's sizes with T2 to T4: sky burn 1.0 EV inside sky and cloud, band +1.0 EV on a waterline, corner burn 0.6 EV, vibrance limit 30. | 1. The `cool_blue` palette off: it tints the whole frame, and a blue cast was the most frequent complaint. 2. If the blue is then missed: the palette at half strength. 3. The size of the sky burn, from what the first sheet on originals shows. |
| Sunset glow | The packaged style with T2 | 1. `aim = 0.5` for land and water: on the owner's lake their thirds end at 58 and 77 of 255 where the photographer's are 85 and 96. 2. Whatever the first sheet on dark originals shows. |

A change that needs code stops the round and comes back to the planner. Two are prepared, and
built only when a tuning sheet shows the need:

| Prepared | Built when |
| --- | --- |
| The style's whole plan is tried on the proxy, and its additions are scaled back together when they add more clipping than the budget or than Standard would | A candidate still gets a new review mark on a tuning photo after the config hypotheses |
| A warm region (sunlit grass, trees, rock) is held out of a cool pull, as lamps are held out of the lift | The blue cast survives with the palette off |

## Tasks

Tiers as in [AGENT_ROUTING.md](../AGENT_ROUTING.md).

| Task | Tier | Files | Proof |
| --- | --- | --- | --- |
| O1 to O6 | The owner | none | The answers, recorded in the ledger |
| T1 | Builder (Sonnet, medium) | `styles.py`, `edit/style_target.py`, the two `fit` calls in `pipeline.py`, `tests/test_local_styles.py` | The three tests of T1, then both suites |
| T2 | Builder (Sonnet, medium) | `styles.py`, `edit/style_target.py`, the place in `pipeline.py` that loads the target, `configs/styles.toml`, `tests/test_local_styles.py` | The three tests of T2 |
| T3 | Builder (Sonnet, medium) | `compose.py`, `configs/styles.toml`, `tests/test_local_styles.py` | The three tests of T3 |
| T4 | Builder (Sonnet, medium) | `geometry.py`, `compose.py`, the one place in `pipeline.py`, `configs/styles.toml`, tests | The two tests of T4 |
| T5 | Builder (Sonnet, medium) | `scripts/web_corpus.py`, `tests/test_web_corpus.py` | A made-up index gives the three kinds and the counts, with no network |
| T6 | Builder (Sonnet, medium) | `scripts/web_corpus.py`, `tests/test_web_corpus.py` | The three tests of T6. Then the new check replayed on the real manifest, read-only |
| T7 | Builder (Sonnet, medium) | `scripts/web_round.py`, `tests/test_web_round.py` | Made-up verdicts give pass and fail at the edges of the bar |
| T8 | Builder (Sonnet, medium) | `scripts/style_target.py`, `tests/test_style_target.py` | The three cases of T8. Then the real target lists replayed, read-only |
| Check | Checker (Opus, high) | read-only | The diff against this spec, the `pipeline.py` hunks, the definition of done, and the tone report by default and with each style |
| Round | The session and a Checker (Opus, high) | config only | The ledger, the held-back sheets, the bar command's answer, the galleries |
| Docs | Tweaker (Haiku, low) | `CHANGELOG.md`, `FEATURE_PLAN.md`, the two earlier plans | none |

T1 to T4 share `pipeline.py`, `tests/test_local_styles.py` and `configs/styles.toml`, so they run
one after the other. T5 and T6 share `scripts/web_corpus.py` in the same way. The engine group and
the tool group can run side by side.

## Acceptance

For the code, as in F59: both suites green, the stub run byte-identical to the commit before, the
tone report with 0 dark made darker and 0 pushed into clipping by default and with each style, and
the public-repository check.

For a style, on its held-back originals:

| Bar | Value |
| --- | --- |
| The Checker's blind verdict against Standard | Of the photos where it sees a difference: better on 70 % or more, with at least 10 such photos, and clearly worse on none |
| Review marks | None new against Standard |
| Distance from the reference on pairs of the kind, median | Smaller than Standard's. The first value of 0.35 EV is set again after the first sheet, because it was never measured on pairs of a style's own kind. |
| The panel for defects | No halo, blotch, banding or new clipping on any photo |
| The owner's blind vote | Decides. Only then `tested = true` |

## Decisions for the owner

| # | Decision | Proposed |
| --- | --- | --- |
| O1 | May FiveK RAW originals be tuning and held-back photos? Their licence says research, and not for commercial advantage. No number measured on them would enter a target, but the styles' settings would be tuned by looking at them. Asked with up to 110 more pairs, about 6.3 GB; the tool as built plans about 87, about 5 GB. | **Answered on 2026-11-24: yes.** While AutoEdit is not sold. Nothing measured on them enters a target. |
| O2 | Own RAW files per kind, put into `curated-web/owner/originals/<kind>/` by hand, with a finished version beside each where one exists | Moody from the Dolomites set. Blue hour and sunset if the owner has them. |
| O3 | The bar counts the photos where the checker sees a difference, not all of them | **Answered on 2026-11-24: yes.** A style that rightly leaves a photo alone should not lose by it. |
| O4 | Which finished photos have the look you mean, per style? At least 12 of the 30 Commons photos of its group, or own finished photos in `curated-web/owner/photos/<kind>/`. | **Answered on 2026-11-24: start from the three examples.** The owner may pick more photos later, and the target is then measured again. |
| O5 | The six blind galleries of round one | **Answered on 2026-11-24: skip the vote.** They show finished photos edited a second time, which is the wrong test. |
| O6 | Standard is marked for review on about a quarter of blue-hour photos | A separate item for the first improvement loop. Not part of this round. The Blue hour round gave it numbers and made it block a fix: see O7 and the round's findings. |
| O7 | Keep iteration 2 of the Blue hour round? It won 10 of the 10 photos that differ, and the keep rule rejects it for one new review mark that Standard gives the same photo. | **Answered on 2026-11-24: yes, keep it.** A review mark counts as new only when Standard does not give the photo one for the same kinds of reason. Iterations 2 and 5 are in the packaged candidate. |
| O8 | Which photos is Blue hour for? On night frames it does not beat Standard. On dusk frames it wins where buildings fill the frame at twilight and loses on sunsets. | Twilight frames with buildings and lamps, taken from both FiveK labels by a look at the original, before any result. Sunsets stay with Sunset glow. That needs more dusk pairs than are here, so a new word to fetch. |

## Built and checked (2026-11-24)

Four builders made the eight tasks from this spec. An independent checker then read the diff,
with the `pipeline.py` changes first, and its points were fixed. At that point no photo had been
fetched and no round had been run.

| Task | What exists |
| --- | --- |
| T1 | The style key `aim`, and `fit(..., aim=)` in `edit/style_target.py`. No packaged style sets it. |
| T2 | A limit for every searched control in a style's `limits`, and `style_target.check_limits`, called when the pipeline is built. Sunset glow has `temp = 8`, Moody landscape `vibrance = 30`. |
| T3 | `within` as a name or a list in a move. Moody's sky burn works inside sky and cloud. |
| T4 | `at = "waterline"` for a band, and `geometry.horizon_position(..., only="waterline")`. Moody's band at the far shore uses it. |
| T5 | `web_corpus.py plan` takes FiveK pairs by time of day, and only rows whose place the index gives as outdoors. A row with no place label at all is kept. |
| T6 | `web_corpus.py split --round 2`, writing into `out-webloop/round-2/`. A pair that round one already measured on becomes a tuning photo, never a held-back one. |
| T7 | `web_round.py bar DIR`, and the round tool on a round-two folder. |
| T8 | `style_target.py` takes a pick of at least 12 photos of a target group. |

**On the owner's three examples**, with SAM 3 regions. Distance from the photographer's result in
stops; lower is closer.

| Example | Standard | The candidate before this build | The candidate as packaged now | With the start values of round two |
| --- | ---: | ---: | ---: | ---: |
| Bridge at blue hour | 0.56 | 0.52 | 0.52 | 0.42 |
| Dolomites lake | 0.71 | 0.70 | 0.61 | 0.37 |
| Lake at sunset | 1.25 | 0.86 | 0.86 | 0.80 |

The start values: Blue hour and Sunset glow with `aim = 0.5`; Moody landscape with the brief's
sizes and ranges drawn around the example. None of the renders was marked for review. These are
the three photos the styles were drawn from. They show that the changes work, not that a style is
good: that is what the round is for.

**Replayed on the real files of round one**, read-only:

- `bar` on Moody's held-back comparison gives what the record says: 3 better, 21 worse, 3 clear
  losses, 2 new review marks.
- `score` on a copy of the first iteration gives the recorded answer.
- The three target lists pass whole and as a pick of 12; a pick of 11 is refused.
- The split reads the nine FiveK pairs that are here (1 night, 5 dawn or dusk, 3 day) and says
  each kind waits for more.

**Checks.** Both suites pass: 1,365 tests and 4 skipped on the system Python (no `tifffile`
there), 1,369 on `.venv`. The stub run and eight RAW files are byte-identical to the commit before
the styles were built. The tone report on the 110 RAW files shows 0 dark made darker and 0 pushed
into clipping, by default and with each of the three candidates; Moody declares 11 photos darker.

**What the first real `plan` has to show**, because it could not be seen without the network: how
many pairs each time of day has, and whether the index says where a photo was taken. The tool
prints both.

**Done on the owner's word (2026-11-24):** `plan`, `fetch`, `verify` and `split --round 2`. See
[the Blue hour round](#the-blue-hour-round-2026-11-24). Moody waits for the owner's own originals
in `curated-web/owner/originals/moody/` (O2).

**What the check found.** The `pipeline.py` changes are sound: the order of the stages is
unchanged, a default run takes the path it took, and `aim = 0` gives the old goals in 200,000
random cases. Fixed after it:

- `bar` could pass its distance part with nothing measured. Now the distance counts when at least
  5 photos came with a reference, as many must be comparable on both sides, and a name it does
  not know is refused. `bar` also wants real SAM regions, as `score` does.
- The five dawn-or-dusk pairs that every iteration of round one was measured on would have become
  held-back photos of Sunset glow. They are tuning photos now.
- A night photo taken indoors could have joined Blue hour. Rows the index marks as indoors are
  left out; a row without that label is kept. The real index labels every row: outdoors, indoors
  or unknown, and only outdoors is taken.
- A joined move took the feather of a named region that was not in the photo.
- Loading the style list alone had become ten times slower through a new import.

**Open after the check:**

- Moving a pair between Blue hour and Sunset glow had no command. It has one since the fetch
  (2026-11-24): a line of `round-2/kinds.txt`, see T6.
- A pair that changes its time label in the index keeps its old kind in the split.
- `bar` judges the first three bars. The panel for defects and the owner's vote stay judgements.
- The tone reports with each style ran on the build before the last small fixes. Those changed no
  picture: five Moody renders are byte-identical before and after.

## The Blue hour round (2026-11-24)

**Stopped for the owner after five iterations.** Two changes are kept and packaged (iterations 2
and 5, see [Kept and packaged](#kept-and-packaged)). Blue hour's held-back photos are shut. The
ledger with every row is `out-webloop/LEDGER.md`, the iterations are in `out-webloop/round-2/`.
Neither is committed.

### The photos

| | |
| --- | --- |
| Index | 2,416 rows are marked outdoors, 388 indoors, 154 unknown. Only outdoor rows are taken: 76 night, 181 dawn or dusk, 2,048 day. |
| Fetched | 92 pairs, no failure. With the 9 of round one: 101 pairs, 6.1 GB. `verify`: 675 entries, 0 errors. |
| Left out | One night pair of round one, a table of candles indoors, by a line of `kinds.txt`. |
| Split, frozen | Blue hour 16 tuning and 24 held-back photos; Sunset glow 16 and 24; control 16. No shoot on both sides, at most two photos of a shoot on a side. The 8 pairs round one measured on are tuning photos. |
| Kind | A pair's kind is its time of day in the index. No pair was moved by eye. |

Every tuning photo is a pair, so each sheet also gives the distance from the retoucher's version.

### The iterations

Better / same / worse is the candidate against the best so far, judged blind by an independent
checker on the 16 tuning photos. Distance is the median over 15 pairs, in stops.

| # | One change | Blind | Distance | Kept |
| --- | --- | --- | --- | --- |
| 1 | `aim = 0.5` | 0 / 15 / 1 | 0.749 to 0.831 | no |
| 2 | No brightness range for sky and water | 10 / 6 / 0, three clear | 0.749 to 0.590 | yes, by the owner's decision (O7) |
| 3 | The sky is held out of the frame's lift, as lamps are | 2 / 14 / 0 | 0.590 to 0.621 | no: the distance grew |
| 4 | The lamp rule's pulls fade with the clipped share of a lamp | 1 / 14 / 1 | 0.590 and 0.590 | no |
| 5 | The lamp rule lets go only of a lit area that is mostly clipped (35 % to 55 %) | 1 / 15 / 0 | 0.590 and 0.590 | yes, on top of 2 |

In every iteration the 16 Standard controls were byte-identical, the tone report was clean, and
9 of 16 proposals were safe as rendered on both sides. Iterations 3 to 5 ran on top of iteration 2
while it was held for the owner.

### What it showed

1. **On night originals the range does not steer the frame.** The lift is stopped by the clipping
   budget around lamps and lit fronts: +0.1 to +0.7 EV on 10 photos, nothing on 5, and the median
   stays at 0.05 to 0.33 where the range begins at 0.392. So `aim` changed one photo of 16.
2. **The sky's range was the packaged style's worst habit.** Drawn around a bright dusk, it lifted
   a night sky by up to 1.3 EV into grey, brown and grain, and pulled a blown sky down by 2 EV
   into flat grey with a pale rim. Without it the candidate won every photo that differs.
3. **The keep rule and the picture disagreed once.** Iteration 2 leaves a black sky black, and
   that photo is then marked for review: more than 12 % of the output under the shadow level,
   whatever the original looked like. Standard gives the same photo the same mark with the same
   number. The round's start escaped it only by greying that sky, which the checker called a
   clear loss on the same photo. On the 16 photos Standard has 2 review marks and the candidate
   the same 2. The rule was not bent: the iteration was held until the owner decided (O7).
4. **Against Standard, the style's lift is what loses on night frames.** Two blind comparisons on
   the tuning photos: 1 better, 8 same, 7 worse; with the sky held 2, 9 and 5. Standard already
   lifts a night frame about as far as it bears. The bar is out of reach there.
5. **The brief fits twilight, not night.** The owner's bridge was a frame too dark for its hour.
   The same candidate against Standard on the 16 dusk tuning photos: 4 better, 9 same, 3 worse.
   It wins where buildings fill the frame at twilight and loses on sunsets and silhouettes, in
   both of two runs. Holding the sky makes that worse (4, 8 and 4, two clear): the lift loses its
   brake and greys the silhouettes.
6. **The lamp rule greyed a blown lit sign.** It pulls highlights down by 12 to 32 and harder the
   more a lamp is clipped. Iteration 5 lets go of an area that is mostly clipped.
7. **Lamps are found.** `lights` is a region on all 15 photos with lamps. The compact-spot rule
   is not needed.
8. **"Colour 20 % stronger" was a reading error of this spec.** On the bridge the photographer's
   colourfulness rises by 1.28 in absolute terms, 1.05 relative to brightness and 0.96 as
   saturation: it comes from the lift. Iteration 2 reaches 1.24 on that photo. No iteration ran.
9. **What the retoucher does on night frames and neither side does:** blacks stay black (10th
   percentile 0.016 against Standard's 0.054), more contrast, and sodium light turned neutral.
   That is a different look from the owner's brief, and FiveK may not feed a target.
10. **The checker is steady on defects and not on taste.** Two photos whose renders were
    byte-identical in both comparisons with Standard flipped between better and worse. Every
    vote that named a defect came out the same.

### Kept and packaged

The owner kept iteration 2 on 2026-11-24, and iteration 5 stands on it. Both are in
`src/autoedit/configs` since then:

- `targets.toml`: Blue hour has no brightness range for sky or water. The frame's range and the
  hold on lamps stay.
- `rules.toml`: the lamp rule (`[opt_in.lights]`) lets go of a lit area that is mostly clipped.
  Its two pulls are whole up to 35 % clipped and gone at 55 %. Only a run that searches for lamps
  loads this rule, so Standard does not.

The keep rule got the owner's reading: a review mark counts as new only when Standard does not
give the same photo one. `web_round.py score` reads that from a comparison with Standard in the
iteration's `vs-standard/` folder; without one, every mark counts as before. Replayed on the five
real iterations it keeps 2 and 5 and rejects 1, 3 and 4.

An independent check of that change found it too wide in two ways, and both were closed. A mark
is excused only when every kind of reason the candidate gives, Standard gives too: a new kind of
problem on a photo that Standard already marks still counts. And the Standard arm must be the
default style, with no local style, rendered from one of the iteration's own two config folders.
The three source-relative flags (dark made darker, pushed into clipping at either end) are never
excused. The same check found the `kinds.txt` line, the removed ranges and the lamp rule sound.

**Checks of the promotion.**

- Both suites pass: 1,370 tests and 4 skipped on the system Python, 1,374 on `.venv`.
- Default output is unchanged. The stub run and eight RAW files are byte-identical to the commit
  before the styles, and the default tone report on the 110 RAW files equals the build's on every
  number.
- The tone report with Blue hour on the 110 RAW files: 0 dark made darker, 0 pushed into
  clipping. 91 edits are accepted as proposed, 18 are blended toward the original and 1 is left
  as the original; before the promotion 93, 16 and 1. Those are daylight mountain frames, not the
  style's kind: without a sky range the style's result changes on 67 of them, and the safety
  blend caught three more and one fewer. On the round's own 16 tuning photos 9 proposals were
  safe as rendered before and after.
- With SAM 3 regions the packaged candidate renders the 16 tuning photos byte-identically to
  iteration 5.
- The app. F60 asks for a walk through a visible browser after a promotion. The Browser pane was
  hidden, so five round photos, all RAW, went through the app's own routes with stub regions:
  preview with the style, a region changed, the change undone, export. All five exports equal
  `autoedit run --no-consistent` byte for byte, with no error. A plain folder run differs by
  design, because it evens brightness across a shoot. The walk with screenshots is still owed.

### What is open

- O8 above, and O6 with the numbers of point 9.
- The held-back photos of Blue hour are not opened until a candidate has a chance at the bar.
- The walk through the app in a visible browser after the promotion (see the checks above).
- The panel for defects was not rendered, for the same reason.
- Sunset glow's round has not started. Its 16 tuning photos were looked at with Blue hour for
  point 5; its held-back photos are shut.

## Left out, and what would bring each back

| Left out | Bring it back when |
| --- | --- |
| Measures relative to the frame (a sky this much darker than its subject) in place of absolute ranges per region | Blue hour still flattens a sky after its sky target is gone |
| A step of the search that moves another listed measure away from its range is refused | A sheet shows it. Seen once in a made-up case: more saturation moved warmth further off. |
| The burn's weight from the darkest channel | A review mark is traced to a burn |
| A made-up "before", a finished photo darkened and flattened, as a test photo | Never as the proof: a style tuned on it learns to undo our own darkening |
| The wording of the optional LLM decider for ops in stops, and a profile's style table that refuses every run | Their own small changes, noted in F59 |
