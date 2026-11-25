# 04. Wider colour grading range

Status: done
Spec: [F62](../F62-auto.md), milestone M3
Who: Builder (Sonnet, medium)

## What to build

The prototype ran warm highlight grading into its limit (+100) on all three examples.

- The four `grade_*` ops in `edit/ops.py::OP_BOUNDS` go from ±100 to ±200.
- Values up to ±100 must render exactly as before; check how `color_grade` scales its amount
  and change only what is needed.
- This touches `edit/ops.py`: a math test and the tone report.

## Done when

- A math test: amounts in [−100, 100] give byte-identical output to the previous commit; ±200
  moves twice as far as ±100 in the grade's own measure.
- The identity test still holds.
- `PYTHONPATH=src python scripts/tone_report.py <raw-folder> --render --fail-on-regression` on the
  in-repo RAW corpus: 0 dark→darker, 0 pushed into clipping.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — All 804 combinations of the four controls and the original integer range −100 to +100 reproduced the original pixels. The extended ±200 controls passed the doubled-effect and identity tests. All 110 native RAWs completed the default tone report with zero dark→darker, zero newly clipped highlights or shadows and zero hard failures.
