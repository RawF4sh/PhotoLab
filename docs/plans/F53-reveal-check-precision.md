# F53. Reveal check precision: measure the seven unmeasured kinds

Status: spec (2026-11-22). Size M. Follows [F21](F21-reveal-check.md). Builds after F22 (hide on
request) has landed, as both edit `configs/reveal.toml`. No engine code changes, nothing changes a
pixel, and the check stays off by default.

## Goal

Seven of the check's twelve kinds were never measured: address, screen, document, card, badge, key
and code. They carry `weak = true` ("may miss") and the default score of 0.5, and on the owner's
photos they raise alarms where the labels say there is nothing (name badge on 10 of 137 photos).
F53 measures them and gives each a word list and a `min_score`, or takes it off the default list:

1. **No measured thing is lost to F53.** It raises a score above F21's rule only while every photo
   the kind finds at the floor (0.40) is still found, less a margin. Missing a thing is worse than
   a false alarm (F21), and F22 hides what the check finds, so this protects hiding as well.
2. **False alarms fall as far as that allows**, and the report says how many remain.
3. **The plate is not touched:** its word, its `min_score` of 0.40 and its findings stay.

## Measured on 2026-11-22

F21 stored the findings of all 21 words down to SAM 3's floor of 0.40 for its 134 labelled photos
(`results.jsonl`, outside the repo); none is labelled with any of the seven. Photos with an alarm:

| Kind (word that fired) | at 0.40 | at 0.50 (today) | at 0.70 | at 0.85 | Highest score |
|---|---|---|---|---|---|
| address (street sign) | 11 | 9 | 8 | 6 | 0.94 |
| badge (name tag) | 13 | 10 | 7 | 2 | 0.86 |
| screen (phone screen) | 5 | 3 | 0 | 0 | 0.69 |
| document (document, whiteboard, shipping label) | 5 | 3 | 1 | 0 | 0.82 |
| code (QR code) | 4 | 4 | 1 | 1 | 0.91 |
| card (ID card, credit card) | 2 | 1 | 1 | 0 | 0.76 |
| key | 0 | 0 | 0 | 0 | |
| any of the seven | 30 | 21 | 15 | 9 | |

- **Small, and where real things would be.** Median box 0.06 % of the frame (name tag) and 0.04 %
  (street sign). On 6 of the 10 badge photos every badge area lies on a person; 6 of the 9 address
  photos are the phone photos of cars; most phone-screen areas lie on people; 2 of the 4 QR codes
  lie inside a number plate.
- **So some may be true.** 119 of the 134 labels were ticked by a session from 1280-pixel previews
  (F21), too small to read a badge or a street sign. They count as false alarms only once the
  owner has looked at full size (T3).
- **F21's rule alone makes it worse.** Its lowest score with at most 1 in 10 photos alarmed is 0.40
  for name tag (13 of 134): three more badge photos. Seven kinds at 1 in 10 is most of a shoot.
- **A short probe** (SAM 3, the 21 photos with an alarm at 0.50, 26 words): the stored scores are
  still exact (largest difference 0.001). A word swap does not clear badge: "name badge" fires on 9
  of name tag's 10 photos. "street name sign" fires on 2 of the 9 street-sign photos. "smartphone"
  (5, all photos of people) and "paper" (4) fire more than today's words. "barcode" found an area
  the stored results hide under "QR code": two words of a kind on one area are merged.

## The data, and who looks at it

Nothing leaves this computer. The owner ticks the labels on the local labelling page; agents read
only numbers (`results.jsonl`, `report.md`), never a photo, preview or crop of this set.

- **In the repo:** the 110 landscape JPEGs hold one trail sign (F21). Not used: easy negatives
  lower the alarm share without helping on people and streets, where the alarms are.
- **The 134 labelled photos:** the negatives. The owner confirms every label (T3), the disputed
  photos first, with full-size crops of the findings.
- **New: at least 20 photos per kind that hold the thing**, added to the same folder (the script
  resumes). Each is also a negative for the kinds it does not hold.
  - *Staged:* the owner's own things, 20 per kind: 6 close (over a tenth of the frame), 8 at middle
    distance, 6 small in the background of a photo of something else; phone and camera, daylight
    and indoor light; dummy content where sensitive (an expired card, a made-up name).
  - *From the library:* photos the owner picks by looking, not by the check: a set the check picked
    would only measure what it already finds.
- **What a tick means**, stated on the page ("readable" at full size): *address* a house number,
  or a sign naming a street or a place; *screen* switched on and facing the camera; *document*
  paper with readable text (letter, envelope, parcel label, receipt, whiteboard with writing);
  *card* a bank, ID, membership or travel card, ticket or boarding pass, front side; *badge* a name
  badge or lanyard card; *key* a door or car key whose cut shows; *code* a scannable barcode or QR.

## Design

### 1. The threshold rule (`score` in `scripts/eval_reveal.py`)

Per kind and measurement, on the grid 0.40, 0.45, ... 0.95, `hits(t)` and `alarms(t)` in photos:

1. **Words.** Each word is measured on its own. While some word adds no photo with the thing (the
   kind's other words find all of its photos at 0.40), drop the one whose removal leaves the fewest
   alarms at the resulting `min_score`, the later one in the file on a tie. No photo is lost.
2. **F21's threshold** `t21` = the lowest `t` with `alarms(t) <= 0.10 * without`. None: off the
   default list, as in F21.
3. **The free range.** `keep` = the highest `t` with `hits(t) == hits(0.40)`;
   `cap = max(0.40, keep - MARGIN)`, `MARGIN = 0.05`, because the next real thing may score a
   little under the weakest one measured. The range runs from `t21` to `max(t21, cap)`.
4. **min_score** = the lowest `t` in the range with `alarms(t) <= max(TARGET * without, the fewest
   alarms in the range)`, `TARGET = 0.02`: up to 2 in 100 photos without the thing, or as close as
   the range allows, and not a step higher than helps.
5. **Verdicts, as F21:** found on under 7 in 10 → `weak`; plate under 9 in 10 → STOP; under
   `MIN_WITH = 10` photos with the thing → not measured, and the kind stays as today.

So `min_score >= t21`: against F21's rule no hit is lost and no alarm added. On today's data the
rule leaves plate, people, face and child where they are and moves writing from 0.40 to 0.55 (10
of 10 still found, alarms 10 → 7 of 124).

### 2. `scripts/eval_reveal.py`

- `run --config FILE` measures the words of FILE (default: the packaged config), so candidate words
  never touch `configs/reveal.toml`.
- Each word is measured as its own kind (`_flat` builds one `Kind` per word, with the kind's name),
  so `results.jsonl` keeps every word's areas; `score` counts only the words of the config it is
  given, so trimming words and scoring again needs no model.
- Each entry records its `words`; an entry that lacks a current word is measured again. The
  segmenter is wrapped in `DiskMaskCache` under `OUT/.autoedit-masks/sam3` (a run's layout): only
  new words and one image pass cost time. T4 copies F21's check-run cache there once: it holds the
  21 words of the 137 photos and still matches under the project's virtual environment (4 of 4
  tried; not under the system Python, whose numpy and Pillow differ).
- `label --labels CSV` starts the kind ticks from that file, not "looked at": only what the owner
  confirms counts. `label --results JSONL` puts first the photos where findings and ticks disagree.
  For a found kind not ticked: up to `MAX_CROPS = 6` crops of its largest areas from the full-size
  photo (the box grown 3 times, at least 160 pixels, long edge at most `CROP_EDGE = 480`, in
  `OUT/crops/`), captioned with the kind's label, never the score. For a ticked kind not found: a
  line saying so. The page states what a tick means (above; a constant in the script).
- The report adds per kind the curve (hits and alarms at each grid value), the words dropped, the
  alarms through each word, `t21`, `cap` and `min_score`; and overall the photos with an alarm of
  any kind, and of the seven, at the recommended scores.

### 3. `scripts/data/reveal_candidates.toml` (new)

The packaged `reveal.toml` plus ten words, 31 in all (the limit is 40): address *street name sign,
door number*; screen *laptop*; document *receipt*; card *boarding pass, bank card*; badge *name
badge, lanyard, ID badge*; key *house key*. On the probe's photos each fired on 0 to 2 except "name
badge" (9; measured anyway, it may find badges "name tag" misses); "house key" was not probed.

## Files

| File | Change |
|---|---|
| `scripts/eval_reveal.py` | sections 1 and 2 |
| `scripts/data/reveal_candidates.toml` | new, section 3 |
| `tests/test_eval_reveal.py` | tests 1 to 7; two old expectations change (below) |
| `src/autoedit/configs/reveal.toml` | T5, values only: `words`, `min_score`, `weak`, `off` of the seven kinds from the report, the numbers in a comment ("measured 2026-..: found 19 of 20, alarms 3 of 260"); no new key, no `reveal.py` change, `[kind.plate]` untouched |
| `docs/FEATURE_PLAN.md`, `CHANGELOG.md`, F21's spec, this file | the F53 row, the decisions-table line on the seven kinds, a pointer from F21's threshold rule, the result |

Config keys: none new. Script constants: `TARGET`, `MARGIN`, `MIN_WITH`, `MAX_CROPS`, `CROP_EDGE`;
`MAX_FALSE_ALARM = 0.10` stays.

## Tasks

Models and effort as [AGENT_ROUTING.md](../AGENT_ROUTING.md) sets them. T1 and T2 share a file.
`FOLDER` holds the photos; `EVAL` holds the set's `labels.csv`, `results.jsonl` and pages.

| Task | Who | What | Proves itself with |
|---|---|---|---|
| T1 | Builder (Sonnet, medium) | section 1 and the report lines; tests 1 to 4 and 7; the two changed expectations | `PYTHONPATH=src python3 -m pytest tests/test_eval_reveal.py -q` |
| T2 | Builder (Sonnet, medium), after T1 | section 2 (run, label, crops), section 3; tests 5 and 6 | the same, then `PYTHONPATH=src python tests/run_all.py` |
| Check | Checker (Opus, high) | T1 and T2 against this spec, before the owner spends time on labels | findings, most severe first |
| T3 | The owner | the new photos into `FOLDER`; `PYTHONPATH=src python3 scripts/eval_reveal.py label FOLDER --out EVAL --labels EVAL/labels.csv --results EVAL/results.jsonl`; every photo ticked and looked at, disputed ones first; the downloaded `labels.csv` moved into `EVAL` | the page's counter: all looked at |
| T4 | The session, in the background, one SAM 3 job | `PYTHONPATH=src .venv/bin/python scripts/eval_reveal.py run FOLDER --labels EVAL/labels.csv --config scripts/data/reveal_candidates.toml`; the owner checks the new photos' disputed findings on a fresh `label --results` page; `run` again (no model time); counts into "Result", no file names | `EVAL/report.md` |
| T5 | Tweaker (Haiku, low) | `configs/reveal.toml` from the report, and the docs, as the Files table says | `PYTHONPATH=src python3 -m pytest tests/test_reveal.py -q` (the config loads); `git diff` has no `[kind.plate]` hunk |

Model time in T4, at F36's speeds: a cached photo needs an image pass and the 10 new words (about
6 s), a new photo all 31 (about 13 s), plus decoding: about an hour for 134 + 150 photos.

## Tests (`tests/test_eval_reveal.py`, no model)

| # | Test | Asserts |
|---|---|---|
| 1 | `test_free_raise_cuts_alarms` | 20 photos found at 0.9; 3 of 100 without at 0.5: F21 gives 0.40, F53 0.55 with no alarm and 20 hits |
| 2 | `test_cap_keeps_every_hit` | the weakest hit at 0.6; 5 of 100 alarms at 0.8: `min_score` 0.40, every hit kept |
| 3 | `test_never_below_f21` | a kind whose F21 threshold already loses a hit keeps exactly F21's threshold |
| 4 | `test_words_dropped_one_at_a_time` | two words that find the same photos: one stays; a word with only alarms goes; hits unchanged |
| 5 | `test_each_word_keeps_its_areas` | two words of one kind on one area give two entries; an entry with fewer words is measured again, one with the same words is not (a fake segmenter counts calls) |
| 6 | `test_label_prefill_and_disputed` | ticks from the CSV, "looked at" empty; the disputed photo first, its crop file written; no score on the page; the "ticked, not found" line; no absolute path, no `http` |
| 7 | `test_union_line` | the report counts the photos with an alarm of any kind, and of the seven, at the recommended scores |

Changed: `test_score_picks_the_lowest_passing_threshold` now expects 0.65 (was 0.55: the free range
reaches no alarm); `test_score_words_that_add_nothing` keeps one of two words with the same photos.

## Acceptance checks (the session runs these)

1. Both suites green; the stub run of the definition of done completes; F53's diff touches no file
   under `src/` but `configs/reveal.toml`.
2. `run` with the packaged config after T5 reproduces the report: a verdict for every kind; the
   plate keeps its word and 0.40 and finds every plate photo it found before.
3. On the 134 photos, counted with the owner's labels, fewer photos carry an alarm of the seven
   kinds with the new `reveal.toml` than with today's; both numbers go into "Result".
4. `autoedit check` with SAM 3 on the new photos: per kind, the photos with findings match the
   report's hits plus alarms within one photo. The owner reads the page.

## Owner decisions (before T3)

1. **Which kinds get photos, and your time:** about 15 minutes to confirm the 134 labels, plus 20
   photos per kind. A kind with under 10 photos stays as today ("may miss", 0.5); one you do not
   need can get `off = true`, and the page then no longer says it looked for it.
2. **The target:** 2 in 100 photos without the thing (`TARGET`), or another number.
3. **A kind still well over the target after the free raise** (badge, if real badges score as low
   as its alarms): keep it with its alarms, as the rule does, or `off = true`.
4. **The rule for the five measured kinds:** on today's data it changes only writing (0.40 →
   0.55). Default: no, T5 edits the seven only.

## Not in F53

- **Per-instance scores** (each area its own score, not the word's best) change the segmenter
  interface and the mask cache, and how many boxes a photo shows and F22 hides, not which photos
  alarm. In the probe, 15 of 47 street-sign instances score under 0.50 on their own.
- **Dropping a finding inside a plate finding:** 2 of 134 photos (a QR code inside a plate).
- **A badge only where a person is:** on 6 of the 10 badge photos every area is on a person already.
- **A "worth a look" tier under `min_score`:** only if decision 3 keeps a kind with many alarms.
- **Box-level hits:** a hit stays a photo (F21), so a finding in the wrong place on a photo with the
  thing counts as found; staged photos with one thing in the frame keep that rare.

## Result

Built (2026-11-22, Builder): T1 and T2 (script, `scripts/data/reveal_candidates.toml`, tests 1 to 7 in
`tests/test_eval_reveal.py`). Waiting for the Check, then T3 (owner labels), T4 (SAM 3 run), T5.
Open: Check, T3 to T5.
