# F50. A highlight pull that keeps the detail of bright skies

Status: spec (2026-11-22). Size M. Off by default (`highlight_detail`). Follows
[F44](F44-raw-highlight-recovery.md), "Cause 2: the two snow photos fail in the sky".

## Goal

With `raw_recover` on, the edit flattens the faint cloud that recovery brings back in a near-white
sky, and the check `encoded_highlight_texture_loss` keeps only 75 % of the edit on 0066 and 0103.
F50 adds an opt-in way to apply the highlight shoulder: a bright pixel is held back by the level of
the bright area around it, not by its own. A smooth area, clipped white included, is held exactly as
today; texture on it keeps its ratio to its surroundings. With the setting off nothing changes.

## Measured (2026-11-22)

1024 px proxy, stub backend, the edit before the safety blend; prototypes patched over `ops.light` in
the planner's scratch folder, not committed. "Float" retention: the check's pairs and lag, with the
output's differences taken before RGB8 rounding (what a step did, without the rounding phase).

**What flattens the sky** (float retention of the worst patch after each step, recovery on):

| Step (the settings of both photos) | 0066 (370, 0) | 0103 (298, 185) |
|---|---|---|
| Regions and composition (sky grad: −0.05 EV, highlights −4) | 0.97 | 0.94 |
| Light: exposure +0.24 EV, `highlight_rolloff` 32 | **0.23** | **0.18** |
| Tone: highlights −8, whites −3.2 | 0.20 | 0.15 |
| Curve, dehaze, clarity, then sharpen | 0.21 | 0.08 |
| The check's number (RGB8) | 0.086 | 0.083 |

- The knee flattens: a frame with no headroom gets the exposure rule's low-headroom allowance (+0.24
  EV at the default intensity) and the knee folds the lifted sky into the last levels under 0.98, a
  slope of about 0.2. The highlights/whites pull costs ×0.87 and greys the sky (13 levels darker);
  on 0103 the sharpening rim along the ridge halves what is left. So halving the pull (F44's try)
  could not help (×0.93; the check moved by rounding phase alone, 0.083 → 0.070). The default decode
  has a flat white sky there, with nothing for the check to measure.

**Candidates** (recovery on; the check's minimum retention, patches under 0.10 in brackets; change in
8-bit levels: mean over source luma 0.80 to 0.98, and the largest; soft cap at white, see the last bullet):

| Candidate | 0066 | 0103 | Bright mean | Largest |
|---|---|---|---|---|
| Today | 0.086 (1) | 0.083 (4) | | |
| Knee weakened where the local variance is high | 0.244 | −0.003 (4) | +2.0 / +2.6 | 9.2 |
| Knee gain from a Gaussian base | 0.368 | 0.091 (2) | 0.0 / 0.0 | 6.8 |
| Knee gain from a guided-filter base | 0.354 | 0.106 | −0.1 / 0.0 | 4.9 |
| **Knee gain from a bright-weighted base** | **0.639** | **0.526** | −0.4 / −0.1 | 8.6 |

- Variance: the lift clips where the sky has texture (0.006 %) and the level jumps with it. Guided
  filter: every window touching the ridge counts as an edge, so beside the ridge, where 0103 fails,
  the knee stays today's. Gaussian: the rock gets into the sky's base.
- The bright-weighted base averages bright pixels only: the sky beside the ridge takes its base from
  the sky. The worst patches keep 0.67 to 0.89 after the light step (today 0.16 to 0.25), 0.53 to
  0.87 after the whole edit. In the crops the cloud is back at today's grey; no halo at the ridge
  (near strong edges 98 % of pixels move by less than 1.9 levels).
- Clipping after the sharpening (0008, recovery on, newly clipped in a channel, budget 0.5 %): with
  the soft cap ending at white, kept bright detail sits near 0.98 and the output sharpening pushes
  it over, 0.33 % → 0.66 %. Ending the cap at 0.975: 0.47 %, and 0066 / 0103 keep 0.621 / 0.492. A
  positive ratio limit of 1.04 (0.60 %) or half of today's room (0.54 %) stay over the budget.

**Corpus** (all 110, the safety workflow as the tone report runs it, the cap at white, recovery off /
on): 0 dark made darker, 0 pushed into clipping, 0 hard failures in all four runs. Pulled back: the
same 12 photos without recovery, 12 instead of 14 with it (0066 and 0103 keep their full edit). The
knee is on in 72 photos; renders change on 70: medians by at most 0.05 levels (0066 and 0103 aside,
now unblended), the bright band by −1.7 to +0.4 levels on average, white share by at most 0.19 % of
the frame. The weak bright skies rise (recovery on: 0086 0.10 → 0.53, 0017 0.12 → 0.64, 0039 0.17 →
0.69; off: 0103 0.13 → 0.43); no retention falls by more than 0.04 (0119, 0.84 → 0.81). The only new
proposal failure is 0008's (above; already pulled back for crushed shadows). A lower cap clips less.

**Another fix in the working tree.** While this was measured, another session left an uncommitted
fix for the same failure (`edit/recover_detail.py`, `[recovered_detail]` in rules.toml, a call after
`apply_image`), on whenever `raw_recover` rebuilt highlights: it adds back half of the source's fine
luma residual (2 px at 1024) in highlights after the whole edit. With it 0066 / 0103 reach 0.271 /
0.393, and 0.728 / 0.651 with F50 (cap at white). It repairs at one scale whatever removed the
texture and has no setting; F50 keeps the knee from removing it. All other numbers here are without it.

## Design

Files (in `src/autoedit/`): `edit/ops.py`, `configs/` `rules.toml` `lessons.toml` `defaults.toml`
`settings.toml`, `contract.py`, `cli.py`, `web/service.py`, `pipeline.py`; tests: `test_highlight_detail.py`
(new), `test_parallel.py`, the settings snapshot; docs: CHANGELOG, FEATURE_PLAN, README. The op sizes
its base by the long edge of the picture it renders: the 1024 px check and the export agree.

### The math

Today `highlight_rolloff` maps each linear channel x (after white balance and exposure), with W the
linear value of 0.98 and kp = W·(1 − amount/200):

```
S(v; a, w) = v for v <= a,  a + (w − a)·(1 − exp(−(v − a)/(w − a))) above     the shoulder
K(x)       = S(x; kp, W)                                                     today's knee
```

F50, with s = `highlight_detail` / 100 and T the linear value of 0.975:

```
1. per pixel, from the gamma luma Y entering the light step (whole frame, once):
   m = clip((Y − 0.70)/0.15, 0, 1)                     how bright the pixel counts
   B = G(m·Y) / max(G(m), 1e-4)                         G: Gaussian blur, sigma 0.01 × long edge
   r = exp(m · ln clip(lin(B)/lin(Y), 1/1.1, 1.1))      base / pixel, linear; 1 when smooth or not bright
2. per channel, b = r·x (the channel at its base level):
   L = K(b)/r                                    r >= 1: the base's gain, x/b kept exactly
   L = max(K(x), S(K(b)/r; min(K(b), T), T))     r <  1: the same gain under a soft cap that ends at T
3. out = K(x) + s·(L − K(x)) where max(x, b) > kp;  K(x) elsewhere, bit for bit
```

### `src/autoedit/edit/ops.py`

Reference code (checked on the cases of the tests; the prototype behind the numbers):

```python
# Highlight detail (F50). Constants of the op, like the levels and grain constants.
_DETAIL_SIGMA = 0.01    # the base: the bright pixels within about this share of the long edge (Gaussian sigma)
_DETAIL_FROM = 0.70     # gamma luma where a pixel starts to count as bright ...
_DETAIL_FULL = 0.85     # ... and counts fully
_DETAIL_RATIO = 1.1     # largest base/pixel ratio (linear) a pixel is held by; beyond it, it is structure
_DETAIL_TOP = float(srgb_to_linear(np.float32(0.975)))   # kept detail ends under 0.975: room for the sharpening


def detail_ratio(rgb: np.ndarray) -> np.ndarray:
    """F50: per pixel, base / pixel in linear light; the base is the mean luma of the BRIGHT pixels around
    it, so a sky beside a dark ridge takes its base from the sky. Clipped to [1/_DETAIL_RATIO,
    _DETAIL_RATIO], faded to 1 on pixels that are not bright. Reads the whole frame: once, before the strips."""
    y = luma(rgb).astype(np.float32)
    m = np.clip((y - _DETAIL_FROM) / (_DETAIL_FULL - _DETAIL_FROM), 0.0, 1.0)
    sigma = _DETAIL_SIGMA * max(y.shape)
    base = gaussian_blur(y * m, sigma) / np.maximum(gaussian_blur(m, sigma), 1e-4)
    r = srgb_to_linear(base) / np.maximum(srgb_to_linear(y), 1e-6)
    return np.exp(m * np.log(np.clip(r, 1.0 / _DETAIL_RATIO, _DETAIL_RATIO))).astype(np.float32)


def _shoulder(v, a, w):
    """v up to a; above it a + (w - a)(1 - exp(-(v - a) / (w - a))): slope 1 at a, under w (a < w)."""
    d = w - a
    return np.where(v > a, a + d * (1.0 - np.exp(-np.maximum(v - a, 0.0) / d)), v)
```

`highlight_rolloff(lin, amount, ratio=None, detail=0.0)` keeps today's body up to `out[over] = ...`,
with `s = min(max(float(detail), 0.0), 100.0) / 100.0 if ratio is not None else 0.0` and the early
return `if not over.any() and s == 0.0: return lin`; then:

```python
    if s == 0.0:
        return out
    r = np.broadcast_to(ratio[..., None], lin.shape)
    b = lin * r                                    # each channel at its base level
    act = np.maximum(lin, b) > kp                  # elsewhere the plain knee's values stay, bit for bit
    r, b, o = r[act], b[act], out[act]
    kb = _shoulder(b, kp, _WHITE_LIN)              # the knee at the base
    top = np.float32(_DETAIL_TOP)
    cap = _shoulder(kb / r, np.minimum(kb, top * np.float32(0.9999)), top)
    local = np.where(r >= 1.0, kb / r, np.maximum(o, cap))   # at/below the base: its gain; above: capped
    out[act] = o + np.float32(s) * (local - o)
    return out
```

Also: `"highlight_detail"` in `OP_NAMES` after `"highlight_rolloff"`, `OP_BOUNDS` (0.0, 100.0),
`SPATIAL_OPS`; `STEPS[1]` becomes `("temp", "tint", "exposure", "highlight_rolloff", "highlight_detail")`,
the knee's own step, so a bundle can still be cut between any two steps and `global_steps` (F29) ends
on the one-pass picture; `light(..., ratio=None, detail=0.0)` and `_tone_steps(out, settings,
ratio=None)` pass them on (`settings.get("highlight_detail", 0.0)`); `apply_ops`, when
`highlight_detail` and `highlight_rolloff` are both above 0, runs the tone branch as `par.rows(lambda
a, r: _tone_steps(a, settings, r), out, detail_ratio(out))` (the base reads the whole frame: once,
before the strips). Only the global blocks set the knee; a LUT export (`lut.py`) bakes today's knee.

### Setting and rules

- `RunConfig.highlight_detail: bool = False` after `highlight_hold` (`# the highlight knee keeps faint
  cloud and snow texture (F50); off → the knee as before`), wired as `highlight_hold` is: `[run]` in
  `defaults.toml`, `cli.py` (`[run]` keys, override map, `--highlight-detail`, store_true, default
  None, help "keep faint cloud and snow texture where the edit holds back the brightest tones
  (opt-in)"), `_ALLOWED` in `web/service.py`; `[setting.highlight_detail]` in `settings.toml`, label
  "Keep detail in bright skies", group "quality", help "Keeps faint cloud and snow texture where the
  edit holds back the brightest tones. Off: those tones are held back evenly, as before.", then
  `AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`.
- `pipeline.py`: `"highlight_detail"` in `_OPT_IN_RULES`; after decide, beside the denoise drop, pop
  it from the global and region settings when `cfg.highlight_detail` is off (no decider, look or genre
  turns it on alone); after `apply_image`, when it and `highlight_rolloff` are set, the note
  `highlight detail: the roll-off held the brightest tones by the level around them, so faint cloud
  and snow texture keep their contrast`. No stage moves.
- `configs/rules.toml`, after the denoise set, the same line in `people`, `landscape` and `other`:

```toml
# Highlight detail (F50, run setting `highlight_detail`): the knee holds each bright pixel by the level of the
# bright pixels around it. A way of applying the knee, not a strength: divided by the intensity, as the night ops.
[opt_in.highlight_detail.landscape.global]
highlight_detail = "100 / max(intensity, 0.01)"
```

- `configs/lessons.toml`, `[op.highlight_detail]` (`test_lessons.py` wants every op): name "Highlight
  detail"; does "Holds the brightest tones back by the level of the bright area around them, so faint
  cloud and snow texture keep their contrast."; when "With the highlight roll-off, on textured bright
  skies and snow, above all recovered RAW highlights."; watch "Acts only with the roll-off. A pixel
  far brighter or darker than its surroundings is held by its own level, as before."

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T0 | Session | Baseline before any edit: `autoedit run ./test-corpus <scratch>/f50-before --backend stub` | — |
| T1 | Builder (Sonnet, high: op math in a protected area) | "ops.py" above, the lesson, tests 1 to 5 | `PYTHONPATH=src python3 -m pytest tests/test_highlight_detail.py tests/test_highlight_knee.py tests/test_parallel.py tests/test_ops.py tests/test_ops_phase2.py tests/test_lessons.py tests/test_teach.py tests/test_lut.py -q` |
| T2 | Builder (Sonnet, medium) | "Setting and rules"; tests 6 and 7; the snapshot | `PYTHONPATH=src python3 -m pytest tests/test_highlight_detail.py tests/test_settings_schema.py tests/test_cli.py tests/test_rules.py tests/test_denoise.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Tweaker (Haiku, low) | CHANGELOG line; F50 row in FEATURE_PLAN.md; `--highlight-detail` beside `--highlight-hold` in README.md | none |
| T4 | Checker (Opus, high) | the diff against this spec and CLAUDE.md; the acceptance | the acceptance |

T1 and T2 share `tests/test_highlight_detail.py`: one after the other.

## Tests (`tests/test_highlight_detail.py`; one line in `tests/test_parallel.py`)

1. Gating: `apply_ops(x, {"highlight_detail": 100.0})` is x; `{"exposure": 0.24, "highlight_rolloff":
   32.0}` gives `array_equal` output with and without `"highlight_detail": 0.0`; so do
   `highlight_rolloff(lin, 32.0, ratio, 0.0)` and `highlight_rolloff(lin, 32.0)`, and an input with
   nothing over the knee comes back as the same object. `test_identity_is_noop` stands unchanged.
2. Math, amount 32, x = `linspace(0, 2, 2001)`, r constant in (1/1.1, 0.95, 1, 1.05, 1.1): below W
   and luma ≤ 0.98; non-decreasing; x itself where x and r·x ≤ kp; today's knee ±1e-6 at r = 1;
   K(r·x)/r ±1e-6 for r ≥ 1 where r·x > kp; for r < 1 not below today's knee (−1e-7).
3. `detail_ratio`: a flat 0.95 frame gives 1 (1e-5), a flat 0.6 frame exactly 1; a 100 × 100 frame
   of 0.95 over 0.30 (rows 60 and down): the dark rows exactly 1, the bright rows within 1e-4 of 1.
4. The goal: a 512 × 512 sky, 0.972 plus `gaussian_blur(standard_normal, 1.5)` (seed 5) scaled to
   std 0.006, `{"exposure": 0.24, "highlight_rolloff": 32.0}` with and without `"highlight_detail":
   100.0`: `assess_pixels`' `minimum_retention` below 0.3 without, above 0.5 with (measured 0.24,
   0.62); mean luma within 0.5/255 of today's; no luma above 0.975 + 1e-6.
5. Strips: `{"exposure": 0.3, "highlight_rolloff": 35.0, "highlight_detail": 100.0}` on a bright
   700 × 900 frame, 1 and 4 threads under `_Pinned`: `array_equal`. The bundle of
   `test_a_bundle_can_be_cut_between_any_two_steps` gets `"highlight_detail": 60.0`.
6. Rules and setting: not in a plain `load_rules(path)`; with `enable=["highlight_detail"]` one
   expression in the three scenes, `eval_expr` 125 at intensity 0.8; the flag, `[run]` and
   `service.make_cfg` reach `RunConfig`, default False (as `test_denoise.py` does).
7. Pipeline, stub, a bright JPEG with a clipped sky (the knee on): default, no `highlight_detail` in
   the global settings; with the setting, 100 (1e-6) and the note; `rule_sets=["highlight_detail"]`
   with the setting off: dropped.

## Acceptance (the session)

- Both suites (system Python and `.venv`); the stub smoke run.
- Default byte-identical: T0's files and sidecars against the same run after T3 (timings aside).
- 0066 and 0103, `raw_recover` and `highlight_detail` on: no `encoded_highlight_texture_loss`,
  minimum retention at least 0.3 (prototype 0.62 and 0.49), the edit kept 100 %.
- Tone gate with the setting, recovery off and on: `PYTHONPATH=src python3 scripts/tone_report.py
  curated-dolomites-summer-2026/selected_raw --render --fail-on-regression --json <file> --set
  highlight_detail=true`, then with `--set raw_recover=true` too: 0 dark made darker, 0 pushed into
  clipping, 0 hard failures; against the same runs without the setting, no photo newly pulled back
  (0066 kept 100 %), medians within a level, white share at most 0.2 % of the frame higher.
- Time of a full-size export of 0066 with and without the setting (prototype, one thread, all-bright
  22 MP: the knee step about four times today's, plus two blurs of the luma).
- Sky crops of 0066, 0103, 0017, 0039, today against F50, judged by the Checker; then the blind gallery.

## Owner decisions (recorded 2026-11-22)

| Decision | Recorded |
|---|---|
| Ships opt-in | Behind the new setting `highlight_detail` (`--highlight-detail`, `[run] highlight_detail`); off by default, default output byte-identical |
| The default later | The owner's blind gallery, off against on: `scripts/blindtest.py --configs-a/--configs-b` with two configs folders that differ only in `[run] highlight_detail`, both with `[decode] recover = true` |

Open, for the owner before T1: F50, the uncommitted `recover_detail` step, or both. They answer the
same failure; shipping both stacks two detail repairs on recovered skies.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| What changes | The knee, not the highlights/whites pull: the knee takes 0.97 to 0.23, the pull ×0.87 | A second key in the tone step, the pull from the same base: 0.723 / 0.635 on 0066 / 0103, largest change 10.1 levels |
| The base | The mean of the bright pixels around, not a guided filter: 0.526 against 0.106 on 0103 | `detail_ratio` |
| Ratio limit | 1.1: 1.2 gives 0.673 / 0.590 and moves the largest change from 8.6 to 14.2 levels | `_DETAIL_RATIO` |
| Base size | 1 % of the long edge: 2 % gives 0.638 / 0.526 and darkens the bright band twice as much | `_DETAIL_SIGMA` |
| End of the cap | 0.975, under the 0.98 clip level: room for the output sharpening (0008) | `_DETAIL_TOP` |
| Constants | Module constants in ops.py, like the levels and grain constants; the rule decides when and how much | keys in rules.toml |
| Intensity | Does not scale it: a way of applying the knee, as the night ops are corrections | the rule |
| `quality.toml` | Untouched: F50 passes the check by keeping texture, not by a looser threshold | none intended |

## Decided (owner, 2026-11-22)

- **F50 only.** The other fix for the same failure (`edit/recover_detail.py`, the `[recovered_detail]`
  rules block, its step after apply and its tests) is removed; the paragraphs above that describe it
  ("another fix in the working tree", "open, for the owner") are history. One mechanism per problem.
- Ships opt-in behind `highlight_detail` (`--highlight-detail`); default output byte-identical
  (checked: stub run on `test-corpus` against the source before F50). A future default is decided by
  a blind gallery of off against on, both with `raw_recover`.
