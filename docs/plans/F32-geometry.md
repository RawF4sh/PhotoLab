# F32. Geometry: straighten and crop

Status: spec (2026-11-17). Size L. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F32.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and
the decisions that are the owner's are in the table below. The plan's own question, "may AutoEdit
straighten and crop?", stays open: both tools are built switched off.

The design was built once on a scratch copy of the tree, which is deleted again. The reference
code below is that code and every number is measured. On the copy both suites passed, on the
system Python and on `.venv`: 868 tests (the 848 of that copy, the 20 of this spec). No SAM 3 job
was run: real masks were read from the mask caches of earlier runs, without a model.

## Goal

Two tools a run can switch on. **Straighten** (`--straighten`) turns the export so that a
waterline or horizon that slopes by up to 5 degrees is level. It acts only on one long straight
line of that kind; otherwise it leaves the photo alone and says why. **Crop** (`--aspect 4:5`, or
`aspect` in an export preset) cuts the export to a shape, placed on the subject. Both are done to
the finished picture in one resampling (a crop alone resamples nothing); the sidecar gives the
angle and the share that was cut, and the preview shows the same frame. The plan's third tool,
**upright**, is not built (decision 3). Nothing is on by default: default output is byte-identical.

## What was found in the code

1. The plan asks for one resampling "after the colour steps and before `sharpen`". `sharpen` is a
   step inside `edit/ops.py::apply_ops`. After it, all on arrays of the source's shape, come the
   palette grade and two gates in `edit/apply.py::apply_image`, then in `pipeline.py::process_one`
   the banding check, a reference match, F24's block, F25's LUT and the stats for `gates.assess`.
2. `process_one` hands its pixels to callers that hold them against the source:
   `quality_runner.py::render_safe` (two shapes give `invalid_shape`, and the workflow falls back
   to the unedited photo), `workflows.py`, `experiments.py`, `calibrate.py` and two scripts.
3. A picture leaves the engine in two places (F23, facts 3 and 4): `emit.py::write_image`, which
   `scripts/contact_sheet.py::_render` calls too, and `web/service.py::_img_b64` (preview, variants).
4. `process_one` holds every mask at analysis size in `raw`, also for names that are no regions
   (`_find_light` reads `sun`); `presegment` must ask for the same names. In a render worker the
   segmenter does not say `open_vocabulary` (`batch.py::_NoModel`); `cfg.backend` is right there.
5. `segment/stub.py::_sky` and `_water` are brightness tests cut by a straight line at 55 % of
   the height. On 279 real photos that cut read as a level line on 71.
6. `exports.py::_KEYS` maps a preset's keys to `RunConfig` fields and refuses any other key.
   `[social]` has `long_edge = 1350`: with 4:5 that is 1080 by 1350.
7. `RunConfig.levels` is F5's black point, and `autoedit run --level` switches it on today:
   argparse takes an unambiguous prefix (tried).
8. Marks, named places, composition moves and the light are placed in shares of the frame that
   `process_one` edits; a thing mark's box is part of its name in the mask caches (`mark_name`).
9. `web/static/studio.html` lays `before`, `after`, the overlays and a drawn oval over one box
   sized by `after`. `scripts/contact_sheet.py::_facts` subtracts two thumbnails of one size.
10. SAM 3 found no `subject` in any of the 16 panel photos; three of them show a person.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| 1. May AutoEdit straighten and crop? | Open, as in the plan. Both tools exist and both are off: nothing is cut unless a run asks. No packaged preset changes; `[social]` gets a commented `aspect = "4:5"` | `straighten = true` under `[run]` in `defaults.toml`; remove the `#` in `exports.toml` |
| 2. The setting's name | `straighten`, not `level`: `levels` is the black point, and `--level` already means `--levels` (fact 7) | A rename |
| 3. Upright | Not built. A numpy estimate of leaning verticals was tried on 229 photos of the repo: one has clear verticals (a bridge), and they do not lean; with a lower bar it picks sun rays and snow texture. There is nothing to tune it on and nothing to show that it works | A feature of its own, once 20 or more photos of buildings or rooms exist. `warp` then needs `Image.PERSPECTIVE`, and `frame` a four-sided outline |
| 4. Where the picture is turned and cut | At the export, on the finished picture: `emit.write_image` and the preview. Not inside `apply` before `sharpen` | Not meant to be reversed: Design 1 |
| 5. Which line counts | The upper edge of the water first, then the lower edge of the sky; each only as one long straight line. Never a meadow's edge or a tree line | `LEVEL` in `geometry.py`; `[geometry]` |
| 6. The limits | A slope above 5 degrees is left alone, not turned by 5: it is meant, or it is no horizon. Under 0.3 degrees the photo counts as level and is not resampled | `max_angle`, `min_angle` |
| 7. Without SAM 3 | Nothing is straightened, and a note says so (fact 5); a crop is placed in the middle | The two `stub` lines of Design 3 |
| 8. Extra masks | With straighten on, the segmenter is also asked for `water` and `sky` when the run's concepts lack them; with a crop on, for `subject` and `person` (fact 10). One light pass each (F36). They never become regions | `_with_geometry_concepts` |
| 9. The crop | The largest frame of the shape, its middle on the middle of the subject's pixels as far as the photo allows. `4:5` is 4 wide to 5 high for every photo: a 3:2 landscape photo keeps 53 % | `frame` and `decide` |
| 10. Marks, places, the light | They stay on the source frame; geometry comes after them (Design 4) | none intended |
| 11. What the checks judge | The whole photo before the cut, as today | A second `stats.compute` on the kept frame |
| 12. The safety workflow (`quality_runner.py`) | Does no geometry and says so: its checks need the source's shape (fact 2) | The lines in `render_safe` |
| 13. Preview and Studio | `before`, `after` and every overlay show the export's frame. No control in Studio; the API takes `straighten` and `out_aspect` | The Studio rebuild |
| 14. A turn set by hand, or switched off for one photo | Not built: an `Adjustment` cannot carry it | A follow-up |

## Design

### 1. Where geometry acts

The plan's place cannot be had as written (fact 1): a resampling there needs the source, the
guard mask and the analysis masks resampled too, and turning the source first changes the shape
of what `process_one` returns (fact 2). So decision and pixels are split, as F23 split grain:

- **Decided in `process_one`**, after the decide stage, from the masks: a record in the result,
  not an op. No decider, look or instruction sets it, and `edit/` is not touched.
- **Done where the picture leaves the engine**: `emit.write_image` turns and cuts, then resizes,
  then adds grain; the preview does the same. `emit` already resamples web exports after
  `sharpen`. No stage is added or moved.
- **The gates and the post-edit stats** run before it, with today's code: both pictures they
  compare have the source's shape, and the export is a part of the picture they judged.
  `process_one` returns the same pixels with and without a tool (`array_equal` on 16 real photos).
- **Measured** on a 22 megapixel photo: turn after sharpen and sharpen after turn differ by 0.003
  levels on average; Pillow's bicubic keeps 1.01 of the unresampled frame's fine detail (bilinear
  0.86); a turn takes 1.15 s, the decision 8 ms. A crop alone is a cut on whole pixels.

### 2. The decision, the frame and the warp (`src/autoedit/geometry.py`, new)

The module, under its docstring and the imports `math`, `numpy as np`, `from PIL import Image as
PILImage` and `from .contract import Geometry`. Not shown: `config(rules)`, which returns
`DEFAULTS` with the `[geometry]` block of `rules` laid over it (numbers as floats, `subject` as a
tuple of strings; an older rules file has no such block).

```python
LEVEL = ("water", "sky")     # the masks straighten reads, in this order
DEFAULTS = {"max_angle": 5.0, "min_angle": 0.3, "tolerance": 0.004, "water_span": 0.5, "water_inliers": 0.8,
            "sky_span": 0.6, "sky_inliers": 0.9, "subject": ("subject", "person")}
_SCAN, _STEP = 8.0, 0.05     # degrees tried on each side of level, and between two tries
_MIN_DEPTH = 0.02            # a column counts when the mask is this share of the height deep there

def _edge(mask: np.ndarray, top: bool) -> tuple[np.ndarray, np.ndarray]:
    """(x, y) of the mask's upper (`top`) or lower edge: one point per column that has one inside the frame."""
    h = mask.shape[0]
    y = mask.argmax(axis=0) if top else h - mask[::-1].argmax(axis=0)      # the first masked row, or one past the last
    ok = (mask.sum(axis=0) >= max(2, _MIN_DEPTH * h)) & ((y >= 2) if top else (y <= h - 2))
    x = np.nonzero(ok)[0]
    return x + 0.5, y[x].astype(np.float64)

def _fit(x: np.ndarray, y: np.ndarray, tol: float) -> tuple[float, int]:
    """(slope in degrees, points on it) of the straight line most points lie on, within `tol` pixels.
    No random numbers: every slope is tried, and the best is refined by least squares over its points."""
    best, on = 0, None
    for a in np.arange(-_SCAN, _SCAN + _STEP / 2, _STEP):
        r = y - math.tan(math.radians(a)) * x
        hit = np.abs(r - np.median(r)) <= tol
        if int(hit.sum()) > best:
            best, on = int(hit.sum()), hit
    if best < 2:
        return 0.0, best
    return math.degrees(math.atan(float(np.polyfit(x[on], y[on], 1)[0]))), best

def horizon(masks: dict[str, np.ndarray], cfg: dict) -> tuple[float, str, float, str]:
    """(angle, line, confidence, why). `angle`: degrees the line slopes, positive when it falls to the right.
    `line`: "waterline", "horizon", or "" when no line is trusted; `why` then says what stood in the way."""
    why = "no water and no sky found"
    for name in LEVEL:
        mask = masks.get(name)
        if mask is None or not mask.any():
            continue
        h, w = mask.shape
        line = "horizon" if name == "sky" else "waterline"
        span, inliers = (cfg["sky_span"], cfg["sky_inliers"]) if name == "sky" else (cfg["water_span"], cfg["water_inliers"])
        x, y = _edge(mask, top=name != "sky")
        if x.size < span * w:
            why = f"the {line} covers too little of the width"
            continue
        angle, n = _fit(x, y, max(1.0, cfg["tolerance"] * max(h, w)))
        if n < span * w or n < inliers * x.size:
            why = f"the {line} is not one straight line (a ridge or a shore, not a horizon)"
        elif abs(angle) > cfg["max_angle"]:
            why = f"the {line} slopes {abs(angle):.1f} degrees, more than the limit of {cfg['max_angle']:g}"
        else:
            return angle, line, n / x.size, ""
    return 0.0, "", 0.0, why

def frame(angle: float, ratio: float | None, hw: tuple[int, int],
          centre: tuple[float, float] | None = None) -> tuple[float, float, float, float]:
    """(cx, cy, w, h) in shares of the photo: the largest upright rectangle of `ratio` (width over height;
    None: the photo's own) inside a photo of `hw` turned by `angle`, its middle on `centre` as far as it can be."""
    H, W = hw
    r = ratio or W / H
    c, s = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    b = min((W / 2) / (r * c + s), (H / 2) / (r * s + c))      # half the height
    a = r * b                                                  # half the width
    free_x, free_y = W / 2 - (a * c + b * s), H / 2 - (a * s + b * c)    # how far the middle may move
    tx, ty = (0.0, 0.0) if centre is None else ((centre[0] - 0.5) * W, (centre[1] - 0.5) * H)
    return (0.5 + min(max(tx, -free_x), free_x) / W, 0.5 + min(max(ty, -free_y), free_y) / H,
            2 * a / W, 2 * b / H)

def decide(masks: dict[str, np.ndarray], hw: tuple[int, int], straighten: bool, aspect: str | None,
           cfg: dict) -> tuple[Geometry | None, list[str]]:
    """(what to do or None, notes for the sidecar). `masks`: name -> bool mask at analysis size.
    `hw`: (height, width) of the photo. `aspect`: "W:H" as RunConfig checked it, or None."""
    notes: list[str] = []
    angle, line, conf = 0.0, "", 0.0
    if straighten:
        found, line, conf, why = horizon(masks, cfg)
        # A rise of one pixel over the width of the masks cannot be told from level.
        level = max(cfg["min_angle"], math.degrees(2.0 / max(m.shape[1] for m in masks.values()))) if masks else 0.0
        if not line:
            notes.append(f"straighten: left alone, {why}")
        elif abs(found) < level:
            notes.append(f"straighten: left alone, the {line} is level (within {level:.2g} degrees)")
            line, conf = "", 0.0
        else:
            angle = found
    ratio = None
    if aspect:
        aw, ah = (int(n) for n in aspect.split(":"))
        ratio = aw / ah
        if not angle and abs(round(hw[0] * ratio) - hw[1]) <= 1:
            notes.append(f"crop: left alone, the photo already has the shape {aspect}")
            ratio = None
    if not angle and ratio is None:
        return None, notes
    # ponytail: the middle of all subject pixels, so two far apart put the frame between them;
    # search for the frame that keeps the most of them if that shows on real photos.
    centre = None
    subjects = [masks[n] for n in cfg["subject"] if n in masks and masks[n].any()] if ratio else []
    if subjects:
        ys, xs = np.nonzero(np.logical_or.reduce(subjects))
        centre = ((xs.mean() + 0.5) / subjects[0].shape[1], (ys.mean() + 0.5) / subjects[0].shape[0])
    cx, cy, w, h = frame(angle, ratio, hw, centre)
    geo = Geometry(angle=round(angle, 3), line=line, confidence=round(conf, 3), aspect=aspect if ratio else "",
                   cx=round(cx, 6), cy=round(cy, 6), w=round(w, 6), h=round(h, 6), kept=round(w * h, 4))
    if angle:
        notes.append(f"straighten: turned {abs(angle):.2f} degrees {'counter-clockwise' if angle > 0 else 'clockwise'} "
                     f"to level the {line} ({conf * 100:.0f} % of it lies on one line)")
    if ratio:
        notes.append(f"crop: to {aspect}, placed {'on the subject' if centre else 'in the middle'}")
    notes.append(f"geometry: {geo.kept * 100:.1f} % of the photo is kept, {(1 - geo.kept) * 100:.1f} % is cut; "
                 "done to the export after the checks, nothing is added")
    return geo, notes

def size(geo: Geometry, hw: tuple[int, int]) -> tuple[int, int]:
    """(height, width) in pixels of the frame `geo` on a picture of `hw`."""
    inset = 2 if geo.angle else 0       # a turned frame stays a pixel inside the picture on every side
    return max(1, round(geo.h * hw[0]) - inset), max(1, round(geo.w * hw[1]) - inset)

def warp(rgb: np.ndarray, geo: Geometry | None) -> np.ndarray:
    """The frame `geo` of the picture `rgb` (float, HxWxC); None returns `rgb` itself. A crop alone is
    a cut on whole pixels; a turn is one bicubic resampling, clipped to 0..1."""
    if geo is None:
        return rgb
    H, W = rgb.shape[:2]
    h, w = size(geo, (H, W))
    cx, cy = geo.cx * W, geo.cy * H
    if not geo.angle:
        x0 = min(max(round(cx - w / 2), 0), W - w)
        y0 = min(max(round(cy - h / 2), 0), H - h)
        return rgb[y0:y0 + h, x0:x0 + w]
    c, s = math.cos(math.radians(geo.angle)), math.sin(math.radians(geo.angle))
    # Pillow's affine: the middle of output pixel (x, y) is read at (a x + b y + c, d x + e y + f).
    coeffs = (c, -s, cx - c * w / 2 + s * h / 2, s, c, cy - s * w / 2 - c * h / 2)
    chans = [np.asarray(PILImage.fromarray(np.ascontiguousarray(rgb[..., i], dtype=np.float32), mode="F")
                        .transform((w, h), PILImage.AFFINE, coeffs, resample=PILImage.BICUBIC), dtype=np.float32)
             for i in range(rgb.shape[2])]
    return np.clip(np.stack(chans, axis=-1), 0.0, 1.0)
```

**How a ridge is told from a horizon:** only one straight line that holds most of an edge is
trusted. Measured on the cached SAM 3 masks of 28 photos (16 panel, 12 holdout):

- 24 lower edges of a sky (ridges, and one field horizon half hidden by a tree): at most 45 % of
  an edge lies on one line. The bar is 90 %, across 60 % of the width.
- 14 upper edges of water: three far waterlines have 94 to 100 % on one line, across as much of
  the width; lakes seen from above, a river and a waterfall at most 86 %, across at most 35 %.
  The bar is 80 %, across half the width. A shore must be that long and straight: from 50 m
  above a lake, a far shore 200 m to 400 m away slopes by 4.8 degrees in a level photo.
- The three are turned, by 1.00, 0.43 and 1.02 degrees (`3D6A2036`, `3D6A2089`, `3D6A2299`):
  enlarged at both ends of the line, each sloped before and is level after.

### 3. From the settings to the export

`contract.py`. After `lut_amount`: `straighten: bool = False` and
`out_aspect: str | None = Field(default=None, max_length=7)`, each with a comment. A record
`Geometry(BaseModel)` above `Result`, in shares of the source photo, each field with a comment:
`angle: float = 0.0` (degrees the levelled line sloped; positive: it fell to the right),
`line: str = ""` ("waterline" or "horizon"; "" = not turned), `confidence: float = 0.0` (share of
the line's points on one straight line), `aspect: str = ""` (the shape asked for; "" = the photo's
own), `cx` and `cy` (0.5: the middle of the kept frame), `w` and `h` (1.0: its width and height),
`kept: float = 1.0` (share of the photo's area that is kept). As the last field of `Result`:
`geometry: Geometry | None = None`. A `field_validator` `_check_out_aspect`, like
`_check_out_subdir`: a falsy value returns None (`--aspect ""` switches a preset's crop off); else
the text must match `re.fullmatch(r"[1-9][0-9]{0,2}:[1-9][0-9]{0,2}", v, re.ASCII)` ("out_aspect
... must be W:H in whole numbers, for example 4:5") and W / H must lie in 0.2 to 5 ("... is outside 1:5 to 5:1").

`pipeline.py`: `geometry` joins the `from . import (...)` list. A field
`geometry_cfg: dict = field(default_factory=dict)`, set in `Pipeline.build` to
`geometry.config(rules) if (cfg.straighten or cfg.out_aspect) else {}`. The method below stands
beside `_with_light_concept` and is the last change to `extra` before the segmenter is called, in
`presegment` and in `process_one`. The block stands in `process_one` where the fence says;
`result.geometry = geo` follows `result.adjustments = adjs`.

```python
    def _with_geometry_concepts(self, extra: list[str], concepts: list[str]) -> list[str]:
        """Straighten reads the water and the sky, a crop is placed on the subject: the segmenter looks for
        them too (F32). They never become regions. The stub's masks are brightness guesses: none is asked for."""
        if not self.geometry_cfg or self.cfg.backend == "stub":
            return extra
        want = (geometry.LEVEL if self.cfg.straighten else ()) + (self.geometry_cfg["subject"] if self.cfg.out_aspect else ())
        return extra + [n for n in want if n not in concepts and n not in extra]
    # ---- and in process_one, directly before "# Shoot-consistency:" ----
        # Geometry (F32): how the finished picture is turned and cut on its way out. Decided here,
        # from the masks; no pixel moves in this function. emit.py and the preview do it, after
        # every check below, so the gates and the stats compare two pictures of the source's shape.
        geo = None
        if self.geometry_cfg:
            stub = cfg.backend == "stub"
            wanted = set(geometry.LEVEL) | set(self.geometry_cfg["subject"])
            geo_masks = {} if stub else {r.name: r.mask for r in raw if r.name in wanted and r.mask.any()
                                         and r.score >= meta_for(r.name, self.meta).min_score}
            geo, geo_notes = geometry.decide(geo_masks, (img.height, img.width), cfg.straighten and not stub,
                                             cfg.out_aspect, self.geometry_cfg)
            if stub and cfg.straighten:
                geo_notes.insert(0, "straighten: not done, the stub backend cannot find water or sky (use --backend sam3)")
            policy_notes += geo_notes
```

- `emit.py`: `write_image` gains a last parameter `geometry=None` and starts with
  `if geometry is not None: rgb = geometry_mod.warp(rgb, geometry)` (import
  `geometry as geometry_mod`), before the `long_edge` resize. `write_outputs` passes
  `geometry=result.geometry`. The EXIF pixel size is the size written, and an export holds no
  embedded preview (F1): nothing of the part that was cut travels in the file.
- `exports.py`: `"aspect": "out_aspect"` in `_KEYS`. `web/service.py`: both settings in `_ALLOWED`.
  `cli.py`: `"straighten"` in the `[run]` list of `_flatten`, `("aspect", "out_aspect")` in its
  `[output]` pairs, both in `overrides`; `--straighten` (`store_true`, `default=None`), `--aspect`.

### 4. Marks, places and the light (F37, F18)

Geometry comes after them. A mark, a named place, a composition move and the light are placed on
the source frame, where the user and the segmenter saw the photo; the finished picture is then
turned and cut as a whole, so each moves with the picture. `mark_name`, the mask caches and
`autoedit adjust` do not change. A mark taken on a turned preview is mapped back by `warp`'s affine.

### 5. The preview and the tools

- `web/service.py`: `_in_frame(alpha, geo)` returns a uint8 overlay as it is for `geo is None`,
  else `(geometry.warp(alpha[..., None].astype(np.float32) / 255.0, geo)[..., 0] * 255.0 + 0.5).astype(np.uint8)`.
  `_mask_previews(resolved, geo=None)` and `_mark_answer(..., geo=None)` pass each alpha through
  it. `preview` keeps the `resolved` it is handed and makes the mask overlays after
  `process_one`, with `geo = result.geometry`; `before` and `after` are `geometry.warp(work.rgb,
  geo)` and `geometry.warp(out_rgb, geo)`, through `_img_b64` as today; `make_variants` does the
  same. For later features (F29's step pictures): every picture of a request goes through it.
- `quality_runner.py::render_safe`, after the grain lines: a set `result.geometry` becomes None,
  with the note "geometry: not done by the safety workflow, which checks the pixels it writes".
- `scripts/contact_sheet.py`: `_render` passes `geometry=res.geometry` to `write_image`; `_facts`
  fits both sides first and resizes the second to the first's size when they differ.

### 6. Config

- `rules.toml`, before `[highlight_hold]`: `[geometry]` with the eight keys of `geometry.DEFAULTS`
  and the same values (`subject` as a list), each with a comment that says what it limits.
- `settings.toml`: `[setting.straighten]`, label "Straighten", group `quality`, help "Turns the
  photo so that a clear waterline or horizon is level, at most 5 degrees. The edges are cut.";
  `[setting.out_aspect]`, label "Crop to shape", group `output`, help "Crops the export to a
  shape such as 4:5 or 1:1, placed on the subject. Empty keeps the photo's own shape."
- `defaults.toml`: `straighten = false` under `[run]`, a commented `# aspect = "4:5"` under
  `[output]`. `exports.toml`: `aspect` in the header's list of keys, `# aspect = "4:5"` in `[social]`.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/geometry.py` | new: Design 2 | T1 |
| `src/autoedit/contract.py` | the two settings, the validator, `Geometry`, `Result.geometry` | T1 |
| `src/autoedit/configs/rules.toml`, `src/autoedit/configs/settings.toml`, `tests/snapshots/settings_schema.json` | `[geometry]`; the two entries; regenerated | T1 |
| `tests/test_geometry.py` | new: tests 1 to 11 (T1), 12 to 17 (T2), 18 to 20 (T3) | T1 to T3 |
| `src/autoedit/pipeline.py` | the field, `build`, `_with_geometry_concepts` and its two calls, the block, `result.geometry` | T2 |
| `src/autoedit/emit.py`, `src/autoedit/exports.py`, `src/autoedit/cli.py`, `src/autoedit/configs/defaults.toml`, `src/autoedit/configs/exports.toml` | Designs 3 and 6 | T2 |
| `src/autoedit/web/service.py` | `_ALLOWED` (T2); Design 5 (T3) | T2, T3 |
| `src/autoedit/quality_runner.py`, `scripts/contact_sheet.py` | Design 5 | T3 |

Not touched: `edit/`, `quality.py`, `instructions.py`, `batch.py`, `adjust.py`, `studio.html`.

## Tasks

One after the other. Other features change the same files, so places are named by their text.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, medium | Design 2 as written; `contract.py`; `[geometry]`; the `settings.toml` entries; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`); tests 1 to 11 | `PYTHONPATH=src python3 -m pytest tests/test_geometry.py tests/test_settings_schema.py tests/test_rules.py tests/test_defaults.py tests/test_profile.py -q` |
| T2 | Builder, Sonnet, high (`pipeline.py`) | Design 3; `defaults.toml` and `exports.toml`; tests 12 to 17 | `PYTHONPATH=src python3 -m pytest tests/test_geometry.py tests/test_pipeline.py tests/test_emit.py tests/test_export_presets.py tests/test_export_metadata.py tests/test_cli.py tests/test_adjust.py tests/test_parallel.py tests/test_marks.py tests/test_grain.py tests/test_lut.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | Design 5; tests 18 to 20 | `PYTHONPATH=src python3 -m pytest tests/test_geometry.py tests/test_web_service.py tests/test_quality_runner.py tests/test_contact_sheet.py tests/test_variants.py tests/test_marks.py tests/test_follow_up.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec (`pipeline.py`: no stage added or moved), the definition of done, both sheets, the privacy check | none |
| Docs | The session, inline | `ARCHITECTURE.md` section 13 and `APP_GUIDE.md`: geometry is done at the export, after every check, before the resize and the grain; the two flags in `README.md`; `CHANGELOG.md`; one line under F37's known limit "Geometry"; the plan row | none |

## Tests

`tests/test_geometry.py`, plain functions. Helpers: `_scene(hw, angle, at=0.4, boat=0.0)` returns
`(rgb, {"sky", "water"})`, a bright blue sky over dark blue water whose waterline crosses the
middle column at `at` of the height and falls to the right by `angle` degrees (`boat`: a share
of the width where a block stands in both masks). `Fake(masks)` is a segmenter that returns the
given masks by name with score 0.9 and lists what it was asked. `_pipe(masks, **settings)` builds
a stub pipeline, then sets `pipe.segmenter = Fake(masks)` and `pipe.cfg` to a copy with
`backend="sam3"`, as `batch._worker_init` does for a render worker.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_a_sloping_line_is_measured` | Frames of 400 by 600 and 600 by 400, slopes -4, -1.5, 0.7, 3: `horizon` is within 0.1 degree, "waterline", confidence above 0.95; the sky alone gives "horizon" within 0.1. A boat on 15 % of the width: still within 0.1, confidence 0.8 to 0.9 |
| 2 | `test_a_sloping_photo_is_levelled_within_a_fifth_of_a_degree` | Slopes -4, 0.7, 3 in both frames: after `warp(rgb, decide(...)[0])` the edge of the picture slopes less than 0.2 degrees; the shape is `size(geo, hw)`; `kept < 1`; a positive slope reads "counter-clockwise" |
| 3 | `test_a_level_photo_is_left_alone` | Slopes 0, 0.2, -0.25: `decide` gives None and the one note "straighten: left alone, the waterline is level (within 0.3 degrees)"; `warp(rgb, None) is rgb`. A 256 px wide mask with one step of a pixel reads 0.3 to 0.4 degrees and is level "within 0.45 degrees" |
| 4 | `test_a_ridge_is_not_a_horizon` | A sky over three peaks: "the horizon is not one straight line ..."; a straight line at 6 degrees: "more than the limit of 5"; at 12: no line; a waterline on 30 % of the width: "covers too little of the width"; an oval lake: no line; no mask: "no water and no sky found" |
| 5 | `test_the_frame_is_the_largest_that_fits` | Three sizes, angles 0, 0.5, -3, 5, ratios None, 0.8, 1, 16/9, three centres: all four corners lie in the photo within 1e-6; the ratio is exact; 0.2 % larger leaves the photo. 3:2 keeps 0.787 at 5 degrees and 0.950 at 1; angle 0 without a ratio is (0.5, 0.5, 1, 1) |
| 6 | `test_a_turn_adds_nothing` | A picture of ones stays ones for five angles and three sizes (no black comes in at an edge); a flat 0.37 stays within 1e-6; noise stays in 0..1 and comes out the same twice; a one-channel array works |
| 7 | `test_a_crop_alone_is_a_plain_cut` | 400 by 600 at "4:5": `array_equal` with `x[:, 140:460]`; `kept` 0.5333; the two notes word for word; 4000 by 6000 gives 4000 by 3200; a 600 by 400 photo at "16:9" gives 225 by 400 |
| 8 | `test_a_crop_keeps_the_subject` | A person blob at 0.2, 0.5 and 0.8 of the width: every pixel of it is in the frame, and the note says "placed on the subject"; at 0.2 the frame starts at the left edge; at 0.4 its middle is at 0.4; two subjects at 0.2 and 0.8: 0.5; an empty mask or a water mask: 0.5 |
| 9 | `test_straighten_and_crop_together` | Slope 3, a person on the left, "1:1": the angle within 0.1, the corners in the photo, the output square within a pixel and level within 0.2; the notes in the order straighten, crop, geometry |
| 10 | `test_the_photo_s_own_shape_is_no_crop` | "3:2" on 400 by 600, 3840 by 5760 and 400 by 601: None, with the note; with a turn the frame keeps "3:2" |
| 11 | `test_settings_and_thresholds` | The defaults are False and None; "" becomes None; `4x5`, `0:5`, `4:0`, `1:9`, `9:1`, `-4:5`, `4:5:6`, Arabic-Indic digits, a leading space, a trailing newline, `1000:999` and `4.5:5` raise; `config({})` is `DEFAULTS`; the packaged block equals `DEFAULTS`; a block with `max_angle = 3` declines a slope of 4 |
| 12 | `test_a_default_run_is_untouched` | `geometry_cfg == {}`; the segmenter was asked for the run's concepts only; `result.geometry is None`; no note starts with straighten, crop or geometry; `write_image` with `geometry=None` writes the bytes of a call without it; the sidecar holds `"geometry": null` |
| 13 | `test_the_pipeline_decides_and_the_export_is_turned` | `_pipe` with slope 3: `out_rgb` has the source's shape and is `array_equal` to the run without the setting; the angle within 0.2, "waterline"; `write_outputs` (with `out_format="png"`) writes a file of `size(geo, hw)` whose edge is level within 0.2; the sidecar holds the record; a dry run decides the same |
| 14 | `test_the_stub_backend_does_not_straighten` | Stub, both tools on: a centred "1:1" frame with angle 0, the stub note, "placed in the middle", and `person` was not asked for; straighten alone gives None |
| 15 | `test_extra_masks_are_asked_for_only_when_needed` | Concepts `["sky"]`: nothing more by default, `water` with straighten, `subject` and `person` with a crop, all three with both; `presegment` asks for the same; the extras are in no region and no `concepts` entry; a mask under its concept's score is not read |
| 16 | `test_presets_flags_and_requests` | A preset with `aspect` gives `out_aspect`; `--straighten --aspect 4:5`; `[run] straighten` and `[output] aspect` in a run file; `--aspect ""` over it gives None; `4x5` raises; `service.make_cfg` takes both keys and refuses "wide" |
| 17 | `test_a_batch_crops_resizes_and_renders_again` | Stub batch, 400 by 300 photos, "1:1" with `out_long_edge = 120`: files of 120 by 120, `kept` 0.75; `adjust.rerender` and a run with two workers write the same bytes; a default run writes 400 by 300 |
| 18 | `test_the_preview_shows_the_export_s_frame` | Stub, "1:1" and one mark: `before` and `after` are 300 by 300, as is every mask and mark overlay; `before` is the middle of the plain preview's (within 2 levels: the dither follows the frame); a variant's `after` is square |
| 19 | `test_the_safety_workflow_does_no_geometry` | `render_safe` with "1:1": the proposal has the source's shape, `geometry` is None, the note |
| 20 | `test_contact_sheet_takes_a_turned_or_cropped_side` | `_facts` of a 600 by 400 and a 400 by 400 picture starts with "diff "; `_render` with "1:1" writes a square JPEG |

## Acceptance checks (the session runs these)

1. Both suites are green: `PYTHONPATH=src python3 tests/run_all.py`, and the same on `.venv`.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Once more into a new
   folder with `--straighten --aspect 4:5`: the exports are 154 by 192 (from 256 by 192), each
   sidecar has `kept` 0.6 and the stub note; a second run, a run with `--workers 2` and
   `autoedit adjust` on one export write the same bytes.
3. Default output is byte-identical to the baseline renders (on the scratch build the smoke corpus
   matched `out-f37/base-stub`). Expected and not pixels: sidecars gain `geometry` and two keys
   under `audit.run_config`, and `config_digest` changes because `rules.toml` gains a block.
4. With SAM 3, one job: `.venv/bin/autoedit run out-f36/panel out-features/F32/panel --backend sam3
   --straighten`. `3D6A2036`, `3D6A2089` and `3D6A2299` are turned by 1.00, 0.43 and 1.02 degrees
   (within 0.05: the cached masks of the same model gave these), about 1.2 s slower each; the
   other 13 exports are byte-identical to the same run without the flag.
5. The same on `testImages/` (10) and `split_before_after_landscapes/before/` (9): open sea, a
   beach, lakes, fields. It is the first trial of a sea horizon under a sky: no cached mask of one
   existed. A sheet for the owner, `out-features/F32/sheet_NN.jpg`, from a session script that is
   not committed: per turned photo the default export beside the straightened one, a thin level
   line across both, the angle, the share kept, and both ends of the line at 100 %. By eye: every
   turned photo is level afterwards, and no photo that was level is turned.
6. Crop, with SAM 3: `--aspect 4:5` on `3D6A1613`, `3D6A1624` and `3D6A1639` (a person in each).
   The sidecar says "placed on the subject" and the person is whole in the export; a second sheet
   shows the centred frame beside it. `aspect = "4:5"` with `long_edge = 1350` gives 1080 by 1350.
7. Tone on the kept frame, for every photo turned in checks 4 and 5:
   `tonemetrics.compare(describe(geometry.warp(source, geo)), describe(geometry.warp(edit, geo)))`
   reports 0 dark made darker and 0 pushed into clipping, as on the three of check 4. The tone
   gate of `CLAUDE.md` renders through the safety workflow and does not see geometry.

## Known limits

- **Upright is not built** (decision 3). Lens corrections stay "Later" in the plan.
- **Few photos are turned.** A line hidden for more than a fifth of its length (a headland, a
  tree in front of a field horizon) is left alone, and mountain photos have no such line.
- **A line can be straight and not level**: a slope that fills the frame under the sky, a long
  shore seen from above. The note names the line that was used.
- **The checks judge the whole photo.** A share of clipped pixels differs on the kept frame:
  by up to 0.006 on a white sky that was partly cut, and by more after a crop.
- **Frame-wide moves follow the whole photo.** A named place ("the left") and the frame vignette
  (0.024 EV at the corners by default) are placed before the cut and are not centred on a crop.
- **A mark is given in shares of the source frame** (Design 4). Studio has no geometry control,
  so its Mark tool never meets a turned preview. The same holds for the boxes of F21's findings.
- **Each photo is judged alone:** frames of one burst can be turned by different amounts. Two
  subjects far apart put a crop between them, and `autoedit adjust` cannot move the frame.
- **Tools that write their own pictures** show the edit without geometry: `scripts/blindtest.py`,
  `autoedit calibrate`, `experiments.py`, the safety workflow. A genre (F30) cannot set straighten.

## Proposed plan row

`| F32 | Geometry: straighten and crop | L | done, off by default (`--straighten`, `--aspect W:H`, `aspect` in an export preset); upright is not built: it needs photos of buildings; the owner's yes on cropping is open |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner, in the row "May AutoEdit straighten and crop (F32)?": the evidence is
  `out-features/F32/`, the change is the row of decision 1. And a new row: "Upright needs 20 or
  more photos of buildings or rooms with leaning walls. Do you have some?"
- Changed against the plan: "Geometry is done to the finished picture at the export, after
  `sharpen` and after every check, not inside `apply`; upright was not built." Why: Design 1 and
  decision 3. Reverse it by: none intended; a feature of its own.

## Result

Built on 2026-11-17 in the unattended run. Status: done, both tools off by default.

- **Built** by three Builders (Sonnet) from this spec. T3 added one thing the spec did not
  foresee: an optional `on_geometry` hook in `process_one`, fired only when a geometry tool is on,
  so the preview's step pictures (F29, built after this spec) are turned and cut like `after`; the
  last step still equals `after` byte for byte.
- **The Checker (Opus, high)** found nothing that had to be fixed and seven notes. Fixed by the
  session, with a test: the `[geometry]` block of `rules.toml` was not checked (`subject =
  "person"` became six one-letter words; misspelled keys and negative values passed). Kept as
  known limits: mask overlays are turned at analysis size, so their 2-pixel inset is a hair smaller
  than the preview's (about 3 preview pixels; Studio sends no geometry); the "already has the shape"
  test allows 1 pixel at the size it is given, so a 1400 px variant can stay uncut where the
  export is cut by 4 pixels; the contact sheet stretches a cropped side to the other's shape, so its
  facts partly measure the cut; the look card (F28) ignores geometry; there is no
  `--no-straighten` to undo `straighten = true` in a run file.
- **Checks:** both suites green after T3 (959 on each interpreter); default output byte-identical
  on the smoke corpus; `--straighten --aspect 4:5` gives 154 by 192 with `kept` 0.6 and the stub
  note, the same bytes again and with `--workers 2`. With SAM 3 (`out-features/F32/`): the panel's
  `3D6A2036`, `3D6A2089` and `3D6A2299` are turned by 1.00, 0.43 and 1.02 degrees, as measured
  for the spec, and the other 13 are byte-identical to the run without the flag; on `testImages/`
  three are turned (Icy River 2.27, Autumn Dock 0.76, Niagara -1.27) and on the split set one
  (Cerro Torre lake 0.71), every other photo byte-identical. The sheets `sheet_01.jpg` and
  `sheet_02.jpg` show each turned photo beside its default with level lines: by eye each is level
  afterwards (Niagara's far horizon sloped about 1 degree before), and no level photo was turned.
  The crop on `3D6A1613`, `3D6A1624` and `3D6A1639` says "placed on the subject" and gives 3072 by
  3840, or 1080 by 1350 with `--long-edge 1350`. Tone on the kept frame of all seven turned photos:
  0 dark made darker, 0 pushed into clipping (`tone-kept-frame.txt`).
- **Not done:** upright (it needs photos of buildings); the owner's yes on cropping.
