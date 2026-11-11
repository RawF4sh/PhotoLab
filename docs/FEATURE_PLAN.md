# AutoEdit feature plan

Engine first, interface later. This plan lists the engine, CLI and API features to build before the
Studio UI is rebuilt and before the desktop app and website work starts. It was written on
2026-11-10 against the code as it stands after the improvement loop (see `CHANGELOG.md`).

Features are built one at a time, in the order below, through the loop described in
[AGENT_ROUTING.md](AGENT_ROUTING.md). Each feature starts with a spec in `docs/plans/`. Update the
status column here whenever a feature moves.

## Order and status

| ID | Feature | Size | Status |
|---|---|---|---|
| F1 | Export keeps metadata and colour profile | S | done |
| F2 | Region instructions | L | planned |
| F3 | Per-photo adjustments | M | planned |
| F4 | Throughput | M | planned |
| F5 | Black and white point | M | planned |
| F6 | Highlights | M | planned |
| F7 | Noise | M | planned, measure first |
| F8 | Defaults | S | waiting for the owner |
| F9 | Shoot consistency v2 | M | planned, measure first |
| F10 | Export presets | S | planned |
| F11 | User config layer | M | planned |
| F12 | Taste calibration | L | planned |
| F13 | Settings schema | S | planned |
| F14 | Model manager | M | planned |
| F15 | API contract | S | planned |
| F16 | More input formats | S | planned, check demand |
| F17 | LLM path refresh | S | planned |

Size: S is a few files and one short spec; M is a new op or module with its tests; L is a new
subsystem. Status values: planned, spec, building, checking, done.

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
  `metadata = "keep" | "no_location" | "none"`. Spec: [plans/F1-export-metadata.md](plans/F1-export-metadata.md).
- **Done when:** tests show that capture time and camera survive, orientation is normal, GPS is
  gone under `no_location`, no tag is written under `none`, and the sRGB profile is present. The
  stub smoke run passes.
- **Owner:** the default is `no_location`, decided on 2026-11-10.
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

## Later, or not planned

- **Later:** lens corrections for RAW, Display P3 output, a watch folder, film grain for the
  cinematic look.
- **Not now:** an XMP hand-off to Lightroom. AutoEdit's op math does not map onto Adobe's sliders.
- **Not now:** the learned recipe predictor as a default. It needs evidence first.
- **Never:** generative edits (sky replacement, object removal, inpainting) and retouching that
  changes content. AutoEdit must never change content.
- **After this plan:** the Studio rebuild, the desktop shell and the website.

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

| Decision | Needed for |
|---|---|
| Result of the blind vote on the improvement loop | Everything in phase 3 |
| Default for `metadata`: decided on 2026-11-10, `no_location` | F1 |
| Should the default also drop camera and lens serial numbers and the owner name? | F1 follow-up |
| RAW development default | F8 |
| May a moody look opt out of the darkness gate? | F8 |
| Default backend `sam3` when installed? | F8 |
| Gated SAM 3 flow, or a second segmenter? | F14 |
| What should the website do first: showcase and download, or online editing? | After this plan |
