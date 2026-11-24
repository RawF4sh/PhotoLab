# AutoEdit — Complete App Guide

AutoEdit is a **local-first, open-vocabulary batch photo editor** with a full photography workflow
built around it: cull a shoot, edit it, review/label the results, build a training set, and learn
your own look — all on your machine. The editing is **non-generative**: it only moves bounded,
explainable photographic sliders (exposure, tone, white balance, dehaze, region grades…). It never
invents or replaces content.

- **UI:** one local interface — `autoedit ui` in the browser at `http://127.0.0.1:8765/`, or `autoedit app` in a desktop window.
- **CLI:** `autoedit run/curate/dataset/rank-variants/train-recipe/train-aesthetic/…`.
- **Core deps are tiny** (numpy, Pillow, pydantic + stdlib). torch/SAM, Anthropic, rawpy, tifffile
  are optional extras, imported lazily — the default "stub" pipeline runs anywhere with no GPU/network.

> Companion doc: [ARCHITECTURE.md](ARCHITECTURE.md) covers the editing engine in depth. This guide
> covers the **whole app**, including the workflow and learning subsystems.

---

## 1. Core principles

| Principle | What it means |
|---|---|
| **Local-first** | Automatic editing and both local editors work on-device. The optional LLM decider and Gemini assistant can send a thumbnail and measurements with consent. |
| **Non-generative** | No object generation, inpainting, sky replacement, or invented pixels. The system only adjusts bounded parametric edits, then re-measures and rejects unsafe results. |
| **Explainable** | Every photo gets a sidecar JSON with a human-readable "why" list: which rules fired, which gates acted, which intent/palette/reference applied. |
| **Copy-only** | Culling and dataset building never move, rename, or delete your originals — they copy. |
| **Rules are data** | Tuning lives in TOML (`configs/`), not Python. Add a stat in code, map it in config. |
| **Degrades safely** | Missing model / no API key / any failure → falls back to the rules baseline. Identity edit is a strict no-op. |

---

## 2. The workflow loop

The app is organized around one loop: **Cull → Preview/Edit → Review → Dataset → Learn → (repeat)**.
Each stage has a UI screen, a CLI command, and a web API.

```
  ┌── Cull ──────┐   pick keepers from a big shoot (diverse / best / aesthetic)
  │              ▼
  │      Preview / Edit ──── style · intensity · intent · palette · reference · learned
  │              ▼
  │      Run batch ───────── edit the whole (or a chosen subset of the) folder
  │              ▼
  │      Review / Label ──── keep/reject + tags (too dark, banding, good style…)
  │              ▼
  │      Dataset build ───── pair originals↔edited targets, preservation-validate
  │              ▼
  │      Train ───────────── aesthetic critic (scorer) + recipe predictor (editor)
  │              ▼
  └──── Learned edit / Variants ─── predict recipes, rank variants, auto-pick best
```

Everything the loop produces (culled subsets, labels, datasets, models, variant rankings) is a plain
file on disk (CSV / JSON / folders), so it's inspectable and portable.

---

## 3. The editing engine (how one photo is edited)

One fixed pipeline, defined in [`pipeline.py`](src/autoedit/pipeline.py):

```
decode → segment → resolve → stats → scene → decide → apply → emit
```

| Stage | Module | Does |
|---|---|---|
| **decode** | `imageio.py` | Load to float32 RGB [0,1] sRGB. RAW (rawpy, with EXIF priors), 16-bit TIFF (tifffile), AVIF, HEIC/HEIF (optional `heif` extra), ICC→sRGB (incl. CMYK profiles), EXIF orientation honored on every path. |
| **segment** | `segment/` | SAM 3 (or stub) → one mask per **named concept**. Per-concept **cache** so style switches don't re-run SAM. |
| **resolve** | `segment/resolve.py` | Gate weak detections; arbitrate overlaps into a non-overlapping label map (no pixel edited twice). |
| **stats** | `stats.py` | Per-region + global stats: brightness, saturation, clipping, haze, **luminance percentiles**, **OKLab chroma/warmth**. |
| **scene** | `scene.py` | Classify `landscape` / `people` / `other`. |
| **decide** | `decide/` | Stats → settings. `rules` (default), `llm` (opt-in cloud), or **`learned`** (recipe predictor). |
| **apply** | `edit/` | Composite region edits → global finishing → palette → safety gates, at **full resolution**. |
| **emit** | `emit.py` | Baked image (JPEG, 16-bit TIFF, WebP or PNG) + sidecar JSON. |

**Analyze on a proxy, apply at full res.** Masks/stats are computed on a downscaled proxy; edits are
applied at native resolution with edge-aware (guided-filter) mask feathering.

### The look controls (what shapes an edit)

- **Style** (`styles.toml`) — a bundle: a *look* (magnitude profile) + default concepts + an LLM brief.
  Built-ins: `natural` (default, true-to-life), `editorial`, `warm_film`, `dramatic`, `cinematic_preserved`.
- **Intensity** — global scale (Subtle / Balanced / Strong).
- **Look intent** (`intent.py`, `intents.toml`) — free text ("warm sunset, moody") → bounded global
  nudges, applied **locally, no cloud**. Type any of: warm, sunset, golden, moody, airy, vibrant,
  muted, teal, cinematic, soft, crisp, black & white, …
- **Palette** (`palette.py`, `edit/palette.py`, `palettes.toml`) — a **regional split-tone in OKLab**:
  shadow/mid/highlight hue+chroma targets, **luminance-preserving** (moves color, not brightness),
  with **neutral protection** (whites/snow stay clean) and gamut-safe roll-off (no posterized skies).
  Palettes: `cool_blue`, `teal_orange`, `warm_gold`, `forest_olive`, `muted_film`. "Auto" infers from
  the intent text or, failing that, from image stats (`auto_palette_for_image`). An explicit pick
  (typed cue or the Palette dropdown) applies at full configured strength; an *inferred* grade is
  deliberately restrained — capped at 55%. `cool_blue` is **never inferred** by default: an
  unrequested cool grade made overcast scenes bluer than their originals, so it now needs an
  explicit pick or a cue such as "cool blue" / "blue hour" (re-enable the inference per configs
  dir with `[auto.cool_blue] warmth_veto = 0.008` in `palettes.toml`).
- **Auto white balance** (region-aware) — measures the colour cast on a natural gray card: SAM's
  `snow` region when present, else bright near-neutral pixels. Corrects blue/overcast casts up to
  a bounded amount; the correction fades to zero as the scene's measured warmth rises, so
  golden-hour light is read as a subject, never neutralized as a cast.
- **Highlight headroom** — exposure, global contrast and positive whites use the measured
  near-white tail of each pixel's brightest RGB channel, so luminance cannot hide channel clipping.
  Contrast fades below one stop of headroom so bright snow, clouds and backgrounds keep detail
  through the finishing pass. Positive whites use the headroom remaining after the planned
  exposure lift, including intensity, so both adjustments share one budget. This rule applies
  to people, landscape and other scenes; negative highlight recovery stays active.
- **Reference** (§4) and **Learned mode** (§7).

### Edit ops (frozen order, `edit/ops.py`)

`white balance → exposure (+ highlight knee) → shadow lift → tone → contrast → curve → dehaze →
clarity → black/white point → vibrance/saturation → targeted HSL → color grade → black and white →
denoise → sharpen → (lut) → (grain)`. A LUT (`--lut FILE.cube`) is laid over the finished picture after
the gates; grain is added at export; `apply_ops` renders neither.
An empty settings dict is a strict no-op. Per-pixel work runs on row strips in threads
(`edit/par.py`) with identical output. Notable:

- **Shadow lift** (`shadows_ev`) — opens dark foregrounds by up to N stops with black pinned
  (a bounded gain that fades to nothing at mid-grey), so a dark scene is lifted without a grey veil.

- **Real dehaze** — physical dark-channel prior (He et al.): estimate airlight, recover transmission,
  refine edge-aware with the guided filter, invert the scattering model, blend by amount. Tapers on
  smooth bright gradients so foggy skies don't band.
- **Palette grade** — the OKLab regional split-tone described above.
- **Black and white point** (`--levels`, off by default) — pulls the measured black point toward a true
  black. It reshapes only the darkest tenth of the frame, so the effect is small (the black floor of
  a washed frame drops by 6 to 10 levels of 255). The white point is available to `autoedit adjust`.
- **Denoise** (`--denoise`, off by default) — chroma first, before sharpening. The rule scales it with
  ISO (from 800, full from 6400) and the exposure lift; a file without an EXIF ISO gets none.
- **Highlight hold** (`--highlight-hold`, off by default) — where the source is near white in all
  three channels, the edit may not darken the pixel, so a blown sky stays white instead of turning
  light grey.
- **Grain** (`--grain N`, off by default) — film grain, luma only, strongest in the midtones. It is
  added to the written picture after the export resize and after every check, so a 2048 px export
  shows the same grain as the full-size file seen at that size. "add grain" works in the text boxes.

### Safety layers (all data-driven, all still apply under learned/variant modes)

- **Region policies** (`policies.py`) — drop forbidden ops / clamp magnitudes per concept; enforced
  after the decider **and re-enforced after reference tuning** (the rails always win).
- **Skin-safe guard** — holds aggressive *global* ops (clarity/saturation/grade) off skin/snow.
- **Oversaturation gate** (`edit/gates.py`) — caps per-pixel chroma increase vs the source
  (`rules.toml [guard].max_chroma_increase`), tightening gradually across feathered mask edges.
- **Banding gate** (`edit/banding.py`) — detects posterization in smooth regions (sky/mist/water),
  source-relative, and **auto-remediates**: reduces the stretch ops (dehaze/contrast/curve/clarity)
  and re-applies once — the sidecar reports the damped settings that actually rendered. Backed by
  **output dither** (±1 LSB deterministic) + **lossless WebP previews** so gradients never contour.
- **Darkness-regression gate** (`edit/gates.py`, `rules.toml [quality_gate]`) — source-relative: a
  photo whose foreground or bottom-band median is already dark (< 0.30) must not come out materially
  darker, or it is flagged for review. Never an absolute brightness target (night frames left alone
  pass); a prompt containing a `preserve_terms` word ("moody", "night", …) opts out.
  `scripts/tone_report.py` runs the same check over a whole folder (dark→darker, pushed-into-clipping,
  distance to the camera render) as an unattended gate. With `--fail-on-regression`, missing or
  failed photos make the run fail even when all successfully measured photos pass. Reports also
  retain RGB clipping deltas, review reasons and expected/completed counts.
- **Region instructions** — the cells of an instructed region keep the region's own policies, so
  `--edit` cannot push a protected region (skin, snow) past its limits.
- **Post-edit assessment** — `quality_score`, `needs_review`, `warnings`, measured on the *edited*
  output. Thresholds are data (`rules.toml [quality_gate]`). Drives the review queue and variant
  scoring.
- **Added-clipping gate** — more than 0.5 percentage points of new highlight clipping triggers
  review even below the absolute clipping thresholds. It compares source and output luminance
  clipping and the fraction with any RGB channel clipped, so single-channel damage is visible.
  The budget and score penalty live in `rules.toml [quality_gate]`; the warning records before,
  after and increase. Preserving a dark exposure does not disable this check.

### Shoot consistency (`consistency.py`)

Two-pass batch normalization: measure each photo's brightness, cluster into lighting groups, nudge
each toward its group target (bounded ±0.15 EV) so a folder doesn't drift photo-to-photo. Toggle in
Settings (default on).

A second mode, `--consistency bursts` (off by default), finds bursts: consecutive shots at most 30 s
apart that repeat one framing (same orientation, thumbnails alike once each row's mean is removed). A
RAW file and its JPEG count as one shot. Each frame is nudged toward the burst's median exposure
(at most 0.5 stops) and white balance (at most 4 units); a nudge never cuts below what the rules
decided and never pushes a frame into clipping. The limits are in `rules.toml [consistency]`.

---

## 4. Reference matching ("edit like this photo")

Give AutoEdit a hero edit and it grades toward that look — **non-generative**, region-aware.

- **Global** (`edit/reference.py::color_transfer` + `tune_settings_to_reference`) — matches overall
  palette/tone and iteratively nudges bounded sliders toward the reference's look features.
- **Region-aware** (M5) — the reference is **decoded and segmented once per run and cached**, each region's look measured
  (`extract_concept_features`), then each source region is pushed toward the reference's **same
  concept** (sky→sky, water→water, mountain→mountain) via `region_settings_from_features`. This is
  **dimension-independent**, so an arbitrary alpine reference (sunset, foggy, stormy, green valley,
  blue-sky) works even at a different crop/size. Bounded and gated like everything else.

UI: **Develop → Reference** — load a reference photo + a match-strength slider. CLI: `--reference IMG`.

---

## 5. Culling (M1)

Copy a curated subset of a big shoot for review or training — **originals untouched**.

- **Modes:** `diverse` (cover the range of looks), `best` (heuristic keepers), `aesthetic` (learned
  critic model).
- **Outputs** (`curate.py`) under `<folder>/AutoEdit Cull/`: `selected/`, `thumbs/`,
  `contact-sheet.html`, `selected.csv`, `candidates.json` (including a `decode_failures` count so
  unreadable files aren't invisible), optional `.xmp` sidecars.
- **UI:** the **Cull** button → pick folder, mode, count, scan-subfolders, XMP; runs a background job
  (`web/curate_jobs.py`) and shows the picks. **API:** `POST /api/v1/curate/jobs`, `GET /api/v1/curate/jobs/{id}`.
  **CLI:** `autoedit curate <in> <out> --mode diverse --count 200`.

---

## 6. Review & labels (M2)

After a batch, label the results — the training signal for the critic and the recipe predictor.

- **Vocabulary** (`review.py`): decision `keep`/`reject` + tags `too_dark`, `too_blue`, `too_warm`,
  `fake_looking`, `changed_content`, `halos`, `banding`, `good_style`, `good_preservation`.
- **Persistence:** append-only `review-labels.csv` + `review-labels.json` in the output folder —
  **durable**: JSON writes are atomic and lock-serialized, and a crash-corrupted JSON is rebuilt
  from the append-only CSV (labeling history can never silently restart). `review.summary()`
  collapses append-only records to the newest label per image for UI hydration.
- **UI:** the post-batch **Review** modal — Keep/Reject + tag chips per card, **Save labels** in the
  header. **API:** `POST /api/v1/review/labels`.

---

## 7. Dataset builder (M3)

Turn original↔edited pairs into a validated training set — the **non-generative gate**.

- **Pairing** (`dataset.py`) — exact stems match first; only leftovers fall back to edit-suffix
  stripping (`_edit`/`_after`/`_copy`…), most-specific key first. **Burst-safe:** `IMG_1-2.jpg` is a
  different shot, and its target pairs with it, never with `IMG_1`. Colliding workspace stems are
  disambiguated instead of overwritten.
- **Preservation `validate_pair`** — the gate that keeps *generated* / content-changed edits out of
  training: aspect/crop match, **edge-structure correlation** (low = content drift → reject), added
  clipping, oversaturation, and introduced banding. Returns metrics + reasons.
- **`build_dataset`** — copy-only workspace: `originals/`, `targets/` (passing pairs), `rejected/`
  (failing targets), `labels.csv`, `manifest.json`.
- **UI:** the **Dataset** button → pick originals + targets folders → **Validate** (preview verdicts)
  or **Build**. **API:** `POST /api/v1/datasets/validate`, `POST /api/v1/datasets/build`.
  **CLI:** `autoedit dataset validate <o> <t>` · `autoedit dataset build <o> <t> <out>`.

---

## 8. Learning subsystems

### 8a. Aesthetic critic (M4) — a finished-photo *scorer*, not an editor

`aesthetic.py` (ridge model) + `critic.py`:
- **`train_from_csv`** — trains from an AVA/Flickr-style labels CSV (auto-detects score/rating columns
  *and* AVA 1–10 vote distributions).
- **`score_folder`** — ranks a folder best-first with a 0–1 score + rank.
- **`review_priority`** — peaks at the 0.5 decision boundary, so uncertain photos go to human review.

Used for: ranking originals for culling, ranking/filtering variants, prioritizing review. **UI:** the
Cull modal's **Aesthetic** mode — Load model / Score only / Train. **API:** `POST /api/v1/aesthetic/train`
(background job) + `GET /api/v1/aesthetic/jobs/{id}`, `POST /api/v1/aesthetic/score`.
**CLI:** `autoedit train-aesthetic <labels.csv> <images> <model.json>`.

### 8b. Recipe predictor (M6) — the first learned *editor* (still non-generative)

`recipe.py` predicts **settings, never pixels**: `image stats → bounded edit recipe`.
- **Training labels** come from approved M3 pairs: `target_recipe` reuses the reference tuner (M5) to
  derive the bounded settings that turn each original into its target. Then the model learns
  `features_from_stats → settings` (multi-output ridge, numpy-only) so at runtime the recipe is
  predicted in **one shot — no reference needed**.
- **`LearnedDecider`** predicts the **global** recipe and takes **per-region** edits from the rules
  baseline; **falls back to pure rules** if the model is missing or prediction fails. Output is
  clamped to op bounds and run through every safety gate.
- **UI:** Settings → **Edit engine** (Rules / Learned) + Load recipe model. **CLI:**
  `autoedit train-recipe <dataset> <model.json>` and `autoedit run … --decider learned --recipe-model m.json`.

### 8b2. Style from your edits (F27)

- **History** (`--history`, `history = true` under `[run]`; off by default): every export adds one line to
  `history.jsonl` in the profile: the scene, the photo's statistics, the look, and your own changes per
  target. No pixels, paths, file names or typed sentences. `autoedit history status | clear --yes`.
- **Suggestions:** `autoedit style suggest` lists a change you made on at least 12 photos, on at least 3
  days, in the same direction on at least 70 % of the photos it could apply to ("You made the sky darker
  on 14 of 17 landscapes"). `--yes NAME` saves it into a new look as a `habit` table. Nothing changes
  without it. API: `GET /api/v1/style/suggestions`, `POST /api/v1/style/accept`. Thresholds: `style.toml`.

### 8b3. Look files (F28)

`autoedit looks export NAME [-o FILE.aelook] [--palette NAME] [--author ..] [--licence ..]` writes a look
as one file; `autoedit looks import FILE [--name NEW] [--force]` checks it whole and installs it into the
profile (a packaged name is never replaced); `autoedit looks card NAME [photos or folders] -o DIR` renders
it on up to 12 photos as a before-and-after sheet (with no photos named: a drawn frame). CLI only.

### 8b4. Teaching mode (F29)

Two keys on `POST /api/v1/preview`. `"steps": true` (or `{"px": 256..2560, "lossless": bool}`) adds the edit
replayed one step at a time: source, regions, shapes, one step per tool group, palette, checks, finish,
grain, each with its picture, values and three lesson lines from `lessons.toml`; the last step equals the
preview. `"try": {"sliders": {...}, "compare": true}` renders the photo from your sliders and sentences
only (no rules, look, palette or sharpening) and, with `compare`, the engine's version, both values side
by side and the checks' facts ("your version clips 3 % of the sky"). A try is never exported.

### 8b5. Pick the keepers (F35)

`autoedit pick FOLDER [-o DIR] [--copy] [--aesthetic-model FILE] [--backend sam3]`: per burst (F9's finder),
the frames ranked by the sharpness of the subject, exposure and the scorer if given; writes `ranking.json`,
`ranking.csv`, `picks.txt` and `contact-sheet.html`; with `--copy` the picks into `picked/`. No source
file is moved, renamed or changed. Weights in `configs/pick.toml`.

### 8b6. Genre packs (F30)

`--genre NAME` (CLI, run files, `"genre"` in API requests) lays a pack from `configs/genres.toml`
under your own settings: a preset of things to find, a look, opt-in rule sets of `rules.toml` and
run settings. `autoedit genres list` shows each pack, where it comes from and whether it is tested;
a menu offers only tested packs and your own. A profile can add packs and change packaged ones.
The two packaged packs, `landscape` and `alpine`, are untested for now (see the file's comment).

### 8b7. Straighten and crop (F32)

`--straighten` levels a clear waterline or horizon (found in the water and sky masks, so SAM 3 is
needed; at most 5 degrees, a level photo is left alone); `--aspect 4:5` crops to a shape, placed on
the subject. Both are off by default; an export preset can carry `aspect`. The sidecar's `geometry`
gives the angle, the frame and the share kept. The cut is done at the export, after every check.

### 8b8. Merge frames (F33)

`autoedit merge FOLDER [-o DIR] [--mode auto|average|brackets|focus|trails] [--dry-run]` finds sets
of frames shot seconds apart (F9's burst finder, plus a comparison at one exposure for brackets),
aligns them by a whole-pixel shift and merges each set into one TIFF (16-bit with `tifffile`) with a
`.merge.json` record of the frames used. `auto` picks `brackets` when the exposure settings differ by
more than 0.5 EV, else `average`; `focus` and `trails` only by name. Frames more than 1 % of the long
edge apart are refused (tripod only); things that moved are merged and flagged. The TIFF then goes
through `autoedit run` like any photo. Settings in `configs/merge.toml`. No photo is ever changed.

### 8b9. Exact colour and clean backgrounds (F34)

For products, food, copies of artwork and school portraits, all off by default:
`--neutral X,Y` makes a grey or white spot (shares of the frame) neutral on every photo of the run;
`--neutral-from FILE` measures it once on one photo (the grey card shot) and gives every photo those
numbers. A spot the white balance cannot make neutral (blown, too dark, a cast beyond its reach) is
refused with its numbers, never half applied. `--backdrop` evens a light, near-neutral background that
reaches the frame edge and takes it to 245 of 255; what SAM 3 found (subject, person, skin) is never
touched. `--consistency-wb` evens white balance within a group as F9 evens brightness;
`--colour-report` prints and records how far each photo's subject colour is from its group's.
`autoedit adjust --neutral X,Y|none` changes the pick of one photo. Settings in `[neutral]`, `[backdrop]`
and `[consistency]` of `rules.toml`.

### 8b10. Night sky tools (F31)

`--night-sky` (or `--genre astro`, which also turns on noise reduction and the `astro` look) treats a
night frame (median at most 0.25 and ISO at least 1600, values in `[night_sky]` of `rules.toml`) as
its own scene `night`: stuck coloured pixels are repaired, a smooth glow (a town's light pollution)
is fitted inside the sky mask and taken off, the frame keeps its darkness, and noise reduction and
sharpening leave star cores alone. Other frames get the usual rules. Off by default; the pack is
untested until 20 night photos have been through the tone gate and a blind sheet.

### 8c. Variant engine (M7)

`variants.py` — generate several recipes per photo, score, rank, auto-pick, review the uncertain.
- **Specs:** diverse default recipes (Natural, Dramatic cool, Warm sunset, Editorial punch, Muted film).
- **Scoring:** post-edit quality gate + optional aesthetic critic, with a penalty when a variant is
  gate-flagged. **`is_uncertain`** sends the pick to review when the winner is flagged or the top two
  are within a hair.
- **UI:** the **Variants** button on the stage → ranked recipes with best/review badges → **Apply**.
  **API:** `POST /api/v1/variants`. **CLI:** `autoedit rank-variants <image|folder>` → `variant-ranking.csv`.
- **Surprise me (F26):** `"surprise": true` (and an optional `"seed"`) on `POST /api/v1/variants` renders
  six looks from the library, drawn by the seed (the same seed gives the same six; the photo's
  current look is left out), and returns `seed` and `looks`. The **Surprise me** button (Look) shows
  them; a pick sets the look by name. `autoedit run ... --look surprise --seed N` prints the six and
  runs the batch with the first; the sidecar records that look's name.

---

## 9. The interface (F49)

### Start it

- **Browser:** `pip install -e '.[ui]'`, then `autoedit ui`. It opens `http://127.0.0.1:8765/`.
- **Desktop window:** `pip install -e '.[app]'`, then `autoedit app`. It opens the same interface in a
  native window. The port is stable for a given profile so the photo editor can reopen its library. On Linux it needs a GTK or Qt web view installed.

Light or dark follows the system. The photo always sits on the same mid grey. Esc and the back
arrow (top left) go back one screen.

### Screens

#### Start

Choose **Automatic** to finish a photo or a folder in one pass. Choose **Photo editor** to work on individual images. **Guided workspace** opens AutoEdit's folder, recipe, review and learning tools.

#### Automatic

Use **Choose photo** or **Choose folder**. The selection shows the originals and the photo count.
**Edit and export** runs AutoEdit's local rules and writes finished files and decision sidecars into
a new dated folder inside `AutoEdit Exports`. You can follow progress, stop the run, see which files
need attention and use **Show exports** to open the results.

#### Photo editor

The manual workspace embeds Brandon Thomas's LightCraft web editor, pinned to version 0.21.1.
On first use, AutoEdit downloads the official release, checks its SHA-256 digest and caches it locally.
Opening photos and using develop controls then work offline. The editor needs a browser or desktop
web view with WebGL 2. See [LightCraft's web edition](https://github.com/storytold/lightcraft/blob/v0.21.1/docs/web.md).

- **Open photo** imports the original into LightCraft's local library. Set exposure, adjust the tone curve or color mixer, crop and straighten, or use **Masking** to limit a change to part of the picture. **Auto adjust** runs LightCraft's automatic development on request.
- **Open RAW as TIFF** opens a full-resolution 16-bit copy using AutoEdit's local RAW decoder (the `accel` extra). Use it for a camera file that LightCraft can only display as a thumbnail. White balance and demosaicing are baked into this copy. Your RAW stays on disk.
- If an unsupported RAW has stopped the editor, choose **Recover photo editor**, then **Open RAW as TIFF**. Recovery can filter the library to the TIFF to avoid reloading the failed RAW thumbnail. **Show all photos** clears that search.
- **Guided tools** opens the original selected through AutoEdit in the Guided workspace. That workspace keeps its own adjustments. Photos added directly inside LightCraft can be selected with the Guided file picker.
- **Compare** opens a before/after view with a draggable divider. Use the arrow keys to fine-tune its position. **Export** saves the developed image through the browser or desktop window.
- **Ask Gemini** lets you review suggested whole-photo adjustments and apply supported values to LightCraft's editable controls. The guided workspace also supports named-region suggestions and recipes.
- LightCraft keeps its catalog and adjustment history in local storage for this browser and app address. Clearing site data removes that catalog. The native LightCraft app has additional capabilities including JPEG XL export. **Open desktop editor** is available when AutoEdit finds an installed copy.
- **Guided tools** opens the AutoEdit workspace. In a guided photo, **Open original in Photo editor** imports the source file. After saving, **Open saved copy in Photo editor** imports the rendered pixels as a new starting point. The editable AutoEdit steps stay in the guided project.

#### Guided workspace

**Open photo**, **Open folder**, **Try a sample set**, and recent folders. The samples are generated
landscapes made locally without a download. Your photos stay on this computer. Reopening a folder
restores its editing project, including selection, individual changes and undo/redo history.
After restarting AutoEdit, a recent folder may ask you to **Choose folder** again. Select the
same folder to restore its saved edits. Canceling leaves the saved project untouched.
Browser drag-and-drop uses a file picker when the browser cannot provide a local file path.
The guided workspace shows the four editing steps and whether the optional object selection model is ready.

#### Folder (home)
The grid of photos (RAW+JPEG pairs count as one) with a status dot on each tile: waiting, editing,
done, look at this, failed. The side panel holds what applies to the whole folder:

- **Look**, **Surprise me** (six looks on the current photo), **Kind of photo**, **Strength**.
- **What to change in every photo**: a sentence, for example "darken the sky".
- **Export as**: a preset (format, size, name, subfolder).
- **Edit N photos**: edits the checked photos. Progress, Pause and Cancel sit under the grid. A
  stopped run resumes. Photos that failed or need a look are listed, and one bad photo never stops the rest.
- **Find**: type words ("snow lake") to filter by what is in the photos.
- **Share** and **Tools** in the top bar; the gear opens Settings.
- The grid shows 100 photos per page. Shift-click checkboxes for a range; selections survive page
  changes. Capture-time groups appear when timestamps are available; these are time groups,
  not a claim that every frame has the same composition. **Review** steps through flagged/failed photos.
- Per-photo looks, intensity, reference choices and adjustments also apply to **Edit N photos**.
- Open **Recipes** to apply a saved recipe to the ticked photos. Each tile shows its active
  recipe names. For a mixed folder, add recipes to the order, then select **Plan**. Review the
  counts and missing objects before choosing **Apply plan**. The first match wins. Changing
  the order and planning again replaces the earlier assignment, and an empty order clears it.
  Unmatched photos return to their pre-plan settings and keep later manual adjustments.
  **Edit N photos** runs the export.
- Set a recipe's optional scene and up to three measurement tests under **Edit name or conditions**.
  A recipe without conditions matches any photo, so put a general fallback last. Failed files and
  missing objects are listed in the plan. A missing target gets a review mark on the photo.

#### Guided photo

Click a tile to see the original. Arrow keys and the filmstrip move between originals. Choose
**Preview AutoEdit** to generate the first edit, or **Resume guided edit** to reopen one with saved work.

- A quick local preview arrives first, followed by a sharp preview (up to 2560 pixels). **Hold the photo
  or press B** to see the original. Choose **Before / after** and drag directly across the image,
  or use the divider's arrow keys. Home and End show the full after and before images.
  **100% preview** shows one preview pixel per screen pixel; drag to pan. Exports use full resolution.
- **Whole photo adjustments** has saved exposure, contrast, highlights, shadows, temperature, tint and
  color controls. Zero means AutoEdit's automatic baseline; values add an adjustment to it.
  Limits and safety checks still apply. **Undo**, **Redo** and **Reset photo** cover all photo changes.
- **Targeted edits** opens **Objects & selections**, where you can select a found region and adjust it with the same seven controls. **Find
  an object** requests a named concept such as "jacket". A missing object stays missing; no mask
  is invented. **Add** and **Subtract** brushes correct the selection on the uncropped source
  frame, with each stroke undoable. Stub selections are approximate demonstrations.
- **Crop & straighten** offers aspect ratios, a movable crop and manual angle. Rotation trims
  unsupported edges instead of filling them. Preview, save and batch use the same framing.
- **Native detail** renders matching original/edited native-resolution crops. One image pixel
  is one displayed pixel. It includes framing and finishing; export resizing and lossy encoding
  are excluded. The ordinary **100% preview** remains limited to preview resolution.
- **Ask Gemini** returns up to six named adjustments. Tick the ones you want and choose
  **Apply selected**. They appear under **Changes**. If the photo changes before you apply
  a suggestion, request a fresh suggestion. The accepted changes render locally after reopening.
- In **Changes**, switch an entry off, adjust its **Amount**, or remove it. Manual sliders keep
  their own values. For example, +0.4 EV at 50% adds +0.2 EV to the exposure slider. The panel
  reports a limited total or a missing object. Undo restores the previous settings.
- In **Recipes**, **Save current settings** captures the look, strength and effective whole-photo
  and named-object adjustments, including enabled Changes at their current amounts. Each
  destination finds its own objects. Export and import transfer the recipe name and settings as
  JSON. Look and strength are photo settings; the new Changes entry controls the recipe's
  adjustment amounts. Reapplying the same recipe replaces its entry.
- **Compare looks** previews looks on the current photo. **Match a reference** brings the reference
  choice and matching strength into this panel. References outside the opened folder may need
  choosing again after restarting the app.
- **Say what to change** in the box under the photo ("warm the grass a little"). Each sentence
  appears under **Your changes** and can be removed.
- **Mark** (or M): drag an oval, then say what changes there ("make this guy more purple"). A
  sentence that names a thing edits that thing inside the oval; any other sentence edits the oval as
  an area. If the thing is not found, nothing is applied and the chip says so. Without SAM 3 every
  mark is an area.
- **Pick something grey**: click a neutral spot to set the white balance exactly.
- **Copy to similar**: after saving, choose recipient photos and the tone, color, white-balance or
  detail adjustments to copy. Exposure matching is optional. Other categories and the recipients'
  marks are kept. The source must be saved again after further changes before it can be copied.
- **How it was done**: the mask and one line per tool, what was held back, and the photo with each step off.
- **Try it yourself**: practice sliders compared with AutoEdit's version. Practice changes remain
  separate from the saved controls in **Adjust this photo**.
- **Masks**: shows the regions that were found.
- **Save this photo**: renders this photo's current choices at full resolution into AutoEdit Exports.
- The filmstrip and arrow keys move through nearby photos; in the review queue they follow the queue.
- Turn on **Save photo ratings for training** in Settings to see the Good / Not good buttons.

#### Tools
One form per tool, then the result in place with "Open the result folder".

| Tool | What it does |
|---|---|
| Pick keepers | The sharpest well-exposed frame of each burst |
| Merge frames | Brackets, repeats, focus series, trails |
| Choose a subset | Copy a varied or best-scored set for review |
| Looks | Save, delete, import, export, card, LUT |
| Your style | Changes you keep making, offered as a look |
| Calibrate | Find your look from pairs you vote on |
| Training | Datasets, scorer, recipe model (advanced) |

#### Settings
A sheet over any screen, drawn from the settings schema (`autoedit settings --json`). Groups:
Region finder and model, RAW, Quality, Look extras (grain, LUT), Output (format, metadata,
workers, export presets and watermark), Privacy, Profile, Advanced, About. Appearance has a dark
surround option. Turn on **Show setting descriptions** for help with individual controls.
Defaults are the ones the command line uses.
Advanced groups are collapsed until needed. The Gemini key and cloud consent are held only
for the current page session, or the server can read `GEMINI_API_KEY`. Each explicit request
sends a stripped thumbnail no larger than 512 pixels and numeric editing context. Full-resolution
photos, local paths, project data and source metadata are excluded. Missing keys, invalid
responses, timeouts and session budget limits leave the current local edit intact.

#### Share sheet
After a run: **Check before sharing** (findings per photo, such as location or a serial number
left in), **Create edit declaration** (what was changed, with hashes), **Write keyword files (.xmp)**,
**Show exports in Finder** (Explorer on Windows), and **Make gallery**.

## 10. CLI reference

```
autoedit run <in> <out> [--style … --prompt … --palette … --intensity … --reference IMG
                         --decider rules|llm|learned --recipe-model m.json
                         --backend stub|sam3|auto --format jpeg|tiff|webp|png --resume --dry-run
                         --metadata keep|shareable|none --edit "…" --export PRESET
                         --long-edge PX --name PATTERN --subdir DIR --workers N --threads N
                         --consistency brightness|bursts --profile DIR
                         --highlight-hold --denoise --levels
                         --no-consistent --no-compose --no-vignette --no-sharpen …]
autoedit adjust <photo|sidecar> [--set sky.exposure=-0.3 --edit "…" --reset --source IMG --mark X0,Y0,X1,Y1 --mark-kind auto|area --explain]
autoedit ui [--host --port --no-open]                 # the interface in the browser
autoedit app                                          # the same interface in a desktop window ([app] extra)

# workflow
autoedit curate <in> <out> --mode diverse|best|aesthetic --count N [--aesthetic-model m.json]
autoedit dataset validate <originals> <targets> [--recursive]
autoedit dataset build    <originals> <targets> <out> [--recursive --no-validate --approve-all]
autoedit rank-variants <image|folder> [--out csv --aesthetic-model m.json]

# training
autoedit train-aesthetic <labels.csv> <images> <model.json>
autoedit train-recipe    <dataset> <model.json>

# personal setup
autoedit looks list | save NAME [--from BASE --set FIELD=VALUE] | delete NAME
autoedit calibrate <photos> --out DIR | --continue DIR [--finish]
autoedit models status | download | remove

# info
autoedit doctor | presets | styles | exports | settings [--json]
```

---

## 11. Web API reference

Every route below also lives under `/api/v1` (for example `/api/v1/preview`); the plain `/api/v1/…`
paths still work. Every failure answers with one error shape, `{"error": "…", "code": "…"}`.
Also available: `GET /api/v1/version`, `GET /api/v1/settings/schema` (the data behind
`autoedit settings`) and `GET /api/v1/jobs/{id}/events`, a server-sent event stream for a job. The
route list is pinned by `tests/snapshots/api_v1.json`.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/styles`, `/api/v1/presets` | List styles / concept presets. |
| `GET /api/v1/list?dir=`, `/api/v1/groups?dir=` | Flat list / RAW+JPEG-paired groups. |
| `GET /api/v1/thumb?path=&px=` | Fast thumbnail (px clamped). `POST /api/v1/pick?kind=` native picker. |
| `POST /api/v1/preview` | Proxy before/after (lossless WebP), actual object masks + decision sidecar. |
| `POST /api/v1/detail` | Local full-resolution render, matched native crops and true dimensions. |
| `POST /api/v1/assist` | Explicit opt-in Gemini proposal; does not apply or save adjustments. |
| `GET /api/v1/editor/status`, `POST /api/v1/editor/install` | Check or install the pinned local LightCraft release. |
| `GET /api/v1/editor/original?path=`, `POST /api/v1/editor/native` | Open a picker-approved original or launch the installed desktop editor. |
| `POST /api/v1/editor/assist` | Request whole-photo changes for a rendered LightCraft preview with explicit Gemini consent. |
| `GET /api/v1/edit-presets`, `POST /api/v1/edit-presets`, `DELETE …/{id}` | Local portable creative presets; separate from concept presets. |
| `POST /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `POST …/control` | Background edit batch + live progress. |
| `POST /api/v1/curate/jobs`, `GET …/{id}` | Culling job (M1). |
| `POST /api/v1/review/labels` | Save review labels (M2). |
| `POST /api/v1/datasets/validate`, `/api/v1/datasets/build` | Dataset builder (M3). |
| `POST /api/v1/aesthetic/train`, `GET …/jobs/{id}`, `POST /api/v1/aesthetic/score` | Critic (M4). |
| `POST /api/v1/variants` | Variant engine (M7). |

---

## 12. Config files (`src/autoedit/configs/`)

| File | Role |
|---|---|
| `rules.toml` | Per-`[scene.region]` op = expression (sandboxed AST). The rule baseline + `[guard]` (guarded ops, chroma cap) + `[banding]` + optional `[quality_gate]`. |
| `looks.toml` | Magnitude profiles (contrast_mul, curve_mul, exposure_mul, vibrance_add, saturation_add, curve/grade finishing), `grain`, `dehaze_add` and black and white (`bw` and six colour weights). Packaged: natural, editorial, punchy, warm_film, dramatic, and bw_classic, bw_red_filter, bw_soft, film_warm, film_cool, film_faded, cross_process, instant. |
| `styles.toml` | User-facing styles = look + concepts + brief. |
| `palettes.toml` | OKLab regional palettes (shadow/mid/highlight hue+chroma, neutral protection). |
| `intents.toml` | Free-text look vocabulary → bounded nudges. |
| `concepts.toml` | Per-concept priority, gating, feather, guards, region policies. |
| `compose.toml` | Compositional moves (vignette, sky graduate). |
| `defaults.toml` | Default run config, including `[output] metadata` (which camera data an export keeps). |
| `instructions.toml` | The words `--edit` understands: verbs, amount words, a limit per op. |
| `exports.toml` | Export presets (`full`, `web`, `social`, `archive`, `webp`). |
| `calibrate.toml` | Dimensions and step sizes of `autoedit calibrate`. |
| `settings.toml` | Description of every run setting (`autoedit settings`). |

Your own differences can live in a **profile** folder (`--profile DIR`, `AUTOEDIT_PROFILE`, or
`~/.config/autoedit`): TOML files with only the keys you change, merged over these files.
`rules.toml` also holds opt-in rule sets (`[opt_in.NAME…]`) that apply only when the run setting
`NAME` is on.

---

## 13. Privacy & safety summary

- Runs fully on-device; full-resolution pixels never leave the machine.
- The **LLM decider** is the only network path — off by default, requires explicit consent + a BYO API
  key, and sends only a thumbnail + stats. It degrades to rules otherwise.
- Culling and dataset building are **copy-only**; identity edits are strict no-ops.
- Edited files keep the source photo's camera data (capture time, camera, lens, exposure, artist,
  copyright) and get an sRGB colour profile. GPS location and the serial numbers of the camera and
  the lens are removed by default; choose with `--metadata keep|shareable|none` (`[output] metadata`
  in `defaults.toml`). `keep` keeps both, `none` writes no camera data at all. An owner name set in
  the camera stays under `shareable`, like the artist and the copyright.
- All learned models predict **bounded sliders only** and pass through the same oversaturation /
  banding / skin-guard / clipping gates as rule-based edits.

---

## 14. Testing

Runnable both as `PYTHONPATH=src python3 tests/run_all.py` (no pytest needed) and pytest-collectable —
**665 tests** across 76 files, currently green (run `PYTHONPATH=src python3 tests/run_all.py`). Coverage: each edit op's math, identity no-op, real
dehaze, OKLab palette (direction / neutral protection / gamut / auto-grade restraint), banding
detector + dither + sidecar consistency, segmentation cache, RAW+JPEG grouping, EXIF orientation
(all 8 values), decode robustness (grayscale/RGBA/CMYK/16-bit), the `veil` atmospheric-haze
estimate vs the legacy dark-channel stat, sky-excluded foreground tone, region-aware auto-WB
(cast measurement + warmth-preserving correction), percentile-driven rule goldens, intent parsing,
batch consistency (+ toggle), culling, review labels (+ crash recovery), dataset pairing
(+ burst safety) + preservation, aesthetic critic, region reference matching (+ policy
re-enforcement), recipe train/predict + learned fallback, LLM merge clamping, variant scoring,
atomic emit + collision de-dup, security/trust boundary, and the blind-test A/B harness
(traversal / symlink / DNS-rebinding / secret-leak), config hardening, and the web service.

For visual evaluation, `scripts/blindtest.py --methods baseline,camera,autoedit --stratify 8`
selects eight images from each source-brightness third and shuffles three versions per photo.
The gallery uses the configured JPEG quality (95 by default; override with `--quality`), writes
`run.json` with public config, config/source hashes and actual backend details, and keeps pipeline
sidecars beside their image slots. `metrics.csv` measures every version over the whole frame for
comparison; sidecars retain SAM-aware foreground/region stats and RGB review decisions. A gallery
render at 1600px is a visual comparison, not proof of full-resolution batch/export behavior.

**Definition of done** per change: `tests/run_all.py` green; a stub corpus run
(`autoedit run ./test-corpus ./out --backend stub`) completes with sidecars + manifest; configs/docs
updated if behavior changed.

---

## 15. From the command line

The interface covers the everyday loop. These commands do the same work, and a few things more, from a
terminal. `autoedit <command> --help` lists every option.

- **Say what you want in words:** `autoedit run ./photos ./out --edit "brighten the jacket, darken the sky slightly"`.
  Any thing the segmenter can find may be named. The sidecar says for each instruction whether it was
  applied, clamped or not found.
- **Fix one photo after the batch:** `autoedit adjust ./out/photo.jpg --set sky.exposure=-0.3` or
  `--edit "warm the grass a little"`. It renders that photo again from its sidecar; `--reset` drops
  your adjustments.
- **Make a big batch faster:** `--workers 4` renders several photos at once; `--threads` sets the
  threads inside one render. Masks are kept on disk in `<out>/.autoedit-masks`, so a re-run or an
  adjustment does not segment again (`--no-mask-cache` turns this off).
- **Export for a purpose:** `--export web` (also `full`, `social`, `archive`, `webp`) sets format,
  size, name and subfolder in one word; `autoedit exports` lists them. `--long-edge`, `--name` (fields
  `{stem}`, `{style}`, `{n}` only) and `--subdir` set them one by one. A run refuses to write an export
  over its own source photo.
- **Even out a burst:** `--consistency bursts` nudges frames of one framing toward the same exposure
  and white balance (section 3).
- **Keep your own settings and looks:** `autoedit looks save warm --from natural --set vibrance_add=6`
  stores a look in your profile folder; `--profile DIR` or `AUTOEDIT_PROFILE` picks the folder.
  `autoedit looks list` shows where each look comes from.
- **Teach it your taste:** `autoedit calibrate ./photos --out ./calib` renders blind pairs; vote on the
  page, then `autoedit calibrate --continue ./calib` for the next round. The result is saved as a look.
- **Manage the SAM 3 weights:** `autoedit models status`, `download` and `remove`. Nothing is
  downloaded unless you run `download`.
- **See every setting:** `autoedit settings` prints each run setting with its type, default and
  range; `--json` gives the schema.
- **Try the three quality options:** `--highlight-hold`, `--denoise` and `--levels`. All are off by
  default and each changes how a photo looks.

---

## 16. File map

```
src/autoedit/
  pipeline.py            # 8-stage orchestrator          contract.py  # RunConfig + all models
  batch.py               # folder batch + consistency    cli.py       # command-line interface
  imageio.py             # decode/encode, RAW/16-bit/ICC/EXIF, dither, OKLab helpers
  stats.py scene.py rules.py looks.py styles.py concepts.py compose.py   # measure + decide (data)
  intent.py palette.py   # free-text intent + OKLab palette model
  policies.py consistency.py notes.py emit.py paths.py
  instructions.py adjust.py      # --edit parsing; per-photo re-render (autoedit adjust)
  exports.py calibrate.py models.py settings_schema.py   # presets · taste calibration · SAM 3 weights · settings schema
  # ── workflow + learning ──
  curate.py              # culling (diverse/best/aesthetic), contact sheet
  review.py              # review labels (vocab, append-only CSV/JSON, summary)
  dataset.py             # pairing + preservation validation + copy-only workspace
  aesthetic.py critic.py # aesthetic scorer + reusable critic (train/score/review-priority)
  recipe.py              # learned recipe predictor (stats → settings)
  variants.py            # variant specs + scoring + pick
  segment/               # base · stub · sam3 · cache (per-concept + disk) · resolve
  decide/                # base · rules_decider · llm_decider · learned_decider · make_decider
  edit/                  # ops · apply · par (row-strip threads) · filters · oklab · palette · gates · banding · reference · mask
  web/
    app.py               # FastAPI routes + trust boundary + origin guard
    service.py           # framework-agnostic logic (preview, curate, dataset, aesthetic, variants)
    jobs.py curate_jobs.py aesthetic_jobs.py   # background jobs
    static/app/          # the interface: index.html, app.css, tools.css and seven ES modules
    tools_jobs.py        # POST /api/v1/tools/{name}: the CLI-only tools as jobs
    desktop.py           # autoedit app: the interface in a pywebview window
  configs/               # rules looks styles palettes intents concepts compose defaults
                         # instructions exports calibrate settings .toml
```
