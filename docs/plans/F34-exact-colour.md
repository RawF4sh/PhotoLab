# F34. Exact colour and clean backgrounds

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F34. Written in an
unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, the owner's decisions are in the table
below. The design was built once on a scratch copy of the tree, which is deleted again: the reference code is that
code and every number is measured, with the stub backend, on made-up pictures. The repo has no product, food or
artwork photos and SAM 3 was not run, so the plan's check "on a real set" stays open. On the copy both suites passed
on both interpreters: the 877 tests of the copy and the 29 of this spec.

## Goal

Four tools for photos whose colour has to be right and the same across a set (products, food, copies of artwork,
school portraits). All four are off, and default output stays byte-identical.

1. **Neutral pick.** `--neutral X,Y` names a spot that is grey or white, and the white balance makes it neutral: for
   one photo (`autoedit adjust`), for every photo at that spot, or measured once for the whole run (`--neutral-from`).
2. **Clean backdrop.** `--backdrop` evens out a light background and takes it to a set white.
3. **One white balance for a group.** `--consistency-wb`: shoot consistency (F9) evens out the white balance too.
4. **Colour report.** `--colour-report` says how far the frames of a group differ in colour.

Stays out: cutting the product out onto a new background. The backdrop step multiplies by a smooth gain, replaces nothing.

## What was found in the code

1. `edit/ops.py::light` is the white balance: in linear light red times `1 + 0.002 temp`, blue times `1 - 0.002 temp`,
   green times `1 + 0.001 tint`. `OP_BOUNDS` hold both to 100: red against blue up to 1.5 to 1, green within 10 %.
2. `stats.py::compute` solves a neutral reference in exactly those units, inline, clipped to 100 (`wb_temp_correction`,
   `wb_tint_correction`, `wb_confidence`), and again on the finished picture: `post_edit_stats` has the cast that is left.
3. `rules.toml` decides the global `temp` and `tint` only for the scenes `landscape` and `other`, in part and never
   against warmth (-5 to +12); `[people.skin]` has `temp = 4`. `looks.py::shape` scales every value by the intensity.
4. `pipeline.py::process_one`: after `decide` come the prompt nudges, the typed deltas, `exposure_bias`, `wb_bias` and
   the reference tuner. `apply_image` renders regions before the global pass: their colour ops see the unbalanced source.
5. `gates.chroma_guard` holds each pixel's `max - min` to 1.30 times the source's plus 0.02. `process_one` can hand it
   another reference, `gate_src` (F7: the denoised source).
6. `consistency.py`: mode `brightness` clusters the folder by brightness and nudges exposure only. Mode `bursts` moves
   `temp` and `tint` toward the median of the decided **settings** (at most `max_wb = 4`), not toward one colour.
7. `segment/stub.py` and `concepts.toml` know no backdrop. On a made-up product photo the stub calls the upper half of
   a white backdrop `sky` and a warm one `skin` (`temp` +3.2 on 87 % of the frame); its `subject` is an oval in the middle.
8. In a render worker `getattr(pipe.segmenter, "open_vocabulary", False)` is False for a SAM 3 run too
   (`batch.py::_NoModel`); `pipe.cfg.backend` is the real name. A worker has only the masks the parent asked for.
9. `banding.py::banding_score` reads new flat runs in a smooth area as banding. `gates.assess` flags a photo with more
   than 5 % of its pixels above 0.98, or with 0.5 % more of them than its source.
10. `imageio._to_u8` adds one dither value, taken from the pixel's position, to R, G and B: a neutral pixel stays
    neutral in 8 bit, and an unchanged float pixel gives an unchanged byte.
11. `process_one` stores the run settings under `audit.run_config` and `adjust.py::rerender` builds its config from
    them. Blocks of plain values in `rules.toml` are read without a check, also from a profile.
12. Since this spec was begun, three lists name run settings or note prefixes: `genres.py::RUN_KEYS` (F30),
    `web/service.py::TRY_DROPS` and `teach.py::NOTES` (F29, with a test that wants every prefix in `pipeline.py`).

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is anything on by default? | No. `neutral` and `wb` are empty; `backdrop`, `consistency_wb`, `colour_report` are false | `[run]` in `defaults.toml`, or a genre's `[NAME.run]` |
| The pick and the rules | It replaces the white balance of the baseline: the global `temp` and `tint`, and the regions'. What the user types or sets afterwards adds to it | Block A in `pipeline.py` |
| A spot the op cannot make neutral (blown, too dark, beyond 100) | Not used: the rules' white balance stays and a note gives the numbers. A pick is met exactly or not at all | Clamp in `neutral.measure` instead of returning None |
| How large is the spot? | A square, 2 % of the analysis picture's long edge wide (31 px of 1536) | `[neutral] patch` in `rules.toml` |
| The whole set | `--neutral-from FILE` measures once and the run carries plain numbers (`wb`). Without it every photo is measured at the spot | not meant to be reversed: Design 1 |
| The oversaturation gate under a pick | It compares with the source as the pick shows it. Against the plain source it took the correction back: colours up to 15 of 255 off | The lines at `gate_src` |
| How is the backdrop found? | In pixels: bright, near neutral, reaching the frame edge. Not by a word given to SAM 3: "backdrop" names a role, not a look, and nothing here could measure it. What a model found (`keep`) is never backdrop | The thresholds in `[backdrop]`; a model word is a task of its own once product photos exist |
| The evening | A gain: the set white over a surface of degree 4 fitted to the backdrop. No fill | `[backdrop] degree`, `max_gain` |
| The set white | 0.96 (245 of 255): white to the eye, and under the 0.98 the checks count as clipped. A backdrop brighter than it is taken down to it | `[backdrop] white = 1.0`; the review check then flags the photo |
| Where the backdrop step runs | On the finished picture: after the gates and a reference match, before a LUT. The review check reads the result; the banding verdict is made before it | not meant to be reversed: Design 2 |
| Consistency: a mode or a flag? | A flag, for both modes. Without it both are as they were | `consistency_wb` |
| How far may a frame be moved toward its group? | 8 units of `temp` and of `tint` (bursts, by setting: 4). Not measured on a real shoot | `[consistency] max_wb_cast` |
| The report | In `run.json` and the last lines of a CLI run. Delta E: the OKLab distance times 100 from the group's median | `consistency.colour_report` |
| Studio | No control. The API takes the five settings | The Studio rebuild |

## Design

### 1. The neutral pick

`stats.py`: the inline solve of `compute` becomes `wb_correction`, which `compute` calls with the same arithmetic.
`neutral.py` is new (imports `math`, numpy, `OP_BOUNDS`, `linear_to_srgb`, `srgb_to_linear`, `wb_correction`).

```python
def wb_correction(r_m: float, g_m: float, b_m: float, limit: float = 100.0) -> tuple[float, float]:        # stats.py
    """(temp, tint), in the units of `ops.light`, that make a patch with the LINEAR means r_m, g_m, b_m neutral. Each
    is held inside +-limit; tint is solved after the (possibly held) temp. Needs r_m + b_m > 0 and g_m > 0."""
    temp = float(np.clip(500.0 * (b_m - r_m) / (b_m + r_m), -limit, limit))
    rb_target = (r_m * (1.0 + 0.002 * temp) + b_m * (1.0 - 0.002 * temp)) / 2.0
    return temp, float(np.clip(1000.0 * (rb_target - g_m) / g_m, -limit, limit))


def measure(rgb: np.ndarray, xy, t: dict = DEFAULTS) -> tuple[Pick | None, str]:                           # neutral.py
    """(pick, "") for the spot `xy` of the gamma picture `rgb`, or (None, why it is not used). `t`: `rules.block`."""
    y0, y1, x0, x1 = box(rgb.shape[:2], xy, t["patch"])
    spot = rgb[y0:y1, x0:x1].reshape(-1, 3)
    if float(np.percentile(spot.max(axis=1), 99)) >= t["max_level"]:
        return None, "the spot is blown out: pick something grey or white that still has tone"
    if float(spot.mean(axis=0).min()) < t["min_level"]:
        return None, "the spot is too dark to measure: pick something lighter"
    r, g, b = (float(v) for v in srgb_to_linear(spot).astype(np.float64).mean(axis=0))
    temp, tint = wb_correction(r, g, b, limit=math.inf)
    if abs(temp) > OP_BOUNDS["temp"][1] or abs(tint) > OP_BOUNDS["tint"][1]:
        return None, (f"the spot needs temp {temp:+.0f} and tint {tint:+.0f}, the white balance reaches "
                      f"{OP_BOUNDS['temp'][1]:g}: it is not grey, or the colour cast is too strong")
    src = tuple(round(float(v) * 255.0, 1) for v in spot_mean(rgb, xy, t["patch"]))
    return Pick(temp, tint, (float(xy[0]), float(xy[1])), src), ""
```

| Also | What |
|---|---|
| `neutral.DEFAULTS`, `RANGES`, `Pick` | `{"patch": 0.02, "min_level": 0.08, "max_level": 0.98}`; `{"patch": (0.002, 0.25), "min_level": (0.0, 1.0), "max_level": (0.0, 1.0)}`; a frozen dataclass `Pick(temp, tint, xy=None, source=None)` |
| `neutral.box(shape, xy, patch)` | `(y0, y1, x0, x1)` of the square round the spot, cut at the frame. With `h, w = shape`: `r = max(1, round(patch * max(h, w) / 2.0))`, `cx = min(w - 1, int(xy[0] * w))`, `cy = min(h - 1, int(xy[1] * h))`; returns `(max(cy - r, 0), min(cy + r + 1, h), max(cx - r, 0), min(cx + r + 1, w))` |
| `neutral.spot_mean(rgb, xy, patch)` | The box's mean per channel as a gamma value: `linear_to_srgb` of the float64 mean of `srgb_to_linear` |
| `neutral.parse(text)` | `"0.42,0.61"` gives `[0.42, 0.61]`, X from the left and Y from the top. Anything but two numbers from 0 to 1 is a `ValueError` that starts `--neutral needs X,Y as shares of the frame, each from 0 to 1` |
| `neutral.for_run(cfg, path)` | For `--neutral-from`. Loads the photo as a run does (`load_image(path, decode=raw_opts_from(cfg))`, `downscale_long_edge` to `cfg.analysis_long_edge`), measures with the checked `[neutral]` block of `cfg.rules_path`, and returns `(cfg.model_copy(update={"neutral": None, "wb": [round(temp, 3), round(tint, 3)]}), line)`; `line` is `neutral pick in NAME at 0.120, 0.850: white balance temp -55.5, tint -22.5 for every photo of this run`. A `ValueError` `--neutral-from NAME: WHY` (the file name without its folder) for no `neutral`, a missing file, a spot that is not used. Sidecars then hold two numbers and no path |
| `rules.block(rules, name, defaults, ranges)` | `{**defaults, **(rules.get(name) or {})}`; for each key of `ranges` (key to (lowest, highest)) a value that is a bool, not a number or outside the range raises `ValueError(f"rules.toml [{name}] {key} must be a number from {lo:g} to {hi:g} (got {v!r})")`. A run stops before a photo is read (fact 11) |
| `Pipeline.neutral_cfg` | A dict field, built as `rules_mod.block(rules, "neutral", neutral_mod.DEFAULTS, neutral_mod.RANGES)` |

`pipeline.py::process_one` (protected; the stage order does not change). Block A, directly before the comment
`# Free-text look intent`:

```python
        # A neutral pick (F34) replaces the white balance of the baseline: the spot the user says is grey or white is
        # measured on the analysis picture, and the temp and tint that make it neutral become the global ones. Regions
        # lose theirs: one white balance for the photo. What the user types or sets afterwards still adds to it.
        pick, pick_notes = None, []
        if cfg.neutral is not None:
            pick, why = neutral_mod.measure(proxy.rgb, cfg.neutral, self.neutral_cfg)
            if pick is None:
                pick_notes.append(f"neutral pick at {cfg.neutral[0]:.3f}, {cfg.neutral[1]:.3f}: not used, {why}")
        if pick is None and cfg.wb is not None:
            pick = neutral_mod.Pick(float(cfg.wb[0]), float(cfg.wb[1]))
        if pick is not None:
            was = (global_settings.get("temp", 0.0), global_settings.get("tint", 0.0))
            for name, s in region_settings.items():
                gone = [k for k in ("temp", "tint") if s.pop(k, None)]
                if gone:
                    pick_notes.append(f"neutral pick: {' and '.join(gone)} of {name} dropped, the pick sets the white balance")
            global_settings["temp"], global_settings["tint"] = pick.temp, pick.tint
```

| Where in `process_one` | Change |
|---|---|
| Directly before `if wb_bias[0] or wb_bias[1]:` | When `pick is not None` and a bias is set: `wb_bias = (0.0, 0.0)` and the policy note `batch normalize: white balance not moved, the neutral pick sets it` |
| `notes_extra: list[str] = list(policy_notes)` | gains `+ pick_notes` |
| Directly after the two lines that set `gate_src` for `denoise` | `if self.chroma_max is not None and pick is not None: gate_src = ops.light(gate_src if gate_src is not None else img.rgb.astype(np.float32), pick.temp, pick.tint)`, with a comment that says why (the decisions table) |
| Directly before `if residual_banding is not None:`, when `pick is not None` | A dict `neutral_audit` and one note for `notes_extra`; `result.audit["neutral"] = neutral_audit` goes next to the other `result.audit` lines. The dict holds `temp`, `tint` and `rules` (the two values of `was`), three decimals. For `cfg.wb` the note is `neutral pick: white balance temp -20.0, tint +5.0 (the rules: +9.6, -0.4), given for the run`. For a spot the audit gains `x`, `y`, `source` and, when `post_stats is not None and same_shape`, `result` (`spot_mean` of `post_proxy.rgb`, of 255, one decimal) and `off` (its largest minus its smallest value); the note is `neutral pick at 0.120, 0.850: white balance temp -55.5, tint -22.5 (the rules: +0.0, +0.0); the spot was 133, 128, 120 of 255 and is 123, 123, 123`, and when `off > 1` it goes on `, 3.2 from neutral: something after the white balance adds colour there` |

Measured through `process_one` and the export, six casts from 6 % green to 22 % red over blue: the spot's channels end
within 0.52 of 255 of each other, in PNG and in JPEG. With the gate against the plain source a chart of pale colours
came out up to 15 levels from the cast-free render (the gate pulled back 3 % of the frame); against the balanced, 3.7.

### 2. The clean backdrop

`backdrop.py` is new (imports numpy, `par`, `build_alpha` of `edit/mask.py`, `Image`, `downscale_long_edge`, `luma`).
No model is asked: the backdrop is what is bright, near neutral and reaches the frame edge through such pixels, on the
analysis-size copy of the finished picture. A white label inside a product is not backdrop; both backends agree.

```python
DEFAULTS = {"white": 0.96, "min_level": 0.55, "max_chroma": 0.12, "min_coverage": 0.10, "degree": 4,
            "max_gain": 1.6, "feather": 0.004, "keep": ["subject", "person", "skin"]}
RANGES = {"white": (0.5, 1.0), "min_level": (0.0, 1.0), "max_chroma": (0.0, 1.0), "min_coverage": (0.0, 1.0),
          "degree": (1, 6), "max_gain": (1.0, 4.0), "feather": (0.0, 0.05)}
_GRID = 256                   # the backdrop is traced from the frame edge on a grid of at most this many cells a side
_CLIP, _CLIP_FLOOR = 2.5, 2.0 / 255.0   # the fit leaves out what lies further than 2.5 robust deviations, never under 2 levels
_ALPHA_FLOOR, _SAMPLES = 1.0 / 255.0, 60000   # a weight under one 8-bit level counts as none; pixels the fit reads


def fit(rgb: np.ndarray, mask: np.ndarray, degree: int, clip: float = _CLIP) -> np.ndarray | None:
    """A smooth surface through the backdrop: per channel a polynomial of `degree` in x and y (both -1..1 over the frame),
    fitted three times without the pixels further than `clip` robust deviations from it (a shadow, a speck).
    Returns (degree + 1, degree + 1, 3), [i, j] for x**i * y**j, or None when too little is left."""
    h, w = mask.shape
    pw = [(i, j) for i in range(degree + 1) for j in range(degree + 1 - i)]
    step = max(1, int(np.sqrt(mask.sum() / _SAMPLES)))
    ys, xs = (v * step for v in np.nonzero(mask[::step, ::step]))
    if ys.size < 50 * len(pw):
        return None
    u, v = xs / max(w - 1, 1) * 2.0 - 1.0, ys / max(h - 1, 1) * 2.0 - 1.0
    a = np.stack([u ** i * v ** j for i, j in pw], axis=1)
    val = rgb[ys, xs].astype(np.float64)
    keep = np.ones(ys.size, bool)
    for _ in range(3):
        sol = np.linalg.lstsq(a[keep], val[keep], rcond=None)[0]
        res = luma(val - a @ sol)
        dev = 1.4826 * np.median(np.abs(res[keep] - np.median(res[keep])))
        keep = np.abs(res) <= clip * max(dev, _CLIP_FLOOR)
        if keep.sum() < 50 * len(pw):
            return None
    coef = np.zeros((degree + 1, degree + 1, 3))
    for (i, j), row in zip(pw, sol):
        coef[i, j] = row
    return coef


def even(rgb: np.ndarray, t: dict = DEFAULTS, long_edge: int = 1536, keep: np.ndarray | None = None,
         small: np.ndarray | None = None) -> tuple[np.ndarray, dict, np.ndarray | None]:
    """(picture, report, weight). `rgb`: the finished picture, float gamma in 0..1; `t`: `rules.block`. Where the
    weight is 0 the picture comes back bit for bit; when nothing is done it is `rgb` itself, the weight is None and
    `report["why"]` says why. `small`: the analysis-size copy when the caller has one (with the shape of `keep`)."""
    if small is None:
        small = downscale_long_edge(Image(rgb=rgb), long_edge).rgb
    mask = find(small, t, keep)
    cover = float(mask.mean())
    report = {"applied": False, "coverage": round(cover, 4), "white": float(t["white"])}
    coef = fit(small, mask, int(t["degree"])) if cover >= t["min_coverage"] else None
    if coef is None:
        report["why"] = (f"no light backdrop found ({cover * 100:.0f}% of the frame is bright, near neutral and reaches "
                         f"the frame edge; {t['min_coverage'] * 100:.0f}% is needed)" if cover < t["min_coverage"]
                         else "the backdrop is too patchy to fit a surface to")
        return rgb, report, None
    h, w = rgb.shape[:2]
    white, top = np.float32(t["white"]), np.float32(t["max_gain"])
    axis = lambda n: np.arange(n, dtype=np.float32) / np.float32(max(n - 1, 1)) * 2.0 - 1.0     # noqa: E731
    gain_of = lambda s: np.clip(white / np.maximum(s, 1e-3), 1.0 / top, top)                    # noqa: E731
    alpha = build_alpha(mask, (h, w), float(t["feather"]), guide=luma(rgb))
    alpha = np.clip((alpha - _ALPHA_FLOOR) / (1.0 - _ALPHA_FLOOR), 0.0, 1.0).astype(np.float32)
    u = axis(w)[None, :]

    def strip(x, a, ys):      # per pixel: its value, its weight, its place
        gain = gain_of(surface(coef, u, ys[:, :1] / np.float32(max(h - 1, 1)) * 2.0 - 1.0))
        out = x * (1.0 + a[..., None] * (gain - 1.0))
        return np.where(a[..., None] > 0.0, np.clip(out, 0.0, 1.0), x).astype(np.float32)

    out = par.rows(strip, rgb, alpha, np.broadcast_to(np.arange(h, dtype=np.float32)[:, None], (h, w)))
    s = surface(coef, axis(mask.shape[1])[None, :], axis(mask.shape[0])[:, None])[mask]   # for the report, at analysis size
    g = gain_of(s)
    report.update(applied=True, level=[round(float(s.min()), 4), round(float(s.max()), 4)],
                  gain=[round(float(g.min()), 4), round(float(g.max()), 4)],
                  limited=bool(g.max() >= top or g.min() <= 1.0 / top))
    return out, report, alpha
```

| Also in `backdrop.py` | What |
|---|---|
| `from_edge(cand, grid=_GRID)` | The part of the bool map `cand` that reaches the frame edge through `cand`. Square cells of `b = ceil(max(h, w) / grid)` pixels (the map padded with False to whole cells); a cell counts when at least half of it is `cand`. Start from the counting cells on the four edges of the grid and grow through counting cells, 8-connected, until nothing is added (a loop of `seen = grown(seen) & cells`, with `grown` a cell or one of its eight neighbours). Return `cand & grown(seen)`, enlarged by `np.kron` and cut to the frame: the one more ring of cells keeps the rim along a product's outline |
| `find(rgb, t, keep=None)` | `from_edge` of `(luma(rgb) >= t["min_level"]) & (rgb.max(axis=-1) - rgb.min(axis=-1) <= t["max_chroma"])`, less the pixels of `keep` |
| `surface(coef, u, v)` | With `u` of shape (1, w) and `v` of shape (rows, 1): the float32 array (rows, w, 3) of the sum over `i, j` of `coef[i, j] * u**i * v**j`, built per `i` as `(sum over j of coef[i, j] * v[..., None]**j) * u[..., None]**i` |

`pipeline.py::process_one`, directly before the comment `# A LUT file (F25) is laid over the finished picture`.
`Pipeline` gains `backdrop_cfg`, a dict or None, built as `rules_mod.block(rules, "backdrop", backdrop_mod.DEFAULTS,
backdrop_mod.RANGES) if cfg.backdrop else None`.

```python
        # Clean backdrop (F34): the light background of the finished picture is evened out and taken to the set white,
        # after the gates and a reference match, before a LUT. Outside the soft edge of the backdrop no pixel changes.
        # The review check below reads the result; the banding verdict does not (it reads a flat backdrop as a plateau).
        backdrop_report = None
        if do_apply and out_rgb is not None and self.backdrop_cfg is not None:
            small = downscale_long_edge(Image(rgb=out_rgb), cfg.analysis_long_edge)
            if given_proxy:
                small = resize_to(small, *proxy.rgb.shape[:2])
            keep = None
            if cfg.backend != "stub" and small.rgb.shape[:2] == resolved.shape:
                # What a model found is never backdrop. The stub's subject is an oval in the middle of every frame.
                found = [resolved.union[n] for n in self.backdrop_cfg["keep"] if n in resolved.union]
                keep = np.logical_or.reduce(found) if found else None
            out_rgb, backdrop_report, _weight = backdrop_mod.even(out_rgb, self.backdrop_cfg, keep=keep, small=small.rgb)
```

Then one note for `notes_extra`. Applied: `backdrop: 85% of the frame evened out and taken to 245 of 255; it was 183 to
233 (gain 1.05 to 1.34)` (from `coverage`, `white * 255`, `level`, `gain`), plus `; where it needed more than the limit
it stays darker` when `limited`. Else: `backdrop: nothing changed, WHY`. A report that is not None is stored as
`result.audit["backdrop"]`. The two banding conditions are not touched. Measured on made-up photos of 600 by 900:

- **The run with the step is the run without it, with the step over its picture** (`array_equal` to `backdrop.even`
  of the default result). Where the weight is 0 the PNG exports have the same bytes. Inside the product no pixel
  changes; along its outline the soft edge moves product pixels by under half a level (0.24).
- **Even:** a backdrop that spanned 30 levels lies within 1.8 of 245 in every pixel of the export. With one level of
  noise single pixels lie up to 5.8 away (the gain multiplies noise) and 17 by 17 means within 1.0. The shadow keeps
  its depth: 0.77 of its surround before, 0.75 of the white after.
- **Review check:** at 245 its verdict and score are the default run's. With `white = 1.0` it flags the photo
  (`highlight clipping increased +80 %`, score 1.0 to 0.4).
- **Banding gate:** it scores the flat backdrop as new plateaus: 0.21 became 0.24 to 0.31 ("reduce", marked for
  review). So its verdict stays the one made on the edit, before the step. **Cost:** 1.4 s on 24 megapixels.

### 3. One white balance for a group, and the report

`consistency.py`. A frame's cast is what its neutral reference still needs after the decided white balance; a group
brings every frame to its median cast. `process_one` and its `wb_bias` path do not change.

```python
def cast_biases(decided: list[dict], max_wb: float, min_confidence: float) -> list[tuple[float, float]]:
    """Per frame of one group: the (temp, tint) bias that brings its colour cast to the group's median. Frames whose
    reference is weaker than `min_confidence` are not moved and do not vote; fewer than two voters move nothing."""
    left = [(d["wb_temp"] - d["temp"], d["wb_tint"] - d["tint"]) if d["wb_conf"] >= min_confidence else None
            for d in decided]
    votes = [v for v in left if v is not None]
    if len(votes) < 2:
        return [(0.0, 0.0)] * len(decided)
    t_mid, n_mid = _median([v[0] for v in votes]), _median([v[1] for v in votes])
    return [(0.0, 0.0) if v is None else
            (max(-max_wb, min(max_wb, v[0] - t_mid)), max(-max_wb, min(max_wb, v[1] - n_mid))) for v in left]
```

| Also | What |
|---|---|
| `_decided(pipeline, path)` | The `try` block of `_burst_plan` as a function, which `_burst_plan` then calls: one `process_one(path, do_apply=False)`, the dict of today plus `wb_temp`, `wb_tint`, `wb_conf` from `global_stats.wb_temp_correction`, `wb_tint_correction`, `wb_confidence`. None when it raises |
| `_cast_plan(out, pipeline, thr, progress=None)` | For mode `brightness`. The files of `out` that have a group, split by `(group, is_raw(path))`. For each part of two or more: `_decided` every file, `cast_biases(..., thr["max_wb_cast"], thr["wb_min_confidence"])` over those that gave one, the result written into `out[path].temp` and `.tint`. First, when `progress` is given, `progress("white balance: deciding N photos once before the first render")` |
| `kind_biases(..., cast=None)`, `_burst_plan(..., by_cast=False)` | With `cast`, a pair (bound, confidence), the `temp` and `tint` of each kind come from `cast_biases(part, *cast)`; the exposure stays that of `burst_biases`. `_burst_plan` passes `(thr["max_wb_cast"], thr["wb_min_confidence"])` when `by_cast` |
| `_with_rules(pipeline, fn)` | `_bursts_plan` under a new name: the same swap to `RulesDecider`, around any plan that decides |
| `plan`, after the length check | `thr = {**BURST_DEFAULTS, **((getattr(pipeline, "rules", None) or {}).get("consistency", {}) if thr is None else thr)}`. `by_cast = cfg.consistency_wb and cfg.neutral is None and cfg.wb is None`: a pick or a given white balance sets it itself. Mode `bursts`: `return _with_rules(pipeline, lambda: _burst_plan(images, pipeline, thr, progress, by_cast))`. Else `out = _brightness_plan(images)`; with `by_cast`, `_with_rules(pipeline, lambda: _cast_plan(out, pipeline, thr, progress))`; `return out` |
| `BURST_DEFAULTS` | gains `"max_wb_cast": 8.0, "wb_min_confidence": 0.3, "colour_of": ["subject", "person"]` |
| `batch.py::run`, the plan | After the `if` that makes `say` for bursts: `elif consistent and cfg.consistency_wb:` makes one with the prefix `Consistency: ` |
| `subject_colour(rgb, union, names)` | For the first of `names` with a non-empty mask of the picture's shape: `{"concept": name, "lab": [L, a, b]}`, the float64 mean of `linear_to_oklab(srgb_to_linear(rgb[mask]))`, five decimals; else None. `process_one` stores it as `result.audit["subject_colour"]` when `cfg.colour_report and post_stats is not None and same_shape` and it is not None, from `post_proxy.rgb`, `resolved.union` and `[consistency] colour_of` |
| `colour_report(sidecars, min_confidence)` | `{"delta_e": DELTA_E, "groups": {...}}`. Each readable sidecar joins the group `batch_group or "all"`, plus `" raw"` for a RAW file name. A group with two or more measured frames gets `photos`, `delta_e_mean`, `delta_e_max` (100 times the Euclidean distance of each `lab` from the group's per-channel median, two decimals) and `furthest` (that frame's file name). A group with two or more frames whose `post_edit_stats.wb_confidence` reaches `min_confidence` gets `wb_spread`: largest minus smallest `wb_temp_correction`, and the same for tint. `DELTA_E` is `OKLab distance times 100 from the group's median colour; about 2 is the smallest difference seen at a glance` |
| `colour_lines(report)` | Per group one line: `  group g1: 3 photos, subject colour differs by 1.7 on average and 5.0 at most (c.jpg); white balance spread temp 6.0, tint 1.5` |
| `batch.py::run`, the report | Collects every photo's sidecar path directly after `emit.output_paths`, also of a photo a resume skips. With `cfg.colour_report and do_apply`: `manifest["colour_report"] = consistency.colour_report(sidecars, wb_min_confidence of the rules)` before `run.json` is written; after the `Done:` line it prints `Colour report (DELTA_E):` and the lines, or `  no group has two photos with a measured subject` |

Measured on eight made-up frames of one product whose white balance drifts over 13 units: the spread of the cast in
the outputs went from 10.8 to 0.1 (`temp`) and from 4.9 to 0.2 (`tint`). With a drift over 30 units: 25.3 to 8.5.

## Files and config keys

| File | Change | Task |
|---|---|---|
| `src/autoedit/stats.py`, `rules.py`, `neutral.py` (new) | Design 1 | T1 |
| `src/autoedit/backdrop.py` (new) | Design 2 | T2 |
| `src/autoedit/consistency.py`, `batch.py` | Design 3 | T3 |
| `src/autoedit/pipeline.py` | Imports `neutral as neutral_mod` (T1), `backdrop as backdrop_mod` (T2), `consistency` (T3). Block A, the bias guard, the gate reference, the audit (T1); the backdrop block (T2); the subject colour (T3) | T1 to T3 |
| `src/autoedit/contract.py` | `RunConfig`, after `lut_amount`: `neutral` and `wb`, each a list of floats or None with `Field(default=None, min_length=2, max_length=2)` (T1); `backdrop` (T2), `consistency_wb`, `colour_report` (T3) as `bool = False`. Two validators: every value of `neutral` from 0 to 1, of `wb` from -100 to 100 (the comparison also refuses NaN and infinity) | T1 to T3 |
| `src/autoedit/cli.py` | The five keys in the `[run]` list of `_flatten`; `neutral`, `backdrop`, `consistency_wb`, `colour_report` in `overrides`. Flags: `--neutral X,Y` (`type=_spot`: `neutral.parse`, its `ValueError` raised as `argparse.ArgumentTypeError`), `--neutral-from FILE` (`dest="neutral_from"`), `--backdrop`, `--consistency-wb`, `--colour-report` (`store_true`, `default=None`). `_cmd_run`, inside its `try` after the config is built: `if args.neutral_from: cfg, line = neutral.for_run(cfg, args.neutral_from); print(line)`. `adjust` gets `--neutral X,Y` that also takes the word `none`, passed on as `rerender(neutral=...)` | T1 to T3 |
| `src/autoedit/adjust.py` | `rerender(..., neutral=None)`: after `stored` is copied, `if neutral is not None: stored["neutral"] = None if neutral == "none" else list(neutral)`. The pick is then this photo's own run setting and is replayed | T1 |
| `src/autoedit/web/service.py` | The five names join `_ALLOWED`; no path comes in, so nothing is checked against the folders. `TRY_DROPS` gains `neutral`, `wb` (T1), `backdrop` (T2) | T1 to T3 |
| `src/autoedit/teach.py`, `genres.py` | `NOTES["finish"]` gains `"backdrop:"` (T2); `RUN_KEYS` gains `backdrop` (T2), `consistency_wb`, `colour_report` (T3) | T2, T3 |
| `src/autoedit/configs/rules.toml` | `[neutral]` and `[backdrop]` with the values of the two `DEFAULTS` and a comment each (for `white`: 0.96 is 245 of 255, under the 0.98 the checks count as clipped; 1.0 is pure white and the review check then reports the backdrop as clipped); the three new keys in `[consistency]` | T1 to T3 |
| `src/autoedit/configs/defaults.toml`, `settings.toml`, `tests/snapshots/settings_schema.json` | `[run]`: `consistency_wb = false`, `colour_report = false`, `backdrop = false`, commented `# neutral = [0.5, 0.5]` and `# wb = [0.0, 0.0]`. Words (help of at most 140 characters): `neutral` "Neutral spot", `wb` "White balance" (`advanced = true`), `backdrop` "Clean backdrop" after `denoise` in group `quality`; `consistency_wb` "Same white balance", `colour_report` "Colour report" after `consistency` in group `batch`. The snapshot regenerated | T1 to T3 |
| `tests/test_exact_colour.py` | new: tests 1 to 13 (T1), 14 to 21 (T2), 22 to 29 (T3) | T1 to T3 |

Relied on: F9 (`Bias`, `plan`, the `wb_bias` path), F7 (`gate_src`), F24's block C and F25's LUT block (the backdrop
block sits between them), F3 (`audit.run_config` is replayed). Not touched: `edit/ops.py`, `edit/apply.py`, the gates,
`quality.toml`, `segment/`, `concepts.toml`, `instructions.*`, `looks.*`. No new op, no new dependency. `pipeline.py`,
`contract.py`, `cli.py`, `service.py` and the config files are edited by every feature: F34 is built when no other is in them.

## Tasks

One after the other: they share files. `.venv` has no pytest; there the proving command is `tests/run_all.py`. The
snapshot: `AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high (`pipeline.py`) | Design 1; its rows of the files table; tests 1 to 13 | `PYTHONPATH=src python3 -m pytest tests/test_exact_colour.py tests/test_wb_stats.py tests/test_rules.py tests/test_pipeline.py tests/test_adjust.py tests/test_cli.py tests/test_web_service.py tests/test_teach.py tests/test_settings_schema.py tests/test_consistency.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T2 | Builder, Sonnet, high (`pipeline.py`) | Design 2; its rows; tests 14 to 21 | `PYTHONPATH=src python3 -m pytest tests/test_exact_colour.py tests/test_pipeline.py tests/test_lut.py tests/test_bw.py tests/test_parallel.py tests/test_teach.py tests/test_genres.py tests/test_settings_schema.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | Design 3; its rows; tests 22 to 29 | `PYTHONPATH=src python3 -m pytest tests/test_exact_colour.py tests/test_consistency.py tests/test_grouping.py tests/test_jobs.py tests/test_parallel.py tests/test_genres.py tests/test_settings_schema.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec (`pipeline.py` is protected). With care: nothing changes with the five settings off; the place of block A and of the backdrop block; the gate reference; the bytes outside the grown mask | none |
| Docs | The session, inline | `README.md` (one example per flag), `APP_GUIDE.md`, `ARCHITECTURE.md` section 8 (`... gates, reference match, (backdrop), (lut)`), `CHANGELOG.md`, the plan row | none |

## Tests

`tests/test_exact_colour.py`, without `pytest.mark`. The file makes its pictures. `_product(h=600, w=900, cast=(1, 1,
1), seed=0, noise=0.004, tilt=0.10, bowl=0.25, level=0.90, gain=1.0, card=True)`, with `u`, `v` the pixel position
from 0 to 1 and `a(x) = abs(x - 0.5)`: a backdrop `level * (1 - bowl * ((u - .5)**2 + (v - .5)**2) - tilt * (v - .5) -
0.06 * (u - .5))`; a box where `a(u) < 0.16` and `a(v) < 0.22`, red `(0.55, 0.20, 0.18)`, green `(0.20, 0.42, 0.24)`
above `v = 0.42`, blue `(0.18, 0.25, 0.55)` below `v = 0.60`; a label of 0.93 where `a(u) < 0.07` and `a(v) < 0.05`;
outside the box everything times `1 - 0.25 * exp(-(((u - .5) / 0.20)**2 + ((v - .75) / 0.035)**2))` (a shadow); a grey
card of 0.50 within 0.04 of `u = 0.12` and 0.05 of `v = 0.85` (`CARD`); then the linear values times `cast` and `gain`,
then seeded Gaussian noise. "Clear zone": `v < 0.18`, or `a(u) > 0.40` and `v < 0.70`. Casts: warm `(1.10, 1, 0.88)`,
cool `(0.90, 1, 1.12)`, green `(0.97, 1.06, 0.97)`, magenta `(1.03, 0.94, 1.03)`, strong `(1.22, 1, 0.84)`. Batch
tests use frames of 320 by 480 or 400 by 600, PNG exports and the stub. Levels are of 255.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_wb_correction_solves_the_op` | For four linear patches inside the bounds, `ops.light` with the result gives channels within 1e-4 of their mean. `(1.0, 0.5, 0.1)` gives `(-100, -80)`; with `limit=math.inf`, `(-409.09, -636.36)`; a grey gives `(0, 0)` |
| 2 | `test_parse_and_box` | `"0.42,0.61"` and `" 0 , 1 "` parse; `"0.4"`, `"a,b"`, `"1.2,0"`, `"nan,0.5"`, three numbers, a negative and `""` raise. `box((1000, 1500), (0.5, 0.5), 0.02) == (485, 516, 735, 766)`; at `(0, 0)` it is `(0, 16, 0, 16)`; at `(1, 1)` it ends at the frame; on 40 by 60 it is 3 by 3 |
| 3 | `test_measure_refuses_what_it_cannot_use` | The card of a photo without cast or noise: no reason, `temp` and `tint` under 0.5, `source == (127.5, 127.5, 127.5)`. A frame of 0.99: "blown"; of 0.03: "too dark"; of `(0.7, 0.2, 0.2)`: "not grey" and `temp -`; a linear cast of `(1.6, 1, 0.6)`: None |
| 4 | `test_picked_spot_is_neutral_through_apply_ops` | Six casts: after `apply_ops` with the pick the card's channel means differ by under 0.1; with `exposure`, `shadows`, `contrast`, `curve`, `vibrance` and `sharpen` in the bundle too, under 0.25 |
| 5 | `test_picked_spot_is_neutral_in_the_export` | Five casts through `process_one` and `emit.write_outputs`, PNG and JPEG: the card's channel means in the decoded export differ by under 1.0 (measured 0.52 at most), the source's by over 10. `audit.neutral` has `x`, `y`, a `source` and a `result` of three values, `off <= 1`; the global `temp` and `tint` are its values; one note starts `neutral pick at 0.120, 0.850: white balance temp ` |
| 6 | `test_pick_replaces_the_white_balance_of_the_rules_and_the_regions` | A cool photo: the rules give `temp` above 5; with the pick the global values are `measure`'s, `audit.neutral.rules` are the rules', every other global setting is the same. A warm photo (the stub's `skin` has `temp`): no region has `temp` or `tint`, and the note `neutral pick: temp of skin dropped, the pick sets the white balance`. With `edit="make it warmer"` the `temp` is above the pick's |
| 7 | `test_a_spot_that_cannot_be_used_changes_nothing` | `neutral=[0.5, 0.5]` (the label): the pixels and settings of the run without, no `audit.neutral`, one note that starts `neutral pick at 0.500, 0.500: not used, ` |
| 8 | `test_wb_given_for_the_run` | `wb=[-20, 5]` on two photos: exactly those values, the audit `{temp, tint, rules}`, a note that ends `given for the run`. With a usable `neutral` too, the spot wins; with one that is not used, `wb` |
| 9 | `test_the_oversaturation_gate_does_not_take_the_pick_back` | A chart of 800 by 1200 (`RandomState(3)`): a ground of 0.80, 30 pale patches of `0.5 + uniform(-0.07, 0.07)` per channel, 30 strong ones of `uniform(0.1, 0.9)`, a grey card of 0.50, noise 0.003. Under the warm, the cool and the strong cast, picked on the card (`concepts=[]`, `compose=False`, `sharpen=False`, `palette="off"`): no note starts `oversaturation gate`, and the picture lies within 5 levels of `apply_ops` with the same settings, less `temp` and `tint`, on the cast-free chart (measured 3.7) |
| 10 | `test_the_pick_is_off_by_default` | `RunConfig().neutral` and `.wb` are None. A default sidecar: no `neutral` in `audit`, no note that starts `neutral pick`, `run_config.neutral` and `.wb` are null. `neutral` of `[1.2, 0.5]`, of one or three values, with NaN, and `wb` of 150, of one value, with infinity raise `ValueError` |
| 11 | `test_cli_api_and_adjust_carry_the_pick` | The flag's value, `[run] neutral` and `wb` of a `--config` file, and `service.make_cfg` reach `RunConfig`; `make_cfg({"neutral": [2, 0]})` raises; `--neutral 2,0` ends in `SystemExit`. `adjust.rerender(img, neutral=CARD)` changes the export and stores `run_config.neutral`; a plain re-render gives the same bytes; `neutral="none"` gives the bytes of the batch; `autoedit adjust IMG --neutral 0.12,0.85` returns 0 |
| 12 | `test_neutral_from_measures_once` | `for_run`: `neutral` is None, `wb` is `measure`'s result on the card frame, the line starts `neutral pick in a_card.png at 0.120, 0.850`. `autoedit run ... --neutral 0.12,0.85 --neutral-from CARD` returns 0 and prints it; every sidecar has that `wb` under `run_config`, the same global `temp` and `tint`, `run_inputs` with its two keys, and no folder name; the card is neutral within 1 in its export. Without `--neutral`, with a missing file, with a spot that is not used: 2, and no output folder |
| 13 | `test_the_consistency_bias_gives_way_to_the_pick` | `process_one(wb_bias=(3, -2))` under a pick: `wb_bias == [0, 0]`, the pick's `temp`, the note |
| 14 | `test_from_edge` | 120 by 180, grid 60: an area on the left edge, an island and a speck give only the first; an empty map gives an empty one; a frame with a hole that holds an island: the island is out and the row along the hole is in. The default grid gives the same |
| 15 | `test_find_needs_light_neutral_and_the_edge` | A noise-free photo: over 99.9 % of the clear zone, no pixel of the box (label included), not the card; coverage 0.80 to 0.90. Rows given as `keep` are out. `level=0.5`, and a cast of `(1.3, 1, 0.7)`: under 1 % |
| 16 | `test_fit_follows_the_surface_not_the_shadow` | The surface is within 1 level of the picture in the clear zone; along `v = 0.75` under the box the picture is below 0.9 of it, and it is within 2 levels of the backdrop's formula. A mask of 25 pixels: None |
| 17 | `test_backdrop_is_even_and_white` | Noise-free: `applied`, `white == 0.96`, not `limited`; the clear zone with weight 1 spanned over 30 levels and every pixel of `_to_u8(out)` there is within 2 of 244.8; the shadow's ratio moves by under 0.03. With noise: box means of 17 by 17 within 2. A cast of `(1.06, 1, 0.93)`: channel means over 8 apart before, every pixel within 2 after |
| 18 | `test_outside_the_grown_mask_nothing_changes` | `out[weight == 0]` is `array_equal` to the input; the box less its outline (`a(u) < 0.15`, `a(v) < 0.21`) has weight 0 on over 97 % and moves by under 0.5. `process_one(backdrop=True)` is `array_equal` to `backdrop.even` of the default result; where the weight is 0 (10 to 20 % of the frame) the two PNG exports are equal; the settings and the masks are the default run's |
| 19 | `test_backdrop_is_bounded` | `tilt=0.25, bowl=0.45`: `limited`, largest gain 1.6, no pixel changed by more than 1.6 or less than 1 / 1.6, the note ends `it stays darker`. `level=1.2`, clipped: smallest gain under 1. A made-up landscape (`(0.45, 0.62, 0.85)` over `(0.22, 0.38, 0.16)`): `out is rgb`, weight None, `why` starts `no light backdrop found`; through `process_one` the pixels of the default run and a note that starts `backdrop: nothing changed, ` |
| 20 | `test_the_review_check_reads_the_backdrop` | `RunConfig().backdrop` is False and a default sidecar has no `audit.backdrop`. With it: `post_edit_stats.mean_lum` rises by over 0.05; `needs_review`, `quality_score` and the banding notes are the default run's; one note holds `% of the frame evened out and taken to 245 of 255; it was `; `audit.backdrop` has exactly `applied, coverage, white, level, gain, limited`. With `white = 1.0` in a copy of `rules.toml`: `needs_review`, the reason holds `highlight clipping`, a note holds `taken to 255 of 255` |
| 21 | `test_backdrop_strips_workers_keep_and_thresholds` | 700 by 900 with four threads and `par.MIN_PIXELS = 0`: picture and weight equal one call. A batch with `backdrop` and `neutral`: `workers=2` writes the bytes of one worker, `adjust.rerender` the same, `autoedit run --backdrop --neutral 0.12,0.85` the same; `service.preview` with both has both audits and another "after". With `pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})` over the stub's masks (what a render worker has), the inside of the subject oval equals the default run and the coverage is lower by over 0.1. `white = 245`, `degree = 40`, `max_gain = 0`, `feather = true`: `Pipeline.build` with `backdrop` raises `[backdrop] KEY must be a number from`, and builds without it; `[neutral] patch = 2` raises; the CLI returns 2 and writes no export |
| 22 | `test_cast_biases` | Casts `(-6, 2), (0, 0), (7, 1)`, settings 0: `(-6, 1), (0, -1), (7, 0)`. With settings `(-2, 1)` and `(3, 0)` on the outer two: `(-4, 0), (0, -1), (4, 0)`. Casts of 30: held to 8. A frame of confidence 0.1: `(0, 0)` and no vote; one voter: all zero; equal casts: all zero. `kind_biases` without `cast` gives zeros here, with `(8, 0.3)` the first result |
| 23 | `test_wb_spread_is_at_least_halved` | Eight frames with the drift `(-6, 2), (-3, -2), (0, 0), (2, 3), (4, -3), (7, 1), (-5, 0), (5, 2)` (a drift `(t, n)` is the linear cast `(1 + 0.002 t, 1 + 0.001 n, 1 - 0.002 t)`), gain within 3 %. The spread of `post_edit_stats.wb_temp_correction`, and of `wb_tint_correction`, over the sidecars is above 4 in a default batch and at most half of that with `consistency_wb` (measured 10.8 to 0.1, 4.9 to 0.2). Every `wb_bias` is within 8, at least six are not zero, a note holds `batch normalize: white balance`; the default batch has only zeros. `adjust.rerender` and `workers=2` give the same bytes |
| 24 | `test_bursts_mode_evens_the_measured_cast` | Four such JPEG frames 3 s apart, mode `bursts`: all in `b1`; with the flag the `temp` spread is at most half of the run without |
| 25 | `test_the_plan_leaves_the_white_balance_to_a_pick_and_never_calls_the_llm` | A decider that raises is not called by `plan` and is put back; biases are found. With `neutral` or `wb` in the config and `pipe.process_one = None`: every `temp` and `tint` is 0. Without the flag, with no pipeline: zeros |
| 26 | `test_colour_report` | Hand-written sidecars. Three frames of `g1` with the `lab` `[.50, .10, .05]` twice and `[.53, .10, .09]`, casts `(2, 1), (5, 1.5), (-1, 0)`: `{"photos": 3, "delta_e_mean": 1.67, "delta_e_max": 5.0, "furthest": "c.jpg", "wb_spread": [6.0, 1.5]}`. A frame of confidence 0.1 does not count for the spread; a lone RAW file and a group of one are left out; two frames without a group are `all`; a missing and a broken file are skipped. The line of `g1`, word for word |
| 27 | `test_subject_colour` | A mask on the red of the box: `concept == "subject"`, `lab` within 1e-4 of `linear_to_oklab` of that red; the first name that has a mask wins; no such region, or a mask of another shape: None |
| 28 | `test_the_run_writes_the_colour_report` | A batch with `colour_report`: `run.json` holds groups whose `photos` add up to 4, each with `wb_spread`; the output holds `Colour report (OKLab distance` and `subject colour differs by`; a sidecar has `audit.subject_colour`. After one sidecar is deleted a resumed run reports the same groups. A default batch: no key, no line, no `subject_colour`, the same export bytes. A dry run: no key |
| 29 | `test_consistency_flags` | `--consistency-wb`, `--colour-report`, `--backdrop` and their `[run]` keys reach `RunConfig`; all are false without. `[consistency]` of the rules holds `max_wb_cast = 8`, `wb_min_confidence = 0.3`, `colour_of`, and still `max_wb = 4` |

No existing test changes; the settings snapshot gains five settings. Default pixels: tests 7, 19 and 28 hold that
settings which do nothing write the default run's pixels; that the default itself did not move is acceptance check 3.

## Acceptance checks (the session runs these)

1. `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` are green.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Once more into a new folder with
   `--backdrop --consistency-wb --colour-report`: it completes, and each sidecar has a note that starts `backdrop:`.
3. Default output is byte-identical to the baseline renders (maximum difference 0). Expected and not pixels: sidecars
   gain five keys under `audit.run_config`, and `config_digest` changes because `rules.toml` gains two blocks.
4. The tone gate of `CLAUDE.md` on `curated-dolomites-summer-2026/selected_raw`: 0 dark to darker and 0 pushed into
   clipping by default, with `--set backdrop=true` and with `--set 'wb=[20,-10]'` (measured on 12 photos: 0 and 0 in
   all three). Compare `safety_fallback` per photo with the default run: with `backdrop` the step changed 4 of the 12
   (snow, cloud) and the safety workflow pulled 3 of them back (1.0 to 0.5, 0.25, 0.25).
5. The made-up set, which stands in for the real one. A script of the session, not committed, writes twelve frames of
   `_product` at 2400 by 3600, with a drift in white balance and brightness, into `out-features/F34/in/`; the first
   has the grey card. Three runs: default; `--neutral 0.12,0.85 --neutral-from in/f00.png --backdrop --colour-report`;
   `--consistency-wb --backdrop --colour-report`. Checked: the card is neutral within 1 in the second run's `f00` and
   all its sidecars have the same `wb`; the clear zone's 17 by 17 means lie within 2 of 245 in runs two and three; the
   spread of `wb_temp_correction` in run three is at most half of run one's; a second run and `--workers 2` write the
   same bytes. `out-features/F34/sheet.jpg` shows four frames in the three runs, for the owner.
6. Time on a 24 megapixel frame, stub, on a busy machine: 10.4 s by default, 10.7 s with a pick, 11.9 s with the backdrop.
7. Through `service.preview` with the stub: both show in `result.audit` and in the "after" picture (Studio has no control).
8. The Checker's review.
9. Open, and only possible with the owner's photos: a real set of at least 20 frames (the plan's "the spread in white
   balance is at least halved"), a real backdrop, and SAM 3's masks as `keep`.

## Known limits

- The pick reaches what `ops.light` reaches: red against blue up to 1.5 to 1, green within 10 %. A stronger cast
  (tungsten light shot as daylight) is refused with its numbers. The op does not hold brightness: a pick against a
  10 % warm cast makes the photo 2 % darker in linear light.
- A pale colour inside the bounds is taken for a cast: nothing can tell beige from a warm grey. Without
  `--neutral-from` every photo of a run is measured at the same spot. The spot is neutral at the white balance step: a
  look's split-tone, a palette, a reference match, a LUT, a region's colour edit or the highlight hold (it raises
  near-white pixels back toward the source, cast included) can move it again, and the note then gives the distance.
- The backdrop is a rule in pixels. A white or light grey product that touches it without an outline is taken for
  backdrop unless a model found it. A grey or coloured backdrop is none. Snow and an overcast sky are one.
- A surface of degree 4 removes a slope, a vignette and two lights (0.5 to 0.8 levels left) and most of one soft box
  (2.6 left). A backdrop that is partly blown out stays uneven by 3 to 5 levels where the blown part ends. A shadow
  darker than `min_level` is not lifted with the backdrop round it and comes out deeper.
- With `white = 1.0` the review check flags every photo, as it does today with any photo that is more than 5 % white.
  With a LUT on, the banding verdict is made after both steps and reads the flat backdrop as a plateau.
- A JPEG export differs outside the grown mask in 2 % of those pixels, by up to 4 levels, all in 8 by 8 blocks that
  hold a pixel of the mask. PNG, TIFF and the preview are exact.
- A group is a brightness cluster or a burst, not "one product". The cast is read from bright neutral pixels, so a
  frame filled by a beige thing votes wrongly, by at most 8 units. The plan decides every photo of a group once more
  before the first render, and with the rules also when the run uses the LLM decider.
- The report is written by CLI runs only. With the stub the "subject" is the oval in the middle of the frame.
- The safety workflow (`scripts/tone_report.py`) judges all of this as part of the edit and blends a picture it refuses
  toward the source: on the made-up set it pulled back 3 of 8 photos by default, 3 with a pick, 6 with the backdrop.

## Proposed plan row

`| F34 | Exact colour and clean backgrounds | M | done, off by default (`--neutral X,Y`, `--neutral-from FILE`, `--backdrop`, `--consistency-wb`, `--colour-report`); checked on made-up pictures only, a real set is open |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Is 245 the right white for a backdrop, or pure white, which the review check flags?" Evidence:
  `out-features/F34/sheet.jpg`. The change: `[backdrop] white` in `rules.toml`.
- For the owner: "The backdrop is found in pixels, not by SAM 3, and a neutral pick the white balance cannot meet is
  refused, not half applied. Right?" Evidence: Design 1 and 2. Only the owner can supply a real set (20 frames).
- Changed against the plan: "With a neutral pick the oversaturation gate compares with the white-balanced source."
  Why: against the plain source it took the correction back on every colour the cast had made grey (up to 15 of
  255). Reverse it by: the lines at `gate_src`.

## Result

Built on 2026-11-17 in the unattended run. Status: done, everything off by default.

- **Built** by three Builders (Sonnet) from this spec. Test deviations, checked by the Checker: test 5's
  precondition is "off by more than 3" (green and magenta casts are 5.3 off, not 10); test 7 clips the
  label with `gain=1.3` (at 0.93 it is a usable white); test 21 erodes the kept oval by 25 px.
- **The Checker (Opus, high)** found nothing that had to be fixed and nine notes. Fixed by the session,
  with a test: the backdrop's soft edge reached up to 6 px into what a model found (12 levels one pixel
  in); now the kept mask is cut out of the weight, so those pixels stay exactly as they are.
  `[neutral] min_level = 0` could divide by zero (the floor is now 0.01). `adjust --neutral none` also
  drops numbers taken with `--neutral-from`. `rules.block` refuses a list of words given as a string,
  a fractional `degree`, and a block that is not a table; `[consistency] colour_of` is checked. Kept as
  known limits: with a reference match or after banding remediation the final banding check reads the
  evened backdrop and can mark a photo for review (the spec named only the LUT case); bools and numeric
  strings pass for `wb` and `neutral` (pydantic's lax mode, as everywhere in `RunConfig`); a bad
  `[neutral]` block stops every run, a bad `[backdrop]` only runs that use it; the colour report can
  count a stale sidecar of a photo that failed this time.
- **Checks:** both suites green (1020 on each interpreter, before the fixes); default output
  byte-identical; the smoke run with `--backdrop --consistency-wb --colour-report` has a `backdrop:` note
  in each sidecar; `service.preview` shows `neutral` and `backdrop` in `result.audit`. The made-up set
  (12 frames, 2400 by 3600, `out-features/F34/`): with the pick the card is neutral within 0.12 of 255
  and every sidecar has the same `wb`; the clear zone is 244.3 to 244.8 in runs two and three; the
  spread of the post-edit white balance is 5.8 with `--consistency-wb` against 43.1 by default; a second
  run and `--workers 2` write the same bytes; `sheet.jpg` shows four frames in the three runs (the faint
  oval in one frame is the stub's made-up centre mask, kept out of the backdrop). Times on that set:
  41 s by default, 51 s with pick and backdrop (12 frames). Tone gate on 110 RAW files: 0 and 0 by
  default, with `backdrop=true` and with `wb=[20,-10]`. But on these mountain photos the backdrop takes
  snow and cloud for a backdrop, and the safety workflow pulls back 51 photos against 11 by default; a
  white balance of `[20,-10]` pulls back 13. The backdrop is a tool for product photos.
- **Open:** a real set of 20 frames, a real backdrop and SAM 3's masks as `keep` (the owner's).
