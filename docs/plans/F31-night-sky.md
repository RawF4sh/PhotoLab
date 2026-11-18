# F31. Night sky tools

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F31;
kind 14 in [GENRES.md](../GENRES.md). Written in an unattended run
([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked; the owner's decisions are in the
table below. Built after [F30](F30-genre-packs.md) (the `astro` pack is a genre pack) and
[F34](F34-exact-colour.md) (`rules.block`). Stacking and star trails are F33's.

The design was built once on a scratch copy of the tree, which is deleted again: the reference code
below is that code, and every number is measured, on made-up skies and on six real night frames
(fact 1). SAM 3 was not run; where a real frame needed a sky mask, a hand-drawn polygon stood in.
On the copy both suites passed, pytest on the system Python and `tests/run_all.py` on `.venv`: 970
tests, the 959 of today and the 11 of this spec.

## Goal

A run setting `night_sky`, off, and the `astro` pack that sets it. With it a night frame (dark,
shot at a high ISO) keeps its darkness instead of being lifted to grey, a town's glow comes off the
sky (`sky_gradient`), stuck pixels are repaired (`hot_pixels`), and noise reduction and sharpening
leave the stars alone (`denoise_stars`, `sharpen_stars`). Every other frame, and every run without
the setting, renders as today, byte for byte.

## What was found in the code

1. Night photos exist: `curated-switzerland-summer-2026/selected` (not committed) holds six of one
   night, 15 or 20 s at ISO 6400 to 12800, the Milky Way over a mountain and a lit valley: JPEGs
   `0370_dark__3D6A2925`, `0540_dark__3D6A2932`, `0504_dark__3D6A2939`, `0757_dark__3D6A2944`; RAW
   `0115_dark__3D6A2923`, `0542_dark__3D6A2941` (both decode magenta). Not 20; the Dolomites, none.
2. Today (stub, defaults) they are lifted: exposure 0.8, `shadows_ev` 0.37 to 1.2, `shadows` 24 to
   36 with `blacks` pinning black, `whites` 10; 1.3 to 2.5 EV over the source in the tone report;
   0370 is pulled back by the safety workflow (0.75) and flagged.
3. `scene.classify` has an `override` nothing passes (F30). `rules.apply` reads `rules[scene]`, so an
   opt-in set can add a scene: `[opt_in.NAME.night.global]` makes `rules["night"]` only when NAME is
   on; `_OPT_IN_RULES` in `pipeline.py` switches a set on by its run setting (`levels`, `denoise`).
4. `apply_ops` gets no mask: region settings are rendered on the whole frame and blended by alpha,
   before the global pass; the gates compare with `full_rgb`; `lut` and `grain` are values it does
   not render. A bundle may be cut between any two steps of `ops.STEPS` (`test_parallel.py`, the
   guard in `apply.py`, F29's `global_steps`): a value that changes an op must sit in its step.
5. `segment/stub.py::_sky` wants a bright, bluish upper part: it finds no sky in any of the six.
6. The tone report renders through `quality_runner.render_safe`, whose pixel audit calls a
   foreground or bottom-third median more than 0.01 below a source under 0.30 `dark_made_darker`
   and blends the edit back. A look's `preserve_exposure` reaches only the review gate. No packaged
   look sets it; `test_defaults.py::test_no_packaged_look_sets_the_flag` guards that (F8).
7. `looks.shape` multiplies every decided value by the intensity. EXIF gives `iso` (0 without), no
   exposure time. `test_lessons.py` wants an `[op.NAME]` per op; `teach.py` shows the region pass as
   one picture, "regions", with the notes its `NOTES` prefixes select. Golden lists of packs and
   looks: `test_genres.py`, `test_bw.py`, `test_look_files.py`, `test_defaults.py`; of `STEPS`:
   `test_denoise.py`, `test_grain.py`, `test_lut.py`, `test_bw.py`, `test_levels.py`, `test_parallel.py`.

## Measured

On the scratch build, stub backend unless named.

1. **What a night frame is.** Median luma at most 0.25 and ISO at least 1600 picks exactly the six
   of 1130 corpus files. The other eight at ISO 1600 or more are bright (medians 0.53 to 1.0). At
   ISO 640 to 1599, 49 daytime frames (44 at ISO 800) have a median of 0.25 or less.
2. **The ops on a made-up sky** (400 x 600; background 0.10 plus an orange glow of 20, 13 and 5
   levels per channel; 60 stars of 2.5 px, heights 0.05 to 0.6). `sky_gradient` at 100: the
   background's range falls to under 0.001 level; every star keeps its height above the ring around
   it within 0.02 %; the darkest 3.4 % of the sky is lifted by at most 2.9 levels (level 5). With
   noise of 2.5 levels, a Milky Way band of 0.04 and a ground: the fitted surface is within 0.14
   level of the true one (a single fit that drops no tile: 3.0 levels off). The star finder finds
   every star and nothing farther than 6 px from one. At noise of 5 levels, for stars of 6 sigma
   and more: `denoise` 100 takes up to 34 % of a star's height, with `denoise_stars` 100 at most
   1.2 %; `sharpen` 30 adds up to 34 %, with `sharpen_stars` 100 at most 1.2 % (at 60: 2.0 %).
3. **The real frames, full size.** Star weight on 0.4 to 2.6 % of a frame, about 0.3 s per 22 MP. Stuck
   pixels: without the colour test 367 to 836 single white pixels per JPEG frame were taken, and
   only 3 to 10 of them sit on the same sensor pixel in two frames: faint stars, which move, not
   stuck photosites. With the test: 0 or 1 per JPEG, 75 and 2 in the RAW files; 0.7 s. The fit: 195
   to 522 tiles, 0.05 s on the analysis picture; the glow spans 2 (0115), 26, 31, 43, 44, 55 levels.
4. **With a hand-drawn sky** (`--genre astro`): the spread of the sky's tile medians (95th minus
   5th percentile) falls from 23 to 41 levels to 6 to 20; the Milky Way stands out. Where the
   polygon stops short of the ridge a band of glow is left, now brighter than the sky above it.
5. **Tone gate, the six night frames** (a folder of links): default 0 and 0, 0370 pulled back to
   0.75 and flagged; `--genre astro` 0 and 0, none pulled back, 0.18 to 0.48 EV over the source
   (0115, median 0.03: 1.35). With the hand-drawn skies as fixed masks (standing in for SAM 3's):
   default 0 and 0, none pulled back; astro 0 and 0 counted, but 0504 and 0540 are blended back to
   0.125: their bottom third is mostly sky, and the glow coming off darkens its median.
6. **Tone gate, 110 RAW of the Dolomites**, `--genre astro`: 0 and 0, no hard failure, as without a
   genre; no night frame (no ISO 1600), so only preset, look and noise reduction act. 7 blended back
   against 12; one only with the pack, 0063_snow (0.75), which `landscape` and `alpine` pull back too.
7. **Default output**: 14 photos (smoke, 6 Dolomites JPEGs, the 6 night frames), maximum difference 0.
8. **Time**, a 22 MP night frame, decoded, stub: 4.8 s without a genre, 9.0 s with the pack and a
   sky: noise reduction 1.0 s, the star weight 0.3 s in each of denoise and sharpen, stuck pixels 0.8 s.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| On by default for night frames? | No: `night_sky` is off; the `astro` pack and `--night-sky` switch it on. Default output is byte-identical (Measured 7) | `night_sky = true` under `[run]` in `defaults.toml`, after the owner's blind sheet |
| What a night frame is | Median luma at most 0.25 and ISO at least 1600, as data (Measured 1). No EXIF, no night frame | `[night_sky]` in `rules.toml` |
| How the night rules attach | A scene of its own, `night`, through `classify(override=)`. Its blocks exist only in the opt-in set: no rule of today changes and none is copied | not meant to be reversed |
| The tone of a night frame | No exposure lift unless the median is under 0.10 (then 60 % of the way, at most 1.5 EV and the headroom); a black-pinned lift of 0.3 EV at black, 0.4 more on the sky; no contrast, curve, dehaze, clarity, colour or white-balance move | `[opt_in.night_sky.night.*]` |
| A new curve shape? | No. `shadows_ev` stops at mid grey: star cores (0.5 and up) are not touched, black stays black. `curve` at a negative strength lifts the dark tones but dims a core of 0.9 by 3 % | a curve op, if the owner's sheet asks for more |
| Where the two corrections act | On the decoded photo, before the region pass, made by the pipeline: the gradient needs the sky mask, and the regions, the global pass and the gates must start from the cleaned frame. They are the first step of `STEPS`, not rendered by `apply_ops`, like `lut` and `grain` | not meant to be reversed |
| How far the sky is taken down | To the 5th percentile of the fitted background over the sky, per channel. The glow comes off; the darkest twentieth (a vignetted corner) is lifted to that level; the colour of the sky's dark part stays: white balance is the job of `temp` and `tint` (the magenta RAW pair keeps a magenta sky over its magenta ground) | `level` in `[night_sky]` (50 keeps the sky's median) |
| Star protection | Two values, `denoise_stars` and `sharpen_stars`, each in its op's step. The star finder's sizes and thresholds are constants of `ops.py`, like F5's and F23's: an op is a function of its pixels and values, and `apply_ops` reads no config | `_STAR_*` in `ops.py` |
| Stuck pixels in a camera JPEG | Only a coloured single pixel counts (Measured 3): a white one is a faint star | `_HOT_COLOUR` in `ops.py` |
| Intensity | The four night values are taken as written: their rules divide by the intensity, which the look multiplies back | the four expressions |
| The pack | `preset = "landscape"` (no new SAM 3 words; the night rules use only the sky), look `astro`, `night_sky` and `denoise` on, `tested = false` (6 photos, the bar is 20). No palette line: the automatic palette fired on none of the six | the `[astro]` table |
| The look's `preserve_exposure` | Set, as the plan asks: taking the glow off darkens the sky on purpose. The first packaged look with it; F8's open question is about moody looks, and only the pack or `--look astro` reach this one. F8's guard test names it as the exception | delete the line and the exception |
| Words for instructions | None and no `[limits]` entry: the values are decided per frame; `--set global.sky_gradient=N` works | a verb in `instructions.toml` |
| The fit | Tile medians with tiles dropped, not F34's `backdrop.fit`: that clips single pixels and, with the Milky Way band of Measured 2, is 1.2 levels off (tile medians: 0.14). F34's `rules.block` reads `[night_sky]` | not meant to be reversed |

## Design

### 1. Switching it on

`contract.py`, directly after `denoise`:
`night_sky: bool = False  # night sky tools (F31): night frames take the night rules; off → the rule set is not loaded and the ops are dropped after decide`.
`pipeline.py`: `_OPT_IN_RULES = ("levels", "denoise", "night_sky")`. `cli.py`: `--night-sky`
(`store_true`, `default=None`, after `--denoise`; help "night sky tools: night frames keep their
darkness, a town's glow comes off the sky, stuck pixels are repaired, and noise reduction and
sharpening spare the stars (opt-in; the astro pack sets it)"), and `"night_sky"` after `"denoise"`
in the `[run]` list of `_flatten` and in `overrides`. `web/service.py::_ALLOWED` and
`genres.RUN_KEYS` gain `"night_sky"` (and the comment of `genres.toml` that lists the run keys).

### 2. The ops (`edit/ops.py`, protected)

`"hot_pixels", "sky_gradient"` open `OP_NAMES` (comment: "night sky (F31): corrections of the
capture, made by the pipeline to the decoded photo"); `"denoise_stars"` and `"sharpen_stars"` follow
their ops; all four are `(0.0, 100.0)` in `OP_BOUNDS` and in `SPATIAL_OPS`. `STEPS` gains the first
step `("hot_pixels", "sky_gradient")`; `("denoise",)`, `("sharpen",)` become `("denoise",
"denoise_stars")`, `("sharpen", "sharpen_stars")`; `_TONE_KEYS` reads `STEPS[1:6]` (the same set).
The comment above `STEPS` and `apply_ops`' docstring add that the first step, like the last two, is
not rendered by `apply_ops`, which passes `settings.get("denoise_stars", 0.0)` and
`settings.get("sharpen_stars", 0.0)` as a third argument. `denoise(rgb, amt, stars=0.0)` ends with
`if stars > 0.0: out += (star_weight(l) * np.float32(min(float(stars), 100.0) / 100.0))[..., None] * (rgb - out)`;
`sharpen(rgb, amt, stars=0.0)`, after `detail = ...`: `if stars > 0.0: detail = detail * (1.0 -
star_weight(l) * np.float32(min(float(stars), 100.0) / 100.0))`. Both docstrings: "`stars` 0..100
(F31) spares the stars of the input by that share of their weight; 0 is the op as before, bit for
bit." The filters import gains `box_blur`. Directly before `vibrance`:

```python
# Night sky (F31). Constants of the ops, like the levels and grain constants. Sizes follow the
# frame's long edge, so a preview and an export find the same stars.
_STAR_EDGE = 1.0 / 1000.0   # a star is narrower than a square of 2r + 1 px, r this share of the long edge
_STAR_K = 5.0               # a star stands this many noise sigmas above its local background ...
_STAR_MIN = 3.0 / 255.0     # ... and at least this much
_HOT_K = 6.0                # a stuck pixel stands this many noise sigmas above all 8 neighbours ...
_HOT_MIN = 24.0 / 255.0     # ... and at least this much, in one channel
_HOT_SUPPORT = 0.25         # its 4 nearest neighbours rise less than this share of its height over the ring around it
_HOT_COLOUR = 0.5           # and its weakest channel stands less than this share of that: a white point is a star
_NB8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_NB4 = ((-1, 0), (0, -1), (0, 1), (1, 0))
_RING = tuple((dy, dx) for dy in range(-2, 3) for dx in range(-2, 3) if max(abs(dy), abs(dx)) == 2)


def _box_max(a, radius):
    return -box_min(-a, radius)


def _robust_sigma(d):
    s = d[::2, ::2] if min(d.shape[:2]) >= 64 else d
    return float(1.4826 * np.median(np.abs(s - np.median(s))))


def star_weight(l: np.ndarray) -> np.ndarray:
    """Where the stars are (F31): 0..1 per pixel of a luma plane, 1 on a core. Background: an opening
    with a square of 2r + 1 px, which removes everything narrower. A star stands above it by more than
    _STAR_K robust sigmas and _STAR_MIN; 1 at twice that, grown by 1 px. The Milky Way, snow: 0."""
    h, w = l.shape[:2]
    r = max(1, int(round(_STAR_EDGE * max(h, w))))
    s = box_blur(l, 1)
    d = (s - box_blur(_box_max(box_min(s, r), r), r)).astype(np.float32)
    med = float(np.median(d[::2, ::2] if min(h, w) >= 64 else d))
    t = max(_STAR_K * _robust_sigma(d), _STAR_MIN)
    return _box_max(np.clip((d - med - t) / t, 0.0, 1.0).astype(np.float32), 1)


def hot_pixels(rgb: np.ndarray, amt: float) -> np.ndarray:
    """Stuck pixels (F31): a pixel above all 8 neighbours in channel c by _HOT_MIN and _HOT_K sigmas,
    alone (_HOT_SUPPORT) and coloured (_HOT_COLOUR), moves toward the median of its 8 neighbours in c
    by amt/100; 0 is the identity. Only the candidates are looked at closely."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    h, w = rgb.shape[:2]
    if a == 0.0 or h < 5 or w < 5:
        return rgb
    p = np.pad(rgb, ((2, 2), (2, 2), (0, 0)), mode="edge")
    hi = None
    for dy, dx in _NB8:
        v = p[2 + dy:2 + dy + h, 2 + dx:2 + dx + w]
        hi = v.copy() if hi is None else np.maximum(hi, v, out=hi)
    sig = np.asarray([_robust_sigma(rgb[..., c] - box_blur(rgb[..., c], 1)) for c in range(3)], np.float32)
    ys, xs, cs = np.nonzero((rgb - hi) > np.maximum(_HOT_MIN, _HOT_K * sig))
    if ys.size == 0:
        return rgb
    py, px, val = ys + 2, xs + 2, rgb[ys, xs, cs]
    ring = np.median(np.stack([p[py + dy, px + dx, cs] for dy, dx in _RING]), axis=0)
    near = np.mean(np.stack([p[py + dy, px + dx, cs] for dy, dx in _NB4]), axis=0)
    e = rgb[ys, xs] - hi[ys, xs]
    alone = ((near - ring) < _HOT_SUPPORT * (val - ring)) & (e.min(axis=1) < _HOT_COLOUR * e[np.arange(ys.size), cs])
    ys, xs, cs, py, px, val = ys[alone], xs[alone], cs[alone], py[alone], px[alone], val[alone]
    if ys.size == 0:
        return rgb
    med = np.median(np.stack([p[py + dy, px + dx, cs] for dy, dx in _NB8]), axis=0)
    out = rgb.copy()
    out[ys, xs, cs] = val + np.float32(a) * (med - val)
    return out


def sky_gradient(rgb: np.ndarray, amt: float, offset: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """The sky's gradient taken off (F31): rgb - amt/100 * alpha * offset in 0..1; offset (HxWx3) is the
    smooth fitted background minus its level (night.py), alpha the feathered sky. 0 is the identity."""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    return par.rows(lambda x, o, m: np.clip(x - o * (np.float32(a) * m)[..., None], 0.0, 1.0).astype(np.float32),
                    rgb, offset, alpha)
```

### 3. `night.py` (new)

Docstring: "Night sky (F31): is a photo a night frame, and the two corrections of the capture the
pipeline makes to the decoded photo before the edit. The pixel math is in edit/ops.py; the
thresholds are data, [night_sky] in rules.toml." `OPS = ("hot_pixels", "sky_gradient",
"denoise_stars", "sharpen_stars")`; `DEFAULTS` = the values of `[night_sky]` (Config), ints where
the TOML has ints; `RANGES = {"max_median": (0.0, 1.0), "min_iso": (0, 1_000_000), "tile": (0.005,
0.25), "min_share": (0.05, 1.0), "degree": (1, 4), "clip": (1.0, 10.0), "level": (0, 100)}`; `_LUMA`
= Rec. 709. `is_night(g, cfg)`: `g.lum_p50 <= cfg["max_median"] and g.iso >= cfg["min_iso"]`.
`_terms(x, y, degree)`: `[x ** i * y ** j for i in range(degree + 1) for j in range(degree + 1 - i)]`.
`surface(coef, h, w, degree)`: the polynomial on the h x w grid of pixel centres in -1..1, HxWx3
float32, term by term.

```python
def fit(rgb, sky, cfg) -> tuple[np.ndarray | None, int]:
    """(coefficients (terms, 3), tiles used) of the sky's background, or (None, tiles) with fewer than
    3 tiles per term. The median of each square tile in which at least `min_share` of the pixels are
    sky and off every star, at the tile's centre in -1..1; tiles further than `clip` robust sigmas
    from the fit of their luma are dropped and the fit made again, three times at most."""
    h, w = rgb.shape[:2]
    degree = int(cfg["degree"])
    good = sky & (ops.star_weight(luma(rgb)) <= 0.0)
    n = max(4, int(round(cfg["tile"] * max(h, w))))
    xs, ys, vals = [], [], []
    for y0 in range(0, h - n // 2, n):
        for x0 in range(0, w - n // 2, n):
            g = good[y0:y0 + n, x0:x0 + n]
            if g.mean() >= cfg["min_share"]:
                vals.append(np.median(rgb[y0:y0 + n, x0:x0 + n][g], axis=0))
                ys.append((y0 + g.shape[0] / 2.0) / h * 2.0 - 1.0)
                xs.append((x0 + g.shape[1] / 2.0) / w * 2.0 - 1.0)
    k = len(_terms(0.0, 0.0, degree))
    if len(vals) < 3 * k:
        return None, len(vals)
    X = np.stack(_terms(np.asarray(xs), np.asarray(ys), degree), axis=1)
    V = np.asarray(vals, np.float64)
    L, keep = V @ _LUMA, np.ones(len(V), bool)
    for _ in range(3):
        c, *_ = np.linalg.lstsq(X[keep], L[keep], rcond=None)
        res = L - X @ c
        new = np.abs(res) <= cfg["clip"] * (1.4826 * np.median(np.abs(res[keep] - np.median(res[keep]))) + 1e-6)
        if new.sum() < 3 * k or np.array_equal(new, keep):
            break
        keep = new
    coef, *_ = np.linalg.lstsq(X[keep], V[keep], rcond=None)
    return coef, int(keep.sum())
```

`correct(img, proxy, regions, settings, cfg, meta) -> (Image, notes)` never changes `img`. If
`hot_pixels` > 0: `ops.hot_pixels` on `img.rgb`, note `f"night sky: {n} stuck pixels replaced by
their neighbours"` (n pixels changed) or `"night sky: no stuck pixel found"`. If `sky_gradient` > 0:
the region `sky` of `regions` (none: `"night sky: no sky found, its gradient is left"`), `fit(proxy.rgb,
sky.mask, cfg)` (none: `f"night sky: too little clear sky to fit its gradient ({tiles} tiles), it is
left"`), `on_sky = surface(coef, ph, pw, degree)[sky.mask]`, `level = np.percentile(on_sky,
cfg["level"], axis=0)` (float32), `offset = surface(coef, h, w, degree) - level`, `alpha =
build_alpha(sky.mask, (h, w), meta_for("sky", meta).feather_px, guide=luma(rgb))`, `rgb =
ops.sky_gradient(rgb, amt, offset, alpha)`, note `f"night sky: the sky's gradient taken off, {span *
255:.0f} levels at most (fitted on {tiles} tiles)"` (`span`: the largest luma of `on_sky` minus the
luma of `level`). Returns `img` if nothing changed, else `dataclasses.replace(img, rgb=rgb)`.

### 4. The pipeline (protected): what is inserted

The stage order does not change: the night frame is found in `scene`, the values come from
`decide`, the corrections are the first thing `apply` does. `night` joins the `from . import`
list; a field after `geometry_cfg`, `night_cfg: dict = field(default_factory=dict)  # [night_sky] of
rules.toml, checked (F31)`, set in `build` after `geometry_cfg=...` by
`night_cfg=rules_mod.block(rules, "night_sky", night.DEFAULTS, night.RANGES),` (F34's helper: a value
out of range stops the run before a photo is read; if F34 is not in the tree, T2 adds `rules.block`
as F34's spec gives it).

- `sc = scene_mod.classify(gstats, present, region_stats)` becomes:

```python
        # Night sky (F31): with the run setting on, a night frame takes the scene "night", whose rules
        # are the [opt_in.night_sky.night.*] blocks; any other frame keeps the usual scene.
        night_frame = cfg.night_sky and night.is_night(gstats, self.night_cfg)
        sc = scene_mod.classify(gstats, present, region_stats, override="night" if night_frame else None)
```

- Block A, after decide: `if not cfg.sharpen:` pops `sharpen_stars` too, from every block;
  `if not cfg.denoise:` pops `denoise_stars` too; `if not cfg.night_sky:` pops every key of `night.OPS`.
- Block B, directly before `# The grain that is added:`: region blocks drop `hot_pixels`, `sky_gradient`.
- Directly after `notes_extra: list[str] = list(policy_notes)`, when `cfg.night_sky`:
  `f"night sky: {'a' if night_frame else 'not a'} night frame (median {gstats.lum_p50:.2f}, ISO
  {gstats.iso:.0f}): {'the night rules' if night_frame else 'the usual rules'}"`.
- First lines of `if do_apply:`, before `guide = luma(img.rgb)`:

```python
            # Night sky (F31): stuck pixels and the sky's gradient are corrections of the capture. They
            # are made once, to the decoded photo, before every region, the global pass and the checks.
            if global_settings.get("hot_pixels") or global_settings.get("sky_gradient"):
                img, night_notes = night.correct(img, proxy, resolved.regions, global_settings, self.night_cfg, self.meta)
                notes_extra += night_notes
```

- `gate_src` in `process_one` and `reference` in `apply.py::apply_image` (the gate's denoised
  reference) pass `global_settings.get("denoise_stars", 0.0)` too.

`img` is rebound, never changed in place. The banding gate does not damp the night values
(`banding.STRETCH_OPS`). F29's `global_steps` skips the first step; the corrections show in the
"regions" picture, and `teach.NOTES` gains `"regions": ("night sky:",)`.

### 5. The gates on frames meant to be dark

`quality.toml`, `gates.py` and `quality.py` do not change. The review gate does not flag a night
frame whose sky got darker: the look `astro` sets `preserve_exposure`. The tone report's audit does
not know the look (fact 6): a frame whose bottom third is mostly sky (0504, 0540) fails
`dark_made_darker` once the glow comes off and is blended back (Measured 5); the report prints 0 and
0, and the signal is `safety_fallback` under 1. With the stub no night sky is found and nothing is
pulled back. Whether the audit should measure a night frame without its sky is the owner's call.

## Config

`rules.toml`: `[night_sky]` directly before `# ─── highlight hold ───`, the two opt-in tables at
the end of the file.

```toml
# ─────────────────────────── night sky ───────────────────────────
# Night sky tools (F31, opt-in: `night_sky` in the run settings; the astro pack sets it). A photo is
# a night frame when its median brightness is at most `max_median` and its ISO at least `min_iso`
# (no EXIF: ISO 0); it takes the scene "night", the [opt_in.night_sky.night.*] rules. The sky
# gradient: per channel a polynomial of `degree` fitted to the medians of square tiles (`tile` of the
# long edge, at least `min_share` sky and off the stars); tiles more than `clip` robust sigmas off
# the fit are dropped. The sky is taken down to the `level` percentile of that background over it.
[night_sky]
max_median = 0.25
min_iso    = 1600
tile       = 0.03125
min_share  = 0.5
degree     = 2
clip       = 2.5
level      = 5

# Night sky (F31). The scene "night" exists only here. Its global block keeps the frame dark: no
# lift but toward a median of 0.10, a small black-pinned lift, no contrast, curve, dehaze, clarity,
# colour or white balance. The four night ops are corrections, not edit strength: divided by the
# intensity, which the look multiplies back. Noise reduction keeps F7's rule and needs `denoise` on.
[opt_in.night_sky.night.global]
hot_pixels    = "100 / max(intensity, 0.01)"
sky_gradient  = "100 / max(intensity, 0.01)"
exposure      = "if(pro_stats_valid > 0, clamp(0.6 * 2.2 * log2(0.10 / max(lum_p50, 0.005)), 0, min(1.5, max(0, headroom_ev - 0.15))), 0)"
shadows_ev    = "0.3"
denoise       = "if(pro_stats_valid > 0, 100 * clamp((iso - 800) / 5600, 0, 1) * clamp(0.4 + 0.75 * planned_exposure, 0.4, 1), 0)"
denoise_stars = "100 / max(intensity, 0.01)"
sharpen       = "7 * (1 - min(iso, 6400) / 12800)"
sharpen_stars = "100 / max(intensity, 0.01)"

# The sky of a night frame: faint stars and the Milky Way are lifted with the black-pinned shadow
# lift, which stops at mid grey: a star's core (0.5 and up) is not touched, black stays black.
[opt_in.night_sky.night.sky]
shadows_ev = "0.4"
```

`looks.toml`, before the black and white section:

```toml
# Night sky (F31), the look of the astro pack: no curve of its own (it darkens dark tones). Taking a
# town's glow off the sky darkens it on purpose: the review gate's darkness check is off.
[astro]
clarity_mul  = 0.7
dehaze_mul   = 0.8
preserve_exposure = true
```

`genres.toml`, at the end:

```toml
# Night sky (F31). Untested: 6 night frames, the bar is 20. Only dark, high-ISO frames get the night rules.
[astro]
description = "Night sky: keeps the night dark, takes a town's glow off the sky, repairs stuck pixels, and noise reduction and sharpening leave the stars alone."
tested = false
preset = "landscape"
look = "astro"
reveal = ["people", "face", "writing", "address"]

[astro.run]
night_sky = true
denoise = true
```

`defaults.toml`, after `denoise`: `night_sky = false          # true: night frames keep their
darkness, lose a town's glow and stuck pixels; denoise and sharpen spare the stars (F31, opt-in)`.
`settings.toml`, after `[setting.denoise]`: `[setting.night_sky]` with `label = "Night sky"`,
`group = "quality"`, `help = "Night photos stay dark and lose a town's glow and stuck pixels; noise
reduction and sharpening spare the stars."` (a test allows 140 characters). `lessons.toml`:
`[op.denoise_stars]` after `[op.denoise]`; `[op.sharpen_stars]`, `[op.hot_pixels]`,
`[op.sky_gradient]` after `[op.sharpen]`:

| Entry | name | does | when | watch |
|---|---|---|---|---|
| `denoise_stars` | Noise reduction spares the stars | Keeps noise reduction off the stars: a small bright point keeps its own pixels and its colour. | Night skies, where noise reduction would smooth faint stars away. | A town's lamps are points too and keep their noise around them. |
| `sharpen_stars` | Sharpening spares the stars | Keeps sharpening off the stars, so a star does not grow a bright core and a dark rim. | Night skies. | The sky around a star is still sharpened; a sky full of noise is better left soft. |
| `hot_pixels` | Stuck pixels | Finds single pixels that stand far above all their neighbours in one colour and gives them the middle value of the neighbours. | Long exposures at night, where the sensor leaves bright coloured dots. | A stuck pixel that is white in a camera JPEG looks like a faint star and is left. Look at full size. |
| `sky_gradient` | Sky gradient | Takes the glow of a town or the moon off the sky: a smooth surface fitted to the sky without its stars is taken away, down to the sky's darkest part. | Night skies that grow brighter and warmer toward the horizon. | It works inside the sky the segmenter found. Where that stops short of the horizon, a band of glow is left. |

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/edit/ops.py`; `src/autoedit/night.py` (new); `configs/lessons.toml` | Design 2 and 3; the four lessons | T1 |
| `tests/test_denoise.py`, `test_grain.py`, `test_lut.py`, `test_bw.py` (the `STEPS` assert), `test_levels.py`, `test_parallel.py` | `("denoise", "denoise_stars")`, `("sharpen", "sharpen_stars")`, `STEPS[1:6]`; the cut bundle gains the four values | T1 |
| `src/autoedit/pipeline.py`, `edit/apply.py`, `contract.py` (`rules.py` only if F34's `block` is missing); `configs/rules.toml`, `configs/looks.toml` | Design 1 (the field, `_OPT_IN_RULES`) and 4; Config | T2 |
| `tests/test_bw.py` (`astro` joins the packaged looks, the only one with the flag), `test_look_files.py` (a look file never carries `preserve_exposure`: compare with `dataclasses.replace(look, preserve_exposure=False)`), `test_defaults.py` (`preserve_exposure is (name == "astro")`) | three asserts | T2 |
| `src/autoedit/genres.py`, `configs/genres.toml`, `cli.py`, `web/service.py`, `teach.py`, `configs/settings.toml`, `configs/defaults.toml` | the rest of Design 1, Config, the `NOTES` line | T3 |
| `tests/test_genres.py` (`astro` joins four golden lists; `run_kwargs("astro", {})`; the hold assert becomes `(0.80, 0.985) if name == "alpine" else None`), `tests/snapshots/settings_schema.json`, `api_v1.json` | asserts; regenerated | T3 |
| `tests/test_night_sky.py` | new: tests 1 to 6 (T1), 7 to 9 (T2), 10 (T3) | T1 to T3 |

## Tasks

One after the other. Other features edit the same files: every edit is placed by its anchor text,
the snapshots are regenerated, never by hand. `.venv` has no pytest: there it is `tests/run_all.py`.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high (protected `ops.py`) | Design 2 and 3, the lessons, the six test edits, tests 1 to 6 | `PYTHONPATH=src python3 -m pytest tests/test_night_sky.py tests/test_denoise.py tests/test_grain.py tests/test_lut.py tests/test_bw.py tests/test_levels.py tests/test_parallel.py tests/test_ops.py tests/test_ops_phase2.py tests/test_lessons.py tests/test_llm_refresh.py -q` |
| T2 | Builder, Sonnet, high (protected `pipeline.py`) | Design 1 (field, `_OPT_IN_RULES`) and 4, `rules.toml`, `looks.toml`, the three asserts, tests 7 to 9 | `PYTHONPATH=src python3 -m pytest tests/test_night_sky.py tests/test_pipeline.py tests/test_rules.py tests/test_bw.py tests/test_look_files.py tests/test_defaults.py tests/test_denoise.py tests/test_adjust.py tests/test_teach.py tests/test_quality_runner.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | the rest of Design 1, `genres.toml`, `settings.toml`, `defaults.toml`, `teach.NOTES`, the snapshots (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py tests/test_api_contract.py -q`), the `test_genres.py` asserts, test 10 | `PYTHONPATH=src python3 -m pytest tests/test_night_sky.py tests/test_genres.py tests/test_cli.py tests/test_web_service.py tests/test_settings_schema.py tests/test_api_contract.py tests/test_tone_report.py tests/test_profile.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | the diff against this spec (`ops.py`, `pipeline.py` protected), the definition of done, acceptance checks 3 to 6 | none |
| Docs | The session, inline | `README.md` (`--genre astro`, `--night-sky`); `ARCHITECTURE.md` section 8 and `APP_GUIDE.md` (the first step of the op order, the two `_stars` values); `GENRES.md` kind 14 ("built, untested: 6 photos"); `CHANGELOG.md`; the plan row and tables | none |

## Tests

`tests/test_night_sky.py`, for pytest and `tests/run_all.py` (no `capsys`, `parametrize`, `approx`
on dicts, `pytest.raises(match=)`). Helpers: `_sky(seed, noise, band)` the sky of Measured 2 (400 x
600, float32; band `band * exp(-((x - 0.8 y - 150) / 30)^2)`) and its star centres; `_heights` a
star's centre luma minus the median of the ring 5 to 9 px around it; `_far(stars, r)`;
`_take_off(rgb, sky)` fits with `night.DEFAULTS` and applies `ops.sky_gradient`, the mask as alpha;
`_photo(path, base, iso)` a JPEG of that sky scaled to `base`, a flat ground in the bottom 30 %, the
ISO in its EXIF (`0x8769: {0x8827: iso}`); `_SkyTop` a segmenter whose sky is the top 70 %;
`_astro(**kw)` the pack's settings written out (stub), so tests 7 to 9 need no genre.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_zero_is_the_identity` | `hot_pixels(x, 0)` and `sky_gradient(x, 0, …)` return `x`; `denoise(x, 40, 0.0)`, `sharpen(x, 30, 0.0)` equal the calls without it; `apply_ops` of `{"hot_pixels", "sky_gradient"}` at 100 and of `{"denoise_stars", "sharpen_stars"}` at 100 returns `x` |
| 2 | `test_the_slope_goes_and_the_stars_stay` | No noise, all sky: over 300 tiles; the star-free background's range per channel above 5 levels before, at most 1 after; every star's height within 2 %; lifted by at most 3 levels, on at most 5 % of the pixels |
| 3 | `test_noise_a_band_and_the_ground` | Noise 0.01, band 0.04, sky in the top 280 rows: the surface within 1 level of the true background (its offset removed); the ground rows byte-identical; stars on the sky within 2 % |
| 4 | `test_stars_are_found_and_noise_is_not` | Noise 0.01 and the band: weight above 0 at every star centre and 0 farther than 6 px from every star; float32 in 0..1 |
| 5 | `test_stuck_pixels_go_and_stars_stay` | 50 single-channel spikes of 0.6 on 0.10 with noise of 1 level: each within 1 level of its neighbours' median; 30 single white pixels unchanged; the noisy sky of test 2 unchanged; at 50 a spike is halfway |
| 6 | `test_denoise_and_sharpen_spare_star_cores`, `test_registered_in_the_frozen_order` | Noise 0.02, stars of height 0.12 or more: `denoise` 100 and `sharpen` 30 with `_stars` 100 keep every height within 2 %, without it one moves more than 20 %. The registrations of Design 2; `_TONE_KEYS` the old set; `apply_ops` of `denoise` 60, `sharpen` 20, both `_stars` 100 equals the two calls |
| 7 | `test_night_detection_and_the_rules_golden` | `Pipeline.night_cfg` of the packaged rules equals `night.DEFAULTS` and `[night_sky]`; `level = 500` in a rules copy stops `Pipeline.build` with a `ValueError` naming `[night_sky] level`; `is_night` of (0.18, 12800) and (0.25, 1600) true, of (0.26, 12800), (0.05, 800), (0.05, 0) false. `_photo` (median 0.0709, ISO 6400), astro settings, `_SkyTop`: scene `night`; global settings to 4 decimals exactly `hot_pixels` 100, `sky_gradient` 100, `exposure` 0.5249, `shadows_ev` 0.24, `denoise` 63.4927, `denoise_stars` 100, `sharpen` 2.8, `sharpen_stars` 100; one region, sky, `shadows_ev` 0.32; the two notes |
| 8 | `test_off_and_day_frames_are_unchanged` | A bright ISO 100 photo: same pixels and scene with and without `night_sky`, same notes but "night sky: not a night frame"; the dark photo without the setting: no night op, no night note; a decider proposing the four ops (global and sky): none left with only `denoise` on; with `night_sky` on and denoise off, `hot_pixels`, `sky_gradient`, `sharpen_stars` left, and no correction in a region |
| 9 | `test_the_astro_pack_end_to_end` | With `_SkyTop`, the luma gap between the sky's top and bottom rows is under a third of the stub run's; the stub run notes "no sky found"; no review flag; `night_sky` in the sidecar's run config |
| 10 | `test_the_switches` | `--night-sky` sets it; `--genre astro` gives `night_sky`, `denoise`, look `astro`, preset `landscape`, genre `astro`; `make_cfg({"night_sky": True})`; `"night sky:"` in `teach.NOTES["regions"]`; `autoedit run --genre astro` on `_photo` exits 0, its sidecar says scene `night` |

## Acceptance checks (the session runs these)

1. Both suites green: `PYTHONPATH=src python3 tests/run_all.py`, and with `.venv/bin/python`.
2. The stub smoke run of `CLAUDE.md` completes; with `--genre astro` into a new folder every sidecar
   has `night_sky: true` and "night sky: not a night frame" (the smoke photos carry no ISO).
3. Default output byte-identical to the baseline (maximum difference 0). Sidecars gain `night_sky`;
   `config_digest` changes (`rules.toml`, `looks.toml`): `adjust` on an older export warns.
4. The tone gate of `CLAUDE.md`, stub, all 110 RAW, without and with the pack:
   `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression [--genre astro]`.
   Both 0 and 0; without the pack the rows equal the baseline's; with it no frame is a night frame
   and every photo's `safety_fallback` is compared with the default run's (Measured 6).
5. The six night frames: a folder of links under `.tmp/` to the files of fact 1, the same command
   with and without `--genre astro`, stub (Measured 5), then `--backend sam3` (`.venv/bin/python`,
   one job at a time). Per photo: night frame or not, whether SAM 3 found the sky and its coverage,
   `safety_fallback`. Expected: 0504 and 0540 blended back with the pack.
6. By eye, full-size SAM 3 renders of the six with `--genre astro` (the horizon, the Milky Way,
   star cores, the magenta RAW pair), crops into `out-features/F31/`. The blind sheet stays open:
   it needs 20 or more night photos from the owner.

## Known limits

- Six night photos, one night, one camera: the thresholds and the night tone are measured on them
  only. A night photo without EXIF, or shot at ISO 800 with a fast lens, gets today's rules.
- The gradient comes off only inside the segmenter's sky: where the mask stops short of the
  horizon a band of glow is left, brighter than the sky above (Measured 4). The stub finds no night
  sky. The glow over the ground stays; a degree-2 surface cannot follow a lamp inside the frame.
- A white stuck pixel in a camera JPEG is left. Previews and report renders see other pixels than
  the full size: 0 to 75 changed per night frame at full size, 0 to 79 at 1536 px, 2 to 132 at
  1024 px (coloured star pixels among them). `sharpen_stars` spares the core, not the sky around it.
- The tone report's audit calls a darker sky in the bottom third a regression (Design 5). The
  `global-first` and `optimized` workflows do not know the night tools; Studio has only the setting.

## Proposed plan row

`| F31 | Night sky tools | M | done, off by default (`--night-sky`, `--genre astro`, untested: 6 night photos, the bar is 20); the owner's 20 night photos and blind sheet are open |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Night sky (F31): 20 night photos for the blind sheet and the pack's gate; should
  the tone report's audit measure a night frame without its sky (0504 and 0540 are blended back)?"
  Evidence: Measured 5, `out-features/F31/`. The change: `tested = true` in `genres.toml`; an audit
  change is a protected task of its own.
- Changed against the plan: "The astro look is the first packaged look with `preserve_exposure`;
  F8's guard test names it." Why: the plan asks for it; only the pack or `--look astro` reach it.
  Reverse it by: deleting the line and the exception.

## Result

Built on 2026-11-17 in the unattended run. Status: done, off by default; the `astro` pack is untested.
The spec was written on Opus at max effort (Fable's quota had run out).

- **Built** by three Builders (Sonnet). One decision of the session overrides the spec: the `astro`
  look has **no** `preserve_exposure`. Whether a look may opt out of the darkness check is an open owner
  question, and the unattended run does not decide it; `tests/test_defaults.py` is unchanged.
- **The Checker (Opus, high)** found one thing that had to be fixed, fixed by the session with a test
  that fails without it: with the setting on, a decider (the LLM path) could still put `sky_gradient`
  and `hot_pixels` on a day frame; now every night op is dropped from any frame that is not a night
  frame. Kept as known limits: tests 4 and 6 pick stars by their measured height and fail on about 1 to
  3 seeds in 200 (the ops behave as designed; Measured 2's "6 sigma" holds from about 9 sigma); test 9's
  made-up frame cannot trip the darkness check and test 3 uses a hard mask; where the sky mask stops short
  of the horizon the band below reads brighter than the darkened sky above (a step of about 8 levels);
  on the two RAW files `hot_pixels` mostly removes single noise spikes (382 and 22 pixels, none on the
  same photosite in both), not stuck photosites, so "stuck pixels" in the note overstates it; CR3 and RAF
  files read ISO 0 and are never night frames.
- **Checks:** both suites green (1026 on each interpreter); default output byte-identical; the smoke
  run with `--genre astro` notes "not a night frame" (no ISO). Tone gate, stub, 110 RAW files with the
  pack: 0 and 0, no frame is a night frame, 7 photos pulled back against 11 without a genre, one
  (`0063_snow`) only with the pack, as with the other packs. The six night frames
  (`out-features/F31/`): stub, 0 and 0, with the pack none pulled back (one without). SAM 3: all six are
  night frames; 0 dark made darker and 0 pushed into clipping, but with the pack the safety workflow
  blends 0504 and 0540 fully back (their bottom third is sky, and taking its glow off darkens it), and
  the renders mark 3 of 6 for review (0504, 0540: dark source made darker; 0757: shadow clipping 11 %).
  By eye (`sheet-sam3.jpg`): the glow is off the sky, the Milky Way and the stars stay, the frames are
  darker; the magenta RAW pair stays magenta (white balance is left alone by design). Time: 129 s for the
  six with SAM 3.
- **Open:** 20 night photos for the blind sheet and `tested = true`; `preserve_exposure` for `astro`;
  whether the tone report's audit should judge a night frame without its sky.
