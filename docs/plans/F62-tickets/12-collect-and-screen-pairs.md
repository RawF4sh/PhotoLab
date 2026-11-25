# 12. Collect and screen pairs per scene

Status: ready-for-agent
Blocked by: 10, 11
Spec: [F62](../F62-auto.md), milestone M2
Who: Builder (Sonnet, medium)

## What to build

- Fetch pairs from the approved sources inside the approved budget, with the F60 web loop's
  budgets and fetch log. Files stay in the private `curated-web/` folder.
- Screen each pair with the approved model, in a fixed answer form: the scene, whether the before
  is unedited, and whether the after is weak, good or overdone. Keep only good pairs of the right
  scene. Store the answers beside the measurements, never the images.
- Nothing fetched is committed; no author names or web file names in committed files.

## Done when

- The kept count per scene under Comments; 45 or more for each scene that goes on to ticket 14.
- The fetch log shows the budget was kept.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
