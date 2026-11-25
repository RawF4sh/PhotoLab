# 15. Review Auto before the release rounds

Status: ready-for-agent
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
