# 12. Collect and screen pairs per scene

Status: needs-info
Blocked by: 10, 11
Spec: [F62](../F62-auto.md), milestone M2
Who: Builder (Sonnet, medium)

## What to build

- Fetch pairs from the approved sources inside the approved budget, with the F60 web loop's
  budgets and fetch log. Files stay in the private `curated-web/` folder.
- Screen each pair with the approved model, in a fixed answer form: the scene, whether the before
  is unedited, and whether the after is weak, good or overdone. Keep only good pairs of the right
  scene. Store the answers beside the measurements, never the images.
- Nothing fetched is committed; no author names or web file names in committed files.

## Done when

- The kept count per scene under Comments; 45 or more for each scene that goes on to ticket 14.
- The fetch log shows the budget was kept.
- `PYTHONPATH=src python tests/run_all.py` is green and the tests stay pytest-collectable.
- `autoedit run ./test-corpus ./out --backend stub` completes, with images byte-identical to the
  previous commit (nothing chooses Auto by default).
- `python scripts/check_public_repo.py` passes. CHANGELOG and docs updated if behaviour changed.
- No git writes: the owner gets a commit block.

## Comments

2026-11-25 — Collected and screened 96 complete pairs from 79 Play Raw topics by 39 original photographers. The first round reviewed 681 topics and collected 94 pairs. A follow-up reviewed another 88 topics and found two more eligible daylight examples. The fetch log accounts for 192 files totalling 3,031,805,240 bytes, with zero failed downloads. The private selection stores the source posts and licence evidence for each original and edit. The local gallery shows reduced-size viewing copies with credit, original links and screening results.

The owner selected free on-device screening for the real collection. A control test exposed an error in the first pass: the model marked the first image as edited even when both thumbnails came from the same original. The revised screen assesses the first image alone before comparing the pair for composition, scene and edit quality. On three prespecified examples it recognised the three neutral RAW renderings and three heavily manipulated controls. Three subtle edits also appeared camera-like, so the file-level provenance remains important. The original judgments are archived separately from the revised screen. No public or personal photos were sent to a remote provider.

| Expected scene | Screened | Kept | Refused |
|---|---:|---:|---:|
| Daylight | 51 | 20 | 31 |
| Twilight | 17 | 2 | 15 |
| Mist or overcast | 18 | 2 | 16 |
| Sunset | 10 | 6 | 4 |
| Total | 96 | 30 | 66 |

All 96 originals have distinct content hashes and source URLs. The independent file check verified the credits, licence references and hashes for all 192 files. Each pair's judgment matched the installed model version and its two source files. The two additional pairs were screened without changing any of the previous 94 decisions. The collection stayed within the shared byte budget and 300-pair ceiling. Daylight qualifies for the independent review in ticket 13. All four scenes still need additional kept examples for the 45-pair requirement in ticket 14.
