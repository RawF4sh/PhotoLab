# CLAUDE.md — working rules for this repo

Guidance for Claude (and humans) working in AutoEdit. Read before changing code.

> History: this began as a Lightroom-plugin idea (Rust+Lua, see `PLAN.md`/`OVERVIEW.md`).
> It pivoted (v0.2) to a **standalone open-vocabulary batch photo editor** in Python. See
> `README.md` and `CHANGELOG.md`. The current code is `src/autoedit/`.

## What this is

A CLI that batch-edits a folder of photos: SAM 3 segments **arbitrary named concepts** →
per-region + global edits (rule baseline, optional LLM refine) → baked output + a sidecar
JSON explaining every decision. The product/wedge is **open-vocabulary region editing**;
the segmentation itself is not the differentiator (Lightroom/Imagen/Aftershoot/Evoto already
auto-mask fixed regions).

## Architecture invariants (don't violate)

1. **One pipeline order.** `decode → segment → resolve → stats → scene → decide → apply →
   emit` (`pipeline.py`). Keep stages swappable; don't fuse them.
2. **Rules are data, not code.** Stats→value mappings live in `configs/rules.toml`, looks in
   `configs/looks.toml`, concept metadata in `configs/concepts.toml`. Add a stat in Python,
   map it in TOML. Never hardcode a tuning value that belongs in config.
3. **Segmenters are swappable behind `segment/base.py::Segmenter`.** SAM (license + torch) is
   isolated in `segment/sam3.py`; never let torch/SAM imports leak elsewhere. The stub must
   always keep the pipeline runnable with no GPU/network.
4. **Analyze on a proxy, apply at full res.** Masks computed on the downscaled proxy,
   upsampled + feathered at apply. Never edit a pixel from more than one region (arbitrated
   label map in `segment/resolve.py`); global ops apply everywhere.
5. **Identity is a strict no-op.** `edit/ops.py::apply_ops({})` must return the input
   unchanged (a test guards this). Each op is gated on a non-zero amount.
6. **The LLM path degrades safely.** Missing `anthropic` / no API key / budget cap / any
   error → fall back to the rules baseline. Off by default. Only a thumbnail + stats leave
   the machine, never full-res pixels.
7. **Weights are never committed.** SAM weights fetch from Hugging Face on first run.

## Conventions

- Core runtime deps stay minimal (numpy, Pillow, pydantic + stdlib) so the stub pipeline runs
  anywhere; torch / Anthropic / OpenCV are **optional extras**, imported lazily.
- Per-image failures are isolated in `batch.py` — one bad photo never aborts the run.
- The sidecar `notes` "why" list is first-class (debugging + future "explain this edit").
- Tests: keep them runnable via `PYTHONPATH=src python tests/run_all.py` (no pytest needed)
  AND pytest-collectable. Any new edit op needs a math test; rule changes need a golden test.

## Definition of done (per change)

- `PYTHONPATH=src python tests/run_all.py` green.
- `autoedit run ./test-corpus ./out --backend stub` completes with sidecars + manifest.
- Config/docs updated if behavior changed.
- Any exposure/tone/decode change: `PYTHONPATH=src python scripts/tone_report.py <raw-folder> --render --fail-on-regression` reports 0 dark→darker and 0 pushed-into-clipping (use the in-repo
  `curated-dolomites-summer-2026/selected_raw` corpus when present; it never runs in CI).

## Planning and delegation

- Planned engine features, their order and their status: `docs/FEATURE_PLAN.md`. Feature specs
  live in `docs/plans/`.
- Before delegating work to a sub-agent, read `docs/AGENT_ROUTING.md` and set the sub-agent's
  `model` and `effort` exactly as it specifies for that kind of work: planning on Fable at `max`
  effort, checks on Opus at `high`, building from a spec on Sonnet at `medium`, mechanical edits
  and searches on Haiku at `low`. That file is authoritative if this summary and it ever differ.
- When the owner asks for work without supervision ("do the rest, don't ask me"), follow
  `docs/UNATTENDED_RUN.md`: no questions, no git writes, default pixels unchanged, owner decisions
  recorded instead of asked, and one commit block at the end.

## Agent skills

### Issue tracker

Specs as `docs/plans/F<N>-<slug>.md` with a row in `docs/FEATURE_PLAN.md`, tickets as `docs/plans/F<N>-tickets/NN-<slug>.md`; never GitHub Issues, never a commit. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels, written as a `Status:` line in each ticket. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
