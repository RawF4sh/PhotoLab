# AutoEdit — How It Works (in depth)

A technical walkthrough of the current system: a **local-first, open-vocabulary batch photo
editor**. You point it at a folder of photos; it segments **arbitrary named regions** (SAM 3),
decides **non-generative parametric edits** per region + globally (rules baseline, optional LLM
refine), bakes the result at full resolution, and writes a **sidecar JSON** explaining every
decision. There is also a local **Studio** web UI.

The product wedge is *open-vocabulary region editing* — you can say "dress", "glacier", "kayak"
and it masks and edits that. The edits themselves are classic, explainable photo adjustments
(exposure, tone, white balance, dehaze, etc.) — never generative pixels.

> Code lives in `src/autoedit/` (~5.9k LoC). Core runtime deps are tiny: numpy, Pillow, pydantic +
> stdlib. torch/SAM, Anthropic, OpenCV/Kornia, rawpy, tifffile are **optional extras**, imported
> lazily, so the default "stub" pipeline runs anywhere with no GPU/network.

---

## 1. The pipeline (one fixed order)

Everything flows through one pipeline, defined in [`pipeline.py`](src/autoedit/pipeline.py)
(`Pipeline.process_one`). The stages are swappable but never fused or reordered:

```
decode → segment → resolve → stats → scene → decide → apply → emit
```

| Stage | Module | What it does |
|---|---|---|
| **decode** | `imageio.py` | Load file → float32 RGB [0,1], sRGB gamma. RAW/16-bit/ICC aware. |
| **segment** | `segment/` | SAM 3 (or stub) → one mask per **named concept**, at proxy resolution. |
| **resolve** | `segment/resolve.py` | Gate weak detections; arbitrate overlaps into a non-overlapping label map. |
| **stats** | `stats.py` | Per-region + global image statistics (brightness, saturation, clipping, haze…). |
| **scene** | `scene.py` | Classify the frame: `landscape` / `people` / `other`. |
| **decide** | `decide/` | Stats → edit values. Rules baseline (+ look/intensity), or LLM refine. |
| **apply** | `edit/` | Composite region edits → global finishing → safety gates, at **full res**. |
| **emit** | `emit.py` | Write baked image + sidecar JSON (the "why"). |

**Key resolution principle: analyze on a proxy, apply at full res.** Masks and stats are computed
on a downscaled proxy (`analysis_long_edge`, default **1536px**). At apply time the masks are
upsampled and edge-aware feathered against the full-resolution image, and every pixel op runs at
full res. The UI preview applies on a 2048px proxy for speed; batch/export uses full resolution.

`Pipeline.build(cfg)` is called once per run — it loads all configs, constructs the resident
segmenter and decider, and is reused across the whole batch so the model load cost is paid once.

---

## 2. Decode & color management (`imageio.py`)

Working representation: **float32 RGB, sRGB gamma, [0,1]**. Even 8-bit JPEGs are promoted to float
so intermediate ops don't quantize. Light-like ops (exposure, white balance) linearize internally
and return to gamma; perceptual ops stay in gamma.

Decode order (bit depth preserved — read high-bit data *before* any RGB conversion that would
truncate it):

1. **Camera RAW** (`.dng/.cr2/.cr3/.nef/.arw/.rw2/…`) → demosaiced at 16-bit via **rawpy/libraw**,
   using camera white balance, `no_auto_bright` (feed the true exposure — AutoEdit decides
   brightness), sRGB-ish gamma. EXIF priors are still read best-effort via Pillow.
2. **16-bit TIFF** via **tifffile** (preserves 48-bit RGB Pillow would truncate). The EXIF
   orientation tag is applied by hand here (`_apply_exif_orientation`) since tifffile returns raw
   pixels — a rotated TIFF loads upright on every decode path.
3. Pillow native 16-bit single-channel modes.
4. **8-bit path**: embedded **ICC profile → sRGB** (AdobeRGB/Display-P3 JPEGs) via `ImageCms` —
   transforming the *original* mode, so CMYK-with-profile converts correctly — honor the EXIF
   orientation tag, then to RGB.

**EXIF priors** are read into a small normalized dict — `iso`, `exposure_ev` — and threaded through
stats so rules can reference camera context (e.g. ease sharpening at high ISO). Missing EXIF →
neutral defaults. `is_raw(path)` / `is_supported(path)` are the public helpers used by pairing.

**Proxies stay float**: `downscale_long_edge` resizes the float channels directly (never a
uint8+dither round-trip), so 16-bit sources keep their precision in analysis proxies.

Output: `save_image` writes JPEG (q95 default, no chroma subsampling — preserves the anti-banding
dither) or **16-bit TIFF** (via tifffile). Writes are **atomic** (temp file + `os.replace`): a crash
mid-write leaves the old file or nothing, never a truncated image a resume would mistake as done.

---

## 3. Segmentation (`segment/`)

A backend takes the proxy image + a list of concept prompts and returns **one `RegionMask` per
detected concept** (instances already unioned). Backends are dumb and swappable behind the
`Segmenter` protocol ([`segment/base.py`](src/autoedit/segment/base.py)); gating/arbitration happen
later. This isolates the SAM license + torch dependency to one file.

- **`StubSegmenter`** — geometric placeholder masks; keeps the whole pipeline runnable with no
  GPU/network (used by tests and the default backend).
- **`Sam3Segmenter`** (`segment/sam3.py`) — SAM 3 open-vocabulary segmentation; torch imported
  lazily; weights fetched from Hugging Face on first run (never committed).

### Per-concept caching (`segment/cache.py`)

`CachingSegmenter` wraps any backend and **memoizes masks per `(image, concept)`**. SAM is the only
slow stage; everything downstream is cheap numpy. So when the UI re-previews the *same* image under
a different style or intensity, only the **new** concepts are segmented — the overlap is served from
cache. (Styles share concepts heavily, so a style switch usually segments one or two fresh concepts
instead of the whole set.) A `None` sentinel remembers "this concept found nothing", so an absent
concept isn't re-run. Keyed by `(path, proxy shape, mtime)`; LRU-bounded to 8 images; pathless
in-memory images bypass it. The resident UI segmenter is wrapped in this cache.

### Resolution / arbitration (`segment/resolve.py`)

Two products from the raw masks:

- **Union masks** (raw, possibly overlapping) — kept for **stats**, where overlap is fine (e.g.
  measuring mean skin luminance even where skin sits inside subject).
- **Arbitrated masks** (non-overlapping label map) — drive the **edits**, so no pixel is edited by
  more than one region. Higher-`priority` concepts claim contested pixels first.

A concept is **present** only if `score ≥ min_score` AND `coverage ≥ min_coverage` AND the mask is
non-empty (per-concept thresholds from `concepts.toml`). Every requested concept (present or not) is
recorded in `detected` for the sidecar audit trail.

---

## 4. Statistics (`stats.py`)

Computed on the proxy. Produces the named variables rules read.

**Global** (`GlobalStatsModel`): `mean_lum`, `mean_sat`, `shadow_clip` (fraction < 0.02),
`highlight_clip` (fraction > 0.98), `haze` (mean of the dark channel `min(R,G,B)` — high = hazy),
`iso`, `exposure_ev`. Convenience vars derived from named regions when present: `sky_lum/sky_sat/
blown_sky`, `subj_lum`, `bg_lum`.

**Pro-tone signals** (v0.4, blind-test-driven — see `PLAN.md`'s retune): `lum_p10/p50/p90`
(percentile black/white points — deliberate levels, not just clip-driven recovery); `fg_lum`/
`fg_p50` (**sky-excluded** mean/median luma — a big bright sky skews `mean_lum` toward "already
bright" while the land sits dark; rules expose for `fg_p50`, not the sky-skewed mean); `veil`
(the *true* atmospheric-haze estimate — requires lifted-dark-channel **and** smooth **and**
desaturated **and** not-near-white, so bright snow/high-key scenes score near 0 where the legacy
`haze` stat — pure dark-channel mean — reads 0.5–0.7 on them); `detail_rms` (mean local luma
detail, drives adaptive clarity); `mean_chroma`/`warmth` (OKLab colourfulness/cast).

**Region-aware auto white balance**: `wb_temp_correction`/`wb_tint_correction`/`wb_confidence` —
measures the colour cast on a natural gray card. SAM's `snow` region is the reference when
present (confidence 1.0 — snow *is* neutral); else bright near-neutral pixels at reduced
confidence (0.6); else confidence 0 (no reference → rules yield identity). This is a
differentiator no global auto has: a segmenter knows *which pixels should be neutral*. The rule
correction is asymmetric and warmth-preserving — up to +12 warm for a blue/overcast cast, floored
at −5 cool, and fades to zero as measured `warmth` rises so golden-hour light is a subject, never
neutralized as a cast.

**Tone-report signals** (dark-RAW diagnosis, see `PROJECT_ASSESSMENT.md`): `lum_p99`, `headroom_ev`
(stops of global lift before the 99.5th percentile reaches white — every exposure rule is capped by
it), `dark_cov` (fraction of pixels below 0.10 luma: dark-but-*unclipped*, which `shadow_clip`'s
0.02 threshold cannot see), and `band_top/mid/bottom_p50` (median luma of the frame's thirds, so a
sidecar shows *where* brightness moved). The same stats are recomputed on the edited pixels with the
same masks (`post_edit_stats` + `post_region_stats`, plus a `name: luma a → b` note per region), and
`scripts/tone_report.py` / `autoedit/tonemetrics.py` turn them into the objective gate: *dark photo
made darker* and *bright photo pushed into clipping*. Exposure rules are **logarithmic** —
`0.6·2.2·log2(target/fg_p50)` — because stops are a ratio of linear light; the earlier linear-delta
form asked for 0.3 EV where the true deficit was ≈3 EV.

**RAW development** (`imageio.RAW_MODES`, `defaults.toml [decode]`): the historical libraw render
(`none`, still the default) sits a median 1.4–1.8 EV under the camera's own embedded JPEG on real
CR2s. Candidates lift the *linear* demosaic before gamma: `preview_ev` (match the embedded preview's
median), `preview_ev_headroom` (same, capped so ≤ `clip_budget` of pixels reach white),
`tone_target`, `libraw_auto`. `imageio.camera_preview()` exposes the camera render every RAW carries;
`scripts/raw_benchmark.py` ranks the candidates against it and only *recommends*.

**Per region** (`RegionStatsModel`): `lum`, `sat`, `clip_low`, `clip_high`, `coverage`. Inside a
region rule block these surface as `region_lum`, `region_sat`, `region_clip_low/high`,
`region_coverage`, and `region_vs_global_lum` (= `region_lum − mean_lum`).

Luminance is Rec.709 on the gamma-encoded values.

---

## 5. Scene classification (`scene.py`)

`classify(g, present, region_stats, override)` → `landscape` / `people` / `other`. Skin must cover
≥ ~8% of the frame (`_SKIN_PORTRAIT_COV`) to be classified `people`; otherwise an incidental face in
a landscape stays `landscape`. The scene name selects which `[scene.*]` rule blocks apply.

---

## 6. Configuration (rules **are data**, not code)

All tuning lives in TOML under `src/autoedit/configs/`. You add a *stat* in Python and *map* it in
TOML; you never hardcode a tuning value that belongs in config.

| File | Role |
|---|---|
| `rules.toml` | Per-`[scene.region]` op = expression. The rule **baseline**. |
| `looks.toml` | Magnitude profiles that scale/offset the baseline (+ curve/grade finishing). |
| `styles.toml` | User-facing styles = a look + default concept set + an LLM brief. |
| `concepts.toml` | Per-concept metadata: priority, gating, feather, guards, policies. |
| `compose.toml` | Compositional moves (vignette, sky graduate). |
| `intents.toml` | Free-text look vocabulary → bounded global nudges (see §10). |
| `defaults.toml` | Default run config. |

### rules.toml + the sandboxed evaluator (`rules.py`)

Sections are `[scene.region]`. `region = "global"` lands on the whole frame; any other name lands on
that resolved mask. Each value is an **expression** evaluated by a small **sandboxed AST evaluator** —
no `eval()`, no attribute access, no arbitrary calls (only `clamp/min/max/abs/if`, arithmetic, and
comparisons), so an edited/untrusted rules file can't execute code. A bad single rule logs and is
skipped — it never kills the photo. Rule keys **are** edit-op names consumed directly by `edit/ops.py`.

Example (current landscape global — the v0.4 pro-tone policy):
```toml
[landscape.global]
# expose for the sky-excluded foreground, not the sky-skewed mean
exposure = "if(fg_p50 > 0, clamp(-(fg_p50 - 0.50) * 0.9, -0.06, 0.32), clamp(-(mean_lum - 0.55) * 0.8, -0.04, 0.30))"
contrast = "if(lum_p90 > 0, clamp(3 + (0.60 - (lum_p90 - lum_p10)) * 40, 3, 16), 5)"   # spread-adaptive
blacks   = "if(lum_p10 > 0, clamp((0.055 - lum_p10) * 180, -18, 0) * (1 - 0.6 * clamp((haze - 0.32) * 2.8, 0, 1)), 0)"
temp     = "clamp(wb_temp_correction * 0.45 * wb_confidence * clamp(1 - max(0, warmth) / 0.035, 0, 1), -5, 12)"
```
Every percentile/WB expression is `if(stat > 0, new, old)`-guarded, so zero-filled stats (older
sidecars, hand-built tests) reproduce the pre-v0.4 output exactly — the policy only activates when
the richer signal is actually available.

Deliberately **"visibly finished," not just corrected** (retuned after two blind tests on real
photos found the old baseline correct-but-invisible on evenly-lit scenes): a deliberate black/white
point, contrast that adapts to the measured tonal spread, and region-aware auto white balance
(§4) all fire by default. Still restrained relative to the assertive looks — `punchy`/`dramatic`
are the opt-in for a stronger commitment.

### looks.toml (`looks.py`) — magnitude shaping

A look never adds new decisions; it scales/offsets existing ones, then everything is scaled by
intensity:

```
final = clamp(base * look_mul + look_add) * intensity
```

Fields: `contrast_mul`, `vibrance_add`, **`saturation_add`** (uniform global lift — vibrance protects
already-saturated pixels, this doesn't; global-only so the skin guard can hold it off faces),
`clarity_mul`, `dehaze_mul`, and global-only finishing `curve_add` (filmic S-curve) +
`grade_{shadows,highlights}_{temp,tint}` (split-tone). `dramatic` is tuned to genuinely commit
(contrast 1.6×, vibrance +14, saturation +12, clarity 1.7×, dehaze 1.8×, deep teal-orange grade).

### styles.toml (`styles.py`)

A **style** bundles `look` + default `concepts` + a natural-language `brief` (combined with the
user's `--prompt` for the LLM decider). Built-ins: `natural` (default, true-to-life),
`editorial` (punchy), `warm_film` (soft portrait), `dramatic` (bold cinematic landscape).

### concepts.toml (`concepts.py`)

Per-concept `ConceptMeta`: `priority` (arbitration order), `min_score`/`min_coverage` (gating),
`feather_px` (mask edge softness, fraction of long edge), `rule_region` (which rule block to use),
`guard_global` (skin-safe guard member, e.g. skin/snow), and **region policies** `forbidden` (ops
dropped entirely on this region) + `max` (per-op magnitude caps).

---

## 7. Deciding the edit (`decide/`)

The decider turns stats into two settings dicts: **global** + **per-region**.

- **`RulesDecider`** (default, `decide/rules_decider.py`) — deterministic, offline, free. Evaluates
  the rule blocks (`rules.apply`), then shapes global + each region by the look and intensity
  (`looks.shape`). This is the whole decision on the default path.
- **`LlmDecider`** (`decide/llm_decider.py`) — optional Anthropic refine/propose on top of the rule
  baseline, steered by the style brief + user prompt. **Degrades safely**: missing `anthropic` / no
  API key / budget cap / any error → falls back to the rules baseline. Off by default, opt-in
  consent required. Only a **thumbnail + stats** leave the machine — never full-res pixels.

`make_decider(cfg)` picks the implementation; `DecisionContext` carries scene/stats/rules/looks/
brief/thumb into it.

---

## 8. Edit ops (`edit/ops.py`) — the frozen order

`apply_ops(rgb, settings)` runs ops in a **frozen order**, each gated on a non-zero amount. An
empty/all-zero settings dict is a **strict no-op** (a test guards identity):

```
white balance → exposure → tone(highlights/shadows/whites/blacks) → contrast → curve
  → dehaze → clarity → vibrance/saturation → targeted HSL → color grade → sharpen
```

Notable ops:

- **exposure** — multiply in linear light (`2^EV`).
- **white_balance** — per-channel gain in linear (temp = R↑/B↓, tint = G).
- **tone** — luminance-weighted lifts for highlights/shadows/whites/blacks.
- **contrast / curve** — linear pivot at 0.5 / filmic smootherstep S-curve on luma.
- **dehaze** — **real dark-channel-prior** (see below).
- **clarity** — local-contrast (luma minus large-radius Gaussian), highlight/shadow protected.
- **vibrance/saturation** — vibrance weights by `(1 − pixel_saturation)` (boosts dull colors more);
  saturation is uniform.
- **targeted_hsl** — saturation/luminance of specific hue bands (skin/orange, green, blue),
  weighted by existing saturation so neutrals stay safe.
- **color_grade** — split-tone: tint shadows and highlights toward different colors.
- **sharpen** — small-radius unsharp mask on luma (output sharpening), applied last.

### Real dehaze (dark-channel prior)

Physical atmospheric-scattering model `I = J·t + A·(1−t)`. The op (`edit/ops.py::dehaze`,
`edit/filters.py::box_min`):

1. Dark channel of the input via a separable patch-min (`box_min`).
2. Atmospheric light **A** = mean color of the brightest ~0.1% of dark-channel pixels (bounded so
   dark scenes don't blow up).
3. Transmission `t = 1 − ω·darkchannel(I/A)`, **refined edge-aware** with the existing
   `guided_filter` (follows real edges → no halos), floored at `t0`.
4. Recover `J = (I − A)/t + A`, then **blend by `amt`** (so a rule value of e.g. 20 = 20% strength,
   and the look's `dehaze_mul` scales it). Negative `amt` adds a light atmospheric veil.

This replaced an earlier fake (contrast+saturation) approximation, and the rule threshold dropped
from a hard `haze>0.45` gate to proportional `(haze − 0.28)·110`, so genuinely foggy frames (~0.41)
actually clear.

---

## 9. Apply & compositing (`edit/apply.py`, `edit/mask.py`, `edit/filters.py`)

`apply_image` composes the final pixels at full resolution:

1. **Region edits** in priority order, with **claim compositing** `a_eff = alpha·(1 − claimed)` so
   even feathered masks never double-edit a pixel.
2. **Compositional moves** (vignette, sky graduate) blended independently — they intentionally
   overlap regions.
3. **Global finishing** over the whole frame. With the **skin-safe guard**, protected regions
   (skin/snow) get the global pass *minus* the high-risk ops (clarity/saturation/vibrance/grade/HSL)
   — faces keep global exposure/tone but not the aggressive look.
4. **Final clip**, then the **oversaturation gate** (see §11).

Masks are upsampled and feathered against the full-res luma with a **guided filter** (`edit/filters.py`)
so a mask alpha snaps to the real sky/hair/ridge boundary instead of haloing. `box_blur` (O(N)
integral-image), `gaussian_blur` (3 box passes), `box_min` (separable erosion), and `guided_filter`
are the dependency-free primitives.

---

## 10. Free-text look intent (`intent.py`, `configs/intents.toml`)

Type what you want — "warm sunset, moody, vibrant" — and it applies **locally, no cloud**. A parser
scans the prompt for known photographic terms and accumulates **bounded global nudges** on top of
the rule baseline, scaled by intensity:

- Vocabulary is **data** (`intents.toml`): `sunset` → warm temp + highlight grade + slight darken;
  `moody` → −exposure/+contrast/−shadows; `black and white` → full desaturate; plus warm/cool/airy/
  vibrant/muted/matte/teal/cinematic/soft/crisp/vintage/film/… Terms match as substrings and
  accumulate; each op is **clamped** so stacking can't go unnatural.
- Applied in `process_one` after the decision, **skipped under the LLM decider** (which already
  consumes the prompt via the brief, to avoid double-applying). The oversaturation gate + skin guard
  still backstop. A `look intent: …` note lands in the sidecar.

---

## 11. Safety: policies, guards, gates

Three independent safety layers, all data-driven:

- **Region policies** (`policies.py`) — hard rails enforced *after* the decider on whatever the
  rules/LLM proposed: drop `forbidden` ops and clamp to `max` magnitudes per concept (e.g. snow
  can't be tinted, skin can't be over-clarified). **Re-enforced after reference tuning** too — the
  reference tuner mutates region settings later in the pipeline, and the rails must win over it.
- **Skin-safe guard** (`pipeline.py::_build_guard` + `edit/apply.py`) — holds aggressive *global*
  ops off `guard_global` concepts (skin/snow). The guarded op list is `rules.toml [guard].ops`.
- **Oversaturation gate** (`edit/gates.py::chroma_guard`) — final pass capping each pixel's chroma
  increase vs the source (`rules.toml [guard].max_chroma_increase`, default +30%); snow/skin and
  near-white highlights held tighter, with the feathered guard mask tightening the ceiling
  *gradually* at region edges. This is what keeps results true-to-life. `gates.assess` then scores
  the **edited** output → quality score + review flag; its thresholds are data too
  (`rules.toml [quality_gate]`, defaults in `gates.GATE_DEFAULTS`).

---

## 12. Shoot consistency (`consistency.py`)

A batch shouldn't edit each photo in a vacuum. A cheap **two-pass anchor**:

1. **Pass 1** — measure each image's brightness from a tiny proxy (`quick_lum`), cluster into
   lighting groups by a 1-D brightness gap split (`plan_groups`), compute a per-group target.
2. **Pass 2** — nudge each image a **bounded ±0.15 EV** toward its group target (`exposure_bias`),
   so a folder doesn't drift in brightness photo-to-photo. The per-scene look still dominates; this
   only removes drift.

Applied by both the CLI batch (`batch.py`) and the UI job runner (`web/jobs.py`), toggleable in the
UI (Settings → Batch consistency, default on).

---

## 13. Output (`emit.py`)

Per photo: a **baked image** (`output_paths` picks the extension from `out_format`; `write_image`
writes JPEG or 16-bit TIFF) plus a **sidecar JSON** (`Result.model_dump`). The sidecar is
first-class — scene + confidence, detected concepts (present + score + coverage), global + per-region
settings, compositional moves, post-edit stats, quality score / needs-review / reasons, batch group,
decider/model, timing, and the **`notes` "why" list** (human-readable rationale: which rules fired,
guard/gate actions, intent terms, batch normalization). The reported settings are the ones that
**actually rendered the pixels** — when the banding gate damps and re-applies, the damped values are
what the sidecar shows. The batch also writes a `run.json` manifest (scene mix, concept hits,
failures, duration).

Same-stem sources (a camera's `IMG_001.dng` + `IMG_001.jpg`, or same-named files across folders)
**never overwrite each other**: `output_paths` takes a per-batch `taken` set and disambiguates with
a `-2`/`-3` suffix, sidecar included. Both image and sidecar writes are atomic (`os.replace`), so
`--resume` (which keys on sidecar existence) can't mistake a partial file for a finished one.

---

## 14. Batch orchestration (`batch.py`)

Enumerate the folder → build the pipeline once → process with **per-image error isolation** (one bad
photo never aborts the run) → optional `resume` (skip photos whose sidecar exists) → write manifest.
Pass 1 of consistency runs here before the loop; `--no-consistent` skips it (each photo judged in
isolation), mirroring the UI's Settings toggle.

---

## 15. The Studio web app (`web/`)

A local FastAPI app (`web/app.py`) over framework-agnostic logic (`web/service.py`), plus a
single self-contained `static/studio.html` (vanilla HTML/CSS/JS, no build step) — a Lightroom/
Capture One-shaped editor: top bar · filmstrip · before/after stage · Develop panel · status bar ·
run drawer · review/settings modals.

### Endpoints

| Route | Purpose |
|---|---|
| `GET /api/styles`, `/api/presets` | List styles / concept presets. |
| `GET /api/list?dir=` | Flat supported-image list (classic UI). |
| `GET /api/groups?dir=` | **RAW+JPEG pairs collapsed** into one logical photo (Studio UI). |
| `GET /api/thumb?path=&px=` | Fast JPEG thumbnail (decode + downscale, no pipeline; `px` clamped ≤2048). |
| `POST /api/pick?kind=` | Native folder/file picker (POST — it registers a trust-boundary root). |
| `POST /api/preview` | Single-image before/after (lossless WebP data-URLs) + decision sidecar. |
| `POST /api/jobs`, `GET /api/jobs/{id}`, `POST .../control` | Background batch with live progress. |

### Trust boundary & safety

The API takes paths from HTTP requests, so every request path must descend from a folder the user
chose via the **native picker** this session (`ensure_allowed` — empty paths rejected, symlinks and
`..` resolved via realpath; `AUTOEDIT_ALLOW_ANY_PATH=1` opts out). Two request guards: a
**Host-header check** on every method rejects non-loopback hosts (DNS-rebinding defense — a hostile
page whose domain re-resolves to 127.0.0.1 would otherwise read responses), and mutating requests
are additionally **Origin-checked** to localhost. `serve` refuses non-loopback binds without
`--unsafe-network`. A **resident segmenter** per `(backend, device)` (cached + the per-concept
cache) means one model load per process; an `INFERENCE_LOCK` serializes GPU inference.

### Preview resolution

`preview()` applies at **full resolution** (bounded to a 4096px long edge for monster files) and
ships before/after capped at 2560px as **lossless WebP** — pixel-exact like PNG (JPEG's DCT would
quantize away the anti-banding dither and re-contour skies) but a much smaller payload.
Segmentation stays at analysis size and is cached, so a re-preview doesn't re-pay SAM cost.

### RAW+JPEG pairing (UI)

`group_images()` groups files sharing a basename (`IMG_001.dng` + `IMG_001.jpg`) into one logical
photo and names the raw/pixel member of each. The UI shows **one filmstrip row per pair** and edits a
**single chosen source** — RAW preferred for latitude — with a per-row **RAW ⇄ JPEG** toggle and a
global Prefer toggle. The batch sends explicit source paths so a paired shoot is **edited once**, not
twice. Thumbnails use the JPEG when present (no slow rawpy decode for a tile). Output format
(JPEG / 16-bit TIFF) is chosen in Settings and flows through as `out_format`.

### Background jobs (`web/jobs.py`)

In-memory job registry; a worker thread builds the pipeline once and iterates photos
(editing → finished/review/failed), writing baked image + sidecar, with per-image isolation,
pause/resume/cancel between photos, and live progress snapshots the UI polls. Runs the consistency
two-pass when enabled.

---

## 16. CLI (`cli.py`)

```
autoedit run <in> <out> [--style …] [--prompt …] [--concepts a,b,c] [--look …]
                        [--intensity F] [--decider rules|llm] [--backend stub|sam3]
                        [--format jpeg|tiff] [--reference img] [--resume] [--dry-run] …
autoedit ui [--host --port --no-open]      # launch Studio
autoedit doctor                            # device + backend availability
autoedit styles | presets                  # list profiles
```

`run.toml`/`defaults.toml` provide defaults; CLI flags and the UI options override them via
`RunConfig` (`contract.py`).

---

## 17. Invariants (don't violate)

1. **One pipeline order** — keep stages swappable; don't fuse them.
2. **Rules are data** — stats→value mappings in TOML, not Python.
3. **Segmenters swappable behind `Segmenter`** — SAM/torch isolated in `segment/sam3.py`; the stub
   always keeps the pipeline runnable with no GPU/network.
4. **Analyze on a proxy, apply at full res** — one arbitrated label map; no pixel edited by two
   regions; globals apply everywhere.
5. **Identity is a strict no-op** — `apply_ops({})` returns the input unchanged (test-guarded).
6. **The LLM path degrades safely** — any failure → rules baseline; off by default; only a
   thumbnail + stats ever leave the machine.
7. **Weights are never committed** — SAM weights fetch from Hugging Face on first run.

---

## 18. Testing

Runnable both as `PYTHONPATH=src python3 tests/run_all.py` (no pytest needed) and pytest-collectable.
**293 tests** currently green. Any new edit op needs a math test; rule changes need a golden test —
percentile/WB rule goldens assert BOTH the new adaptive behavior on populated stats AND exact
byte-for-byte reproduction of the pre-retune output on zero-filled stats. Coverage includes:
identity no-op, each op's math, dehaze (identity + real haze reduction), the `veil` vs legacy
`haze` divergence on bright-but-not-hazy scenes, sky-excluded foreground stats, region-aware
auto-WB (cast measurement + warmth-preserving correction), segmentation cache (overlap reuse,
absent-concept sentinel, LRU), RAW+JPEG grouping, intent parsing, guards/gates, security/trust
boundary, batch consistency, web service preview/jobs, and the blind-test A/B harness itself.

**Definition of done** (per change): test suite green; a stub corpus run completes with sidecars +
manifest; configs/docs updated if behavior changed.

---

## 19. File map

```
src/autoedit/
  pipeline.py            # the 8-stage orchestrator (process_one)
  batch.py               # folder batch + per-image isolation + consistency pass 1
  contract.py            # RunConfig + all pydantic models (Result, stats, settings)
  cli.py                 # command-line interface
  imageio.py             # decode/encode, RAW/16-bit/ICC, EXIF, color helpers
  stats.py               # per-region + global statistics
  scene.py               # scene classification
  rules.py               # sandboxed expression evaluator + rule application
  looks.py               # magnitude shaping (look × intensity)
  styles.py              # style profiles (look + concepts + brief)
  concepts.py            # per-concept metadata + policies
  compose.py             # compositional moves (vignette, sky graduate)
  intent.py              # free-text look intent → bounded global nudges
  policies.py            # post-decision region hard rails
  consistency.py         # two-pass shoot brightness normalization
  notes.py               # human-readable "why" notes
  emit.py                # sidecar + image output, manifest
  paths.py               # config/cache path resolution
  segment/
    base.py              # Segmenter protocol + RegionMask
    stub.py / sam3.py    # backends (no-GPU stub / SAM 3)
    cache.py             # per-concept mask cache (fast style switching)
    resolve.py           # gating + overlap arbitration → label map
  decide/
    base.py              # DecisionContext + Decider protocol
    rules_decider.py     # default deterministic decider
    llm_decider.py       # optional Anthropic refine/propose
  edit/
    ops.py               # the frozen-order edit primitives
    apply.py             # region/compose/global compositing + gates
    filters.py           # box/gaussian blur, box_min, guided_filter
    mask.py              # alpha building / feathering
    gates.py             # oversaturation gate + post-edit assessment
    reference.py         # optional reference-photo color match
  web/
    app.py               # FastAPI routes + middleware
    service.py           # framework-agnostic logic (preview, groups, pipeline)
    jobs.py              # background batch jobs + live progress
    static/studio.html   # the single-file Studio UI
  configs/
    rules.toml looks.toml styles.toml concepts.toml compose.toml intents.toml defaults.toml
```
