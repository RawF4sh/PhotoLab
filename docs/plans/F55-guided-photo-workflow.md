# F55. A guided, controllable photo workflow

## Goal and scope

Make the saved workspace useful from the first photo through an inspected, full-resolution
export. Deliver the five requested priorities: measured natural editing, visual object controls,
guided first use, Gemini suggestions that remain editable, and everyday framing/detail/presets.
Existing local edits remain available without cloud access. The owner performs all commits.

## Implementation contracts

### Objects and selection corrections

`region_settings` is a bounded dictionary of concept names to the existing seven editor offsets.
`mask_refinements` maps a concept name to an ordered list of normalized strokes:
`{mode: "add" | "subtract", radius: number, points: [[x,y], ...]}`. Coordinates and radius
refer to the untransformed photo. Validate finite values, names, counts and total points before
work. Apply corrections after segment and before resolve, using copied masks. Arbitration still
assigns each pixel to at most one region. Regions with no correction follow the unchanged path.
Expose actual masks and labels for click selection; offer a list as a keyboard alternative.
Visual offsets carry `Adjustment.resolved_only = true` through sidecars and copied edits.
They apply only to the original resolved region's cells, even after text instructions split
those cells. Typed instructions retain their existing union semantics. Edge feathering and
the existing global finishing steps still apply.
The UI has explicit select/add/subtract modes and stores each completed stroke in photo history.
Editing coordinates must stay correct when geometry is active; show the uncropped frame while
selecting or refining when necessary. Missing objects are reported, not replaced by fake masks.

### Framing and native detail

`photo_geometry` is `{angle: number, crop: [x0,y0,x1,y1] | null}` with bounded angle and a
nonempty normalized crop. Geometry is a per-photo override, applied at the existing emit stage;
preview, single export and batch use the same contract. Rotation must never invent edge pixels.
The visual panel provides aspect choices, a crop rectangle, manual angle and reset. Geometry
and all object controls survive restart, reset and undo/redo.

`POST /detail` takes the normal photo request plus normalized centre `x,y` and bounded `size`.
It returns matched original/edited native-resolution crops, explicit crop coordinates, full
dimensions and scale 1. It uses the same local render and output geometry as export, excludes
cloud calls, and uses existing path approval. Do not label a scaled preview as native detail.

### Gemini suggestions

`POST /assist` takes a normal photo request, a nonempty bounded prompt and explicit cloud opt-in.
It sends only a bounded thumbnail and numeric editing context to the fixed Google API endpoint.
Keys are session-only or environment-provided, never in projects, presets, logs or sidecars.
The provider returns validated bounded deltas for the same seven global/region controls, plus a
short explanation. Unknown regions/operations, malformed or nonfinite values are rejected.
The response is a proposal: no saved edits change until the user clicks Apply. The client shows
the proposed changes and can dismiss them. Applying is one history action and adds the deltas to
the current controls within their bounds. Previews/exports after applying need no model call.
Timeout, refusal, missing key, budget cap or other error leaves the local edit usable. Test the
transport with fakes; do not spend money or upload user photos during automated verification.

### Presets and first use

Local named presets store portable creative choices and whole-photo offsets only, not source
paths, keys, crop coordinates, masks, privacy findings or photo-specific marks. Reuse one on a
photo or folder; retain individual photo overrides. Validate and atomically write presets in the
application profile. Guide the first visit through Open, Improve, Adjust and Export, with clear
readiness, local/cloud wording, and advanced settings behind disclosure.

### Natural results and evidence

Use existing local photos when available, grouped by actual coverage rather than assumed genre.
Add a repeatable blind comparison/report workflow for default and candidate recipes, including
brightness, clipping, skin/neutral colour and highlight texture where measurable. Introduce any
new tuning through TOML with golden tests. A candidate can be tried without silently replacing
the default. Preserve the existing quality thresholds. Default-pixel changes require a blind
review and the owner's vote; lack of diverse real photos must remain visible in the report.

## Parallel tasks and ownership

1. Engine/backend: object controls, selection corrections, geometry/native detail, durable
   project/batch parity. Own Python service/app/jobs/projects, contract, geometry, pipeline and
   new backend helpers/tests. Network and operation math are outside this task.
2. Editor: own photo.js, editor.css and new photo UI modules. Implement object/refinement,
   framing/detail, assistant review and preset controls against the contracts above.
3. Gemini: own new decide/gemini.py and web/assist.py and focused tests; verify Google's current
   official request contract. Coordinate route wiring with the backend owner.
4. Root integration: onboarding, settings, folder/project JavaScript, portable preset service,
   natural-result evaluation, documentation and verification. Shared-file changes are coordinated.

## Acceptance and verification

- API validators fail before writes and reject unbounded work; no new credential persistence.
- Object offsets and corrections affect preview/save/batch consistently; no overlapping edits.
- Native detail agrees with full-resolution output and reports its true dimensions.
- Geometry is source-preserving, recoverable through history, and consistent across export paths.
- Gemini proposals can be inspected, applied once, undone or dismissed without implicit calls.
- First-run and preset flows work with the generated sample and no GPU/network.
- Focused math, contract and integration tests; JavaScript syntax and interaction checks.
- Full dependency-free test runner and stub batch with sidecars/manifest.
- Any tone/decode change: full curated RAW report, zero dark-made-darker and pushed-into-clipping.
- Browser checks at desktop/mobile sizes, keyboard focus, no console errors and saved screenshots.
- Independent review of protected network/pipeline changes, followed by fixes and decisive checks.

## Status and validation

Implemented across three parallel agents and root integration. Independent review found and
fixed a visual-selection bug: raw union masks could spread an object offset into another
object's arbitrated claim. A persisted `resolved_only` marker now preserves the displayed
selection on save, copy and replay, without changing legacy typed instructions. Hard-mask
ownership and real-feather interior probes pass; native detail matches the full PNG render.

- Backend focused checks: 71 tests passed; independent selection/instruction/lesson/sync
  checks: 35 passed. Geometry review sampled 2,000 valid angle/crop combinations without
  invented edge pixels. API/settings snapshots include the new endpoints and hidden fields.
- Default and `natural_clean`: **110/110 curated RAW photos each**, zero errors, zero
  dark-made-darker, zero highlights or shadows pushed into clipping. Reports are local at
  `.tmp/f55-tone-default.json` and `.tmp/f55-tone-natural-clean.json`.
- Blind natural comparison: **16 real landscape pairs**, SAM 3 segmentation, zero failures
  or measured tone regressions. Local review: `.tmp/f55-natural-review/gallery/index.html`.
- Final stub CLI smoke: **2 edited, zero failures**, with two images, two explanatory
  sidecars and `run.json` in `.tmp/f55-smoke-final`.
- Browser checks: generated sample, persisted object offset and brush, square crop/2° angle,
  native 512px crop from the framed 771px output, preset save/reuse and inherited folder
  controls, missing-object feedback, undo/redo, and a successful three-photo folder export.
  Gemini review/apply/undo/redo/dismiss used a fake transport; no cloud photo or paid call.
  Consent follows Settings within a session and resets on reload. Test key absent from all
  saved project/preset/sidecar JSON. 375px layouts have no horizontal overflow; desktop,
  dark surround and keyboard controls checked; browser console clean. Evidence:
  `.tmp/f55-workspace.jpg`. Temporary test server stopped after verification.
- JavaScript syntax and whitespace checks pass. Full dependency-free suite:
  **1,232 passed, zero failed**, using `PYTHONPATH=src AUTOEDIT_PROFILE=''
  AUTOEDIT_NO_LOCAL_MODEL=1 python3 -u tests/run_all.py`; log `.tmp/f55-tests-verified.log`.
  The existing look-inventory test was updated to list the new opt-in candidate; its default
  look assertions remain unchanged. The focused look/API/settings checks also pass (33 tests).
- Editor interaction harness passed: `node --experimental-vm-modules
  /tmp/autoedit-f55-editor-smoke.cjs` (temporary local harness). It covers object lookup,
  missing/reserved names and request limits, selection/refinement, framing, native detail,
  assistant application/history and disposal of stale requests.

## Reopening follow-up — 2026-11-23

An app restart clears the server's approved folders while the Recent list and editing projects
remain saved. Clicking a recent item used to expose a technical filesystem-access error. The
client now retains the API status/code and offers a **Choose folder** sheet for a denied folder.
It uses the existing native picker, then loads the existing project. It does not grant access
from a stored path. Cancel keeps the sheet and saved data intact; unrelated server/network
errors retain their normal handling. A photo-opening request also retains its target photo.

Verification: 20 focused interface/project tests pass, including a package-free Node harness
that executes the real shell, folder and project modules. The API check proves cancellation
does not grant access and a successful picker reopens the same saved project. Browser checks
used a restarted local test server and a simulated picker: cancel, retry, restored +0.35 EV
and undo history all passed, with no console errors. Screenshot: `.tmp/f55-reopen.jpg`.
The stub batch produced two images, two sidecars and a manifest with zero failures. The full
dependency-free suite passed: **1,233 tests, zero failures** (`.tmp/f55-reopen-tests.log`).
Whitespace checks pass. No rendering behavior changed, so the earlier RAW gates were not rerun.

## Remaining limits

- `natural_clean` is opt-in. No default was changed and no preference winner is claimed.
  The owner still needs to vote in the blind gallery before any default recipe change.
- Available real-photo validation is landscape-heavy. Portrait/skin, indoor, product and
  phone colour accuracy are not established; the coverage report explicitly leaves these
  categories untested. Passing tone gates does not establish subjective quality.
- Precise named-object detection requires the optional SAM 3 model. The no-model workflow
  identifies its approximate demo masks and reports missing objects honestly.
- Gemini's request contract was checked against Google's official API/model documentation;
  automated and browser tests use a fake provider, so live provider quality is not measured.
- Native detail shows source-resolution rendered pixels, before export resize/lossy encoding.
- No commit, stash, reset, branch switch, deployment or publication was performed. Unrelated
  existing work is preserved; the owner chooses the changes to stage and commit.
