# 07. Strength to 150 %, and a shoot that looks alike

Status: done
Blocked by: 01
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

- The existing `--intensity` and the app's strength slider run 0 to 150 % under Auto. 100 % is the
  goal; above it the move goes further in the same direction, inside the limits and checks.
- After the fit, each photo's settings are pulled toward its shoot group's median by
  `[auto] shoot_pull` (first value 0.5).

## Done when

- Tests: 0 % equals Simple; 150 % never crosses a limit; a group of three near-identical frames
  ends closer together than without the pull.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — Covered zero strength against Simple, bounded settings at 150%, the median pull for a group of three, and local retries for unsafe results. A mixed batch preserved its per-photo styles. Resuming the saved shoot, previewing it, opening variants and exporting it individually reproduced the batch settings; the individual and batch exports were byte-identical.
