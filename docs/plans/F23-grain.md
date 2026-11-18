# F23. Grain

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F23.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and
the decisions that are the owner's are in the table below.

The design was built once on a scratch copy of the tree, which is deleted again. The reference
code below is that code and every number is measured. On the copy both suites passed, on the
system Python and on `.venv`: the 757 tests of today and the 22 of this spec.

## Goal

Film grain that a run can switch on, a look can carry, and a sentence can ask for. It is the last
thing done to a picture: never sharpened, never denoised, and as fine on a web export as on a
full-size one. It ships off. No packaged look has grain and the run setting is empty, so default
output stays byte-identical.

## What was found in the code

1. `edit/ops.py::apply_ops` is not only the final render. `edit/apply.py::apply_image` calls it
   for every region, every layer and the global pass; `edit/reference.py::tune_settings_to_reference`
   calls it on the analysis copy up to 16 times per photo; `workflows.py` and the post-reference
   lifts in `pipeline.py::process_one` call it too. A step rendered there is rendered in all.
2. The global pass is not the end of the picture. After it `apply_image` runs the palette grade,
   `gates.highlight_hold` and `gates.chroma_guard`. Then `process_one` runs `banding.assess` (and
   may render again with damped settings), the reference match with a second hold and gate, and
   `stats.compute` with `gates.assess` for the review flag.
3. The export resize comes later still, outside the pipeline: `emit.py::write_image` calls
   `imageio.resize_long_edge` (Lanczos), then `save_image`, whose `_to_u8` dithers by one level.
   Every product export goes through `emit.write_outputs`: `batch.py` (two places),
   `adjust.py::rerender`, `web/service.py::run_one`, `web/jobs.py`.
4. The Studio preview does not go through `emit`. `web/service.py::preview` and `make_variants`
   encode with `_img_b64`, which has its own resize (to 2560 and to 1100 px).
5. A name is an op when it is in `ops.OP_NAMES`: `instructions.py::load_vocab` refuses a verb with
   another name, `adjust.py::parse_set` refuses it for `--set`, and
   `tests/test_parallel.py::test_steps_list_every_op_once` wants every name in `ops.STEPS`.
   `decide/llm_decider.py` offers the model every key of `ops.OP_BOUNDS`.
6. A look is shaped into the settings inside the decider (`looks.py::shape`). A look table may
   hold the fields of the `Look` dataclass (`looks.FIELDS`): a profile look and
   `autoedit looks save --set` accept a new field without more code.
7. `instructions.py::from_translation` takes from a model only ops with an entry under `[limits]`
   in `instructions.toml`, and `translate_local.py::_system` lists only verbs with such an op.
8. `process_one` stores the run settings in the sidecar (`audit.run_config`) and
   `adjust.py::rerender` builds its config from them: a new run setting is replayed on a re-render.
9. The pixel audit `quality.py::assess_pixels` (used by `quality_runner.py::render_safe` and by
   `scripts/tone_report.py`) reads every n-th pixel, not an average. Fine noise reaches it in full.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is anything grainy by default? | No. The run setting is empty and no packaged look has grain | `grain = N` in a look of `looks.toml`, or under `[run]` in `defaults.toml`, after the owner has seen the crops |
| Where is the grain added? | At the export, after the resize, in `emit.write_image`. The preview adds it after its own resize. `grain` is an op name and the last step of `ops.STEPS`, but `apply_ops` does not render it | Not meant to be reversed: Design 1 and 4 |
| How strong is 100? | A luma grain of 0.05 at mid grey (12.75 of 255). 25, the step of "add grain", is 3.2 levels | `_GRAIN_SIGMA` in `edit/ops.py` |
| How large is size 1? | One clump is 1/2000 of the long edge: 3 px on a 6000 px export, 1 px on a 2048 px one | `_GRAIN_CELL` in `edit/ops.py` |
| What seeds the pattern? | A digest of the photo's file name, without its folder. Not its pixels: the pattern would then change with every change of the edit and jump at each preview | `emit.grain_args` |
| Run setting against look | The run setting wins, also `--grain 0`. Left out, the look's grain is used, scaled by `--intensity` like every look value. Instructions add to either | The line that computes `grain` in `pipeline.py` |
| May a model add grain? | No. What a decider proposes for it is dropped, and the models that read free text are not offered the op (no `[limits]` entry) | Remove the `pop` in `pipeline.py`; add `grain = 60` under `[limits]` and measure again with `scripts/eval_translator.py` |
| Grain on a part of the photo ("add grain to the sky") | Not built. The clause is not understood, with the reason "grain is for the whole photo" | A later feature |
| The safety workflow (`quality_runner.py`) | Adds no grain and says so in the notes: it audits the pixels it writes | The two lines in `render_safe` |
| A control in Studio | None. The API takes `grain` and `grain_size`, and "add grain" works in the text boxes | The Studio rebuild |

## Design

### 1. Where the grain is added

Rendered in `apply_ops`, grain would be drawn on every proxy of the reference tuner (fact 1),
judged by every gate (fact 2), and then shrunk by the export resize (fact 3), which blurs it away
on a web export. So grain is a value in the settings and one pixel step at the export:

- `grain` is in `OP_NAMES`, `OP_BOUNDS` (0 to 100) and `SPATIAL_OPS`, and it is the last step of
  `STEPS`. A look, an instruction, `--set` and the sidecar treat it like any op. `apply_ops` has
  no branch for it: `apply_ops(x, {"grain": 80})` returns `x`.
- `pipeline.py::process_one` puts the amount into `global_settings["grain"]` and changes nothing
  else. The pixels it returns, the gates and the review flag are the same with and without grain.
- `emit.write_image` adds the grain after `resize_long_edge`, directly before the encoder. That
  is the stage `emit`, which already resizes and dithers: the order `decode → … → apply → emit`
  does not change.
- The preview and the variants add it in `_img_b64`, after their resize. A preview shows the
  grain of an export of its own size (2560 px). For a photo that needs no resize it is the
  export's pattern, pixel for pixel (measured).

Without grain, `write_image` and `_img_b64` take no new branch.

### 2. The op (`edit/ops.py`)

Protected file. Add below `levels`. `_resize_float` gains a third parameter,
`resample=PILImage.BILINEAR`, which it passes to `resize`. `_CLIP_LO, _CLIP_HI = 0.0205, 0.9795`
exist already: the levels the statistics count as clipped (0.02 and 0.98), with a margin (F5).

```python
# Grain (F23). Constants of the op, like the levels constants above.
_GRAIN_SIGMA = 0.05          # luma grain at amount 100 at mid grey: 12.75 of 255
_GRAIN_CELL = 1.0 / 2000.0   # size 1: one grain clump is this share of the frame's long edge
_GRAIN_CUT = 3.0             # the pattern is cut at this many standard deviations


def grain(rgb: np.ndarray, amt: float, size: float = 1.0, seed: int = 0) -> np.ndarray:
    """Film grain (F23): a seeded luma pattern added to the frame. `amt` 0..100.

    One value per pixel is added to R, G and B alike, so no colour is added. The pattern is white
    noise drawn on a grid of grain cells and enlarged to the frame, so a clump is `size`
    two-thousandths of the long edge wide on any frame: 3 px on a 6000 px export, 1 px on a
    2048 px one. A cell finer than a pixel cannot be drawn; the grain is then weakened in
    proportion, which is what shrinking a full-size picture does to its grain. The weight is 1 at
    mid grey and 0 at the levels the statistics count as clipped, and the pattern is cut at 3
    sigma, so the luma of a pixel never crosses one of those levels. The same frame size, size
    and seed give the same pattern (`RandomState` is a frozen stream). 0 is the identity.

    Not called by `apply_ops`: the pattern belongs to one frame size, so it is added to the
    picture that is written or shown, after its resize (emit.py, web/service.py)."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    h, w = rgb.shape[:2]
    cell = float(size) * max(h, w) * _GRAIN_CELL            # one clump, in pixels of this frame
    gh, gw = (max(1, round(n / max(cell, 1.0))) for n in (h, w))
    n = np.random.RandomState(int(seed) % 2**32).standard_normal((gh, gw)).astype(np.float32)
    if (gh, gw) != (h, w):
        n = _resize_float(n, (w, h), PILImage.BICUBIC)
    n = n - n.mean()
    n = np.clip(n / max(float(n.std()), _EPS), -_GRAIN_CUT, _GRAIN_CUT)
    t = np.clip((luma(rgb) - _CLIP_LO) / (_CLIP_HI - _CLIP_LO), 0.0, 1.0)
    delta = (a * _GRAIN_SIGMA * min(1.0, cell)) * (4.0 * t * (1.0 - t)) * n
    return np.clip(rgb + delta[..., None], 0.0, 1.0).astype(np.float32)
```

Registrations: `"grain"` last in `OP_NAMES`; `"grain": (0.0, 100.0)` in `OP_BOUNDS`; `"grain"` in
`SPATIAL_OPS`; a last step `("grain",)` in `STEPS`. Above `STEPS`, two comment lines: the last
step is a value in the bundle that `apply_ops` does not render; `emit.py` adds it to the picture
it writes, after the resize. The docstring of `apply_ops` gets the same in one sentence; its
code does not change.

Why the luma cannot cross a level: the largest step is `0.15 * 4t(1 - t)`, the distance to the
nearer level is `0.959 * min(t, 1 - t)`, and `0.6 * (1 - t) < 0.959`.

Measured: 3.18, 6.35 and 12.70 levels at mid grey for 25, 50 and 100. The mean of a frame moves
by 0.06 levels. 0.4 s on a 24 megapixel frame, 0.7 s on 45. A web export against the full-size
export seen at web size: 1.04 times its grain at half the size, 0.95 at a quarter.

### 3. From the settings to the export

`contract.py`, after `denoise`:

```python
    grain: float | None = Field(default=None, ge=0.0, le=100.0)  # film grain on the written picture (F23); None = the look's own, 0 = none
    grain_size: float = Field(default=1.0, ge=0.5, le=4.0)  # one grain clump in two-thousandths of the export's long edge
```

`looks.py`: a last field `grain: float = 0.0` on `Look`, read in `load` with
`grain=float(body.get("grain", 0.0))`. `shape` does not change.

`pipeline.py::process_one`, block A, directly after the block that drops `denoise`:

```python
        # Grain (F23) is asked for by the run setting, the look or an instruction, never by a
        # decider. Here it is a value in the global settings and no pixel: emit adds it to the
        # picture it writes, after the checks below and after the export resize.
        for s in (global_settings, *region_settings.values()):
            s.pop("grain", None)
        grain = cfg.grain if cfg.grain is not None else self.looks.get(look, looks_mod.Look()).grain * cfg.intensity
        if grain > 0.0:
            global_settings["grain"] = grain
```

Block B, directly after `policy_notes += [f"instruction {p}" for p in self.adjust_problems]`:

```python
        # The grain that is added: the base above plus what instructions asked for, inside 0..100.
        grain = min(max(global_settings.pop("grain", 0.0), 0.0), ops.OP_BOUNDS["grain"][1])
        if grain > 0.0:
            global_settings["grain"] = grain
```

`emit.py` (new imports: `hashlib`, and `from .edit import ops`):

```python
def grain_args(result: Result, cfg) -> tuple[float, float, int] | None:
    """(amount, size, seed) for `ops.grain` on a picture of this result, or None for no grain.
    The seed is a digest of the photo's file name: the same photo gets the same pattern in every
    run and in every folder."""
    amount = float(result.global_settings.get("grain", 0.0))
    if amount <= 0.0:
        return None
    seed = int.from_bytes(hashlib.blake2b(result.image.encode("utf-8"), digest_size=4).digest(), "big")
    return amount, float(cfg.grain_size), seed
```

- `write_image` gains a last parameter `grain: tuple[float, float, int] | None = None`. Directly
  after the `long_edge` resize: `if grain: rgb = ops.grain(rgb, *grain)`.
- `write_outputs` computes `grain = grain_args(result, cfg)`, passes `grain=grain`, and when it is
  set appends the note
  `f"grain: {grain[0]:.3g} of 100, size {grain[1]:g}, added to the export after the checks and the resize"`.

`web/service.py`: `"grain"` and `"grain_size"` in `_ALLOWED`. `_img_b64` gains the same last
parameter and the same line, between its resize and `_to_u8` (import `ops` inside the function,
next to `_to_u8`). `preview` and `make_variants` pass `grain=emit.grain_args(result, cfg)` for
the "after" picture. The "before" picture never gets grain.

`quality_runner.py::render_safe`, after the `try` block that calls `engine.process`:

```python
    if result.global_settings.pop("grain", None):   # F23: this workflow checks the pixels it writes
        result.notes.append("grain: not added by the safety workflow, which checks the pixels it writes")
```

`scripts/contact_sheet.py::_render` passes `grain=emit.grain_args(res, pipe.cfg)` to
`emit.write_image`, so a panel sheet shows the product's export.

### 4. The checks and the grain

Every check runs on the picture before the grain. Measured on six photos of the Dolomites corpus
(5760 px, stub backend), with the grain laid over the finished edit and the check run on that:

| Check, if it saw the grain | Grain 25 | Grain 50 | Grain 100 |
|---|---|---|---|
| Banding gate, `banding.assess` | 0.000, pass | 0.000, pass | 0.000, pass |
| Review gate, `gates.assess`: score and flag | unchanged | unchanged | unchanged |
| Share of pixels with a channel beyond 0.02 or 0.98, largest rise (budget 0.005) | 0.0002 | 0.0011 | 0.0039 |
| Pixel audit, `quality.assess_pixels`, against the same edit without grain | passes, 6 of 6 | fails on 2: new channel clipping | fails on 6: `severe_region_boundary_artifact`, 0.41 to 0.48 against the limit 0.25 |

- The banding gate would be blinded. A posterized sky that scores 0.31 ("reduce", so the gate
  damps the edit and renders again) scores 0.00 under grain 10.
- The pixel audit reads strong grain as a boundary artifact (fact 9). Its thresholds in
  `quality.toml` are never loosened, so it must not see the grain, and the safety workflow adds none.
- Sharpen and denoise are steps of `apply_ops`, long before.
- Highlight hold: before. The weight is 0 from luma 0.9795 up, so held whites stay white.
- Oversaturation gate: before. Grain adds one value to R, G and B, so `max - min` of a pixel
  cannot grow, and the gate's limit still holds on the export.
- Review gate: before. The luma cannot cross 0.02 or 0.98, so the luma clip shares of the export
  are those of the checked picture.
- The dither of `_to_u8` still runs after the grain.

### 5. Switching it on

- **Run setting:** `--grain N`, `--grain-size S`, `[run] grain` and `grain_size` in a run file,
  `grain` and `grain_size` in an API request. `--grain` is not scaled by `--intensity`.
- **Look:** `grain = N` in a look table. It gives `N * intensity`.
- **Words** (`instructions.toml`, before `[places.top]`; no entry under `[limits]`):

```toml
# Grain (F23) is for the whole photo. Aimed at a part of it, the clause is not understood.
[verbs.grain]
words = ["grain", "grainy", "grainier", "add grain", "more grain", "some grain", "film grain", "a grainy look"]
grain = 25
```

`instructions.py`: two constants next to `SIZE_ON_OBJECT`, and four changes in `parse`:

```python
WHOLE_PHOTO_OPS = frozenset({"grain"})   # finishing ops (F23): one value for the whole photo, never for a part
WHOLE_PHOTO = "grain is for the whole photo"
```

1. Before `if mark:` in the loop over the parts: `whole = bool(WHOLE_PHOTO_OPS & set(vocab.verbs[verbs[0]]))`.
2. First lines inside `if mark:`: `if whole: why = WHOLE_PHOTO; break` (a mark is a part).
3. The branch that borrows a target becomes `elif not concept and not whole:`.
4. Directly after the block `if target is None:`: `if whole and target != GLOBAL: why = WHOLE_PHOTO; break`.

`adjust.py::parse_set`, before the `return`:

```python
    if op in instructions.WHOLE_PHOTO_OPS and target != instructions.GLOBAL:
        raise ValueError(f"--set {text!r}: {instructions.WHOLE_PHOTO}, use global.{op}")
```

Required parses (golden test, known concepts `{"sky"}`):

| Text | Result |
|---|---|
| `add grain`, `more grain`, `make it grainy`, `add some grain`, `a grainy look` | `global`, grain +25 |
| `a little grain` | `global`, grain +12.5 |
| `much more grain` | `global`, grain +40 |
| `add grain and brighten the sky` | `global` grain +25, then `sky` exposure +0.30 |
| `brighten the sky and add grain` | `sky` exposure +0.30, then `global` grain +25 |
| `add grain to the sky`, `add grain to the top`, `make the jacket grainy` | not understood (grain is for the whole photo) |
| `add grain`, with a mark | not understood (grain is for the whole photo) |
| `no grain` | not understood (negation or exclusion), as today |

`lessons.toml`, before `[mask.thing]`: `[op.grain]` with `name = "Grain"`,
`does = "Lays a fine, even texture over the whole photo, like the grain of film."`,
`when = "A photo that looks too clean, or a set that should feel like film."`,
`watch = "It is for the whole photo. Heavy grain hides fine detail and makes the file larger."`

## Config keys

The run settings `grain` (0 to 100, empty by default) and `grain_size` (0.5 to 4, default 1.0),
the look field `grain` (default 0) and the verb `[verbs.grain]` are in Design 3 and 5. With them:

- `defaults.toml`, under `[run]`: a commented line `# grain = 25` (left out = the look's own, 0 =
  none) and `grain_size = 1.0`.
- `cli.py`: both keys in the `[run]` list of `_flatten` and in `overrides`; `--grain` and
  `--grain-size` (`dest="grain_size"`), both `type=float`.
- A `grain` rule in `rules.toml` would be dropped with the decider's: grain is not decided from
  statistics.
- `settings.toml`, at the end of the Look group:

```toml
[setting.grain]
label = "Grain"
group = "look"
help = "Film grain on the finished photo, 0 to 100. Empty uses the look's own; 25 is light, 100 is heavy."

[setting.grain_size]
label = "Grain size"
group = "look"
help = "How coarse the grain is. 1 is fine, 2 is twice as coarse. It follows the size of the export."
```

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/edit/ops.py` | `grain`, three constants, `_resize_float(resample=)`, the four registrations, two comments | T1 |
| `tests/test_denoise.py`, `tests/test_parallel.py` | the two changed asserts (see Tests) | T1 |
| `tests/test_grain.py` | new: tests 1 to 11 (T1), 12 to 20 (T2), 21 and 22 (T3) | T1 to T3 |
| `src/autoedit/contract.py`, `src/autoedit/looks.py` | the two run settings; the look field | T2 |
| `src/autoedit/pipeline.py` | blocks A and B | T2 |
| `src/autoedit/emit.py` | `grain_args`, `grain=` on `write_image`, the note in `write_outputs` | T2 |
| `src/autoedit/web/service.py` | `_ALLOWED`, `_img_b64`, `preview`, `make_variants` | T2 |
| `src/autoedit/quality_runner.py`, `scripts/contact_sheet.py` | two lines; one argument | T2 |
| `src/autoedit/cli.py`, `src/autoedit/configs/defaults.toml`, `src/autoedit/configs/settings.toml` | flags, keys, words | T2 |
| `tests/snapshots/settings_schema.json` | regenerated | T2 |
| `src/autoedit/instructions.py`, `src/autoedit/adjust.py` | the whole-photo rule | T3 |
| `src/autoedit/configs/instructions.toml`, `src/autoedit/configs/lessons.toml` | the verb; the lesson | T3 |

## Tasks

One after the other: each task needs the one before it. `.venv` has no pytest; there the proving
command is `tests/run_all.py`.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high (protected file) | Design 2; tests 1 to 11; the two changed asserts | `PYTHONPATH=src python3 -m pytest tests/test_grain.py tests/test_denoise.py tests/test_parallel.py tests/test_ops.py tests/test_ops_phase2.py tests/test_levels.py tests/test_llm_refresh.py -q` |
| T2 | Builder, Sonnet, high (`pipeline.py`) | Design 3; "Config keys" without the verb; tests 12 to 20; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_grain.py tests/test_pipeline.py tests/test_emit.py tests/test_export_presets.py tests/test_export_metadata.py tests/test_web_service.py tests/test_settings_schema.py tests/test_cli.py tests/test_adjust.py tests/test_quality_runner.py tests/test_contact_sheet.py tests/test_profile.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | Design 5: the verb, the whole-photo rule, the lesson; tests 21 and 22 | `PYTHONPATH=src python3 -m pytest tests/test_grain.py tests/test_instructions.py tests/test_instruction_words.py tests/test_follow_up.py tests/test_marks.py tests/test_adjust.py tests/test_lessons.py tests/test_local_translator.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec (`ops.py` and `pipeline.py` are protected), the definition of done, the crop sheet | none |
| Docs | The session, inline | `ARCHITECTURE.md` section 8 and `APP_GUIDE.md`: `→ grain` at the end of the op order, with "added at the export, after the resize and after every check; `apply_ops` does not render it"; one example with `--grain 25` in `README.md`; `CHANGELOG.md`; the plan row | none |

## Tests

`tests/test_grain.py`. Math tests use frames of 400 by 600 with `size=4.0`: a cell of 1.2 px, so
the grain has its full strength. "std" is the standard deviation of the luma of `out - x`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_zero_is_the_identity` | `ops.grain(x, 0) is x`, also with a size and a seed; `ops.apply_ops(x, {"grain": 80.0}) is x` |
| 2 | `test_luma_only_and_mean_kept` | A frame with every channel in 0.2..0.8, amount 100: `out - x` is the same in R, G and B within 1e-6; each channel mean moves by less than 0.5 of 255; float32, same shape |
| 3 | `test_same_input_same_output` | The same call twice is `array_equal`; another seed differs; another size differs |
| 4 | `test_more_amount_more_variance` | Flat 0.5: std at 25 < at 50 < at 100; at 100 within 3 % of `_GRAIN_SIGMA`, at 50 within 3 % of half of it |
| 5 | `test_strongest_in_the_middle_tones` | Flat frames: std at 0.5 > at 0.2 > at 0.9 > 0; std at 0.2 is 0.609 of std at 0.5 (± 0.02); at 0.0, 0.015, 0.985 and 1.0 the frame comes back `array_equal` |
| 6 | `test_luma_never_crosses_a_clip_level` | A grey ramp 0..1, amount 100: lumas inside 0.0205..0.9795 stay inside 0.02..0.98; pixels outside are unchanged; the shares below 0.02 and above 0.98 are equal before and after |
| 7 | `test_size_follows_the_long_edge` | `b(k)` = std of the k by k block means over std of the pixels. Flat frames of 400 by 600 and 800 by 1200, same size: `b_small(2)` and `b_big(4)` differ by less than 0.08 (measured 0.66, 0.62); `b_big(2) > b_small(2) + 0.1`; twice the size on the small frame gives `b(2)` within 0.08 of `b_big(2)` |
| 8 | `test_web_and_full_size_look_alike` | Grain on a flat 800 by 1200 frame, shrunk with `resize_long_edge` to 600 and to 300, against grain on a flat frame of that size: the std ratio lies in 0.85..1.15 (measured 1.04, 0.95) |
| 9 | `test_finer_than_a_pixel_is_weaker` | Flat 200 by 300, size 1 (cell 0.15 px), amount 100: std is `0.15 * _GRAIN_SIGMA` within 5 % |
| 10 | `test_shapes_and_small_frames` | Frames of 1 by 1, 16 by 16, 33 by 70, 301 by 127: same shape, float32, finite, inside 0..1 |
| 11 | `test_registered_last_and_not_rendered_by_apply_ops` | The name is in `OP_NAMES` and `SPATIAL_OPS`, its bounds are (0, 100); `STEPS[-1] == ("grain",)`, `STEPS[-2] == ("sharpen",)`; `apply_ops(x, {"sharpen": 20, "grain": 80})` equals `apply_ops(x, {"sharpen": 20})` |
| 12 | `test_default_is_off_and_byte_identical` | `RunConfig().grain is None`, `grain_size == 1.0`; every packaged look has `grain == 0`; `emit.write_image` with `grain=None` writes the bytes of a call without it; a stub batch with `grain=0.0, grain_size=3.0` writes the bytes of a default batch; the default sidecar has no `grain` setting and no note that starts with "grain" |
| 13 | `test_the_checks_do_not_see_the_grain` | `process_one` with `grain=60` and without: pixels `array_equal`; `Result.model_dump` equal apart from `global_settings`, `audit` and the timings; `global_settings` differs by `grain: 60` only |
| 14 | `test_export_adds_it_after_the_resize` | `write_image(out, png, "png", long_edge=300, grain=g)` decodes to `_to_u8(ops.grain(resize_long_edge(out, 300), *g))`; `write_outputs` gives a 300 by 200 export, `grain: 40` in the sidecar and the note, word for word |
| 15 | `test_rerun_and_rerender_are_byte_identical` | Two batch runs with grain: the same bytes; `adjust.rerender` without a change: the same bytes; the same photo under the same name in another folder: the same bytes; under another name: other bytes |
| 16 | `test_run_setting_look_and_decider` | A look with `grain = 30` in a temporary `looks.toml`: 24 at intensity 0.8, 30 at 1.0; with `grain=10`: 10; with `grain=0.0`: no `grain` setting; a decider that adds `grain` to the global and the region settings: none in the result |
| 17 | `test_cli_and_api_carry_the_setting` | `--grain 30 --grain-size 2`, `[run]` in a `--config` file and `service.make_cfg` each set both; without them `grain` is None; 150, -1, size 9 and size 0.1 raise `ValueError` |
| 18 | `test_preview_shows_the_grain` | `service.preview` with and without grain: the same "before", another "after"; "after" equals `_img_b64(out, 2560, fmt="webp", grain=emit.grain_args(res, cfg))` |
| 19 | `test_gates_do_not_fire_on_grain_alone` | A sky gradient over textured ground, grain 100 laid over it: `banding.assess` gives "pass" with score 0.0 on a smooth share above 0.3; `gates.assess` on the statistics of both gives score 1.0, no review, no warning; at grain 25 `quality.assess_pixels` with a sky mask passes (boundary measure 0.13) |
| 20 | `test_safety_workflow_adds_no_grain` | `render_safe` with `grain=40`: the pixels of the run without; no `grain` setting; the note |
| 21 | `test_instruction_words_golden` | The table "Required parses", exactly; `"grain" not in vocab.limits`; the prompt of the local model (`translate_local._system`) does not contain "grain"; `parse_set("global.grain=20")` works; `parse_set("sky.grain=20")` raises `ValueError` that names `global.grain` |
| 22 | `test_typed_grain_reaches_the_export` | Stub pipeline: `edit="add grain"` gives 25 and status `applied`; with `grain=30`: 55; five grain clauses: 100 and status `clamped`; on the look of test 16: 49; a batch with the instruction writes other bytes than one without, and its sidecar has the instruction note and the grain note |

Changed in existing tests, nothing else. On the scratch build exactly these three failed first:

- `tests/test_denoise.py::test_registered_before_sharpen`: `ops.STEPS[-2] == ("denoise",)`
  becomes `ops.STEPS[ops.STEPS.index(("sharpen",)) - 1] == ("denoise",)`.
- `tests/test_parallel.py::test_a_bundle_can_be_cut_between_any_two_steps`: the bundle gains
  `"grain": 30.0`.
- `tests/snapshots/settings_schema.json`: two more settings.

## Acceptance checks (the session runs these)

1. `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py`
   are green.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Once more into a new
   folder with `--grain 40 --grain-size 4`: the exports differ from the default ones, each
   sidecar has `grain: 40` and the grain note, and a second run writes the same bytes.
3. Default output is byte-identical to the baseline renders (the reference photos and the smoke
   corpus, maximum difference 0). Expected and not pixels: sidecars gain `grain` and `grain_size`
   under `audit.run_config`, and `config_digest` changes because `instructions.toml` gains a
   verb, so `autoedit adjust` on an export from before F23 prints its usual warning.
4. The tone gate of `CLAUDE.md`:
   `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression`
   reports 0 dark to darker and 0 pushed into clipping. With `--set grain=50` the rows and the
   `safety_fallback` of every photo are the same: this report renders through the safety
   workflow, which adds no grain.
5. Tone on the grained exports themselves, which check 4 cannot see. For 12 photos of the corpus,
   write each edit at full size and at 2048 px, without grain and with grain 50, and compare the
   two files with `tonemetrics.describe` and `tonemetrics.compare`. Measured on the scratch build
   (24 exports, JPEG 95): 0 dark to darker, 0 pushed into clipping; the luma clip shares move by
   at most 0.0001, the mean by 0.00003, the foreground median by at most 0.008, the bottom band
   by 0.001; the share of pixels with a channel beyond 0.02 or 0.98 rises by at most 0.004.
6. The sheet for the owner, `out-features/F23/crops.jpg`, made with a small script of the session
   that is not committed. Four photos (a sky, a person, a dark one, snow). Per photo three rows of
   crops of 420 by 300 px from the same spot: the full-size export at 100 %, the 2048 px export
   at 100 %, the full-size export shrunk to 2048 px. Columns: grain 0, 15, 25 and 50 at size 1,
   and 25 at size 2. Every cell has its caption. The exports stay next to the sheet.
7. `ops.grain` takes about 0.4 s on a 24 megapixel frame, and a default run takes no longer.
8. In the browser, stub backend: "add grain" in the instruction box shows grain in the preview
   and `grain +25.0` in "What it did"; "add grain to the sky" is reported as not understood.
9. The Checker's review, and its look at the sheet: grain is visible at 25, has no pattern or
   grid, and rows one and three of a photo look alike.

## Known limits

- Luma grain only. No colour grain and no setting for roughness.
- A picture under 2000 px on its long edge (at size 1) gets weaker grain, because a clump would
  be finer than a pixel. The 256 px photos of the smoke corpus get 13 % of the strength.
- Strong grain cuts a colour channel that sits near 0 or 1 in a mid-bright, saturated area: at
  100, up to 3 % of the pixels of one bright sky crossed 0.98 in a channel. The luma cannot cross.
  The mean is kept; a median can move a little (check 5).
- The checks judge the picture before the grain. Tools that write their own pictures show the
  edit without it: `scripts/blindtest.py`, `autoedit calibrate`, `experiments.py`, the safety
  workflow. `scripts/contact_sheet.py --panel` shows it.
- The word "grain" is now a verb: "darken the wood grain" is not understood (two verbs). "less
  grain" is not understood either, and a typed sum cannot go below 0, so a follow-up cannot take
  grain away. Lower `--grain`.
- The cloud decider is still offered the op name (its list is `OP_BOUNDS`); its value is dropped.
- One file name, one pattern: it cannot be drawn again, and two photos with the same name share it.
- Files grow: at grain 25 a JPEG of quality 95 grew from 0.74 to 0.97 MB at 2048 px and by 13 %
  at full size; the lossless preview by 11 %.
- A rerun on one install is byte-identical. Across numpy versions the floats differ in the last
  bit (measured: 6e-8 between the two interpreters, no 8-bit value changed on the test frame).

## Proposed plan row

`| F23 | Grain | M | done, off by default (`--grain N`, a look's `grain`, "add grain"); the owner looks at the crops in `out-features/F23/` |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Is grain 25 the right light grain, and should a packaged look carry grain?"
  Evidence: `out-features/F23/crops.jpg`. The change: `_GRAIN_SIGMA` in `edit/ops.py`;
  `grain = N` in a look of `looks.toml`.
- Changed against the plan: "Grain is the last step of `ops.STEPS` in name only: `apply_ops`
  does not render it, the export adds it after the resize." Why: the plan asked for both, and
  `apply_ops` also runs on proxies and before every gate. Reverse it by: none intended.

## Result

Built on 2026-11-17 in the unattended run. Status: done, off by default.

- **Built** by three Builders (Sonnet; T1 and T2 at high effort, T3 at medium) from this spec,
  which the Planner (Fable, max) wrote after building the feature once in a scratch copy. The
  Checker (Opus, high) found nothing that had to be or should be fixed; of its four notes the
  session fixed one (a region grain value replayed from a hand-edited sidecar is now dropped in
  `pipeline.py` block B, so the sidecar never shows grain that is not in the pixels). The other
  three stay notes: an instruction note can overstate the grain it added when a run setting plus
  the instruction pass 100 (the same as every other op); "give it film grain" is refused with the
  reason "grain is for the whole photo" (a leftover word becomes a target); test 19 asserts the
  audit passes at 25 without pinning the measure.
- **Check 1:** 779 tests on both interpreters (757 before).
- **Checks 2 and 3:** the stub smoke run completes; default exports are byte-identical to the
  renders made before F37 (`out-f37/base-stub`). The Checker compared the committed code with the
  working tree on the smoke corpus and a synthetic photo through `autoedit run`, `adjust`, the
  preview and the variants: identical. `--grain 40 --grain-size 4` twice gives the same bytes,
  also with `--workers 2`, and differs from the default run.
- **Check 4:** the tone report on the 110 RAW photos of the curated corpus, default settings:
  0 dark made darker, 0 bright and 0 shadow pushed to clipping, 0 hard failures (11.5 minutes).
  The run with `--set grain=50` was not made: this report renders through the safety workflow,
  which drops the grain (test 20), so its rows cannot differ.
- **Check 5** (24 export pairs from 12 RAW photos, stub backend, grain 0 against 50, full size and
  2048 px): 0 dark to darker, 0 pushed into clipping; luma clip shares move by at most 0.00003,
  the mean by 0.00001, the foreground median (not sky-excluded here) by at most 0.0046; the share
  of pixels with a channel beyond 0.02 or 0.98 rises by at most 0.0087, on the two dark photos
  (0.004 had been measured on the scratch build). Script and exports: `out-features/F23/`.
- **Check 6:** the sheet `out-features/F23/crops.jpg`, four panel photos. Grain shows at 25 as a
  fine even texture with no grid; the 2048 px export and the full-size export shrunk to 2048 px
  look alike (rows two and three; row one is a 100 % crop of a smaller area and is not meant to
  match row three, the check's wording was off). On the snow photo the spot is a rock edge and
  grain is hard to judge there.
- **Check 7:** `ops.grain` takes 0.36 s on a 24 megapixel frame (median of 3).
- **Check 8:** through the preview service with the stub backend: "add grain" changes the after
  picture, `global_settings.grain` is 25 and the note reads `applied to the whole photo (grain
  +25.00)`; "add grain to the sky" is "not understood (grain is for the whole photo)". Not
  checked in the browser itself: Studio always asks for SAM 3.
- **Check 9:** done (the review above; the sheet was looked at by the session).
