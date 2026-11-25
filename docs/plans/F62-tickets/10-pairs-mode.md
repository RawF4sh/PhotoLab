# 10. Measure before-and-after pairs

Status: done
Spec: [F62](../F62-auto.md), milestone M2
Who: Builder (Sonnet, medium)

## What to build

`scripts/style_target.py` grows a pairs mode:

- Split side-by-side images, align before and after (the prototype's edge-shift alignment).
- Measure `reference_features` of both, per region (global, sky, land, water, mountain), and the
  move between them.
- Keep the third of the pairs that moved the photo most (landscapes) or the middle third
  (portraits, later). Each range is the middle 80 % of that third; each control's limit is the
  move's 90th percentile.
- Write a `targets.toml` table with `photos = N`, and the limits for `auto.toml`. Refuse a scene
  with fewer than 45 pairs.
- Reads downloaded files: run with `python -I`, never from inside `curated-web/`.

## Done when

- `tests/test_style_target.py`: synthetic pairs with a known move give back that move and the
  expected ranges; a side-by-side image shifted by a few pixels is aligned.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — The isolated pairs command aligns split or separate images, measures each available region and estimates equivalent control moves. It emits numeric targets, limits and optional routing, and rejects a scene below 45 kept pairs. A known 1.5 EV edit and translated image pairs were recovered in the tests.
