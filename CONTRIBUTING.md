# Contributing to AutoEdit

Thanks for your interest! AutoEdit is a side project — issues and PRs are welcome on a
best-effort basis.

## Setup

```bash
git clone https://github.com/RawF4sh/AutoEdit
cd AutoEdit
pip install -e .            # core (numpy, Pillow, pydantic) — runs with the stub segmenter
# optional extras: pip install -e '.[sam]'  '.[llm]'  '.[ui]'  '.[accel]'
```

Run the tests (no pytest required, but it works too):

```bash
PYTHONPATH=src python tests/run_all.py
# or: python -m pytest
```

Try it end to end:

```bash
autoedit run ./some-photos ./out --style editorial --dry-run
autoedit doctor      # device + backend status
```

## The easiest, highest-value contribution: tuning

Most of AutoEdit's behavior is **data, not code** — no recompiling to tune:

- [src/autoedit/configs/rules.toml](src/autoedit/configs/rules.toml) — stats → per-region edit values
- [src/autoedit/configs/styles.toml](src/autoedit/configs/styles.toml) — selectable styles (look + concepts + brief)
- [src/autoedit/configs/looks.toml](src/autoedit/configs/looks.toml) — magnitude shaping + split-tone
- [src/autoedit/configs/compose.toml](src/autoedit/configs/compose.toml) — radial/linear compositional moves
- [src/autoedit/configs/concepts.toml](src/autoedit/configs/concepts.toml) — concept presets + metadata

Edit, run on real photos, and open a PR with before/after examples.

## Conventions (see [CLAUDE.md](CLAUDE.md))

- Core stays dependency-light (numpy/Pillow/pydantic + stdlib); torch/Anthropic/FastAPI are
  optional extras, imported lazily. The stub segmenter must keep the pipeline runnable with
  no GPU/network.
- New edit ops need a math test; `apply_ops({})` must stay a strict no-op.
- Per-image failures are isolated — one bad photo never aborts a batch.
- The LLM path is opt-in (`--allow-cloud` + a key) and degrades to rules.

## PRs

Use a GitHub noreply email for commits. Do not attach private photos, API keys, or
unreviewed sidecars. Before pushing, run `python3 scripts/check_public_repo.py --history`
and a redacted Gitleaks scan as described in [SECURITY.md](SECURITY.md).

Keep them focused, include tests for behavior changes, and make sure
`PYTHONPATH=src python tests/run_all.py` is green.
