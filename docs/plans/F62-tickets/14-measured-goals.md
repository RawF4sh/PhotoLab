# 14. Measured goals replace the provisional ones

Status: needs-info
Blocked by: 01, 02, 10, 13
Spec: [F62](../F62-auto.md), milestone M2
Who: Builder (Sonnet, medium)

## What to build

Run the pairs mode (ticket 10) on the kept pairs. Write `auto_sunset`, `auto_twilight`,
`auto_daylight`, `auto_mist` and `auto_landscape` into `configs/targets.toml` with `photos = N`,
and the limits into `configs/auto.toml`. Point `styles.toml [auto] targets` at them. A scene
below 45 pairs keeps the broader goal.

## Done when

- Only numbers enter the repository.
- The sheet from ticket 01 again, on the measured goals; the difference from Simple stays visible.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — The measured-goal writer is implemented. All four scene routes currently point to the provisional landscape goal. The local screen kept 20 daylight, 6 sunset, 2 twilight and 2 mist pairs. Daylight needs at least 25 more kept originals and a passing independent check before it can supply a measured goal. Sunset needs 39 more, and twilight and mist each need 43. No FiveK retouches were used to set product targets.
