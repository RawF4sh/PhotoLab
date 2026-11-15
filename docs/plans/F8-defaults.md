# F8. Defaults

Status: spec (2026-11-11). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F8.

## What this feature is, in an unattended run

F8 is three decisions that belong to the owner. This run changes **no default**. It builds the
two small mechanisms the decisions need, so that each decision becomes a one-line change, and it
collects the evidence for the third.

| Decision | What exists after this feature | The one-line change, if the owner says yes |
|---|---|---|
| Default backend `sam3` when the model is installed | `backend = "auto"`: SAM 3 when it is installed and its weights are in the local cache, else the stub | `backend = "auto"` under `[segment]` in `defaults.toml` |
| May a moody look opt out of the darkness gate? | A look flag `preserve_exposure` (default false), read like the "moody" prompt words | `preserve_exposure = true` under the look in `looks.toml` |
| RAW development default | The tone gate numbers for `none` and `preview_ev_headroom`, and a blind gallery | `raw_mode = "preview_ev_headroom"` under `[decode]` in `defaults.toml` |

## 1. `backend = "auto"`

- `RunConfig.backend: Literal["stub", "sam3", "auto"] = "stub"`. `--backend` accepts `auto`.
- `segment/__init__.py`:

```python
def sam3_ready(model_id: str = "facebook/sam3") -> bool:
    """True when SAM 3 can run without a download: torch and transformers are installed and the
    weights are in the local Hugging Face cache. Imports neither torch nor the model."""
    import importlib.util

    try:
        if not (importlib.util.find_spec("torch") and importlib.util.find_spec("transformers")):
            return False
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(model_id, "config.json"), str)
    except Exception:
        return False


def resolve_backend(backend: str) -> str:
    """"auto" -> "sam3" when it is ready, else "stub". Any other name is returned as is."""
    return ("sam3" if sam3_ready() else "stub") if backend == "auto" else backend
```

- `Pipeline.build` resolves once, at the top:
  `cfg = cfg.model_copy(update={"backend": resolve_backend(cfg.backend)})` when the backend is
  `auto`. Everything after that sees `stub` or `sam3`: the segmenter, the mask cache folder, the
  sidecar's `run_config` (so `adjust` repeats the backend that actually ran).
- `batch.run` reads the backend from `pipeline.cfg`, not from its argument, for the manifest, for
  `_effective_workers` and for the worker configuration.
- `autoedit doctor` prints one more line: `backend "auto" would use: sam3` or `stub`.

## 2. Look flag `preserve_exposure`

- `looks.Look.preserve_exposure: bool = False`, read from `looks.toml` (`preserve_exposure`).
- `pipeline.process_one`: the existing `preserve = ...` line becomes

```python
            preserve = (bool(cfg.prompt) and any(t in cfg.prompt.lower() for t in preserve_terms)) or \
                self.looks.get(look, looks_mod.Look()).preserve_exposure
```

- No packaged look sets the flag. A comment at the top of `looks.toml` documents it:
  `# preserve_exposure = true: this look means to be dark; the darkness check of the review gate`
  `# does not apply to it (as for the prompt words "moody", "low key", ...).`

The flag only changes the review flag and the quality score of a photo. It does not change pixels.

## 3. RAW development default: evidence only

The session runs, on the Dolomites RAW corpus:

- `scripts/tone_report.py <raws> --render --fail-on-regression` (the default, `raw_mode = none`);
- the same with `--raw-mode preview_ev_headroom`;

and records for both: dark to darker, pushed into clipping, the median distance to the camera
render in EV, and the count of photos that stay dark. A blind gallery of the two modes on RAW
holdout photos is prepared for the owner's vote.

## Files

`src/autoedit/segment/__init__.py`, `src/autoedit/contract.py`, `src/autoedit/pipeline.py`,
`src/autoedit/batch.py`, `src/autoedit/cli.py`, `src/autoedit/looks.py`,
`src/autoedit/configs/looks.toml` (comment only), `tests/test_defaults.py` (new).

## Task

One Builder (Sonnet, medium). Proving command: `PYTHONPATH=src python3 -m pytest tests/test_defaults.py tests/test_cli.py tests/test_parallel.py tests/test_quality_gate.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

## Tests: `tests/test_defaults.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_auto_resolves_to_the_stub_without_weights` | With `segment.sam3_ready` replaced by a function returning `False` (restored in `finally`): `resolve_backend("auto") == "stub"`, `Pipeline.build(RunConfig(backend="auto")).cfg.backend == "stub"`, and a `batch.run` on one synthetic photo writes `"backend": "stub"` into `run.json` and into the sidecar's `audit.run_config` |
| 2 | `test_auto_resolves_to_sam3_when_ready` | With `sam3_ready` returning `True`: `resolve_backend("auto") == "sam3"`; `resolve_backend("stub") == "stub"` and `resolve_backend("sam3") == "sam3"` whatever `sam3_ready` says. Do not build a pipeline here (that would load SAM 3) |
| 3 | `test_sam3_ready_never_raises` | With `importlib.util.find_spec` replaced by a function that raises, `sam3_ready()` is `False` |
| 4 | `test_default_backend_is_unchanged` | `RunConfig().backend == "stub"` and the packaged `defaults.toml` says `backend = "stub"` |
| 5 | `test_look_flag_opts_out_of_the_darkness_check` | A looks file copy (in `tmp_path`) with a look `moody_test` that has `preserve_exposure = true` and `exposure_mul = 1.0`, and a rules-independent way to make the output darker than a dark source (a direct global adjustment `exposure -0.6` through `process_one(adjustments=...)`): with `look="natural"` the result is flagged `needs_review` with a reason containing `darker`; with `look="moody_test"` that reason is absent. The pixels of the two runs are equal when the two looks have the same values |
| 6 | `test_no_packaged_look_sets_the_flag` | Every look in the packaged `looks.toml` has `preserve_exposure == False` |
| 7 | `test_cli_accepts_auto` | `--backend auto` parses and reaches `RunConfig.backend == "auto"` (before `Pipeline.build`) |

## Result

Built on 2026-11-11, in the unattended run. Status: done; no default changed.

- **Built** by one Builder (Sonnet, medium): `backend = "auto"` (`segment.sam3_ready`,
  `segment.resolve_backend`, resolved in `Pipeline.build`), the look flag `preserve_exposure`,
  and a `doctor` line that says what `auto` would use.
- **Deviations accepted:** `batch.run` builds the pipeline first and takes its config from it, so
  the manifest and the render workers see the resolved backend. The test of the look flag cuts
  the exposure by 1.5 EV (a cut of 0.6 is cancelled by the rules' own lift).
- **Fixed by the session:** the web service builds its own segmenter and passed `"auto"` on
  unresolved. `service.make_cfg` now resolves it (test added).
- **Review** (Checker, Opus, high), fixed in "Review round 1 (F8 to F11)" of the
  [F11 spec](F11-user-config-layer.md): `auto` chose SAM 3 on an interpreter whose Transformers
  cannot import SAM 3, and every photo failed. `sam3_ready` now also needs the weights file in the
  cache and, as the last word, a real import of the SAM 3 classes (done once, inside
  `segment/sam3.py`): on this machine the system Python has the packages, and the import still
  fails on a broken binary in a dependency, which no cheaper check sees. `make_segmenter`
  resolves `auto` itself, which covers the quality runner and the blind test harness. The test of the look flag
  compared looks that differed in other fields; it now fails when the flag is ignored.
- **On this machine** `auto` resolves to `sam3` with the project's own environment (`.venv`): the
  weights are in the local cache. With the system Python it resolves to the stub, because the
  SAM 3 import fails there. The default
  is still `stub`.
- **RAW development default, the evidence** (tone gate, 110 RAW files of the Dolomites corpus):

  | `raw_mode` | Dark to darker | Into clipping | Median EV against the camera's render | Foreground still dark after the edit | Pulled back by the safety check |
  |---|---|---|---|---|---|
  | `none` (default) | 0 | 0 | -1.33 | 39 of 110 | 12 |
  | `preview_ev_headroom` | 0 | 0 | -0.80 | 23 of 110 | 14 |

  Both pass the gate. The candidate lands half a stop nearer to the camera's own render and
  leaves fewer photos dark; two more photos need the safety fallback. "Still dark" means a
  foreground median below 0.30. A blind gallery of the two modes is in
  `out-features/galleries/raw-mode/`.
- **Default output:** byte-identical to the baseline renders; both test suites green.
- **Open for the owner:** the three one-line decisions in the table at the top of this file.
