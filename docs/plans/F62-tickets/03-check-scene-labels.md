# 03. Check the scenes against 120 labelled originals

Status: ready-for-agent
Blocked by: 02
Spec: [F62](../F62-auto.md), milestone M1
Who: Checker (Opus, high)

## What to build

Label 120 landscape originals by eye (FiveK originals already on disk, the in-repo Dolomites
corpus), without seeing the classifier's answers, then compare. Labels and the comparison stay in
the private `out-webloop/` folder; only the counts go here.

## Done when

- Agreement 85 % or more, and no daylight photo called twilight or the reverse.
- If missed: findings naming the thresholds to change, under Comments. Ticket 02 is reopened.

## Comments

2026-11-25 — Visually labelled the originals before seeing predictions. The 120 originals are distinct by source SHA256. Predictions from successive revisions were compared with these same fixed labels. Revisions were developed on separate Swiss shoots using the types of errors found. These 120 photos and their shoots are excluded from the eventual preference rounds. The final check used local subject masks, no forced scene and no remote screening.

| Label | Correct | Total |
|---|---:|---:|
| Daylight | 58 | 64 |
| Twilight | 18 | 23 |
| Sunset | 11 | 13 |
| Mist or overcast | 10 | 20 |
| All | 97 | 120 |

Agreement was 80.8%, five correct labels short of the 85% bar. There were zero daylight-to-twilight or twilight-to-daylight swaps. Nine mist/overcast scenes were called daylight, six daylight scenes were called mist and three twilight scenes used the broad landscape fallback.

The colour-only classifier scored 98/120, with 7/20 mist and 62/64 daylight. On 27 separate Swiss development originals, cloud geometry and sky-colour variation helped distinguish broad cloud decks from small blue openings across changes in white balance. On the fixed check, that raised mist recognition to 10/20 while lowering daylight to 58/64. Inspect the false cloud decks at `deck_min_coverage`, `deck_width_min` and `deck_max_blue_tail`. Seven of the nine missed overcast scenes took the bright-exposure decision. The next comparison should cover bright, diffuse lighting near `sunlit_capture_min_ev` and sunlit cloud banks on fresh labelled shoots. Labels, source hashes and predictions remain private. Ticket 02 is reopened.
