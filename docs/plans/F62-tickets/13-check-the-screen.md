# 13. Check the pair screen

Status: ready-for-human
Blocked by: 12
Spec: [F62](../F62-auto.md), milestone M2
Who: Checker (Opus, high)

## What to build

Look at 20 kept and 20 refused pairs per scene and judge whether the screen was right: scene,
unedited before, good after.

## Done when

- Findings under Comments. If more than 3 of 20 kept pairs per scene are wrong, the screen's
  prompt is fixed and ticket 12 re-runs the screen (not the downloads).

## Comments

2026-11-25 — Daylight has 20 kept and 31 refused pairs. The private daylight review contains all 20 kept and a reproducible sample of 20 refused pairs, mixed and numbered. Its form hides the model's judgments. A reviewer can label each pair in a browser and export the completed answers for comparison. The daylight review is awaiting independent labels.

Twilight has 2 kept and 15 refused, mist or overcast has 2 and 16, and sunset has 6 and 4. Each needs 20 in each group before its independent review can begin.
