# F4. Throughput

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F4.
Builds on [F3](F3-per-photo-adjustments.md): the disk mask cache lets render workers run without
a segmentation model.

## Goal

Render the same pixels faster. Targets from the plan: 10 s or less for one 22 MP photo, and at
least three times the batch throughput on the development machine (10 cores, 64 GB). The measured
maximum pixel difference against renders made before this feature must be 0.

## Measured before the design (2026-11-11)

On a 22 MP JPEG with SAM 3 masks from the cache (four regions, natural style), `.venv` Python:

| Step | Time on the full frame |
|---|---|
| `light` (white balance, exposure, knee) | 0.79 s |
| `vibrance` | 0.83 s |
| targeted HSL plus saturation (sky region) | 2.3 to 2.5 s |
| `tone`, `curve`, `shadow_lift`, `contrast` | 0.13 to 0.21 s each |
| `clarity`, `sharpen` (OpenCV blur) | 0.23 to 0.24 s each |
| Feathered alpha for one region | 0.22 s |

- Per-pixel ops take about three quarters of the apply stage. They run on one core.
- **A per-pixel op chain gives bit-identical values on a horizontal strip and on the full frame**
  (maximum difference 0.0 for every region's chain, strips of 64 to 512 rows). Gathering pixels
  into an N by 1 array does not (1e-7), so only strips are used.
- Running strips one after another is not faster (2.0 s against 2.1 s): the work is bound by
  compute, not by memory. Running them in threads is the gain. numpy releases the GIL inside its
  loops.
- Skipping work where a region's alpha is zero gains little without changing pixels: the feather
  leaves values of 1e-6 and below over 3 to 7 % of the frame, which stretches a region's bounding
  box to most of the frame. A cut-off would change pixels, so it is not used.
- Ops that read neighbours or the whole frame (`dehaze`, `clarity`, `sharpen`) cannot be cut into
  strips: the box blur keeps running sums, so a strip rounds differently. They stay on the full
  frame. OpenCV already runs them on several cores.

## Design

1. **Strips in threads** (`edit/par.py`, new). `par.rows(fn, *arrays)` runs a per-pixel function
   on strips of 256 rows in a thread pool and joins the results. Frames below 1.5 MP run as one
   call, so tests and previews keep their path.
2. **`apply_ops` uses it** for its two per-pixel stretches: the tone steps before `dehaze` and the
   colour steps after `clarity`. The order of the steps does not change, and no op's math changes.
   The frozen order becomes data (`ops.STEPS`) so that tests and `apply.py` can refer to it.
3. **The guarded global pass shares its prefix.** With a skin or snow guard, `apply_image` renders
   the global settings twice: once in full, once without the guarded ops. Every step before the
   first guarded op is the same in both, so it is computed once. This is exact: applying a bundle
   in two calls cut between two steps is the same sequence of function calls.
4. **Blends run in strips too** (`out * (1 - a) + edited * a` in `apply_image`).
5. **Render workers** (`batch.run(..., workers=N)`). Processes started with `spawn`, one pipeline
   per worker. With a model backend the parent segments each photo, one at a time, into the disk
   mask cache and hands the photo to a worker, which reads the masks from the cache and never loads
   the model. Threads are divided among the workers.

Not in this feature: a GPU port (see "What a GPU port would have to cover" in the Result), and
any change to `gates.chroma_guard` (its early exit makes a strip version inexact; it is a
protected file).

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Default number of render workers | 1, as today. `--workers 0` picks half the cores, at most 4 | `[performance] workers` in `defaults.toml` |
| Default number of threads | One per core (`threads = 0`) | `[performance] threads`, `--threads`, or `AUTOEDIT_THREADS` |
| The LLM decider with workers | Runs with one worker: its call and budget caps are counted per process | Remove the check in `batch._effective_workers` |

## Files

| File | Change |
|---|---|
| `src/autoedit/edit/par.py` | new |
| `src/autoedit/edit/ops.py` | `SPATIAL_OPS`, `STEPS`, `apply_ops` split into two per-pixel helpers and the driver. No change to any op function |
| `src/autoedit/edit/apply.py` | shared prefix in the guarded pass; blends through `par.rows` |
| `src/autoedit/pipeline.py` | `presegment`; `par.set_threads(cfg.threads)` in `build` |
| `src/autoedit/batch.py` | workers |
| `src/autoedit/contract.py` | `RunConfig.workers`, `RunConfig.threads` |
| `src/autoedit/cli.py`, `src/autoedit/configs/defaults.toml` | `--workers`, `--threads`, `[performance]` |
| `tests/test_parallel.py` | new |

## `edit/par.py`

```python
"""Row-strip threads for per-pixel work (F4).

A per-pixel function gives a pixel the same value whatever array the pixel sits in. So running it
on horizontal strips in threads and joining the strips is bit-identical to one call over the
frame; tests/test_parallel.py holds every op to that. numpy releases the GIL inside its loops, so
the strips really run side by side. A function that reads neighbouring pixels or a statistic of the
whole frame (a blur, dehaze) must not go through here. Never call `rows` from inside `rows`.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

MIN_PIXELS = 1_500_000   # a smaller frame is one call: threads would cost more than they save
STRIP_ROWS = 256
_threads: int | None = None
_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def threads() -> int:
    """Threads for strip work: set_threads(), else AUTOEDIT_THREADS, else one per core."""
    if _threads is not None:
        return _threads
    try:
        n = int(os.environ.get("AUTOEDIT_THREADS", "0"))
    except ValueError:
        n = 0
    return n if n > 0 else (os.cpu_count() or 1)


def set_threads(n: int | None) -> None:
    """Fix the thread count of this process. None or 0 means automatic."""
    global _threads, _pool
    with _lock:
        _threads = n if n and n > 0 else None
        if _pool is not None:
            _pool.shutdown(wait=True)
            _pool = None


def rows(fn, *arrays: np.ndarray) -> np.ndarray:
    """`fn(*arrays)`, computed strip by strip. The arrays share their first two dimensions."""
    global _pool
    h, w = arrays[0].shape[:2]
    n = threads()
    if n <= 1 or h * w < MIN_PIXELS or h < 2 * STRIP_ROWS:
        return fn(*arrays)
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="autoedit-rows")
        pool = _pool
    parts = pool.map(lambda y: fn(*(a[y:y + STRIP_ROWS] for a in arrays)), range(0, h, STRIP_ROWS))
    return np.concatenate(list(parts), axis=0)
```

## `edit/ops.py`

Add after `OP_BOUNDS` (`_GRADE_KEYS` and `_HSL_KEYS` move above it):

```python
# Ops that read more than their own pixel: neighbours (blurs) or the whole frame (dehaze airlight).
SPATIAL_OPS = frozenset({"dehaze", "clarity", "sharpen"})

# The frozen order as data: one tuple per step of apply_ops, in order. A settings bundle may be
# cut between two steps and applied in two calls with the same result (apply.py relies on it).
STEPS = (
    ("temp", "tint", "exposure", "highlight_rolloff"),
    ("shadows_ev",),
    ("highlights", "shadows", "whites", "blacks"),
    ("contrast",),
    ("curve",),
    ("dehaze",),
    ("clarity",),
    ("vibrance", "saturation"),
    _HSL_KEYS,
    _GRADE_KEYS,
    ("sharpen",),
)
_TONE_KEYS = frozenset(k for step in STEPS[:5] for k in step)
_COLOUR_KEYS = frozenset(k for step in STEPS[7:10] for k in step)
```

Replace `apply_ops` by the following. `_tone_steps` and `_colour_steps` hold the existing lines of
`apply_ops`, moved without any change:

```python
def _tone_steps(out: np.ndarray, settings: dict) -> np.ndarray:
    """The per-pixel steps before dehaze: light, shadow lift, tone, contrast, curve."""
    temp, tint = settings.get("temp", 0.0), settings.get("tint", 0.0)
    ev, rolloff = settings.get("exposure", 0.0), settings.get("highlight_rolloff", 0.0)
    if temp or tint or ev or rolloff:  # WB → exposure → knee, one linear pass (frozen order kept)
        out = light(out, temp, tint, ev, rolloff)
    if settings.get("shadows_ev", 0.0):  # black-pinned lift, before the additive tone sliders
        out = shadow_lift(out, settings["shadows_ev"])
    if any(settings.get(k, 0.0) for k in ("highlights", "shadows", "whites", "blacks")):
        out = tone(out, settings.get("highlights", 0.0), settings.get("shadows", 0.0),
                   settings.get("whites", 0.0), settings.get("blacks", 0.0))
    if settings.get("contrast", 0.0):
        out = contrast(out, settings["contrast"])
    if settings.get("curve", 0.0):
        out = curve(out, settings["curve"])
    return out


def _colour_steps(out: np.ndarray, settings: dict) -> np.ndarray:
    """The per-pixel steps after clarity: vibrance and saturation, targeted HSL, split-tone."""
    if settings.get("vibrance", 0.0) or settings.get("saturation", 0.0):
        out = vibrance(out, settings.get("vibrance", 0.0), settings.get("saturation", 0.0))
    if any(settings.get(k, 0.0) for k in _HSL_KEYS):
        out = targeted_hsl(out, settings.get("hsl_skin_sat", 0.0), settings.get("hsl_green_sat", 0.0),
                           settings.get("hsl_green_lum", 0.0), settings.get("hsl_blue_sat", 0.0))
    if any(settings.get(k, 0.0) for k in _GRADE_KEYS):
        out = color_grade(out, settings.get("grade_shadows_temp", 0.0), settings.get("grade_shadows_tint", 0.0),
                          settings.get("grade_highlights_temp", 0.0), settings.get("grade_highlights_tint", 0.0))
    return out


def apply_ops(rgb: np.ndarray, settings: dict) -> np.ndarray:
    """Apply a settings bundle in the frozen order (`STEPS`). Does not mutate the input. The
    per-pixel stretches run on row strips in threads (edit/par.py), which changes no value."""
    out = rgb
    if any(settings.get(k, 0.0) for k in _TONE_KEYS):
        out = par.rows(lambda a: _tone_steps(a, settings), out)
    if settings.get("dehaze", 0.0):
        out = dehaze(out, settings["dehaze"])
    if settings.get("clarity", 0.0):
        out = clarity(out, settings["clarity"])
    if any(settings.get(k, 0.0) for k in _COLOUR_KEYS):
        out = par.rows(lambda a: _colour_steps(a, settings), out)
    if settings.get("sharpen", 0.0):  # output sharpening, last
        out = sharpen(out, settings["sharpen"])
    return out
```

`from . import par` goes with the imports. With an empty or all-zero bundle `apply_ops` still
returns the input object itself.

## `edit/apply.py`

The guarded global pass. Replace the body of `if guard_mask is not None and guard_ops:` with:

```python
            # Two renders by design: the kept ops include ones whose result depends on the whole
            # frame, so the guarded pass cannot run on a crop. Every step before the first guarded
            # op is the same in both renders, so it runs once (exact: a bundle cut between two
            # steps of ops.STEPS and applied in two calls is the same sequence of calls).
            cut = next((i for i, step in enumerate(ops.STEPS)
                        if any(k in guard_ops and global_settings.get(k) for k in step)),
                       len(ops.STEPS))
            first = {k for step in ops.STEPS[:cut] for k in step}
            pre = {k: v for k, v in global_settings.items() if k in first}
            post = {k: v for k, v in global_settings.items() if k not in first}
            base = ops.apply_ops(out, pre) if pre else out
            full = ops.apply_ops(base, post)
            kept = {k: v for k, v in post.items() if k not in guard_ops}
            guarded = ops.apply_ops(base, kept) if kept else base
            out = par.rows(lambda f, g, m: f * (1.0 - m[..., None]) + g * m[..., None],
                           full, guarded, guard_mask)
```

The region and compositional blends become
`out = par.rows(lambda o, e, a: o * (1.0 - a[..., None]) + e * a[..., None], out, edited, a_eff)`
(the same expression per pixel; `a3` is no longer needed). The sibling-cell branch from F2 keeps
its own expressions.

## `pipeline.py`

- In `build`: `par.set_threads(cfg.threads)` (import `par` from `.edit`).
- New method:

```python
    def presegment(self, path: str) -> None:
        """Decode and segment one photo, nothing else: fills the mask cache for a render worker."""
        concepts = self.concepts_for(style_for(self.cfg.style, self.styles))
        extra = instructions.extra_concepts(self.adjustments, concepts)
        if concepts or extra:
            img = load_image(path, decode=raw_opts_from(self.cfg))
            self.segmenter.segment(downscale_long_edge(img, self.cfg.analysis_long_edge),
                                   concepts + extra)
```

The proxy is built with the same two calls as in `process_one`, so the cache key matches.

## `contract.py`, `cli.py`, `defaults.toml`

- `RunConfig.workers: int = Field(default=1, ge=0, le=64)`, comment
  `# render processes for a batch; 0 = half the cores, at most 4`.
- `RunConfig.threads: int = Field(default=0, ge=0, le=256)`, comment
  `# threads for per-pixel work inside one render; 0 = one per core`.
- `defaults.toml`: a new table

```toml
[performance]
workers = 1                # render processes for a batch; 0 = half the cores, at most 4
threads = 0                # threads inside one render; 0 = one per core
```

- `_flatten` reads both from `[performance]`; `--workers` and `--threads` (type int) on `run`;
  `_cmd_run` passes `workers=cfg.workers` to `batch.run`.

## `batch.py`

`run(cfg, input_dir, out_dir, do_apply=True, resume=False, consistent=True, workers=None)`.
`workers=None` means `cfg.workers`. With one worker the existing loop runs unchanged.

```python
def _effective_workers(cfg: RunConfig, workers: int) -> tuple[int, str]:
    """(count, reason it was reduced or "")."""
    n = workers if workers > 0 else min(4, max(1, (os.cpu_count() or 2) // 2))
    if n > 1 and cfg.decider == "llm":
        return 1, "the LLM decider counts its calls and budget per process"
    if n > 1 and cfg.backend != "stub" and not cfg.mask_cache:
        return 1, "render workers need the mask cache with a model backend"
    return n, ""


class _NoModel:
    """Stands in for the segmenter inside a render worker: the parent fills the mask cache."""

    def segment(self, img, concepts):
        raise RuntimeError("mask cache miss in a render worker: " + ", ".join(concepts))


_WORKER: dict = {}


def _worker_init(cfg: RunConfig, out_dir: str, threads: int) -> None:
    pipe = Pipeline.build(cfg.model_copy(update={"threads": threads}))
    pipe.use_mask_cache(out_dir)
    if isinstance(pipe.segmenter, DiskMaskCache):
        pipe.segmenter.inner = _NoModel()
    _WORKER["pipe"] = pipe


def _render(path: str, image_out: str, sidecar: str, bias: float, gid: str, do_apply: bool) -> dict:
    pipe = _WORKER["pipe"]
    out_rgb, result = pipe.process_one(path, do_apply=do_apply, exposure_bias=bias, batch_group=gid)
    emit.write_outputs(out_rgb if do_apply else None, result, image_out, sidecar, pipe.cfg, path)
    return {"scene": result.scene, "group": result.batch_group, "ms": result.timing_ms,
            "regions": [m.target for m in result.masks],
            "present": [c.name for c in result.concepts if c.present]}
```

For more than one worker, `run`:

1. prints one line when `_effective_workers` reduced the count, with the reason;
2. opens `ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"),
   initializer=_worker_init, initargs=(cfg, out_dir, max(1, par.threads() // n)))`;
3. walks the images in order: assigns the output paths (the same `taken` logic), applies
   `resume`, computes the consistency bias, and, when the backend is not the stub, calls
   `pipeline.presegment(path)` (a failure there is recorded for that photo and the photo is
   skipped) before `pool.submit(_render, ...)`;
4. reports each finished photo with the same progress line as today, as results come in (also
   while it is still submitting), and collects failures per photo. A worker exception fails that
   photo only;
5. writes the same manifest, plus `"workers": n`.

The consistency pass 1 stays in the parent. Output paths are assigned by the parent in input
order, so the file names do not depend on which worker finishes first.

## Tasks

Two Builders, one after the other (both touch `pipeline.py` and `contract.py`).

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder (Sonnet, high: protected file `ops.py`) | `par.py`, `ops.py`, `apply.py`, `Pipeline.build` thread setting, `RunConfig.threads`, `--threads`, tests 1 to 6 | `PYTHONPATH=src python3 -m pytest tests/test_parallel.py tests/test_ops.py tests/test_ops_phase2.py tests/test_guard.py tests/test_tone_stack.py -q` then `PYTHONPATH=src python3 tests/run_all.py` |
| T2 | Builder (Sonnet, medium) | `presegment`, `batch.py` workers, `RunConfig.workers`, `--workers`, `[performance]`, tests 7 to 9 | `PYTHONPATH=src python3 tests/run_all.py` |

## Task T3: the two largest per-pixel stretches that are left (added after measuring T1)

After T1 a 22 MP render takes 8.2 to 11.2 s (before: 18.4 to 23.7 s). The slowest of the three
test photos is above the 10 s target. Its profile shows two per-pixel stretches that still run on
one core: the last lines of `dehaze` (three calls, 2.9 s together) and `gates.chroma_guard`
(1.5 s). Both can go through `par.rows` without changing a value.

### `edit/par.py`

```python
def each(fn, *arrays: np.ndarray) -> list:
    """`fn` on every row strip of the arrays, results as a list in strip order (for a reduction
    that needs one small value per strip). Below the size limits: one call, a list of one."""
    global _pool
    h, w = arrays[0].shape[:2]
    n = threads()
    if n <= 1 or h * w < MIN_PIXELS or h < 2 * STRIP_ROWS:
        return [fn(*arrays)]
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="autoedit-rows")
        pool = _pool
    return list(pool.map(lambda y: fn(*(a[y:y + STRIP_ROWS] for a in arrays)), range(0, h, STRIP_ROWS)))
```

`rows` becomes `np.concatenate(each(fn, *arrays), axis=0)` when there is more than one strip and
`fn(*arrays)` otherwise (same behaviour as today).

### `edit/ops.py`, `dehaze`

Only the last five lines change: the per-pixel inversion of the scattering model moves into a
local function that runs on strips. `A`, `t0`, the transmission `t` and the blend map are computed
exactly as today, on the whole frame.

```python
    if work is not rgb:
        t = _resize_float(t, (w, h))
        blend = _resize_float(blend, (w, h))

    def invert(x, t_, blend_):          # per pixel: runs on row strips, same values
        t3 = np.clip(t_, t0, 1.0)[..., None]
        recovered = (x - A) / t3 + A    # invert the scattering model
        return (x + blend_[..., None] * (np.clip(recovered, 0.0, 1.0) - x)).astype(np.float32)

    return par.rows(invert, rgb, t, blend)
```

### `edit/gates.py`, `chroma_guard`

Protected file. The per-pixel math does not change; it moves into two local functions so that it
can run on strips. The early exit stays a decision about the whole frame.

```python
    source = source.astype(np.float32, copy=False)   # only if the function did not already assume it
    allowance = max(0.0, float(neutral_allowance))
    arrays = (source, edited) if protect is None else (source, edited, protect)

    def ceiling_of(src, out, prot=None):
        src_c = _chroma(src)
        ceiling = src_c * (1.0 + max_increase) + allowance
        if prot is not None:
            p = np.clip(prot.astype(np.float32), 0.0, 1.0)
            protected = np.minimum(ceiling, src_c * (1.0 + protect_increase) + 0.008)
            ceiling = ceiling * (1.0 - p) + protected * p
        hi = luma(out) > highlight_lum
        return np.where(hi, np.minimum(ceiling, src_c * 1.02 + 0.004), ceiling)

    def count_over(src, out, prot=None):
        return int((_chroma(out) > ceiling_of(src, out, prot)).sum())

    def guard(src, out, prot=None):
        out_c = _chroma(out)
        ceiling = ceiling_of(src, out, prot)
        over = out_c > ceiling
        l = luma(out)[..., None]
        scale = np.ones_like(out_c)
        np.divide(ceiling, out_c, out=scale, where=over)
        scale = np.clip(scale, 0.0, 1.0)[..., None]
        return np.clip(l + (out - l) * scale, 0.0, 1.0).astype(np.float32)

    n_over = sum(par.each(count_over, *arrays))
    if n_over == 0:
        return edited.astype(np.float32), 0.0
    return par.rows(guard, *arrays), n_over / float(edited.shape[0] * edited.shape[1])
```

Keep the existing docstring and comments; move the comments to the lines they describe. The
returned fraction was `float(over.mean())`: the count divided by the number of pixels is the same
number. Do not add a `source.astype` line if the current code does not have one: keep whatever
dtype handling is there.

### Tests (add to `tests/test_parallel.py`)

| # | Test | Asserts |
|---|---|---|
| 10 | `test_dehaze_strips_are_bit_identical` | With `par.MIN_PIXELS` set to 0: `dehaze(x, 30)` on a 700 by 900 random hazy frame (values lifted toward 0.7) under 4 threads equals 1 thread (`array_equal`). Same on a 1500 by 1700 frame, where dehaze works on its internal proxy |
| 11 | `test_chroma_guard_strips_are_bit_identical` | With `par.MIN_PIXELS` set to 0, for a source and an edit with clearly raised saturation (700 by 900): pixels and fraction under 4 threads equal 1 thread exactly, with and without a `protect` mask. With an edit equal to the source the result is `(edited as float32, 0.0)` in both modes |
| 12 | `test_chroma_guard_matches_the_previous_formula` | The result equals a copy of the pre-change function body written out in the test (take it from `git show HEAD:src/autoedit/edit/gates.py`), `array_equal` on the pixels and exact equality of the fraction, on a 96 by 96 frame with and without `protect` |
| 13 | `test_each_returns_one_value_per_strip` | `par.each(lambda a: a.shape[0], x)` sums to the height in both modes; `rows` still equals one call |

Proving command: `PYTHONPATH=src python3 -m pytest tests/test_parallel.py tests/test_gates.py tests/test_dehaze.py tests/test_guard.py tests/test_quality_gate.py -q`, then
`PYTHONPATH=src python3 tests/run_all.py`.

## Tests: `tests/test_parallel.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_rows_is_bit_identical_for_every_per_pixel_op` | With `par.MIN_PIXELS` set to 0 (restored in `finally`) and a random float32 image of 700 by 900: for five random settings bundles that together use every op outside `SPATIAL_OPS` with a non-zero value, `apply_ops` under `par.set_threads(4)` equals `apply_ops` under `par.set_threads(1)` with `np.array_equal`. Same for an image of 701 by 333 |
| 2 | `test_steps_list_every_op_once` | The names in `ops.STEPS`, flattened, equal `set(ops.OP_NAMES)` with no duplicate |
| 3 | `test_a_bundle_can_be_cut_between_any_two_steps` | For a bundle with every op non-zero and every cut position: `apply_ops(apply_ops(x, pre), post)` equals `apply_ops(x, bundle)` with `np.array_equal` (96 by 96 image) |
| 4 | `test_guarded_global_pass_is_unchanged` | `apply_image` with a guard mask and `guard_ops = {"clarity", "saturation", "vibrance", "grade_shadows_temp"}` equals the old formula written out in the test (`full = apply_ops(x, g)`, `kept` without the guard ops, `full * (1 - m) + guarded * m`, then the final clip) with `np.array_equal`. Also with a guard op in the first step (`"exposure"`), where nothing can be shared |
| 5 | `test_identity_returns_the_input_object` | `apply_ops(x, {}) is x` and `apply_ops(x, {"exposure": 0.0}) is x` |
| 6 | `test_thread_setting` | `par.set_threads(3)` gives `par.threads() == 3`; `set_threads(None)` with `AUTOEDIT_THREADS=2` gives 2; a `Pipeline.build(RunConfig(threads=5))` sets 5. Everything is restored in `finally` |
| 7 | `test_workers_write_the_same_files` | `batch.run` on five small synthetic photos plus one broken file, stub backend, once with `workers=1` and once with `workers=2`, into two folders: the same file names, identical image bytes, sidecars equal once `timing_ms` and `stage_timings_ms` are removed, the same failure for the broken file, `run.json` equal except `duration_s`, `out_dir` and `workers` |
| 8 | `test_effective_workers` | The LLM decider gives 1 with a reason; a non-stub backend with `mask_cache=False` gives 1 with a reason; `workers=0` gives a value between 1 and 4; `workers=3` with the stub gives 3 |
| 9 | `test_worker_never_loads_a_model` | `_worker_init` on a config whose pipeline has a `DiskMaskCache` (build it as `tests/test_adjust.py` does for `use_mask_cache`) replaces the inner segmenter; a cache miss raises `RuntimeError` naming the concept |

The pytest shim of `tests/run_all.py` has no fixtures besides `tmp_path`; restore every patched
value in `finally`.

## Review round 1 (2026-11-11)

The Checker (Opus, high) compared the code before and after on 840 op arrays, 216 `apply_image`
arrays and 7 gate cases per input and found no value that differs. It found one case where the
*batch* changes pixels, and robustness problems around threads and workers.

| # | Finding | Change |
|---|---|---|
| 1 | Blocking. With a model backend, `--reference` and several workers, the parent never segments the reference photo. In the worker the cache miss is swallowed and the region reference match is dropped: pixels differ by up to 0.04 | `presegment` also fills the cache for the reference |
| 2 | `par.set_threads` shut the shared pool down. The web server builds a pipeline while a job renders, and that job's next strip call then failed | `set_threads` returns early when nothing changes and replaces the pool without shutting it down |
| 3 | A worker that dies broke the pool: every queued photo failed | The photos that are not finished are rendered in the parent |
| 4 | Ctrl-C did not stop a run with several workers | The pool is shut down with its queue cancelled on any exception; at most `2 * workers` photos are in flight |
| 5 | `workers` and `threads` were written into every sidecar, so sidecars differed between worker counts | Both are left out of `audit.run_config` |
| 6 | `chroma_guard` computed the ceiling twice: slower on one thread and on previews | One measuring pass keeps the ceiling; the second pass reuses it |
| 7 | A render worker built the real backend, which imports torch in every worker | A worker builds with the stub backend and takes the run's configuration afterwards; OpenCV gets the worker's thread count |
| 8 | `AUTOEDIT_THREADS` had no upper bound | Capped at 256 |
| 9 | Five tests were weak | Strengthened, see below |

### `pipeline.py`

`presegment`, at its end:

```python
        if self.cfg.reference:
            # the reference's masks too, once per run: a render worker reads them from the cache
            self._reference_concept_features(concepts)
```

`_NOT_STORED` gains `"workers"` and `"threads"`.

### `edit/par.py`

```python
def threads() -> int:
    ...
    return min(n, 256) if n > 0 else (os.cpu_count() or 1)


def set_threads(n: int | None) -> None:
    """Fix the thread count of this process. None or 0 means automatic. A strip call that is
    running keeps the pool it holds; the pool is replaced, never shut down under it."""
    global _threads, _pool
    value = n if n and n > 0 else None
    with _lock:
        if value == _threads:
            return
        _threads = value
        _pool = None
```

### `edit/gates.py`, `chroma_guard`

Protected file. Same arithmetic per pixel as before F4; the ceiling is computed once.

```python
    def measure(src, out, prot=None):
        out_c = _chroma(out)
        ceiling = ceiling_of(src, out, prot)
        return int((out_c > ceiling).sum()), ceiling, out_c

    def scale_down(out, ceiling, out_c):
        over = out_c > ceiling
        l = luma(out)[..., None]
        scale = np.ones_like(out_c)
        np.divide(ceiling, out_c, out=scale, where=over)
        scale = np.clip(scale, 0.0, 1.0)[..., None]
        return np.clip(l + (out - l) * scale, 0.0, 1.0).astype(np.float32)

    parts = par.each(measure, *arrays)
    n_over = sum(p[0] for p in parts)
    if n_over == 0:
        return edited.astype(np.float32), 0.0
    ceiling = np.concatenate([p[1] for p in parts], axis=0)
    out_c = np.concatenate([p[2] for p in parts], axis=0)
    return (par.rows(scale_down, edited, ceiling, out_c),
            n_over / float(edited.shape[0] * edited.shape[1]))
```

`ceiling_of` stays as it is; `count_over` and the old `guard` go away. Keep the comments on the
lines they describe.

### `batch.py`

```python
def _render_with(pipe, path, image_out, sidecar, bias, gid, do_apply) -> dict:
    out_rgb, result = pipe.process_one(path, do_apply=do_apply, exposure_bias=bias, batch_group=gid)
    emit.write_outputs(out_rgb if do_apply else None, result, image_out, sidecar, pipe.cfg, path)
    return {...}      # the dict `_render` returns today


def _render(path, image_out, sidecar, bias, gid, do_apply) -> dict:
    return _render_with(_WORKER["pipe"], path, image_out, sidecar, bias, gid, do_apply)


def _worker_init(cfg: RunConfig, out_dir: str, threads: int) -> None:
    # Build with the stub backend: a render worker reads masks from the cache and must not load a
    # model, and building the real backend would import torch in every worker.
    pipe = Pipeline.build(cfg.model_copy(update={"backend": "stub"}))
    pipe.cfg = cfg
    par.set_threads(threads)
    try:
        import cv2
        cv2.setNumThreads(threads)
    except Exception:
        pass
    pipe.use_mask_cache(out_dir)
    if isinstance(pipe.segmenter, DiskMaskCache):
        pipe.segmenter.inner = _NoModel()
    _WORKER["pipe"] = pipe
```

In `run`, for several workers:

- the pool is created without `with`; the whole submit-and-collect section runs in
  `try: ... except BaseException: pool.shutdown(wait=False, cancel_futures=True); raise` and ends
  with `pool.shutdown(wait=True)` on the normal path;
- before a photo is submitted, the loop waits until fewer than `2 * n` photos are in flight
  (collecting finished ones while it waits);
- when collecting a result raises `concurrent.futures.process.BrokenProcessPool`: print
  `a render worker died; finishing <k> photos in this process`, stop submitting to the pool, and
  render every photo that has no result yet with `_render_with(pipeline, ...)` in the parent, one
  after the other, with the same per-photo failure handling. The pool is then shut down with
  `wait=False, cancel_futures=True`.

The one-worker loop does not change.

### Tests (`tests/test_parallel.py`)

| Test | Change |
|---|---|
| new `test_workers_keep_the_reference_match` | The Checker's simulation, in one process. A parent pipeline for a config with `backend="sam3"` and a `reference` photo, built with the stub (`Pipeline.build` of the stub config, then `pipe.cfg = cfg`), `use_mask_cache(out)`, the stub segmenter standing in for the model; `presegment(photo)`. A second pipeline built the same way with `_NoModel` as the cache's inner segmenter renders the photo: the pixels equal the parent pipeline's own render (`array_equal`), and the notes contain `reference match (region)`. Without the `presegment` fix this test fails |
| new `test_set_threads_while_rendering` | Two threads loop `apply_ops` on a 600 by 800 frame with `par.MIN_PIXELS = 0` for one second while a third calls `par.set_threads` with alternating values: no exception, and every result equals the single-thread result |
| new `test_broken_pool_falls_back_to_the_parent` | With `batch._render` replaced, in the parent, by a function that raises `BrokenProcessPool` is not possible across processes; instead test the helper: factor the fallback into a function that takes the unfinished jobs and the pipeline, and assert that it renders them all and records a failure for a broken file only |
| `test_workers_write_the_same_files` | Runs with `cfg.workers` set (not the argument) for the two-worker run, and asserts that the sidecars are equal with only the timing fields removed (so `run_config` no longer differs) |
| `test_chroma_guard_matches_the_previous_formula` | Also on a 700 by 900 frame with `par.MIN_PIXELS = 0` and 4 threads |
| `test_dehaze_strips_are_bit_identical` | Also against a copy of the pre-change tail (take `dehaze` from `git show HEAD:src/autoedit/edit/ops.py`) on a 700 by 900 frame |
| `test_guarded_global_pass_is_unchanged` | Adds a guard op whose setting is 0 and a guard op that is missing from the settings |
| `test_worker_never_loads_a_model` | Restores the thread count it changes; asserts that the worker pipeline's `cfg.backend` is the run's backend while its segmenter was built from the stub |
| `test_thread_setting` | `AUTOEDIT_THREADS=999999` gives 256 |

Proving commands: `PYTHONPATH=src python3 -m pytest tests/test_parallel.py tests/test_gates.py tests/test_guard.py tests/test_quality_gate.py tests/test_adjust.py tests/test_jobs.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py` (540 tests before this round).

## Acceptance checks (the session runs these)

1. Both test suites green.
2. Default renders byte-identical to the baseline (sets A, B and the smoke corpus), with one thread
   and with all threads.
3. Renders of three 22 MP photos with SAM 3 masks byte-identical before and after.
4. Timing table: the three photos before and after, apply stage and whole render.
5. Batch throughput on eight photos for workers 1, 2 and 4.
6. The tone report gate on the Dolomites corpus: 0 and 0.
7. Checker review: `ops.py` is a protected file.

## Result

Closed on 2026-11-11, in the unattended run.

- **Built** in three tasks by Builders (Sonnet): strips in threads, render workers, and the two
  per-pixel stretches that were left (the tail of `dehaze`, the oversaturation gate). One review
  by a Checker (Opus, high) compared before and after on 840 op arrays, 216 `apply_image` arrays
  and 7 gate cases per input and found no differing value; its nine findings and their fixes are
  in "Review round 1" above.
- **One 22 MP photo** (SAM 3 masks from the cache, development machine, 10 cores):

  | Photo | Before | After | Apply stage before | Apply stage after |
  |---|---|---|---|---|
  | JPEG, four regions | 21.2 s | 7.9 s | 20.1 s | 6.8 s |
  | RAW, three regions | 18.4 s | 7.3 s | 16.4 s | 5.2 s |
  | JPEG, four regions, dehaze in three passes | 23.7 s | 8.6 s | 22.6 s | 7.4 s |

  All three outputs are byte-identical before and after. The target of 10 s is met.
  Timed again on the complete tree, after F17: 7.9 s, 7.2 s and 8.5 s, and the three outputs are
  still byte-identical to the renders made before F4. The later features cost a default run
  nothing.
- **A batch of eight 22 MP photos** (stub backend): 135.6 s with one thread, 69.7 s with threads,
  43.5 s with two workers, 33.1 s with four, 31.6 s with eight workers of one thread each. All
  outputs byte-identical. The target of three times the throughput is met with two workers (3.1
  times) and reaches 4.1 times with four.
- **Tone gate** on the 110-photo RAW corpus: 0 dark to darker, 0 pushed into clipping.
- **Default output:** byte-identical to the renders made before the feature, with one thread and
  with all threads.

### What a GPU port would have to cover

After this feature a render spends its time in the parts that cannot run on strips, measured on
the slowest of the three photos: `dehaze` (three passes: the proxy resize, the dark channel and
the guided filter), the blurs of `clarity` and `sharpen`, building one feathered alpha per region
(a guided filter at 1600 px and two resizes), the reductions and clips around them, and decoding
and encoding the file. A GPU port would have to cover the whole chain to pay off, because moving
a 22 MP frame to the GPU and back costs about as much as one per-pixel op: the linear-light
conversion, the tone ops, the colour ops with their RGB to HSV round trip, the box and guided
filters, the dark-channel minimum, and the gates. It would not be bit-identical to the CPU path
(other float rounding), so it would need its own tolerance tests and its own blind check.

