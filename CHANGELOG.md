# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

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

### Changed
- `rules.toml` exposure is **EV-correct**: `0.6·2.2·log2(target/fg_p50)`, capped by `headroom_ev`
  (landscape/other/people; `other` keeps the subject-first priority). Shadows respond to the
  foreground-median deficit and dark coverage, not only to clipped pixels. Contrast and blacks fade
  to zero on dark sources. Mountain/grass/foliage region lifts are EV-correct and relative to the
  foreground median so they compose with the global lift. Legacy zero-filled stats reproduce the old
  output exactly (all new terms sit behind `pro_stats_valid`).
- Tests: 293 (was 265), including per-op proof that no tone op erases a dark-frame lift.

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
