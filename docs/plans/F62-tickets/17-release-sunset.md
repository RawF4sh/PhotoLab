# 17. Release round: sunset

Status: needs-info
Blocked by: 03, 15
Spec: [F62](../F62-auto.md), milestone M4
Who: Session, then the owner

## What to build

As ticket 16, on held-back sunset originals.

## Done when

- As ticket 16, for sunset.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — Sunset remains unreleased. No held-out release round or owner vote has been conducted on measured goals. The private round tool verifies independent shoots, encoded render pairs, per-photo fault reviews and the complete vote. The provisional development sheet does not count toward this round.

| Held-out photos | Median distance | Photos at distance ≥3 | Fault panel | Votes for Auto / total | Released |
|---:|---:|---:|---|---:|---|
| 0 | — | — | Pending | 0 / 0 | No |
