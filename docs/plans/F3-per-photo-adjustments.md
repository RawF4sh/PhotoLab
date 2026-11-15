# F3. Per-photo adjustments

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F3.
Builds on [F2](F2-region-instructions.md): the `Adjustment` structure, `instructions.plan`, and
the `adjustments=` parameter of `Pipeline.process_one`.

## Goal

After a batch, one photo can be changed without touching the run settings of the others:

```
autoedit adjust out/IMG_0042.jpg --set sky.exposure=-0.3 --set global.contrast=5
autoedit adjust out/IMG_0042.jpg --edit "warm the grass a little"
autoedit adjust out/IMG_0042.jpg --reset
```

The photo is rendered again from its source with the settings of the original run, plus its own
adjustments. Masks come from a cache on disk, so SAM 3 does not run again. A re-render without any
adjustment gives exactly the pixels of the batch output.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is the mask cache on by default? | Yes, for every backend except the stub: a hidden folder `.autoedit-masks/` in the output folder | `mask_cache = false` under `[segment]` in `defaults.toml`, or `--no-mask-cache` |
| Does a full re-run keep per-photo adjustments? | No. `--resume` keeps adjusted photos; a run without it renders every photo from the run settings again | Read the old sidecar in `batch.run` and pass its `scope == "photo"` adjustments on |
| Where does `adjust` find the source photo? | `run.json` in the output folder (`input_dir` plus the file name), or `--source`. A sidecar never stores a folder path | Nothing to reverse |
| An API endpoint for `adjust`? | Not in this feature. The function `adjust.rerender` is ready for one | Add a route that calls it |

## What a sidecar records from now on

- `adjustments`: every adjustment applied to the photo. Each has a `scope`: `"run"` (from the
  run's `--edit`) or `"photo"` (from `autoedit adjust`). Only `photo` adjustments are carried over
  to the next re-render; `run` adjustments are derived again from the stored run settings.
- `exposure_bias`: the shoot-consistency nudge the batch gave this photo.
- `audit.run_config`: the `RunConfig` of the run, without the config file paths, `api_key`,
  `reference` and `recipe_model`.
- `audit.run_inputs`: `{"reference": <file name or null>, "recipe_model": <file name or null>}`.
  File names only, as the notes already do for a reference.
- `audit.output`: the file name of the exported image.

## Files

| File | Change |
|---|---|
| `src/autoedit/segment/cache.py` | new class `DiskMaskCache`, constant `MASK_CACHE_DIR` |
| `src/autoedit/segment/__init__.py` | export both |
| `src/autoedit/contract.py` | `Adjustment.scope`, `Result.exposure_bias`, `RunConfig.mask_cache` |
| `src/autoedit/paths.py` | `config_paths(configs_dir=None)` |
| `src/autoedit/pipeline.py` | `use_mask_cache`, `known_concepts`, the audit entries, `exposure_bias` |
| `src/autoedit/emit.py` | `write_outputs` |
| `src/autoedit/adjust.py` | new: `parse_set`, `rerender` |
| `src/autoedit/batch.py`, `src/autoedit/web/jobs.py`, `src/autoedit/web/service.py` | use `use_mask_cache` and `write_outputs` |
| `src/autoedit/cli.py` | `adjust` command, `--no-mask-cache`, `[segment] mask_cache`, `config_paths` |
| `src/autoedit/configs/defaults.toml` | `mask_cache = true` under `[segment]` |
| `tests/test_adjust.py` | new |

Not touched: `edit/`, `instructions.py` (except what a test shows to be wrong), `rules.py`,
`decide/`, the config files other than `defaults.toml`.

## `segment/cache.py`

Add below `CachingSegmenter`. Needs `import json`.

```python
MASK_CACHE_DIR = ".autoedit-masks"


class DiskMaskCache:
    """Keep each photo's raw masks on disk, keyed by the proxy pixels, so a re-render
    (`autoedit adjust`), a resumed batch or a later run with another style does not segment
    again. One .npz per photo: every concept tried so far, plus a JSON index. Never pickled, so
    a file planted in an output folder cannot run code."""

    def __init__(self, inner: Segmenter, root: str):
        self.inner, self.root = inner, root

    def __getattr__(self, name):            # `.name` and the like of the wrapped segmenter
        return getattr(self.inner, name)

    def _path(self, img: Image) -> str:
        pixels = np.ascontiguousarray(img.rgb)
        digest = hashlib.blake2b(memoryview(pixels).cast("B"), digest_size=16).hexdigest()
        return os.path.join(self.root, digest + ".npz")

    @staticmethod
    def _read(path: str) -> dict:
        try:
            with np.load(path, allow_pickle=False) as z:
                index = json.loads(bytes(z["index"]).decode())
                return {e["name"]: (RegionMask(name=e["name"], mask=z[e["key"]].astype(bool),
                                               score=float(e["score"]), coverage=float(e["coverage"]),
                                               instance_count=int(e["instances"]))
                                    if e.get("key") else None)
                        for e in index}
        except Exception:                   # missing, truncated or foreign file: segment again
            return {}

    def _write(self, path: str, bucket: dict) -> None:
        os.makedirs(self.root, exist_ok=True)
        index, arrays = [], {}
        for i, (name, rm) in enumerate(bucket.items()):
            if rm is None:                  # tried, found nothing
                index.append({"name": name})
                continue
            arrays[f"m{i}"] = np.asarray(rm.mask, dtype=bool)
            index.append({"name": name, "key": f"m{i}", "score": float(rm.score),
                          "coverage": float(rm.coverage), "instances": int(rm.instance_count)})
        arrays["index"] = np.frombuffer(json.dumps(index).encode(), dtype=np.uint8)
        with open(path + ".part", "wb") as fh:
            np.savez_compressed(fh, **arrays)
        os.replace(path + ".part", path)

    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        path = self._path(img)
        bucket = self._read(path)
        missing = [c for c in concepts if c not in bucket]
        if missing:
            fresh = {rm.name: rm for rm in self.inner.segment(img, missing)}
            bucket.update({c: fresh.get(c) for c in missing})
            try:
                self._write(path, bucket)
            except OSError:                 # a read-only output folder must not fail the photo
                pass
        return [bucket[c] for c in concepts if bucket.get(c) is not None]
```

The wrapped segmenter is only called on a miss. `Sam3Segmenter` loads its model on first use, so a
re-render that hits the cache never loads SAM 3.

## `contract.py`

- `Adjustment.scope: Literal["run", "photo"] = "run"`, after `source`.
- `Result.exposure_bias: float = 0.0`, after `batch_group`, comment
  `# shoot-consistency nudge this photo got (needed to re-render it alone)`.
- `RunConfig.mask_cache: bool = True`, in the segmentation block, comment
  `# keep raw masks under <out>/.autoedit-masks (not for the stub) so re-renders skip segmentation`.

## `paths.py`

```python
_CONFIG_FILES = ("rules", "looks", "concepts", "styles", "compose", "intents", "palettes", "instructions")


def config_paths(configs_dir: str | None = None) -> dict[str, str]:
    """RunConfig path arguments for a configs folder. A file the folder does not have is left
    out, so RunConfig falls back to its default (an older custom folder keeps working)."""
    base = configs_dir or globals()["configs_dir"]()
    return {f"{name}_path": os.path.join(base, f"{name}.toml") for name in _CONFIG_FILES
            if os.path.exists(os.path.join(base, f"{name}.toml"))}
```

Use it in `cli.load_run_config` and `service.make_cfg` in place of their hand-written path
blocks. (The parameter shadows the module function `configs_dir`; rename the parameter if you
prefer, but keep the keyword `configs_dir` for callers.)

## `pipeline.py`

```python
    def known_concepts(self) -> set[str]:
        """Concept names the configs define; the instruction parser maps plurals onto them."""
        return set(self.meta) | {c for cs in self.presets.values() for c in cs}

    def use_mask_cache(self, out_dir: str) -> None:
        """Keep raw masks under `out_dir` so `autoedit adjust` and later runs skip segmentation.
        Not for the stub: its masks are instant and deterministic."""
        if (self.cfg.mask_cache and self.cfg.backend != "stub"
                and not isinstance(self.segmenter, DiskMaskCache)):
            self.segmenter = DiskMaskCache(
                self.segmenter, os.path.join(out_dir, MASK_CACHE_DIR, self.cfg.backend))
```

`Pipeline.build` uses `known_concepts` logic for the parser (F2 computed the same set inline).

At the end of `process_one`, where `result.audit.update(...)` already runs, add:

```python
        result.exposure_bias = float(exposure_bias)
        result.audit.update(
            run_config=cfg.model_dump(mode="json", exclude=_NOT_STORED),
            run_inputs={k: (os.path.basename(v) if v else None)
                        for k, v in (("reference", cfg.reference), ("recipe_model", cfg.recipe_model))})
```

with, at module level:

```python
# Never written into a sidecar: local paths and the secret. `adjust` takes the config paths from
# the current environment and asks for the two input files again.
_NOT_STORED = {name for name in RunConfig.model_fields if name.endswith("_path")} | {
    "api_key", "reference", "recipe_model"}
```

## `emit.py`

```python
def write_outputs(out_rgb, result: Result, image_out: str, sidecar: str, cfg, source: str) -> None:
    """Write the baked image (when there are pixels) and the sidecar that explains it."""
    if out_rgb is not None:
        note = write_image(out_rgb, image_out, cfg.out_format, cfg.quality,
                           source=source, metadata=cfg.metadata)
        if note:
            result.notes.append(note)
        result.audit["output"] = os.path.basename(image_out)
    write_sidecar(result, sidecar)
```

`batch.run`, `jobs._run` and `service.run_one` call it in place of their own
write-image, append-note, write-sidecar blocks. Each of the three also calls
`pipeline.use_mask_cache(out_dir)` once, right after the pipeline is built. In `batch.run` a
dry run passes `out_rgb=None`, as today no image is written.

## `adjust.py`

```python
"""Re-render one photo from its sidecar, with per-photo adjustments (F3)."""

from __future__ import annotations

import json
import os

from . import emit, instructions
from .contract import Adjustment, Result, RunConfig
from .edit.ops import OP_NAMES
from .paths import config_paths
from .pipeline import Pipeline

_SIDECAR = ".autoedit.json"


def parse_set(text: str) -> Adjustment:
    """"sky.exposure=-0.3" -> a photo adjustment. The target is `global` or a concept."""
    left, eq, value = text.partition("=")
    target, dot, op = left.strip().rpartition(".")
    target = " ".join(target.lower().split())
    if not eq or not dot or not target or op not in OP_NAMES:
        raise ValueError(f"--set expects TARGET.OP=VALUE (for example sky.exposure=-0.3), got {text!r}")
    try:
        delta = float(value)
    except ValueError:
        raise ValueError(f"--set {text!r}: {value!r} is not a number") from None
    return Adjustment(target=target, ops={op: delta}, scope="photo")


def _source_from_manifest(out_dir: str, image: str) -> str | None:
    try:
        with open(os.path.join(out_dir, "run.json")) as fh:
            path = os.path.join(json.load(fh)["input_dir"], image)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return path if os.path.isfile(path) else None


def rerender(target: str, sets=(), edit: str | None = None, reset: bool = False,
             source: str | None = None, configs_dir: str | None = None,
             reference: str | None = None, recipe_model: str | None = None) -> dict:
    """Render the photo behind `target` (its exported image or its sidecar) again. Stored
    per-photo adjustments are kept unless `reset`; `sets` and `edit` add new ones."""
    sidecar = target if target.endswith(_SIDECAR) else os.path.splitext(target)[0] + _SIDECAR
    if not os.path.isfile(sidecar):
        raise FileNotFoundError(f"no sidecar at {sidecar}")
    with open(sidecar) as fh:
        old = Result.model_validate_json(fh.read())
    stored = old.audit.get("run_config")
    if not stored:
        raise ValueError("this sidecar does not record its run settings; "
                         "render the photo again with `autoedit run` first")
    out_dir = os.path.dirname(os.path.abspath(sidecar))
    source = source or _source_from_manifest(out_dir, old.image)
    if not source or not os.path.isfile(source):
        raise FileNotFoundError(f"source photo for {old.image} not found; pass --source")
    given = {"reference": reference, "recipe_model": recipe_model}
    for key, name in (old.audit.get("run_inputs") or {}).items():
        if name and not given.get(key):
            raise ValueError(f"the run used --{key.replace('_', '-')} {name}; pass it again")
    cfg = RunConfig(**{**config_paths(configs_dir), **stored,
                       **{k: v for k, v in given.items() if v}})
    pipe = Pipeline.build(cfg)
    pipe.use_mask_cache(out_dir)
    kept = [] if reset else [a.model_copy(update={"status": ""})
                             for a in old.adjustments if a.scope == "photo"]
    new = [parse_set(s) for s in sets]
    problems: list[str] = []
    if edit and edit.strip():
        parsed, problems = instructions.parse(edit, pipe.instr, pipe.known_concepts())
        new += [a.model_copy(update={"scope": "photo"}) for a in parsed]
    out_rgb, result = pipe.process_one(source, adjustments=kept + new,
                                       exposure_bias=old.exposure_bias, batch_group=old.batch_group)
    result.notes += [f"instruction {p}" for p in problems]
    image_out = os.path.join(out_dir, old.audit.get("output") or (
        os.path.basename(sidecar)[:-len(_SIDECAR)] + (".tif" if cfg.out_format == "tiff" else ".jpg")))
    emit.write_outputs(out_rgb, result, image_out, sidecar, cfg, source)
    return {"image": image_out, "sidecar": sidecar, "problems": problems,
            "adjustments": [a.model_dump() for a in result.adjustments]}
```

`Pipeline.process_one` copies the adjustments it is given, so the run-level ones keep the default
scope `run` and the ones passed in keep `photo`.

## `cli.py`

- `adjust` sub-command: positional `target` (help: `an exported photo or its .autoedit.json`),
  `--set` (`action="append"`, default `[]`, metavar `TARGET.OP=VALUE`), `--edit`, `--reset`
  (`store_true`), `--source`, `--reference`, `--recipe-model`, `--configs-dir`.
- `_cmd_adjust`: calls `adjust.rerender`; `FileNotFoundError` and `ValueError` print to stderr
  and return 2. On success print one line:
  `Re-rendered <image name>: <n> adjustments (<a> applied, <c> clamped, <f> not found)` and one
  line per problem.
- `--no-mask-cache` on `run` (`dest="mask_cache"`, `store_false`, `default=None`); `_flatten`
  reads `mask_cache` from `[segment]`; `load_run_config` passes the override.
- Add `adjust` to the command list in the module docstring.

## Tasks

One Builder (Sonnet, medium).

| Task | Content | Proving command |
|---|---|---|
| T1 | `DiskMaskCache`, the contract fields, `config_paths`, `use_mask_cache`, `known_concepts`, the audit entries, `write_outputs` and its three callers, tests 1, 2 and 10 | `PYTHONPATH=src python3 -m pytest tests/test_adjust.py tests/test_segment_cache.py tests/test_jobs.py tests/test_web_service.py tests/test_cli.py -q` |
| T2 | `adjust.py`, the CLI, `defaults.toml`, tests 3 to 9 | `PYTHONPATH=src python3 tests/run_all.py` |

## Tests: `tests/test_adjust.py`

Plain functions with `tmp_path`. Build inputs with the synthetic image of
`tests/test_pipeline.py::_make` (sky on top, foliage below, a skin-coloured ellipse).

| # | Test | Asserts |
|---|---|---|
| 1 | `test_disk_mask_cache` | With a fake inner segmenter that counts its calls: the first `segment` call asks the inner one; a second `DiskMaskCache` on the same folder returns equal masks, scores and instance counts without asking; a concept that found nothing is remembered and not asked again; asking for one more concept asks the inner one for that concept only; an image with other pixels gets its own file; a file overwritten with garbage is ignored and segmented again; no file in the folder is a pickle (`np.load(..., allow_pickle=False)` reads it) |
| 2 | `test_use_mask_cache` | A pipeline with the stub backend keeps its segmenter. After `pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})` and `pipe.segmenter = <fake>`, `use_mask_cache(out)` wraps it once (a second call does not wrap again) under `<out>/.autoedit-masks/sam3`; with `mask_cache=False` it does not wrap |
| 3 | `test_rerender_without_adjustment_is_identical` | `batch.run` on a folder with two photos of different brightness (so the consistency bias is not zero for at least one; assert that), stub backend. For each output: read the bytes, call `adjust.rerender(image)`, the bytes are identical and the sidecar is equal once `timing_ms` and `stage_timings_ms` are removed |
| 4 | `test_adjustment_changes_only_its_target` | `rerender(image, sets=["sky.exposure=-0.4"])`: the mean of the top 40 rows of the decoded image drops by more than 5 levels; the bottom 20 rows differ from the batch output by at most 2 levels. The sidecar has one adjustment with scope `photo`, status `applied`, target `sky` |
| 5 | `test_adjustments_accumulate_and_reset` | A second `rerender` with `sets=["global.contrast=5"]` leaves two `photo` adjustments in the sidecar. `rerender(image, reset=True)` leaves none and the image bytes equal the batch output again |
| 6 | `test_resume_keeps_adjusted_photo` | After an adjustment, `batch.run(..., resume=True)` leaves the adjusted image bytes and sidecar untouched |
| 7 | `test_parse_set` | `"sky.exposure=-0.3"`, `" Red Jacket.temp = 4 "` (target `red jacket`) and `"global.contrast=5"` parse; `"sky.exposure"`, `"exposure=1"`, `"sky.nope=1"` and `"sky.exposure=abc"` raise `ValueError` |
| 8 | `test_rerender_errors` | A sidecar without `audit.run_config` raises `ValueError`. With `run.json` removed, `rerender` raises `FileNotFoundError`, and works again with `source=`. A sidecar whose `audit.run_inputs.reference` is set raises `ValueError` unless `reference=` is passed |
| 9 | `test_cli_adjust` | `cli.main(["adjust", image, "--set", "sky.exposure=-0.2"])` returns 0 and changes the image; `cli.main(["adjust", image, "--set", "nonsense"])` returns 2; `cli.main(["adjust", image, "--edit", "brighten the unicorn"])` returns 0 and the sidecar records `not_found` |
| 10 | `test_sidecar_holds_no_local_path` | After `batch.run` into `tmp_path`: the sidecar text contains neither the input folder path nor the output folder path; `audit.run_config` has no key ending in `_path` and no `api_key`, `reference` or `recipe_model`; `RunConfig(**audit["run_config"])` validates; `audit.output` is the image file name; `exposure_bias` is a float |

Existing tests that replace `emit.write_image` or `emit.write_sidecar` in `tests/test_jobs.py` or
`tests/test_web_service.py` may need to follow the call into `write_outputs`. Keep what they
assert; report each change.

## Acceptance checks (the session runs these)

1. Both test suites green.
2. The stub smoke run completes with sidecars and a manifest.
3. Default renders are pixel-identical to the baseline (reference sets A and B and the smoke
   corpus).
4. On two real photos with the SAM 3 backend: a batch run writes `.autoedit-masks/sam3`;
   `autoedit adjust` with no adjustment gives byte-identical output without loading SAM 3
   (measured by time); with `--set sky.exposure=-0.3` the sky gets darker.
5. The Checker reviews this feature together with F2's follow-ups.

## Known limits

- A region edit runs before the global pass. A global op that measures the whole frame (the
  dehaze airlight) can therefore shift pixels far from the adjusted region by a small amount.
- Outputs written by the Studio jobs have no `run.json`; `adjust` then needs `--source`.
- Re-rendering uses the config files that are current when `adjust` runs. If `rules.toml` changed
  since the batch, the re-render differs from the batch output.
- The mask cache is keyed by the proxy pixels. It does not notice new SAM weights; delete
  `.autoedit-masks/` after changing them.

## Result

Closed on 2026-11-11, in the unattended run.

- **Built** by one Builder (Sonnet, medium). One review by a Checker (Opus, high), together with
  the F2 fixes; its 13 findings and their fixes are in "Review round 2" above. A second Checker
  pass confirmed all 13 as fixed.
- **Tests:** `tests/test_adjust.py`.
- **Default output:** byte-identical to the renders made before the feature. Every sidecar gained
  the fields listed under "What a sidecar records from now on", plus a digest of the config files.
- **With SAM 3,** two 22 MP photos: the batch wrote `.autoedit-masks/sam3` (21 KB and 35 KB);
  `autoedit adjust` without an adjustment reproduced both exports byte for byte in 25 s each,
  without loading the model; `--set sky.exposure=-0.3 --edit "brighten the trees"` darkened the
  top third from 169 to 152 of 255 and left the bottom quarter unchanged (largest difference 0).


## Review round 2 (2026-11-11)

The Checker (Opus, high) reviewed F3 together with the F2 fixes. One blocking finding and a list
of smaller ones. This section replaces the parts above that it contradicts. Three snippets above
are already out of date since F2's review round 1 (see that section in
[F2-region-instructions.md](F2-region-instructions.md)): `paths.config_paths` sets the seven
original paths always and leaves out only a missing `instructions.toml`; `adjust.rerender` uses
`pipe.vocab()`; the summary line of `autoedit adjust` counts `blocked` too.

### Findings and what changes

| # | Finding | Change |
|---|---|---|
| 1 | Blocking. `--set sky.exposure=nan` rendered a black photo and wrote a sidecar that could not be read again | `parse_set` rejects a value that is not finite; `instructions._summed` skips one |
| 2 | A re-render of a run that used the LLM decider called the cloud again on the batch's consent, or fell back to the rules without saying so | `adjust` needs `--allow-cloud` for such a run and refuses to write when the decider changed |
| 3 | Policy inheritance looked at the whole target and its last word only: "skin tones" and "face of the man" got no skin policy, "snow field" got the grass policy | Every word of the target is checked; all matches apply |
| 4 | A cell whose settings ended up empty claimed nothing, so the part of a layer outside every region could spill into it and bypass its policy | In a region an instruction reached, a cell without settings still claims its pixels |
| 5 | "instead of", "rather than", "or", "more than" passed, and the excluded thing ended up inside the target | Added to `[parser] reject`, with "percent" and "times" |
| 6 | "brighten and the jacket" applied the verb twice; two amount words: the first won silently | Duplicates within a clause are dropped; a part with two amounts is rejected |
| 7 | Combining marks were dropped from targets in scripts that use them | Tokens are NFC-normalised and keep marks |
| 8 | The configs folder of the batch is not recorded; a re-render with other config files differs silently | The sidecar stores a digest of the config files; `adjust` warns when it differs |
| 9 | `--reference` was only checked for presence; `--recipe-model` was demanded for runs that never used one | The file name must match the stored one; `recipe_model` is recorded only for the learned decider |
| 10 | A hand-edited `audit.output` could point outside the output folder | Only its base name is used |
| 11 | The disk cache key ignored the shape of the proxy; the temp file name was fixed | Shape and dtype are part of the key; a mask of another shape is dropped; a unique temp file |
| 12 | `autoedit adjust` printed a traceback for a read-only folder | `OSError` is caught |
| 13 | Six tests could not fail for the reason they exist | Strengthened, see below |

### `adjust.py`

- `parse_set`: after `float(value)`, `if not math.isfinite(delta): raise ValueError(f"--set {text!r}: the value must be a finite number")`.
- `rerender(..., allow_cloud: bool = False)`. Directly after `stored` is read:

```python
    # Consent for the cloud is given per command: what the batch was allowed does not carry over.
    stored = {**stored, "allow_cloud": bool(allow_cloud)}
    if stored.get("decider") == "llm" and not allow_cloud:
        raise ValueError("the run used the LLM decider; a re-render calls it again (a thumbnail "
                         "leaves the machine and your key is billed). Pass --allow-cloud to allow it")
```

- The stored inputs:

```python
    for key, name in (old.audit.get("run_inputs") or {}).items():
        if not name:
            continue
        flag = "--" + key.replace("_", "-")
        if not given.get(key):
            raise ValueError(f"the run used {flag} {name}; pass it again")
        if os.path.basename(given[key]) != name:
            raise ValueError(f"the run used {flag} {name}, not {os.path.basename(given[key])}")
```

- After `process_one`, before anything is written:

```python
    if result.decider != old.decider:
        raise ValueError(f"the re-render used the {result.decider} decider, the run used "
                         f"{old.decider}; nothing was written")
    warnings: list[str] = []
    if old.audit.get("config_digest") and old.audit["config_digest"] != pipe.config_digest:
        warnings.append("the config files differ from the ones the batch used; "
                        "the re-render may differ from the batch output")
    result.notes += [f"re-render: {w}" for w in warnings]
```

- The output name is `os.path.basename(old.audit.get("output") or <the default name>)`.
- The returned dict gets `"warnings": warnings`.

### `pipeline.py`

- A field `config_digest: str = ""`, set in `build`:

```python
def _config_digest(cfg: RunConfig) -> str:
    """A short hash of the config files a run reads, so a re-render can tell that they changed."""
    h = hashlib.blake2b(digest_size=8)
    for name in sorted(n for n in RunConfig.model_fields if n.endswith("_path")):
        try:
            with open(getattr(cfg, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()
```

- `result.audit.update(...)` also sets `config_digest=self.config_digest`; `run_inputs` records
  `recipe_model` only when `cfg.decider == "learned"` (else `None`).
- The apply loop for a plan: a region that an instruction reached passes ALL its cells, also the
  ones without settings; an untouched region behaves as it always did.

```python
                for group, concept, touched in zip(adj_plan.groups, adj_plan.feather, adj_plan.touched):
                    cells = [(fmask, region_settings.get(cell.name) or {}) for cell, fmask in group]
                    if not touched:
                        cells = [(fmask, s) for fmask, s in cells if s]
                        if not cells:
                            continue
                    feather_px = meta_for(concept, self.meta).feather_px
                    entry = [(build_alpha(fmask, full_hw, feather_px, guide=guide), s) for fmask, s in cells]
                    regions_apply.append(entry if touched else entry[0])
```

### `instructions.py`

- `Plan.touched: list[bool]`, one per group: `True` once a layer reached one of its cells (the
  two places that append to `hit[target]` inside the group loop), and `True` for every group made
  from the part of a layer outside the regions.
- `_summed`: `if not math.isfinite(float(delta)): continue` before a delta is added.
- `_policy_owners`, complete:

```python
def _policy_owners(target: str, meta: dict[str, ConceptMeta], vocab: Vocab) -> list[str]:
    """The concepts whose region policy a target must obey: itself, and before it every protected
    concept one of its words names ("skin tones" -> skin, "snow field" -> snow and grass)."""
    owners: list[str] = []
    for word in [target, *target.split()]:
        for cand in (word, word[:-1] if word.endswith("s") else word + "s"):
            like = vocab.policy.get(cand) or (cand if cand in meta else None)
            if like and like != target and like not in owners:
                owners.append(like)
                break
    return owners + [target]
```

- `_toks`, complete (needs `import unicodedata`):

```python
def _toks(text: str) -> tuple[str, ...]:
    """Words of a text: letters, marks and digits of any script, with an inner apostrophe or
    hyphen ("don't", "t-shirt"). NFC first, and a typographic apostrophe must not hide a negation."""
    text = unicodedata.normalize("NFC", text.lower().replace("’", "'"))
    out, cur = [], []
    for ch in text + " ":
        if unicodedata.category(ch)[0] in "LMN" or (ch in "'-" and cur):
            cur.append(ch)
        elif cur:
            out.append("".join(cur).rstrip("'-"))
            cur = []
    return tuple(t for t in out if t)
```

- `parse`: a part with more than one amount word rejects the clause with the reason
  `more than one amount` (next to the check for more than one verb; keep all amounts of a part in
  the part to count them). When the adjustments of a clause are collected, one that equals an
  earlier one of the same clause (same target and ops) is dropped.

### `edit/apply.py`

In the sibling-cell branch a cell without settings claims its pixels as an unchanged copy:

```python
            for alpha, settings in entry:
                w = np.clip(np.minimum(alpha * free, free - total), 0.0, None)
                if float(w.max()) <= 1e-4:
                    continue
                # A cell without settings still claims its pixels, as an unchanged copy, so the
                # part of a layer outside the regions cannot spill into it and bypass its policy.
                part = (ops.apply_ops(out, settings) if settings else out) * w[..., None]
                mix = part if mix is None else mix + part
                total = total + w
```

### `segment/cache.py`

- `_path`: the digest covers `repr((pixels.shape, str(pixels.dtype))).encode()` and then the
  pixel bytes.
- `_read(path, shape)`: an entry whose mask shape differs from `shape` (the proxy's first two
  dimensions) is dropped, so that concept is segmented again.
- `_write`: a unique temp file in the cache folder (`tempfile.mkstemp(dir=self.root, suffix=".part")`,
  written through `os.fdopen`), then `os.replace`; the temp file is removed when writing fails.

### `instructions.toml`, `cli.py`

- `[parser] reject` gains `"instead"`, `"rather"`, `"than"`, `"or"`, `"percent"`, `"times"`.
- `autoedit adjust` gets `--allow-cloud` (`store_true`), catches `OSError` like the other two
  errors, and prints each warning of the result as `warning: ...`.

### Tests

| Test | Change |
|---|---|
| `test_parse_set` | `"sky.exposure=nan"`, `"sky.exposure=inf"` and `"sky.exposure=-inf"` raise `ValueError` |
| `test_parse_rejects` (instructions) | `"brighten the sky instead of the grass"`, `"brighten the sky rather than the grass"`, `"brighten the sky or the grass"`, `"brighten the sky more than the grass"`, `"brighten the sky twenty percent"`, `"slightly more brighten the sky"` give no adjustment and a problem |
| `test_parse_golden` (instructions) | `"brighten and the jacket"` gives exactly one adjustment. `_toks` keeps combining marks: a Devanagari or Thai word comes back as one token, and NFD `"café"` equals NFC `"café"` |
| `test_policy_inheritance` | `"skin tones"` gives `["skin", "skin tones"]`; `"face man"` gives `["skin", "face man"]`; `"snow field"` gives `["snow", "grass", "snow field"]` |
| `test_split_region_leaves_no_ring` | Both runs also get `Adjustment(target="subject", ops={"exposure": 0.7})`, so the region carries a strong edit. Keep the bound 0.003 |
| new `test_empty_cell_still_claims` (apply level) | `apply_image(img, [[(a1, {}), (a2, s)], (a_bg, t)], {})` where `a_bg` covers the area of `a1` as well: inside `a1` the result equals the input (the later entry cannot edit it) |
| new `test_blocked_instruction_does_not_spill` (pipeline level) | A fake segmenter with `snow` and a `jacket` that crosses the snow outline, `concepts=["snow"]`, rules that give snow no settings (pass a rules file copy without the `[<scene>.snow]` blocks, or use a scene whose rules have none), `edit="warm the jacket"`: inside the snow region, away from its outline, the output equals the run without `edit` within 1e-4 |
| `test_rerender_errors` | A sidecar whose `run_config.decider` is `"llm"` raises `ValueError` without `allow_cloud=True`. A reference with another file name than the stored one raises. A sidecar whose `audit.output` is `"../x.jpg"` writes inside the output folder |
| new `test_rerender_warns_when_configs_changed` | After a batch, `rerender` with `configs_dir` pointing at a copy of the configs whose `looks.toml` has one value changed returns a warning and adds a `re-render:` note; with the same configs there is no warning |
| new `test_rerender_through_the_disk_cache` | A pipeline with a non-stub fake segmenter behind `DiskMaskCache` (as `test_use_mask_cache` builds it), `process_one` twice: the second run does not call the fake and gives identical pixels |
| `test_disk_mask_cache` | Two proxies with the same bytes and different shapes (16 by 32 and 32 by 16, both flat) get different files; a cache file whose stored mask has another shape than the proxy is ignored for that concept |
| `test_sidecar_holds_no_local_path` | The batch runs with a `reference` (a file in `tmp_path`) and a `prompt`; the sidecar still contains no folder path; `run_inputs.reference` is the file name; `run_inputs.recipe_model` is `None` |

### Known limits (added)

- A run that used the LLM decider is not reproduced exactly by a re-render: the model is asked
  again.
- `adjust` warns when the config files differ from the batch's, but it cannot restore them.

### Proving commands

`PYTHONPATH=src python3 -m pytest tests/test_instructions.py tests/test_adjust.py tests/test_apply_groups.py tests/test_segment_cache.py tests/test_parallel.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py` (501 tests before this round, 0 failed).
