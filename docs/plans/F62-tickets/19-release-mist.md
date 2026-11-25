# 19. Release round: mist

Status: ready-for-agent
Blocked by: 03, 15
Spec: [F62](../F62-auto.md), milestone M4
Who: Session, then the owner

## What to build

As ticket 16, on held-back overcast or mist originals. Painted mist is out of scope (spec, "Left
out"); if the panel names its absence as the reason Auto loses, record it under Comments.

## Done when

- As ticket 16, for mist.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments
