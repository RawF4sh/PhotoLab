# 21. Pull Auto back only for faults it adds to Simple

Status: ready-for-agent
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

Auto's fault check compares every render with the original, so a fault the Simple render of the
same photo already has is charged to Auto. On the development sheet one twilight frame fails
`blown_light_source:lights` at every strength, 0 % included: it ends on Simple and gains a review
mark that its Simple render does not have. From now on Auto answers only for what it adds.

The rule, with no threshold changed: a check the Simple render passes stays held against the
original, as today. A check the Simple render fails is held, at the same limit, against the Simple
render instead. Auto is therefore never accepted at a strength that fails today for a reason other
than a fault Simple already has. The sheet's other six cut-back photos fail a check their Simple
render passes, so they keep their strength.

Protected area: build exactly this, and a Checker (Opus, high) reviews the diff before the ticket
closes. `assess_pixels`, `configs/quality.toml` and `scripts/auto_release.py` do not change (the
release tool reads `fault_check["failures"]`, which now holds the added faults).

**1. `src/autoedit/quality.py`: `added_faults(source, output, masks, thresholds, baseline, first=None)`**
returns `(added, inherited)`, two lists of failure names in the order of the first report.

- `first` is the report of `assess_pixels(source, output, masks, thresholds)`, computed when not given.
- `first` has no failures, or `baseline is None`: `(first["failures"], [])`.
- `base = assess_pixels(source, baseline, masks, thresholds)`. No failure name in both `first` and
  `base`: `(first["failures"], [])`.
- `own = assess_pixels(baseline, output, masks, thresholds)`. A failure of `first` is **inherited**
  when all four hold; every other failure is **added**:
  - all three reports have `metrics["comparison"]`. `assess_pixels` returns before it sets that
    key on an invalid shape or invalid pixels, and an unmeasured report inherits nothing;
  - `base` lists it (the baseline fails that check against the source);
  - `own` does not list it (the step from the baseline to the output passes it);
  - for `encoded_highlight_texture_loss`: every patch in `first["metrics"]["highlight_texture"]["patches"]`
    with `retention` below `texture_min_retention` is also below it at the same `x`, `y` in `base`.
    The texture check samples patches, and `own` may sample other places than `first`.

**2. `src/autoedit/pipeline.py`, `process_one`, the `check_auto` branch.** The `assess_pixels` call
keeps its reference, masks and limits, and `ridge` still comes from it. After it:

- `kept` is `_retry_inputs`, or an empty dict when that is `None`.
- At strength 0 (`not auto_active`) the render is the Simple render: `kept["simple"] = post_proxy.rgb`.
- `kept["check"] = (reference.rgb, post_proxy.rgb, masks, <the limits given to assess_pixels>)`.
- `added, inherited = quality.added_faults(*kept["check"], kept.get("simple"), first=check)`.
- `auto_audit["fault_check"] = {"failures": added, "inherited": inherited, "sky_edge_halo": ridge}`.
- `inherited` not empty: one line in `notes_extra`,
  `Auto fault check: the Simple edit already fails <names>; not counted against Auto.`

At strength 0 the render is its own baseline: `failures` is empty and what the check found is in
`inherited`. An unmeasured report still counts, so it still ends in review as today.

**3. `src/autoedit/pipeline.py`, `_retry_auto_faults.checked`,** from the first render to the end of
the retry loop. A first render without failures goes on as today. So does a requested strength of
0: its failures are recorded as the one attempt and nothing is retried. Otherwise:

- Before anything else is rendered, keep the first render's `audit["strength"]`, the caller's own
  `auto_strength` (it may be `None`) and `options["_retry_inputs"]["check"]`.
- Render Simple once: `auto_strength = 0.0`, after `del rgb` and `events.clear()` as for any retry.
- Judge the first render again without rendering it:
  `faults = quality.added_faults(*<kept check>, options["_retry_inputs"]["simple"])[0]`.
- `faults` not empty: `attempts` starts with `{"strength": <first strength>, "failures": faults}`,
  then the factors below 1 as today (`remediation_factors` and 0.0, descending).
- `faults` empty (only inherited faults): render the requested strength again with the caller's own
  `auto_strength`; the factors below 1 follow only if that render reports failures.
- In the loop an attempt is recorded when it has failures or `attempts` is not empty, and the loop
  stops at the first attempt without failures. A photo with only inherited faults therefore has
  no `safety_attempts` and no "strength reduced" note.
- The code after the loop stays as it is: the stage replay with its "verdict changed" check, the
  note from `attempts[0]["failures"]`, and the "Simple also needs review" branch.

Cost: nothing for a photo whose first render passes. Otherwise one more render (Simple) and, per
failing attempt, up to two more assessments of the analysis picture (about half a second each next
to a render of 5 to 17 seconds on the development sheet). A photo that fails at every strength
renders Simple twice.

**4. Docs.** One line in `CHANGELOG.md`; in the spec the "Checks under Auto" bullet and the ticket
range (01 to 21).

## Done when

- `tests/test_quality_pixels.py`, `added_faults` on synthetic arrays, with the highlight ramp of
  `test_encoded_texture_collapse_is_detected_below_white` as the source:
  - Simple already flattens the ramp and Auto adds nothing (the same picture, and that picture made
    uniformly darker): `([], ["encoded_highlight_texture_loss"])`.
  - Simple keeps the ramp and Auto flattens it: added.
  - Two ramps, Simple flattens one, Auto flattens the other as well: added.
  - Simple keeps about a third of the ramp's contrast and Auto about a twentieth, so each step
    passes alone and the result fails: added.
  - A NaN in the baseline: every failure added, none inherited.
  - With crafted reports (a patched `assess_pixels`), `base` failing the texture check and `own`
    clean: a failing patch of `first` that passes in `base` makes it added; with that patch
    failing in `base` too it is inherited.
- `tests/test_quality_pixels.py`, the pipeline. The ordered mocks of
  `test_auto_retries_share_one_decision_and_return_the_accepted_editable_settings` become a fake
  `assess_pixels` that answers from its arguments; its reports carry `metrics["comparison"]` and
  `metrics["highlight_texture"]["patches"]`. Cases:
  - Simple already fails (every report against the original lists the texture check, none against
    the Simple render): Auto keeps the requested strength, the pixels equal a plain render at that
    strength, `failures == []`, `inherited` lists the check, no `safety_attempts`, `needs_review`
    is False, the note is there, `apply_image` ran three times, one decision, one segmentation.
    The same holds with `auto_strength=.75`.
  - Auto adds a fault at full strength only: 75 %, two `safety_attempts`, the note names the fault,
    the pixels equal a plain render at 75 %.
  - Simple already fails and Auto's own step fails the same check at full strength: 75 %, and the
    first attempt lists that check.
  - Every strength above 0 adds a fault: 0 %, the pixels equal Simple, `failures == []`,
    `needs_review` is False.
  - With `on_stage`, in the first and the third case: one `candidate_decode`, one `final_float`,
    no "verdict changed" error.
- `tests/test_scene.py` and `tests/test_auto_release.py` pass unchanged: 0 % and an unreleased scene
  still equal Simple exactly.
- The private development sheet (`out-webloop/f62/sheet`, the settings of its Auto arm) rendered
  again, with strength and review mark per photo, before and after, under Comments. The Planner's
  replay with `aim = 0.25` expects one change: the twilight frame at 0 % goes to 25 %, loses its
  review mark and reports `inherited = ["blown_light_source:lights"]`. No photo gets a lower
  strength or a new review mark.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars and manifest, and its
  images are byte-identical to the previous commit.
- `PYTHONPATH=src python scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression`
  reports 0 dark→darker and 0 pushed-into-clipping for the default run.
- `python scripts/check_public_repo.py` passes.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — Planner. Tickets 22 to 24 build on this one, and the spec's ticket range already reads
01 to 24: step 4 leaves that line as it is. A replay of this ticket's rule on the sheet with
`aim = 0.5` gives the same single change as with 0.25: the twilight frame goes from 0 % to 25 %
(distance from Simple 2.45) and loses its review mark; no other photo changes.
