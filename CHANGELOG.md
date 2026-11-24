# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased] — Local photo editor and style evaluation

- Automatic edit and Photo editor share local development. The editor opens the original,
  with AI Edit, Isolate, Learn, Adjust and optional Ask Gemini in its sidebar.
- Open photo develops supported RAWs, including Canon CR2 and CRW. Fine-tune restores
  LightCraft settings from the project. Manual exports use the full-resolution source, output
  preset and metadata policy, with spot tools and mask usage recorded in the sidecar.
- Added opt-in photographic style targets and region-shaped lighting. Standard retains its
  existing processing. Specialized styles require a passing blind evaluation to enter the menu.
- Added local tools to assemble licensed evaluation panels, verify file provenance, check
  decoding, and compare style candidates against a frozen baseline.
- Changed: Automatic edit exports each photo on its own. It no longer evens a folder's brightness
  from photo to photo, so its file equals the editor's and the command line's for one photo.
- Unchanged from the earlier Automatic and Photo editor work: a photo marked for review or as
  failed opens on its edit, Automatic follows the choices in Settings, and the first Fine-tune
  launch asks before it downloads LightCraft (about 75 MB).
- Fixed after a check of this work by a second session:
  - `autoedit adjust` and `autoedit sync` refuse a photo that was finished by hand in Photo
    editor. They used to write its export again without the manual changes.
  - A photo's own Hide setting reaches every picture made for Fine-tune.
  - A single export never replaces a file AutoEdit did not write, and never another photo's
    export of the same name: the second photo gets `-2`.
  - A run of saves in Fine-tune is one undo step. A lost manual-edit file no longer blocks its
    photo. Every retouched spot is recorded, so the edit declaration cannot miss one.
  - Export on a photo without an edit shows a notice. It used to jump into Fine-tune.
  - With a style, the frame's lift and a region's own lift share one limit (2 EV on a JPEG), and a
    region's search stays inside that region's limits. A candidate style without a label stays
    hidden, a quality config file cannot switch off the colour check, and the optional LLM decider
    is not offered the new burn op.
  - When the saved project of a folder cannot be read, `autoedit run` stops before the first
    photo and says why: it could not tell which photos were finished by hand.
  - Loop tools: a round is scored only when its sheets, controls and pairs come from the same two
    config folders; a download is bounded by the size its source listed, one bad file no longer
    stops a run, and a web page saved under a photo extension is refused; photographers are
    compared by a normalised credit; `style_target.py` runs isolated and takes owner files only
    from `owner/photos/`.
- `tests/run_all.py` prints SKIP for a test whose optional library is missing (it used to count
  as passed), and takes test file names to run a part of the suite.
- `scripts/check_public_repo.py` also fails on an absolute home-folder path in a tracked file.

## [Unreleased] — Editable changes and folder recipes (F56)

- Added a Changes list with individual amounts, toggles, undo and saved provenance.
- Gemini suggestions now contain individually selectable changes. Applying a selection keeps
  the manual sliders editable and includes the selected contributions in local preview and export.
- Extended saved presets into portable recipes with object adjustments and optional conditions.
  The folder planner previews assignments in priority order, records missing objects for review,
  and supports replanning while preserving independent edits.
- Fixed after a check against the plan: recipe **Export** also works in the desktop window, an
  empty recipe order clears a folder plan, and a suggestion that repeats your request is renamed
  from its controls instead of refused. The request text is still never stored.

## [Unreleased] — Guided photo editing and optional Gemini suggestions (F55)

### Added

- Guided first use with sample photos, local/model readiness and collapsed advanced settings.
- Found-object sliders, arbitrary named-object requests, and undoable selection correction
  brushes. Coordinates stay attached to the uncropped source; missing objects are explicit.
- Visual crop/straighten and native-resolution before/after detail inspection, shared across
  preview, save and folder export. Rotation never fills missing edges.
- Gemini suggestions as reviewable, bounded global/object adjustments. Applying is one history
  action and leaves subsequent rendering local. Session-only keys, explicit cloud consent,
  stripped thumbnails, request limits and safe failure preserve the local workflow.
- Local named presets for look, strength and whole-photo controls, reusable on photos/folders
  while preserving per-photo overrides. Presets exclude coordinates, paths and credentials.
- Opt-in `natural_clean` candidate and `scripts/natural_review.py` for repeatable blind comparison
  with honest category coverage. The default recipe is unchanged; owner preference is still required.

Spec and verification: `docs/plans/F55-guided-photo-workflow.md`.

### Fixed

- Reopening a saved folder after restarting the app now offers **Choose folder** instead of
  a technical access error. The native picker restores access, then the saved controls and
  history load normally. Canceling leaves the project untouched.

## [Unreleased] — Saved editing workspace (F54)

### Added
- Main photo controls for exposure, contrast, highlights, shadows, temperature, tint and color.
  They are saved adjustments to the automatic edit, shared by previews, single exports and batches.
- Local projects restore folder choices, individual edits, selection, export state and bounded
  undo/redo history. Projects survive the desktop server changing ports; credentials are excluded.
- Visual look comparisons, a reference panel, split before/after, preview pixel zoom/pan,
  a neighboring-photo filmstrip, and quick previews followed by sharp previews.
- Single-photo opening, locally generated sample images, paged folder grids, Shift selection,
  capture-time groups and a review queue. Browser drops without a local path use the file picker.
- Copy-to-similar recipient and adjustment-category selection; unrelated edits and marks on
  recipient photos are preserved. Exposure matching can be switched off independently.

### Changed
- Preview decode caching is bounded and invalidated by source/decode changes; stale queued
  previews can be skipped. Exports continue using full-resolution source pixels.
- Global adjustment limits include the automatic baseline; clamped adjustments are reported.

Spec and verification: `docs/plans/F54-editing-workspace.md`.

## [Unreleased] — F22 and F50–F53

### Added
- **Hide on request** (F22), off by default: `autoedit run ... --hide plate,face` hides every
  finding of those reveal kinds in the export; `--hide-method cover|mosaic|blur` and the `hide`
  and `hide_method` settings. `cover` (default) is one flat fill in the average colour of the
  surroundings and copies nothing from under the region; `mosaic` and `blur` scale with the region and keep a trace.
  The mask is grown by a margin and never feathered inward; hiding is the last pixel step of
  apply, on the full-size picture, and the source is never changed. Methods and numbers are data
  in the `[hide]` table of `configs/reveal.toml`. The photo screen lists findings with a Hide
  button. Content rule (owner, 2026-11-22): AutoEdit never invents content; hiding a region is
  allowed when the user asks and is always reported.
- Every hidden region is in the sidecar (`reveal.hidden`: kind, box, method, trace) and the
  notes; a request that hid nothing says why (`reveal.hide_note`); `run.json` and the last line
  of a run count them. Spec and result: `docs/plans/F22-hide-on-request.md`.
- **Sky detail** (F50), off by default (`--highlight-detail`, setting `highlight_detail`): the
  highlight roll-off takes its gain from the level around each bright pixel, so faint cloud and snow
  keep their contrast. With `raw_recover` it keeps 0066 and 0103 at their full edit (tone gate: no
  photo pulled back that the default leaves alone). Replaces an earlier, unreleased texture step
  for the same failure. Spec: `docs/plans/F50-sky-highlight-pull.md`.
- **Fast analysis** (F51), off by default (`--fast-analysis`, `[decode] fast_analysis`): the edit is
  decided on LibRaw's half-size development and applied to the full one; previews come faster.
  Spec: `docs/plans/F51-fast-analysis-decode.md`.
- **Lens vignette** (F52), off by default: `autoedit lens-profile FOLDER` builds a per-lens profile
  (camera and lens model names and numbers only) by comparing each RAW with its camera JPEG; 
  `--lens-vignette` applies it at decode, as strong as the camera's own correction. Spec:
  `docs/plans/F52-lens-vignette-profiles.md`.
- **Reveal check measurement** (F53): `scripts/eval_reveal.py` measures each word on its own and
  recommends thresholds by the new rule; it changes no setting until the owner labels new photos.
  Spec: `docs/plans/F53-reveal-check-precision.md`.

### Changed
- Previews encode their lossless WebP at the lowest effort (same pixels, about 10x faster), and RAW
  thumbnails decode at half size (F51).
- The desktop app's folder and file dialogs open on its own window (`autoedit app`), and a second
  click no longer opens a second dialog.
- The reveal findings are built by one function that can also return their masks; the report
  is unchanged.
- The "Never" line of the plan now reads: AutoEdit never invents content.

## [Unreleased] — A minimal interface for the browser and the desktop (F49)

### Added
- **A new interface** at `/` (`web/static/app/`): Start, Folder, Photo, Tools, Settings and a Share
  sheet, light or dark with the system, the photo on a mid grey. Spec: `docs/plans/UI-minimal.md`.
- **`autoedit app`** opens the same interface in a native window (pywebview, extra `[app]`) on a free
  loopback port. On Linux it needs a GTK or Qt web view.
- **`POST /api/v1/tools/{name}`** and **`GET /api/v1/tools/jobs/{id}`** run find, keywords, declare,
  gallery, check, sync, merge, pick, looks, calibrate, models, doctor and more as jobs
  (`web/tools_jobs.py`).
- A UI batch writes a minimal `run.json` (`input_dir`) so adjust, sync and declare find their sources.

### Changed
- Pages carry `script-src 'self'` and `frame-ancestors 'none'`.
- README, ARCHITECTURE and APP_GUIDE describe the new interface; APP_GUIDE is shorter and organised by screen.

### Removed
- **Breaking:** Studio (`/`, `studio.html`), the classic page (`/classic`) and AutoEdit Lab (`/lab`).
- **Breaking:** the pre-v1 `/api/...` mount. Only `/api/v1/...` remains; scripts must update their paths.

## [Unreleased] — Highlight recovery follow-ups, a faster loader

### Changed
- **`--raw-recover`** (F44) no longer changes exposure headroom: `stats` undoes the recovery shoulder
  on its p99.5 (`imageio.unshoulder`), so the edit brightens as it would without recovery. With the
  `recovered_highlight` rules this keeps 0109, 0028 and 0033 at their full edit; 0066 and 0103 are
  still pulled back (the edit flattens recovered cloud in a bright sky, see the F44 spec).
- **Faster decoding, same pixels:** integer images convert to float in place with no clip pass; the
  linear RAW modes encode through a 65536-entry table; the colour-fringe fix (`--lens-ca`) measures
  the green channel once for red and blue. JPEG and TIFF load 30 to 55 % faster, the linear RAW modes
  about 20 %, the default RAW decode about 6 % (LibRaw's demosaic is most of it), the colour-fringe
  fix about 34 %. Output is byte-identical on every decode option.

## [Unreleased] — Highlight recovery, colour fringes, copy edits, watermark, gallery (F44–F48)

### Added
- **`--raw-recover`** (F44): RAW highlights that the default decode clips to flat white get their
  brightness back from a second LibRaw decode, shaded under white by one shoulder. Off by default;
  only with `raw_mode = "none"`. A new stat, `recovered_highlight` (the share recovery rebuilt), feeds
  the global `highlights` and `whites` rules, so recovered highlights are held as clipped ones were;
  it is 0 without recovery, so default output does not change.
- **`--lens-ca`** (F46): lateral colour fringes are measured per photo and red and blue are scaled
  onto green, at full resolution before analysis. Off by default. Vignette correction was measured
  and left out (a fit on one frame is off by as much as the vignette).
- **`autoedit sync PHOTO`** (F47): copies one photo's own changes to the photos of the same scene
  taken within 10 minutes, and moves their exposure toward it (at most 1 EV). Marks, crop,
  straighten and LLM decisions are never copied. `[sync]` in `rules.toml`.
- **Watermark** (F48): a text line or a PNG logo in a corner of the export, set only in an export
  preset of your own (`watermark`, `watermark_logo`, `watermark_corner`, `watermark_size`,
  `watermark_opacity`). Drawn on the export only; `autoedit declare` lists it.
- **`autoedit gallery FOLDER [-o DIR]`** (F48): a static `gallery.html` of the exports with
  thumbnails, relative links and captions; tiles of photos with reveal findings carry a warning.
- F45 (dehaze by distance) was measured and is already covered by the dark-channel dehaze and the
  region rules; nothing was built.

## [Unreleased] — Edge masks, exit code, search, edit declaration, timelapse (F39–F43)

### Added
- **`--snap-edges`** (F39): region masks that stop a few pixels short of the frame edge are
  extended to it, so a region edit leaves no thin unedited rim. Off by default; `[masks]
  snap_edge_px` in `rules.toml`.
- **`autoedit run --strict`** (F40): ends with 1 when any photo failed. Without it, 0 as before.
- **`autoedit find FOLDER WORD ...`** and **`autoedit keywords FOLDER`** (F41): search a run's
  sidecars by scene and detected concepts ("snow lake"), and write the concepts as keywords into a
  `.xmp` beside each export. Reveal kinds (people, faces, plates…) are searchable but never keywords.
- **`autoedit declare FOLDER`** (F42): `declaration.html` and `.json`, per photo what was changed
  (whole photo, regions, shapes, geometry, look, repairs, other steps) with SHA-256 hashes of the
  source and the export, for contests and news desks. No signing.
- **`--consistency timelapse`** (F43): evens out frame-to-frame brightness in a timelapse while
  keeping the slow change of light and real steps. Off by default.

## [Unreleased] — Reveal check (F21)

### Added
- **`autoedit check FOLDER [--out DIR] [--strict]`** and **`autoedit run ... --reveal-check`**: a
  report of what a photo may give away before it is shared — number plates, writing, people,
  faces, screens, documents and more, found by SAM 3 — and which file metadata (GPS position,
  serial number, names, capture time) is in the source and in the export, never its values.
  `reveal.html` in the output folder; a `reveal` block in each sidecar and in `run.json`. Kinds,
  words and thresholds in `configs/reveal.toml`; seven kinds with no measured photo are marked
  "may miss". Off by default; the check never changes a pixel. Without SAM 3 the picture part
  reports "not run".
- Studio: **Check before sharing** in Settings draws the findings over the preview.

## [Unreleased] — Night sky tools (F31)

### Added
- **`--night-sky`** and the **`astro`** pack (untested): night frames keep their darkness, lose a
  town's glow in the sky, get stuck pixels repaired, and keep their stars through noise reduction
  and sharpening. Four new op values: `hot_pixels`, `sky_gradient`, `denoise_stars`, `sharpen_stars`.

## [Unreleased] — Exact colour and clean backgrounds (F34)

### Added
- **`--neutral X,Y`** and **`--neutral-from FILE`**: a grey or white spot sets the white balance of one
  photo or the whole set; refused with its numbers when it cannot be met.
- **`--backdrop`**: a light backdrop evened out and taken to a set white; the product untouched.
- **`--consistency-wb`** evens white balance across a group; **`--colour-report`** reports how far
  each photo's subject colour is from its group's. All off by default.

## [Unreleased] — Merge frames (F33)

### Added
- **`autoedit merge FOLDER`**: sets of frames from a tripod (exposure brackets, repeats against
  noise, focus series, star trails) merged into one TIFF each, with a record of the frames used.
  Hand-held sets that are too far off are refused; things that moved are flagged. Settings in
  `configs/merge.toml`.

## [Unreleased] — AutoEdit Lab (F38)

### Added
- **`/lab`**, a minimal second page beside Studio: say what to change (sentences and ovals),
  looks and Surprise me, strength, straighten and crop, hold for the original, "How it was done"
  and "Try it yourself", export one photo or edit the checked ones. Studio and the API unchanged.

## [Unreleased] — Straighten and crop (F32)

### Added
- **`--straighten`** levels a clear waterline or horizon by up to 5 degrees (SAM 3's water and
  sky masks), and **`--aspect W:H`** crops to a shape placed on the subject; `aspect` in an
  export preset too. Both off by default; the sidecar's `geometry` gives the angle and the share
  kept. Done to the finished picture at the export, after every check.

## [Unreleased] — Genre packs (F30)

### Added
- **`--genre NAME`** and `autoedit genres list`: a genre pack names the things to find, a look,
  opt-in rule sets and run settings, as data in `configs/genres.toml`; own packs in the profile.
  Two packs, `landscape` (the same bytes as `--preset landscape`) and `alpine` (adds the
  highlight hold). Both are marked untested: each pulls one photo of the tone gate back that a run
  without a genre does not, so no menu offers them yet. `GET /api/v1/genres` lists them.

## [Unreleased] — Teaching mode (F29) and Pick the keepers (F35)

### Added
- **Teaching mode** as engine and API: `"steps"` on `POST /preview` replays the edit one step at a
  time with a lesson on each tool (the last step equals the preview, byte for byte); `"try"` renders
  your own slider version and compares it with the engine's, with facts from the existing checks.
  Every op now has a lesson in `lessons.toml`.
- **`autoedit pick FOLDER`**: ranks the frames of each burst by the sharpness of the subject and
  their exposure, writes a ranking and a contact sheet, and copies the picks only with `--copy`.
  Source files are never moved or changed.

## [Unreleased] — Look files (F28)

### Added
- **`autoedit looks export NAME`** writes a look as one `.aelook` file (TOML), with its palette
  and habits if asked; **`autoedit looks import FILE`** checks a file from someone else whole
  (known keys, limits, 16 kB, a plain name) before anything is written; **`autoedit looks card`**
  renders a look on your photos as a before-and-after sheet to show or share.

## [Unreleased] — Style from your edits (F27)

A run without `--history` and without a habit look renders and writes the same as before.

### Added
- **A local history of your own changes** (`--history`, off by default): one line per export in
  the profile's `history.jsonl`, holding no paths, file names, pixels or typed words.
  `autoedit history status`, `autoedit history clear --yes`.
- **`autoedit style suggest`**: habits found in the history (12 photos, 3 days, 70 % by default,
  in `style.toml`), worded as a question; `--yes NAME` saves them into a new look.
- **A look can carry habits** (a `habit` table per scene and target), replayed as typed
  instructions within `[limits]`.

## [Unreleased] — Surprise me (F26)

### Added
- **Six looks you would not have picked**, drawn from the look library by a seed: a Surprise
  button in Studio, `"surprise": true` on `POST /api/variants`, and `--look surprise --seed N`
  (it prints the six and runs the batch with the first). The pick is a named look, so a batch can
  be repeated; the sidecar records the name, never the word "surprise".

## [Unreleased] — LUT files (F25)

A run without a LUT, or with an identity LUT, renders the same bytes as before.

### Added
- **`.cube` LUT files**: `--lut FILE.cube` and `--lut-amount N` (0 to 100). The LUT is laid over
  the finished picture (after the oversaturation gate, before the review check), with tetrahedral
  interpolation, so greys stay grey. The reader is strict: text only, 16 MB at most, known
  keywords only, finite numbers, the right number of rows. The sidecar names the file and its
  digest, never its folder. In an API request the file must lie in a picked folder.
- **`autoedit looks lut NAME -o FILE.cube`** writes the whole-picture colour part of a look as a
  33-point LUT for other programs; the file lists what it leaves out (region edits, the rules'
  per-photo decisions, clarity, sharpening, grain).

## [Unreleased] — Black and white, and film looks (F24)

A run with one of the five old looks renders the same bytes as before.

### Added
- **Black and white with a choice of how colours turn grey**: an op `bw` with six colour weights
  (red, yellow, green, cyan, blue, magenta). A weight lightens or darkens its colour, and never
  darkens the shadows. A full black and white export is exactly grey after every later step.
- **Eight looks**, chosen with `--look`: `bw_classic`, `bw_red_filter` (darker blue skies),
  `bw_soft`, `film_warm`, `film_cool`, `film_faded`, `cross_process`, `instant`. The film casts are
  mild by design: the oversaturation gate allows about 5 of 255 on neutral tones.
- A look can lift its blacks for a faded look (`dehaze_add`).

## [Unreleased] — Grain (F23)

A run without grain renders the same bytes as before.

### Added
- **Film grain**, off by default: `--grain N` (0 to 100) and `--grain-size`, a look's `grain`, or
  "add grain" / "more grain" / "grainy" in the instruction boxes. Luma only, strongest in the
  midtones, the same pattern on every rerun of a photo. It is added to the written picture after
  the resize, so a web export and a full-size export look alike, and after every check, so no
  gate mistakes it for noise or banding. Grain is for the whole photo: "add grain to the sky" is
  refused with a reason.

## [Unreleased] — Point and tell (F37)

A run without a mark or an instruction renders the same bytes as before.

### Added
- **Mark a spot and say what to change.** In Studio, the Mark button lets you drag an oval on the
  photo and type a sentence for it: "make this guy more purple", "brighten this a little". A
  sentence that names a thing edits that thing along its outline: SAM 3 finds everything with that
  name, and the oval picks what has its centre inside it. Any other sentence edits the oval itself,
  soft at the edge. Each mark is a chip with an "Oval only" switch, Undo and remove; marks are kept
  when the page reloads and are used by the batch.
- **"Show me how"** on every chip of "Edit this version": the mask, each tool with what was asked
  and what was applied, a short lesson on each tool, what was held back and why, and a switch that
  shows the photo without the change. The lesson texts are data in `configs/lessons.toml`.
- **The same without a browser:** `autoedit adjust PHOTO --mark X0,Y0,X1,Y1 --edit "…"`, with
  `--mark-kind auto|area` and `--explain`, which prints the walk-through. In the API the preview,
  run-one and jobs routes take `photo_marks`.
- **Skin and snow keep their limits inside a mark:** where the run found them as regions, and for a
  named thing also where it did not (a small or distant person). A circled glacier takes no colour.
- `scripts/eval_marks.py` measures what a mark finds on a file of hand-drawn boxes. On 51 boxes on
  16 photos, the name with the oval picking the instance was right on 29 of 37 things (a box
  prompt alone: 18) and gave no thing on 12 of 13 boxes that held only part of something bigger. A
  new mark on a photo that has had a preview costs 0.5 s of segmentation.

### Changed
- **Skin has a tint limit** (`tint = 6` in `[concept.skin] max`). A typed colour change on skin
  ("make the skin pinker") is held there; before, nothing limited it. No rule sets tint on a
  region, so a run without instructions renders as before.
- **Named places keep the limits of skin and snow too.** "Make the top pink" used to tint the snow
  and the skin under the gradient. Now those parts get the change after their policy and the rest
  gets it in full. "Darken the top" and "add a vignette" render as before.

### Fixed
- **A preview of a photo above 4096 pixels is analysed on the picture the export analyses.** It
  used to analyse a smaller copy, so a mask could differ between preview and export (measured: an
  overlap of 0.92 on a 22 megapixel photo; now 1.00). A single-photo export of a previewed photo
  now also finds its masks in memory. Exports themselves are unchanged.

## [Unreleased] — Faster segmentation (F36)

### Changed
- **SAM 3 reads each photo once.** The image encoder used to run once per concept. It now runs
  once per photo, and every concept is a light pass on its output. A photo with seven concepts
  takes 4.1 s to segment instead of 16.6 s (16 photos, Apple M1 Max). Every mask is identical to
  before, so the output does not change.
- **A thing named later on the same photo skips the encoder.** In Studio, a follow-up such as
  "brighten the jacket" spends 0.5 s in segmentation.
- **Less GPU memory held between photos on Apple Silicon:** 12.1 GB instead of 15.5 GB.

### Removed
- `kornia` and `colour-science` from the `accel` extra, and the `cli` extra (`typer`, `rich`).
  Nothing imported them, and `pip install '.[accel]'` no longer installs torch.

## [Unreleased] — Free-text instructions (F20)

### Added
- **Colour and size words:** "make the sky pinker", purple, golden, teal, green, bluer; "a bigger
  gradient", "a smaller vignette", "a wider glow".
- **A small model on your computer reads what the word list misses** (Ollama with
  `qwen3.5:9b`, `--translator auto|local|off`, a switch in Studio's settings). It fills in the same
  bounded form a typed instruction gives, for the run-wide instructions and for "Edit this
  version"; only the sentence is sent, and only to this computer. Without Ollama nothing changes.
  On 40 test sentences it read 24 of the 26 it was given correctly, and never turned a "don't" into
  an edit.

### Changed
- The cap on warming or cooling one target by instruction rose from 20 to 50 (colour words need it).

## [Unreleased] — Shaped light and follow-up edits (F18, F19)

### Added
- **Shapes by instruction:** `--edit` (and Studio) understand places, not only objects: "darken
  the top with a gradient", "brighten the center", "warm the left side", "add a vignette", "add a
  warm glow where the sun is". The sun is found by SAM 3; with no sun in the frame the glow is
  reported as not found and nothing changes.
- **Automatic sun glow** (`--light-shaping`, off by default): a soft warm lift around the sun.
- **New mask shapes:** gradients at any angle and a soft round glow (`edit/mask.py`).
- **Follow-up edits in Studio:** type a change under the preview and it is added on top of the
  edit; each change is a chip you can remove, with Undo and Start over; the list is kept when the
  page reloads and is used by the batch. A box "Instructions for every photo" applies to all.
- **"What it did"** in Studio: a window with everything the current preview did, step by step.

## [Unreleased] — Personal style and interface groundwork (F12 to F17)

A default run renders the same bytes as before.

### Added
- **Taste calibration** (`autoedit calibrate PHOTOS --out DIR`, then `--continue DIR`). The
  command picks a diverse handful of your photos and renders blind pairs that differ along one
  dimension of the look (brightness, depth, colour, warmth). You vote on a self-contained page;
  each round moves the look toward what you chose and uses a smaller step. The result is saved
  as a look in your profile (`--look calibrated`). The dimensions and step sizes are data in
  `configs/calibrate.toml`.
- **Model manager** (`autoedit models status | download | remove`): shows whether the SAM 3
  packages and weights are present and how much disk they use, fetches the weights on request,
  and removes them with `--yes`. Nothing is downloaded unless you run `download`.
- **Versioned API**: every JSON route of the local web app is also served under `/api/v1`, with
  one error shape (`{"error", "code"}`), `GET /api/v1/version`, and
  `GET /api/v1/jobs/{id}/events` as a server-sent event stream. The old `/api/...` paths keep
  working. The route list is pinned by a snapshot test.
- **More input formats**: AVIF (Pillow 11.3 or later), and HEIC and HEIF with the optional extra
  `pip install 'autoedit[heif]'`. Without the extra a HEIC photo fails by itself with a message
  that names the extra; the rest of the batch runs. `autoedit doctor` lists both.
- **Settings schema** (`autoedit settings`, `autoedit settings --json`,
  `GET /api/v1/settings/schema`): every run setting with its label, group, type, default, range
  or choices and a one-line help text, so that an interface can build its controls from it. The
  words live in `configs/settings.toml`; a test fails when a setting is added without them.
- `ARCHITECTURE.md`: a section "Adding a segmenter".

### Changed
- **LLM decider (optional, off by default, your own key).** The default model is
  `claude-haiku-5-5`. The request no longer sends `temperature` (current models reject it), uses
  a strict tool schema, and treats a refusal like any other failure: the rules decide. The budget
  cap counts the real token usage with two price settings (`[llm] input_usd_per_mtok`,
  `output_usd_per_mtok`) and now defaults to 1.00 USD per run. With the LLM decider active, a
  clause of `--edit` that the local parser did not understand is translated by the model into
  the same bounded structure the parser produces; only the text of that clause is sent.

## [Unreleased] — Workflow features (F8 to F11)

A default run renders the same bytes as before: every item below is a new option or command.

### Added
- **Export presets** (`--export NAME`, `autoedit exports`, `configs/exports.toml`): `full`, `web`,
  `social`, `archive` and `webp`. A preset sets the format, quality, long edge, file name pattern,
  subfolder and metadata mode in one word; a flag given with it wins over it.
- **Output options**: `--long-edge PIXELS` (never upscales), `--name "{stem}_{style}_{n}"`,
  `--subdir NAME`, and `--format webp|png` next to JPEG and TIFF. Every format carries the sRGB
  profile.
- **Shoot consistency for bursts** (`--consistency bursts`, `[run] consistency`). Shots taken
  within 30 seconds of each other that repeat one framing (same orientation, and thumbnails that
  match once the sky-over-ground layout is taken out) form a burst; a RAW file and its JPEG count
  as one shot. Each burst photo is decided once without rendering and then nudged toward the
  burst's median exposure and white balance, inside bounds (`[consistency]` in `rules.toml`). A
  nudge never cuts below what the rules decided and never pushes a frame into clipping. The
  default mode, `brightness`, is unchanged.
- **User config layer** (`--profile DIR`, `AUTOEDIT_PROFILE`, `~/.config/autoedit`). A profile
  folder holds TOML files with only your differences; they are merged key by key over the
  packaged files, so changing one value no longer means copying a whole file. With no profile
  nothing changes.
- **`autoedit looks list | save | delete`**: manage your own looks in the profile, for example
  `autoedit looks save warm --from natural --set vibrance_add=6`.
- **`backend = "auto"`** (`--backend auto`): SAM 3 when it is installed and its weights are in the
  local cache, the stub otherwise. `autoedit doctor` says which one that is. The default backend
  is unchanged.
- **Look flag `preserve_exposure`** in `looks.toml`: a look that is dark on purpose can opt out of
  the "dark photo made darker" review flag, like the "moody" prompt words do. No packaged look
  sets it.
- Sidecars record `wb_bias` and the new output settings; `autoedit adjust` re-renders a photo
  from a preset subfolder and reproduces burst nudges exactly.

### Security and safety (found in review, fixed before release)
- A file name pattern can only use the plain fields `{stem}`, `{style}` and `{n}`, and the filled
  name must be a plain file name. Before, a style name such as `../../x` together with
  `--name "{style}-{stem}"` wrote exports outside the output folder, also through a web request.
- A run refuses to write an export over a source photo: its own or another input of the run,
  compared by file identity, so that `IMG_1.JPG` on a case-insensitive disk and a sibling file of
  another format are covered too. The photo concerned fails with a message; the rest of the
  batch runs.
- A subfolder that is a symlink leading out of the output folder is refused.
- The LLM instruction translation never receives a clause that the local parser rejected as a
  negation or an exclusion ("don't brighten the face"), and a value from the model that is not a
  finite number is dropped.
- `autoedit adjust` finds its run folder from the subfolder recorded in the sidecar instead of
  guessing from the parent folder, which could re-render another run's photo over an export.
- A profile look with a value that is not a finite number is rejected; it rendered a black image.
- `backend = "auto"` only chooses SAM 3 when the installed Transformers has it and the weights
  file is in the cache; before, it could choose a backend that then failed on every photo.
- An explicit value in a run file wins over an export preset; `--export ""` switches a preset off.

### Changed
- `tests/run_all.py`: the stand-in used when pytest is not installed now also provides
  `pytest.approx` and `.value` on `pytest.raises(...) as e`, and tests never read a developer's
  own profile.

## [Unreleased] — Image quality, three opt-in features (F5, F6, F7)

Each of these changes how a photo looks, so each ships switched off and waits for a blind vote.
A default run renders the same bytes as before.

### Added
- **Black and white point** (`--levels`, `[run] levels`). A new op pair `black_point` and
  `white_point`: the op measures where the darkest and brightest tones of the frame sit after the
  lifts and pulls them toward a true black and a clean white. It reshapes only the darkest and the
  brightest tenth of the frame, so the body of the picture does not move and a dark photo cannot
  come out darker; it adds no clipping. The effect is small (the black floor drops by 6 to 10
  levels of 255 on washed frames). A rule drives the black point; the white point is available to
  `autoedit adjust`.
- **Highlight hold** (`--highlight-hold`, `[run] highlight_hold`). Where the source is near white
  in all three channels, the edit may not darken the pixel, with a smooth ramp from 0.80 to 0.985.
  A blown sky stays white instead of turning light grey (measured: 231 to 254 of 255).
- **Noise reduction** (`--denoise`, `[run] denoise`). A new op `denoise`, chroma first, placed
  before sharpening; a rule scales it with ISO (from 800, full from 6400) and with the exposure
  lift. Both of its filters follow the grain measured on the frame, so a clean frame keeps its
  fine detail. Measured at ISO 12,800 on a RAW file: colour noise in dark flat areas 24.7 to 0.1 levels,
  luma noise 17.9 to 7.5 (the level of the source).
- Opt-in rule sets in `rules.toml`: `[opt_in.NAME.<scene>.<block>]` tables join the rules only
  when the run setting NAME is on, so a feature that is off cannot leak into a tool that reads
  the rules file directly.
- `scripts/tone_report.py --set KEY=VALUE`: run the tone gate with any run setting switched on.

### Changed
- With `denoise` active, the oversaturation gate compares the edit with the denoised source.
  Against the raw source it stamped the source's colour noise back onto the result.

### Known limits
- With the highlight hold on, a frame whose source is more than 5 % blown is flagged for review:
  the review gate counts clipping that the source already had.
- Noise reduction keys on the EXIF ISO. A file without it gets none from the rule.

## [Unreleased] — Throughput (F4)

### Added
- Per-pixel work runs on row strips in threads (`edit/par.py`). A 22 MP render went from 18 to
  24 s down to 8 to 11 s on the development machine, with byte-identical output.
- `--threads N` (`[performance] threads`, `AUTOEDIT_THREADS`): threads inside one render; 0, the
  default, is one per core.
- `--workers N` (`[performance] workers`): render processes for a batch; 0 picks half the cores,
  at most 4. The default stays 1. With a model backend the parent segments each photo, one at a
  time, into the mask cache, and the workers render from it without loading the model. Eight
  22 MP photos: 136 s on one core, 70 s with threads, 44 s with two workers, 33 s with four.
- `ops.STEPS`: the frozen op order as data.

### Changed
- The guarded global pass (skin or snow guard) computes the steps before the first guarded op
  once instead of twice. Same pixels.
- `run.json` records `workers`.

## [Unreleased] — Per-photo adjustments (F3)

### Added
- `autoedit adjust <photo-or-sidecar> --set sky.exposure=-0.3 --edit "warm the grass a little"`:
  renders one photo again with the settings of its run plus its own adjustments. `--reset` drops
  them. A re-render without adjustments gives the bytes of the batch output.
- A mask cache on disk (`<out>/.autoedit-masks/`, every backend except the stub; `--no-mask-cache`
  or `[segment] mask_cache = false` turns it off): `adjust`, a resumed batch and a later run with
  another style do not segment again. The cache is never unpickled.
- Each sidecar records its run settings (`audit.run_config`, without file paths and without the
  API key), the shoot-consistency nudge (`exposure_bias`), the exported file name and a digest of
  the config files. Per-photo adjustments are stored with `scope: "photo"`.

### Changed
- `emit.write_outputs` replaces three copies of the write-image, note, write-sidecar sequence.
- Every sidecar has an `adjustments` list, empty when nothing was directed.

### Known limits
- `adjust` finds the source photo through `run.json` in the output folder, or takes `--source`.
  A sidecar never stores a folder path.
- A run that used the LLM decider needs `--allow-cloud` for a re-render, and the model is asked
  again, so the result can differ from the batch.
- A full re-run renders every photo from the run settings; `--resume` keeps adjusted photos.

## [Unreleased] — Region instructions (F2)

### Added
- `--edit "brighten the jacket, darken the sky slightly"` (`[run] edit` in a run file, `edit` in the
  Studio API): plain-language instructions for named regions, parsed locally with no LLM. Any
  concept the segmenter can find may be named, including one that has no rule block.
- `configs/instructions.toml`: the vocabulary as data. Eight verbs (brighten, darken, warm, cool,
  saturate, mute, soften, deepen) with their word forms, amount words (slightly, a little, much),
  and a limit per op.
- An instruction is a delta on top of the rule baseline. A named thing inside another region (a
  jacket inside `subject`) keeps that region's settings and gets the delta on top. No pixel is
  edited by two regions, and region policies apply to the sum.
- The sidecar lists every instruction under `adjustments` with a status (`applied`, `clamped`,
  `not_found`) and one line in `notes`. A clause that was not understood is named, never guessed.

### Changed
- `paths.config_path` falls back to the packaged file when the active configs folder does not
  have it, so a local tuning copy made before a feature added a config file keeps working.

### Known limits
- The grammar is small: verb, optional amount, target, joined by "and" or commas. A negation
  ("don't brighten the sky") is rejected.
- Each instructed region costs one more full-frame pass at apply time.
- A region edit runs before the global pass, so a global op that measures the whole frame
  (dehaze) can shift far pixels by a small amount.
- Without SAM 3 the stub segmenter finds a handful of concepts; everything else is reported as
  not found.

## [Unreleased] — Export keeps camera data (F1)

### Added
- Edited files carry the source photo's EXIF (capture time, camera, lens, exposure settings,
  artist, copyright) and an embedded sRGB ICC profile. Before, an export had neither.
- Run setting `metadata` (`--metadata`, `[output] metadata` in `defaults.toml`, `metadata` in the
  Studio API): `keep`, `shareable` (the default: the GPS block and the serial numbers of the camera
  and the lens are removed) or `none` (no EXIF).
- Under `keep` and `shareable`, each sidecar's `notes` records what the export did with the
  metadata.

### Changed
- In the copied EXIF, orientation is reset to upright, pixel size and colour space describe the
  exported file, and `Software` names AutoEdit. The maker note and the embedded thumbnail are not
  copied.

### Known limits
- `shareable` keeps the names the photographer entered: the artist, the copyright and an owner name
  set in the camera. `none` writes no camera data at all.
- A 16-bit TIFF carries the profile and the basic tags only (camera, capture time, artist,
  copyright). XMP and IPTC blocks (ratings, keywords, captions) are not copied.

## [Unreleased] — Screenshot loop: depth without haze, neutral colour

Eight changes, each judged blind on a frozen 16-photo panel through the real pipeline with the
SAM 3 backend (three more were tried and rejected). Every rule has a comment in the TOML and a
golden test. The tone report on the 110-RAW Dolomites corpus stays at 0 dark→darker, 0 pushed
into clipping.

### Added
- `shadows_ev` edit op (`edit/ops.py::shadow_lift`): a black-pinned shadow lift in stops, a
  bounded gain that fades to zero at mid-grey. Inert unless a rules file sets it.
- Look fields `curve_mul` and `exposure_mul` (default 1.0): a look's tonal mood can now scale
  rule values that already carry the safety fades.
- `scripts/contact_sheet.py --panel`: blind A/B contact sheets of two configs dirs (Original |
  A | B | Reference, random side per row, key in a separate JSON), 100% crop sheets, blind-safe
  `facts.txt`, and `--tally` to score written verdicts.

### Changed
- **Black anchor.** The additive `shadows` lift is paired with an equal negative `blacks`, so
  opening the shadows no longer raises the black floor into a grey veil. The older "deepen
  washed blacks" term only acts on frames without a dark tail.
- **Dark foregrounds** the highlight headroom keeps `exposure` from reaching are opened by
  `shadows_ev` (half of the denied exposure, up to 1.5 stops).
- **Depth follows the lift.** A `curve` rule scales the filmic curve with the exposure actually
  spent, on frames that have no deep shadows of their own.
- **Highlight pull-downs capped** (global highlights ≥ −10, whites ≥ −4; snow region −4…−10 /
  −4…0): white snow and overcast sky are no longer flattened to grey.
- **No unrequested cool grade.** The auto palette no longer infers `cool_blue`
  (`[auto.cool_blue] warmth_veto = -1.0`); `--palette cool_blue` and prompt cues still work.
- **Regions cannot become patches.** Regional exposure lifts are capped at +0.15 EV, the
  mountain region's additive shadow lift is gone, its dehaze fades on dark regions and its local
  contrast uses the end-pinned curve.
- **Output sharpening halved** and no longer repeated per region.
- **`cinematic_preserved`** is colour-neutral (no cool shadow grade, no saturation push, no extra
  dehaze) and takes its depth from `curve_mul`.

### Known limits
- Within the darkness gate, `cinematic_preserved` differs visibly from `natural` only on photos
  the rules lift. A mood darker than the source needs the gate's opt-out.
- Washed, headroom-limited frames still lack a true black point; that needs a tone-curve op.
- RAW output still sits about 1.3 EV under the camera's own render (`raw_mode = "none"`).

## [Unreleased] — Auditable autonomous quality workflow

### Added
- Recursive capture selection, immutable source/artifact provenance, isolated errors,
  bounded safety fallback, native export assessment, and strict resume in
  `scripts/quality_workflow.py`.
- Shared source-relative RGB/luminance clipping, texture, color, gradient, edge and
  finite-pixel assessment. Missing measurements remain unavailable. Observational
  intermediate-stage audits expose damage hidden by later tone operations.
- Separate development-first and bounded-optimization experiments, six RAW controls,
  fixed-mask component comparisons, frozen tuning/holdout protocols, and shard merging.
- Two-arm blind galleries with candidate-specific decode, opaque identities,
  independent random streams, explicit photo ties, strict vote validation and exact binomial scoring.

### Changed
- Preview-derived experimental RAW development falls back to zero lift with an
  explicit reason; RGB channels determine available headroom. JPEG/TIFF decoding
  and production rendering order are preserved.
- Strict tone reports now fail on added RGB or shadow clipping, invalid exports,
  incomplete sidecars, and source/artifact hash failures.

### Fixed
- Preserve accepted resume provenance across setup failures, interruptions and transient verification errors without weakening artifact hash checks.
- Reject malformed/nonfinite safety configuration instead of allowing invalid budgets.
- Snapshot per-image options and preserve fixed-mask safety separately from resegmented checks.
- Record actual backend, optional runtime dependencies, and encoding-only remediation reasons.

See [the workflow guide](docs/QUALITY_WORKFLOW.md) for commands, migration and rollback.
The current production default remains in place; numerical gains are not evidence
of aesthetic superiority.

## Prior unreleased validation — Shared highlight budget

### Fixed
- White-point lifts now use highlight headroom remaining after the planned exposure adjustment,
  including intensity. The rules evaluator makes `planned_exposure` available before later rules
  regardless of TOML key order, with an independent value per global/region block. This fixes the
  neutral-frame regression introduced by the highlight knee while preserving its foreground lift.
- `tone_report.py --fail-on-regression` now rejects partial reports. Failed renders, missing or
  invalid sidecars and measurement errors stay isolated but are counted and named in the report.

### Added
- Tone rows retain any-channel RGB clipping changes and review reasons alongside luminance metrics.
- Blind galleries record public configuration, config/source hashes, backend metadata (revision
  when exposed by the runtime) and
  pipeline sidecars with mask-aware safety results. JPEG quality follows the resolved config
  (95 by default) and supports `--quality`. Whole-frame comparison metrics and blind shuffling remain.

### Validation and remaining issues
- 322 tests pass; stub CLI smoke emits a JPEG, sidecar and manifest. Full 110-RAW stub report
  is complete with 0 dark→darker and 0 luminance-clipping regressions; three separate RGB
  review flags remain. Real SAM checks confirm two of those RGB flags (0008 and 0042).
- Thirteen SAM risk cases and four native exports pass the tone gates with no review flags.
  A fresh 24-photo/72-variant baseline/camera/AutoEdit gallery also passes both AutoEdit tone
  gates and RGB review, with verified source/config hashes and 23 burst groups. These sets overlap.
- Native crop inspection confirms a remaining defect: regional exposure can flatten bright
  snow texture before global rolloff moves it below the clipping threshold. A region-only knee
  tune did not preserve useful 8-bit highlight amplitude and was not adopted. Reproduction,
  crops and exact results are under `out-validation-2026-09-06/`; no new human rankings exist.

## [Unreleased] — Highlight detail and clipping review

### Fixed
- Highlight headroom now measures the 99.5th percentile of per-pixel max(R,G,B), protecting
  individual color channels that the previous luminance estimate could miss. Both positive
  white-point terms use the existing headroom guard; negative highlight recovery stays active.
- Global contrast now fades with highlight headroom in every scene. This prevents the finishing
  pass from consuming the headroom protected by the exposure rules on bright snow/backgrounds.
  Full contrast needs one stop of headroom; legacy stats retain their previous settings.
- New clipping above a configurable 0.5 percentage-point budget triggers review regardless of
  the overall quality score or absolute clipping fraction. Both luminance and any-channel RGB
  clipping are compared with the source, with before/after values in the warning.
- Regression coverage includes contrast goldens across scenes, a bright-tail image through the
  full finishing stack, and a deliberately clipping recipe routed into the review queue.

### Validation
- 307 tests passed; stub CLI smoke emitted images, sidecars and a run manifest.
- All 110 Dolomites RAWs rendered with the default natural recipe, RAW mode `none` and stub
  backend at 1024px: 0 dark→darker and 0 pushed-into-clipping (previously 0 and 4).
  The four original failures also pass RGB clipping review; one other photo remains flagged
  by the stricter RGB check.

## [Unreleased] — Studio region controls and preview fidelity

### Fixed
- Studio sends added/enabled concepts to preview and batch, preserving disabled selections across
  photos. An explicit empty concept list disables region editing instead of restoring defaults.
- Masks displays actual arbitrated region footprints from the preview's segmentation pass, with
  transparent backgrounds and colors matching the concept controls. Stale masks are cleared.
- Preview and variants honor the same RAW development options as export; Studio exposes the
  mode in Settings. Segmentation cache keys include proxy pixels so changing development cannot
  reuse stale masks. The default RAW mode and tone rules are unchanged.
- “None” selects zero photos and disables Run. An explicit empty API selection is rejected before
  a job or output folder is created; omitting `files` still scans the input folder.

## [Unreleased] — Dark-RAW diagnosis: objective tone gate + EV-correct exposure

A 204-photo two-way blind test on Dolomites CR2s left dark frames dark or darker. Reproduced on the
in-repo 110-pair corpus (`curated-dolomites-summer-2026/selected_raw` + camera JPGs): the libraw
render AutoEdit starts from sits a median **1.4–1.8 EV under the camera's own embedded preview on
every file**; the exposure rule then treated a luma *difference* as stops (0.3 EV asked where ≈3 EV
was the deficit), shadows only responded to clipped pixels, and contrast/blacks (pivot 0.5) erased
the little lift that was applied. The quality gate scored a dark→darker edit 0.94. See
`PROJECT_ASSESSMENT.md`.

### Added
- `scripts/tone_report.py` + `autoedit/tonemetrics.py`: objective pass/fail numbers for any folder —
  mean/percentile luma, sky-excluded foreground, top/middle/bottom bands, dark-pixel coverage,
  clipping, distance to the camera render, and two flags: **dark photo made darker** and **bright
  photo pushed into clipping**. Reads sidecars (`--out`) or renders in memory (`--render`);
  `--fail-on-regression` makes it an unattended gate.
- New stats (`stats.py`/`contract.py`): `lum_p99`, `headroom_ev` (stops before the 99.5th
  percentile clips), `dark_cov`, `band_top/mid/bottom_p50`; post-edit **regional** stats and
  `decode_ev` in the sidecar, plus a `name: luma a → b` note per region.
- **RAW development candidates** (`imageio.RAW_MODES`, `defaults.toml [decode]`, `--raw-mode`):
  `none` (unchanged default) | `libraw_auto` | `preview_ev` (match the embedded camera preview's
  linear median) | `preview_ev_headroom` (same, capped so ≤ clip_budget reaches white) |
  `tone_target`. `imageio.camera_preview()` exposes the camera render every RAW carries.
- `scripts/raw_benchmark.py`: ranks the development candidates over a RAW folder against the camera
  preview (median |EV gap|, clip violations, dark-scene recovery, per-category consistency,
  outliers, optional pipeline regressions) and names a recommendation — it never flips the default.
- Rules sandbox: safe `log2` / `log`. Blind-test harness: `camera` arm (`--vs-camera`),
  `--stratify N` by source-brightness tercile, burst grouping with per-group significance,
  `metrics.csv` per gallery, `--configs-a/--configs-b`, and a sample-size (power) line.
- Darkness-regression safety gate (`gates.assess`, `rules.toml [quality_gate]`): a dark source made
  materially darker is flagged for review unless the prompt asks for dark (`preserve_terms`).
  `compose.toml` `min_coverage` (a 3% sliver of sky no longer earns a half-frame grad).

### Added (follow-up pass)
- **Highlight knee** op `highlight_rolloff` (`edit/ops.py`): a soft shoulder in linear light whose
  asymptote is the clip level, applied inside the exposure pass so a lift compresses into white
  instead of clipping. Rule: strength rises as `headroom_ev` falls; the exposure cap gains up to
  0.3 EV when the knee is on. Measured on the highlight-risk subset: −0.16 EV closer to the
  camera render, no new dark→darker, ≤ 1 marginal clip flag.
- `edit/filters.py`: OpenCV fast path for box blur / erosion when the `accel` extra is installed
  (identical numerics to ~5e-6; a 22 MP stub run went 28 s → 21 s). numpy stays the reference.
- `apply_ops` runs white balance → exposure → knee as one linear pass (`ops.light`); the
  standalone `white_balance`/`exposure` ops remain.
- README: first-run SAM 3 notes (gated weights, `huggingface-cli login`, MPS/CUDA/CPU, rawpy).

### Removed
- `emit = "editable" | "both"`: accepted but never implemented (no XMP hand-off exists). Only
  `baked` remains; `--emit editable` now fails at argument parsing instead of silently writing
  nothing.

### Changed
- `rules.toml` exposure is **EV-correct**: `0.6·2.2·log2(target/fg_p50)`, capped by `headroom_ev`
  (landscape/other/people; `other` keeps the subject-first priority). Shadows respond to the
  foreground-median deficit and dark coverage, not only to clipped pixels. Contrast and blacks fade
  to zero on dark sources. Mountain/grass/foliage region lifts are EV-correct and relative to the
  foreground median so they compose with the global lift. Legacy zero-filled stats reproduce the old
  output exactly (all new terms sit behind `pro_stats_valid`).
- Tests: 311 (was 265), including per-op proof that no tone op erases a dark-frame lift and
  knee math / routing / rule goldens.

## [Unreleased] — Pro-tone retune (blind-test-driven)

Two blind tests on real photos (see `scripts/blindtest.py`) established the diagnosis: the engine
architecture was sound (AutoEdit beat a competent global-auto baseline 10/12 on hard blown-sky
photos, p≈0.02), but the *policy* was timid and one auto-decision was actively wrong — the auto
`cool_blue` palette fired at 72–85% strength on scenes with measured positive warmth, fighting the
photo's actual light. On easy, evenly-lit photos the engine was correct but visually indistinguishable
from a decent auto — "they look identical."

### Added
- `scripts/blindtest.py --rules-a/--rules-b`: config-A-vs-config-B blind mode (shared cached
  segmenter, one SAM pass per photo) — the instrument every rule change below was measured
  through. `score()` now reports an exact binomial p-value and derives method names from the
  manifest instead of a hardcoded 3-way constant. Galleries with more than two methods require
  a predeclared `--pair METHOD_A METHOD_B`; significance output explicitly states its independent-
  photo assumption.
- **New stats** (`stats.py`/`contract.py`): `fg_lum`/`fg_p50` (sky-excluded foreground tone —
  calibrated at ~0.28 vs a sky-skewed `mean_lum` of ~0.6 on real alpine photos); `veil` (a true
  atmospheric-haze estimate — requires lifted-dark-channel *and* smooth *and* desaturated *and*
  not-near-white, so bright snow/high-key scenes score near 0 where the legacy dark-channel
  `haze` stat reads 0.5–0.7); `detail_rms`; `dark_p10`; `snow_cov`/`sky_cov`/`water_cov`;
  `wb_temp_correction`/`wb_tint_correction`/`wb_confidence` (region-aware white-balance signal),
  plus `pro_stats_valid`/`subj_cov` availability signals that distinguish absent stats from a
  legitimate measured zero.
- **Region-aware auto white balance**: measures the colour cast on a natural gray card — SAM's
  `snow` region when present (full confidence), else bright near-neutral pixels (reduced
  confidence). The correction is asymmetric and warmth-preserving: up to +12 warm for a
  blue/overcast cast, floored at −5 cool, fading to zero as the scene's measured warmth rises —
  golden-hour light is read as a subject, never neutralized as a cast.

### Changed
- **Auto-palette restraint**: the automatic `cool_blue` grade now has a hard warmth veto (skipped
  outright at measured warmth ≥0.008) and every inferred grade is capped end-to-end, even when
  global intensity exceeds 1 (cool_blue ≤40%, others ≤55%) — an unrequested color decision should
  whisper, never fight the photo.
- **Percentile-driven tone policy** ("visibly finished," not just corrected): exposure now targets
  the sky-excluded foreground median; a deliberate black/white point is set from measured
  percentiles on washed/flat frames; contrast adapts to the measured tonal spread; clarity adapts
  to measured local detail; `natural`'s filmic curve strengthened (2.0 → 5.0). Every new expression
  is guarded by the explicit `pro_stats_valid` marker, so legitimate zero-valued measurements use
  the new policy while a sidecar or config lacking the new stats reproduces the previous output.
- 255 dependency-free tests (was 231).

## [0.2.0] — Unreleased — Pivot to standalone open-vocabulary editor

Research established that segmentation-driven auto-masking is already commoditized (Lightroom
Adaptive Presets + Imagen/Aftershoot/Evoto), with **open-vocabulary** region targeting the
only unoccupied lane. Since SAM masks can't enter Lightroom, the product became a standalone
Python tool. Full rationale + design in the approved plan
(`~/.claude/plans/linked-mixing-engelbart.md`).

### Added
- Python package `autoedit` (`src/autoedit/`): decode → SAM 3 segment → resolve (priority
  arbitration, absence gating) → per-region stats → scene → decide → apply → emit.
- Backends behind a `Segmenter` protocol: `StubSegmenter` (heuristic, GPU-free, runs now)
  and `Sam3Segmenter` (real open-vocab, `[sam]` extra, lazy torch).
- Decision layer: `RulesDecider` (sandboxed-AST rule evaluator) + `LlmDecider` (Claude
  structured tool-use, refine/propose, cost caps, graceful fallback to rules).
- Pixel apply engine (`edit/`): WB→exposure→tone→contrast→dehaze→clarity→vibrance, guided
  mask feather, priority compositing, identity no-op.
- `autoedit` CLI (`run` / `doctor` / `presets`), batch orchestrator with per-image error
  isolation + `--resume` + run manifest, sidecar JSON with "why" notes.
- Config-as-data (rules, looks, concepts, defaults); zero-dependency test runner
  (`tests/run_all.py`); Python CI.
- **Styles + brief** (`styles.toml`, `--style`/`--prompt`): pick a look + describe intent
  instead of training on a catalog; brief threaded to the LLM decider.
- **Adjustment vocabulary**: filmic curve, targeted HSL (skin/green/blue), color grading
  (split-tone); styles inject global curve + grade.
- **Compositional masks** (`compose.toml`, `edit/mask.py`): radial vignette + linear sky
  grad placed by reasoning about the scene; `--no-compose`.
- **BYO-key cloud consent**: the LLM path is gated behind `--allow-cloud` + a key, with a
  disclosure and transparent fallback to local rules.
- **Local web app** (`autoedit ui`, `[ui]` extra): FastAPI backend + browser UI over the
  batch engine (pick folder → style → brief → before/after preview → run).
- **Packaging**: configs bundled in the package (`paths.py` resolver) so `pip install
  autoedit` works anywhere; PyPI release workflow; `CONTRIBUTING.md`; refreshed issue
  templates + `FUNDING.yml`. 40 tests passing.

### Changed
- Preview before/after ships as **lossless WebP** (pixel-exact like PNG — the anti-banding
  dither survives — at a much smaller payload); previews render at full resolution (≤4096px).
- `/api/pick` is now **POST** (it registers a trust-boundary root, so it must sit behind the
  origin guard). Quality-gate thresholds moved to data (`rules.toml [quality_gate]`); default
  JPEG export quality aligned at 95 everywhere (preserves the dither).
- Analysis proxies and the dehaze working image resize in **float** (no uint8+dither
  round-trip), so 16-bit sources keep precision through stats.
- New `--no-consistent` flag (CLI parity with the UI's batch-consistency toggle); `doctor`
  prints the resolved configs dir; CLI `train-aesthetic` delegates to `critic.train_from_csv`.

### Fixed (full-codebase review, 2026-07)
- **Safety ordering**: region policies (forbidden ops / caps) are re-enforced after the
  reference tuner — a warm reference can no longer re-introduce e.g. `temp` on snow.
- `[guard].max_chroma_increase` sat inside `[banding]`, so the documented oversaturation
  knob was dead (defaults coincidentally matched). Moved where the engine reads it.
- TIFFs decoded via tifffile now honor the EXIF orientation tag; RAW files carry their
  ISO/exposure-bias EXIF priors; CMYK-with-ICC converts through the correct profile.
- Same-stem sources (`IMG_001.dng` + `IMG_001.jpg`, cross-folder files) no longer silently
  overwrite each other's outputs, sidecars, or dataset-workspace files; image/sidecar/label
  writes are **atomic** (`os.replace`) so a crash never leaves truncated files `--resume` trusts.
- Dataset pairing is **burst-safe**: exact stems match before edit-suffix stripping, so
  `IMG_1-2.jpg` (a different shot) pairs with its own target; bare `copy` no longer mangles stems.
- Review labels survive crashes and concurrent saves (lock + atomic JSON + recovery from the
  append-only CSV). The sidecar reports banding-damped settings that actually rendered; the
  reference image decodes once per run instead of 2-3× per photo.
- Background jobs: controls are no-ops on finished jobs, empty inputs error instead of a
  silent 0-item "done", the registry is capped; curate/critic results count decode failures.
- Classic UI picker updated for the POST route; `compose.toml` typos in `shape`/`from` fail
  loudly; `RunConfig` validates enums/ranges.

### Security (review hardening)
- **Host-header check on every request** (DNS-rebinding defense) alongside the existing
  localhost Origin check on mutations; thumbnail `px` clamped so `/api/thumb` can't serve
  full-resolution images; default output paths resolve inside approved roots *before* the
  trust-boundary check; empty paths fail closed; `api_key` is excluded from every
  serialization path (sidecars, manifests, `model_dump`, `repr`).
- New attack-shaped tests: `..` traversal, symlink escape, foreign-Host/cross-origin
  requests, secret-leak, LLM merge clamping. Suite grew 188 → 231 tests.

### Removed
- Rust engine (`engine/`), Lua Lightroom plugin (`plugin/`), duplicated `rules/`, Rust
  scripts and release workflow — superseded by the standalone Python design.

### Notes
- `PLAN.md` / `OVERVIEW.md` describe the prior Lightroom-plugin product (v0.1) and are kept
  for history; see `README.md` for the current product.

<!--
Per-release template:

## [0.1.0] - YYYY-MM-DD
### Added
### Changed
### Fixed
-->
