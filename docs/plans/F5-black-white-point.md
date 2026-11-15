# F5. Black and white point

Status: spec (2026-11-11). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F5.
Comes after [F4](F4-throughput.md) (`ops.STEPS`, `par.rows`).

## Goal

A washed frame keeps a grey floor today: nothing in the op chain sets a true black. This feature
adds a levels-style op that measures where the darkest and brightest tones of the frame sit and
pulls them to a true black and a clean white, with pinned ends and soft knees.

It changes default pixels, so it ships **off** (`levels = false`). The owner's blind vote decides
whether it becomes a default.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Is the feature on by default? | No. `--levels` or `[run] levels = true` turns it on | Set `levels = true` in `defaults.toml` after the blind vote |
| Does a rule drive the white point? | No. The op has both ends; the rules set `black_point` only. `white_point` is available to `autoedit adjust --set global.white_point=50` | Add a `white_point` rule to the global blocks in `rules.toml` |
| Do explicit adjustments need the setting? | No. `--set global.black_point=60` and instructions work with `levels = false` | Strip the two ops after `instructions.plan` as well |

## The op

Two op names, each a strength from 0 to 100: `black_point` and `white_point`. One function,
`ops.levels`, at a fixed place in the frozen order: after `clarity`, before `vibrance`. All tone
ops have run by then, so the op measures the floor they actually produced (an exposure lift raises
the floor with everything else), and the colour ops still see its result.

**It measures its own input.** The black point `f` is the 0.1th percentile of the luma, the white
point `w` the 99.9th, taken on every fourth pixel in both directions. A rule cannot know these
numbers: it sees the source statistics, not the frame after the lifts.

**Black side.** With strength `s` (0 to 1), the amount removed at the floor is

    b = min(s * (f - 0.03), 0.3 * (p - f)),  never below 0      (p: the pivot, see below)

- `0.03` is the target floor. The statistics and gates count a channel below `0.02` as clipped, so
  the target sits just inside.
- The second term keeps the slope at the floor at 1.6 or less and lets the op fade out on high-key
  frames and on frames whose dark end is flat (no effect when the floor is at the pivot).
- A frame that already has a true black (`f <= 0.03`) is not touched.

The luma curve, with pivot `p = min(0.5, the 10th percentile of the luma)`:

    l >= p        y = l                                   identity above the pivot
    f <= l < p    y = l - b * ((p - l) / (p - f))^2       b removed at the floor, nothing at the pivot
    l < f         y = (f - b) * (l / f)^g,  g = m * f / (f - b),  m = 1 + 2 * b / (p - f)

The toe below `f` holds 0 at 0 and matches the value and the slope `m` at `f`, so the curve is
continuous with a continuous slope, and monotonic. Only 0.1 % of the pixels lie below `f`.

**White side.** The mirror image on `u = 1 - l`: the amount added at the measured white is
`a = min(s * (0.94 - w), 0.3 * (q - (1 - w)))`, never below 0, with the pivot
`q = min(0.5, 1 - the 90th percentile)`. `0.94` sits inside the highlight
clip level `0.98`.

**Colour and clipping.** The curve is applied as a luma ratio to all three channels, like `tone`
and `curve`. The ratio is limited per pixel so that a channel inside `[0.0205, 0.9795]` stays
inside: the op cannot add shadow or highlight clipping, by construction.

**No effect at 0.** `levels(rgb, 0, 0)` returns its input object.

**Why the pivot is the frame's own 10th percentile (changed after the first tone gate).** The
first build used a fixed pivot at mid-grey. The curve then lowered every tone below 0.5 a little,
and on dark photos that took up to 0.03 from the median of the bottom third. The quality gate
does not allow a dark source to come out darker, so the safe renderer pulled the whole edit back
on three of the 110 corpus photos (blend factors 0.5, 0.5 and 0.25: those photos lost half of
their edit or more). With the pivot at the 10th percentile the curve reshapes only the darkest
tenth of the frame. The median of any area that covers a quarter of the frame or more cannot
move, so the op cannot trip that gate. The slope cap then also leaves a frame alone whose darkest
tenth is one flat tone (fog), where there is no black to anchor.

## Files

| File | Change |
|---|---|
| `src/autoedit/edit/ops.py` | `levels` and its helpers; the two names in `OP_NAMES`, `OP_BOUNDS`, `STEPS`, `SPATIAL_OPS`; one line in `apply_ops` |
| `src/autoedit/contract.py` | `RunConfig.levels: bool = False` |
| `src/autoedit/pipeline.py` | strip the two ops after `decide` unless `cfg.levels` |
| `src/autoedit/cli.py`, `src/autoedit/web/service.py`, `src/autoedit/configs/defaults.toml` | `--levels`, `[run] levels`, `"levels"` in `_ALLOWED` |
| `src/autoedit/configs/rules.toml`, `src/autoedit/rules.py` | the opt-in rule set `[opt_in.levels.*]`; `rules.load(path, enable=())` |
| `scripts/tone_report.py` | `--set KEY=VALUE` (repeatable) for extra `RunConfig` fields |
| `tests/test_levels.py` | new |

## `edit/ops.py`

The code below is the first build. The pivots `p` and `q`, the measurement of the 10th and 90th
percentile and the luma bound came later; `ops.py` is the reference.

Protected file. Add the constants and functions below `curve`. Nothing else in the file changes
except the four registrations and the one line in `apply_ops`.

```python
# Black and white point (F5). The targets sit inside the clip levels the stats and the gates use
# (a channel below 0.02 or above 0.98 counts as clipped), so the op cannot add clipping.
_BLACK_FLOOR = 0.03
_WHITE_CEIL = 0.94
_LEVELS_PIVOT = 0.5      # the curve is the identity on the far side of mid-grey
_LEVELS_TAIL = 0.1       # percent of the pixels allowed beyond a measured point
_LEVELS_SLOPE = 0.3      # caps the amount so the slope at a measured point stays <= 1.6
_CLIP_LO, _CLIP_HI = 0.0205, 0.9795


def _levels_points(l: np.ndarray) -> tuple[float, float]:
    """Measured black and white point of a luma plane: the 0.1th and 99.9th percentile, on every
    fourth pixel in both directions (1.4 M samples of a 22 MP frame)."""
    s = l[::4, ::4] if l.ndim == 2 and min(l.shape) >= 256 else l
    lo, hi = np.percentile(s, (_LEVELS_TAIL, 100.0 - _LEVELS_TAIL))
    return float(lo), float(hi)


def _levels_curve(l: np.ndarray, f: float, b: float, w: float, a: float) -> np.ndarray:
    """The luma map. Black side: remove `b` at the floor `f`, fading to nothing at the pivot, with
    a power toe below `f` that keeps 0 at 0. White side: add `a` at the white `w`, the mirror
    image. Monotonic, continuous slope, the identity when b == a == 0."""
    p = _LEVELS_PIVOT
    y = l
    if b > 0.0:
        yf, m = f - b, 1.0 + 2.0 * b / (p - f)
        mid = l - b * np.square(np.clip((p - l) / (p - f), 0.0, None))
        toe = yf * np.power(np.clip(l / f, 0.0, 1.0), m * f / yf)
        y = np.where(l < f, toe, np.where(l < p, mid, l))
    if a > 0.0:
        u, uf = 1.0 - l, 1.0 - w
        vf, m = uf - a, 1.0 + 2.0 * a / (p - uf)
        mid = u - a * np.square(np.clip((p - u) / (p - uf), 0.0, None))
        toe = vf * np.power(np.clip(u / uf, 0.0, 1.0), m * uf / vf)
        y = np.where(u < uf, 1.0 - toe, np.where(u < p, 1.0 - mid, y))
    return y.astype(np.float32)


def _levels_apply(rgb: np.ndarray, f: float, b: float, w: float, a: float) -> np.ndarray:
    l = luma(rgb)
    ratio = (_levels_curve(l, f, b, w, a) + _EPS) / (l + _EPS)
    lo, hi = rgb.min(axis=-1), rgb.max(axis=-1)
    # a channel that is inside the clip levels stays inside (darkening, then brightening)
    ratio = np.where(lo >= _CLIP_LO, np.maximum(ratio, _CLIP_LO / np.maximum(lo, _EPS)), ratio)
    ratio = np.where(hi <= _CLIP_HI, np.minimum(ratio, _CLIP_HI / np.maximum(hi, _EPS)), ratio)
    return (rgb * ratio[..., None]).astype(np.float32)


def levels(rgb: np.ndarray, black: float = 0.0, white: float = 0.0) -> np.ndarray:
    """Black and white point, each a strength 0..100. The op measures where the darkest and the
    brightest 0.1 % of ITS INPUT begin and pulls those points toward a true black (0.03) and a
    clean white (0.94). A frame that already has them is not touched; 0 and 0 is the identity.
    The measurement reads the whole frame, so this op never runs on a strip or a crop; the
    per-pixel map does."""
    if black <= 0.0 and white <= 0.0:
        return rgb
    f, w = _levels_points(luma(rgb))
    p = _LEVELS_PIVOT
    b = max(0.0, min(min(black, 100.0) / 100.0 * (f - _BLACK_FLOOR), _LEVELS_SLOPE * (p - f))) if black > 0.0 else 0.0
    a = max(0.0, min(min(white, 100.0) / 100.0 * (_WHITE_CEIL - w), _LEVELS_SLOPE * (p - (1.0 - w)))) if white > 0.0 else 0.0
    if b <= 0.0 and a <= 0.0:
        return rgb
    return par.rows(lambda x: _levels_apply(x, f, b, w, a), rgb)
```

Registrations:

- `OP_NAMES`: add `"black_point", "white_point"` after `"clarity"`.
- `OP_BOUNDS`: `(0.0, 100.0)` for both.
- `STEPS`: a new step `("black_point", "white_point")` between `("clarity",)` and
  `("vibrance", "saturation")`. `_COLOUR_KEYS` then starts one step later; build it from the step
  names, not from positions: the steps holding `vibrance`, the HSL keys and the grade keys.
- `SPATIAL_OPS`: add both (they read a whole-frame statistic).
- `apply_ops`, after the `clarity` line:

```python
    if settings.get("black_point", 0.0) or settings.get("white_point", 0.0):
        out = levels(out, settings.get("black_point", 0.0), settings.get("white_point", 0.0))
```

## `pipeline.py`

Next to the existing `if not cfg.sharpen:` block:

```python
        if not cfg.levels:  # black and white point is opt-in until it has passed a blind vote (F5)
            for s in (global_settings, *region_settings.values()):
                s.pop("black_point", None)
                s.pop("white_point", None)
```

It sits before the instruction plan, so an explicit adjustment or instruction that names one of
the two ops is kept.

## `rules.toml` and `rules.py`: opt-in rule sets

The rule must not sit in the scene blocks: tools and tests read `rules.toml` directly, without the
pipeline, and would then apply a feature that is switched off. So the feature's rule lives in an
*opt-in rule set*:

```toml
[opt_in.levels.people.global]
black_point = "if(pro_stats_valid > 0, 100 * max(clamp((if(band_bottom_p50 > 0, min(fg_p50, band_bottom_p50), fg_p50) - 0.30) / 0.08, 0, 1), clamp((planned_exposure - 0.15) / 0.35, 0, 1)), 0)"
```

and the same for `landscape` and `other`. `rules.load(path, enable=())` removes the `opt_in` table
from what it returns and merges `[opt_in.NAME.<scene>.<block>]` into `[<scene>.<block>]` for every
NAME in `enable`. `Pipeline.build` enables the sets whose run setting is on
(`_OPT_IN_RULES = ("levels",)`). A plain `rules.load(path)` never sees the rule.

The session tunes the constants with the tone gate after the build. A golden test pins the rule.

## `scripts/tone_report.py`

`--set KEY=VALUE`, repeatable: each pair becomes a `RunConfig` field for `--render`. The value is
parsed with `json.loads` and kept as a string when that fails (`--set levels=true`,
`--set intensity=1.0`, `--set raw_mode=preview_ev_headroom`). An unknown key is an argparse error.

## Tasks

One Builder (Sonnet, high: `ops.py` is a protected file).

| Task | Content | Proving command |
|---|---|---|
| T1 | everything in the Files table, tests 1 to 12 | `PYTHONPATH=src python3 -m pytest tests/test_levels.py tests/test_parallel.py tests/test_ops.py tests/test_tone_stack.py tests/test_rules.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |

## Tests: `tests/test_levels.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_curve_is_pinned_and_monotonic` | For 200 random valid `(f, b, w, a)` (`0.031 < f < 0.45`, `0 < b <= min(f - 0.03, 0.3 * (0.5 - f))`, and the mirror for the white side) and `l = linspace(0, 1, 4001)`: `y[0] == 0`, `y[-1] == 1`, `diff(y) >= -1e-6`, `0 <= y <= 1` |
| 2 | `test_curve_values` | Black only, `f = 0.10`, `b = 0.07`: `y(0.10)` is 0.03, `y(0.5)` is 0.5, `y(0.8)` is 0.8, `y(0.3)` is `0.3 - 0.07 * 0.25` (tolerance 1e-6). White only, `w = 0.80`, `a = 0.09`: `y(0.80)` is 0.89 and `y(0.2)` is 0.2 |
| 3 | `test_identity` | `levels(x, 0, 0) is x`. A frame whose luma starts at 0.01 comes back as the same object for `black=100`. A frame whose floor is 0.6 comes back as the same object for `black=100` |
| 4 | `test_black_point_moves_the_floor` | A frame whose body spans `[0.30, 0.90]` with a dark tail down to 0.10 (6 % of the pixels): after `levels(x, 100, 0)` the 0.1th percentile of the luma is between 0.028 and 0.045; every pixel above the 12th percentile is bit-identical; at strength 50 the floor is between 0.055 and 0.075 |
| 5 | `test_white_point_moves_the_white` | The mirror: a body in `[0.10, 0.50]` with a bright tail up to 0.80: the 99.9th percentile ends between 0.86 and 0.945, and every pixel below the 88th percentile is bit-identical |
| 5a | `test_the_body_of_a_dark_frame_does_not_move` | A frame whose bottom third is its darkest part: the floor moves by more than 0.01, and the median of the bottom third, and of the darkest quarter, third and half of the frame, is exactly unchanged |
| 5b | `test_a_flat_dark_end_is_left_alone` | A frame whose darkest tenth is one flat tone: no pixel moves by 0.002 or more |
| 6 | `test_no_new_clipping` | A random frame with saturated dark and bright colours (channels drawn from `[0.021, 0.979]`, luma floor about 0.08): after `levels(x, 100, 100)` no channel is below 0.02 or above 0.98 |
| 7 | `test_measurement_is_whole_frame_and_strips_are_identical` | With `par.MIN_PIXELS` set to 0 (restored in `finally`), a 700 by 900 frame: `levels` under 4 threads equals `levels` under 1 thread (`array_equal`). `levels` on the top half alone differs from the top half of `levels` on the frame (the measurement is of the whole input) |
| 8 | `test_registered_in_the_frozen_order` | Both names are in `OP_NAMES`, `OP_BOUNDS`, `SPATIAL_OPS`; `apply_ops(x, {"black_point": 100})` equals `levels(x, 100, 0)`; in a bundle with `clarity` and `vibrance`, cutting before and after the levels step gives the same result as one call (`array_equal`) |
| 9 | `test_rule_golden` | Evaluate the packaged `black_point` rule with `rules.eval_expr` on hand-made contexts: a bright source (`fg_p50 = 0.45`, `band_bottom_p50 = 0.40`) gives 100; a dark source without lift (`fg_p50 = 0.20`, `planned_exposure = 0`) gives 0; the same with `planned_exposure = 0.5` gives 100; `pro_stats_valid = 0` gives 0. The three global blocks hold the same expression |
| 10 | `test_setting_gates_the_rule` | A washed synthetic photo (luma in `[0.12, 0.85]`, saved as JPEG in `tmp_path`), stub backend: with the default config the sidecar's `global_settings` has no `black_point` and the output equals the output of a config whose rules file has the `black_point` lines removed; with `levels=True` the sidecar has it and the 0.1th percentile of the output luma is lower than in the default output |
| 11 | `test_adjustment_works_without_the_setting` | `process_one(path, adjustments=[Adjustment(target="global", ops={"black_point": 80}, scope="photo")])` with `levels=False` keeps `black_point` in `global_settings` and lowers the floor |
| 12 | `test_cli_api_and_report_carry_the_setting` | `--levels` sets `cfg.levels`; `[run] levels = true` in a `--config` file does; `service.make_cfg({"levels": True})` does; `scripts/tone_report.py`'s `--set levels=true` reaches `RunConfig` (call its argument handling, not a render); `--set nope=1` is an error |

## Acceptance checks (the session runs these)

1. Both test suites green; default renders byte-identical to the baseline (the setting is off).
2. Tone gate on the Dolomites corpus with `--set levels=true`: 0 dark to darker, 0 pushed into
   clipping, 0 shadow clipping. The rule constants are tuned until this holds.
3. A contact sheet of the washed panel and holdout photos, default against `levels`, judged by
   the Checker; a blind gallery for the owner's vote.
4. Checker review of `ops.py`.

## Known limits

- The op measures the whole frame. Used on a region (`--set sky.black_point=50`) it still measures
  the whole frame and applies the map inside the region.
- Ops that run after it (vibrance, saturation, split-tone, sharpening) can still move a channel
  past a clip level, as they can today.

## Result

Built on 2026-11-11, in the unattended run. Status: done, off by default (`--levels`).

- **Built** by one Builder (Sonnet, high) from this spec. The opt-in rule sets
  (`rules.load(path, enable=...)`) were added by the session when the first build showed that a
  rule in a scene block reaches every direct user of the rules file.
- **Changed after the first tone gate.** The counts were 0 dark to darker and 0 clipping, but
  only because the safe renderer had pulled three photos back (see "Why the pivot is the frame's
  own 10th percentile" above). A stronger rule made it seven. The op now reshapes only the darkest
  tenth of the frame.
- **The rule is now `if(pro_stats_valid > 0, 100, 0)`.** With the op safe by construction, the
  first rule (scaled with source brightness and exposure lift) only made the effect weaker: on
  the washed test frames it gave strengths of 20 to 80. The code blocks under "`rules.toml`"
  above show the first rule.
- **Review** (Checker, Opus, high): no blocking finding. Fixed after it: the "adds no clipping"
  claim was false for a dark pixel with one channel at 0, so the luma is now held inside the clip
  levels as well (test with coloured pixels below the floor); the curve test now draws random
  pivots; `black_point` and `white_point` joined the banding remediation's list of stretch ops.
  Not changed: the percentiles are measured on every fourth pixel, which a texture with a period
  of exactly four pixels could mislead.
- **What it does to a picture: little.** On the four washed test frames the black floor drops by
  6 to 10 levels of 255 (33 to 24, 27 to 20, 20 to 10, 15 to 9) and 1 to 3 % of the pixels move.
  The median of the frame does not move. At screen size the difference is hard to see.
- **Why it cannot do more.** The washed look of a lifted dark photo sits in its lower body, and
  the quality gate does not allow the body of a dark source to come out darker. Depth on such
  frames is a matter of the look (contrast and curve), which [F12](F12-taste-calibration.md) lets
  a user calibrate.
- **Default output:** byte-identical to the baseline renders; both test suites green.
- **Evidence:** `out-features/F5/` (sheet and report). No blind gallery: a difference this small
  would be a coin toss.
- **Recommendation to the owner:** leave it off, or try it on a shoot of your own. It is safe and
  it sets a deeper black where the frame has one, but it does not change how a photo reads.
- **Final gate** (the final rule and op, 110 RAW files): 0 dark to darker, 0 pushed into
  clipping, 0 shadow clipping; the same 12 photos pulled back as with the default settings, none
  new. The median of the bottom third and of the foreground moves by less than 0.0001 on every
  photo. 86 photos change at all; the mean changes by at most 0.4 of 255. Shadow clipping rises by
  at most 0.06 % of a frame (the budget is 0.5 %): the op holds its own output inside the clip
  levels, the colour ops after it can still move a few pixels.
