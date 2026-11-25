# 16. Release round: twilight

Status: ready-for-agent
Blocked by: 03, 15
Spec: [F62](../F62-auto.md), milestone M4
Who: Session, then the owner

## What to build

On held-back twilight originals, split by shoot and never used before:

1. The sheet: Simple and Auto (`--auto-unreleased`).
2. The visible-difference bar: median OKLab distance from Simple 5 or more (×100), and 3 or more on
   80 % of the photos. If missed, stop: limits or goal are revisited first.
3. The Checker's fault panel and the automatic checks: no halo, banding, blotch, blown light or
   wrong skin colour; no new review marks against Simple.
4. A blind gallery of 10 or more photos for the owner. Never cast the owner's vote.

## Done when

- The bar table filled in under Comments and in the spec.
- Auto preferred on 7 of 10 or better: `released = true` for twilight in `auto.toml`.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
