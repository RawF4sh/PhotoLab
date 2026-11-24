# F61 · Style round two: unedited originals and four engine changes

**Status on 2026-11-24.** Spec. Nothing is built. It follows the first round of
[F59](F59-local-styles.md), which the loop of [F60](F60-web-photo-loop.md) ran and all three
candidates lost. Six decisions at the end are the owner's, and the first of them comes before
anything is fetched or built.

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
| Moody landscape | The owner's own RAW files of overcast, mist and shade. The Dolomites set has about 20 dark, neutral and flat frames to choose from. The rest: FiveK originals labelled **day**, outdoors, nature, chosen as overcast from the small previews of its index if it has them, otherwise after the fetch. | The owner's pick (O4) |
| Control | 16 FiveK originals labelled **day**, 8 of them already here: Standard must stay as it is on them | none |

Rules:

- **Sizes:** 16 tuning and 24 held-back photos per kind, as in round one. A style with fewer than
  40 originals waits.
- **Split by shoot.** A shoot is one camera on one day, read from the file. No shoot is on both
  sides, and no shoot gives more than two photos to one panel.
- **A kind is assigned before any result exists**, from the label and a look at the original. A
  pair may be moved between Blue hour and Sunset glow by a line in the ledger, until the split is
  frozen.
- **Work to do.** For each held-back photo the report says whether the style changed it at all.
  A photo the style rightly leaves alone is a tie, and ties no longer count against it (O3).
- **The first round's photos** are kept as a panel for defects: its 16 tuning and 24 held-back
  Commons photos per kind. On them a style must show no halo, blotch, banding or new clipping.
  How far a finished photo should change is not judged there, and nothing is tuned on them.
- **Licences are as in F60.** FiveK is for research and never feeds a target. Its use as tuning and
  held-back photos is decision O1. Nothing fetched is committed.
- **Budget:** up to 110 more FiveK pairs, about 6.3 GB, inside the approved 150 pairs and 12 GB:
  40 each for Blue hour and Sunset glow, up to 20 for Moody landscape, 8 for the control. No new
  Commons files are needed.

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

`scripts/web_corpus.py`: `discover_fivek` reads the time, place and subject of every row of the
index, which is one request for a description and no photo. Night becomes `blue_hour`, dawn or
dusk becomes `sunset`, day stays `control`. `plan` prints how many pairs each label has. `select`
takes up to 40 pairs per kind. The session stops and reports if a kind has fewer than 40.

### T6. The split of round two

`scripts/web_corpus.py split --round 2` writes its split and its lists into
`out-webloop/round-2/` and leaves the files of round one alone. It groups by shoot (camera and
capture day from the file), takes at most two photos of a shoot into one panel, and accepts the
owner's originals from `curated-web/owner/originals/<kind>/`. Tests with made-up files: no shoot
on both sides, the cap of two, and round one's files untouched.

### T7. The bar as a command

`scripts/web_round.py bar DIR` reads the held-back verdicts and the key and prints, per style:
decided (better plus worse), the share better, the clear losses, the new review marks, how many
photos the style changed at all, and the same counts by shoot. It answers pass or fail by the
bar of this spec. The session no longer works the bar out by hand.

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
| O1 | May FiveK RAW originals be tuning and held-back photos? Their licence says research, and not for commercial advantage. No number measured on them would enter a target, but the styles' settings would be tuned by looking at them. Up to 110 more pairs, about 6.3 GB. | Yes, while AutoEdit is not sold. If no: each style waits for 40 of the owner's own RAW files of its kind. |
| O2 | Own RAW files per kind, put into `curated-web/owner/originals/<kind>/` by hand, with a finished version beside each where one exists | Moody from the Dolomites set. Blue hour and sunset if the owner has them. |
| O3 | The bar counts the photos where the checker sees a difference, not all of them | Yes. A style that rightly leaves a photo alone should not lose by it. |
| O4 | Which finished photos have the look you mean, per style? At least 12 of the 30 Commons photos of its group, or own finished photos in `curated-web/owner/photos/<kind>/`. | The owner picks, Moody first. Until then a style starts from ranges around its one example. |
| O5 | The six blind galleries of round one | Skip the vote. They show finished photos edited a second time, which is the wrong test. |
| O6 | Standard is marked for review on about a quarter of blue-hour photos | A separate item for the first improvement loop. Not part of this round. |

## Left out, and what would bring each back

| Left out | Bring it back when |
| --- | --- |
| Measures relative to the frame (a sky this much darker than its subject) in place of absolute ranges per region | Blue hour still flattens a sky after its sky target is gone |
| A step of the search that moves another listed measure away from its range is refused | A sheet shows it. Seen once in a made-up case: more saturation moved warmth further off. |
| The burn's weight from the darkest channel | A review mark is traced to a burn |
| A made-up "before", a finished photo darkened and flattened, as a test photo | Never as the proof: a style tuned on it learns to undo our own darkening |
| The wording of the optional LLM decider for ops in stops, and a profile's style table that refuses every run | Their own small changes, noted in F59 |
