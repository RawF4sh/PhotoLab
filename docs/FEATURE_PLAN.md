# AutoEdit feature plan

Engine first, interface later. This plan lists the engine, CLI and API features to build before the
Studio UI is rebuilt and before the desktop app and website work starts. It was written on
2026-11-10 against the code as it stands after the improvement loop (see `CHANGELOG.md`).

Features are built one at a time, in the order below, through the loop described in
[AGENT_ROUTING.md](AGENT_ROUTING.md). Each feature starts with a spec in `docs/plans/`. Update the
status column here whenever a feature moves.

Extended on 2026-11-16 with phases 8 to 11 (F21 to F35), at the owner's request: a check for
what a photo gives away, film looks and filters, a style that grows from the owner's edits, and
more kinds of photography. The kinds have their own file, [GENRES.md](GENRES.md).

F36 was added and built the same day, ahead of F21 at the owner's word ("F36 first"): F21 asks
SAM 3 for about 20 more things per photo, and F36 makes each of them a light pass.

F37 was added on 2026-11-16 and is built ahead of F21's open tasks at the owner's word ("Yes,
F37 first"), because those tasks wait for the measurement on the owner's photos. Its spec is
[F37](plans/F37-point-and-tell.md).

## Order and status

| ID | Feature | Size | Status |
|---|---|---|---|
| F1 | Export keeps metadata and colour profile | S | done |
| F2 | Region instructions | L | done |
| F3 | Per-photo adjustments | M | done |
| F4 | Throughput | M | done |
| F5 | Black and white point | M | done, off by default (`--levels`): safe, small effect |
| F6 | Highlights | M | done, off by default (`--highlight-hold`); the RAW highlight option was measured and removed |
| F7 | Noise | M | done, off by default (`--denoise`) |
| F8 | Defaults | S | done: the mechanisms exist, no default changed; three decisions wait for the owner |
| F9 | Shoot consistency v2 | M | done, off by default (`--consistency bursts`) |
| F10 | Export presets | S | done |
| F11 | User config layer | M | done |
| F12 | Taste calibration | L | done as a tool; the owner's own calibration and blind test are open |
| F13 | Settings schema | S | done |
| F14 | Model manager | M | done; a real download was not made in the unattended run |
| F15 | API contract | S | done |
| F16 | More input formats | S | done for AVIF; HEIC needs one check with `pillow-heif` installed |
| F17 | LLM path refresh | S | done as code; one real call with the owner's key is open |
| F18 | Shaped light: gradients, ovals, a sun glow | M | done; the automatic glow is off by default |
| F19 | Follow-up edits | M | done |
| F20 | Free-text instructions (more words, a local model) | M | done |
| F21 | Reveal check: what a photo gives away | L | done, off by default (`--reveal-check`, `autoedit check`, "Check before sharing" in Studio); measured on 134 of the owner's photos (plates 14 of 14); seven kinds unmeasured and marked "may miss"; tiles not built; spec [F21](plans/F21-reveal-check.md) |
| F22 | Hide on request: cover, mosaic, blur | M | built, off by default (`--hide plate,face`, `--hide-method`, `hide` and `hide_method` settings, a Hide control in the app); the owner said yes to the content rule on 2026-11-22; a real photo at full size is still to be looked at; spec and result [F22](plans/F22-hide-on-request.md) |
| F23 | Grain | M | done, off by default (`--grain N`, a look's `grain`, "add grain"); the owner looks at the crops in `out-features/F23/` |
| F24 | Black and white, and film looks | M | done, off by default (`--look bw_classic`, `bw_red_filter`, `bw_soft`, `film_warm`, `film_cool`, `film_faded`, `cross_process`, `instant`); the owner looks at the sheets in `out-features/F24/` |
| F25 | LUT files | S | done, off by default (`--lut FILE.cube`, `--lut-amount N`; `autoedit looks lut NAME -o FILE.cube`); the owner tries a LUT of their own |
| F26 | Surprise me | S | done (`--look surprise --seed N`, `"surprise": true` on `POST /variants`, a Studio button) |
| F27 | Style from your edits | M | done, off by default (`history = true` under `[run]`, `autoedit style suggest`); the blind check and the Studio card are open, and no real history exists yet |
| F28 | Look files: export, import, preview card | M | done (`autoedit looks export`, `import`, `card`; CLI only); which sample photos a card shows by default waits for the owner |
| F29 | Teaching mode | M | done as engine and API (`steps` and `try` on `POST /preview`); no page until the test UI; "your style" waits for a real history (F27) |
| F30 | Genre packs | M | done for the mechanism and two packs (`--genre`, `autoedit genres list`, own packs in the profile); both packs untested by the spec's bar (one photo pulled back each); every other kind waits for photos; spec [F30](plans/F30-genre-packs.md) |
| F31 | Night sky tools | M | done, off by default (`--night-sky`, `--genre astro`, untested: 6 night photos, the bar is 20); the `astro` look has no `preserve_exposure` (an open owner question); the owner's 20 night photos and blind sheet are open; spec [F31](plans/F31-night-sky.md) |
| F32 | Geometry: straighten and crop | L | done, off by default (`--straighten`, `--aspect W:H`, `aspect` in an export preset); upright is not built: it needs photos of buildings; the owner's yes on cropping is open; spec [F32](plans/F32-geometry.md) |
| F33 | Merge frames: brackets, stacks, focus series, trails | L | done as a tool (`autoedit merge FOLDER [-o DIR]`, modes `average`, `brackets`, `focus`, `trails`); tested on made-up frames and on brackets made from real RAW files; a real bracket, night series and focus series are open, the corpora hold none; spec [F33](plans/F33-merge-frames.md) |
| F34 | Exact colour and clean backgrounds | M | done, off by default (`--neutral X,Y`, `--neutral-from FILE`, `--backdrop`, `--consistency-wb`, `--colour-report`); checked on made-up pictures only, a real set is open; spec [F34](plans/F34-exact-colour.md) |
| F35 | Pick the keepers | M | done as a tool (`autoedit pick FOLDER [-o DIR] [--copy]`); the check against the owner's own keepers on a shoot of 30 bursts is open |
| F36 | Speed and frameworks: SAM 3 reads each photo once | M | done (built before F21); a lock for SAM 3 jobs was built, measured and dropped; the worker default waits for the owner |
| F37 | Point and tell: circle a thing, say what to change, see how it was done | L | done (built before F21's open tasks; T9, the preview of a large photo, added on 2026-11-17); five things found on the way: three done on 2026-11-18 (disk cap, Studio limits from the server, the guide section), two wait for the owner (filling holes, the "bigger" message) |
| F38 | AutoEdit Lab: a minimal test UI | M | done (`/lab`, `web/static/lab.html`): a second page beside Studio for the features of the unattended run; Studio and the API unchanged; spec [F38](plans/F38-test-ui.md) |
| F39 | Masks that reach the frame edge | S | done, off by default (`--snap-edges`); spec [F39](plans/F39-edge-masks.md) |
| F40 | An exit code for failed photos | S | done (`run --strict`); the default stays 0 until the owner says otherwise; spec [F40](plans/F40-failure-exit-code.md) |
| F41 | Search by what is in the photo, and keywords | M | done (`autoedit find`, `autoedit keywords`, keywords as `.xmp` beside the export); spec [F41](plans/F41-search-and-keywords.md) |
| F42 | Edit declaration | M | done (`autoedit declare`); no signing (C2PA needs a certificate); merged frames not declared; spec [F42](plans/F42-edit-declaration.md) |
| F43 | Timelapse flicker | M | done, off by default (`--consistency timelapse`); tested on made-up frames, a real timelapse is open; spec [F43](plans/F43-timelapse-flicker.md) |
| F44 | RAW highlight recovery | M | done, off by default (`--raw-recover`); tone gate 0 and 0, and the highlight rules hold recovered highlights without adding exposure headroom. F54 restores source highlight detail: 0066 now keeps its full edit; 0103 still receives the 75% safety blend in the integrated run. Blind gallery of 48 photos, off vs on, built for the owner. spec [F44](plans/F44-raw-highlight-recovery.md), follow-up [F54](plans/F54-editing-workspace.md) |
| F45 | Dehaze by distance | S | not built: measured as covered by the existing dehaze and region rules; spec [F45](plans/F45-distance-dehaze.md) |
| F46 | Lens fixes: colour fringes | S | done, off by default (`--lens-ca`); vignette correction left out, a per-lens median over a shoot is an owner decision; spec [F46](plans/F46-lens-fixes.md) |
| F47 | Copy edits to similar photos | M | done (`autoedit sync PHOTO`); spec [F47](plans/F47-copy-edits.md) |
| F48 | Watermark and gallery | M | done (watermark keys in an export preset, off unless set; `autoedit gallery`); spec [F48](plans/F48-watermark-and-gallery.md) |
| F49 | A minimal interface, on the desktop and in the browser (replaces Studio and the Lab) | L | done (U0–U11; installers U12 wait for the owner's signing decision); spec [UI-minimal](plans/UI-minimal.md) |
| F54 | A saved editing workspace | L | done: saved controls, projects/history, quick previews, visual looks/reference, comparison/zoom, folder/review navigation, selective sync and recovered highlight detail. 1,185 tests and both 110-photo RAW tone gates pass; spec [F54](plans/F54-editing-workspace.md) |
| F50 | Sky detail: a highlight roll-off that keeps faint cloud | M | done, off by default (`--highlight-detail`); with `raw_recover` 0066 and 0103 keep their full edit; tone gate 0 and 0; replaces the unreleased `recover_detail` step (owner, 2026-11-22); default by blind vote; spec [F50](plans/F50-sky-highlight-pull.md) |
| F51 | Faster previews: half-size analysis, quicker WebP, half-size thumbnails | M | done; `fast_analysis` off by default (tone gate 0 and 0, no photo newly pulled back, 0051 moves 1.7 levels); WebP and thumbnails always on (owner's yes on thumbnails); spec [F51](plans/F51-fast-analysis-decode.md) |
| F52 | Lens vignette profiles from the camera's own JPEG | M | done, off by default (`autoedit lens-profile`, `--lens-vignette`, as strong as the camera); 6 buckets cover 87 of 110 corpus photos; tone gate 0 and 0, one photo (0095) newly pulled back to 75 %, one (0065) no longer; spec [F52](plans/F52-lens-vignette-profiles.md) |
| F53 | Reveal check precision for the seven unmeasured kinds | M | measuring tools built (`scripts/eval_reveal.py`); waits for the owner's photos and labels (about 20 per kind) before any threshold changes; spec [F53](plans/F53-reveal-check-precision.md) |
| F55 | Guided photo workflow and Gemini suggestions | L | done: object selection/sliders/brushes, framing/native detail, portable presets, first-use guidance and reviewable Gemini adjustments. Reopening after an app restart has a guided recovery step. 1,233 tests and stub/browser checks pass; both earlier 110-photo RAW gates pass. Natural-clean candidate stays opt-in pending owner blind preference; spec [F55](plans/F55-guided-photo-workflow.md) |
| F56 | Editable changes, adjustable suggestions and recipes | L | done: Changes list, individually selectable suggestions, portable recipes and folder planning. Browser and export checks pass, and an independent check on 2026-11-23 found and fixed three gaps (see the spec). Open, both the owner's steps: the live Gemini assessment and the [LightCraft measurements](plans/F56-lightcraft-check.md); spec [F56](plans/F56-connected-editor-and-lightcraft.md), first and larger version [F56 full vision](plans/F56-full-vision.md) |
| F57 | AI editing modes with Gemini, and photographer references | M | built on 2026-11-24 and replaced in the interface by F58 the same day: no page asks Gemini from Automatic any more, and **Ask Gemini** in the editor is the optional path. The server side was checked on 2026-11-24 for what leaves the computer; spec [F57](plans/F57-ai-editing-modes.md) |
| F58 | Original-first photo editor and RAW import | L | built on 2026-11-24 on the fallback of its decision D1 (AutoEdit's photo screen is the editor, LightCraft opens as Fine-tune), then checked by a second session with eight fixes. Automatic edit and Photo editor share the local edit. The editor has the split comparison, Isolate, Learn and optional Ask Gemini. Fine-tune restores LightCraft's settings from the project and exports at full size through AutoEdit. Open, the owner's: the RAW brightness default (D6), whether Automatic evens a folder's brightness (D12), and LightCraft's remove and fill tools (D4); spec and results [F58](plans/F58-editor-sidebar-and-cr2.md), [probe](plans/F58-probe.md) |
| F59 | Photographer-directed local styles | L | engine built on 2026-11-24 and checked by a second session with six fixes; **no specialized style is released**. Each landscape candidate ran one round and missed its bars. Against Standard on 24 held-back photos, better / same / worse was 5 / 9 / 10 for Blue hour, 2 / 19 / 3 for Sunset glow and 3 / 0 / 21 for Moody landscape. Standard is unchanged. Next: round two in [F61](plans/F61-style-round-two.md). Open: finished portraits for Natural portrait; spec and results [F59](plans/F59-local-styles.md) |
| F60 | Web photo evaluation loop | L | tools built and first run done on 2026-11-24, checked by a second session with eleven fixes: 491 files fetched inside the approved budgets, 186 of 190 format samples opened and exported at full size, one round per landscape style, six blind galleries for the owner's vote. FiveK is used for measuring only. One photographer is on both sides of the frozen split; round two does not use the group that crossed; spec and results [F60](plans/F60-web-photo-loop.md) |
| F61 | Style round two: unedited originals and four engine changes | L | engine changes and loop tools (T1 to T8) built and checked on 2026-11-24. The FiveK originals were fetched the same day (101 pairs, 6.1 GB) and the **Blue hour round ran on 16 night originals: no release, stopped for an owner decision**, held-back photos still shut. Without its sky range the candidate won 10 of the 10 photos that differ; the owner kept that change, and it is in the packaged candidate with a fix of the lamp rule. Against Standard it stands at 1 / 8 / 7 on night frames and 4 / 9 / 3 on dusk frames, where it wins on twilight with buildings. The first round tuned and judged the styles on finished photos, where the right edit is close to none, so round two tests on RAW originals and starts from the owner's examples again. Built: a style aims inside its range, every searched control has a limit, a move may work inside several regions, a band is placed on a waterline only, FiveK pairs by time of day, a split by shoot, and the release bar as a command. On the owner's three examples the start values of round two bring the distance from the photographer's result from 0.52 to 0.42, from 0.70 to 0.37 and from 0.86 to 0.80 stops. On the owner's answer to O8 Blue hour ran a round on twilight frames (the last 49 FiveK pairs fetched): on 24 held-back photos it beats Standard on 11 of 15 decided and is closer to the retoucher, and misses the bar by one clear loss (a lit fountain blown to white). In the owner's blind vote (O9, 2026-11-25) all 24 pairs looked the same, so Blue hour is not released. Next: Moody waits for the owner's own RAW files; spec and results [F61](plans/F61-style-round-two.md) |
| F62 | Auto: edit each photo toward a goal for its scene | L | spec (2026-11-25), nothing built: finer landscape scenes, goals measured from forum before-and-after pairs, Auto at run time with wider limits, and release per scene by the owner's blind vote. Replaces the three named landscape styles; Standard is renamed Simple. Prototype on branch `prototype/auto-goal-fit`; spec [F62](plans/F62-auto.md) |

Size: S is a few files and one short spec; M is a new op or module with its tests; L is a new
subsystem. Status values: planned, spec, building, checking, done. Two more exist since the
unattended run of 2026-11-11 ([UNATTENDED_RUN.md](UNATTENDED_RUN.md)): `done, off by default` (built
behind a setting that waits for the owner's blind vote) and `blocked` (with the reason).

## Before anything is built

These are the owner's steps, not agent work.

1. Cast the blind vote on the improvement loop (`out-loop/blind/index.html`) and score it.
2. Commit the improvement-loop change set, so features start from a clean tree. Done on
   2026-11-10.
3. Answer the open decisions at the end of this file when their feature comes up.

## Where the engine stands

Checked against the code on 2026-11-10.

| Area | Exists today | Gap |
|---|---|---|
| Region edits | SAM 3 masks for any named concept; rule edits for eight region types (skin, subject, sky, mountain, snow, foliage, grass, water) and their aliases | A concept without a rule block is segmented and then never edited, in rules mode and in LLM mode |
| Free-text intent | About 30 mood terms mapped to bounded global nudges, offline | Cannot target a region ("brighten the jacket") |
| Tone | Exposure with highlight knee, black-pinned shadow lift, tone, contrast, curve, dehaze, clarity | No black or white point; blown skies end near 232 of 255; RAW output about 1.3 EV under the camera render |
| Colour | Region-aware auto white balance, OKLab palettes, targeted HSL, split-tone | White balance is not aligned across a shoot |
| Noise | Nothing | No denoise op; the shadow lift raises noise with the shadows (not yet measured) |
| Batch | Resume, per-photo failure isolation, brightness-only consistency (±0.15 EV) | One photo at a time, 22 s for a 22 MP photo |
| Output | JPEG, 16-bit TIFF, sidecar JSON with reasons; since F1 the source's EXIF and an sRGB profile | No resize; XMP and IPTC are not copied; a 16-bit TIFF carries the basic tags only |
| Per-photo control | Nothing | One photo cannot be adjusted without changing the run settings for all |
| Personal style | Reference matching, review labels, dataset builder, recipe predictor, variants, aesthetic critic | A custom look means copying a whole config file or folder; votes do not feed back into a look |
| Inputs | JPEG, PNG, TIFF, WebP, BMP, camera RAW; ICC converted to sRGB | No HEIC or AVIF; no lens corrections |
| Models | SAM 3 through Hugging Face (gated), stub fallback, `autoedit doctor` | Manual download and login; the default backend is the stub; the LLM default model id predates the current Haiku |

## Phase 1: directed edits

### F1. Export keeps metadata and colour profile (S)

The first feature, chosen small on purpose: it also proves the agent loop end to end.

- **Why:** an edited JPEG carries no EXIF at all, so capture time, camera, lens and copyright are
  lost and edited photos stop sorting by time in other tools. It also carries no ICC profile.
- **Build:** read the source EXIF when the export is written and write it back in `save_image`
  for JPEG and TIFF, with orientation reset to normal (the pixels are already rotated) and the
  embedded thumbnail dropped. Embed an sRGB profile. Add a run setting
  `metadata = "keep" | "shareable" | "none"`. Spec: [plans/F1-export-metadata.md](plans/F1-export-metadata.md).
- **Done when:** tests show that capture time and camera survive, orientation is normal, GPS and
  the serial numbers are gone under `shareable`, no tag is written under `none`, and the sRGB
  profile is present. The stub smoke run passes.
- **Owner:** the default `shareable` removes GPS and the camera and lens serial numbers. Decided
  on 2026-11-10 (location) and 2026-11-11 (serial numbers).
- **Dropped from the first sketch:** a setting for relative sidecar paths. Sidecars hold the file
  name only; the sketch's claim that they hold absolute paths was wrong.

### F2. Region instructions (L)

- **Why:** the product's promise is open-vocabulary region editing. Today `rules.py` skips every
  concept that has no rule block, and `decide/llm_decider.py` ignores regions the rules did not
  produce, so a custom concept gets a mask and no edit.
- **Build:**
  - An instruction vocabulary as data in a new `configs/instructions.toml`: verbs (brighten,
    darken, warm, cool, saturate, mute, soften, deepen) mapped to op deltas, and amount words
    (slightly, a little, much) mapped to a scale.
  - A local parser, text to a list of `(concept, {op: delta})`, clause by clause, with no LLM.
  - Concepts named in an instruction join the run's concept list for segmentation.
  - The deltas are added to the region's settings after `decide` and before region policies and
    gates, so every existing safety layer still applies.
  - The sidecar notes report each instruction as applied, clamped, or not found in this photo.
  - CLI `--edit "..."` and an `edit` field in the API.
- **Spec must settle:** how an instructed concept is arbitrated against the region that contains
  it (a jacket inside `subject`), since no pixel may be edited by two regions.
- **Done when:** parser golden tests pass; a stub pipeline test covers one instruction on `sky`
  and one on an absent concept; a SAM 3 contact sheet on six panel photos shows three
  instructions; identity is still a no-op; the tone gate is unchanged when no instruction is given.

### F3. Per-photo adjustments (M)

- **Why:** after a batch, one photo cannot be nudged without changing the run for all. Every
  future slider needs this.
- **Build:** the same adjustment structure as F2 (target `global` or a concept, op, delta), stored
  per photo in the sidecar. `autoedit adjust <photo-or-sidecar> --set sky.exposure=-0.3`
  re-renders from the sidecar and the mask cache without segmenting again. `--resume` keeps
  adjusted photos.
- **Done when:** a re-render with no adjustment is pixel-identical to the batch output; an
  adjustment changes only its target (pixels outside the feather are unchanged); a sidecar
  round-trip test passes.

## Phase 2: speed

### F4. Throughput (M)

- **Why:** a 22 MP photo takes 22 s, of which 19 s is the apply stage, and a batch runs one photo
  at a time. Profile of 2026-11-10: `apply_ops` runs eight times over the full frame; about 5 s
  goes to sRGB and linear conversions and 4.6 s to array reductions; there is no single hot spot.
  Faster renders also shorten every later feature's verification.
- **Build:** worker processes for the apply stage across photos, with segmentation kept serial
  (one SAM 3 job at a time). Remove redundant work in the op chain with pixel-identical output. No
  GPU port in this feature; report what one would have to cover.
- **Done when:** a before and after timing table on three photos; the measured maximum pixel
  difference against pre-change renders (goal 0); measured batch speed-up per worker; tests and
  tone gate green. Targets: 10 s or less for one 22 MP photo, and at least three times the batch
  throughput on the development machine.

### F36. Speed and frameworks (M, added 2026-11-16)

- **Why:** after F4 the slow stage was segmentation. SAM 3 ran its whole model once per concept,
  so the image encoder ran seven times on one photo. The owner also asked whether any part
  should move to another framework.
- **Build:** the image encoder runs once per photo and each concept is one light pass on its
  output. The last photo's encoder output stays in memory, so a thing named later on the same
  photo skips the encoder. Packages that nothing imports leave the extras. Spec and result:
  [F36](plans/F36-speed-and-frameworks.md).
- **Result:** segmentation of a photo with seven concepts takes 4.1 s instead of 16.6 s, with
  every mask identical. A follow-up edit that names a new thing takes 0.5 s of segmentation
  instead of about 2.2 s (the encoder plus one pass). No part needs another framework: the
  measured numbers are in the spec.
- **Measured and dropped:** a lock that lets only one SAM 3 job run at a time. Two or three
  jobs at once already finish in the time they would take one after the other, so the lock
  gains nothing. The owner dropped it on 2026-11-16.
- **Owner:** one decision is open, in the table at the end of this file.

## Phase 3: image quality

Every feature in this phase changes default pixels. Each needs the tone gate, a blind contact
sheet and the owner's vote before it becomes a default.

### F5. Black and white point (M)

- **Why:** washed frames with no highlight headroom keep a grey floor, because nothing sets a true
  black.
- **Build:** a levels-style op with pinned ends that sets black and white points from luminance
  percentiles, with soft knees, at a fixed place in the frozen op order. A rule in `rules.toml`
  drives it from existing percentile stats.
- **Done when:** math and golden tests pass; tone gate at 0 and 0; the washed panel photos
  improve on the contact sheet.

### F6. Highlights (M)

- **Why:** blown skies render light grey, about 232 of 255. A clipped area should stay white with
  a smooth shoulder. The RAW decode uses LibRaw's default highlight handling; its other modes are
  untested here.
- **Build:** measure first on the blown-sky holdout photos. Then evaluate LibRaw's highlight modes
  behind a `raw_highlight` setting, and add a roll-off rule that keeps clipped regions white.
- **Done when:** tone gate at 0 and 0; the added-clipping gate is unchanged; a contact sheet of
  the blown-sky photos.

### F7. Noise (M, measure first)

- **Why:** no denoise op exists, and the shadow lift can raise dark regions by up to 1.5 EV, which
  raises their noise too. This has not been measured.
- **Build:** step one is a measurement on the highest-ISO corpus photos (flat dark patches, before
  and after the edit). Only if noise is visible at 100 %: a denoise op that scales with ISO and
  with the lift, chroma first, placed before sharpening.
- **Done when:** the measurement is reported. If the op is built: a math test and 100 % crops of
  high-ISO photos.

### F8. Defaults (S, owner)

Three config decisions, each a small change with a gate run once the owner has decided:

- RAW development default. `raw_mode = "none"` leaves RAW output about 1.3 EV under the camera
  render.
- Whether a moody look may opt out of the darkness gate. Inside the gate, `cinematic_preserved`
  differs from `natural` only on photos the rules lift.
- Default backend `sam3` when the model is installed.

## Phase 4: batch polish

### F9. Shoot consistency v2 (M, measure first)

- **Why:** grouping uses brightness only and nudges exposure by ±0.15 EV. `consistency.py` itself
  names time, EXIF and white-balance clustering as the next step.
- **Build:** measure white-balance and exposure drift inside time-adjacent groups on the corpus
  first. Then group by capture-time gaps plus brightness and align white balance and exposure
  inside a group, bounded. The sidecar records group and bias.
- **Done when:** drift numbers before and after on two shoots; tests; a contact sheet of one group.

### F10. Export presets (S)

- **Build:** long-edge resize, WebP and PNG output, a filename template and an output subfolder.
  No extra sharpening by default.
- **Done when:** one test per option; a stub smoke run with a preset.

## Phase 5: personal style

### F11. User config layer (M)

- **Why:** there is no key-by-key overlay. A custom look means copying and maintaining a whole
  config file or folder.
- **Build:** a profile folder whose TOML files are merged key by key over the packaged ones
  (looks, styles, palettes, intents, instructions). `autoedit looks list|save|delete`. Validation
  on load with clear errors.
- **Done when:** merge tests pass (override one key, add one look, reject a bad file) and the
  packaged defaults are unchanged when no profile exists.

### F12. Taste calibration (L)

- **Why:** the improvement loop tuned the look by hand from blind sheets. The same loop can run
  for any user on their own photos.
- **Build:** `autoedit calibrate <folder>` picks a stratified sample, renders A/B pairs that differ
  along one look dimension (lift, depth, colour, warmth), reuses the blind gallery and its
  scoring, fits the look values from the votes and writes a user look into the profile from F11.
- **Done when:** a test recovers the known preference of a simulated voter, and one real run
  produces a look that wins a fresh blind test against `natural`.

## Phase 6: ready for an interface

### F13. Settings schema (S)

- **Build:** one description of every user-facing setting (label, type, range, default, group),
  generated from `RunConfig` and the looks file. `GET /api/settings/schema` and
  `autoedit settings`. A snapshot test. The future interface renders from it; Studio hardcodes
  four styles today.

### F14. Model manager (M)

- **Why:** SAM 3 needs a Hugging Face account, an accepted licence and a 3.7 GB download, all by
  hand today.
- **Build:** `autoedit models status|download|remove` with progress and resume. The licence step
  stays with the user. Document the plug point behind `segment/base.py::Segmenter` and evaluate
  one freely distributable segmenter.
- **Owner:** accept the gated flow, or fund the alternative segmenter. Read Meta's licence before
  any commercial use.

### F15. API contract (S)

- **Build:** a version prefix, an OpenAPI snapshot test, uniform error codes and progress events
  for jobs.

### F16. More input formats (S, check demand)

- **Build:** HEIC, HEIF and AVIF through an optional extra.

### F17. LLM path refresh (S)

- **Why:** the default model id `claude-haiku-4-5-20251001` predates the current Haiku, and the
  LLM can only refine regions the rules produced.
- **Build:** the current model id (read the Claude API reference first). Let the LLM translate a
  hard instruction into F2's structure. Sensible budget cap defaults.

## Phase 7: editing like a person (added 2026-11-15)

### F18. Shaped light (M)

- **Why:** editors also mask by shape and place, not only by object: a graduated filter at an
  angle, an oval, a warm glow where the sun comes in. AutoEdit placed three such moves on its
  own and none on request.
- **Build:** gradient and glow shapes, a light finder, places as instruction targets ("darken
  the top with a gradient", "add a warm glow where the sun is"), and an automatic sun glow
  behind `--light-shaping`, off by default. Spec: [F18](plans/F18-shaped-light.md).
- **Done when:** the instruction examples work on real photos; the tone gate with the setting
  on is at 0 and 0; a blind gallery for the owner's vote.

### F19. Follow-up edits (M)

- **Why:** after the edit, the owner wants to type what else should change and see it added on
  top, step by step, with undo.
- **Build:** a follow-up bar in Studio, a run-wide instruction field, per-photo follow-ups
  carried into the batch as photo-scope adjustments. Spec: [F19](plans/F19-follow-up-edits.md).
- **Done when:** the Studio walkthrough in the spec's acceptance checks passes in a browser.

### F20. Free-text instructions (M)

- **Why:** "make the sky have a bigger gradient for pink" was not understood: the instruction
  reader is a word list with no colours beyond warm and cool and no sizes.
- **Build:** colour and size words in `instructions.toml`; a small model on this computer
  (Ollama, `qwen3.5:9b`) that reads what the list misses into the same bounded form, for the
  run-wide text and for "Edit this version". Spec: [F20](plans/F20-free-text-instructions.md).
- **Done when:** 80 % of a set of 40 written phrasings come back right, no negation becomes an
  edit, and the owner's sentence works in Studio.

## Phases 8 to 11: where the engine stands (added 2026-11-16)

The owner asked for four things: a check for what a photo gives away, with a blur for it; a look
that grows out of the owner's own edits and can be shared; grain, old-film looks and filters; and
more kinds of photography, night sky first. The kinds of photography have their own file,
[GENRES.md](GENRES.md): 32 kinds, each with its plan. Checked against the code on 2026-11-16.

| Area | Exists today | Gap |
|---|---|---|
| What a photo gives away | Exports drop the GPS position and the camera and lens serial numbers by default (`metadata = "shareable"`, F1) | Nothing looks at the picture itself; names the photographer entered (artist, copyright) stay; nothing reports what was found |
| Hiding things | Nothing | No blur, mosaic or cover |
| Grain and film looks | Looks, palettes, split-tone, mood words (matte, faded) | No grain op; black and white is saturation at zero, with no choice of how colours turn grey; no LUT files |
| Personal style | Blind-vote calibration (F12), looks saved by hand (F11), a recipe predictor that needs before/after pairs | The changes a user types after an edit (F19) are kept per photo and never looked at as a habit |
| Sharing a look | A look is a few numbers in a TOML file in the profile | No file to hand to someone else, no check of a file from someone else, no picture that shows what the look does |
| Teaching | The "What it did" report: every step with its reason | A step cannot be seen alone; the user cannot try first and compare; Studio has two sliders |
| Kinds of photography | Three scenes (people, landscape, other), three concept sets (generic, wedding, landscape), rules for eight region types | One real test set (mountain landscapes). No pack for any other kind |
| Geometry | Nothing | No rotate, straighten, crop or perspective |
| Several frames into one | Bursts are found and evened in brightness (F9) | Never merged: no brackets, no stacking, no focus series |
| Picking | A scorer (`critic.py`), a diverse-subset copier (`curate`), bursts (F9) | Nothing ranks the frames of one burst |

## Phase 8: check before sharing

The feature the owner asked for most. Order matters: the check first, measured, then the blur.

### F21. Reveal check (L, measure first)

- **Why:** a photo that is posted or delivered can give away more than it should: a number plate,
  a house number, a screen, a letter on a table, a name badge, a child's school crest, the GPS
  position inside the file. Phone apps exist that blur faces and number plates in one photo at a
  time (a web search on 2026-11-16 found several; their claims were not tested). None was found
  that checks a whole shoot inside the editing run, for things the photographer names, on the
  photographer's own computer. SAM 3 makes that possible here: it finds any named thing.
- **Build:**
  - `configs/reveal.toml`: what to look for, as data. Each entry has the words given to SAM 3, a
    minimum score, and a kind (`plate`, `address`, `writing`, `screen`, `document`, `card`,
    `badge`, `people`, `face`, `child`, `key`, `code`). First list: number plate; house number, street sign;
    computer, phone and TV screen; letter, document, whiteboard, parcel label; bank and ID card,
    ticket, boarding pass; name badge; face; child; key; barcode and QR code. The user adds own
    words in the profile (F11), for example a company logo.
  - `reveal.py`: measurement only, no pixel changes. Part one runs the segmenter with the reveal
    words on the proxy and keeps each finding (kind, box, score, share of the frame). Part two
    reads the file: GPS position, serial numbers, artist and owner name, capture time, and says
    what the chosen export will still carry.
  - The result goes into the sidecar (a `reveal` block) and the manifest. A page `reveal.html` in
    the output folder shows every photo with a finding, boxes drawn. The run summary gets one
    line ("14 of 212 photos have findings"). `autoedit check FOLDER` runs the check alone.
  - Studio: a "Check before sharing" panel that draws the findings on the preview.
  - Wording: the check never says "safe". It says what it looked for and what it found. It never
    works out who a person is (no face recognition). A face that is the subject of a portrait is
    listed apart from other people in the frame.
  - With the stub segmenter only the file part runs, and the report says so. It must never print
    an empty "nothing found" for a part that did not run.
  - Off by default (`--reveal-check`). The stage order is unchanged: the check is part of
    `segment` and `stats`.
  - Later option: reading the text in the picture (an address, an email, a phone number). That
    needs a local text reader the engine does not have.
- **Spec:** [F21](plans/F21-reveal-check.md).
- **Measure first:** a labelled set of at least 100 photos (at least 20 with plates, 20 with
  screens, 20 with documents or signs, 40 with nothing). Run SAM 3 on the reveal words and write
  down, per kind, how many were found and how many false alarms came up. If fewer than 9 of 10
  readable plates are found, stop and report: the feature then needs a second detector. Also
  measure the time the extra words add per photo.
- **Done when:** the found and false-alarm rates per kind are in the spec's result; the owner has
  read the report of one real shoot; with the check off the output is byte-identical; the stub
  run still completes.

### F22. Hide on request (M)

- **Why:** the owner wants what the check finds to be blurred.
- **Build:**
  - `--hide plate,screen` hides every finding of those kinds; in Studio a finding has a Hide
    button. Nothing is hidden unless the user asks.
  - Three methods, as data in `reveal.toml`. `cover` is the default: one flat fill in the average
    colour of the region's surroundings, so nothing under the region is copied into it. `mosaic` and `blur` look softer
    and keep a trace of what was there; the interface says so. A light blur or small blocks over
    text can be read back, so both scale with the size of the region.
  - The mask is grown by a margin and never feathered inward. Hiding is the last pixel step of
    `apply`, on the full-size image. The source file is never changed.
  - The sidecar lists every hidden region (kind, box, method), and "What it did" shows it. The
    exported file must hold no second copy of the picture from before the hiding. Today's export
    copies a fixed list of metadata tags and should carry no embedded preview; a test makes sure.
  - Nothing is generated: no copied texture, no rebuilt background, nothing that looks like
    picture content.
- **Owner decision (yes, 2026-11-22):** "AutoEdit never invents content: nothing is generated,
  replaced or filled in. Hiding a region is allowed when the user asks for it, and is always
  reported." What was built is in [F22](plans/F22-hide-on-request.md).
- **Done when:** at the hide step, two different pictures under the same mask give identical
  pixels with `cover` (a test: nothing under the region is copied into the fill; the region still
  counts toward the whole-photo exposure and colour measurement, so its average can shift the edit
  slightly); pixels outside the grown mask are
  byte-identical to a run without hiding; the exported file holds no embedded preview; a real
  photo is checked at full size; default output is byte-identical.

## Phase 9: film and filters

Small features with a visible result. The owner asked for grain, an older look and "some random
filter".

### F23. Grain (M)

- **Why:** film grain has been on the Later list since the first plan.
- **Build:** an op `grain` (amount 0 to 100) with `grain_size`, the last step of `ops.STEPS`,
  after `sharpen`, so it is never sharpened or cleaned away by `denoise`. Luma grain, strongest
  in the middle tones and fading toward black and white. The pattern is seeded from the photo's
  identity, so a rerun is byte-identical. Its size follows the image's long edge and it is added
  after the export resize, so a web export and a full-size export look alike. Looks can carry it;
  the instruction words "add grain", "more grain", "grainy" map to it.
- **Done when:** a math test (zero is identity; the mean stays within 0.5 of 255; same input,
  same output; more amount, more variance); the review gate does not report grain as noise or
  banding, with `quality.toml` untouched; a sheet of 100 % crops for the owner; default output is
  byte-identical.

### F24. Black and white, and film looks (M)

- **Why:** black and white today is saturation at zero. Editors choose how each colour turns grey
  (a red filter makes a blue sky dark). Old-film looks are faded blacks, soft highlights and a
  colour cast in shadows and highlights: most ops for that exist and need looks written as data.
- **Build:** an op `bw` (amount) with a weight per colour band (red, yellow, green, blue), in the
  colour step. Looks as data: `bw_classic`, `bw_red_filter`, `bw_soft`, `film_warm`, `film_cool`,
  `film_faded`, `cross_process`, `instant`. The names say what the look does; no film brand is
  named or claimed. A second op, `halation` (a warm glow round the brightest areas), only if the
  contact sheet shows the looks need it.
- **Done when:** a math test for `bw` (grey input is unchanged; zero is identity); a golden test
  per look; the tone gate at 0 and 0 per look, where a look that means to be dark uses the
  existing `preserve_exposure` field; a contact sheet of every look on 12 photos.

### F25. LUT files (S)

- **Why:** thousands of colour filters exist as `.cube` LUT files, free and sold, and a LUT is how
  a look travels to other programs, video editors included.
- **Build:** `--lut FILE.cube` with an op `lut` (amount 0 to 100) after the colour steps. A strict
  reader: text only, a size limit, known keywords only. `autoedit looks lut NAME -o FILE.cube`
  writes the whole-picture colour part of a look as a LUT; region edits cannot live in a LUT, and
  the file says so in a comment.
- **Done when:** an identity LUT is a no-op; a known LUT matches a reference within 1 of 255; a
  broken file is refused with a clear message; the review gate runs after the LUT as after any
  edit.

### F26. Surprise me (S)

- **Why:** a quick way to see looks the user would not have picked.
- **Build:** a Studio button and `--look surprise --seed N`: six looks from the library on the
  current photo, side by side (the variants machinery exists). The user picks one; the pick is a
  named look, so the batch can be repeated.
- **Done when:** the same seed gives the same six; the sidecar records the picked look by name.

## Phase 10: your own style

### F27. Style from your edits (M, measure first)

- **Why:** the owner wants a look that builds up over time out of the photos already edited.
  Today a personal look comes from blind votes (F12) or from saving one by hand (F11). What a user
  types after an edit (F19) is the clearest sign of taste, and it is not used.
- **Build:**
  1. History. On export, one line per photo into `history.jsonl` in the profile: the scene, the
     numbers the recipe predictor already uses (`recipe.features_from_stats`), the look, and the
     user's own changes per target. No pixels, no file names, no metadata. A setting turns it
     off; `autoedit history clear` deletes it. This step is cheap and can be built at any time.
  2. Suggestions. `autoedit style suggest` and a Studio card. Per scene and target it takes the
     median of the user's changes. A suggestion appears only when the same change was made on at
     least 12 photos from at least 3 runs, in the same direction on at least 70 % of them: "You
     made the sky darker on 14 of 17 landscapes. Add it to your look?" Yes writes a new named
     look into the profile. Nothing changes without a yes.
  3. Blind check. The old look against the new one on the user's own photos, with the voting page
     of F12.
  - Later: when the change depends on the photo (dark photos get more lift), train the recipe
    predictor on the history. It stays "not now" until there is evidence.
- **Done when:** a made-up history with one planted habit gives exactly that suggestion; a history
  without a habit gives none; a yes changes the look file and nothing else; nothing leaves the
  computer.

### F28. Look files (M)

- **Why:** people give away and sell presets. An AutoEdit look is data already. It needs a file
  that travels and a picture that shows what it does.
- **Build:** `autoedit looks export NAME` writes one file (`NAME.aelook`, TOML): the look's
  values, palette, grain, black-and-white weights, a format version, optional author and licence
  lines. Never photos, paths or history. `autoedit looks import FILE` checks it the way the
  profile layer checks a config: known keys only, finite numbers inside the limits, a size limit.
  A look file is data and can never run code. `autoedit looks card NAME` renders the look on
  sample photos as one before-and-after sheet (PNG and HTML) to show or sell it.
- **Owner decision:** which sample photos. The repo has two small test images. Either the owner
  picks a dozen of their own, or a licence-free set is downloaded (a download needs a yes).
- **Done when:** export then import gives the same look; a set of hostile files (unknown keys,
  huge numbers, not-a-number, 10 MB, names that look like paths) is refused, with tests; the card
  renders with the stub segmenter; a look from someone else stays inside the limits and the
  review gate.

### F29. Teaching mode (M)

- **Why:** the owner wants the tool to teach how to edit, toward the user's own style. The "What
  it did" report says what was done and why. Teaching needs three more things: see one step
  alone, try first and compare, and be told what went wrong in numbers.
- **Build** (in Studio; the server re-renders with the code of `adjust`):
  1. Step through. The edit replayed one step at a time in editing order, each with the picture
     before and after that step and three lines from `configs/lessons.toml`: what the tool does,
     when editors use it, what to watch for.
  2. Try first. The engine's edit is hidden. The user edits with typed instructions (F19, F20)
     and a small set of sliders for the basic tools, then compares: their values beside the
     engine's, plus what the existing checks measure (blown highlights, crushed shadows, skin
     colour shift, banding, colours pushed out of range). Facts, not taste: "your version clips
     3 % of the sky".
  3. Your style. A page from the history of F27: how the user's edits differ from the baseline
     per scene, with example photos.
  - No model is needed. Later option: the local model (F20) answers "why did you darken the sky?"
    from the sidecar's reasons only.
- **Done when:** in the browser, on a real photo, the last step's picture equals the full preview
  byte for byte; the compare table matches the sidecar; a test checks that every op has a lesson.

## Phase 11: more kinds of photography

The full list and the plan for each kind is in [GENRES.md](GENRES.md). The features here are the
mechanism and the new tools that several kinds need.

### F30. Genre packs (M)

- **Why:** each kind of photography has its own things to find, its usual moves and its limits.
  The engine has three scenes and three concept sets.
- **Build:** `configs/genres.toml`. A genre names a concept set, opt-in rule sets (the
  `[opt_in.NAME...]` mechanism exists), a default look, run settings (for example noise reduction
  on) and what the reveal check looks for (F21). `--genre NAME`, `autoedit genres list`, a Studio
  menu, own genres in the profile. A genre selects data; there is no code per genre. The engine
  may suggest a genre for a folder and never applies one unasked.
- **Done when:** `--genre landscape` gives the same bytes as today's `--preset landscape`; each
  new pack has a golden test and passes the tone gate at 0 and 0 on at least 20 photos of its
  kind. A pack without test photos stays `planned`.

### F31. Night sky tools (M)

- **Why:** the owner named astrophotography. A night-sky photo breaks what the engine assumes: the
  frame is meant to be dark, city light puts a colour cast and a brightness slope across the sky,
  and stars are small bright points that noise reduction eats and sharpening swells.
- **Build** (wide night landscapes: Milky Way, aurora, star trails; not telescope work with
  calibration frames, which Siril and PixInsight do):
  - `sky_gradient`: fits a smooth surface to the sky background inside the sky mask, stars left
    out, and subtracts it per channel. Removes the cast and the slope.
  - A sky rule that lifts faint detail and holds star cores; a new curve shape only if the
    existing `curve` op cannot do it.
  - `hot_pixels`: single stuck pixels replaced by the middle value of their neighbours.
  - Star protection: `denoise` and `sharpen` work less on star cores.
  - The `astro` pack, with a look that uses `preserve_exposure`.
  - With F33: stacking for noise, and star trails.
- **Done when:** math tests per op (zero is identity; on a made-up sky with a slope and stars the
  slope is gone to within 1 of 255 and each star keeps its brightness within 2 %); the tone gate's
  behaviour on frames that are meant to be dark is written down, `quality.toml` untouched; a blind
  sheet on at least 20 night-sky photos, which the owner has to supply.

### F32. Geometry (L)

- **Why:** the engine cannot rotate, crop or straighten. A sloping horizon and leaning walls are
  the first thing an editor fixes in landscapes, seascapes, buildings and rooms.
- **Build:** `level` (rotate to the horizon, found from the boundary between the sky, water and
  ground masks; at most 5 degrees), `upright` (leaning vertical lines made vertical, limited),
  `crop` to a shape for an export preset (`aspect = "4:5"`, placed on the subject mask). Decided
  in `decide`, applied in `apply` as one resampling of the full-size image after the colour steps
  and before `sharpen` and `grain`. Off by default; the sidecar gives the angle and how much of
  the frame was cut.
- **Owner decision:** straightening cuts a little off the edges. Proposed: allowed, because
  nothing is invented; the cut is limited and reported.
- **Later:** lens corrections from a lens database.
- **Done when:** a made-up tilted horizon is level to within 0.2 degrees; a level photo is left
  alone byte for byte; a made-up leaning grid is upright; a sheet of real photos.

### F33. Merge frames (L)

- **Why:** several kinds are shot as a set of frames meant to become one picture: exposure
  brackets (a room with bright windows, a sunset), repeats against noise (night sky, a dark
  stage), focus series (macro, products), star trails.
- **Build:** `autoedit merge FOLDER`. Groups come from the burst finder of F9 plus the exposure
  data. Modes: `average` (less noise), `brackets` (per pixel, the best-exposed frames blended),
  `focus` (per pixel, the sharpest frame), `trails` (per pixel, the brightest). First version:
  frames from a tripod, aligned by a small shift. Aligning a moving sky is a second task. The
  result is one 16-bit TIFF per group, with a record of the frames used, and that file then goes
  through the normal pipeline. The stage order is unchanged.
- **Content rule:** every pixel comes from the user's own frames of the same scene. Things that
  moved between frames leave ghosts; the first version flags such a group and does not repair it.
- **Done when:** made-up tests per mode (the average of N noisy frames cuts noise by the square
  root of N within 10 %; `brackets` leaves no clipped pixel where a frame has detail; `focus`
  picks the sharp frame); one real bracket set and one real night set; groups with movement are
  flagged.

### F34. Exact colour and clean backgrounds (M)

- **Why:** products, food, copies of artwork and school portraits need colour that is right and
  the same across the set, and a backdrop that is evenly white. That is a different job from
  "make it look good".
- **Build:** a neutral pick (`--neutral x,y`, or a click in Studio on something grey or white)
  that sets the white balance for one photo or the whole set. A backdrop rule: the background
  region evened out and taken to a set white, the product untouched. Shoot consistency (F9)
  extended from brightness to white balance. A report of the colour difference between frames of
  the same product.
- **Stays out:** cutting the product out onto a new background.
- **Done when:** the picked patch is neutral within 1 of 255; a made-up uneven backdrop is even
  within 2 of 255 while pixels outside the grown mask are byte-identical; on a real set the spread
  in white balance is at least halved.

### F35. Pick the keepers (M)

- **Why:** sports, wildlife, events and weddings are shot in bursts, and most of the time goes
  into choosing. The parts exist: bursts (F9), the scorer, `curate`.
- **Build:** `autoedit pick FOLDER`. Per burst, the frames ranked by the sharpness of the subject
  mask (not of the whole frame), exposure, and the scorer when a model is there. Output: a ranking
  file and a contact sheet with the pick marked; picks are copied or tagged. No source file is
  moved or deleted. Open eyes and smiles need a face model and are not in the first version.
- **Done when:** in made-up bursts with one sharp frame, that frame is always picked; on a real
  shoot of at least 30 bursts the owner's own pick is among the tool's top two in 8 of 10 bursts;
  a hash test shows no source file changed.

## Phase 12: one editor, and edits a photographer would keep (added 2026-11-24)

The owner asked for three things: the editor redesign of F58 planned through, the features that
take the result to the level of a person editing by hand, and a loop that takes photos from the
web and makes them come out well. The owner supplied three before-and-after examples. Measured
against them, the rules of that day made moves of about half the size, could not shape light
inside the frame, and clipped the lamps when asked to match (the numbers are in
[F59](plans/F59-local-styles.md)).

All three were planned, built and checked on 2026-11-24. The editor works. The loop works. No
specialized style has passed yet.

### F58. One editor (L): built and checked

- **Why:** the Photo editor kept its edits in the browser, exported past AutoEdit, and its
  **AI Edit** button asked Gemini. Isolate and Learn lived on another screen. Some Canon RAW files
  did not open.
- **Built:** the proving step measured LightCraft as too slow to sit behind **Run AI Edit**
  (4.70 s against 4 s), so AutoEdit's own photo screen is the editor, with a sidebar of Adjust, AI
  Edit, Isolate, Learn and Ask Gemini. LightCraft opens from Adjust as Fine-tune. The AutoEdit
  project is the record for both stages, every export goes through AutoEdit, and a RAW file opens
  through AutoEdit's decoder.
- **Checked:** a photo without manual changes exports byte for byte as the command line does.
  Eight things were fixed, among them a Hide setting that did not reach Fine-tune and a retouched
  spot that the edit declaration could miss.
- **Open:** three decisions of the owner (D4, D6, D12).

### F59. Local styles (L): engine built and checked, no style released

- **Why:** the local engine needs real settings for Blue hour, Sunset glow, Moody landscape and
  Natural portrait. Mood words barely move a picture, and stronger settings alone fail the checks.
- **Built:** a style carries a target, as ranges measured on finished photos, and a search pulls a
  photo toward it inside the clipping budgets. Light is shaped inside a region, with a band at a
  waterline and a new op `burn_ev` that darkens with black pinned. Lamps and lit windows hold
  while the frame is lifted. A style may declare that it means to be darker or richer; that skips
  only the matching relative check.
- **Result of the first rounds:** all three landscape candidates lost to Standard on held-back
  photos, Moody landscape clearly. They stay candidates, to be run by name only.
- **Done when:** per style, the loop's held-back photos and the owner's blind vote pass it.

### F60. The web photo loop (L): built, first run done, checked

- **Why:** every corpus in the repository was the owner's mountain landscapes. Genre packs, night
  sky, upright and the styles all waited for other photos.
- **Built:** a fetch tool with a manifest of licence and author per file, and a loop with three
  tracks. It opens: one RAW file per camera. It looks right: one round per style, blind sheets
  judged by a checker, distance from a retoucher's result on pairs. It works in the app: the same
  photos through the editor in a browser.
- **First run:** 491 files, 186 of 190 format samples exported, RAW picker support for six more
  formats, three rounds, six galleries for the owner. No fetched file is in any commit.

### F61. Style round two (L): tools built, Blue hour round run and stopped for the owner

- **Why:** the first round tuned and judged the styles on finished web photos. On those, Blue
  hour's exposure differs from Standard's by a median of 0.00 EV, and two of Moody's three kept
  changes made a move smaller. On the owner's own examples the candidates barely differed from
  Standard.
- **Built:** a style aims inside its range, every control its search moves has a limit, a move
  may work inside several regions, and a band is placed on a waterline only. The loop takes FiveK
  RAW originals by time of day, splits them by shoot, and answers the release bar with a command.
- **First sign:** on the owner's three examples the start values of round two bring the distance
  from the photographer's result from 0.52 to 0.42 (bridge), 0.70 to 0.37 (Dolomites lake) and
  0.86 to 0.80 stops (lake at sunset). Three photos the styles were drawn from: a sign, no proof.
- **Blue hour round (2026-11-24):** five iterations on 16 FiveK night originals, each judged blind
  by an independent checker. Taking out the sky's brightness range won 10 of the 10 photos that
  differ; the owner kept it although one photo gains a review mark that Standard gives too, and
  the keep rule now counts such a mark as not new. It is packaged, with a lamp rule that leaves a
  blown lit sign white. Against
  Standard the candidate is better on 1, the same on 8 and worse on 7 night photos: Standard
  already lifts a night frame as far as it bears. On 16 dusk photos it is 4, 9 and 3, winning
  where buildings fill the frame at twilight. The held-back photos were not opened.
- **Done when:** per style, the held-back originals and the owner's blind vote pass it.

### Next

1. Standard on night frames (O6 of F61): its review rule marks a black sky, and it lifts night
   shadows to a grey.
2. The round of Sunset glow, on its own dusk originals.
3. The owner's own RAW files of overcast and mist for Moody landscape (O2 of F61), and which
   finished photos have each look (O4).
5. Finished portraits from the owner or a photographer, for Natural portrait.

## Ideas, not planned yet (added 2026-11-16)

None has a spec. Whether people want them was not researched, except where it says so.

- **A record of what was edited.** The sidecar already proves that nothing was generated. Two
  uses: a one-page "edit declaration" for photo contests and news desks that limit editing, and
  Content Credentials (the C2PA standard) signed into the export. The second needs a signing
  certificate; how other programs show a self-made one has to be checked first. This fits the
  product rule better than any other idea here.
- **Search by what is in the photo.** The run already knows the regions of every photo. An index
  over the sidecars answers "all photos with snow and a lake". The same list can be written as
  keywords into the file for stock agencies, and as a first draft of an image description for
  websites.
- **Timelapse flicker.** A folder of frames evened in brightness from frame to frame. The
  machinery is the burst evening of F9.
- **Learn from a photo you admire.** Give a reference photo and get, in plain words, how it
  differs from yours (the reference matching exists). It belongs with teaching mode.
- **Speak instead of type.** A microphone button on the instruction box. Speech must be turned
  into text on this computer, which needs a local speech model.
- **Brighter-than-white output.** Phones and browsers now show photos with real highlights
  (gain-map JPEG and AVIF). The engine works in standard range; this is a large change.
- **Print help.** Sharpening for the print size, a warning for colours a printer cannot make,
  a border.
- **A delivery page.** The contact-sheet pages exist; a client gallery with picks is close.
- **Release check.** After the reveal check: "people found in 40 photos; is a signed release on
  file?" A checklist, nothing more.
- **Needs an owner decision under the content rule, proposed to stay out:** background blur
  (fake shallow focus), skin smoothing and blemish removal, light leaks, dust, frames and
  watermarks laid over the photo, repairing dust and scratches on scans.

## Later, or not planned

- **Later:** lens corrections for RAW, Display P3 output, a watch folder. (Film grain moved into
  the plan as F23.)
- **Proposed by the unattended run of 2026-11-11** (each is a feature of its own, none is built):
  - *RAW highlight recovery.* The RAW files hold cloud detail that the default decode clips to
    flat white (seen with LibRaw's blend mode, [F6 result](plans/F6-highlights.md)). LibRaw's
    own modes are not usable as they are: one darkens every frame, the other turns skies purple.
    A recovery needs the exposure put back after the decode, with a shoulder.
  - *Masks that reach the frame edge.* SAM 3 masks stop 1 to 12 proxy pixels short of the frame
    edge on most photos, so a region edit leaves a thin unedited rim
    ([F2 result](plans/F2-region-instructions.md)). An opt-in "snap masks to the frame edge".
  - *A source-relative review check for white areas.* With the highlight hold on, a photo whose
    source is more than 5 % white is flagged for review although the edit added no clipping. The
    check lives in a protected file.
  - *An exit code that reports failed photos.* `autoedit run` ends with 0 even when photos
    failed (it always did); a script cannot tell. Exit 1 when any photo failed.
  - *Depth on lifted dark photos.* The black point cannot give it (the quality gate protects the
    body of a dark photo); it is a matter of the look. F12's calibration is the tool for it.
- **Not now:** an XMP hand-off to Lightroom. AutoEdit's op math does not map onto Adobe's sliders.
- **Not now:** the learned recipe predictor as a default. It needs evidence first.
- **Never:** generative edits (sky replacement, object removal, inpainting) and retouching that
  invents content. AutoEdit never invents content: nothing is generated, replaced or filled in.
  Hiding a region is allowed when the user asks for it, and is always reported (F22, decided
  2026-11-22). Straightening or cropping (F32) asks the owner for a bounded exception too; it
  generates nothing and is reported in the sidecar.
- **After this plan:** the website (the Studio rebuild and the desktop shell are done as F49).

## Rules for every feature

- The spec comes first and names its tests. The definition of done in `CLAUDE.md` applies.
- A feature that changes exposure, tone or decode runs the tone report gate: 0 dark to darker and
  0 pushed into clipping. Thresholds in `configs/quality.toml` are never loosened.
- A feature that changes default pixels stays behind a setting until a blind contact sheet and the
  owner's vote approve it.
- New tuning values live in TOML. A new op needs a math test; a rule change needs a golden test.
- Nothing is committed by an agent. Each finished feature ends with a commit and push block for
  the owner, on its own branch.

## Decisions for the owner

Open decisions. Each is a one-line change once it is made; the evidence is named.

| Decision | Evidence | The change, if yes |
|---|---|---|
| Highlight hold on by default? | Blind gallery `out-features/galleries/highlight-hold/`; [F6 result](plans/F6-highlights.md) (cost: more photos flagged for review) | `highlight_hold = true` under `[run]` in `defaults.toml` |
| Noise reduction on by default? | 100 % crops in `out-features/F7/`; [F7 result](plans/F7-noise.md) | `denoise = true` under `[run]` |
| Black point on by default? | `out-features/F5/`; [F5 result](plans/F5-black-white-point.md). The run's recommendation: leave it off | `levels = true` under `[run]` |
| RAW development default | Blind gallery `out-features/galleries/raw-mode/`; table in the [F8 result](plans/F8-defaults.md) | `raw_mode = "preview_ev_headroom"` under `[decode]` |
| Default backend `sam3` when it is installed? | `autoedit models status`, `autoedit doctor` | `backend = "auto"` under `[segment]` |
| May a moody look opt out of the darkness check? | [F8 spec](plans/F8-defaults.md) | `preserve_exposure = true` under the look in `looks.toml` |
| Bursts mode as the default consistency? | [F9 result](plans/F9-shoot-consistency.md) | `consistency = "bursts"` under `[run]` |
| Gated SAM 3 flow, or a second segmenter? | The candidate list in `ARCHITECTURE.md`, "Adding a segmenter" (licences unverified) | F14 follow-up |
| What should the website do first: showcase and download, or online editing? | none yet | After this plan |
| May AutoEdit hide a region when the user asks (F22)? | Yes, 2026-11-22: [F22](plans/F22-hide-on-request.md) | Built; the "Never" line above is reworded |
| May AutoEdit straighten and crop (F32)? Both are built switched off. | `out-features/F32/` | `straighten` in `defaults.toml`; `aspect` in an export preset |
| Upright needs 20 or more photos of buildings or rooms with leaning walls (F32). Do you have some? | none | a task of its own |
| Which kinds of photography first? | [GENRES.md](GENRES.md), "Order". A pack can only be tested on photos of its kind, and the repo has mountain landscapes only | The owner names the kinds and supplies at least 20 photos of each |
| Sample photos for look cards (F28): own photos, or a licence-free set from the web? | F28 | A download needs a yes |
| Keep a local history of your own edits, on by default (F27)? | F27: no pixels, no file names, never leaves the computer | `history = true` under `[run]` |
| Do background blur, skin smoothing and overlays stay out? | "Ideas, not planned yet", last item. Proposed: they stay out | none |
| Render workers on by default? | F36, T5: twice the batch speed, and 5.3 GB per worker. Proposed: no, `--workers 0` per run | `workers = 0` under `[performance]` in `defaults.toml` |
| Read the lesson texts; which sliders should "try first" have, and how wide? (F29) | `configs/lessons.toml` | `[try.sliders]` |
| Pick the keepers: are the weights right, one pick per burst or more, tag picks for Lightroom? (F35) | `out-features/F35/swiss/contact-sheet.html` | `configs/pick.toml`; a later `--keep N` and XMP task |
| Night sky (F31): 20 night photos for the blind sheet and the pack's gate? May the `astro` look set `preserve_exposure` (it would opt night frames out of the darkness check; built without it)? Should the tone report's audit measure a night frame without its sky? | `out-features/F31/` | `tested` in `genres.toml`; `preserve_exposure` in `[astro]` of `looks.toml` |
| Exact colour: is 245 the right white for a backdrop, or pure white (which the review check flags)? The backdrop is found in pixels, not by SAM 3, and a neutral pick the white balance cannot meet is refused, not half applied: right? A real set of 20 frames is open (F34) | `out-features/F34/sheet.jpg` | `[backdrop] white` in `rules.toml` |
| Merge frames: a merged bracket is as bright as its darkest frame and ends about 0.7 EV under the middle frame after a run. Keep it linear, or add a shoulder at the middle frame's brightness? Do you have a tripod bracket, a night series and a focus series to try? (F33) | `out-features/F33/brackets/` | a `[brackets]` key in `merge.toml` |
| Genre packs: mark `landscape` and `alpine` tested? Each pulls one photo back (stub 0063_snow, SAM 3 0001_dark) that a run without a genre does not; fewer in all on the stub (8 against 11). Should `alpine` keep the highlight hold? Which kinds next? (F30) | `out-features/F30/tone-*.json` | `tested` in `configs/genres.toml` |
| Should what a look shapes be held inside the op bounds (today a profile look can push `curve` to 136 of 100)? (F28, unattended run) | [F28 spec](plans/F28-look-files.md), known limits | A clamp at the end of `looks.shape`, with a byte check |
| Are 12 photos, 3 days and 70 % the right bar for a style suggestion, and is a day the right "run"? (F27, unattended run) | [F27 spec](plans/F27-style-from-edits.md), "Measured" | `[suggest]` in `style.toml` |
| Should a look name that no file defines be refused (today it renders as the neutral look and the sidecar names it)? Should `--look surprise` render every photo six times? (F26, unattended run) | [F26 spec](plans/F26-surprise-me.md), facts 2 and 6 | A check of `cfg.look` in `Pipeline.build`; a feature of its own |
| A LUT goes past the oversaturation gate, and a black and white look with a toning LUT gives a toned black and white. Right? (F25, unattended run) | [F25 spec](plans/F25-lut-files.md), Design 3 and the decisions table | The rows of its decisions table |
| Which of F24's eight looks stay, and are the casts of `film_warm`, `film_cool` and `cross_process` strong enough? A stronger red filter? Should the prompt words "black and white" use the new op? (F24, unattended run) | `out-features/F24/bw_*.jpg`, `film_*.jpg`; [F24 result](plans/F24-bw-film-looks.md) | The values in `looks.toml`; `bw = 100` in `intents.toml` |
| Is grain 25 the right light grain, and should a packaged look carry grain? (F23, unattended run) | `out-features/F23/crops.jpg`; [F23 result](plans/F23-grain.md) | `_GRAIN_SIGMA` in `edit/ops.py`; `grain = N` in a look of `looks.toml` |
| Fill the holes inside a marked thing? | [F37 result](plans/F37-point-and-tell.md), "Found on the way": SAM 3's mask has holes on dark, even surfaces. Filling them changes what a mark edits against what SAM 3 returned | A small task of its own |
| The editor (F58): the RAW brightness default (D6); should Automatic edit even a folder's brightness again, or keep exporting each photo on its own as the build does (D12); may LightCraft's remove and fill tools be used in the manual stage (D4) | [F58](plans/F58-editor-sidebar-and-cr2.md), "Decisions for the owner" | The editor's neutral original, Automatic's exports, the edit declaration |
| Styles (F61): own RAW files of overcast and mist for Moody landscape (O2); which finished photos have the look you mean, per style (O4, started from the three examples); Standard's review marks on low-key photos as an item of its own (O6). Answered on 2026-11-24: FiveK originals may be test photos, the bar counts decided photos, no vote on the galleries of round one | [F61](plans/F61-style-round-two.md), "Decisions for the owner" | The photos, the targets and the start of round two |

Things only the owner can do, left open by the unattended run:

- Vote in the two blind galleries (`out-features/galleries/HOW_TO_VOTE.txt`).
- Calibrate a personal look on your own photos (`autoedit calibrate`) and blind-test it against
  `natural`.
- `pip install pillow-heif` and run the tests once, to verify HEIC input for real.
- One real `autoedit models download` through the gated Hugging Face flow.
- One real run of the LLM path with your own key (F17).

Decisions the unattended run took without the owner are listed in each spec under "Decisions
taken without the owner", with how to reverse them. The ones that changed something an earlier
plan said:

| Decision | Why | Reverse it by |
|---|---|---|
| `raw_highlight` (LibRaw's highlight modes) was removed after it was built | Measured: one mode darkens 109 of 110 photos, the other turns skies purple ([F6 result](plans/F6-highlights.md)) | Build the "RAW highlight recovery" proposal above |
| The black point only reshapes the darkest tenth of a frame, at full strength | The first version made dark photos darker and tripped the safety check on 3 of 110 photos ([F5 result](plans/F5-black-white-point.md)) | `_LEVELS_BODY` in `edit/ops.py` and the rule in `rules.toml` |
| The highlight hold starts at 0.80, not 0.94 | 0.94 gave stepped, blotchy skies | `[highlight_hold]` in `rules.toml` |
| The calibration steps are about three times the size first specified | Smaller steps gave pairs nobody could tell apart | `configs/calibrate.toml` |
| A run refuses to write an export over a source photo (its own or another input); that photo fails, the batch goes on | Found in two reviews: an output folder that is the input folder, `--subdir` pointing back at it, upper-case extensions on a case-insensitive disk, a sibling file of another format | none intended |
| Bursts are repeats of ONE framing (same orientation, thumbnails alike without the sky-over-ground layout); RAW files and JPEGs are evened out separately | The first comparison grouped different pictures shot seconds apart ([F9 result](plans/F9-shoot-consistency.md)) | `[consistency]` in `rules.toml` |
| The LLM translation of instructions never gets a clause that was rejected as a negation or an exclusion | Found in review: it could turn "don't brighten the face" around | none intended |
| F21's T2 and T3 were built on the first part of the measurement (134 photos, no screens or documents), before the owner said whether that was enough | The owner asked for an overnight run; the check is off by default and changes no pixel | `configs/reveal.toml` per kind; a second labelled set re-runs `scripts/eval_reveal.py` on the new photos only |
| F21's seven unmeasured kinds report "may miss" and keep the default score of 0.5; on the owner's photos they raise false alarms (name badge on 10 of 137) | Missing a plate is worse than a false alarm in a check before sharing | `min_score` or `off = true` per kind in `reveal.toml` |
| Already decided earlier: default for `metadata` is `shareable` (no GPS, no serial numbers) | F1 | `[output] metadata` |
