# F29. Teaching mode

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F29. Written in an
unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked; the owner's decisions are in the table
below. Build it after [F27](F27-style-from-edits.md), whose `habits` field a try has to empty; nothing else of F26 to
F30 is needed. It completes the lessons file that [F37](F37-point-and-tell.md) started.

The design was built twice on scratch copies of the tree (the second with F26 and F27's first two tasks in it), both
deleted again: the reference code is that code, and every number is measured with the stub (SAM 3 was not run). On the
second copy both suites gave 870 passed and 0 failed on both interpreters: the 853 tests of that tree and 17 new ones
(the 18th of this spec replaces a test). Of 16 rules removed in turn, each failed a test.

## Goal

**Step through:** the edit of one photo again, one step at a time in editing order, each with its picture, its tools
and three lines on what a tool does, when editors use it and what to watch for. **Try first:** the user edits the
untouched photo with seven sliders and typed sentences, then sees the engine's edit beside it: the values of both and
what the checks measure on both. **Your style** stays F27's listing. Two keys in the body of `POST /preview`; no page,
no route, no run setting; default output stays byte-identical.

## What was found in the code

1. `edit/apply.py::apply_image` hands five pictures to `on_stage`: `after_regions`, `after_composition`, `after_global`,
   `after_palette`, `after_chroma_guard`. `pipeline.py::process_one` adds `candidate_decode` (the picture it edits) before
   them and `final_float` after the reference match, the neutral pass of a full black and white and the LUT. Only
   `workflows.py::process` listens today.
2. The global pass is one call of `ops.apply_ops`, or three with the skin guard: `apply_image` cuts `ops.STEPS` at the
   first guarded op that has a value, renders the steps before it once and the rest twice, with and without the guarded
   ops, and blends the two by the guard mask. `test_parallel.py::test_a_bundle_can_be_cut_between_any_two_steps` holds
   `apply_ops` to it: a bundle cut between two steps and applied in two calls gives the same bits.
3. Two steps of `ops.STEPS` are values that `apply_ops` does not render: `lut` (`process_one` lays it after the gates)
   and `grain` (`emit.py::write_image` and `web/service.py::_img_b64` add it after their resize). `apply_ops` returns
   its input object when it renders nothing.
4. The banding gate in `process_one` can call `apply_image` a second time with damped settings. The sidecar then holds
   the damped values, and every observer hears the second render too.
5. `web/service.py::preview` encodes with `_img_b64`: lossless WebP at 2560 px. `imageio.py::_to_u8` dithers by a hash
   of the pixel's position, so one float picture always gives the same bytes. `web/app.py` hands the whole request body
   to `preview`, and `test_api_contract.py::_projection` records routes and parameter names only.
6. A `Pipeline` is a dataclass whose parts can be replaced after `build` (`service.build_pipeline` replaces the
   segmenter); a look is read with `self.looks.get(name, Look())`, its habit (F27) from `self.habits`.
   `instructions.py::_summed` clamps a typed change to `[limits]` and a direct one (an `Adjustment` without `source`,
   what `adjust --set` makes) to the op's bounds; both then pass the region policies and every gate.
7. The sidecar holds part of what the checks measure: the clipped shares of the frame and of every found region,
   before and after (`stats.py::compute`), `rgb_highlight_clip_any`, and the verdict of `edit/gates.py::assess`. The
   banding score (`edit/banding.py::assess`) and the share the oversaturation gate pulled back reach it only as notes,
   above a threshold. Nothing measures the colour of skin; `edit/reference.py::rgb_to_lab` gives CIELAB.
8. `configs/lessons.toml` holds 7 of the 36 ops of `ops.OP_NAMES`; `lessons.py::load` returns three tables;
   `test_lessons.py::test_every_typed_tool_has_a_lesson` checks the ops of `[limits]`; F37's `lessons.walk` is in every preview.

## Measured

Stub backend; a synthetic photo (sky, foliage, a skin-coloured oval) and four 22 megapixel JPEGs of the Dolomites
corpus, which a preview works on as 4096 px copies; load average 3.7 to 5.1. Not measured: SAM 3, a browser, RAW files.

- **The same bits.** `apply_image` with and without `on_step`, 144 cases (3 frame sizes, 1 and 4 threads, 4 bundles, 6
  guard sets): `array_equal` in all. A preview with `steps` has the `after` of the plain preview byte for byte, for 9
  option sets on the synthetic photo and on the four real ones; with `{"px": 2560, "lossless": true}` its last step's
  picture is that `after` too. Without F29 against with it: the stub smoke exports and 29 previews are equal, 31 of 31.
- **Time** on 22 megapixels: a plain preview 10.9 to 11.7 s, with `steps` 15.1 to 15.6 s (12 steps), a try 9.0 to
  10.0 s, with `compare` 17.9 to 18.1 s. The global pass on 11 megapixels, with the guard: 1.14 s, step by step 1.42 s.
- **Encoding is the cost.** A 4096 px picture as lossless WebP at 2560 px: 3.2 s, 3.6 MB; at 1280 px: 0.94 s, 1.0 MB; as
  a JPEG of quality 90 at 1280 px: 0.30 s, 0.2 MB. Twelve step pictures: 2.5 to 2.9 MB as JPEGs, 12.7 to 13.5 MB
  lossless. An answer with steps: 13.3 to 14.2 MB of JSON, against 9.9 to 10.3 MB without.
- **A try that goes too far** (exposure +1.2, contrast +25, saturation +40, "darken the sky", on a real photo): "Your
  version clips 68 % of the sky. The source: 0.0 %. AutoEdit: 0.0 %." A try with nothing set is the source, byte for byte.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| A page, a route? | Neither. Two keys in the body of `POST /preview`: `steps` and `try`; both snapshots stay. The test UI that follows the plan is the first page, `studio.html` is not touched, and the plan's check "in the browser" runs from Studio's console (acceptance check 5) | The test UI; a route that calls `service.preview` |
| What is replayed? | The preview's own render, heard through two observers. Not a second implementation of the edit, and not a re-render from a sidecar on disk: the plan's "code of `adjust`" is `process_one`, and a preview has no sidecar yet | `autoedit adjust --steps DIR` with the same observers |
| What is a step, and its picture? | Design 1, with one picture for all regions and one for everything after the checks. A JPEG of quality 90 at 1280 px; 256 to 2560 px and lossless on request, which is how the byte-for-byte check is made | `teach._STAGES`, more `on_step` calls; `teach.STEP_PX` |
| What does a try keep of the engine? | Its analysis, its masks, the region policies and every check. Nothing that edits: no rules, look, look habit, palette, shapes, sharpening, grain, reference, LUT, run instruction or prompt. So a check can pull a try back, and the table shows what was set beside what was rendered | `TRY_DROPS`, `TRY_FIXED` in `web/service.py`. A try that passes the checks by is not meant |
| Which sliders? | Exposure, contrast, highlights, shadows, temperature, tint, saturation, each wider than the engine goes, so a try can fail. Data: `[try.sliders]` | `lessons.toml` |
| Which facts? | Design 4: blown, crushed, a colour channel at its limit, banding, the colour shift of skin and snow, and what the checks said. A flag only where a threshold exists already (`[quality_gate]`, the banding gate) | `teach.compare` |
| Can a try be exported? | No. Only `/preview` reads `try`; `/run-one` and `/jobs` export the engine's edit (test 17) | A feature of its own |
| Your style | Not built. F27's history is off and empty, a line of it holds no file name, path or pixel (so no example photos), and F27's route gives what a first panel shows (see API) | A task on a real history |
| The lesson texts | Written here by the Planner (Design 6): 29 tools, 5 steps, 5 checks. The session reads every line | `lessons.toml` |

## Design

### 1. The steps

| `kind` | Its picture is | It is a step when |
|---|---|---|
| `source` | `candidate_decode`: the picture that is edited | always, first |
| `regions` | `after_regions`: every region's own bundle, blended by claim | a pixel moved half a level of 255 or more |
| `shapes` | `after_composition`: vignette, gradients, glow, the places and ovals of instructions | the same |
| `tools`, one per group of `ops.STEPS` | what `on_step` reports (Design 2) | the group has a value and rendered something |
| `palette` | `after_palette` | a pixel moved half a level or more |
| `checks` | `after_chroma_guard`: the clip to 0..1, the highlight hold, the oversaturation gate | always |
| `finish` | `final_float`: the reference match with its second pass of the checks, the neutral pass of a full black and white, the LUT | a pixel moved half a level or more |
| `grain` | the finished picture, encoded with `emit.grain_args` | the edit has grain |

### 2. The global pass, one step at a time (`edit/apply.py` above `apply_image`, `pipeline.py`)

```python
def global_steps(out, global_settings, guard_mask=None, guard_ops=None):
    """The global pass of `apply_image`, one step of `ops.STEPS` at a time (F29). Yields (the ops of the step that
    have a value, the picture after it) for every step that renders something. The last picture is bit for bit what
    the one-pass code gives; with a guard every picture is the blend of the two renders so far."""
    guard = guard_mask is not None and bool(guard_ops)
    full = kept = out
    shown = False
    for step in ops.STEPS:
        part = {k: global_settings[k] for k in step if global_settings.get(k)}
        if not part:
            continue
        was_full, was_kept = full, kept
        full = ops.apply_ops(was_full, part)
        keep = {k: v for k, v in part.items() if k not in guard_ops} if guard else part
        # until a guarded op has a value the two renders are one array
        kept = full if (was_kept is was_full and keep == part) else (ops.apply_ops(was_kept, keep) if keep else was_kept)
        if full is was_full and kept is was_kept:
            continue                # a value apply_ops renders nothing for (lut, grain), or an op with nothing to do
        shown = True
        yield tuple(part), (_guard_blend(full, kept, guard_mask) if guard else full)
    if guard and not shown:         # the one-pass code blends the input with itself
        yield (), _guard_blend(out, out, guard_mask)
def _guard_blend(full, guarded, guard_mask):
    return par.rows(lambda f, g, m: f * (1.0 - m[..., None]) + g * m[..., None], full, guarded, guard_mask)
```

The bits are the same because a bundle cut between two steps and applied in two calls is the same sequence of calls
(fact 2), and the guarded render parts from the full one at the first step with a guarded op, as in the one-pass code.
`apply_image` gains a last parameter `on_step=None` (a read-only observer; None: no new code runs). Its line `if
global_settings:` becomes `if global_settings and on_step is not None:` with the body `for keys, out in
global_steps(out, global_settings, guard_mask, guard_ops): on_step(keys, out)`, then `elif global_settings:` over the
block that is there today, which does not change. `pipeline.py::process_one` (protected; the stage order does not
change) gains a last parameter `on_step=None` after `proxy`, and both calls of `apply_image` end with
`chroma_reference=gate_src, on_step=on_step,`. Nothing else changes there.

### 3. The collector (`teach.py`, new)

It reads what a render reports and changes no pixel; `_lines` and `_pct` come from `lessons.py`. `NOTES` holds the
sidecar notes that belong to a step, by how they begin; `_TYPED` says in which kind of step a typed change acts, by the
kind of its mask. After a second render by the banding gate the steps are those of that render (fact 4).

```python
STEP_PX, STEP_PX_RANGE = 1280, (256, 2560)      # long edge of a step picture: the default, the limits
# ponytail: one picture for all regions and one for all after the checks; more needs more on_step calls with names.
_STAGES = {"candidate_decode": "source", "after_regions": "regions", "after_composition": "shapes",
           "after_palette": "palette", "after_chroma_guard": "checks", "final_float": "finish"}
_QUIET = ("regions", "shapes", "palette", "finish")   # no step of their own unless a pixel moved half a level
NOTES = {"palette": ("palette:",),
         "checks": ("highlight hold:", "oversaturation gate:", "banding gate:", "banding watch:", "⚠ needs review:"),
         "finish": ("reference match", "post-reference", "lut:")}
GUARD_NOTE = "skin-safe guard:"
_TYPED = {"thing": "regions", "named": "regions", "area": "shapes", "place": "shapes", "photo": "tools"}
def _tool(op, value, les):
    return {"op": op, "value": round(float(value), 4), **_lines(les, "op", op)}
class Steps:        # the pictures of one render: `stage` is on_stage, `step` is on_step, `encode` makes an answer's picture
    def __init__(self, encode):
        self.encode, self.items, self.first, self.last = encode, [], None, None
    def _add(self, kind, keys, rgb):
        d = None if self.last is None or rgb is self.last else np.abs(rgb - self.last)
        peak, moved = (0.0, 0.0) if d is None else (float(d.max()) * 255.0, float(d.mean()) * 255.0)
        same = self.last is not None and peak == 0.0        # the same picture gives the same bytes
        self.items.append({"kind": kind, "ops": list(keys), "peak": peak, "moved": round(moved, 3),
                           "after": self.items[-1]["after"] if same else self.encode(rgb)})
        self.first = rgb if self.last is None else self.first
        self.last = rgb
    def stage(self, name, rgb):
        if name == "after_regions" and len(self.items) > 1:     # the banding gate rendered again:
            del self.items[1:]                                  # the second render is the edit
            self.last = self.first
        if name in _STAGES:
            self._add(_STAGES[name], (), rgb)
    def step(self, keys, rgb):
        if keys:
            self._add("tools", keys, rgb)
    def done(self, result, les, guard_ops=(), walk=(), grained=None) -> list[dict]:
        items = list(self.items)
        if items[-1]["kind"] == "finish" and items[-1]["peak"] < 0.5:
            last = items.pop()                      # nothing to see: the picture stays, the step goes
            items[-1] = {**items[-1], "after": last["after"]}
        items = [it for it in items if it["kind"] not in _QUIET or it["peak"] >= 0.5]
        if grained:
            items.append({"kind": "grain", "ops": ["grain"], "moved": None, "after": grained})
        ...                                         # one dict per item: the table below
```

| Key of a step | Content (`walk`: what `lessons.walk` gave; `grained`: the finished picture with its grain, if any) |
|---|---|
| `kind`, `moved`, `after` | the item's |
| `tools` | `_tool(op, result.global_settings.get(op, 0.0), les)` for each op of the item; a `finish` has `lut` when that has a value |
| `title` | the names of the tools (the op where a lesson has no name) joined by ", "; without tools the `name` of the step's lesson, else the kind |
| `does`, `when`, `watch` | the step's lesson: `[step.KIND]`, for `checks` `[guard.gate]`; absent where there is none (`tools`, `grain`) |
| `where` | `regions`: for each entry of `result.masks` with a non-zero op, `{target, coverage, tools}` with a tool per non-zero op; `shapes`: the same from `result.composition`; else `[]` |
| `typed` | the indices of the entries of `walk` that act in the step: `_TYPED[entry["mask"]["kind"]]` is the step's kind, and in a `tools` step the entry also has a tool of the step |
| `notes` | the notes of `result.notes` that start with a prefix of `NOTES[kind]`; in a `tools` step that holds an op of `guard_ops`, those that start with `GUARD_NOTE`; else `[]` |

### 4. The compare (`teach.py`)

```python
def measure(source, out, proxy, union, pipe) -> dict:
    """What the sidecar does not hold: the banding score, and how far the mean colour of each protected region moved
    (CIELAB a and b). `source`, `out`: one size; `proxy`: the analysis picture of the source (an Image); `union`: its masks."""
    def small(rgb):
        return downscale_long_edge(Image(rgb=rgb), 1280).rgb        # the size the banding gate reads
    score, action = banding.assess(small(source), small(out), pipe.banding)[:2] if pipe.banding else (0.0, "pass")
    post = resize_to(downscale_long_edge(Image(rgb=out), pipe.cfg.analysis_long_edge), *proxy.rgb.shape[:2])
    colour = {}
    for name, mask in union.items():
        if meta_for(name, pipe.meta).guard_global and mask.any():
            (a0, b0), (a1, b1) = (rgb_to_lab(x.rgb[mask][None])[0, :, 1:].mean(axis=0) for x in (proxy, post))
            colour[name] = float(math.hypot(a1 - a0, b1 - b0))
    return {"banding": float(score), "banding_action": action, "colour": colour}
```

`compare(mine, theirs, mine_m, theirs_m, les, sliders=None, gate=None)` takes the two results and their two measures:

- `tools`: for `global`, then every target of `masks`, then of `composition` (the try's first, then the engine's that
  are new), one row per op in `ops.STEPS` order with a value in the try, in the engine's edit or, for `global`, in
  `sliders`: `{where, op, the four lesson lines of the op, set, yours, engine}`. `set` is the slider's value or `None`;
  `yours` and `engine` are the two results' values, rounded to 4 decimals.
- `said`: for `yours` and `engine`, `{quality, needs_review, warnings}` from the result and `notes`: its notes that
  start with a prefix of `NOTES["checks"]`, with `GUARD_NOTE` or with `policy[`.
- `checks`: rows `{key, where, source, yours, engine, flag, line}` plus the four lines of `[check.KEY]`, in the table's
  order. `s, y, e` are `mine.global_stats`, `mine.post_edit_stats`, `theirs.post_edit_stats`; `t` is `{**gates.GATE_DEFAULTS,
  **(gate or {})}`; a share is written by `lessons._pct`; a row of the first four kinds is left out when all three
  values are under 0.0005, which reads 0.0 %.

| `key`, `where` | `source`, `yours`, `engine` | `flag` | `line` |
|---|---|---|---|
| `blown`, `frame` | `highlight_clip` of `s`, `y`, `e` | `yours - source > t["clip_budget"]` | "Your version clips {yours} of the frame. The source: {source}. AutoEdit: {engine}." |
| `crushed`, `frame` | `shadow_clip` | `yours - source > t["shadow_clip_budget"]` | the same with "crushes" |
| `blown`, then `crushed`, for each region of `mine.region_stats` that both `post_region_stats` hold | `clip_high` (then `clip_low`) of the region's entry in `region_stats` and in the two `post_region_stats` | as for the frame | as for the frame, with "of the {region}" |
| `channel`, `frame` | `rgb_highlight_clip_any` of `s`, `y`, `e` | `yours - source > t["clip_budget"]` | "In your version {yours} of the frame has a colour channel at its limit. The source: {source}. AutoEdit: {engine}." |
| `banding`, `frame` | `None`, then `banding` of the two measures | the try's `banding_action` is not `pass` | "Banding in smooth areas: {yours:.2f} in your version ({action}). AutoEdit: {engine:.2f}." |
| `colour`, each region of the try's `colour` | `None`, then the two shifts (0.0 where the engine's measure has none) | never | "Your version moved the colour of the {region} by {yours:.1f}. AutoEdit: {engine:.1f}." |

### 5. Try first, and the service (`teach.py`, `web/service.py`)

`class NoEdit` is the decider of a try: `name = "none"`, and `decide(self, ctx)` returns `({}, {})`. The sliders become
one direct adjustment on the whole photo, placed first. Three functions of `teach.py` check what comes in:

| Function | Returns, and refuses with a `ValueError` |
|---|---|
| `check_steps(value) -> (px, lossless)` | `None` and `False`: `(0, False)`. `True`: `(STEP_PX, False)`. An object with `px` (a whole number inside `STEP_PX_RANGE`) and `lossless` (true or false), each optional: those. Refused: anything else, a boolean as `px`, another key |
| `sliders(les) -> {op: (lowest, highest, step)}` | `[try.sliders]`, in file order, as floats. Refused: an op that is not in `ops.OP_BOUNDS`; a value that is not a list of three finite numbers (no boolean); a range outside the op's bounds or with lowest at or above highest; a step of 0 or less |
| `check_try(value, table) -> {"sliders": {op: value}, "compare": bool}` | The sliders that are not 0, as floats. Refused: no object; a key besides `sliders` and `compare`; an empty `table` ("lessons.toml has no [try.sliders] table; copy it from the packaged file"); `sliders` that is no object; `compare` that is no boolean; an op without a slider (the message lists the sliders); a value that is a boolean, no number, or outside the range (written `not lo <= v <= hi`, which refuses NaN too) |

In `web/service.py` the import line gains `teach`, `Adjustment` is imported from `..contract`, and above `preview`:

```python
TRY_DROPS = ("edit", "prompt", "reference", "lut")            # run options that edit: a try starts without them
TRY_FIXED = {"decider": "rules", "allow_cloud": False, "compose": False, "palette": "none", "grain": 0.0}
NOT_THE_ENGINE = ("try", "steps", "photo_edit", "photo_marks")   # request keys that are the user's own
```

`_edit(image_path, opts, configs_dir, profile, trial=None, steps=None) -> dict` is today's body of `preview` from `cfg
= make_cfg(...)` to the line that calls `_mark_answer`, moved without a change but these: the first four lines below
open it; the next two follow `photo_adjs = photo_adjs + mark_adjs`; `_resolved` also fills a new dict `union` from
`resolved.union`; `process_one` gets `on_stage=steps.stage if steps else None, on_step=steps.step if steps else None`.

```python
    cfg = make_cfg({**{k: v for k, v in opts.items() if k not in TRY_DROPS}, **TRY_FIXED} if trial is not None else opts,
                   configs_dir, profile)
    pipe = build_pipeline(cfg)
    if trial is not None:
        pipe.decider, pipe.looks, pipe.habits = teach.NoEdit(), {}, {}
    if trial and trial["sliders"]:      # a direct adjustment, as `autoedit adjust --set global.OP=VALUE` makes it
        photo_adjs.insert(0, Adjustment(target=instructions.GLOBAL, ops=dict(trial["sliders"]), scope="photo"))
    return {"cfg": cfg, "pipe": pipe, "work": work, "proxy": proxy, "out": out_rgb, "result": result, "union": union, "walk": walk,
            "overlays": overlays, "problems": [*pipe.adjust_problems, *photo_problems], "marks": marks, "mark_overlays": mark_overlays}
```

`preview` keeps its signature; its docstring gains three lines on the two keys. After `check_image(image_path)`:

1. Only when the request has `try` (not None) or a truthy `steps`: `les = lessons.load(paths.config_path("lessons.toml",
   packaged_fallback=True))`. With `try`: `table = teach.sliders(les)`, then `trial = teach.check_try(opts["try"], table)`.
2. `px, lossless = teach.check_steps(opts.get("steps"))`, `px = min(px, max_px)`. With `px`: `walk = teach.Steps(small)`,
   where `small(rgb, grain=None)` is `_img_b64(rgb, px, fmt="webp" if lossless else "jpeg", grain=grain)`.
3. `r = _edit(image_path, opts, configs_dir, profile, trial, walk)`. Today's eight keys of the answer are built from
   `r`, with `grain = emit.grain_args(r["result"], r["cfg"])` on `after` as now. With `walk`: `answer["steps"] =
   walk.done(r["result"], les, r["pipe"].guard_ops, r["walk"], small(r["out"], grain) if grain else None)`.
4. With `trial`: `answer["try"] = {"sliders": [...]}`, one entry per slider of `table`, in its order: `{op, min, max,
   step, value, the lesson lines of the op}`; `value` is the checked value, or 0.0.
5. With `trial["compare"]`: `e = _edit(image_path, opts without the keys of NOT_THE_ENGINE, configs_dir, profile)`; for
   `x` in `(r, e)` a measure `teach.measure(x["work"].rgb, x["out"], x["proxy"] or downscale_long_edge(x["work"],
   x["cfg"].analysis_long_edge), x["union"], x["pipe"])`; then `answer["compare"] = {"engine": _img_b64(e["out"], max_px,
   fmt="webp", grain=emit.grain_args(e["result"], e["cfg"])), **teach.compare(r["result"], e["result"], the two
   measures, les, trial["sliders"], r["pipe"].quality_gate)}`.

### 6. Lessons (`configs/lessons.toml`, `lessons.py`)

`lessons.py`: `TABLES = ("op", "mask", "guard", "step", "check", "try")`; `load` returns one dict per name. In the
file's header the `[op.NAME]` line becomes "one entry per op of edit/ops.py", and a line each is added for `[step.KIND]`
(a step of the replay that is not one tool), `[check.KEY]` (what a check measures on a finished picture) and `[try.sliders]`.
Each row is one entry `[TABLE.KEY]` with the lines `name`, `does`, `when`, `watch`: the ops after `[op.grain]`, the rest last.

| Entry | name | does | when | watch |
|---|---|---|---|---|
| `op.highlight_rolloff` | Highlight roll-off | Bends the brightest tones over gently, so a lift in exposure runs out below white and does not hit it. | Whenever exposure goes up on a photo with bright parts: clouds, snow, a white shirt. | Too much leaves the brightest parts flat and grey-white. Below its knee nothing changes. |
| `op.shadows_ev` | Shadow lift | Brightens the dark tones, counted in stops, and keeps black at black. | A dark foreground under a bright sky, a face in shade. | Noise comes up with the shadows. Past about one stop the dark parts start to look flat. |
| `op.highlights` | Highlights | Makes the bright half of the tones darker or brighter and leaves the dark half alone. | A bright sky or bright clouds that should show more of their shape. | Pulled far down, bright areas turn dull grey. A blown area stays blown: nothing is left in it to bring back. |
| `op.shadows` | Shadows | Makes the dark half of the tones brighter or darker and leaves the bright half alone. | Detail that hides in dark trees, rock or clothing. | It lifts pure black as well, so a strong lift lays a grey veil over the photo. The shadow lift does not. |
| `op.whites` | Whites | Moves only the brightest tones: up for a cleaner white, down for more room at the top. | Snow or clouds that look grey, or bright parts that sit too close to pure white. | Raised, it is the quickest way to clip a highlight. |
| `op.blacks` | Blacks | Moves only the darkest tones: down for a deeper black, up for a softer, lifted one. | A flat photo that has no real black, or a faded look with the blacks lifted. | Lowered, the dark parts close up into one black shape. |
| `op.curve` | Tone curve | Lays a soft S-shaped curve over the tones: more contrast in the middle, and both ends roll off gently. | Depth without the hard ends that plain contrast gives. Most looks use it. | It darkens the lower tones. On a dark photo that costs detail in the shadows. |
| `op.dehaze` | Dehaze | Estimates the haze between the camera and the scene and takes it out, so distant parts gain contrast and colour. Below zero it adds a light veil. | Distant mountains, a hazy valley, a view through moist air. | Smooth skies and fog break into bands, a sky can turn dark and too blue, and near things get heavy. |
| `op.black_point` | Black point | Finds the darkest tones the photo really has and moves them down to a true black. | A photo whose darkest part is only dark grey, so it looks flat. | Only the darkest tenth of the tones is reshaped. Fog has no black to find and is left alone. |
| `op.white_point` | White point | Finds the brightest tones the photo really has and moves them up to a clean white. | Snow, paper or clouds that came out grey. | It stops below pure white, so it cannot clip. A photo that is meant to be dim loses its mood. |
| `op.vibrance` | Vibrance | Makes colours stronger, the dull ones most, and leaves strong colours nearly alone. | A photo that looks pale overall. It is the safer choice wherever there is skin. | Grey rock and fog pick up colour that was never there. |
| `op.hsl_skin_sat` | Orange tones (skin) | Raises or lowers the strength of the orange hues only, where skin sits. | Skin that looks too red or too pale after the rest was edited. | It follows the hue, not the person: sand, wood and brick are orange too. |
| `op.hsl_green_sat` | Greens: strength | Raises or lowers the strength of the green hues only. | Grass and leaves that look dull, or greens that look neon and should calm down. | Greens turn artificial quickly. Yellow-green leaves are only partly reached. |
| `op.hsl_green_lum` | Greens: brightness | Makes the green hues brighter or darker and leaves other colours alone. | A dark forest that should open up, or a bright meadow that pulls the eye. | Lifted greens look flat and pale, and a rim can show where green meets another colour. |
| `op.hsl_blue_sat` | Blues: strength | Raises or lowers the strength of the blue hues only. | A pale sky or pale water, or a sky that looks too heavy. | A strong blue sky shows bands and noise, and blue shadows on snow grow with it. |
| `op.grade_shadows_temp` | Shadow colour: warm or cool | Adds a little warmth or coolness to the dark tones only. | Cool shadows under warm light give depth. It is the classic colour grade. | It is a look, not a correction: black stops being neutral, and skin in shade changes colour. |
| `op.grade_shadows_tint` | Shadow colour: green or magenta | Shifts the dark tones toward green or toward magenta. | A film look, or to take a colour cast out of the shadows. | Small values only. A tint in the shadows soon reads as a mistake. |
| `op.grade_highlights_temp` | Highlight colour: warm or cool | Adds a little warmth or coolness to the bright tones only. | Warm highlights for evening light, cool ones for a clean, cold look. | White things stop being white: clouds, snow, a shirt. |
| `op.grade_highlights_tint` | Highlight colour: green or magenta | Shifts the bright tones toward green or toward magenta. | A film look, or to take a cast out of bright areas. | It shows first in skies and on skin. Small values only. |
| `op.bw` | Black and white | Takes the colour out. Each colour turns into a grey of its own, and six weights set how bright. | Strong shapes and light, colours that distract, a set that should hold together. | Colours that differed only in hue end as the same grey. The weights pull them apart. |
| `op.bw_red` | Black and white: reds | Sets how bright red things come out in grey: up is lighter, down is darker. | Raised, skin and lips get lighter. A red jacket can stand out or step back. | Skin holds much red: lowered, faces get dark and every blemish shows. |
| `op.bw_yellow` | Black and white: yellows | Sets how bright yellow things come out in grey. | Autumn leaves, sand, dry grass, sunlit rock. | Skin has yellow in it as well. A strong move changes faces. |
| `op.bw_green` | Black and white: greens | Sets how bright green things come out in grey. | Raise it for lighter leaves and meadows, lower it to make them heavy. | Leaves are often more yellow than green: the yellow weight moves them too. |
| `op.bw_cyan` | Black and white: cyans | Sets how bright cyan things come out in grey. | Pale sky near the horizon, glacier ice, shallow water. | A sky runs from cyan into blue. Move both weights together, or a step shows in it. |
| `op.bw_blue` | Black and white: blues | Sets how bright blue things come out in grey. | Lower it for a dark sky with bright clouds: what a red filter did on film. | A sky pushed far down shows noise and bands, and blue shadows go black. |
| `op.bw_magenta` | Black and white: magentas | Sets how bright magenta and pink things come out in grey. | Flowers, dusk skies, pink clothing. | Most photos hold little magenta. A strong move mostly changes noise. |
| `op.denoise` | Noise reduction | Smooths noise away: colour speckle first, then some of the fine grain in brightness. | High ISO, or dark parts that were lifted and now show their noise. | Fine detail goes with it: hair, grass, fabric. Look at full size. |
| `op.sharpen` | Sharpening | Raises the contrast along fine edges, so detail looks crisper. | At the end, on almost every photo, lightly. | Bright rims along edges, and noise that is sharpened too. It cannot rescue a photo that is out of focus. |
| `op.lut` | LUT file | Moves every colour to the colour a table says, the same over the whole photo. The table is a .cube file. | A look that was made elsewhere, or one that should match video. | A table knows nothing about this photo: it can clip, and it can shift skin. It is laid on after the checks, so look at the result. |
| `step.source` | The photo as it came | The picture before any step: the file as it was developed, with no edit. | Every replay starts here. | Look at it for a moment before you step on. What would you change first? |
| `step.regions` | Region edits | Each thing that was found gets its own correction first: the sky, the skin, the grass. No pixel is edited by two regions. | Before anything is done to the whole photo, so the parts start from a good balance. | Edges. Switch the masks on and look where two regions meet. |
| `step.shapes` | Shaped light | Soft shapes laid over the frame: a darker edge, a gradient over the sky, a glow at the sun, and the places you named. | To lead the eye. Editors call them radial and graduated filters. | A shape does not follow outlines. When you can see the shape itself, it is too strong. |
| `step.palette` | Colour palette | Pushes shadows, midtones and highlights a little toward chosen colours and keeps their brightness. Skin and snow stay neutral. | A colour mood over the whole photo: cool, warm, sunset. | It lies over everything else. A strong palette makes every photo look alike. |
| `step.finish` | Finishing | What a run lays over the checked picture: a match to a reference photo, a LUT file, and a last pass that makes a full black and white truly neutral. | Only when the run asked for one of them. | The checks do not pull a LUT back. Look at the result. |
| `check.blown` | Blown highlights | The share of pixels brighter than 0.98 of 1. They are flat white: no detail is left in them. | Exposure, whites, highlights or contrast went too far for the bright parts. | A small light or a glint may clip. A sky, snow or a white dress should not. |
| `check.crushed` | Crushed shadows | The share of pixels darker than 0.02 of 1. They are flat black. | Exposure or blacks went down too far, or contrast went up. | A little true black gives depth. Dark hair or clothing that ends as one shape is lost detail. |
| `check.channel` | A colour at its limit | The share of pixels where red, green or blue is at its top. Brightness alone can hide it: a red jacket loses its folds long before it looks white. | Saturation, warmth or exposure were pushed on a colour that was strong already. | Flowers, sunsets, clothing, and skin in warm light. |
| `check.banding` | Banding | A score from 0 to 1 for steps the edit made in smooth areas such as sky or fog. When it is too high, the tone moves are reduced and the photo is rendered again. | Contrast, dehaze or a curve stretched a few close tones far apart. | Skies, fog and still water. A JPEG source bands sooner than a RAW file. |
| `check.colour` | Colour shift of skin and snow | How far the average colour of a protected region moved from the source, in Lab units. Brightness is left out. | White balance, tint, saturation or a colour grade reached a region that should keep its colour. | A shift of 2 to 3 is about where a viewer starts to notice. Skin goes orange or grey, snow goes yellow or blue. |

Then `[try.sliders]`, with the comment "The sliders of "try first" (F29), in the order of the panel: OP = [lowest, highest,
step], inside the bounds of the op.", and: `exposure = [-2.0, 2.0, 0.05]`, `contrast = [-50, 50, 1]`, `highlights = [-100,
100, 1]`, `shadows = [-100, 100, 1]`, `temp = [-50, 50, 1]`, `tint = [-50, 50, 1]`, `saturation = [-50, 50, 1]`.

## API

`POST /api/v1/preview` takes two more keys in its body; a malformed one is a 400. No other route reads them.

| Key | Shape |
|---|---|
| request `steps` | `true`, or `{"px": 256 to 2560, "lossless": true or false}`, each optional (1280, false). With `try` it replays the try |
| request `try` | `{"sliders": {op: number}, "compare": true or false}`, each optional. The engine's edit is left out; `photo_edit` and `photo_marks` are the user's sentences as always, and `before`, `after`, `result`, `walkthrough` and the other keys describe the try |
| answer `steps` | The steps, the source first: `{kind, title, tools: [{op, value, name, does, when, watch}], does, when, watch, where: [{target, coverage, tools}], typed: [index into walkthrough], notes: [text], moved, after}`. `after` is a JPEG data URL (lossless WebP on request); `moved` is the mean change in levels of 255, `null` for grain |
| answer `try` | `{"sliders": [{op, name, does, when, watch, min, max, step, value}]}`, in the order of the panel |
| answer `compare` | `{"engine": the engine's picture, encoded as after, "tools": [{where, op, name, does, when, watch, set, yours, engine}], "checks": [{key, where, source, yours, engine, flag, line, name, does, when, watch}], "said": {"yours": {quality, needs_review, warnings, notes}, "engine": the same}}` |

- **Step through** in the test UI: one request with `steps`. A step's picture before is the entry before it; for the
  last step a page may show `after`, that picture lossless and at full size. A step that is not `tools` or `grain`
  carries its lesson itself; `typed` leads to F37's panel for that typed change.
- **Try first.** `{"try": {}}` gives the untouched photo and the sliders. Each change is a full preview, so apply on a
  button or after a pause. With `compare`: the two pictures side by side, the `tools` rows grouped by `where` (`global`
  first), `set` beside `yours` where they differ, and the `checks` sentences, flagged ones first.
- **Your style.** Nothing is built. A first panel reads F27's `GET /api/v1/style/suggestions` (`on`, `photos`, `days`,
  `looks`, `suggestions` with `ask` and `fact`, `left_out`): off, with how to switch it on; on and empty; or the counts
  and suggestions, with F27's `POST /style/accept` behind a button. A fuller page wants every habit below the bar, which
  should be designed on a real history, and example photos, which a history line cannot name.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/configs/lessons.toml` | 39 entries, `[try.sliders]`, the header | T0 |
| `src/autoedit/edit/apply.py`, `src/autoedit/pipeline.py`, `src/autoedit/lessons.py` | `global_steps`, `_guard_blend`, `on_step`; `on_step` passed on; `TABLES`, `load` | T1 |
| `src/autoedit/teach.py` | new: the constants, `_tool`, `Steps`, `check_steps` (T1); `NoEdit`, `sliders`, `check_try`, `measure`, `compare` (T2) | T1, T2 |
| `src/autoedit/web/service.py` | the imports, `_edit`, `steps` in `preview` (T1); the three constants, the try lines of `_edit`, `try` in `preview` (T2) | T1, T2 |
| `tests/test_teach.py`, `tests/test_lessons.py` | new: tests 1 to 9 (T1), 11 to 18 (T2); test 10 replaces one (T1) | T1, T2 |

Not changed: `edit/ops.py`, `edit/gates.py`, `edit/banding.py`, `quality.py`, every other config file, `contract.py`,
`web/app.py`, `studio.html`, both snapshots. Relied on: F23 (`emit.grain_args`), F24 and F25 (the ops `bw*` and `lut`,
the look `bw_classic`), F27's task T2 (`Pipeline.habits`, and `cli.write_look(..., habit=)` in test 11; without it, drop
`pipe.habits` and that part of the test), F37 (`lessons.walk`, `_lines`, `_pct`). F26, F27 and F30 edit the import line
and `make_cfg` of `web/service.py`: the edits here go by their anchor text.

## Tasks (one after the other; `.venv` has no pytest, there `tests/run_all.py` proves)

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T0 | Tweaker, Haiku, low | Design 6: the entries from the table by its rule, the sliders block, the header lines | `PYTHONPATH=src python3 -c "import tomllib; from autoedit.edit import ops; d = tomllib.load(open('src/autoedit/configs/lessons.toml', 'rb')); assert set(d['op']) == set(ops.OP_NAMES) and all(e[k].strip() for t in ('op', 'mask', 'guard', 'step', 'check') for e in d[t].values() for k in ('name', 'does', 'when', 'watch')); print(len(d['op']), len(d['step']), len(d['check']), len(d['try']['sliders']))"` prints `36 5 5 7` |
| T1 | Builder, Sonnet, high (`pipeline.py`) | Designs 1 to 3; `check_steps`; `lessons.load`; `_edit` and `steps` in `preview`; tests 1 to 10 | `PYTHONPATH=src python3 -m pytest tests/test_teach.py tests/test_lessons.py tests/test_parallel.py tests/test_pipeline.py tests/test_web_service.py tests/test_follow_up.py tests/test_marks.py tests/test_grain.py tests/test_bw.py tests/test_lut.py tests/test_api_contract.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T2 | Builder, Sonnet, medium | Designs 4 and 5: the compare and the try; tests 11 to 18 | `PYTHONPATH=src python3 -m pytest tests/test_teach.py tests/test_lessons.py tests/test_web_service.py tests/test_follow_up.py tests/test_marks.py tests/test_style.py tests/test_security.py tests/test_api_contract.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec. With care: `global_steps` against the one-pass code, the `if` that became `elif`, the move into `_edit`, what a try keeps of a request, the three checking functions; the privacy check | none |
| Docs | The session, inline | `ARCHITECTURE.md` (sections 9 and 15: `on_step`, the two keys), `APP_GUIDE.md`, `README.md` (the line on `lessons.toml`), `CHANGELOG.md`, the plan row; it reads the lesson texts | none |

## Tests

`tests/test_teach.py`, stub backend; `EXACT = {"px": 2560, "lossless": True}`. `_photo(tmp_path, flat=False)`: sky, foliage
and a skin-coloured oval, 256 px, with noise that keeps the banding gate out; `flat=True`: no noise, so that gate renders again.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_stepwise_global_pass_is_bit_identical` | `apply_image(x, [], g, guard_mask=, guard_ops=)` with and without `on_step` is `array_equal`: a 96 px frame with 1 thread and one of 1301 by 1203 with 4 (row strips); a bundle of 19 ops with `lut` and `grain` in it, `{"saturation": 12}`, `{"clarity": 0, "grain": 20}`; no guard and four guard sets, one naming an op without a value. The ops `on_step` named are the ops with a value in `ops.STEPS` order, without `lut` and `grain`; the last picture, clipped, is the result |
| 2, 3 | `test_steps_end_at_the_preview`, `test_a_plain_preview_takes_no_new_path` | For 8 option sets (default, `dramatic`, grain, three typed changes, `bw_classic`, a prompt that brings a palette, no concepts, intensity 0): with `steps: EXACT`, `before` and `after` are the plain preview's, `steps[0].after == before`, `steps[-1].after == after`, and the result is equal apart from its timings; with `steps: true` the `after` is the same, every step picture is a JPEG data URL, and kinds and titles are those of the exact walk. A preview without the keys calls `process_one` once, with `on_stage=None` and `on_step=None`; its answer has no `steps`, `try` or `compare` |
| 4 | `test_steps_follow_the_editing_order` | "brighten the sky, warm the photo, darken the top" with `steps`: the kinds are in the order of Design 1, `source` first, `checks` once; the tools of all `tools` steps are the ops with a value in `ops.STEPS` order, each with the sidecar's value (4 decimals) and four lesson lines; every other kind has `does`, `when`, `watch`; `where` of `regions` equals `result.masks`, `where` of `shapes` holds `instruction:top`; `typed` leads to "the sky" (regions), "the top" (shapes) and "the whole photo" (one `tools` step); a `tools` step has the `skin-safe guard:` note only when one of its ops is guarded |
| 5, 6 | `test_a_second_render_replaces_the_first`, `test_grain_and_a_lut_are_steps` | Flat photo, `dramatic`: the plain preview has a `banding gate:` note; the walk has one `source`, one `regions`, one `checks`; its tool values are the sidecar's damped ones; its last picture is the plain `after`; `checks` carries the note. A 5-point `.cube` written into `tmp_path` and grain 40: the last two kinds are `finish` (tool `lut`, the `lut:` note) and `grain` (value 40, another picture than `finish`), and the last picture is the plain `after`. `bw_classic` alone: no `finish`, and the last picture is the plain `after` |
| 7 | `test_the_collector_keeps_the_last_picture` | `teach.Steps` fed by hand, with an `encode` that returns the mean: equal values after the source, a quarter-level change, a tool, a blend without ops that differs by 1e-6, the checks; all of it again with another lift; then a `final_float` under half a level. `done` gives source, the tool of the second render, checks; the picture of `checks` is the last one's and its `moved` is 0; with `grained` a last step `grain` |
| 8, 9 | `test_step_notes_are_the_pipelines_own_words`, `test_steps_are_checked` | Every prefix in `teach.NOTES` and `teach.GUARD_NOTE` occurs in the source of `pipeline.py`. As `steps`: `5`, `"yes"`, px 100, 99999, 1280.5 and `true`, an unknown key, `lossless: 1` are a `ValueError`; `false` gives no `steps`; `check_steps(True) == (1280, False)` |
| 10 | `test_every_op_has_a_lesson` (`tests/test_lessons.py`, replaces `test_every_typed_tool_has_a_lesson`) | The keys of `[op]` are exactly `ops.OP_NAMES`; `[mask]` holds thing, area, named, place, photo; `[guard]` policy, limit, gate; `[step]` source, regions, shapes, palette, finish; `[check]` blown, crushed, channel, banding, colour; every entry has the four lines, none empty; a missing file gives `{t: {} for t in lessons.TABLES}` |
| 11 | `test_an_untouched_try_is_the_source` | Style `dramatic`, look `bw_classic`, a prompt, a run `edit`, palette `cool_blue`, grain 30, `decider: "llm"` with `allow_cloud`, and `try: {}`: `after == before`; no settings, masks, composition or adjustments; `decider == "none"`, `model` is None; no `compare`; the answer's sliders are the table's, in order, all at 0. A profile look with a habit as `look`: the plain preview has its adjustments, the try has none and is the source |
| 12 | `test_a_try_applies_what_the_user_set_and_nothing_else` | Concepts `["sky"]`, sliders exposure 0.5, contrast 20, saturation 0, "brighten the sky, darken the top": global settings exactly `{exposure: 0.5, contrast: 20}`, masks `[("sky", {exposure: 0.3})]`, composition `[("instruction:top", {exposure: -0.3})]`; the first adjustment is global, has no source, status `applied`; with `steps` the walk is source, Exposure, Contrast, checks and ends at the try's own picture |
| 13 | `test_the_compare_table_matches_both_sidecars` | A try with `compare` against the plain preview of the same request: `compare.engine` is its `after`; the `engine` values of all rows are exactly the non-zero values of its `global_settings`, `masks` and `composition` (4 decimals), the `yours` values those of the try's result; `set` is the slider value and `None` without a slider; global rows are in `ops.STEPS` order; `said.engine` holds its `quality_score`, `needs_review` and `warnings` |
| 14, 15 | `test_the_facts_are_what_the_checks_measure`, `test_measure_reads_the_banding_gates_own_score` | Sliders exposure 2, saturation 50: the row (`blown`, `frame`) has `source`, `yours`, `engine` equal to `highlight_clip` of the three stats, is flagged, and its `line` starts "Your version clips"; (`blown`, `skin`) equals `post_region_stats` and reads "Your version clips 100 % of the skin. The source: 0.0 %. AutoEdit: 0.0 %."; no row for the sky, which clips in none of the three; (`channel`, `frame`) equals `rgb_highlight_clip_any`; (`colour`, `skin`) is larger for the try than for the engine; every row has the four lesson lines; `said.yours.needs_review`. A try with nothing set: no flag besides banding, a colour shift of 0.0. On the flat photo `measure` gives the score and the action of `banding.assess` on 1280 px copies, the score the sidecar's warning names; `colour` has `skin` only; a picture against itself gives 0.0 |
| 16, 17 | `test_a_try_is_checked`, `test_a_try_never_reaches_an_export` | A string, a list, an unknown key, `sliders` as a list, `compare: 1`, an op without a slider, 9, NaN, `true`, `"1"`, 10 to the 400th: `ValueError`. A slider table with an unknown op, a range outside the bounds, two numbers, lowest above highest, step 0, infinity, a string: `ValueError`; no table: `ValueError`; the packaged table is the seven of Design 6, in order. `service.run_one` with `try` and `steps` in its options writes the bytes of the run without them |
| 18 | `test_api_takes_steps_and_try` | Through `POST /api/v1/preview`: the two equalities of test 2; a try with `compare` answers 200 and its `engine` is the plain `after`; a bad `steps` and a bad `try` are 400 `bad_request`. `tests/test_api_contract.py` passes unchanged |

## Acceptance checks (the session runs these)

1. Both suites are green (`PYTHONPATH=src python3 tests/run_all.py`, and with `.venv/bin/python`), the stub smoke run of
   `CLAUDE.md` completes with sidecars and a manifest, the Checker has reviewed, and the session has read the 39 lessons.
2. Default output is byte-identical to the baseline renders (maximum difference 0), a preview without the two keys has
   the `before` and `after` of the tree before F29, and both snapshots are unchanged. No tone gate: no tone code changes.
3. The replay ends at the preview, with the stub, on two photos of the curated corpus. The line prints `True True` and
   the steps: `PYTHONPATH=src python3 -c 'from autoedit.web import service as s; p, o = "curated-dolomites-summer-2026/selected_jpeg/NAME.jpg", {"backend": "stub"}; a = s.preview(p, o); b = s.preview(p, {**o, "steps": {"px": 2560, "lossless": True}}); print(b["after"] == a["after"], b["steps"][-1]["after"] == a["after"], [x["title"] for x in b["steps"]])'`
4. The same once with SAM 3: `.venv/bin/python`, `"backend": "sam3"`, one job at a time, a panel photo with a person,
   so the skin guard is on; the time goes into the Result. Then a try there: `{"try": {}}` gives `after == before`, and
   the lines of a try with exposure +1.2 and `compare` are read.
5. In the browser, on a real photo: start `autoedit ui`, open a photo, generate its preview, run the lines below in the
   page's console. `[true, true, true]`: the last step's picture is the full preview byte for byte, and the compare
   table matches the sidecar.

   ```js
   const body = JSON.parse(previewBody(STATE.files[STATE.sel], [])), post = b => fetch('/api/v1/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)}).then(r => r.json());
   const plain = await post(body), walk = await post({...body, steps: {px: 2560, lossless: true}}), tried = await post({...body, try: {sliders: {exposure: 1.2}, compare: true}});
   [walk.steps.at(-1).after === plain.after, tried.compare.engine === plain.after, tried.compare.tools.every(r => r.where !== 'global' || Math.abs((plain.result.global_settings[r.op] || 0) - r.engine) < 1e-4)]
   ```

## Known limits

- **One picture for all regions,** and one for a reference match and a LUT together. With the skin guard a step's
  picture is a blend of the render with and the render without the guarded tools so far: the edit, had it stopped there.
- **A try is a full preview:** about 9 s on a 22 megapixel JPEG here, 18 s with `compare`, more for a RAW file, so a
  slider is not live. The way out is a `px` that bounds the working copy: since F37's T9 the decisions would not move.
- **A check can change a try.** The banding gate damps tone values and renders again; the table then shows `set` and
  `yours` apart, and `said` has the gate's note. A try has no sharpening, so the engine's picture looks crisper.
- **The measures are coarse.** Step pictures are JPEGs: a smooth sky can show blocks that the lossless `after` does
  not. The colour shift is of a region's mean colour, for skin and snow only. `moved` is a mean over the frame.
- **Not built:** keeping a try, a replay of an exported photo from its sidecar, a page, "your style", the plan's later
  local model that answers "why". An older local `lessons.toml` gives steps without lesson lines and refuses a try.

## Proposed plan row

`| F29 | Teaching mode | M | done as engine and API (`steps` and `try` on `POST /preview`); no page until the test UI; "your style" waits for a real history (F27) |`

For the owner's table: "Read the lesson texts, and say which sliders 'try first' should have and how wide" (evidence and
change: `configs/lessons.toml`). Changed against the plan: "F29 has no page of its own, and 'your style' is F27's
listing", because the test UI follows the plan and F27's history is off and empty.

## Result

Built on 2026-11-17 in the unattended run. Status: done as engine and API (no page; the test UI uses it).

- **Built** by Builders (Sonnet) from this spec, after F27. The 39 lessons were read by the session;
  two lines were corrected.
- **Found in the checks:** `steps: {}` (a valid value that asks for steps) crashed the
  preview with a 500, because the lessons were loaded only for a `try`: fixed by the session, with a
  test. The guard-kind assertion of the walk-through test was made exact.
- **What held:** default output is byte-identical to the committed code; a preview without `steps`
  and `try` answers as before; a try never reaches an export (`run_one` bytes unchanged).
- **Checks:** the replay with the stub on two photos of the curated corpus prints `True True` (the
  last step's picture is the full preview byte for byte), 11 and 12 steps, 50 s and 70 s for preview
  plus replay on a loaded machine (`out-features/F29/replay-stub.txt`). With SAM 3 on
  `0013_snow`: replay `True True`, an empty try gives `after == before`, a try of exposure +1.2 with
  `compare` has `engine` equal to the plain preview, and its lines read "Your version clips 42 % of
  the frame. The source: 0.2 %. AutoEdit: 0.0 %." and the same for the sky (19 %) and the mountain
  (62 %). Times there: preview 21.6 s, replay 44.4 s, try 7.9 s, try with compare 16.9 s
  (`out-features/F29/sam3-check.txt`). SAM 3 found no person in that photo, so the skin guard was
  not exercised with SAM 3 (the stub tests cover it). Not run: the browser check of acceptance 5
  (the test UI's walk-through covers steps and try in the page).
