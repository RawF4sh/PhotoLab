# 23. Name the fault: a light made grey

Status: ready-for-agent
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

A sun or a lamp that is blown to white in the original has no detail left. When an edit darkens
the frame or the region around it, the white disk turns grey. On the development sheet the sunset
with the sun on the horizon is accepted today at 75 % with a grey-blue sun: the white core falls
from a luma of 1.00 to 0.66 (0.56 at 100 %), and no check names it. The sun of the other sunset
falls to 0.84 at 100 %. Simple leaves both at 0.97.

The fault check gets one more check beside `blown_light_source`: a light that is white in the
original must stay a light.

Protected area: build exactly this, and a Checker (Opus, high) reviews the diff before the ticket
closes. No existing threshold changes.

**1. `src/autoedit/configs/quality.toml`:** `light_dull_level = 0.90`, with the other light
settings, and a comment: a lamp or sun that is white in the original (luma above
`highlight_level`) may not fall below this luma; the share is of the lamp or sun itself, the same
`light_clip_fraction` as for a newly blown light.

**2. `src/autoedit/quality.py`**

- `light_dull_level` joins `_NUMERIC_FIELDS`. `_validate_quality_config` also refuses a value above
  `highlight_level`.
- `_protected_regions`, in the loop over `sun` and `lights`, after the newly-blown test:
  `dulled = (luma(source[mask]) > highlight_level) & (luma(output[mask]) < light_dull_level)`.
  `metrics["<name>_dulled_fraction"]` is its mean over the mask. Above `light_clip_fraction` the
  failure is `dulled_light_source:<name>`.

The check runs wherever `assess_pixels` gets a `sun` or `lights` mask. In the pipeline that is the
Auto fault check only. A default run has no such mask, so its audits and the tone report read as
before. A light that is only bright in the original (not above `highlight_level`) is not judged,
and neither is the colour of a light.

**3. Docs.** One line in `CHANGELOG.md`; in the spec the "Checks under Auto" bullet names the new
check.

## Left out

- **The highlight hold (F6) as the remedy.** Tried on the sheet. It restores only neutral white, so
  the sun comes out as a cut-out disk inside its darkened glow, and the boundary check fails from
  50 % to 100 % (0.41 to 0.79 on analysis-size replays, against a limit of 0.25).
- **Stopping only the control that dulls the light**, where today the whole edit is scaled down:
  ticket 24, which is on hold.
- **Sparing the light from a darker frame** (the frame's darkening faded out around the detected
  light). Bring it back when measured sunset goals ask for a darker frame around a visible sun and
  this check is what holds them.

## Done when

- `tests/test_quality_pixels.py`, on the arrays of
  `test_small_light_and_skin_faults_are_measured_against_the_same_region_of_the_original` with the
  light at 1.0 in the source:
  - the light at 0.60 in the output: `dulled_light_source:sun` is in the failures and
    `sun_dulled_fraction` is 1.0;
  - the light at 0.95: no such failure;
  - a light at 0.95 in the source and 0.50 in the output: no such failure (it was not blown);
  - half of the light's pixels at 0.60, with `light_clip_fraction` at 0.6: no such failure;
  - the same mask named `lights`: `dulled_light_source:lights`;
  - the unchanged picture: passes.
- `tests/test_quality_config_validation.py`: a `light_dull_level` above `highlight_level`, below 0
  or not a number is refused.
- The private development sheet (`out-webloop/f62/sheet`, the settings of its Auto arm) rendered
  again, with strength, distance and review mark per photo, before and after, under Comments. The
  Planner's replay at full size (tickets 21 and 22 in place) expects:
  - the sunset with the sun on the horizon fails the new check from 50 % up and goes from 75 % to
    25 %, with a white sun. Its distance from Simple goes from 3.97 to 4.45: at 25 % the regions
    take back less of the frame's darkening than at 75 %;
  - the other sunset fails the new check from 50 % up as well, and stays at 25 %, where the
    texture check already held it;
  - none of the three other frames with a `lights` mask (two twilight, one overcast) fails the new
    check, and no other photo changes;
  - the sheet's median distance goes from 5.29 to 5.53, with 8 of 12 photos at 3 or more as
    before.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars and manifest, and its
  images are byte-identical to the previous commit.
- `PYTHONPATH=src python scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression`
  reports 0 dark→darker and 0 pushed-into-clipping for the default run.
- `python scripts/check_public_repo.py` passes.
- No git writes: the owner gets a commit block.

## Comments
