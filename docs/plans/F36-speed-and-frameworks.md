# F36. Speed and frameworks

Status: spec (2026-11-16). Nothing is built; it waits for the owner's decisions at the end.
Size M. Not yet in [FEATURE_PLAN.md](../FEATURE_PLAN.md): another session had that file open
while this was written. The proposed row is the last section.

## Goal

Make what exists faster where a measurement says it pays, and say for each part whether another
framework would do it better. Every task leaves default output byte-identical. A framework change
that would move pixels is tested first and stays behind a setting.

## What was found

1. **Segmentation is the slow stage, by far.** One photo with the seven concepts of `natural`
   spent 80 to 92 s in SAM 3. All the pixel work on a 22 MP photo takes 7 to 9 s (F4).
2. **Most of that is repeated work.** `segment/sam3.py` runs the whole model once per concept, so
   the image encoder, the expensive part, runs seven times on the same picture. Run once and
   reused, the same photos took 23 s each, and all seven masks were identical bit for bit.
3. **No part needs another framework today.** One change is worth a test: SAM 3 on Apple's MLX.
   The local model already runs on MLX, through Ollama.
4. **Jobs slow each other more than any code does.** The tests take 88 s alone and 346 to 511 s
   next to other heavy jobs (SAM 3 probes, a Studio render).

## Measured before the design (2026-11-16)

Development machine (10 cores, 64 GB), `.venv` (torch 2.12.0, transformers 5.12.1), MPS, analysis
size 1536, two 22 MP JPEGs.

**The machine was not quiet while this was measured.** A second session's SAM 3 jobs and, later,
a Studio render ran during parts of it. At one point the load average was above 100 on 10 cores
and 19 of 20 GB of swap were in use. Which timings were affected was not recorded, so read every
absolute time below as an upper bound. The ratios hold: both paths ran minutes apart in one
process. The mask comparison does not depend on load. Task T0 measures again on a quiet machine.

### SAM 3 per photo, seven concepts

| | Photo 1 | Photo 2 |
|---|---|---|
| Today: the whole model once per concept | 80.2 s | 92.5 s |
| Image encoder once, then one light pass per concept | 22.7 s | 22.7 s |
| Gain | 3.5 times | 4.1 times |
| Masks and scores, all seven concepts | identical | identical |

### Inside one photo (second run)

| Part | Time |
|---|---|
| Image encoder | 7.4 to 8.1 s |
| One concept after that | 1.0 to 1.9 s |
| of which the text encoder | 0.04 s |
| of which moving to the CPU and post-processing | 0.01 to 0.07 s |

Cost today: `concepts × (encoder + pass)`. Cost with the encoder run once:
`encoder + concepts × pass`. The gain grows with the number of concepts: none for one concept,
3.5 times for seven, about 5 times for 27 (F21's reveal words on top of the seven).

### Tried and dropped

| Idea | Result |
|---|---|
| Half precision on MPS | Not faster (encoder 8.8 s), and the masks move (overlap 0.994 to 1.0) |
| All concepts in one batched pass | Slower: 25 s against 10.5 s for seven single passes. Masks identical |
| Keeping the text embeddings between photos | Nothing to gain: 0.04 s of a 1.5 s pass |

### Other measurements

- **A second measurement.** F21's probe, run the same day by another session, reports the same
  comparison on its own photos: the whole model once per word against one image pass for all
  words. Its numbers go into [F21](F21-reveal-check.md), "Step one".
- **Starting up.** 18 s when the files are in the system's file cache (imports 5 s, weights 1 s,
  move to the GPU 4 s, first pass 8 s). 95 s on the first use after a while (imports 28 s, weights
  and move 48 s, first pass 19 s). No warning about an operation falling back from the GPU to
  the CPU appeared.
- **Memory.** 3.6 GB allocated on the GPU. The Metal driver held 14.5 GB at the end of the probe,
  which ran both paths and a half-precision pass. A normal run is not measured yet (T0).
- **Tests.** 701 pass. Under pytest, alone: 88 s (79 s of CPU). Under `tests/run_all.py` next to
  the other jobs: 511 s and 346 s (100 s and 130 s of CPU). The suite is not slow; it waits.
- **Pixels.** From F4, last timed after F17: 7 to 9 s for one 22 MP photo; a batch of eight
  takes 69.7 s with one render worker (the default) and 33.1 s with four.
- **Local model.** Ollama 0.40.13 runs `qwen3.5:9b` on its MLX engine (its log names the MLX
  runner). A call takes 1.5 s (F20).
- **Declared, never imported.** `kornia` and `colour-science` (extra `accel`), `typer` and `rich`
  (extra `cli`): no import in `src`, `scripts` or `tests`. `kornia` requires torch, so
  `pip install '.[accel]'` installs torch for nothing.
- **Seen on the way.** Four render workers of a run on 2026-11-11 are still alive, idle, without
  a parent: a run that is killed hard leaves its workers behind. They cost nothing; not planned.

## Other frameworks

| Part | Runs on | Looked at | Verdict | Why |
|---|---|---|---|---|
| Pixel ops | numpy and OpenCV on the CPU, row strips in threads (F4) | The GPU, through torch or MLX | Keep | F4's analysis stands: a port must cover the whole chain, is not bit-identical and doubles the protected op math. After T1 segmentation still costs twice the pixel work, and it is the GPU's job |
| Segmentation | SAM 3 in Transformers on torch MPS | MLX ports, Core ML, Apple's Core AI, smaller models | Test MLX (T6). The rest later | A Core ML conversion of SAM 3.1 reports 2.05 s per image for the encoder, and 2.38 s on MLX, on a 16 GB laptop; here the encoder took 7.4 to 8.1 s, perhaps under load. Other float rounding: masks will not be identical |
| Local model | Ollama, `qwen3.5:9b` | mlx-lm in process, llama.cpp | Keep | It already runs on MLX. 1.5 s a call. The code needs no package, only HTTP on this computer |
| Cloud model | Anthropic SDK | none | Keep | Refreshed in F17, off by default |
| RAW and files | rawpy, Pillow, tifffile | none | Keep | Reading and writing are about 1 s of a render |
| Studio and API | FastAPI, uvicorn, one HTML file of 1,500 lines | A component framework; a desktop shell | Not now | The plan puts the Studio rebuild after the engine. F13 and F15 give it a settings schema and a versioned API, so the choice stays open. It belongs in the rebuild's spec |
| Tests | Own runner, also collected by pytest | A parallel runner | Keep | 88 s alone |
| Packaging | setuptools with extras | none | Clean up (T3) | Four packages nothing imports |

For the rebuild's spec, when it comes:

- Apple showed SAM 3 converted for its new Core AI framework: 623 MB instead of 3.4 GB, with a
  text prompt, for macOS 27 and Swift. That matters for a Mac app's download size.
- A shell such as Tauri around the existing local server is the route most reports describe.
- Smaller models (EfficientSAM3, EOVSAM) exist as research code; their licences and quality here
  are unknown. They belong to the open decision from F14 about a second segmenter.

Sources, read on 2026-11-16; none of their numbers was tested here:
[mlx_sam3](https://github.com/Deekshith-Dade/mlx_sam3),
[MLX weights](https://huggingface.co/mlx-community/sam3-image),
[Core ML conversion](https://huggingface.co/sdkv2/sam3.1-coreml),
[Apple, WWDC26 session 326](https://developer.apple.com/videos/play/wwdc2026/326/),
[Ollama on MLX](https://ollama.com/blog/mlx),
[EfficientSAM3](https://arxiv.org/abs/2511.15833), EOVSAM (arXiv 2608.02284),
[Tauri with a sidecar](https://evilmartians.com/chronicles/making-desktop-apps-with-revved-up-potential-rust-tauri-sidecar).

## Design

### T0. A clean baseline (the session)

Before any code changes, because it is the "before" of T1.

1. Wait for a quiet machine: load average under 2, no other AutoEdit job, Studio idle or
   stopped. Write the load average next to every time that is recorded.
2. Copy the 16 photos of `out-loop/panel.txt` into `out-f36/panel/` (copies; nothing is moved).
3. `.venv/bin/autoedit run out-f36/panel out-f36/before --backend sam3 --dry-run`. The masks land
   in `out-f36/before/.autoedit-masks/`; the time per photo is `stage_timings_ms["segment"]` in
   each sidecar.
4. Memory of one render worker: `/usr/bin/time -l` on a stub run of one 22 MP photo, the line
   "maximum resident set size". T5 needs it.
5. Driver memory after the 16 photos (`torch.mps.driver_allocated_memory()`), once.

### T1. Encode the image once per photo

`segment/sam3.py` only. `Sam3Model.forward` takes `vision_embeds` in place of `pixel_values`, and
with `pixel_values` its first step is the same encoder call, so the masks cannot differ.

```python
    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        if not concepts:
            return []
        self._ensure_model()
        h, w = img.height, img.width
        out: list[RegionMask] = []
        vision, sizes = self._encode(img.rgb)   # the image encoder: once per photo
        for concept in concepts:
            instances = self._run_concept(vision, sizes, concept)
            ...                                 # union, best score, RegionMask: as today

    def _encode(self, image_rgb):
        """The image encoder, once per photo. Every concept reuses its output: the full forward
        is this call followed by the per-concept part, so the masks are the same."""
        import torch
        from PIL import Image as PILImage

        # The processor expects uint8 PIL (0-255); our proxy rgb is float [0,1].
        pil = PILImage.fromarray((np.clip(image_rgb, 0.0, 1.0) * 255 + 0.5).astype(np.uint8), "RGB")
        inputs = self._processor(images=pil, return_tensors="pt").to(self.device)
        with torch.no_grad():
            vision = self._model.get_vision_features(pixel_values=inputs["pixel_values"])
        sizes = inputs.get("original_sizes")
        sizes = sizes.to("cpu").tolist() if hasattr(sizes, "to") else sizes
        return vision, sizes

    def _run_concept(self, vision, sizes, concept: str):
        import torch

        text = self._processor(text=concept, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._model(vision_embeds=vision, input_ids=text["input_ids"],
                                  attention_mask=text.get("attention_mask"))
        ...                                     # CPU move, post-processing, instances: as today,
                                                # with target_sizes=sizes
```

Keep the comments on the lines they describe. Update the module docstring: one encoder pass per
photo, one light pass per concept.

### T2. Keep the last photo's image features

In Studio, "Edit this version" with a thing that has no mask yet runs the encoder again on a photo
it has just encoded. Keeping the features of one photo in memory turns that from encoder plus
pass into one pass.

- `segment/cache.py`: the three digest lines of `DiskMaskCache._path` move into
  `pixel_digest(rgb) -> str`, unchanged, so existing mask caches keep their file names.
- `Sam3Segmenter.__init__`: `self._kept = None`. In `segment`:

```python
        key = pixel_digest(img.rgb)
        if self._kept is not None and self._kept[0] == key:
            vision, sizes = self._kept[1], self._kept[2]
        else:
            self._kept = None                   # let go of the last photo before encoding the next
            vision, sizes = self._encode(img.rgb)
            self._kept = (key, vision, sizes)
```

One photo only. In a batch the last photo's features are dropped before the next is encoded, so
the peak does not double. The size of the kept features is measured and written into the Result.

### T3. Remove packages nothing imports

- `pyproject.toml`: `accel` becomes `["opencv-python>=4.9", "tifffile>=2024.1", "rawpy>=0.19"]`;
  the `cli` extra and its comment line go.
- The word "Kornia" and "colour-science" leave `README.md` (the install line), `CLAUDE.md`,
  `AGENTS.md` and `ARCHITECTURE.md` (the optional-extras sentence in each).
- Proof beyond the search: `.venv/bin/pip uninstall -y kornia kornia-rs colour-science`, then
  the full tests with `.venv/bin/python`, `autoedit doctor`, and one photo with `--backend sam3`.

### T4. One SAM 3 job at a time, enforced (only after a yes)

Two SAM 3 jobs on one GPU starve each other; the working rule "one at a time" cannot be kept by
sessions that do not see each other. A lock in `segment/sam3.py`, held for one photo's
segmentation:

```python
@contextlib.contextmanager
def _one_job():
    """One SAM 3 photo at a time on this machine: a second job waits its turn. The system frees
    the lock when a process dies, so it cannot go stale."""
    try:
        import fcntl
    except ImportError:      # ponytail: no lock on Windows; msvcrt.locking if it is ever needed
        yield
        return
    with open(os.path.join("/tmp", f"autoedit-sam3-{os.getuid()}.lock"), "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("waiting for another SAM 3 job on this computer", file=sys.stderr)
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
```

`segment` wraps its encoder and concept passes in `with _one_job():`. Cost: a Studio preview
waits while a batch segments one photo.

### T5. Render workers by default (only after a yes)

`[performance] workers = 0` in `defaults.toml`: half the cores, at most four. Output is
byte-identical (F4's test). With SAM 3, the parent then segments the next photo while a worker
renders the last one. The evidence is F4's table and T0's memory per worker; if four workers
need more than half of a 16 GB machine, the recommendation is to leave the default at 1.

### T6. SAM 3 on MLX: a probe (only after a yes)

A throwaway measurement, nothing in `src/`. A scratch venv with an MLX port (`mlx-vlm`, or
`mlx_sam3`), the MLX weights (3.5 GB) in a scratch folder, the 16 panel photos, the seven
concepts. Recorded: encoder time, time per concept, start-up, memory, and for every concept the
overlap with the masks from T1.

A second backend gets its own spec only if a whole photo is at least twice as fast as after T1,
every present concept overlaps at 0.98 or more, and no concept changes between present and
absent. Otherwise the numbers go into the Result and the work stops. A second backend would be
opt-in (`--backend`); as a default it would change pixels and need the blind sheet and the
owner's vote.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| The unused packages | Removed from the extras (T3) | Restore the two lines in `pyproject.toml` |
| How many photos keep their features | One, in memory, never on disk (estimated at about 100 MB; T2 measures it) | `_kept` in `segment/sam3.py` |
| Precision, batching, input size of SAM 3 | Unchanged: measured, no gain | Nothing to reverse |

## Files

| File | Change |
|---|---|
| `src/autoedit/segment/sam3.py` | T1, T2, and T4 after a yes |
| `src/autoedit/segment/cache.py` | `pixel_digest` (T2) |
| `tests/test_sam3_glue.py`, `tests/test_segment_cache.py` | tests below |
| `pyproject.toml`, `README.md`, `CLAUDE.md`, `AGENTS.md`, `ARCHITECTURE.md` | T3 |
| `src/autoedit/configs/defaults.toml` | T5 after a yes |

No protected file is touched.

## Tasks

One after the other: T1, T2 and T4 share `sam3.py`. F21 also changes `segment/sam3.py` and
`segment/cache.py` (its `open_vocabulary` flag), so the two features are built one after the
other, never at the same time.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T0 | The session | Baseline on a quiet machine | The numbers, in the Result |
| T1 | Builder (Sonnet, medium) | Encode once; tests 1 to 3 | `PYTHONPATH=src python3 -m pytest tests/test_sam3_glue.py tests/test_segment_cache.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T2 | Builder (Sonnet, medium) | Kept features, `pixel_digest`; tests 4 to 6 | The same |
| T3 | Tweaker (Haiku, low) | The exact old and new strings from the session's brief | `PYTHONPATH=src python3 tests/run_all.py` |
| T4 | Builder (Sonnet, medium) | The lock; tests 7 and 8 | As T1 |
| T5 | The session | One line in `defaults.toml`, the docs line | `PYTHONPATH=src python3 tests/run_all.py` |
| T6 | The session | The probe | The numbers, in the Result |
| Check | Checker (Opus, high) | The diff of T1, T2 and T4 against this spec; the definition of done | Findings as `file:line` |

## Tests

The fakes in `tests/test_sam3_glue.py` change with T1: `_Proc.__call__` returns pixel inputs for
`images=` and token inputs for `text=`; `_Model` gets `get_vision_features` and counts its calls;
`_seg()` sets `_kept = None`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_the_image_is_encoded_once_for_all_concepts` | Three concepts: one encoder call, three passes, each with `vision_embeds` and without `pixel_values` |
| 2 | `test_no_concepts_does_not_touch_the_model` | `segment(img, [])` is `[]` and `_ensure_model` is never called |
| 3 | The two existing tests | Pass unchanged in what they assert |
| 4 | `test_a_second_call_on_the_same_photo_reuses_the_features` | Two calls, same pixels, different concepts: one encoder call |
| 5 | `test_another_photo_encodes_again_and_drops_the_old_features` | Other pixels: a second encoder call; `_kept` holds the new key only |
| 6 | `test_pixel_digest_keeps_the_mask_cache_names` | `DiskMaskCache._path` equals the pre-change lines written out in the test |
| 7 | `test_the_lock_lets_one_holder_in_at_a_time` | Two threads inside `_one_job()`: their intervals do not overlap |
| 8 | `test_the_lock_is_released_after_an_error` | After an exception inside, the next holder gets in at once |

## Acceptance checks (the session runs these)

1. Both test suites green.
2. **Masks identical.** The same run as T0 into `out-f36/after`, then:

   ```sh
   .venv/bin/python - <<'EOF'
   import glob, os, numpy as np
   a, b = "out-f36/before/.autoedit-masks", "out-f36/after/.autoedit-masks"
   names = sorted(os.path.basename(p) for p in glob.glob(a + "/*.npz"))
   assert names and names == sorted(os.path.basename(p) for p in glob.glob(b + "/*.npz"))
   for n in names:
       x, y = np.load(os.path.join(a, n)), np.load(os.path.join(b, n))
       assert sorted(x.files) == sorted(y.files), n
       assert all(np.array_equal(x[k], y[k]) for k in x.files), n
   print(len(names), "photos: every mask and score identical")
   EOF
   ```

3. A table of the segmentation time per photo, before and after, with the load average.
4. Studio: a first preview, then "Edit this version" naming a thing with no mask yet. The segment
   stage of the second render takes less than a third of the first.
5. Default output byte-identical: the stub smoke run, and three photos rendered with SAM 3.
6. Driver memory after 16 photos. If it is more than twice the allocated memory, try
   `torch.mps.empty_cache()` at the end of `segment`; keep it only if the time per photo rises by
   less than 5 %.
7. The Checker's review.

## Decisions for the owner

| Decision | Evidence | The change, if yes |
|---|---|---|
| Build T0 to T3 before F21? | F21 adds about 20 words per photo: 20 whole-model runs today, 20 light passes after T1. Its tiles multiply both. F21's probe reports the same comparison, so the change should be built once, from T1 here | F36 goes ahead of F21 in the order, and F21 refers to T1 |
| One SAM 3 job at a time, enforced (T4)? | Several jobs at once starved each other for more than 45 minutes (noted on 2026-11-10); today's measurements ran next to a second session's job without either knowing | T4 is built. A Studio preview then waits while a batch segments one photo |
| Render workers on by default (T5)? | F4: 69.7 s against 33.1 s for eight photos. T0's memory per worker | `workers = 0` under `[performance]` |
| May the session download the MLX weights (3.5 GB) and install an MLX port into a scratch venv (T6)? | "Other frameworks" above. The numbers there are other people's | T6 runs; nothing in `src/` changes |

## Proposed plan entry

For the table in `FEATURE_PLAN.md`:

`| F36 | Speed and frameworks: SAM 3 encodes each photo once | M | spec; waits for the owner's decisions |`
