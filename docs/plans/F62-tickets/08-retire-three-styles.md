# 08. Retire Blue hour, Sunset glow and Moody landscape

Status: ready-for-agent
Blocked by: 01
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

Their blocks leave `configs/styles.toml` and `configs/targets.toml`, and the interface stops
offering them. The machinery they used (moves, fit, limits) stays: Auto uses it. Tests that name
them are changed to use `auto` or a small fixture style. Their F59 and F61 history stays in the
specs.

## Done when

- `grep -rn "blue_hour\|sunset_glow\|moody_landscape" src/ tests/` finds only fixtures that say why.
- The app's style menu shows Simple and Auto (browser check).
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
