# F59 · Local styles that make a photographer's moves

Status (2026-11-24). The local engine and evaluation tools are implemented. Standard is released. All three landscape candidates completed their first held-back evaluation and fell below the release thresholds. Their blind galleries are ready for the owner. Natural portrait awaits finished portraits. This work connects to [F58](F58-editor-sidebar-and-cr2.md),
which offers these styles behind **Run AI Edit**, and [F60](F60-web-photo-loop.md), the loop that
tunes and tests them.

## Goal

**Run AI Edit** with a fitting style makes an edit of the size and shape a photographer makes, on
this computer, inside the safety checks. Five styles: **Standard**, **Blue hour**, **Sunset
glow**, **Moody landscape** and **Natural portrait**.

A style is done when the loop's held-back photos and the owner's blind vote say so, not when its
code is merged.

## Implementation and evaluation

Standard uses the existing natural style. The normal editor and settings lists expose released styles. The evaluation catalog at `GET /api/v1/local-styles?candidates=true` also lists the three landscape candidates. An explicit CLI style selects one for local testing.

```sh
PYTHONPATH=src .venv/bin/python -m autoedit.cli run ./photos ./out --backend sam3 --style blue_hour
PYTHONPATH=src .venv/bin/python scripts/style_target.py --manifest curated-web/manifest.csv --list out-webloop/target-blue_hour.txt --style blue_hour --out .tmp/f59/target-blue.toml
```

The second command verifies every selected file against its recorded checksum, role and source licence. It measures finished pixels through the pipeline's resolved masks and writes quartiles for the photographic features used by the style. Each photo contributes one observation per detected region. Counts accompany the ranges. The same tool accepts `--owner-files` in place of the manifest for a photographer's own finished images.

The owner approved richer colour for Sunset glow and Moody landscape, and deeper shadows for Moody landscape when explicitly selected. Clipping, skin and snow protection, banding and edge checks still apply. The exposure search on a JPEG or embedded eight-bit preview is capped at two stops across the frame and the selected region together. Sunset glow enables denoising unless the caller supplies a different setting. Its three-stop limit applies only to RAWs with denoising enabled.

The first Blue hour target run measured 30 eligible finished photographs with the cached local SAM model. The middle half of frame medians was 0.259–0.366 in encoded luma. Sky medians were 0.317–0.567 across 28 frames and water medians were 0.228–0.346 across 12. Lamp masks met the measurement area floor on 22. On a separate tuning set, ten originals contained identifiable lamps or illuminated windows. Before viewing the outputs, a reviewer marked 25 light sources on those originals. The actual lamp mask intersected 22 of the 25 points and at least one point in every eligible frame. The threshold for adding the compact-spot fallback was not reached. The Blue hour round evaluated these measurements using the copied configuration in `out-webloop/iter-01/configs`. The seed range was brighter at 0.392–0.490.

The two later fits also used 30 finished photographs apiece and the local SAM model. Sunset glow's whole-frame median range was 0.339–0.458. It identified 29 skies, 18 water regions, 28 mountains and 24 tree regions. The corresponding median ranges were 0.561–0.766, 0.414–0.530, 0.213–0.284 and 0.112–0.167. Sky warmth ranged from −0.097 to +0.041 in the reference descriptor. Moody landscape's whole-frame p10 was 0.078–0.217, with chroma 0.086–0.151. Its 27 detected skies had medians of 0.711–0.903. Those bright skies motivated the test of a lighter sky burn. The frozen measurement tables and per-photo details remain in each private evaluation round.

Default and explicit Standard produced identical JPEG bytes to a clean HEAD archive on both smoke fixtures. The held-back comparisons reported below fell short of the release thresholds. The owner’s blind vote remains open. The S6 experiment tested two-threshold rules on whole-image brightness, shadow brightness, warmth and chroma. Only Blue hour qualified on its 30-photo development set (24 fits and two false positives on 30 ordinary controls). On separate author-disjoint panels it identified 8 of 16 Blue hour photos, one of 16 ordinary controls, three of 16 Sunset and five of 16 Moody. Recall fell below the 8-in-10 bar. Sunset and Moody produced no qualifying development rule. The editor therefore offers no automatic suggestion.

Before tuning, all three packaged seed configurations were checked separately on 110 local RAWs per style. The stub segmenter and the rendered 1024-pixel tone report made these repeatable CPU checks. All 330 assessments completed with zero introduced highlight clipping, shadow clipping, unexpected darkness or hard pixel failures. A source blend could be used by the normal quality workflow. One photo per style fell back to identity.

| Seed style | Accepted as proposed | Accepted after safety blending (including identity) | Declared darker |
| --- | ---: | ---: | ---: |
| Blue hour | 93/110 | 17/110 | 0 |
| Sunset glow | 100/110 | 10/110 | 0 |
| Moody landscape | 91/110 | 19/110 | 12 |

The engine and packaged seed inputs for this run have SHA-256 fingerprint `d1cacb44bf032fedccf6f1874663517c1a2c62e45576635496117c4a4ee8b0e3`. The isolated web rounds run separate native comparisons on their measured configurations.

After the three retained Moody adjustments entered the packaged preview preset, the 110-RAW check ran again. Moody accepted 100 edits as proposed, blended nine toward the original and used the original for one. Eleven edits were recorded as intentionally darker. Standard accepted 98 as proposed, blended 11 and used the original for one. Both completed with zero unexpected darkness, new highlight or shadow clipping, or hard failures. The same code passed all 1,305 tests and reproduced both pre-change Standard smoke exports byte-for-byte. The independent visual review below used different photos and actual SAM masks.

Blue hour's first tuning comparison improved the median distance on the nine research pairs from 0.623 to 0.537 EV. It gained five visual preferences, tied eight and lost three. On the independent 24-photo held-back comparison, the retained Blue edit won five, tied nine and lost ten against Standard. The first ten counted toward the release bar yielded two wins, three ties and five losses. A high-key mountain showed conspicuous mask artifacts. This round failed the release criteria. On those same 24 images, Standard flagged six edits for review and Blue hour flagged four. It introduced no warning category on an image that lacked that warning under Standard. All three original tone flags stayed clear. The owner gallery keeps the full result available for inspection. The held-back photos stay outside the tuning set.

Sunset glow’s measured target won three tuning votes, tied five and lost eight. It introduced a darkness and review flag on one image. Two narrower proposals each tied all 16 comparisons. The round retained its starting edit, with a median expert distance of 0.623 EV on nine pairs. Against Standard on 24 held-back photos, Sunset glow won two, tied 19 and lost three. All first ten tied. Standard flagged three edits for review and Sunset glow flagged two. It introduced no new warning type on any image and none of the three original tone flags. This candidate missed both release thresholds.

Moody landscape retained three changes. The first won six tuning preferences, tied nine and lost one. The second won ten and tied six after reducing the sky burn. The third removed a bright band along the sea horizon. Its three wins came from variants of one capture, and the other 13 tied. Median expert distance moved from 0.792 to 0.607 EV, above the 0.35 EV release threshold. On 24 held-back photos from 13 photographers, a reviewer preferred the retained edit over the round start on 21 and tied three. A second reviewer comparing against Standard gave Moody landscape three wins and 21 losses, including three clear defects. Two smooth skies showed mottled patches and a hazy mountain had an unnaturally dark sky and a pale rim. The preregistered first ten yielded two wins and eight losses. On the full set, Standard flagged one image for review and Moody flagged two different images for increased RGB shadow clipping. One of the two new flags was in the first ten. Neither arm tripped an original tone flag, including the darkness check. The new RGB warnings remain subject to the absolute protection even though the style declares darker shadows.

## The evidence

The owner supplied three before-and-after examples on 2026-11-24 as "what good looks like". Each
was split into its two sides and measured. They are screenshots of about 600 pixels and 8 bits,
and the first shows two halves of one frame, so its numbers are rough. They are briefs, not the
test set.

**What the photographers did**

| Example | Brightness | Colour |
| --- | --- | --- |
| Mountain lake at sunset, a dark frame opened up | Whole +1.7 EV. Sky +1.0 EV. Land and water +3.1 to +3.4 EV. | From a blue cast to neutral and warm |
| Dolomites lake, flat and violet to moody and blue | Whole −0.3 EV. Sky −1.1 EV, −1.6 to −2.2 EV along the top edge. A band at the far shore +1.1 to +1.4 EV. Lower corners −0.7 to −0.8 EV. The centre went from darker than the edge (0.87) to much brighter (1.55). Blacks raised from 10 to 19 of 255. | 75 % stronger. Hue turned from violet-blue (279°) to blue (259°) |
| Bridge at blue hour | Whole +1.2 EV, even across the frame. The cells with the lamps only +0.3 to +0.7 EV. | 20 % stronger, hue unchanged |

Inside one frame the second edit moves brightness by 3.6 stops from place to place. A whole-photo
slider gives one number.

**How close today's engine gets.** The figure is the average distance from the photographer's
result in stops, measured in 36 cells across the frame. Lower is closer. SAM 3 found the regions.

| Example | Untouched | Today's rules | Style `dramatic` | Mood words | Matched to the finished picture |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lake at sunset | 2.81 | 1.26 | 1.41 | 1.30 | 0.56 |
| Dolomites lake | 0.70 | 0.71 | 0.73 | 0.77 | 0.52 |
| Bridge at blue hour | 1.17 | 0.56 | 0.62 | 0.58 | 0.18 |

What the runs showed:

- **The moves are half the size.** On the bridge the rules lift +0.54 EV where the photographer
  lifted +1.2 EV. On the lake the land reaches 48 of 255 where the photographer reached 84.
- **Mood words barely move the picture.** "Moody, blue, cinematic" made the Dolomites frame
  slightly worse. Today's rules brighten its sky, and the photographer darkened it.
- **Matching to a finished picture gets closest every time.** That path
  (`edit/reference.py`) already searches for settings that reach a target.
- **Matching the bridge clipped the lamps**: highlight clipping rose by 7.5 % of the frame, and
  the photo was marked for review. Highlight hold and highlight detail did not change that.
- **Stronger settings alone do not pass the checks.** Two runs on scratch copies of the config:
  - The colour limit raised from +30 % to +90 % brought the Dolomites colour to 5.8 of the
    photographer's 6.3 (4.7 before). The photo was then marked for review: "saturation rose".
  - The sky gradient at −1.1 EV and the vignette at −0.45 EV, with the `cool_blue` palette,
    brought the picture little closer (0.61) and added 8.4 % shadow clipping. The gradient crosses
    the whole frame, so it darkened the dark mountains under the sky. Nothing made the bright band.

So four things are missing: the **size** of the move, its **shape** inside the frame, **colour**
that may go further when a style means it, and **lights** that hold while the frame is lifted.

## What exists and is reused

| Need | Already in the engine |
| --- | --- |
| A style as data | `configs/styles.toml`: a look, a list of things to find, a brief |
| Search for settings that reach a target | `edit/reference.py`: whole photo, per region, top and bottom gradients |
| Shaped light | `compose.py`, `configs/compose.toml`: radial, linear, glow |
| A straight waterline or horizon | `geometry.horizon` |
| Colour by tonal zone | `configs/palettes.toml`, in OKLab |
| A look that means to be dark | `preserve_exposure` in `looks.toml`: skips only the relative darkness check |
| Noise after a strong lift | `denoise`, switched on by run settings as genre packs do |
| Rule sets that join only on request | `[opt_in.NAME.…]` in `rules.toml` |
| Sheets, blind votes, the tone gate | `scripts/contact_sheet.py`, `blindtest.py`, `tone_report.py` |

## Design

### S1. A style carries a target

`styles.toml` gains optional keys per style. A style without them behaves as today, and Standard
is that style.

```toml
[blue_hour]
label    = "Blue hour"
look     = "natural"
concepts = ["sky", "water", "building", "bridge", "lights"]
brief    = "…"            # only Ask Gemini reads this
target   = "blue_hour"    # a table in configs/targets.toml; absent means no pull
pull     = 0.8            # 0 to 1: how far toward the target at strength 1
limits   = { exposure = 1.5, shadows_ev = 1.5, chroma = 0.45 }
intent   = []             # "darker", "richer": see S4
tested   = false          # set after the loop's round and the owner's vote

[blue_hour.run]           # run settings the style switches on, as a genre pack does
denoise = true
```

`configs/targets.toml` says where finished photos of a kind sit, as **ranges**: the middle half
of what the loop measured on finished photos, for the whole frame and per kind of region.

```toml
[blue_hour.global]
p50    = [0.39, 0.49]     # the photo's median brightness lands in here
chroma = [0.070, 0.090]
[blue_hour.sky]
p50    = [0.45, 0.60]
[blue_hour.lights]
hold   = true             # never pulled; see S3
[blue_hour.meta]
photos = 0                # how many finished photos the ranges come from
```

- The measures are the ones `reference_features` already computes (`p10`, `p50`, `p90`, `lstd`,
  `sat`, `chroma`, `warmth`, `tint`, and the rest).
- A photo inside a range is left alone on that measure. A photo outside is pulled to the nearest
  edge of the range, scaled by `pull` and by the strength slider, and never past `limits`.
- The existing search does the pulling. It takes a table of ranges where it takes a reference
  photo today. Every result is a named setting with a reason in the sidecar's notes, such as
  "Blue hour: frame lifted toward its range (median 0.29 → 0.40)". The pixel-level colour transfer
  of reference matching is not used for styles, because it cannot be shown as steps.
- The search keeps the clipping budgets of `quality.toml` as hard limits. A candidate that adds
  more clipping than the budget is refused inside the search, so a style settles for less
  brightness instead of a review mark.
- `scripts/style_target.py` writes a target table from a list of finished photos, through the
  real pipeline's masks. It writes numbers only, never a photo, a file name or an author.

A fixed target would force every photo to one brightness. Ranges, `pull` below 1, `limits`, and
the fact that the photographer chose the style keep that in check. The loop's control photos show
whether it holds.

### S2. Shape inside the frame

Three additions to the compose moves, and one new op.

- **`within = "sky"`** limits a move to a region. The move's weight is multiplied by that
  region's feathered mask. If the region is absent, the move does not fire. A sky burn then
  cannot darken the mountains under the sky.
- **`shape = "band"` with `at = "horizon"`** places a horizontal band on the straight waterline
  or horizon that `geometry.horizon` trusts, with a `height` and a `feather`. No trusted line, no
  move. This is the lighter far shore of the second example. It changes brightness and contrast
  where the picture already is; it adds nothing.
- **A style carries its own moves** as `[STYLE.moves.NAME]`, with the fields of `compose.toml`.
  A style's move replaces the packaged move of the same name. The packaged whispers stay as they
  are for Standard.
- **A new op, `burn_ev`** (0 to 2): darker middle and bright tones with black pinned. A vignette
  or burn made with it cannot add shadow clipping.

  ```
  u    = clamp( ln(l / t0) / ln(t1 / t0), 0, 1 )      t0 = 0.03, t1 = 0.50
  w    = u² (3 − 2u)
  out  = l · 2^(−burn_ev · w)                          colour follows by the luma ratio
  ```

  `l` is the luma the other tone ops use, in the encoding `shadow_lift` works in. Pixels at or
  below `t0` are unchanged, and `t0` stays above the clipping level of `stats.py` (0.02). The
  curve rises everywhere while `burn_ev < 2.7`: its slope is positive when
  `burn_ev · ln 2 · w′(u) / ln(t1/t0) < 1`, and `w′` is at most 1.5. The bound of 2 leaves room.
  It joins the step of `shadows_ev` in the frozen order, and is gated on a non-zero amount like
  every op.

### S3. Lights hold

- A new thing to find, `lights`, with the words "lamp", "street light" and "lit window" in
  `concepts.toml`. If SAM 3 misses the lamps on more than 2 of 10 blue-hour photos in the loop,
  the bright compact spot rule of the light finder (`light.py`) is used for this region instead.
- A rule block for it in `rules.toml`, joined only by styles that list `lights`: the region takes
  back most of the frame's lift and pulls its highlights down.
- A `hold` region is never pulled toward a target (S1).

### S4. What a style may trip

Two checks compare the edit with the source and are right to stop an accident. A style that
means the change says so.

| Intent | Skips | Never skips | State |
| --- | --- | --- | --- |
| `darker` | The relative darkness check (`dark_made_darker`) | Clipping, banding, halos | Approved for an explicitly chosen Moody landscape style. |
| `richer` | The two relative colour checks ("saturation rose", `excessive_color_increase`) | Clipping, banding, halos, the guard on skin and snow | Approved for explicitly chosen Sunset glow and Moody landscape. |

`limits.chroma` raises the oversaturation limit of `rules.toml` (`[guard] max_chroma_increase`,
0.30) for a style that declares `richer`, to at most 0.90. No threshold in `quality.toml` changes.

### S5. The five styles

| Style | Meant for | Looks for | What it does | May trip |
| --- | --- | --- | --- | --- |
| Standard | Any photo | Today's list | Today's rules. Changes only by the owner's blind vote. | nothing |
| Blue hour | Dusk and dawn with lamps | sky, water, building, bridge, lights | Lifts the frame into its range; sky and water stay cool, lights stay warm and hold | nothing |
| Sunset glow | A bright warm sky over dark land | sky, cloud, water, mountain, snow, trees | Opens land and water; keeps and strengthens the warm sky; shaded snow and trees stay cool | `richer` |
| Moody landscape | Overcast, mist, lakes, mountains | sky, cloud, mountain, water | Burns the sky inside the sky; lifts a band at the waterline; a vignette with black pinned; deeper colour in one hue family | `darker`, `richer` |
| Natural portrait | A person as the subject | skin, face, subject, background | Skin brightness and colour into their range; the background a little under | nothing |

Seed values, from one frame each. The loop replaces them with ranges from many photos, and the
ledger records each change.

| Style | Seeds |
| --- | --- |
| Blue hour | Median brightness toward 100 to 125 of 255. Lift of at most 1.5 EV. Lights at most +0.7 EV. |
| Sunset glow | Land and water lifted by up to 3 EV from a RAW file, with noise reduction on. Sky about +1 EV. |
| Moody landscape | Sky −1.0 EV. Band +1.0 EV. Corners −0.6 EV with `burn_ev`. Colour up to 75 % stronger, toward blue (259°). Blacks a little raised. |

### S6. Suggest a style, never apply one

A `fits` table per style: a few tests on the measured numbers, in the form recipes already use
(`web/recipe_conditions.py`). The editor then shows one line, "This looks like blue hour", with the
style one click away. Built only if the loop shows the suggestion is right on 8 of 10 photos of
the kind and wrong on at most 1 of 10 control photos.

## Design decisions

| Decision | Implementation or remaining requirement |
| --- | --- |
| May Moody landscape skip the relative darkness check? | Yes for Moody landscape. No for the others. |
| May a style declare `richer`, which skips only the two relative colour checks? | Yes for Sunset glow and Moody landscape. The guard on skin and snow stays. |
| How far may a style's colour go? | Up to +90 % for a style that declares `richer`. Everything else stays at +30 %. |
| A lift of 3 EV on dark land | From a RAW file, with noise reduction on. A JPEG is capped at +2 EV. |
| Is a lighter, softer band where haze already is "tone" under the content rule? | Yes. The move needs a waterline or horizon, changes only brightness and contrast, and is reported in the sidecar. |
| Photos of people for Natural portrait | The owner's own, or a photographer's pairs (see F60). Until then the style stays `tested = false` and is not offered. |
| Standard | Unchanged unless a blind vote says otherwise. |

## Tasks

Tiers as in [AGENT_ROUTING.md](../AGENT_ROUTING.md). S1-T1, S2-T1 and S4 touch protected files
(`pipeline.py`, `edit/ops.py`, `quality.py`, `edit/gates.py`). This spec is their Planner spec, and
the Checker reviews them without exception. The order of the pipeline's stages does not change.

| Task | Tier | Files | Proof |
| --- | --- | --- | --- |
| S1-T1 | Builder (Sonnet, medium) | `styles.py`, `configs/styles.toml`, a new `configs/targets.toml`, `edit/reference.py`, the place in `pipeline.py` where reference matching runs, `tests/test_styles.py`, `tests/test_reference.py` | With no target the output is byte-identical. A made-up dark photo moves to the edge of its range and not past. No candidate adds clipping above the budget. The notes name what was pulled. |
| S1-T2 | Builder (Sonnet, medium) | a new `scripts/style_target.py`, a test | Made-up photos give the expected ranges. The output holds numbers only. |
| S2-T1 | Builder (Sonnet, high) | `edit/ops.py`, `configs/lessons.toml`, `tests/test_ops.py` | Zero is identity. Pixels at or below `t0` are unchanged. A ramp stays rising at 2 EV. The share of pixels under the clipping level does not grow. Colour ratios hold. |
| S2-T2 | Builder (Sonnet, medium) | `compose.py`, `configs/compose.toml`, `styles.py`, `tests/test_compose.py`, `tests/test_shaped_light.py` | A move with `within` changes no pixel outside the feathered region. A band sits on a made-up waterline and is absent without one. A style's move replaces the packaged one. |
| S3 | Builder (Sonnet, medium) | `configs/concepts.toml`, `configs/rules.toml`, a golden test | The golden test; the loop's count of found lamps |
| S4 | Builder (Sonnet, medium), after the owner's yes | `looks.py`, `quality.py`, `edit/gates.py`, tests | Only the two relative colour checks are skipped. Every other failure still fires. `quality.toml` is untouched. |
| Check | Checker (Opus, high) | read-only | The diff against this spec; the definition of done; `tone_report.py --style NAME --render --fail-on-regression` per style on the 110 RAW files |
| S5 | The loop of F60, one round per style | config only | The ledger, the held-back sheet, the owner's vote |
| S6 | Builder (Sonnet, medium), if the loop's numbers allow | `styles.py`, the editor's AI Edit section | The two rates above |
| Docs | Tweaker (Haiku, low) | `CHANGELOG.md`, `FEATURE_PLAN.md`, `APP_GUIDE.md`, `ARCHITECTURE.md` | none |

Order: S1, S2, S3, then the Check, then S5 for Blue hour (it needs the least), Sunset glow, Moody
landscape. The owner approved S4 for these named styles.

## Acceptance

For the code (S1 to S4):

- `PYTHONPATH=src python tests/run_all.py` is green and stays pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, and its pictures are
  byte-identical to the commit before. No default changes.
- The tone report on the 110 RAW files reports 0 dark to darker and 0 pushed into clipping, by
  default and with each style on. A style that declares `darker` is reported apart.
- `python scripts/check_public_repo.py` passes.

For a style (S5), on photos of its kind in the loop:

| Bar | First value |
| --- | --- |
| Distance from the photographer's result on pairs, median | 0.35 EV or less (today 0.56 to 1.26 by the rules) |
| Review marks other than a declared intent | none new against Standard |
| The Checker's blind verdict on held-back photos | the style is better than Standard on 7 of 10, and clearly worse on none |
| Control photos (ordinary daylight) under Standard | unchanged |
| The owner's blind vote | decides; only then `tested = true` |

The first round may correct the first values with evidence. The ledger records it.

## Left out, and what would bring each back

| Left out | Bring it back when |
| --- | --- |
| The learned recipe predictor as the source of a style | Ranges fail on a kind of photo where the right move depends on the photo, and there are pairs to learn from |
| A colour mixer with hue per band in the engine | A style cannot reach its hue with palette, temperature and tint. LightCraft's mixer covers manual work. |
| Point curves in the engine | A style needs a tone shape the existing ops cannot make |
| "How does my photo differ from this one", in words | The owner asks; it belongs with Learn |
| Time of day from capture time and place | The `fits` tests of S6 are too often wrong |
| Fog, light rays, a new sky | Never: nothing is generated |
