# 22. Auto's regions reach the frame edge

Status: done
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

The segmenter's masks stop a few pixels short of the frame edge. On all 12 photos of the
development sheet the sky mask ends 2 to 12 analysis pixels below the top edge on most columns.
On the frame with the white overcast sky (Auto calls it daylight) it touches the edge on 34 of
1,536 columns. The feathered edit fades out over that gap. Simple's edits are too mild to show it.
Auto's sky edits leave a light band along the top edge, and on that frame the fault check reads
the band as lost highlight texture at every strength from 25 % up, so the photo ends at 12 %.

From now on Auto's own regions are made from masks that reach the frame edge. Simple, an
unreleased scene and strength 0 render as before.

**1. `src/autoedit/pipeline.py`, `process_one`,** where `candidate_regions` is made (the call of
`landscape_regions`):

- `band = int((self.rules.get("masks") or {}).get("snap_edge_px", 12))`, the setting `--snap-edges`
  already uses.
- When the run has not snapped its masks (`px` is 0) and `band > 0`: `landscape_regions` gets a
  list in which every entry of `raw` is replaced by a copy with `snap_to_edges(rm.mask, band)`
  (`dataclasses.replace`, as the `--snap-edges` line above does). Otherwise it gets `raw`.
- `raw` itself is not changed. Simple's `resolved`, the scene measures (`sky_colour`,
  `cloud_cover`, `_find_light`), the reveal check, the geometry masks and the trusted `sun` and
  `lights` masks of the fault check keep reading the masks as the segmenter returned them.

**2. `src/autoedit/configs/rules.toml`, `[masks]`:** the comment of `snap_edge_px` also says that
Auto's own regions always use it.

**3. Docs.** One line in `CHANGELOG.md`.

## Left out

- **Gaps deeper than the band.** On the same frame the sky mask has three notches up to about 40
  pixels deep at the right frame edge, and they stay as light spots. A wider band is no fix: on the
  sheet real things at the edge (a palm, branches, a rock face) sit just as close. They differ
  from the sky beside them by 0.1 to 0.5 in luma, a false gap by less than 0.01. Bring it back as a
  fill that compares luma when the Checker's panel names a blotch at a frame edge.

## Done when

- `tests/test_edge_masks.py`, with the fake segmenter of `_snap_run` (a sky that stops 3 pixels
  below the top edge) and no `concepts` in the run settings:
  - `style="auto", scene="daylight", auto_unreleased=True`: the regions given to `on_resolved`
    have a sky that fills row 0, and the segmenter's own array is unchanged.
  - The same run with `intensity=0`, and a run with `style="standard"`: row 0 of the sky is empty,
    as today.
  - The Auto run with `snap_edges=True` gives the same regions as the first run: the masks are
    snapped once.
- `tests/test_scene.py` and `tests/test_auto_release.py` pass unchanged: 0 % and an unreleased
  scene still equal Simple exactly.
- The private development sheet (`out-webloop/f62/sheet`, the settings of its Auto arm) rendered
  again, with strength, distance from Simple and review mark per photo, before and after, under
  Comments. The Planner's replay at full size expects:
  - the frame with the white overcast sky goes from 12 % to 75 % (distance 3.61 to 16.06). Its top
    band is gone, and its one failure at 100 % is `severe_region_boundary_artifact`;
  - the daylight frame with the sunlit rock and the twilight frame with the lamps go one step
    lower, 25 % to 12 % (1.04 to 0.65, and 2.45 to 1.97 with ticket 21 built; without ticket 21
    the twilight frame stays at Simple). The gap lines no longer count as region boundaries, so
    the boundary check reads only real ones;
  - no other photo changes its strength, and no photo gains a review mark;
  - the sheet's median distance goes from 3.81 to 5.29, with 8 of 12 photos at 3 or more as
    before.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars and manifest, and its
  images are byte-identical to the previous commit.
- `PYTHONPATH=src python scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression`
  reports 0 dark→darker and 0 pushed-into-clipping for the default run.
- `python scripts/check_public_repo.py` passes.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 (session): Built as written: Auto's own regions use `snap_to_edges` copies of the masks; `raw` is untouched. The daylight frame with the grey top band goes from 0.65 (12 %) to 16.06 (75 %); the Checker's panel should still look at its flat grey sky (see Left out). Development sheet (12 photos, full size, `aim = 0.5`), Auto against Simple: median distance 3.81 before tickets 21 to 23, 5.52 after; photos at 3 or more stay at 8 of 12. Full suite 1,430 passed, 0 failed; stub images byte-identical to the previous state; tone report on the 110 in-repo RAW photos: 0 dark made darker, 0 pushed into clipping; public-repo check passes. A Checker (Opus, high) reviewed tickets 21 to 23 together: no blockers; Simple and default runs cannot reach the new checks (they run only while Auto is active, and no default run gets a sun or lights mask). One comment in `pipeline.py` corrected on its finding.
