# F56. A connected editor, editable AI, and adaptive batch workflows

Date: 2026-11-23  
Status: **Superseded as the build plan on 2026-11-23 by [F56](F56-connected-editor-and-lightcraft.md). Kept as the long-term picture; nothing here is scheduled.**  
Paths and commands in this file are relative to the repository root.

## 1. What we are building

Turn AutoEdit into a complete photo workspace with five connected areas:

| Area | What the user can do |
| --- | --- |
| Editor | Edit a photo manually, inspect masks, adjust tone and color, frame it, and export. Works without AI or a network. |
| AI Studio | Describe a result, preview the suggested changes, then accept, adjust, disable, or undo individual changes in the Editor. |
| Batch | Group a shoot, apply different recipes to different types of photos, preview representative results, and review exceptions. |
| Learn | See what changed on the current photo, why it changed, and practice the same adjustment without losing work. |
| Recipes | Save fixed looks and adaptive editing rules, organize multiple recipes, and reuse or share them. |

Keep the existing search, culling, reference matching, privacy checks, hide tools, merge,
export presets, and galleries accessible through a Tools area. They should open the same
photos and projects rather than creating a separate editing experience.

The central workflow is:

**Open a photo → edit manually or ask AI → inspect and refine each change → save a recipe
→ preview it on a group → process the batch → open exceptions in the same editor.**

Start with the current local desktop app and local browser interface. A hosted editor,
accounts, payments, collaboration, and a browser extension are separate future projects;
they are not prerequisites for this workflow.

## 2. What exists and what needs to change

This inventory comes from the current source and the F54/F55 implementation records, not
the older historical inventory at the beginning of `docs/FEATURE_PLAN.md`.

| Capability | Current implementation | Work in this plan |
| --- | --- | --- |
| Saved projects | Version 1 projects, per-photo overrides, snapshot undo/redo, atomic saves | Versioned editing documents with named steps, provenance, migration, and revision checks |
| Manual editor | Seven exposed tone/color offsets, object offsets, brush corrections, crop/straighten, comparison, native detail | Broader controls, clear manual/automatic starting states, editable step inspector, histogram and curve controls |
| Gemini | Explicit request, bounded thumbnail/context, proposal review, apply into normal saved controls | Persistent per-change AI steps, partial acceptance, protected manual adjustments, stale-proposal handling |
| Presets | Named local look/intensity/global-offset presets | Preserve these; add separate adaptive recipes with conditions and concept-specific rules |
| Batch | Per-photo options, consistency, selected copying, progress, pause/cancel, isolated failures | Recipe routing, representative previews, persistent run plans, restart recovery, explainable exception review |
| Teaching | Existing `teach.py`, lessons in TOML, preview `steps` and `try` support | Dedicated Learn view connected to actual saved edits |
| Rendering | Python pipeline, optional SAM 3, stub backend, full-resolution export | Retain as the reference implementation; evaluate optional LightCraft reuse at a defined boundary |

Relevant existing files:

- Projects: `src/autoedit/web/projects.py`, `src/autoedit/web/static/app/project.js`.
- Editor/shell: `src/autoedit/web/static/app/{main,photo,photo_tools,folder,presets,tools}.js`.
- Rendering/API: `src/autoedit/{pipeline,contract,batch}.py`,
  `src/autoedit/web/{app,service,jobs,photo_base}.py`, `src/autoedit/edit/ops.py`.
- AI: `src/autoedit/web/assist.py`, `src/autoedit/decide/gemini.py`.
- Presets/teaching: `src/autoedit/web/edit_presets.py`, `src/autoedit/{teach,lessons}.py`,
  `src/autoedit/configs/lessons.toml`.

F55 records 1,233 passing tests and previous full-corpus tone checks. Those are historical
results, not fresh verification of this plan. Existing limits remain visible: the real-photo
corpus is landscape-heavy, live Gemini quality has not been established, and `natural_clean`
is still an opt-in candidate awaiting a blind preference decision.

## 3. How LightCraft fits

### Verified upstream facts

LightCraft is a Rust application with a separable egui interface and engine layers. Its
README describes it as alpha. This is an integration candidate, not evidence that it already
meets AutoEdit's quality requirements. [Source](https://github.com/storytold/lightcraft#how-its-built)

Its CLI exposes a headless MCP server over standard input/output, as well as a connection
to a running desktop instance. The documentation describes persistent libraries and an
exclusive library lock. This gives us a concrete route for a prototype, but does not prove
we can embed its UI or render our existing document unchanged.
[MCP documentation](https://github.com/storytold/lightcraft/blob/main/docs/mcp.md)

Most project code offers MIT or Apache 2.0. Exceptions matter: its NOTICE identifies an
Apache-only segmentation component, separately licensed model weights, asset notices, and
restricted ArtCraft branding. The exact code and build features selected for reuse must be
inventoried. Do not treat the top-level MIT option as a license for every bundled component.
[MIT license](https://github.com/storytold/lightcraft/blob/main/LICENSE-MIT),
[NOTICE](https://github.com/storytold/lightcraft/blob/main/NOTICE)

Upstream documents camera-specific RAW limitations. Test the actual cameras in our corpus;
accepting an extension is not proof of full sensor decoding.
[RAW support evidence](https://github.com/storytold/lightcraft/blob/main/docs/raw-parity.md)

These sources were checked on 2026-11-23. Their `main` branches can change. Phase 0 must pin
the exact revision used for evaluation and record source URLs, build features, and checksums.

### Recommended integration approach

AutoEdit owns the user interface, project document, AI proposals, recipe evaluation, and
batch orchestration. LightCraft should supply reusable editing capabilities where its
quality and integration justify them.

1. Prototype control of an isolated LightCraft instance through its documented interface.
2. Test whether the required engine components can be exposed behind a small local adapter.
3. Keep the Python renderer available and working while the adapter is evaluated.
4. Promote only capabilities that preserve masks, edit state, color handling, and export fidelity.

An egui/Rust app is not automatically a JavaScript editor we can drop into the current page.
A visual overlay or an iframe alone would not connect its edits to AutoEdit's saved state.
Do not commit to replacing the application shell before the integration test passes.

The production preference is selective reuse of editing components behind AutoEdit's existing
pipeline stages. A headless whole-application bridge is an evaluation tool first. If it cannot
respect the required stage and mask boundaries, it must not become an opaque replacement for
the pipeline. Record the failed requirements, retain the Python engine, and reuse suitable
permissively licensed components where practical. This decision does not block the shared
document, editor, AI, teaching, or recipe features.

## 4. Architecture and behavior contracts

### One editing document

Editor, AI Studio, Learn, and Batch all read and change one canonical document per photo.
Changing tabs must not duplicate edits, flatten them, or start another AI request.

Proposed document fields:

| Field | Purpose |
| --- | --- |
| `schema_version`, `revision` | Migration and conflict detection |
| `source_id`, `source_fingerprint` | Identify the approved original and detect replacement |
| `engine_id`, `engine_version`, `capabilities` | Record the renderer and supported operations |
| `baseline` | Explicit Original or Auto starting state, resolved settings, config snapshot/digest |
| `steps` | Named editable changes with stable IDs and origin: manual, rules, AI, or recipe |
| `selections` | Concept identity, resolved mask reference, refinements, geometry coordinate space |
| `geometry` | Crop and straighten in the existing normalized source coordinate system |
| `history`, `future` | Bounded undo/redo transactions, separate from enabled editing steps |
| `recipe_application` | Recipe version, matched rules, measured inputs, and generated step IDs |
| `export_state` | Last exported revision and whether the current edit differs |

Each adjustment step has an ID, label, target, typed parameters, enabled state, and provenance.
AI-origin steps also retain the validated explanation, provider/model identifier, proposal ID,
and the revision on which the proposal was based. Store no credentials or raw provider response.
Persisting the user's prompt should be an explicit local option, not necessary for replay.

### Composition rules

- Named steps are editable contributions to one render plan. They are not successive JPEG
  renders and are not an unrestricted Photoshop-style pixel layer stack.
- Existing scalar offsets compose with the saved baseline, then clamp once to the supported
  operation bounds. Explain visible clamping in the inspector.
- Selecting a step edits that step's contribution. Adjusting a whole-photo control outside
  a selected step creates or updates a manual contribution with a clearly identified target.
- Example: an AI exposure contribution of +0.4 EV and a later manual contribution of -0.1 EV
  produce +0.3 EV relative to the baseline. Disabling the AI step leaves the -0.1 EV contribution.
  Keep both the contribution and the resulting value inspectable.
- Non-additive settings such as a look, crop, or curve use typed replacement semantics. The
  most recent enabled replacement for that property wins; disabling it reveals the previous
  value. Do not sum curves or interpret them as scalar offsets.
- Undo reverses an editing transaction; disabling a step changes its active contribution.
  These are distinct actions and both survive restart.
- No arbitrary render-order dragging in the first release. The engine's operation order stays
  fixed. Grouping steps in the UI must not imply that the user changed pixel-processing order.
- Preserve manual edits when a new AI proposal or recipe arrives. Explicit replacement is a
  different action with a preview and one undo transaction.

### One renderer contract across every view

Keep the invariant:

`decode → segment → resolve → stats → scene → decide → apply → emit`

Compile document contributions in the decision stage and run the existing application stage
once. Keep geometry at its existing emit boundary. Teach can observe intermediate stages;
it must not alter the render. Recipes use existing statistics/scene results inside the decide
stage; they must not introduce a second competing pipeline.

Any optional rendering adapter must declare input/output color space, transfer function,
precision, supported controls, and which stage it implements. AutoEdit currently uses
gamma-encoded working RGB with internal linearization for some operations; never assume
another engine uses the same slider units or color representation.

Require parity between sharp preview, native detail, single export, and batch for the same
document revision. Draft previews may be reduced resolution and must say so. JPEG compression
and resizing are accounted for when comparing output; raw encoded file hashes are not a
cross-format fidelity test.

Masks stay on the proxy for analysis, are arbitrated before application, and are upsampled
and feathered at full resolution. One region owns each pixel; global operations still apply
everywhere. Preserve `resolved_only` behavior for visual object edits. Do not send a stack of
overlapping LightCraft masks through a second blend that changes this rule.

### Reproducibility and storage

- Save the accepted baseline and effective recipe settings; opening a project must not silently
  rerun AI or adopt new rule defaults.
- Persist accepted mask data as bounded local assets with checksums and model/config provenance,
  outside the JSON document. A cached artifact alone is not durable project storage.
- Keep artifacts referenced by current state and undo history. Collect only unreferenced assets.
- If an original changed, or a required renderer/model/artifact is missing, explain the mismatch.
  Reanalysis or engine conversion creates a new revision; it must not silently change the saved edit.
- Version 1 migration preserves existing pixels and history where possible. Treat an old flat
  set of controls as a legacy step; do not invent AI provenance that was never saved.
- Back up the old project before migration, write the new version atomically, and make migration
  repeatable. Older app versions must reject newer documents without overwriting them.
- The current project has an 8 MB JSON limit, 10,000-photo limit, and bounded snapshot histories.
  Add document size tests. Move toward an atomic project manifest plus per-photo documents and
  shared immutable assets; do not copy all masks and step histories into every folder save.
- Use revision checks on writes to prevent a stale tab, queued request, or batch completion from
  overwriting a newer photo edit.

### Privacy and optional dependencies

Manual editing, rule-based auto editing, and local recipes work without a provider key.
Gemini remains opt-in and receives only a stripped bounded thumbnail and allowed context.
Full-resolution pixels, credentials, local paths, and EXIF are not sent in AI context.
Timeouts, refusals, missing models, and budget exhaustion leave the local document usable.

Keep LightCraft/Rust and model dependencies optional. The minimal Python installation and stub
segmenter still run without GPU or network. Keep torch/SAM imports behind the existing segmenter
boundary. No model weights go into version control.

## 5. Implementation phases

Each phase has a demonstrable result and a completion gate. Finish the contracts before
parallel implementation of code that depends on them.

### Phase 0 — LightCraft integration and quality evaluation

**Dependencies:** none. **Size:** medium investigation, with a bounded scope.

1. Record the existing worktree state and preserve staged/unstaged work. Make no commits.
2. Pin a LightCraft revision or release. Record selected crate/dependency licenses, optional
   features, asset notices, and branding replacements needed for distribution.
3. Use an isolated scratch library and sample copies. Build or obtain a verified matching CLI
   and test headless import, inspect controls, edit, render, save, close, reopen, and export.
4. Probe the actual protocol and parameter schema; don't assume README examples cover every
   required command. Test cancellation, process failure, malformed responses, and library locks.
5. Test global controls, object masks, crop transforms, curves, and full-resolution outputs.
   Record each capability as exact, convertible with a visible difference, unsupported, or untested.
6. Compare against AutoEdit on JPEG/PNG/TIFF and available RAW camera files, including rotated
   EXIF, bright skies, dark subjects, skin, fine detail, and out-of-gamut color when examples exist.
7. Determine whether selected components can honor AutoEdit's stage order, exclusive region
   ownership, identity behavior, and color contract. Test source-buffer import/export if that
   is the proposed boundary; CLI RAW rendering by itself does not prove this capability.
8. Record startup time, draft/sharp preview latency, native-detail latency, export throughput,
   memory, binary size, and maintenance/build requirements on named hardware.

**Deliverables:** `docs/plans/F56-lightcraft-evaluation.md`, a capability mapping, repeatable
comparison harness, local visual report, and a concrete adopt/partial-reuse/defer decision.
Experimental adapter code stays optional and separate from production routing.

**Gate:** demonstrate one photo opened, edited, reopened, manually adjusted after an external
command, and exported; provide an honest mask/color/RAW compatibility report. Do not adopt a
renderer solely because its UI looks complete. If critical requirements fail, proceed with the
Python renderer and document which LightCraft components remain usable.

### Phase 1 — Shared document, named edits, and migration

**Dependencies:** existing-engine path can start alongside Phase 0; final adapter contract follows
Phase 0. **Size:** large. This is the foundation for all later features.

1. Define document, step, proposal, selection, and revision schemas with strict finite bounds.
2. Implement the document compiler using current engine semantics and a shared control registry.
   Add capability/units metadata instead of duplicating slider ranges in AI and UI code.
3. Implement durable per-photo storage, immutable assets, atomic migration, revision conflict
   handling, and bounded history. Preserve folder aliases and picker-based path approval.
4. Connect preview, detail, single export, batch, copied edits, and project reopen to the compiler.
5. Add named step enable/disable, edit, remove, reset, and grouped undo/redo operations.
6. Retain compatible legacy API inputs while older routes/tools are migrated. Version the new
   payloads; reject ambiguous requests that specify both a document and competing flat edits.
7. Pin config/baseline/asset identity for replay and invalidate preview caches by document revision,
   source fingerprint, engine version, mask revision, and relevant config digest.

**Proposed modules:** `src/autoedit/document.py`, `src/autoedit/edit/plan.py`,
`src/autoedit/editor_controls.py`, `src/autoedit/web/document_store.py`, with coordinated changes
to `contract.py`, `pipeline.py`, `web/{projects,service,jobs,app}.py`, and `app/project.js`.

**Gate:** migrate a real version 1 project and restore every existing edit/history action; toggle
one named contribution and keep unrelated changes; obtain matching same-engine export pixels
through single and batch paths. Simulated failed saves retain the previous readable project.

### Phase 2 — Connected workspace and a stronger manual editor

**Dependencies:** Phase 1. Shell layout can be designed against its agreed contract beforehand.
**Size:** large.

1. Add Editor, AI Studio, Batch, Learn, and Recipes navigation with persistent photo/folder context.
   Place existing utilities in Tools; maintain routes or redirects for existing entry points.
2. Use a stable layout: library/filmstrip, central preview, tool inspector, and named edit history.
   Small screens use drawers and focused panels. Preserve keyboard navigation and visible focus.
3. Make the starting state explicit: **Original** starts without automatic creative adjustments;
   **Auto** retains the editable rules baseline. Switching creates an undoable transaction.
   Keep default source decoding/color conversion; original means no creative edit, not raw bytes.
4. Expand supported manual controls in groups: Light, Color, Detail, Objects, and Framing.
   Expose existing engine controls first: whites/blacks, vibrance, clarity, dehaze, sharpening,
   denoise, supported color grading and hue-band adjustments. Label their actual capabilities;
   the existing limited HSL controls are not a complete arbitrary-color mixer.
5. Add an accurate histogram and clipping overlay, numerical entry, per-control reset, before/after,
   baseline/edited comparison, fit/zoom, and clear native-detail inspection.
6. Retain concept selection, add/subtract brushes, selection overlay, and crop-aware coordinates.
   Make a missing object visible and offer correction; never substitute an unrelated mask.
7. Add a luminance point-curve editor with keyboard-accessible points, reset, and numerical input.
   Use a separate typed curve field; the current scalar `curve` operation is not a point-curve
   schema. Document its fixed application position and interaction with the legacy curve.
8. Implement bounded monotone interpolation with identity/end-point tests; avoid overshoot and
   unwanted clipping. Add RGB channel curves only after the luminance/color tests pass, as a
   distinct capability. If a renderer cannot support a curve, do not flatten it silently.
9. Connect new controls to undo, save, presets, export, and batch before exposing them as ready.

**Proposed UI modules:** `app/editor_shell.js`, `app/edit_steps.js`, `app/curve_editor.js`;
extract focused parts of `photo.js` rather than growing its event handling indefinitely.
**Engine/config work:** `editor_controls.py`, `edit/curves.py`, typed contract fields, and
`configs/lessons.toml`; existing operation tuning continues to live in TOML.

**Gate:** complete a manual edit, including object refinement and curve/crop, with no network;
reopen it, undo/redo it, and export at full resolution. Empty/zero/identity settings retain the
strict no-op invariant. Mouse, keyboard, desktop, and narrow-screen workflows are usable.

### Phase 3 — AI suggestions that remain individually editable

**Dependencies:** Phase 1 and enough of Phase 2 to inspect steps. **Size:** medium/large.

1. Return validated typed steps plus per-step explanations from Gemini, using the shared control
   registry and actual supported targets. Introduce capabilities gradually; don't promise curves
   or selection creation until their renderer contracts exist.
2. Attach `proposal_id`, `photo_id`, `base_revision`, target selection revisions, provider/model,
   and normalized changes to the proposal envelope. AI never supplies executable commands.
3. Show a before/after proposal preview and a list such as “Lift subject,” “Reduce sky highlights,”
   and “Warm color.” Let users adjust amounts, accept selected steps, accept all, or dismiss.
4. Applying accepted steps is a local transaction with a replay-safe proposal ID. Repeated clicks
   cannot apply a proposal twice. Accepted steps stay visible in Editor after restart.
5. If the photo or relevant mask changed while the request was running, flag the proposal as stale.
   Allow a new preview against the current revision or a fresh request; never apply it blindly.
6. Preserve user-protected steps and manual edits on follow-up requests. Show the resulting delta
   and explicitly identify any requested replacement.
7. Keep provider adapters separate from rendering. Capture sanitized errors, bounded retry rules,
   cancellation, and a budget ledger; opening, previewing, or exporting an accepted edit is local.
8. Distinguish model explanations from measured facts. Show supported metrics and guard actions
   separately; do not present a generated rationale as a verified observation.

**Files:** extend `decide/gemini.py`, `web/assist.py`, document contracts, and a new focused
`app/ai_studio.js`. Extend existing fake-provider tests before any optional live evaluation.

**Gate:** ask for an edit, accept two changes, reduce one, disable another, reopen, then export
without another model call. Missing consent/key, invalid output, stale results, and exhausted
budget preserve all current work. Logs/projects/sidecars contain no key.

### Phase 4 — Detailed single-photo workflow

**Dependencies:** Phases 2–3. **Size:** medium.

1. Provide a photo brief, reference selection, desired strength, and explicit protected changes.
   Keep local Auto and manual editing usable without Gemini.
2. Let a user request a bounded refinement pass after inspecting the first result. Each pass is
   a new proposal, not an unbounded autonomous loop or an automatic upload on each slider drag.
3. Offer original/current/proposed comparison, native-detail review, target-mask inspection,
   and local variants where supported.
4. Record checks for clipping, excessive changes, unreliable/missing selections, and quality
   fallback. Let the user inspect why an automatic adjustment was limited.
5. Define quality presets by analysis/refinement effort, not export resolution. Every final export
   uses the same full-resolution renderer and selected output settings.
6. Benchmark the incremental benefit of detailed analysis and extra requests; report latency/cost
   and user preference rather than asserting that more AI calls make a photo better.

**Gate:** a single-photo edit can be refined with local tools and optional AI while each change
stays inspectable and recoverable. Single mode's quality advantage must be demonstrated on a
representative evaluation set, not assumed from its name.

### Phase 5 — Adaptive recipes and a rule builder

**Dependencies:** Phase 1; recipe creation from AI also needs Phase 3. **Size:** large.

1. Keep two clear concepts: a **preset** stores fixed creative settings; an **adaptive recipe**
   stores conditions and bounded actions that are reevaluated for each photo.
2. Add recipe creation from an edited photo. Show what can transfer, which changes remain fixed,
   and which suggested conditions need a sample preview. Never infer a reliable adaptive rule
   from one photo without showing its behavior on other examples.
3. Define a versioned recipe with ID/name, base preset, match conditions, ordered rules, target
   concepts, bounded actions, stop/continue behavior, missing-target policy, and review reasons.
4. Start with explainable inputs already available: brightness/clipping statistics, validated
   metadata, region presence/coverage, and measured scene properties. Broader subject categories
   need an explicit classifier and measured quality; unknown remains unknown.
5. Add a visual builder: **When → Target → Adjustment → Limits → Otherwise**. Support multiple
   recipes, duplication, local version history, enable/disable, import/export, and preview.
6. Use a typed allowlist of predicates/actions. User recipe files cannot execute Python, shell,
   arbitrary expressions, URLs, or provider instructions. Editing thresholds belong in recipe
   data/config, not hardcoded Python condition branches.
7. Route each photo to the first matching enabled recipe in explicit priority order, with a visible
   fallback. Show all matches in the dry run so overlapping conditions are understandable.
   Within a recipe, ordered rules compose with the document's documented semantics.
8. Re-detect concept targets on each photo. Don't copy a source photo's mask pixels, crop, brush
   coordinates, or object identity to unrelated images.
9. Dry-run the recipe on varied representatives and show matched conditions, actual values,
   proposed changes, missing regions, and review flags before processing the group.
10. Save a recipe version/snapshot with each application. Updating a library recipe must not
    silently change existing photos. Explicit reapply replaces that recipe's generated steps,
    preserves later manual work, and is undoable; it must not compound the same recipe twice.

Example intent, with numerical thresholds supplied through validated recipe data:

| When | Target | Action | If unavailable or uncertain |
| --- | --- | --- | --- |
| Subject brightness below the configured target | Resolved subject region | Lift within the recipe's exposure limit | Flag for review |
| Sky has measurable highlight headroom | Resolved sky region | Reduce highlights within bounds | Skip this action and explain |
| Skin region exists | Resolved skin region | Apply restrained configured color adjustment | Leave skin-specific action unapplied |
| No routing rule matches | Whole photo | Use the selected fallback recipe or no recipe | Show fallback status |

**Proposed modules:** `src/autoedit/adaptive_recipes.py`,
`src/autoedit/configs/adaptive_recipes.toml`, `src/autoedit/web/recipe_store.py`, and
`app/recipes.js`. Do not repurpose `src/autoedit/recipe.py`: it already implements the learned
recipe predictor. Keep learned prediction, fixed looks, and conditional recipes distinguishable.

**Gate:** two different photo groups receive different recipes, an unmatched image follows the
selected fallback, a missing target triggers the declared policy, and every applied adjustment
remains editable. Portable recipes exclude keys, private paths, photo masks, and personal metadata.

### Phase 6 — Large batches with consistent results and exception review

**Dependencies:** Phases 1 and 5; optional AI sampling also needs Phase 3. **Size:** large.

1. Add a planning pass that groups photos by relevant measured properties and actual metadata.
   Reuse existing scene/consistency machinery where appropriate; filename adjacency alone does
   not establish shared lighting, framing, or subject matter.
2. Preview typical and outlier images from each group. Make recipe assignments editable before
   starting. Estimate work and, if cloud use is selected, disclose the sampling policy/call cap.
3. Default batch operation to local recipe evaluation per image. Optional Gemini assistance can
   propose a group recipe from selected representatives; it must not send every photo implicitly.
4. Snapshot selected inputs, document revisions, recipe versions, engine/config identity, output
   choices, and budget settings into a persistent run plan. Changing the workspace after start
   affects the next run, not the already-running snapshot.
5. Persist each item's state and output manifest atomically. Resume after restart, retry only
   failed/unfinished items, honor cancellation between bounded units of work, and retain the
   existing per-image failure isolation. Current in-memory web jobs need this extension.
6. Avoid duplicate exports on resume. Identify completed work by input fingerprint plus render-plan
   identity and verified output; use temporary outputs followed by atomic completion records.
7. Bound thumbnail/render/model queues and caches. Keep full-resolution images out of the UI list.
   Coordinate interactive preview priority with background rendering and optional GPU work.
8. Apply the same rendering quality and output resolution as single-photo mode. Save time through
   shared recipe design, cached analysis where valid, and fewer model requests—not lower-quality
   final rendering or blindly copying one photo's numeric corrections to all photos.
9. Route failures, missing targets, outliers, safety blends, and uncertain matches to an exception
   queue with reasons. Open an exception directly in Editor, preserve manual changes, and rerun
   just that item at its new revision.
10. If a batch finishes after the user edited the same photo, keep the batch result attached to its
    run revision and mark it outdated; do not overwrite the newer document.

**Files:** extend `web/jobs.py`, `batch.py`, consistency services, folder/project modules;
add `web/batch_plans.py` and `app/batch_review.js` as needed.

**Gate:** a mixed batch previews its routing, processes all supported photos, isolates a corrupt
file, survives pause/cancel/restart, and retries without duplication. Single/batch parity is
proven for identical plans. A changed photo cannot be overwritten by an older job result.

### Phase 7 — Learn from the user's actual edits

**Dependencies:** Phases 1–3; recipe explanations can follow Phase 5. **Size:** medium.

1. Build a Learn view from existing teaching data and actual document steps. Show before/after,
   the selected target, changed controls, measured effects, and short guidance from lesson data.
2. Let users isolate an edit and inspect its contribution. Where engine operations interact,
   label the comparison accurately; disabling one contribution is not necessarily a separate
   physical render stage.
3. Add “Try it yourself” on a temporary document branch. Reset or dismiss without changing the
   saved photo; Keep transfers the practice changes as one normal editing transaction.
4. Teach core tasks in context: exposure, highlights, white balance, contrast/curves, color,
   object masks, crop, and recipe behavior. Link each lesson to its real control.
5. Keep instruction useful offline. Optional AI explanation uses the same consent/budget controls
   and cannot make unsupported claims about the photo or grading of a creative choice.

**Files:** extend `teach.py`, `lessons.py`, `configs/lessons.toml`; add `app/learn.js`.

**Gate:** the user can understand an accepted AI/manual step, practice changing it, discard the
practice unchanged, or keep it and undo it. Learn and Editor always agree about saved values.

### Phase 8 — Integrate the qualified LightCraft components and ship the complete workflow

**Dependencies:** Phase 0 decision plus Phase 1 contracts. Adapter development can run alongside
later features; production promotion waits for the full verification below. **Size:** large if
engine reuse qualifies; smaller if only selected components qualify.

1. Implement the selected optional local adapter using the pinned components. Use a narrow
   allowlist of operations, validated arguments, bounded buffers/timeouts, and safe process
   cleanup. Discover capabilities instead of sending arbitrary generated commands.
2. Keep access scoped to approved sources and private scratch assets. Disable upstream update,
   download, and cloud behaviors unless explicitly part of a separately selected feature.
3. Map operation units and color conversions explicitly. Unsupported controls stay unavailable
   for that engine. Do not silently convert an editable unsupported operation to baked pixels.
4. Test restart, missing binary, version mismatch, malformed output, slow render, and child-process
   failure. A document that requires LightCraft must retain its saved state if unavailable;
   switching to Python is an explicit conversion with a comparison, not a silent fallback.
5. Preserve one engine choice for the photo's preview/detail/export/batch. Do not preview with
   LightCraft and export through a different engine while implying exact visual parity.
6. Package an optional installation path with required notices, removed restricted branding,
   supported-platform builds, and a reproducible version manifest. Review existing AutoEdit
   optional dependencies too; top-level project licenses alone do not describe a distribution.
7. Keep a tested Python-only path. Add upgrade tests for saved documents and pinned upstream
   versions. Track upstream changes deliberately rather than pulling unpinned code at startup.
8. Complete accessibility, performance, onboarding, and export/recovery checks across all modes.
   Update `APP_GUIDE.md`, `README.md`, `CHANGELOG.md`, API docs, and the feature-plan status.

**Gate:** the adopted integration passes stage/mask/color/identity requirements and improves a
measured capability without breaking saved edits. If it does not qualify, ship the connected
AutoEdit workflow with its existing engine and publish the evaluation internally; do not claim
LightCraft integration was delivered.

## 6. API changes to agree before parallel implementation

Names below are proposed, not endpoints that already exist. All paths are under `/api/v1`.

| Contract | Proposed behavior |
| --- | --- |
| `GET /editor-capabilities` | Versioned controls, units, bounds, targets, curves, renderer availability |
| Existing `/project` plus per-photo document routes | Versioned manifest, lazy photo loading, optimistic revision checks, atomic saves |
| Existing `/preview`, `/detail`, single-export route | Render the same validated document revision; return revision/engine identity |
| Existing `/assist` | Proposal envelope with source revision and per-step explanations; no saved mutation |
| `POST /documents/{id}/commands` | Validate local editing transaction; apply accepted proposal IDs once; return new revision |
| `/recipes` and `POST /recipes/preview` | Local recipe CRUD/import/export and explainable dry-run evaluation |
| `POST /batch-plans` | Snapshot routing, selected revisions, estimated work, and review requirements |
| Existing `/jobs` and job controls | Execute a saved plan; durable status, cancellation/resume, per-item results |

Mutation payloads need bounded sizes and known fields. The server revalidates capabilities,
paths, operation targets, and expected revisions. Revision conflict responses keep the user's
draft and offer reload/reapply. Never deserialize provider output into privileged commands.

## 7. Parallel implementation sequence

Use parallel agents during implementation, following the owner's earlier preference. Do not
start independent implementations of competing document schemas. Root owns integration and
shared-file wiring; workers coordinate before touching the same files. No agent commits.

| Wave | Root/integration | Engine agent | UI agent | Workflow agent |
| --- | --- | --- | --- | --- |
| A | Freeze contracts, record compatibility/migration fixtures | Phase 0 LightCraft evaluation | Map shell/control interactions against contracts | Specify recipe routing and persistent batch model |
| B | Route integration and migrations | Phase 1 compiler, storage, capability registry | Phase 2 shell, step inspector, controls | Phase 3 proposal lifecycle with fake provider |
| C | End-to-end single-photo integration | Curves and optional qualified adapter | Phase 4 single-photo and Phase 7 Learn UI | Phase 5 recipe evaluator and builder contracts |
| D | Cross-path and migration review | Adapter parity and rendering checks | Recipe/batch UI and exception review | Phase 6 durable batch execution |
| E | Release gates, docs, owner-facing review | Fix engine findings | Fix interaction/accessibility findings | Fix recovery/routing findings |

Ownership is by files/modules after contracts are agreed. Shared files such as `app.py`,
`contract.py`, `main.js`, and the central project modules have one integrator at a time.
Provide focused tests and a change summary with each handoff. Independent review should
concentrate on rendering correctness, data migration, cloud boundaries, and job recovery.

## 8. Verification and quality gates

### Behavioral checks

- Document composition, one-time proposal application, stale results, undo/redo, atomic saves,
  version 1 migration, source changes, aliases, and concurrent revision conflicts.
- New operation math: zero/identity, finite/bounded output, curve monotonicity/endpoints,
  color conversion, and existing operation compatibility. Rules changes need golden fixtures.
- Arbitrated mask ownership, refined masks, crop/rotation coordinates, feather boundaries, and
  preview/native-detail/full-resolution agreement.
- Recipe rule conflicts, multiple recipes, fallback routing, missing regions, portable imports,
  invalid predicates, limits, deliberate reapplication, and preserved manual changes.
- Batch restart/resume, cancellation, output collisions, source protection, changed inputs,
  partial exports, retries, and a user edit racing job completion.
- Provider errors/invalid responses tested with fakes; confirm no secrets in persisted artifacts.
  Automated checks make no live calls and upload no user photos.
- Optional adapter failures tested separately; core tests remain runnable without Rust, weights,
  a GPU, or a provider key. Report skipped adapter tests clearly.

### Required repository checks for implementation changes

Run from the repository root using the appropriate Python environment:

```bash
PYTHONPATH=src python tests/run_all.py
autoedit run ./test-corpus ./.tmp/f56-stub-smoke --backend stub
git diff --check
```

Confirm the stub smoke produced images, explanatory sidecars, and a manifest, with failures
reported accurately. Use a new empty smoke output directory for each decisive run.

For exposure, tone, decode, curve, or rendering-engine changes:

```bash
PYTHONPATH=src python scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression
```

Require zero dark-to-darker and zero newly pushed-into-clipping results under the repository's
gate. Extend the report to exercise the candidate engine explicitly; a passing Python-only run
does not validate LightCraft. Keep intentionally dark creative choices in separate documented
fixtures; do not weaken automatic-edit gates to make a candidate pass.

### Visual and performance evidence

- Check the complete workflow in the actual app, including offline operation, keyboard-only use,
  desktop and narrow layouts, dark/light appearance, stale requests, and unsaved/error states.
- Compare lossless/native outputs and displayed previews, with documented tolerances for display
  scale, resampling, and encoding. Preserve the same-engine exact comparisons where appropriate.
- Run blind comparisons of AutoEdit default versus candidate settings/renderers. Check highlight
  texture, skin/neutral color, masking edges, noise, and detail in addition to aggregate tone.
- Record coverage honestly. Obtain licensed or user-provided portrait, indoor, product, night,
  and phone examples for claims in those categories; do not claim validation from landscapes alone.
- Record p50/p95 latency and peak memory on named hardware: startup, 640/2560 previews, native detail,
  full-res export, representative batches, and a generated 10,000-item library navigation test.
  Set numerical performance budgets from Phase 0 baseline measurements before implementation
  tuning; separately document expected slow cold starts/model initialization.
- Changes to default visual output require the existing blind-review/owner preference gate.
  Keep experiments opt-in while those results are pending.

## 9. Risks and planned responses

| Risk | Response |
| --- | --- |
| LightCraft cannot accept our resolved masks or match stage boundaries | Keep it experimental; selectively reuse components or retain Python rendering |
| Controls have matching names but different color math | Explicit unit/color mapping, visual comparison, capability declarations |
| Engine switch changes a saved photo | Pin engine per document; explicit conversion with preview and retained previous revision |
| Large histories make projects exceed storage limits | Per-photo documents, shared mask assets, bounded transactions, size/memory tests |
| AI overwrites careful manual edits | Protected steps, explicit replacement, expected revision, partial acceptance |
| Recipes look good on a sample but fail on mixed folders | Diverse/outlier previews, per-image evaluation, unknown fallback, exception queue |
| Batch work races interactive editing or consumes all memory | Immutable run snapshots, revision-aware completion, bounded prioritized queues |
| A recent upstream revision or dependency changes licensing/build behavior | Pin revision/features; review notices and dependency changes before upgrades |
| Quality appears better only on landscapes | Publish category coverage and keep unsupported quality claims out of the UI |

## 10. Release milestones and definition of complete

1. **Integration decision:** a concrete LightCraft evaluation and selected implementation boundary.
2. **Editable foundation:** existing projects migrate; manual and AI steps can be inspected,
   changed, disabled, undone, saved, and exported through one document.
3. **Connected single-photo workspace:** Editor, AI Studio, and Learn share the current photo and
   support a complete offline/manual workflow plus optional detailed AI refinement.
4. **Adaptive shoot workflow:** multiple recipes, visible routing, representative previews,
   durable batches, and exception review return to the same editable photo state.
5. **Qualified integration and release:** adopt only tested LightCraft capabilities, complete the
   compatibility/quality/accessibility checks, and document remaining limitations.

The complete user demonstration is: edit one photo, inspect the AI steps, manually change one,
practice an adjustment in Learn, save an adaptive recipe, apply two recipes to different groups,
interrupt/reopen a batch, fix an exception, and export. All accepted changes remain editable;
originals and credentials remain protected; the same work reopens correctly.

## 11. Handoff and commit policy

This turn creates the plan file only. The next implementation starts with Phase 0 and the
Phase 1 contracts. Do not install LightCraft, refactor the app, or alter saved projects as part
of preparing this document.

Never commit, stash, reset, or discard the owner's changes. The owner performs commits.
When implementation is delivered, provide selective staging/commit commands for the actual
reviewed changes, to be run from the repository root. Preserve all pre-existing staged and
unstaged work.
