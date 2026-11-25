# 06. Light moves scaled by the goal, and a guided sky edge

Status: ready-for-agent
Blocked by: 01
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

- Under Auto on landscapes, three moves with the existing `compose.py` shapes: the sky darkened
  from the top (`linear`, `within = ["sky", "cloud"]`), a frame vignette (`radial`), and a warm
  glow around the detected light (`glow`) on sunset scenes.
- A move's amount is scaled by how far the photo is from its goal, never past its limit in
  `auto.toml`.
- Sky and cloud edits under Auto use the guided feather the `within` masks already have.

## Done when

- Tests: a photo already at its goal gets no move; a far one gets the limit and no more.
- The sheet from ticket 01 again: the halo check (ticket 05, if done) passes on every photo.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
