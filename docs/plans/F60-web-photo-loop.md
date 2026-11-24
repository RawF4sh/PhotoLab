# F60 · The web photo loop

The first evaluation closed on 2026-11-24. The implementation record below reports RAW coverage, the results for three landscape styles and the galleries awaiting the owner’s vote. It is the test for [F59](F59-local-styles.md) and the standing form of
release check 3 in [F58](F58-editor-sidebar-and-cr2.md).

## What it is

A loop that a session runs again and again:

1. take photos from the web, from sources whose licences allow it,
2. put them through AutoEdit the way a person would,
3. find what fails, or what looks worse than a photographer's edit,
4. change one thing,
5. prove the change helped and broke nothing,
6. write it down, and go round again.

The owner's blind vote decides what ships. The loop's own verdicts stand in for that vote
between votes.

It grows out of the first improvement loop ([IMPROVEMENT_LOOP_PROMPT.md](../IMPROVEMENT_LOOP_PROMPT.md),
11 iterations in November 2026) and keeps its ledger, its keep rule, its blind sheets and its
gates. Four things are new.

| | First loop | This loop |
| --- | --- | --- |
| Photos | The owner's mountain landscapes | Many kinds, from the web, with the licence of each on record |
| Aim | Fix what a sheet shows | Also come close to what a photographer did with the same frame |
| Judge | The session itself, blind | A Checker, blind. The session never judges its own candidate. |
| Scope | The rules | Three tracks: it opens, it looks right, it works in the app |

## Where the photos come from

Read on 2026-11-24. The fetch tool keeps each source's terms, as they stood on the day of the
first fetch, in the corpus folder.

| Source | What it has | Licence, as read | Used for |
| --- | --- | --- | --- |
| raw.pixls.us | RAW files straight from cameras, about one per camera and mode. A mirror of the full set was 2,804 files and 68 GB. | New files are public domain (CC0). Some older ones are CC BY-NC-SA. Its "masterset" holds only the CC0 files. | Track O: does every camera's file open. The masterset only. |
| Wikimedia Commons | Finished photos of every kind, with author and licence per file | Per file. Taken: CC0, public domain, CC BY, CC BY-SA. A file marked non-free is refused. | Track L: photos of each kind, and finished examples for style targets |
| MIT-Adobe FiveK | 5,000 RAW files, each retouched by five trained retouchers, labelled by indoors or outdoors, time of day, light and subject. About 50 GB; single files can be fetched. | "For research purposes", and not "directed toward commercial advantage". Two licence files, one per file list. | Track L: the one large set of real before-and-after pairs. Measuring only. Its retouchers adjusted the tone of the whole photo, "akin to a postcard", so it shows the size of a move and little shaping inside the frame. |
| HDR+ burst set | 3,640 bursts | CC BY-SA | Later, for merge frames (F33) |
| The owner's own | The three examples of 2026-11-24, own photos, a photographer's RAW files with their finished edits (F57) | The owner's | Briefs, pairs, the portrait style |

Not fetched by a script:

| Source | Why not |
| --- | --- |
| Unsplash | Its API terms require showing the photo from its own address and reporting each download, its help pages restrict use for machine learning, and its licence forbids compiling a similar service. |
| Pexels | Its terms were not read. Treated like Unsplash until they are. |
| Signature Edits and similar free RAW packs | Two descriptions of its terms disagree. Its own terms page decides. |
| Flickr | Needs a key, and the licence differs per photo |
| PPR10K, RAISE | Non-commercial research only |
| Any web page with a before and after | The copyright is unknown |

The owner may put such files into `curated-web/owner/` by hand, as briefs for private use. They
never leave the computer.

Rules for every source:

- The licence, the author and the page of each file go into the manifest. A file without a
  licence the list above takes is not fetched.
- Nothing fetched is ever committed. Committed files hold counts and measured numbers, never a
  photo, a file name from the web, or an author.
- Numbers that ship in a style target ([F59](F59-local-styles.md), S1) come only from CC0,
  public-domain, CC BY and CC BY-SA photos and the owner's own. A research-only set measures; it
  does not feed a target.

## Where things live

Both folders are already ignored by git (`curated-*/`, `out-*/`).

```
curated-web/
  manifest.csv      id, track, kind, role, pair, file, source, page, licence, author, sha256, bytes, fetched, query
  SOURCES.md        each source's terms on the day of the first fetch, with links
  opens/            Track O: RAW samples by maker and model, odd JPEG, PNG, TIFF, HEIC, AVIF
  looks/<kind>/     Track L: photos of one kind
  pairs/<id>/       a before and its finished after
  owner/            the owner's briefs and photos
out-webloop/
  LEDGER.md         the record; a new session resumes from its last row
  panel-<kind>.txt  holdout-<kind>.txt  crops-<kind>.txt
  iter-NN/          a candidate's config, sheets, verdicts, key
  opens/            coverage.csv and the findings of Track O
  app/              screenshots of Track A
  blind/<kind>/     the gallery for the owner's vote
```

Kinds, to start: `blue_hour`, `sunset`, `moody`, `portrait`, and `control` (ordinary daylight,
where the right edit is small). Per kind: 16 photos to tune on, 12 held back, and 30 or more
finished photos for the target.

Held-back photos are chosen once, by author and day, so that no shoot is on both sides. They are
not opened until a round ends.

## The three tracks

### Track O: it opens

Every fetched file must open, edit and export, or fail with a message that names the reason.

- **Sample:** up to three files per maker and format from the CC0 masterset, the owner's own
  camera models first, about 150 files. Then about 40 odd files from Commons: CMYK and
  progressive JPEG, 16-bit PNG, every EXIF orientation, an unusual colour profile, a panorama
  above 100 megapixels, HEIC, AVIF.
- **Run:** `autoedit run curated-web/opens out-webloop/opens/run --backend stub --strict`, then
  a report per file: opened or failed and why, size, orientation against the embedded preview,
  brightness against the camera's own picture, seconds.
- **In the editor**, once F58's M1 exists: 20 of the files through **Open photo** in a browser.
- **Passes when** no file crashes the run, none comes out turned or squeezed, and every failure
  names its reason.
- **A fix** of one file with its test is made in the loop. Anything larger goes on the ledger's
  "found" list and into `FEATURE_PLAN.md` as a proposal. A test never depends on a fetched file.

### Track L: it looks right

One round tunes one style on one kind of photo. An iteration:

1. **Hypothesis.** One change, in one sentence, tied to something a sheet or a number showed.
2. **Change.** In a copied config folder, `out-webloop/iter-NN/configs`. Code only when TOML
   cannot say it, behind a key, with a test. A rejected idea never touches `src/`.
3. **Render** the kind's panel and the control panel through the real pipeline with SAM 3, for
   the current best and the candidate.
4. **Measure.**
   - The gates: `tone_report.py --render --fail-on-regression` with the style on, the native
     flags, and how many proposals are safe as rendered.
   - On pairs, the distance from the photographer's result: brightness in 36 cells, colour
     strength, hue, and brightness per region.
   - `facts.txt`, which says which side is darker, more saturated or warmer, never which is the
     candidate.
5. **Look, blind.** Sheets of Original | A | B | Reference, at most four rows each, and a sheet
   of 100 % crops of the risky places: lamps, mask edges, skin, smooth sky, lifted shadows. A
   Checker writes Better, Same or Worse per photo with a reason in the rubric's terms, then the
   key is read.
6. **Keep** only if all of these hold. Otherwise the iteration folder goes to `rejected/`.
   - Better outnumbers Worse, and no photo is clearly worse.
   - The gates pass, with no new native flag and no fewer safe proposals.
   - The control photos under Standard are byte-identical when only a style changed.
   - The median distance on pairs is not larger. Distance is evidence; nothing is kept on
     distance alone.
7. **Log** one row in `LEDGER.md`: iteration, hypothesis, change, the counts, the gates, kept
   or not, the sheets.
8. **After every three kept changes:** promote them into `src/autoedit/configs`, run the full
   tests, the tone gate on the 110 RAW files and the stub smoke run, and one pass of Track A.

A round stops after three iterations in a row with nothing kept, after twelve iterations, or
when a decision is the owner's.

**At the end of a round:**

- One sheet on the held-back photos, final against the round's start, reported as it is.
- A blind gallery of 24 held-back photos for the owner, built with `scripts/blindtest.py` and
  its two config folders, with the command that scores the vote.
- The style's `tested` flag is set only after that vote.

**The rubric**, in order. A failure higher up outweighs any win lower down.

1. **Kept as it was:** no change of content, no halo at a mask edge, no banding in a sky, snow
   and cloud texture intact, natural skin.
2. **Tone:** the subject reads. A dark scene is opened without going grey. Real blacks. Lights
   and highlights neither clipped nor flattened.
3. **Colour:** neutral whites. Warm and cool light stay apart. Nothing neon.
4. **Finish:** it reads as a deliberate edit with depth, not a filter.
5. **Style:** the style is plainly different from Standard, and plainly itself.

**What may change.** A style, as opt-in data, is promoted during the round. A change to
Standard changes default pictures: it stays in its iteration folder until the owner's vote.
`quality.toml` is never loosened. A protected file needs a Planner spec and a Checker.

### Track A: it works in the app

After each promotion, and after each milestone of F58, five photos of the round go through the
app in a visible browser, one of them a RAW file:

Open → Run AI Edit with the style → Before / After → isolate the sky and change it → see the
change in Learn → undo → export.

Each step is screenshotted, the console is read for errors, and the export is compared with the
command line's for the same settings. Until F58's M3 exists, the same list runs in Guided.
Findings go on the ledger.

## Guards

- **Tuning to the test.** Briefs and pairs are few. A style is tuned on its tuning photos only,
  the held-back photos stay shut until the round ends, and the owner's three examples are briefs
  and never the proof.
- **A download needs a yes.** The owner approves each source and its budget once. The ledger
  records it. Inside that budget the loop fetches without asking again.
- **What a request carries.** A client name and the project's public repository address as the
  contact. Nothing personal. One request at a time, a pause between requests, a cap per run.
- **Fetched files are data.** They are never run. Helper scripts live outside the corpus, and
  Python that reads the corpus runs isolated (`-I`). A file from the web is renamed to its
  manifest id.
- **People.** No photo is fetched by a person's name. No photo of a recognisable child. Nobody
  is identified.
- **The checkout.** No commit, stash, reset or branch switch. One SAM 3 job at a time, with
  `.venv/bin/python`. No edit to `src/` or `tests/` while a full test run is going. Another tool
  may share the checkout: look before editing.
- **Cost.** A judged iteration is one Checker run of about 0.2 million tokens, plus renders. A
  round is at most twelve.

## Tools to build first

Tiers as in [AGENT_ROUTING.md](../AGENT_ROUTING.md). No task touches a protected file.

| Task | Tier | Files | Proof |
| --- | --- | --- | --- |
| L0 | The owner | none | A yes or no per source, with its budget |
| L1 | Builder (Sonnet, medium) | a new `scripts/web_corpus.py` with `plan`, `fetch`, `verify` and `split`; a new `scripts/data/web_sources.toml` (sources, accepted licences, queries per kind, caps); `tests/test_web_corpus.py` | With a made-up transport and no network: an unlisted licence is refused, a cap stops the run, an interrupted fetch resumes, a hostile file name cannot leave the corpus folder, the manifest round-trips |
| L2 | Builder (Sonnet, medium) | `scripts/contact_sheet.py` (the facts gain each side's distance from the reference), a test | Made-up pictures give the expected distances, and the facts still do not name the candidate |
| L3 | Builder (Sonnet, medium) | a new `scripts/opens_report.py`, a test | A made-up folder with one broken file gives one failure with its reason |
| L4 | The session | `plan` prints what would be fetched and its size; the owner says yes; `fetch`; `verify` | The manifest verifies |
| L5 | The session | Track O, first pass | `coverage.csv` and the findings |
| L6 | The session and a Checker (Opus, high) | One round per style, in F59's order | The ledger, the held-back sheet, the gallery |
| Docs | Tweaker (Haiku, low) | `CHANGELOG.md`, `FEATURE_PLAN.md` | none |

L1 to L3 need nothing from F58 or F59. Track O can start as soon as L4 is done.

## Decisions for the owner

| Decision | Proposed |
| --- | --- |
| raw.pixls.us, CC0 masterset, up to 5 GB | Yes |
| Wikimedia Commons, up to 600 files and 6 GB | Yes |
| FiveK, a sample of up to 150 pairs and 12 GB | Yes, for measuring only. Its licence says research, and not for commercial advantage. If AutoEdit is ever sold, drop it or ask. |
| Photos of people | The portrait kind waits for the owner's own photos or a photographer's pairs |
| The contact in the client name | The project's public repository address |
| Disk for the corpus | 25 GB in all |
| Who judges | A Checker, blind. The owner's vote decides. |
| May style targets be made from Commons photos? | Yes: a target is a handful of measured numbers, and the photos stay on this computer |

## The prompt

Start a fresh session in this checkout, not a worktree, and say "Read
`docs/plans/F60-web-photo-loop.md` and run the loop." To keep it going, run it under `/loop`.

---

You are running AutoEdit's web photo loop. Read `CLAUDE.md`, `docs/AGENT_ROUTING.md` and
`docs/plans/F60-web-photo-loop.md` first. Their rules apply throughout.

1. **Resume.** If `out-webloop/LEDGER.md` exists, read it and continue from its last row. Never
   start over. If it does not exist, create it with the frozen panels, the baseline config
   folder and the commands you will use.
2. **Check the ground.** `PYTHONPATH=src python tests/run_all.py` is green. `autoedit doctor`
   reports SAM 3 from `.venv/bin/python`. `git status` shows what is uncommitted: change nothing
   you did not make, and never commit.
3. **Photos.** Fetch only from the sources the ledger records a yes for, and only inside their
   budgets. If no yes is recorded, print the `plan` list and stop for the owner.
4. **Track O first**, once: run it, report the coverage, fix what is one file and a test, list
   the rest.
5. **Then Track L**, one round per style in the order of `docs/plans/F59-local-styles.md`. Follow
   the eight steps of an iteration exactly. A Checker judges the blind sheets. You read the key
   only after its verdicts are written.
6. **Track A** after each promotion.
7. **Stop** after three iterations in a row with nothing kept, after twelve in a round, or when
   a decision is the owner's. Then finish the round: the held-back sheet, the blind gallery, the
   score command.
8. **Report:** what changed, what got better or worse with the sheet paths, what is unresolved,
   what needs the owner, and one commit and push block that you do not run.

## Acceptance

- The tools pass their tests with no network, and the full suite stays green.
- A first fetch, approved by the owner, leaves a manifest in which every file has a licence the
  list takes, an author and a checksum that verifies.
- Track O's first pass ends with a coverage table and a list of findings.
- One round of Track L ends with a ledger, a held-back sheet and a gallery the owner can vote in.
- No fetched file and no author's name is in any commit.

## Left out, and what would bring each back

| Left out | Bring it back when |
| --- | --- |
| Fetching from sites that need a key or forbid scripts | The owner gets written permission or a key and asks |
| Training a model on the pairs | Ranges and rules fail where the right move depends on the photo |
| A scheduled run every night | A round has run by hand twice and its cost is known |
| Votes from other people | The website exists |

## Implementation record, 2026-11-24

The owner approved the three sources and byte ceilings above. The portrait group requires owner-supplied photographs. The first acquisition reserved 30 target photographs, 16 tuning photographs and 24 held-back photographs for each of the four other kinds. Roles are disjoint by photographer across all four kinds. The hashes and exact split are local. FiveK pairs are restricted to measuring the two renders against the expert edit.

Use the repository interpreter with isolated imports. Acquisition is resumable. The discovery plan fetches metadata only. Selection reserves individual IDs before downloading the photographed scenes.

```sh
.venv/bin/python -I scripts/web_corpus.py plan --sources rawpixls,commons,fivek
.venv/bin/python -I scripts/web_corpus.py select --holdout 24
.venv/bin/python -I scripts/web_corpus.py fetch --approved rawpixls,commons,fivek --selected
.venv/bin/python -I scripts/web_corpus.py verify
.venv/bin/python -I scripts/web_corpus.py split
.venv/bin/python -I scripts/opens_report.py curated-web/opens out-webloop/opens --resume
```

The ledger, camera coverage CSV, terms snapshots, split lists and rendered images stay in the ignored evaluation folders. The browser opened each of the first 20 camera originals and displayed its decoded pixels. On four JPEGs and a Canon RAW, offline command-line, Automatic and Photo editor exports matched byte-for-byte. In 15 separately logged, locally re-encoded format checks, the editor exported the eight EXIF orientations, progressive and profiled CMYK JPEG, Display P3, 16-bit grayscale PNG and RGB TIFF, AVIF and HEIC. HEIC first reported its optional decoder dependency. Installing the existing HEIF extra enabled the same file to export.

The blinded contact sheet adds distances to the retoucher's image, in 36 brightness cells and per detected region, with hue and color-strength errors. A resolution mismatch from different aspect ratios is recorded. Vote files require a reason and complete coverage before `scripts/web_round.py score` reads the key. It checks native flags, Standard output hashes, safety counts, the tone gate and median paired brightness error. `scripts/web_round.py gallery` reuses the two rendered versions for the exact frozen holdout and builds the offline owner vote. The private ledger records the specific hypothesis, evidence and outstanding decisions.

The first verified acquisition totals 491 files and 8.455 GB. It includes 150 CC0 camera originals, 323 eligible Commons images and nine FiveK before/expert pairs. Every stored file verified against its recorded size and SHA-256. The RAW tests led to picker support for CRW, ERF, FFF, MOS, IIQ and RWL after verifying that the decoder could develop their pixels. ARRI's sampled format remains unsupported by the installed LibRaw decoder.

On the first 16-photo Blue hour tuning panel, the independent blind checker preferred the measured target on five photographs, tied eight and preferred round start on three. Neither side gained a new native warning. Both passed the tone gate, 12 proposals were safe as rendered versus 11 at round start, and all 16 Standard controls matched byte-for-byte. Median absolute brightness error across the nine expert pairs fell from 0.623 to 0.537 EV. This passes the round's keep rule and falls short of F59's 0.35 EV release criterion. The owner preference vote remains separate. One of the 24 frozen Blue hour holdout originals was a 189.7-megapixel JPEG above Pillow's normal safety limit. Its display-size comparisons use the same 1600-pixel system-decoded image in every arm. The evaluation records the native-open limitation and the original hash.

**Opening and export.** The all-file audit covered 190 licensed originals. The editor exported 186 at their full developed dimensions. Four formats/camera combinations failed explicitly with this LibRaw build (ARRI, ImBack, and two Paralenz samples). The strict CLI enumerated 189 recognized extensions, reused all 186 verified results and returned status 1 for the three unsupported files with a recognized RAW extension. The all-file audit also captured the unrecognized ARRI extension. The orientation check found 143 matches, 36 images without a camera preview, five with a different reference framing, and two possible rotations. Inspection of both flagged cases found upright exports alongside sideways embedded thumbnails. Separate tests identified camera RAW data in seven TIFF containers and developed two monochrome scanner DNGs with all 16 bits available. Two TIFF-contained Canons opened in the browser at a 1600-pixel preview and exported at 2496 × 1662 and 4082 × 2718.

**Blue hour on unseen photographs.** Two independent checkers recorded preferences across the frozen 24-photo holdout, with a separate random assignment for each comparison. The measured settings won five, tied nine and lost ten against the starting settings. Against Standard, the counts were also five, nine and ten. On the preregistered first ten against Standard, the candidate won two, tied three and lost five. A high-key snow scene developed a pale outline around the ridge and visible spots in the sky in both comparisons. Across eleven photographer groups, three favoured the measured settings, four preferred the comparison and four tied. The measured settings remain private, and Blue hour keeps its preview status. Holdout votes inform the release decision only.

The local owner gallery at `out-webloop/blind/blue_hour/index.html` compares the measured edit with Standard. The second gallery at `out-webloop/blind/blue_hour_vs_start/index.html` compares it with round start. Both include all 24 pictures and a credits table. To score the first gallery after voting, download the results and run the following from the checkout.

```sh
cd /Users/david/Documents/Projects/AutoEdit
cp "$HOME/Downloads/results.csv" out-webloop/blind/blue_hour/results.csv
.venv/bin/python -I scripts/blindtest.py --score out-webloop/blind/blue_hour/results.csv --pair autoedit baseline
```

For a future corpus, group bursts and visually similar compositions from the same viewpoint before drawing the target, tuning and holdout lists. Reserve distinct views within each panel. Retain the existing photographer separation and report preference by photographer as well as by frame. The present split has disjoint photographers across roles, but includes multiple frames of some viewpoints within individual panels. Its original assignments and scores stay frozen.

**Sunset glow tuning.** Three consecutive private trials failed the keep rule. The first won three photographs, tied five and lost eight, including two clear losses. Safe proposals fell from 14 to 12 out of 16, and median paired brightness error rose from 0.623 to 0.642 EV. A second trial tied on all 16, with 14 of the resulting style JPEGs byte-identical. A third also tied on all 16, with 13 byte-identical; its paired error rose slightly. All three trials preserved the 16 Standard controls. The starting Sunset settings remain the final settings for this round.

**Sunset glow on unseen photographs.** The final and starting configurations produced byte-identical JPEGs on all 24. In the separate blind comparison with Standard, Sunset won two, tied 19 and lost three across 14 photographers. All ten preregistered judgements were ties. There were no clear losses or additional clipping/review warnings. Median error on the nine paired expert edits remained 0.623 EV. The blind owner gallery compares Sunset with Standard at `out-webloop/blind/sunset/index.html`; the final-versus-start record is at `out-webloop/blind/sunset_vs_start/index.html`. The existing preview designation stays in place.

**Moody landscape.** Three private tuning changes were kept in sequence. The measured sky target reduced median brightness error on the nine expert pairs from 0.792 to 0.756 EV. Reducing the sky burn to 0.25 EV brought it to 0.607 EV and increased safe proposals from eight to eleven of the native 16. Lowering far-shore exposure from +1.0 to +0.2 EV removed a conspicuous bright waterline in three views from the same photographer. Thirteen tied and paired error held at 0.607 EV. Each trial preserved the 16 Standard controls.

On the 24 held-back photographs, the final Moody settings beat their starting settings on 21, tied three and lost none. Against Standard, Moody won three and lost 21. The preregistered first ten against Standard produced two wins and eight losses. Three clear losses in the full set showed repeated blotches in two skies and a pale rim along a hazy ridge in a third. Two new shadow-clipping review flags appeared in the full set, including one in the first ten. The three tuning changes remain in the preview preset; the preference, paired-distance and review criteria for a tested style were missed. Owner comparisons are at `out-webloop/blind/moody/index.html` and `out-webloop/blind/moody_vs_start/index.html`.

All six galleries were opened in Chrome. The browser decoded both images for each of 24 captures in each gallery (288 displayed images total) with zero reported browser exceptions. Each gallery has its own blinded slot hashes, local image credits and download identifier. Owner votes remain outstanding. The same scoring command shown above works for each gallery when its matching `results.csv` is saved alongside its own `manifest.csv`.

**App check after the Moody changes.** A separate evaluation profile enabled the packaged Moody settings in the normal picker. Chrome exercised Run AI Edit, the comparison slider, Isolate, Learn, undo and full-size export on four tuning photos and two Canon CR2s. With the repeatable stub detector, all six exports matched byte-for-byte across Photo editor, Automatic edit and the CLI. The private record is `out-webloop/app/moody/parity.json`. The release picker offers Standard.

The same six photos also went through a visible Chrome window with real SAM 3 masks. The model produced the masks offline on the Apple GPU. The repeat run required an exact match of source proxy and concept to reuse a mask. Five photos had a sky to adjust. On the indoor Canon CR2 the user action **Find an object** located a hair dryer instead. Its exposure change appeared in Learn, and undo removed both the change and the newly requested object. The browser exported all six JPEGs byte-for-byte identically to the standalone CLI. The 42 step screenshots, onscreen window capture, source checksums and results are in `out-webloop/app/moody-sam/visible/`.
