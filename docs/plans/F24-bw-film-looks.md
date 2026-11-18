# F24. Black and white, and film looks

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F24.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and
the decisions that are the owner's are in the table below. Built on [F23](F23-grain.md), which is
in the tree.

The design was built once on a scratch copy of the tree, which is deleted again. The reference
code below is that code. Every number is measured: on the 110 RAW files of the Dolomites corpus
(stub backend, 1024 px, what `tone_report.py --render` renders) or on the test frames named. On
the copy both suites passed, on the system Python and on `.venv`: the 779 tests of today and the
19 of this spec.

## Goal

A black and white that chooses how each colour turns grey, as three looks, and five film looks.
All are chosen by name: `--look bw_red_filter`. Nothing is on by default. No existing look
changes, and default output stays byte-identical.

## What was found in the code

1. Black and white today is four prompt words in `intents.toml` that give `saturation = -100`.
   `pipeline.py::process_one` scales it by the intensity (-80 at the default 0.8). `saturation` is
   in `[guard] ops`, so `edit/apply.py::apply_image` leaves skin and snow in colour, and the
   palette grade is laid over the result.
2. `apply_image` renders regions, then layers, then the global pass (`ops.apply_ops`). After it
   come the palette grade, `gates.highlight_hold` (opt-in) and `gates.chroma_guard`; `process_one`
   may add a reference match (`edit/reference.py::color_transfer`) with a second hold. So an op
   of the global pass acts after every region edit, and three later steps work against the colour
   source. Measured on a grey render: the hold hands back up to 14 of 255 of colour where the
   source is near white, and a reference match colours the whole frame.
3. A look is shaped into the settings inside the decider (`looks.py::shape`) and every value is
   scaled by the intensity. A look has no field that lifts the blacks.
4. `ops.tone`'s `blacks` lifts by a gain: pure black stays black and the noise next to it is
   multiplied. A negative `dehaze` is a plain veil, `rgb * (1 + a/2) - 0.425 * a` with
   `a = amount / 100`, which lifts black evenly. A fade is therefore a negative `dehaze`.
5. `ops.color_grade` adds one offset per channel. In the shadows the channel it lowers falls
   under the clip level 0.02, which the pixel audit counts (`introduced_rgb_low_clipping`).
6. The oversaturation gate (`gates.chroma_guard`, allowance 0.02 from `pipeline.py`) lets a cast
   of 5 of 255 through on a neutral pixel, 2 on skin and snow, 1 above luma 0.90. A stronger cast
   fails the pixel audit as well: the packaged palettes at their normal strength, laid over
   `natural`, were pulled back by the safe renderer on 6 to 10 of 22 photos where `natural` was not.
7. `scripts/tone_report.py --render` renders through `quality_runner.render_safe`, which blends a
   failing edit toward the source and then counts 0. It does not read a look's
   `preserve_exposure`; only `gates.assess` in `process_one` does. No packaged look sets the flag
   (`tests/test_defaults.py`), and whether one may is an open owner decision (F8).
8. On this corpus `natural` is pulled back on 12 of 110 photos and flagged for review on 6.
   `punchy` and `dramatic` do worse (on 22 photos: 4 and 9 more pulled back, 1 and 3 dark photos
   made darker). The bar for a new look is `natural`, photo by photo.
9. Two measures of the audit sit near their limit under `natural`. Highlight texture after 8-bit
   rounding (`quality._texture`, limit 0.10): 0.13 to 0.27 on three photos, and a grey picture
   scores lower by itself, because its three channels round as one (0.27 became 0.05 when the
   `natural` render was turned grey). The step at a mask edge (`boundary_curvature_p99`, limit
   0.25): 0.14 and 0.20 on two dark photos with a bright sky.
10. A typed delta is held inside the op's bounds (`instructions._summed`): for an op of 0 to 100
    a negative delta becomes 0.
11. The Studio page has no look list, only four style cards from a list in the page
    (`web/static/studio.html`, `STYLES`). The API takes `look` (`web/service.py::_ALLOWED`), and
    `settings_schema._choices` lists the looks of `looks.toml`.
12. `tests/run_all.py` runs the tests without pytest. Its stand-in has `raises`, and `approx` for
    plain numbers: no `pytest.mark.parametrize`, no `approx` on a dict.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is anything black and white or faded by default? | No. Eight new looks, chosen with `--look`; the five old looks do not change | none needed |
| Does `--intensity` scale black and white? | No. `bw` and the weights are taken as written: the look is black and white at every strength. Its tone values scale as always | multiply by `cfg.intensity` in block A |
| May a weight darken a shadow? | No: nothing at luma 0.30 or below, fully from 0.60 up. A real filter also darkens blue shadows; that made 2 of 110 dark photos darker, and the look flag that would allow it is the owner's open decision (fact 7) | `_BW_DARK = 0.0` in `edit/ops.py`, and `preserve_exposure = true` under `[bw_red_filter]` |
| How strong is `bw_red_filter`? | cyan -45, blue -60: a mid-blue sky goes from 0.53 to 0.46. With -80 and -90 (0.53 to 0.40) the audit read the darker sky as a boundary artifact on 2 of 110 photos (fact 9), and the safe renderer pulled them back | the weights in `looks.toml` |
| How strong is a film cast? | Inside what the oversaturation gate lets through (fact 6): 1 to 3.5 of 255 | a cast allowance for looks, a feature of its own ("Known limits") |
| Does `cross_process` get more colour? | No. With `saturation_add` and `vibrance_add` at 4 to 6 the look was no stronger (2.0 of 255 from `natural`, 2.3 without), because the oversaturation gate takes the colour back, and it was pulled back on 10 to 14 photos, not 8 | those two fields under `[cross_process]` |
| Do the looks carry grain? | No. F23 left "should a packaged look carry grain" to the owner. The proposed amounts are in a comment of `looks.toml` | `grain = N` under the look; `tests/test_grain.py` then asserts "no look but these" |
| Do the prompt words "black and white", "b&w", "monochrome", "mono" use the new op? | No. They give `saturation -100` as before (fact 1) | `bw = 100` under the four words in `intents.toml`, `"bw": 100.0` in `intent._BOUNDS`, and no intensity factor for `bw` in the intent loop of `pipeline.py` |
| Toned black and white (sepia) | Not built. The split-tone runs before `bw`, and the skin guard would keep a tone off faces and snow | a feature of its own |
| `halation` | Not built. A glow needs a second spatial op in the protected file. The mild looks are mild because of fact 6, which a glow does not change, and the others differ from `natural` by 6 to 14 of 255 without it | a feature of its own |
| Styles, a look picker in Studio | None (fact 11). `--look` works with every style; the API and the settings schema carry the looks | the Studio rebuild; a table in `styles.toml` |
| The safety workflow | Unchanged. A black and white edit it pulls back shows colour again (fact 7): 5 or 6 of 110 photos, against 12 pulled back under `natural` | blend toward a grey copy of the source in `quality_runner.render_safe` |

## Design

### 1. The op (`edit/ops.py`)

Protected file. `bw` is an amount of 0 to 100 with six weights of -100 to 100: `bw_red`,
`bw_yellow`, `bw_green`, `bw_cyan`, `bw_blue`, `bw_magenta`. Seven op names, one step.

- **Bands.** No hue angle. A pixel is, exactly, `lo * white + (mid - lo) * secondary +
  (hi - mid) * primary`: its smallest channel as white, what its two larger channels share beyond
  that as yellow, cyan or magenta, and what its largest channel has alone as red, green or blue.
  The grey value is the white part plus each coloured part times how bright that colour comes out.
- **Weights.** 0 leaves a colour at its own Rec.709 luma. +100 makes it as bright as white, -100
  as dark as black, linear between. Nothing has to sum to 1: a weight acts on a coloured part,
  and a neutral pixel has none. **Grey input is unchanged, bit for bit, with any weights.**
- **All weights 0** gives the luma of `imageio.luma` (largest error 1.2e-7), so the gates, whose
  tone statistics are luma, see the tones of the colour picture.
- **Band edges.** At an edge the part that changes band is zero, so the grey value is continuous.
  Measured with the strongest weights: it moves at most 2.2 times the largest channel move.
- **Dark protection.** What a weight takes away is scaled by twice the share of the pixel's luma
  that lies above 0.30, at most 1: nothing at 0.30 or below, everything from 0.60 up. No pixel is
  taken below 0.30. So the median of a dark area cannot fall, and the darkness gate cannot fire
  because of a weight.
- **Clip guard.** The grey value lies between the smallest and the largest channel. With a
  channel at 0.02 or more it is too, and it rises above 0.98 only where the luma was there
  already. (A bound of 0.9795 for every pixel flattened kneed highlights: the texture measure of
  fact 9 fell from 0.26 to 0.14 on one photo.)
- **Order.** The last colour step, after the split-tone: nothing in a bundle adds colour after
  it. Per pixel, so it runs on row strips. 0.29 s on 22 megapixels (`saturation`: 0.22 s).

Add below `targeted_hsl`. `_CLIP_LO`, `_CLIP_HI` and `_EPS` exist.

```python
# Black and white (F24). Rec.709 luma of the six pure colours, in the order of the weights in
# BW_KEYS: red, yellow, green, cyan, blue, magenta. A weight of 0 leaves a colour at this value.
_BW_LUMA = (0.2126, 0.9278, 0.7152, 0.7874, 0.0722, 0.2848)
_BW_DARK = 0.30   # the gates' dark level: a weight never darkens a pixel at or below it, nor takes one below it
_BW_FULL = 2.0    # a weight darkens by this times the share of the luma above _BW_DARK: fully from 0.60 up


def _bw_coeff(weight: float, own: float) -> float:
    """How bright a pure colour comes out in grey: its own luma at 0, white at +100, black at -100."""
    w = min(max(float(weight), -100.0), 100.0) / 100.0
    return own + w * (1.0 - own) if w >= 0.0 else own * (1.0 + w)


def bw(rgb: np.ndarray, amt: float, red: float = 0.0, yellow: float = 0.0, green: float = 0.0,
       cyan: float = 0.0, blue: float = 0.0, magenta: float = 0.0) -> np.ndarray:
    """Black and white (F24) with a weight per colour. `amt` 0..100, each weight -100..100.
    (Write the rest of the docstring from the seven points above.)"""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    own = np.asarray(_BW_LUMA, np.float32)
    add = np.asarray([_bw_coeff(w, o) for w, o in zip((red, yellow, green, cyan, blue, magenta), _BW_LUMA)],
                     np.float32) - own                                # what each weight adds to its colour
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lo, hi = rgb.min(axis=-1), rgb.max(axis=-1)
    mid = np.maximum(np.minimum(r, g), np.minimum(np.maximum(r, g), b))   # the median, exact
    p = np.where(hi == r, 0, np.where(hi == g, 2, 4))    # the primary: red, green or blue
    s = np.where(lo == b, 1, np.where(lo == r, 3, 5))    # the secondary: yellow, cyan or magenta
    l = lo + (mid - lo) * own[s] + (hi - mid) * own[p]   # the pixel's luma
    e = (mid - lo) * add[s] + (hi - mid) * add[p]        # what the weights add to it
    e = np.where(e < 0.0, e * np.minimum(1.0, _BW_FULL * np.maximum(l - _BW_DARK, 0.0) / np.maximum(l, _EPS)), e)
    # Lower bound: the dark level (or the pixel's own luma below it), and 0.02 for a pixel that
    # has a channel there. Upper bound: 0.98, or the pixel's own luma above it. Each bound is the
    # pixel's own value where that is nearer, so it is continuous and a grey pixel is never moved.
    y = np.clip(l + e, np.maximum(np.minimum(l, _BW_DARK), np.minimum(hi, _CLIP_LO)),
                np.maximum(l, _CLIP_HI)).astype(np.float32)
    if a == 1.0:
        return np.repeat(y[..., None], 3, axis=-1)
    return (rgb + np.float32(a) * (y[..., None] - rgb)).astype(np.float32)
```

The median is taken with `minimum` and `maximum`, not as `r + g + b - lo - hi`, and the luma comes
from the same split, not from `luma(rgb)`: both keep a grey pixel exact.

Registrations:

- `BW_KEYS = ("bw", "bw_red", "bw_yellow", "bw_green", "bw_cyan", "bw_blue", "bw_magenta")` below
  `_HSL_KEYS`. Public: `pipeline.py` reads it.
- `OP_NAMES`: the seven names after the HSL names. `OP_BOUNDS`: `"bw": (0.0, 100.0)`, each
  weight `(-100.0, 100.0)`. Not in `SPATIAL_OPS`.
- `STEPS`: `BW_KEYS,` directly after `_GRADE_KEYS,`. In the line that builds `_COLOUR_KEYS`, add
  `BW_KEYS[0]` to the set `{"vibrance", _HSL_KEYS[0], _GRADE_KEYS[0]}`.
- `_colour_steps`, last lines before `return out`:

```python
    if settings.get("bw", 0.0):  # the last colour step: nothing in a bundle adds colour after it
        out = bw(out, settings["bw"], *(settings.get(k, 0.0) for k in BW_KEYS[1:]))
```

### 2. Looks and the pipeline

`looks.py`: after `grain`, the fields `dehaze_add: float = 0.0` and the seven of `BW_KEYS`, each
`float = 0.0`. A tuple `_BW_FIELDS` with the seven names, below `_GRADE_FIELDS`. In `load`, after
the `grain` line: `dehaze_add=float(body.get("dehaze_add", 0.0)),` and
`**{k: float(body.get(k, 0.0)) for k in _BW_FIELDS},`. In `shape`, after the `curve_add` lines
(global only, before the intensity loop):

```python
        if look.dehaze_add:
            settings["dehaze"] = settings.get("dehaze", 0.0) + look.dehaze_add
```

`shape` does not handle `bw`: the pipeline sets it, so it is never scaled.

`pipeline.py::process_one`, block A, directly after F23's lines that set `global_settings["grain"]`
(before the comment "Free-text look intent"):

```python
        # Black and white (F24) comes from the look, an instruction or --set, never from a decider.
        # It is taken as written, not scaled by intensity: a black and white look is black and
        # white at every strength.
        for s in (global_settings, *region_settings.values()):
            for k in ops.BW_KEYS:
                s.pop(k, None)
        bw_look = self.looks.get(look, looks_mod.Look())
        if bw_look.bw > 0.0:
            global_settings.update({k: getattr(bw_look, k) for k in ops.BW_KEYS if getattr(bw_look, k)})
```

Block B, directly after F23's lines that clamp the grain (before "Shoot-consistency"):

```python
        # Black and white (F24): the look's amount plus what instructions asked for, inside 0..100.
        # At 100 the photo ends neutral, so no palette grade is laid over it.
        bw_amount = min(max(global_settings.pop("bw", 0.0), 0.0), ops.OP_BOUNDS["bw"][1])
        bw_full = bw_amount >= ops.OP_BOUNDS["bw"][1]
        if bw_amount > 0.0:
            global_settings["bw"] = bw_amount
            weights = ", ".join(f"{k[3:]} {global_settings[k]:+g}" for k in ops.BW_KEYS[1:] if global_settings.get(k))
            policy_notes.append(f"black and white: {bw_amount:g} of 100" + (f" ({weights})" if weights else ""))
        if bw_full and palette_arg is not None:
            palette_arg, palette_note = None, "palette: none, the photo is black and white"
```

Block C, directly before the comment "Final banding verdict":

```python
        # A full black and white ends neutral (F24). Two steps after the global pass work against
        # the colour source and can hand a little of its colour back: the highlight hold and a
        # reference match. Every pixel is set to its own luma, so the tone they made stays.
        if bw_full and out_rgb is not None:
            out_rgb = np.repeat(luma(out_rgb)[..., None], 3, axis=-1)
```

Block C costs 0.08 s on 22 megapixels. The pipeline order does not change.

### 3. What cannot bring the colour back

- A region rule, or an instruction on a region or a layer (`saturation`, `vibrance`, `temp`):
  they render before the global pass (fact 2) and only change how the weights act there.
- The global bundle: `bw` is its last colour step.
- The skin guard: `bw` is not in `[guard] ops`, so a face is grey too.
- A decider (rules, learned, cloud): block A drops what it returns for the seven names.
- `--intensity`. An instruction: a typed delta can raise `bw`, not lower it (fact 10).
  `--set global.bw=100` makes any look black and white, `--set sky.bw=100` one region.
- The palette grade: skipped with a note (block B). The hold and a reference match: block C.
- A style: it only names a look, and `--look` wins over the style's look.

What can: another look, or `bw` below 100 in a look of the user's own. That is partly colour on
purpose, and blocks B and C do not act.

### 4. The gates and the looks

Measured on the 110 photos with the values below, with the functions the report uses
(`process_one`, `quality.assess_pixels`, the blend factors of `quality.toml`); eight cases were
checked against `render_safe` itself and agree. "Pulled back": the safe renderer blended the edit
toward the source (`safety_fallback` below 1). "More than `natural`": photos pulled back further
than under `natural`, with the reason.

| Look | Difference from `natural`, of 255 (luma part) | Pulled back (`natural`: 12) | More than `natural` | Flagged by `process_one` (`natural`: 6) |
|---|---|---|---|---|
| `bw_classic` | 11.0 (0.5) | 6 | 1: highlight texture | 1 |
| `bw_red_filter` | 11.8 (2.8) | 5 | 0 | 1 |
| `bw_soft` | 13.8 (6.3) | 6 | 1: highlight texture | 0 |
| `film_warm` | 4.0 (3.8) | 7 | 1: highlight texture | 1 |
| `film_cool` | 3.2 (2.2) | 7 | 2: highlight texture | 1 |
| `film_faded` | 9.1 (8.6) | 5 | 0 | 0 |
| `cross_process` | 2.3 (2.3) | 8 | 0 | 3 |
| `instant` | 6.3 (6.1) | 7 | 1: highlight texture | 1 |

Before the safe renderer blended anything, no look made a dark photo darker, and none pushed a
photo into clipping that `natural` did not (one photo, shadows). No look tripped the banding
gate; the audit's banding measure stayed below 0.03 (limit 0.35). Every look is pulled back on
fewer photos than `natural`, because `bw` holds the channels inside the clip levels and a fade
lifts them. The highlight texture cases are the photos of fact 9. Between two looks the smallest
difference is 2.0 of 255 (`film_warm` and `cross_process`, on 37 of the photos).

**A look passes** when the report (acceptance check 4) exits 0, the look is pulled back on no more
photos than `natural`, and a photo pulled back further than under `natural` has no new reason
other than `encoded_highlight_texture_loss`.

**When a look fails**, one change, then the report again:

- `dark_made_darker`: it cannot come from a weight. Set the look's `contrast_mul` and `curve_mul`
  to 1.0 and its `curve_add` to at most 5.0, the values of `natural`.
- `introduced_rgb_low_clipping` under a film look: the cast has too little fade under it. Lower
  `dehaze_add` by 2. The rule the values below keep: `0.00425 * -dehaze_add` is at least
  `0.0008 * max(-(t + n/2), n, t - n/2)`, the most the shadow cast takes from a channel, for
  `grade_shadows_temp = t` and `grade_shadows_tint = n`.
- `introduced_rgb_high_clipping`: halve `grade_highlights_temp` and `grade_highlights_tint`.
- `severe_region_boundary_artifact` under `bw_red_filter`: `bw_cyan` and `bw_blue` 10 nearer to 0.
- A second failure: take the look out of `looks.toml` and out of tests 12, 18 and 19, and list
  it as open. No look gets `preserve_exposure`, and no threshold moves.

## Looks as data

Append to `src/autoedit/configs/looks.toml`, with a blank line before each table. In the header
comment of the file, name the new fields: `dehaze_add` (global only: added to the rules' dehaze;
below zero it lifts the blacks), `grain` (F23), and `bw` with its six weights.

```toml
# ─────────────────────── black and white (F24) ───────────────────────
# bw = 100 turns the photo black and white. The six weights say how bright each colour comes
# out: 0 = as bright as the colour is, above 0 lighter, below 0 darker. A weight darkens only the
# brighter part of the picture (edit/ops.py::bw), so a blue sky goes dark and a shadow does not.
# bw and the weights are taken as written: the intensity does not scale them.
[bw_classic]                 # every colour at its own brightness; a little more depth than natural
bw = 100
curve_mul    = 1.4
curve_add    = 5.0
clarity_mul  = 1.2
dehaze_mul   = 0.8
[bw_red_filter]              # a red filter on the lens: blue and cyan darker (the sky), red and yellow lighter
bw = 100
bw_red     = 30
bw_yellow  = 15
bw_green   = -10
bw_cyan    = -45
bw_blue    = -60
bw_magenta = 10
curve_mul    = 1.4
curve_add    = 5.0
clarity_mul  = 1.2
dehaze_mul   = 0.8
[bw_soft]                    # low contrast, blacks lifted by 7 of 255, skin a little lighter
bw = 100
bw_red     = 15
bw_yellow  = 10
contrast_mul = 0.7
curve_mul    = 0.6
curve_add    = -6.0          # below zero: softer mid-tones, both ends stay
clarity_mul  = 0.5
dehaze_mul   = 0.0           # no haze removal, so the lift is the same on every photo
dehaze_add   = -8.0
# ─────────────────────── film looks (F24) ───────────────────────
# Named by what they do; no film stock is named or imitated. A cast needs lifted blacks under it
# (dehaze_add below zero), or the channel it lowers falls under the clip level in the shadows.
# The oversaturation gate lets a cast of about 5 of 255 through, so the casts are mild.
# No look has grain (F23) until the owner says so. Proposed: grain = 20 for film_warm and
# film_cool, 30 for film_faded, 15 for cross_process, 25 for instant.
[film_warm]                  # warm and soft: a warm tone, blacks lifted by 6, a little less colour
contrast_mul = 0.85
curve_add    = 2.0
clarity_mul  = 0.6
dehaze_mul   = 0.5
dehaze_add   = -7.0
saturation_add = -6.0
grade_shadows_temp = 8.0
grade_highlights_temp = 15.0
grade_highlights_tint = 2.0
[film_cool]                  # cool and bleached: blue in the shadows, a quarter less colour
contrast_mul = 1.0
curve_add    = 5.0
clarity_mul  = 0.9
dehaze_mul   = 0.8
dehaze_add   = -5.0
saturation_add = -24.0
grade_shadows_temp = -15.0
grade_shadows_tint = -3.0
grade_highlights_temp = -10.0
[film_faded]                 # an old print: blacks lifted by 12 to a grey, low contrast, much less colour
contrast_mul = 0.75
curve_mul    = 0.5
curve_add    = -6.0
clarity_mul  = 0.5
dehaze_mul   = 0.0
dehaze_add   = -14.0
saturation_add = -18.0
grade_shadows_temp = -5.0
grade_highlights_temp = 5.0
[cross_process]              # slide film developed as a negative: blue-green shadows, yellow-green
contrast_mul = 1.2           # highlights, a little more contrast. The mildest look: no extra colour,
curve_mul    = 1.5           # because the oversaturation gate takes that back.
curve_add    = 5.0
dehaze_add   = -6.0
grade_shadows_temp = -14.0
grade_shadows_tint = -8.0
grade_highlights_temp = 8.0
grade_highlights_tint = -4.0
[instant]                    # an instant print: soft, blacks lifted by 9, cool green shadows, warm highlights
contrast_mul = 0.85
curve_add    = -3.0
clarity_mul  = 0.5
dehaze_mul   = 0.0
dehaze_add   = -10.0
saturation_add = -10.0
grade_shadows_temp = -8.0
grade_shadows_tint = -5.0
grade_highlights_temp = 10.0
grade_highlights_tint = 2.0
```

Chosen with `--look NAME`, `look = "NAME"` under `[run]` in a run file, or `look` in an API
request. `autoedit looks list` and the settings schema show the looks without more code.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/edit/ops.py` | `bw`, `_bw_coeff`, three constants, `BW_KEYS`, the registrations, two lines in `_colour_steps` | T1 |
| `tests/test_parallel.py` | the bundle of `test_a_bundle_can_be_cut_between_any_two_steps` gains `"bw": 60.0, "bw_red": 40.0, "bw_yellow": 25.0, "bw_green": -20.0, "bw_cyan": -80.0, "bw_blue": -90.0, "bw_magenta": 15.0` | T1 |
| `tests/test_bw.py` | new: tests 1 to 11 (T1), 12 to 19 (T2) | T1, T2 |
| `src/autoedit/looks.py` | eight fields, `_BW_FIELDS`, two lines in `load`, two in `shape` | T2 |
| `src/autoedit/pipeline.py` | blocks A, B and C | T2 |
| `src/autoedit/configs/looks.toml` | the eight looks, the header lines | T2 |
| `tests/snapshots/settings_schema.json` | regenerated: eight more choices of `look` | T2 |

F23 pieces this relies on: the field `Look.grain` and its line in `load` (the new ones follow
them), the two grain blocks in `process_one` (blocks A and B follow them), `("grain",)` as the
last step of `STEPS`, and the assertion in `tests/test_grain.py` that no packaged look has grain.
Nothing of F23 is changed.

## Tasks

One after the other. `.venv` has no pytest; there the proving command is `tests/run_all.py`.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high (protected file) | Design 1; tests 1 to 11; the bundle in `tests/test_parallel.py` | `PYTHONPATH=src python3 -m pytest tests/test_bw.py tests/test_parallel.py tests/test_ops.py tests/test_ops_phase2.py tests/test_levels.py tests/test_denoise.py tests/test_grain.py tests/test_llm_refresh.py -q` |
| T2 | Builder, Sonnet, high (`pipeline.py`) | Design 2; "Looks as data"; tests 12 to 19; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_bw.py tests/test_styles.py tests/test_defaults.py tests/test_profile.py tests/test_calibrate.py tests/test_settings_schema.py tests/test_pipeline.py tests/test_grain.py tests/test_tone_stack.py tests/test_adjust.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec (`ops.py` and `pipeline.py` are protected), the definition of done, the sheets | none |
| Docs | The session, inline | `ARCHITECTURE.md`: section 6 (the new look fields; "a look never adds new decisions" now has two exceptions, grain and black and white) and section 8 (`→ color grade → black and white → denoise`, a bullet for `bw`); `APP_GUIDE.md` (the look controls, the `looks.toml` row); an example with `--look bw_red_filter` in `README.md`; `CHANGELOG.md`; the plan row | none |

## Tests

`tests/test_bw.py`, without `pytest.mark` (fact 12). Weight sets, in the op's order: `NEUTRAL`
six zeros; `RED_CHANNEL = (100, 100, -100, -100, -100, 100)`; `RED_FILTER = (30, 15, -10, -45,
-60, 10)`; `WEIGHTS` is those three, six times -100, six times 100, and `(37, -12, 55, -80, 3,
-64)`. Frames are seeded random float32 in 0..1 unless said. "Neutral": the three channels are
`array_equal`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_zero_is_the_identity` | `ops.bw(x, 0) is x`, also with weights; `apply_ops(x, {"bw": 0.0}) is x`; `apply_ops(x, {"bw_red": 50.0, "bw_blue": -50.0})` equals `x` |
| 2 | `test_grey_input_is_unchanged` | 500 random greys and 0, 0.02, 0.0203, 0.0205, 0.5, 0.9795, 0.9797, 0.98, 1: `array_equal` to the input for every set of `WEIGHTS`, at 100 and at 60 |
| 3 | `test_neutral_weights_keep_the_luma` | Amount 100: neutral, float32, same shape; equal to `luma(x)` within 1e-6 where the largest channel and the luma are 0.0205 or more. Amount 50 on values of 0.05..0.95: the luma is kept within 1e-6 and `max - min` is half |
| 4 | `test_a_weight_moves_its_own_colour_only` | Six patches `0.4 + 0.5 * colour`. One weight at -100, -50, 0, 50, 100: the five other patches stay within 1e-6; its own patch rises with the weight, is 0.9 at +100 and `l - (l - 0.4) * min(1, 2 * (l - 0.30) / l)` at -100, with `l` its luma |
| 5 | `test_a_red_filter_darkens_a_blue_sky` | `RED_CHANNEL` on values of 0.05..0.95, with `red` the red channel and `l` the luma: the result is `red` where `red >= l`, else `max(l - (l - red) * min(1, 2 * max(l - 0.30, 0) / l), min(l, 0.30))`, within 1e-6. `RED_FILTER`: sky `(0.35, 0.55, 0.85)` is more than 0.06 darker than with `NEUTRAL` (0.529 to 0.457), skin `(0.85, 0.62, 0.50)` more than 0.02 lighter (0.660 to 0.716), foliage `(0.30, 0.45, 0.20)` within 0.02 |
| 6 | `test_a_dark_pixel_is_never_made_darker` | For every set of `WEIGHTS`, with `l` the luma: where `l <= 0.30` the grey value is at least `l`; where `l > 0.30` it is at least 0.30 (both within 1e-6). A frame of values 0..0.45 (median luma below 0.30): its median does not fall |
| 7 | `test_the_grey_value_lies_between_the_channels` | `min <= y <= max` for every set |
| 8 | `test_continuous_across_band_edges` | 200,000 pixels, of them 100,000 with two channels within 1e-4 of each other, 40,000 within 0.03 of black or white, 20,000 with values 0.25..0.35. Each moved by at most 1e-4 per channel: the grey value moves by at most 3e-4, for every set |
| 9 | `test_nothing_is_pushed_into_clipping` | A frame with many values near 0 and 1, amounts 100 and 50, every set: no channel and no luma goes from 0.02 or more to below; no luma from 0.98 or less to above; a channel goes above 0.98 only where the luma was above |
| 10 | `test_shapes_and_small_frames` | 1 by 1, 16 by 16, 33 by 70, 301 by 127, amounts 100 and 40: same shape, float32, finite, inside 0..1 |
| 11 | `test_registered_as_the_last_colour_step` | `BW_KEYS` as above; the bounds; no name in `SPATIAL_OPS`, all in `_COLOUR_KEYS`; the step sits between `_GRADE_KEYS` and `("denoise",)`. A bundle of vibrance, saturation, `hsl_blue_sat`, both grade temps, `bw` 100 with three weights, and `sharpen` 20: neutral, and equal to `sharpen(bw(apply_ops(x, the colour ops), 100, the weights), 20)` |
| 12 | `test_the_default_is_unchanged` | `Look()` has 0.0 in the eight new fields; `looks._BW_FIELDS == ops.BW_KEYS`; the packaged looks are the five old and the eight new; only the three `bw_` looks have a value in `BW_KEYS`; the five old have `dehaze_add == 0`; `natural == Look(clarity_mul=0.7, dehaze_mul=0.8, curve_add=5.0)`; no look has `preserve_exposure` or `grain`. A default stub render: no `bw` key, no note that starts with "black and white", colour in the picture |
| 13 | `test_dehaze_add_is_global_and_scaled` | `Look(dehaze_mul=0.5, dehaze_add=-8.0)` on `{"dehaze": 10.0}` at 0.8: `(10 * 0.5 - 8) * 0.8` global, `10 * 0.5 * 0.8` for a region; on `{}` at 1.0 global: `{"dehaze": -8.0}`; a default look changes nothing |
| 14 | `test_a_bw_look_ends_neutral` | The three looks at intensity 0.3, 0.8 and 1.5: neutral; the seven values in `global_settings` are the look's; the note, for `bw_red_filter` word for word `black and white: 100 of 100 (red +30, yellow +15, green -10, cyan -45, blue -60, magenta +10)`. With `palette="warm_gold"`: neutral, the note `palette: none, the photo is black and white`, no note that starts with `palette: warm_gold` (a run without the look has one). With `highlight_hold=True`: neutral, and a note that starts with "highlight hold". With `reference=` a colour photo: neutral. `--set global.saturation=-100` on `natural` is not neutral (the skin stays) |
| 15 | `test_a_decider_cannot_set_or_drop_it` | A decider that adds `bw: 60, bw_red: 30` everywhere, look `natural`: no name of `BW_KEYS` in the global or a region setting, colour in the picture. One that adds `bw: 10, bw_blue: -90`, look `bw_classic`: `bw == 100`, no `bw_blue`, neutral |
| 16 | `test_region_edits_cannot_bring_colour_back` | `bw_red_filter` with `sky.saturation=80`, `skin.temp=40`, `global.vibrance=60`, `global.grade_shadows_temp=-60` and the prompt "vibrant warm sunset": neutral; the four adjustments are applied or clamped |
| 17 | `test_set_and_instructions_reach_the_op` | `global.bw=100`: neutral, `bw == 100`; twice: still 100. `bw_classic` with `global.bw=-40`: still 100 and neutral, status `clamped`. `global.bw=-40`, or `global.bw_blue=-90`, on `natural`: no `bw`, colour. `sky.bw=100`: applied, no global `bw`; rows 70 to 130 of the scene have a mean `max - min` below 0.01 and the rows from 300 on above 0.05 |
| 18 | `test_look_golden` | For each look: `shape(dict(GOLDEN_BASE), look, 1.0, is_global=True)` plus the look's non-zero `BW_KEYS` has the keys and the values of `GOLDEN[name]` (each value with `pytest.approx`). At intensity 0.5 the `bw` values are the same |
| 19 | `test_looks_do_what_their_names_say` | The chart below through `apply_ops` with the shaped settings at 0.8, clipped to 0..1; the asserts are listed under the table |

The scene of tests 12 to 17 is `tests/test_grain.py::_scene` (a 600 by 400 JPEG: sky over
foliage, a skin-coloured oval) plus `a[:60, :200] = [255, 250, 225]`, a blown warm patch for the
hold. `GOLDEN_BASE = {"exposure": 0.4, "contrast": 10.0, "curve": 10.0, "clarity": 10.0,
"dehaze": 10.0, "vibrance": 4.0}`. `GOLDEN[name]` is the row below as a dict of op names (the
four `grade_*` ops, `bw`, `bw_red` and so on), plus `exposure 0.4` and `vibrance 4.0`. An empty
cell is a key that is absent.

| Look | contrast | curve | clarity | dehaze | saturation | grade shadows temp, tint | grade highlights temp, tint | bw and weights |
|---|---|---|---|---|---|---|---|---|
| `bw_classic` | 10.0 | 19.0 | 12.0 | 8.0 | | | | bw 100 |
| `bw_red_filter` | 10.0 | 19.0 | 12.0 | 8.0 | | | | bw 100, red 30, yellow 15, green -10, cyan -45, blue -60, magenta 10 |
| `bw_soft` | 7.0 | 0.0 | 5.0 | -8.0 | | | | bw 100, red 15, yellow 10 |
| `film_warm` | 8.5 | 12.0 | 6.0 | -2.0 | -6.0 | 8 | 15, 2 | |
| `film_cool` | 10.0 | 15.0 | 9.0 | 3.0 | -24.0 | -15, -3 | -10 | |
| `film_faded` | 7.5 | -1.0 | 5.0 | -14.0 | -18.0 | -5 | 5 | |
| `cross_process` | 12.0 | 20.0 | 10.0 | 4.0 | | -14, -8 | 8, -4 | |
| `instant` | 8.5 | 7.0 | 5.0 | -10.0 | -10.0 | -8, -5 | 10, 2 | |

Test 19. The chart is two rows of 101 pixels: a grey ramp 0..1, and patches of sky `(0.35, 0.55,
0.85)`, skin `(0.85, 0.62, 0.50)` and foliage `(0.30, 0.45, 0.20)`, 34, 34 and 33 wide. With the
base `{"curve": 10.0}`: `black` is the luma at ramp 0, `warm` is R minus B at ramp 15 and at ramp
80, `colour` the mean `max - min` of the patch row. With the base `{"contrast": 8.0, "curve":
10.0}`: `spread` is the luma at ramp 75 minus that at ramp 25. Measured values are in brackets.

- The three `bw_` looks are neutral. `black` is 0 for `natural`, `bw_classic`, `bw_red_filter`.
- `bw_red_filter`: sky more than 0.05 darker than under `bw_classic` (0.459, 0.533), skin more
  than 0.02 lighter (0.736, 0.679).
- `bw_soft`: `black` above 0.02 (0.027), `spread` more than 0.03 below `bw_classic` (0.506,
  0.577), skin lighter.
- `film_warm`: `warm` above 0.004 at both places (0.0070, 0.0121). `film_cool`: below -0.008 and
  below -0.002 (-0.0135, -0.0081). Both have less `colour` than `natural`.
- `film_faded`: `black` above 0.04 (0.047), `spread` more than 0.03 below `natural` (0.493,
  0.567), `colour` below 0.85 of `natural` (0.81).
- `cross_process`: `spread` more than 0.002 above `natural` (0.5716, 0.5672), `warm` below -0.008
  and above 0.004 (-0.0126, 0.0066), and green above blue at ramp 80.
- `instant`: `black` above 0.03 (0.035), `warm` below -0.004 and above 0.004 (-0.0067, 0.0079).
- Every film look: `black` above 0.01, and no channel of the pixel at ramp 0 is 0.

Changed in existing tests, nothing else. On the scratch build exactly these two failed first:
the bundle in `tests/test_parallel.py`, and `tests/snapshots/settings_schema.json`.

## Acceptance checks (the session runs these)

1. `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py`
   are green.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Once more into a new
   folder with `--look bw_red_filter`: every export has three equal channels when it is opened
   again, and each sidecar has `bw: 100` and the note.
3. Default output is byte-identical to the baseline renders (the reference photos and the smoke
   corpus, maximum difference 0). On the scratch build: 50 renders of 10 corpus photos under the
   five old looks, and the smoke exports. Expected and not pixels: `config_digest` changes
   because `looks.toml` grew, so `autoedit adjust` on an older export prints its usual warning.
4. The tone gate of `CLAUDE.md`, once for `natural` and once per look, in the background, up to
   three at a time, about 10 minutes each (`out-features/F24/` must exist):
   `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression --set look=NAME --json out-features/F24/tone-NAME.json`.
   Each exits 0: 0 dark to darker, 0 pushed into clipping. Then, from the nine files, per look
   and photo, `safety_fallback` against the `natural` run, judged by the rule in Design 4.
   Expected: the table there. The reason for one photo is
   `render_safe(photo, RunConfig(look=NAME), image=img, common_source=img)[1].audit["proposal_assessment"]["failures"]`
   (`quality_runner`), with `img` the photo loaded and brought to 1024 px by
   `imageio.downscale_long_edge`.
5. The sheets for the owner, in `out-features/F24/`, made with a small script of the session
   that is not committed. Twelve photos of the corpus, by the first four digits of their file
   names: 0002, 0028, 0042 (blue sky), 0039 (bright), 0057, 0103, 0119 (snow), 0041 (green), 0061
   (neutral), 0095 (warm), 0100, 0109 (dark). Each is rendered at full size through
   `Pipeline.process_one` and written with `emit.write_image`, as the product does. With SAM 3
   through `.venv/bin/python` when it is ready (one pipeline per look, all sharing one segmenter
   wrapped in `contact_sheet.DiskSegmenter`, so a photo is segmented once), else with the stub;
   the caption says which. Laid out with `contact_sheet._write_sheets`, four photos to a sheet.
   `bw_NN.jpg`: the original, `natural`, the three black and white looks. `film_NN.jpg`:
   `natural`, the five film looks. Each caption: the look, its mean difference from `natural` in
   levels of 255, and the review flag. The exports stay next to the sheets.
6. The differences of check 5, per look over the twelve photos, go into the "Result" section. A
   look below 2 of 255 is named there as not plainly different.
7. `ops.bw` takes about 0.3 s on a 22 megapixel frame, and a default run takes no longer.
8. The Checker's review, and its look at the sheets: every black and white cell is neutral, a
   face is grey, the sky under `bw_red_filter` is darker than under `bw_classic`, and no film
   look shows a coloured edge round a face or along snow.

## Known limits

- The film casts are mild (fact 6): 1 to 3.5 of 255 in the op, less after the gate on a photo
  that already leans that way, none on skin and snow (the skin guard holds the split-tone off
  them). `film_cool` and `cross_process` are the mildest looks: 3.2 and 2.3 of 255 from `natural`.
  A cast to see at a glance needs an allowance for looks in the oversaturation gate and a second
  look at the pixel audit: the owner's call, not built.
- The red filter darkens a mid-blue sky by about 20 of 255, and nothing below 0.30. Shadows keep
  their tone.
- A grey picture scores lower on the audit's highlight texture measure (fact 9). The safety
  workflow therefore pulls back a photo or two that it accepts in colour, and what it pulls back
  shows colour again. `autoedit run` and Studio do not blend.
- The gate numbers were measured with the stub, as the tone gate is. Its regions are colour
  thresholds; with SAM 3 the mask edges lie elsewhere.
- A fade under `--levels` is partly undone: the black point pulls a lifted floor back toward 0.03.
- `bw` cannot be typed: the prompt words are the old desaturation, and the instruction parser
  splits "black and white" at "and". `--look` and `--set global.bw=100` are the ways.
- The cloud decider is still offered the seven op names; what it returns for them is dropped.
- A reference photo with a black and white look: the tone follows it, the colour does not.

## Proposed plan row

`| F24 | Black and white, and film looks | M | done, off by default (`--look bw_classic`, `bw_red_filter`, `bw_soft`, `film_warm`, `film_cool`, `film_faded`, `cross_process`, `instant`); the owner looks at the sheets in `out-features/F24/` |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Which of the eight looks stay, and are the casts of `film_warm`, `film_cool`
  and `cross_process` strong enough?" Evidence: the sheets. The change: the values in
  `looks.toml`; a stronger cast is a feature of its own.
- For the owner: "A stronger red filter (a sky from 0.53 to 0.40, not to 0.46)?" and "Should
  the prompt words 'black and white' use the new op?" Evidence and change: the decisions table.
- Changed against the plan: "Six colour weights, not four." Why: a blue sky is part cyan; with
  four bands the filter would reach only its blue part. Reverse it by: none intended.
- Changed against the plan: "A weight darkens only the brighter part of the picture, and no look
  uses `preserve_exposure`." Why: the red filter made 2 of 110 dark photos darker, and the flag
  is an open owner decision. Reverse it by: the row in the decisions table.
- Changed against the plan: "A fade is a negative `dehaze` (look field `dehaze_add`), not a
  lifted black point or a curve." Why: fact 4. Reverse it by: none intended.
- Changed against the plan: "`halation` and a toned black and white were not built." Why: the
  decisions table.

## Result

Built on 2026-11-17 in the unattended run. Status: done, off by default (the eight looks are
chosen with `--look`).

- **Built** by two Builders (Sonnet, high) from this spec, which the Planner (Fable, max) wrote
  after building and measuring the feature once on a scratch copy. The Checker (Opus, high) found
  nothing that had to be fixed. Its three should-fix findings were fixed by the session: colour
  weights given without `bw` (`--set global.bw_blue=-90`) are now dropped with a note, where they
  used to be reported as applied while changing nothing; test 14 checks the values of all three
  black and white looks at three intensities; test 4 asserts that a patch rises strictly with its
  weight. Notes kept: the cloud decider is still offered the seven names and block A drops them
  (a known limit of this spec).
- **Check 1:** both suites green (the count is in the closing line of the run report).
- **Checks 2 and 3:** the stub smoke run completes; default exports are byte-identical to the
  baseline. The Checker compared the committed code with the working tree on the smoke corpus and
  a synthetic photo under no look and each of the five old looks, through `process_one`, the
  export, `adjust`, the preview and the variants: identical. `--look bw_red_filter` exports have
  three equal channels and the note `black and white: 100 of 100 (red +30, yellow +15, green -10,
  cyan -45, blue -60, magenta +10)`.
- **Check 4, the tone gate** on the 110 RAW photos, once for `natural` and once per look: every
  run 0 dark made darker, 0 pushed into clipping, 0 hard failures. Pulled back by the safety
  workflow: `natural` 12 photos; the looks 5 to 8. Pulled back further than under `natural`: 0086
  (`bw_classic`, `film_cool`) and 0103 (`bw_soft`, `film_warm`, `film_cool`, `instant`), the two
  photos the spec named, whose texture measure sits near its limit under `natural` already.
  Reports: `out-features/F24/tone-*.out` and `.json`.
- **Checks 5 and 6, the sheets** (`out-features/F24/bw_01..04.jpg`, `film_01..04.jpg`, made with
  SAM 3, three photos to a sheet because of the sheet height cap). Mean difference from `natural`
  over the twelve photos, in levels of 255: bw_classic 15.7, bw_red_filter 17.2, bw_soft 18.5,
  film_warm 4.6, film_cool 4.2, film_faded 10.5, cross_process 2.6 (1.3 on 0119), instant 7.3. No
  look is below 2 on average; `cross_process` and `film_cool` fall below 2 on single photos. All
  black and white cells are exactly grey. The red filter makes the top fifth of a blue-sky photo
  7 to 18 levels darker than `bw_classic`. The corpus has no faces, so "a face is grey" was not
  seen on a real photo (the synthetic test covers it).
- **Check 7:** `ops.bw` took 0.89 s on a 22 megapixel frame, measured while three tone reports ran
  on the same machine; the 0.3 s of the spec was not confirmed under that load.
- **Check 8:** the Checker's review is above; the session looked at the sheets.
