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
