# AutoEdit — Complete App Guide

AutoEdit is a **local-first, open-vocabulary batch photo editor** with a full photography workflow
built around it: cull a shoot, edit it, review/label the results, build a training set, and learn
your own look — all on your machine. The editing is **non-generative**: it only moves bounded,
explainable photographic sliders (exposure, tone, white balance, dehaze, region grades…). It never
invents or replaces content.

- **UI:** a local web app ("Studio") — `autoedit ui`, opens at `http://127.0.0.1:8765/`.
- **CLI:** `autoedit run/curate/dataset/rank-variants/train-recipe/train-aesthetic/…`.
- **Core deps are tiny** (numpy, Pillow, pydantic + stdlib). torch/SAM, Anthropic, rawpy, tifffile
  are optional extras, imported lazily — the default "stub" pipeline runs anywhere with no GPU/network.

> Companion doc: [ARCHITECTURE.md](ARCHITECTURE.md) covers the editing engine in depth. This guide
> covers the **whole app**, including the workflow and learning subsystems.

---

## 1. Core principles

| Principle | What it means |
|---|---|
| **Local-first** | Everything runs on-device. Full-resolution pixels never leave the machine. The only optional exception is the LLM decider, which sends a thumbnail + stats — off by default, opt-in. |
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
| **decode** | `imageio.py` | Load to float32 RGB [0,1] sRGB. RAW (rawpy, with EXIF priors), 16-bit TIFF (tifffile), ICC→sRGB (incl. CMYK profiles), EXIF orientation honored on every path. |
| **segment** | `segment/` | SAM 3 (or stub) → one mask per **named concept**. Per-concept **cache** so style switches don't re-run SAM. |
| **resolve** | `segment/resolve.py` | Gate weak detections; arbitrate overlaps into a non-overlapping label map (no pixel edited twice). |
| **stats** | `stats.py` | Per-region + global stats: brightness, saturation, clipping, haze, **luminance percentiles**, **OKLab chroma/warmth**. |
| **scene** | `scene.py` | Classify `landscape` / `people` / `other`. |
| **decide** | `decide/` | Stats → settings. `rules` (default), `llm` (opt-in cloud), or **`learned`** (recipe predictor). |
| **apply** | `edit/` | Composite region edits → global finishing → palette → safety gates, at **full resolution**. |
| **emit** | `emit.py` | Baked image (JPEG/16-bit TIFF) + sidecar JSON. |

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
  the intent text or, failing that, from image stats (`auto_palette_for_image`).
- **Reference** (§4) and **Learned mode** (§7).

### Edit ops (frozen order, `edit/ops.py`)

`white balance → exposure → tone → contrast → curve → dehaze → clarity → vibrance/saturation →
targeted HSL → color grade → sharpen`. An empty settings dict is a strict no-op. Notable:

- **Real dehaze** — physical dark-channel prior (He et al.): estimate airlight, recover transmission,
  refine edge-aware with the guided filter, invert the scattering model, blend by amount. Tapers on
  smooth bright gradients so foggy skies don't band.
- **Palette grade** — the OKLab regional split-tone described above.

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
- **Post-edit assessment** — `quality_score`, `needs_review`, `warnings`, measured on the *edited*
  output. Thresholds are data (`rules.toml [quality_gate]`). Drives the review queue and variant
  scoring.

### Shoot consistency (`consistency.py`)

Two-pass batch normalization: measure each photo's brightness, cluster into lighting groups, nudge
each toward its group target (bounded ±0.15 EV) so a folder doesn't drift photo-to-photo. Toggle in
Settings (default on).

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
  (`web/curate_jobs.py`) and shows the picks. **API:** `POST /api/curate/jobs`, `GET /api/curate/jobs/{id}`.
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
  header. **API:** `POST /api/review/labels`.

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
  or **Build**. **API:** `POST /api/datasets/validate`, `POST /api/datasets/build`.
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
Cull modal's **Aesthetic** mode — Load model / Score only / Train. **API:** `POST /api/aesthetic/train`
(background job) + `GET /api/aesthetic/jobs/{id}`, `POST /api/aesthetic/score`.
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

### 8c. Variant engine (M7)

`variants.py` — generate several recipes per photo, score, rank, auto-pick, review the uncertain.
- **Specs:** diverse default recipes (Natural, Dramatic cool, Warm sunset, Editorial punch, Muted film).
- **Scoring:** post-edit quality gate + optional aesthetic critic, with a penalty when a variant is
  gate-flagged. **`is_uncertain`** sends the pick to review when the winner is flagged or the top two
  are within a hair.
- **UI:** the **Variants** button on the stage → ranked recipes with best/review badges → **Apply**.
  **API:** `POST /api/variants`. **CLI:** `autoedit rank-variants <image|folder>` → `variant-ranking.csv`.

---

## 9. The Studio UI

`autoedit ui` serves a single self-contained page (`web/static/studio.html`, vanilla HTML/CSS/JS,
no build step) — a Lightroom/Capture One-shaped editor.

- **Top bar:** Folder / Photo open · Folder name · Before/After/Split/Side view · Masks · **Cull** ·
  **Dataset** · Run batch · Settings.
- **Filmstrip (left):** real thumbnails, RAW+JPEG **pairs collapsed** into one row (edit a single
  chosen source, toggle RAW⇄JPEG), per-photo **checkboxes** to run a subset.
- **Stage (center):** high-res before/after with a draggable split, **Variants** button, mask overlay.
  Previews are **full-resolution, lossless WebP** (pixel-exact — no banding, smaller than PNG), on a
  manual "regenerate" flow.
- **Develop panel (right):** Style · Intensity · **Look intent** (+ chips) · **Palette** (+ strength) ·
  **Reference** (+ strength) · Concepts · Adjustments · Regions · Why.
- **Settings:** output format (JPEG / 16-bit TIFF) · batch consistency · **Edit engine** (Rules /
  Learned) + recipe model.
- **Modals:** Run drawer (live batch progress) · **Review** (keep/reject + labels) · **Cull** ·
  **Dataset** · **Variants**.

**Trust boundary:** every filesystem path the API touches must descend from a folder you chose via the
native picker this session (set `AUTOEDIT_ALLOW_ANY_PATH=1` to opt out). Every request is
Host-header-checked to loopback (DNS-rebinding defense) and mutating requests are additionally
localhost-origin-checked; `serve` refuses non-loopback hosts without `--unsafe-network`.

---

## 10. CLI reference

```
autoedit run <in> <out> [--style … --prompt … --palette … --intensity … --reference IMG
                         --decider rules|llm|learned --recipe-model m.json
                         --backend stub|sam3 --format jpeg|tiff --resume --dry-run
                         --no-consistent --no-compose --no-vignette --no-sharpen …]
autoedit ui [--host --port --no-open]                 # launch Studio

# workflow
autoedit curate <in> <out> --mode diverse|best|aesthetic --count N [--aesthetic-model m.json]
autoedit dataset validate <originals> <targets> [--recursive]
autoedit dataset build    <originals> <targets> <out> [--recursive --no-validate --approve-all]
autoedit rank-variants <image|folder> [--out csv --aesthetic-model m.json]

# training
autoedit train-aesthetic <labels.csv> <images> <model.json>
autoedit train-recipe    <dataset> <model.json>

# info
autoedit doctor | presets | styles
```

---

## 11. Web API reference

| Endpoint | Purpose |
|---|---|
| `GET /api/styles`, `/api/presets` | List styles / concept presets. |
| `GET /api/list?dir=`, `/api/groups?dir=` | Flat list / RAW+JPEG-paired groups. |
| `GET /api/thumb?path=&px=` | Fast thumbnail (px clamped). `POST /api/pick?kind=` native picker. |
| `POST /api/preview` | Full-res before/after (lossless WebP) + decision sidecar. |
| `POST /api/jobs`, `GET /api/jobs/{id}`, `POST …/control` | Background edit batch + live progress. |
| `POST /api/curate/jobs`, `GET …/{id}` | Culling job (M1). |
| `POST /api/review/labels` | Save review labels (M2). |
| `POST /api/datasets/validate`, `/api/datasets/build` | Dataset builder (M3). |
| `POST /api/aesthetic/train`, `GET …/jobs/{id}`, `POST /api/aesthetic/score` | Critic (M4). |
| `POST /api/variants` | Variant engine (M7). |

---

## 12. Config files (`src/autoedit/configs/`)

| File | Role |
|---|---|
| `rules.toml` | Per-`[scene.region]` op = expression (sandboxed AST). The rule baseline + `[guard]` (guarded ops, chroma cap) + `[banding]` + optional `[quality_gate]`. |
| `looks.toml` | Magnitude profiles (contrast_mul, vibrance_add, saturation_add, curve/grade finishing). |
| `styles.toml` | User-facing styles = look + concepts + brief. |
| `palettes.toml` | OKLab regional palettes (shadow/mid/highlight hue+chroma, neutral protection). |
| `intents.toml` | Free-text look vocabulary → bounded nudges. |
| `concepts.toml` | Per-concept priority, gating, feather, guards, region policies. |
| `compose.toml` | Compositional moves (vignette, sky graduate). |
| `defaults.toml` | Default run config. |

---

## 13. Privacy & safety summary

- Runs fully on-device; full-resolution pixels never leave the machine.
- The **LLM decider** is the only network path — off by default, requires explicit consent + a BYO API
  key, and sends only a thumbnail + stats. It degrades to rules otherwise.
- Culling and dataset building are **copy-only**; identity edits are strict no-ops.
- All learned models predict **bounded sliders only** and pass through the same oversaturation /
  banding / skin-guard / clipping gates as rule-based edits.

---

## 14. Testing

Runnable both as `PYTHONPATH=src python3 tests/run_all.py` (no pytest needed) and pytest-collectable —
**231 tests** across 46 files, currently green. Coverage: each edit op's math, identity no-op, real
dehaze, OKLab palette (direction / neutral protection / gamut), banding detector + dither + sidecar
consistency, segmentation cache, RAW+JPEG grouping, EXIF orientation (all 8 values), decode
robustness (grayscale/RGBA/CMYK/16-bit), intent parsing, batch consistency (+ toggle), culling,
review labels (+ crash recovery), dataset pairing (+ burst safety) + preservation, aesthetic critic,
region reference matching (+ policy re-enforcement), recipe train/predict + learned fallback, LLM
merge clamping, variant scoring, atomic emit + collision de-dup, security/trust boundary
(traversal / symlink / DNS-rebinding / secret-leak), config hardening, and the web service.

**Definition of done** per change: `tests/run_all.py` green; a stub corpus run
(`autoedit run ./test-corpus ./out --backend stub`) completes with sidecars + manifest; configs/docs
updated if behavior changed.

---

## 15. File map

```
src/autoedit/
  pipeline.py            # 8-stage orchestrator          contract.py  # RunConfig + all models
  batch.py               # folder batch + consistency    cli.py       # command-line interface
  imageio.py             # decode/encode, RAW/16-bit/ICC/EXIF, dither, OKLab helpers
  stats.py scene.py rules.py looks.py styles.py concepts.py compose.py   # measure + decide (data)
  intent.py palette.py   # free-text intent + OKLab palette model
  policies.py consistency.py notes.py emit.py paths.py
  # ── workflow + learning ──
  curate.py              # culling (diverse/best/aesthetic), contact sheet
  review.py              # review labels (vocab, append-only CSV/JSON, summary)
  dataset.py             # pairing + preservation validation + copy-only workspace
  aesthetic.py critic.py # aesthetic scorer + reusable critic (train/score/review-priority)
  recipe.py              # learned recipe predictor (stats → settings)
  variants.py            # variant specs + scoring + pick
  segment/               # base · stub · sam3 · cache (per-concept) · resolve
  decide/                # base · rules_decider · llm_decider · learned_decider · make_decider
  edit/                  # ops · apply · filters · oklab · palette · gates · banding · reference · mask
  web/
    app.py               # FastAPI routes + trust boundary + origin guard
    service.py           # framework-agnostic logic (preview, curate, dataset, aesthetic, variants)
    jobs.py curate_jobs.py aesthetic_jobs.py   # background jobs
    static/studio.html   # the single-file Studio UI
  configs/               # rules looks styles palettes intents concepts compose defaults .toml
```
