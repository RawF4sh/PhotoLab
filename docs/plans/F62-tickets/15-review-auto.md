# 15. Review Auto before the release rounds

Status: needs-info
Blocked by: 04, 05, 06, 07, 08, 09, 14
Spec: [F62](../F62-auto.md), milestone M3
Who: Checker (Opus, high)

## What to build

An independent review of the F62 diff:

- `apply_ops({})` is still the identity, and Simple is unchanged byte for byte on the test corpus
  and the in-repo RAW corpus.
- The halo check catches the prototype's halo.
- Under Auto: no control past its limit, the darkness check off only for landscapes, fault checks on
  everywhere, the unreleased fallback works.
- What leaves the computer is unchanged.

## Done when

- Findings as `file:line` under Comments, each fixed or answered.

## Comments

2026-11-25 — Independent review found and retested six issues. A corrected people scene now reaches the baseline decider (src/autoedit/pipeline.py:512). Retries reuse the same decision and masks (src/autoedit/pipeline.py:41). Auxiliary lights reach the fault check (src/autoedit/quality.py:256). A changed control stays within its limit after backoff (src/autoedit/edit/style_target.py:142). An impossible combined regional range adds no fitted or group correction (src/autoedit/consistency.py:456). A stale landscape vote cannot exempt a portrait (src/autoedit/styles.py:162). Full release sign-off awaits the checked, measured goals in ticket 14.

Compared the two default smoke exports and all 110 native Canon RAW exports with the previous revision. All 112 encoded images matched byte for byte, with no skipped or failed files. Four full-resolution RAWs were additionally replayed from their sidecars with the final renderer and matched again. The default tone report passed on all 110 RAWs. No owner photos were submitted to an external model during this review.
