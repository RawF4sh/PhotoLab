# 05. An automatic halo check at the sky edge

Status: done
Blocked by: 01
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

A strong sky edit on a soft mask leaves a light band along the ridge (seen in the prototype's
Dolomites fit).

- A check on the proxy: along the sky boundary, a band that the edit made brighter than both the
  sky and the land beside it. Thresholds in `configs/quality.toml` (or `auto.toml` if it is only
  used by Auto).
- It runs for every style. Under Auto a photo that fails is pulled back to the strength that
  passes, with the reason in `notes`.

## Done when

- A test with a synthetic ridge and a soft mask: the check fails before and passes after a pull-back.
- The check reproduces the prototype's halo: run it on the prototype's Dolomites fit and the
  `fit_light` output (private `out-webloop/prototype-auto/`), report the number under Comments.
- No photo under Simple on the test corpus is newly flagged.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — The brighter-than-both-sides test measured 0.2592 on the regional prototype and 0.2110 on its shaped-light render, above the 0.12 threshold. The global-only fit was 0.0746, the example editor’s result 0.0724, and unchanged pixels 0.0000. Neither default smoke photo acquired a fault. A synthetic ridge exercises detection and strength reduction.
