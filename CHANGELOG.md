# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased] — Shared highlight budget and reproducible evaluation

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
