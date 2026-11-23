# F54. A saved editing workspace

The next release makes the existing local editor useful for everyday photo work: a control in
the editor must affect its preview, its saved photo, and the same photo in a batch. Reopening a
folder must restore that work. Automatic rules remain the baseline; Gemini remains optional.

## Shared contract

- `photo_settings` is an object of numeric adjustment offsets. The server validates its keys
  and ranges against the lesson slider definitions and applies it through photo adjustments.
  Zero means no additional adjustment to AutoEdit's decision. The sidecar records the result.
- `/jobs` accepts `photo_options`, keyed by a selected source path. Only photo-level look,
  intensity, neutral/reference choices, hide choices and slider offsets may vary here.
- `GET /project?dir=…` returns `{project: null | object}`. `POST /project` takes `{dir, project}`
  and returns `{saved: true}`. Version 1 stores folder choices, per-photo overrides and history,
  selection/export state, and the last photo index. Projects are local, atomically written in
  the application profile, independent of the desktop server's port. Credentials are excluded.
- The client project module exposes `getPhoto`, `updatePhoto`, `undoPhoto`, `redoPhoto`,
  `canUndo`, `canRedo`, `subscribeProject`, and `persistProject`. An undefined patch value
  removes an inherited override; empty entries/sliders explicitly clear those values.
- Preview requests may specify `preview_edge` (640, 1600 or 2560). Fast and sharp results use
  the same analysis settings. Cached pixels must be immutable and keyed by source metadata
  and decode settings. Full-resolution exports continue decoding at full resolution.
  Cloud-decider previews request only the sharp pass, so progressive preview does not double calls.

## Parallel ownership

1. Editor: `photo.js`, new editor styles/helpers. Main sliders, history, visual look selection,
   split comparison, preview zoom, filmstrip, reference selection, cancellation of stale UI work.
2. Workspace: `main.js`, `folder.js`, `settings.js`, new project module/styles. Durable project
   state, batch propagation, range selection, paged folders, review navigation and single-photo
   opening. Bursts require actual capture times; filenames alone are not evidence.
3. Backend: web service/routes/jobs, new persistence/cache helpers and focused tests. Validate
   contracts, preserve existing hide requests, and prove preview/export/batch parity.
4. Integration: styles, docs, selective copy-to-similar, quality investigation, regression tests
   and app checks. Existing uncommitted hide/reveal work is preserved throughout.

## Selective copy

The existing sync tool gains explicit recipient sidecars and adjustment categories (tone,
color, white balance, detail), with an independent exposure-matching switch. A requested target
must be among the dry-run candidates. Omitted selection preserves the existing CLI behavior;
an empty selection changes no photos. Unselected adjustment categories on recipients survive.
Marks stay local to their photo. The UI shows recipients before copying.

After a copy, the workspace imports the resulting non-mark photo adjustments, exposure bias and
white balance as a validated `photo_base`. Further sliders/sentences add to that saved base.
Existing mark entries stay editable. The base participates in history and reset, so reopening or
saving a recipient cannot silently discard the copied result. No source paths or cloud settings
are carried inside the base. Global baseline plus offsets is clamped to the operation bounds.

## Acceptance

- Slider edits survive navigation/restart and are present in single and batch sidecars.
- Undo/redo/reset restore the whole photo state, including removed overrides.
- Project saves cannot include credentials, outside-folder photos or invalid settings.
- Quick previews are visibly identified, replaced by sharp previews, and stale responses cannot
  replace a newer photo or edit. Default preview sizing remains compatible with existing callers.
- Large folders use a bounded page of thumbnails; range selection and review filtering preserve
  selections across pages. Browser files without a usable local path receive an honest fallback.
- Sync selection/categories are validated before writes and preserve unrelated target edits.
- The dependency-free test runner, focused tests, JavaScript syntax checks and stub batch smoke
  pass. Any tone/decode changes also require the full curated RAW tone report. Image quality
  experiments do not replace default rendering without measurable evidence and a blind review.

No publishing, installation, paid calls, commits or changes to the original photos are required.

## Recovered highlight detail

Removed 2026-11-22 (owner decision, see F50): the after-apply texture step is gone, F50's opt-in
`highlight_detail` is the fix for the two F44 failures.

## Integrated verification

Browser checks exercised the generated sample folder, saved exposure changes, undo/redo,
navigation, project reopen, visual look selection, split comparison and preview zoom. A batch
export retained the individual Punchy look and +0.35 exposure offset on its selected photo.
Selective sync copied only white balance to one of two candidate recipients; saving that recipient
again produced identical decoded export pixels. The recipient workspace showed its imported edit.
The final browser console contained no errors.

Focused Python checks cover baseline-plus-offset bounds, controls across all render paths,
reference approval, genre/default-look parity, cache ownership/invalidation, project history and
aliases, imported copies including consistency biases, sync selection/categories, and generated
sample EXIF. JavaScript syntax and temporary interaction harnesses cover history, cancellation,
folder switching, copy imports and draft preservation.

Final validation: `PYTHONPATH=src python3 tests/run_all.py` passed all 1,185 tests. The stub batch
smoke produced two images, two sidecars and its manifest with no failures. JavaScript syntax,
interaction harnesses and `git diff --check` passed. Rules previews requested 640 then 2560 pixels;
the cloud-decider harness requested only 2560, without making a live model call.

Both full curated RAW reports completed all 110 photos with no errors: default decode and
`--set raw_recover=true` each reported zero dark photos made darker, zero photos pushed into
highlight or shadow clipping, and zero hard failures. In the integrated recovery run, 0066 now
keeps its full edit; 0103 still receives the existing 75% safety blend. The isolated crop gains
below therefore do not imply that every integrated recovery fallback has been eliminated.

The two recovered-cloud regression crops improved minimum encoded texture retention from
0.086 to 0.271 and from 0.083 to 0.393. Both cleared the existing texture check without new floating
channel clipping; source pixels were unchanged. The correction bypasses intentional negative
clarity/sharpening, active noise reduction and clean-backdrop edits. Changes smaller than half an
output code still trigger the final audit and banding check.

Practical limits: 100% zoom is in preview pixels (up to 2560), not native RAW pixels. Browser
drag/drop without a local path uses the picker. Capture-time sequences do not establish identical
framing. Folder access remains subject to the local application's picker approval; project files
do not automatically grant access to external reference photos.
