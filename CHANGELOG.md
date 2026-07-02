# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

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
