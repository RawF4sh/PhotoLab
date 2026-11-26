# 24. A fit that stops before its own faults

Status: needs-triage
Blocked by: 21, 22, 23
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, high), once the owner or the session takes it off hold. Hard: the fit, the
retry loop and a safety module in one change.

**On hold, by the Planner's advice.** The design below was replayed on the development sheet at
full size. With tickets 21 to 23 in place it moves one more photo over a distance of 3 (8 to 9 of
12) and takes the median from 5.53 to 5.12. Neither release bar depends on it: the median is
reached without it, and 80 % of the photos at 3 or more is not reached with it. What keeps three
photos under 3 is the provisional goal, which asks little of them, not a fault. Build this ticket
when a sheet with measured goals shows photos cut back for a fault inside the photo (lost
highlight texture, a blown or a dulled light).

## What to build

When Auto's fault check fails, the retry loop scales the whole edit down until it passes. On the
development sheet four photos fail because of one or two controls each:

- a sunset: the sky lift of +1.3 EV flattens the glow around the sun. The rest of the edit passes
  on its own (distance from Simple 3.4), and the photo ends at 25 % (2.15).
- the sunset with the sun on the horizon: the frame's −2.1 EV turns the sun grey (ticket 23), and
  the land lift of +2.0 EV flattens the rim of the sun.
- the daylight frame with the sunlit rock: `whites` +74 blows the rock.
- the twilight frame with the lamps: the frame lift of +0.9 EV blows more lamps.

The fit's own clipping test cannot see these. It counts newly clipped pixels in the finished
render of its part against a budget of about 1 % of that part. A lift that flattens one small
bright patch stays inside the budget, and a region lift that clips before the frame pulls the
result down again leaves no clipped pixel at the end.

With this ticket, after the fault check has named a fault of this kind, the photo is fitted again
with a guard: each control stops where the photo's own pixels would fail one of those checks, and
the other controls keep their amounts. The whole-edit factors stay as the fallback. A photo whose
first render passes is not touched, and neither is a photo that fails only a boundary check.

Protected area: build exactly this, and a Checker (Opus, high) reviews the diff before the ticket
closes. `assess_pixels` and `configs/quality.toml` do not change.

**1. `src/autoedit/quality.py`**

- `INTERIOR_FAULTS = ("encoded_highlight_texture_loss", "blown_light_source", "dulled_light_source", "skin_colour_shift")`:
  the checks one part of an edit can fail inside its own pixels.
- `interior_excess(source, output, masks=None, thresholds=None) -> dict`: for those checks, how far
  `output` is over each limit, measured with the code and the limits of `assess_pixels`: the config
  validated the same way, `_sample`, `_texture(source, output, cfg, sampled_masks, luma(sample))`,
  and `_protected_regions` on the two sampled pictures. Keys and values:
  - `("encoded_highlight_texture_loss", x, y)` for every reported patch:
    `texture_min_retention - retention`;
  - `("blown_light_source", name)`: `<name>_newly_blown_fraction - light_clip_fraction`;
  - `("dulled_light_source", name)`: `<name>_dulled_fraction - light_clip_fraction`;
  - `("skin_colour_shift", name)`: `<name>_colour_shift - skin_colour_delta`.

  A value above 0 is a failure of `assess_pixels` on the same two pictures. The inputs are two
  finite pictures of one shape, as the fit renders them.

**2. `src/autoedit/edit/style_target.py::fit(..., faults=None)`**

`faults(settings) -> dict` gives the excess of every interior check for the plan with this call's
part set to `settings` (step 3 builds it). With `faults=None` nothing changes.

- Before the search: `allowed = {key: max(0.0, value) for key, value in faults(base).items()}`.
  Settings are **clean** when every value of `faults(settings)` is at most
  `allowed.get(key, 0.0) + 1e-6`: each check is inside its limit, or no worse than before this call
  changed anything.
- After the 8 search steps of a control, when the control moved: try
  `initial + cut * (reached - initial)` for `cut` in
  `sorted({1.0, *safety["remediation_factors"]}, reverse=True)` and keep the first clean one.
  `cut` 0 stands for the settings from before this control's search and is kept without a check.
  When `cut` is below 1: `best` becomes those settings, `current_features` is measured again on the
  sample (`features(render(small, best), sample_mask, ranges.keys())`), and the control's name is
  remembered.
- In the last loop (the factors 1, .5, .25, .125 on the whole analysis picture) a candidate that is
  not clean is skipped, like one that fails `clipping_ok`.
- When a control was held back, the returned notes end with
  `held back before a fault: <controls>`.

**3. `src/autoedit/pipeline.py`, `process_one(..., auto_guard: bool = False)`** (keyword only, next
to `auto_strength`; only the retry loop sets it).

- The lines of the fault check that build `masks` (`resolved.union` plus the trusted `sun` and
  `lights` masks) move into a method `_fault_masks(resolved, raw)`. The check and the guard use it.
- With `auto_active and auto_guard`, before the frame fit: `fault_masks`, the limits the check
  uses (`{**safety, "preserve_exposure": True, "richer": True}`), and a function
  `own_faults(frame, name=None, settings=None)`:
  - start from a copy of `proxy.rgb`;
  - for every region of `resolved.regions`, in order: its settings are `settings` when
    `region.name == name`, otherwise `region_settings.get(region.name)`. With settings, the pixels
    of `region.mask` take `ops.apply_ops(proxy.rgb, <its settings>)`. Keep these renders in a dict
    keyed by region name and settings, so a region that does not change is rendered once;
  - `ops.apply_ops(<picture>, frame)` when `frame` is not empty;
  - return `quality.interior_excess(proxy.rgb, np.clip(<picture>, 0.0, 1.0), fault_masks, <limits>)`.
    The clip is what `apply_image` does to the finished picture. Without it `whites` leaves values
    above 1, and the texture check reads them as kept detail (the first prototype missed the blown
    rock for this reason).
- The frame fit gets `faults=own_faults`. The fit of a region gets
  `faults=lambda s, n=region.name: own_faults(global_settings, n, s)`. Without the guard both get
  `None`.
- Moves, the shoot pull and everything after the fit stay as they are.

**4. `_retry_auto_faults.checked`,** as ticket 21 leaves it, where the first render has been judged
again and `faults` is not empty:

- When the kind of one failure (the text before its first `:`) is in `quality.INTERIOR_FAULTS`:
  `options["auto_guard"] = True`, and the requested strength is rendered again first, with the
  caller's own `auto_strength` (as ticket 21 does for a photo with only inherited faults). The
  factors below 1 follow as today, with the guard still on. Each of these attempts carries
  `"guarded": True` in `safety_attempts`.
- Otherwise the loop runs as today, without the guard. This matters: after a failure of the
  boundary check alone, a guarded refit took a twilight frame from a distance of 6.6 to about 2 on
  an analysis-size replay, because it held a lift back for lit windows the real check accepts.
- After the loop, with the guard on: `audit["guarded"] = True`. The note reads
  `Auto refitted with the fault guard after the photo failed: <reason>.` when the accepted strength
  is the first attempt's strength, and `Auto strength reduced to <n> % ...` as today otherwise.

Cost: nothing for a photo whose first render passes or fails only a boundary check. Otherwise one
more render, and a guarded fit that took about 3 seconds longer than a plain one on the sheet.

**5. Docs.** One line in `CHANGELOG.md`; in the spec the "Checks under Auto" bullet says that such
a photo is first fitted again with the guard.

## Left out

- **Boundary faults, part by part.** `severe_region_boundary_artifact` is not a mask defect. A
  guided mask follows the real edge, so two regions moved by different amounts leave a step in the
  luma change at that edge, and the check fails when the two sides differ by more than 0.25 on 1 %
  of the boundary (a sky darkened beside lifted land, or beside dark posts). The guard does not
  measure it: on the analysis picture the same plan reads about 1.5 times higher than the real
  check (0.43 against 0.27 on a twilight frame), so it would cut too much. The whole-edit factors
  hold three photos at 50 or 75 %, all with a distance above 6. Bring back a factor for the regions
  alone when a release sheet has photos under the bar for this check only.
- **Moves.** No fault on the sheet comes from a light move once the masks reach the frame edge.
  Guard them when a move alone fails a photo.
- **Opposing frame and region moves.** On the sunset with the sun on the horizon the frame went
  −2.1 EV and both regions +2.0 EV. With the guard the frame stops near −0.5 EV and the regions
  have little to take back. Net the two when release sheets still show them cancelling.
- **A check the Simple plan already fails** gets no tolerance: a control that makes it worse by
  any amount is held back. On the twilight frame with the lamps that holds back four of the
  frame's five controls. Give such a check its own limit on the step from the Simple plan, as
  ticket 21 does for the final verdict, when release sheets show controls held back for nothing.
- **Small lights.** For a patch of a few dozen pixels (lit windows) the analysis picture reads half
  the texture the real check does (0.05 against 0.12), so the guard holds a lift back earlier than
  needed. It only runs after the real check failed. Render the checked patches at full size when a
  release sheet shows the loss.
- **The shoot pull** can raise a held-back control again; the whole-edit factors then catch it.
  Bring it back when a shoot shows it.

## Done when

- `tests/test_local_styles.py`, `style_target.fit` on a flat coloured frame with a goal for `p50`
  and one for `chroma`, and a fake `faults`:
  - `faults = lambda s: {"bright": s.get("exposure", 0.0) - 0.4}`: the fitted exposure is above 0
    and at most 0.4, below the plain fit's; `vibrance` still moves; the notes name `exposure` as
    held back.
  - The same with `auto=True, aim=0.5` and strength 1.5: the exposure is still at most 0.4.
  - A base that is already over (`base = {"exposure": 0.5}` with the same `faults`): the exposure
    stays 0.5. With `faults = lambda s: {"bright": 0.2}` (over, and never worse) the result equals
    the plain fit.
  - `faults = lambda s: {"x": -1.0}` and `faults=None`: both equal the plain fit.
- `tests/test_quality_pixels.py`, `interior_excess`:
  - on the pictures of `test_encoded_texture_collapse_is_detected_below_white`, of
    `test_small_light_and_skin_faults_are_measured_against_the_same_region_of_the_original` and of
    ticket 23's dulled light: the kinds with a value above 0 are exactly the failures of
    `assess_pixels` whose kind is in `INTERIOR_FAULTS`;
  - the unchanged picture: no value above 0;
  - a hard luma step along a mask boundary (it fails `severe_region_boundary_artifact`): no value
    above 0.
- `tests/test_quality_pixels.py`, the pipeline, with the fake `assess_pixels` of ticket 21 and a
  fake `quality.interior_excess` that answers from the pictures (for example the brightest luma
  over a level):
  - The first render reports `encoded_highlight_texture_loss` and the guarded fit avoids it: the
    photo keeps the requested strength, `safety_attempts` has two entries (the second with
    `"guarded": True` and no failures), `audit["guarded"]` is True, the note says "refitted with
    the fault guard", `needs_review` is False, and the pixels equal a plain call with
    `auto_guard=True`. `fit` got a `faults` function in the second render only.
  - The first render reports only `sky_edge_halo`: no guarded attempt; strength, attempts and
    pixels as before this ticket, and `fit` never got `faults`.
  - The guarded attempt still fails: 75 % follows with the guard on, and the pixels equal a plain
    call with `auto_strength=.75, auto_guard=True`.
  - With `on_stage`, in the first case: one `candidate_decode`, one `final_float`, no "verdict
    changed" error.
- `tests/test_scene.py` and `tests/test_auto_release.py` pass unchanged: 0 % and an unreleased
  scene still equal Simple exactly.
- The private development sheet (`out-webloop/f62/sheet`, the settings of its Auto arm) rendered
  again, with strength, distance and review mark per photo, before and after, under Comments. The
  Planner's replay at full size (tickets 21 to 23 in place) expects:
  - the sunset: 25 % to 100 % with the guard (sky lift held at +0.3 EV, `whites` and the highlight
    grade held back); distance 2.15 to 3.46;
  - the sunset with the sun on the horizon: 25 % to 100 % (frame exposure held at −0.5 EV, land
    lift at +0.2 EV), with a white sun; distance 4.45 to 3.63. The full edit differs less from
    Simple than the cut-down one, which kept a frame darkening that no region took back;
  - the daylight frame with the sunlit rock: 12 % to 50 % (`whites` held back; the boundary check
    holds the rest); distance 0.65 to 1.81;
  - the twilight frame with the lamps: 12 % to 75 % (frame lift held back); distance 1.97 to 2.41;
  - the three photos that fail only the boundary check and the five that pass at once: unchanged;
  - no photo gains a review mark; the sheet's median distance goes from 5.53 to 5.12, and 9 of 12
    photos are at 3 or more (8 before).
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars and manifest, and its
  images are byte-identical to the previous commit.
- `PYTHONPATH=src python scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression`
  reports 0 dark→darker and 0 pushed-into-clipping for the default run.
- `python scripts/check_public_repo.py` passes.
- No git writes: the owner gets a commit block.

## Comments
