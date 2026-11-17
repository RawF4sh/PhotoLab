# F18. Shaped light: gradients, ovals and a sun glow

Status: spec (2026-11-15). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F18.
Builds on the compositional moves (`compose.py`) and on [F2](F2-region-instructions.md).

Written on Opus at the owner's request, because the Planner tier (Fable) had reached its usage
limit (see `docs/AGENT_ROUTING.md`).

## Goal

An editor does not only mask objects. They drag a graduated filter over the sky at an angle,
drop an oval on the face or the middle of the frame, and paint a soft warm lift where the sun
comes in. AutoEdit has three such moves today (a subject vignette, a graduated darken on the
sky, a frame vignette), placed automatically and never on request. This feature adds:

1. **Shapes:** a linear gradient at any angle, an oval anywhere, and a glow (a soft round light
   with a long falloff).
2. **A light finder:** where the main light in the frame is (the sun, a bright window), from a
   SAM 3 concept and a brightness fallback, or "none".
3. **Shapes by instruction:** `--edit "darken the top with a gradient, add a warm glow where the
   sun is, brighten the center"`.
4. **An automatic sun glow,** off by default (`--light-shaping`), for the owner's blind vote.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Automatic glow on by default? | No: `light_shaping = false`. A shape the user asks for by instruction is on, because it is asked for | `[run] light_shaping = true` in `defaults.toml` after the vote |
| Where an instructed shape lives | In the compositional layer (like a vignette): it overlaps regions on purpose and is blended on its own, not split into region cells | Nothing to reverse |
| Region policies on an instructed shape | Not applied: "brighten the center" over a face brightens the face, as asked. The skin-safe guard of the global pass is unchanged | A policy owner per place in `instructions.toml` |
| The sun outside the frame | No glow; the instruction is reported as "not found" (a glow from the edge guesses the direction of light, which the photo does not tell reliably) | A rule in `light.py` |
| Shapes combined with objects ("brighten the trees on the left") | Out of scope: one target per clause, a place OR an object | A later feature |
| Size and strength of each shape | Data in `instructions.toml` `[places.*]` and `compose.toml` | Edit the files |

The row "Region policies on an instructed shape" was reversed for skin and snow on 2026-11-16, at the owner's word, by [F37](F37-point-and-tell.md), task T8: under a named place, skin and snow that the run found get the change after their policy; every other region gets it in full, and a change the policies leave alone renders as before.

## Design

### 1. Shapes (`edit/mask.py`)

```python
def gradient_alpha(full_hw, angle_deg: float, start: float, end: float) -> np.ndarray:
    """Linear gradient at any angle. The ramp runs along the direction `angle_deg` (0 = from the
    top down, 90 = from the right to the left, 180 = from the bottom up, 270 = from the left to
    the right). Positions are fractions of the frame along that direction, measured from the
    edge the gradient starts at: alpha 1 before `start`, 0 after `end`, a smoothstep between."""

def glow_alpha(full_hw, cx: float, cy: float, radius: float, falloff: float = 2.5) -> np.ndarray:
    """A soft round light: 1 at the centre, a Gaussian-like falloff that reaches 0.5 at `radius`
    (fraction of the frame's long side) and fades out by `falloff * radius`. Never a hard edge."""
```

`radial_alpha` (ovals) and `linear_alpha` stay as they are; `linear_alpha(frm=...)` must equal
`gradient_alpha` at the matching angle with `start = 0` within 1e-6 where both are linear (test),
or the spec of `gradient_alpha` says why not. All shape functions are per-pixel and run on
strips (`edit/par.py`) when the frame is large.

### 2. The light finder (`light.py`, new)

```python
@dataclass
class Light:
    cx: float; cy: float; radius: float      # fractions; radius of the bright core
    source: str                              # "concept" | "brightness"
    score: float

def find_light(proxy_rgb: np.ndarray, union: dict[str, np.ndarray], cfg: dict) -> Light | None:
```

- **Concept first:** when `union` has `sun` (a SAM 3 concept, requested only when a glow is
  wanted, see below) with coverage below `max_core_coverage`, its centroid and size.
- **Brightness fallback:** on the proxy luma, pixels at or above `max(core_lum, the 99.8th
  percentile)`; the largest connected group of them (a small flood fill on a downsampled grid,
  numpy only; no scipy). It counts only when it is compact (coverage below `max_core_coverage`,
  bounding box not wider than `max_core_width`) and inside or touching the sky. A frame without
  a sky has no light by brightness (a bright speck there is a reflection, a lamp or snow), and a
  whole bright sky is not a light source.
- **None** when neither holds. A sun outside the frame is none.
- All thresholds in `compose.toml` `[light]`: `core_lum = 0.97`, `max_core_coverage = 0.04`,
  `max_core_width = 0.25`, `sky_touch_px = 3` (proxy pixels). The session measures them on the
  corpora (see Acceptance) and may tune them before the result is written.

### 3. Compositional moves gain two things (`compose.py`, `compose.toml`)

- `shape = "glow"`: placed on the `Light` (`at = "light"`), radius `radius_scale` times the core
  radius (with a floor `min_radius`), ops from the move.
- `requires = "<run setting>"`: the move fires only when that `RunConfig` bool is true.

```toml
[light]
core_lum = 0.97
max_core_coverage = 0.04
max_core_width = 0.25
sky_touch_px = 3

[sun_glow]
when = "always"
requires = "light_shaping"   # off by default (F18): waits for the owner's blind vote
shape = "glow"
at = "light"
radius_scale = 4.0
min_radius = 0.08
ops = { exposure = 0.12, temp = 8, highlights = -6 }
```

`light_shaping = True` adds the concept `sun` to the run's segmentation concepts, so SAM 3 looks
for it. With it off, nothing changes: no concept, no light search, no move (default output
byte-identical; test).

### 4. Shapes by instruction (`instructions.py`, `configs/instructions.toml`)

A **place** is a target word that means a part of the frame, not an object:

```toml
[places.top]
words = ["top", "upper part", "upper half", "sky side"]
shape = "gradient"
angle = 0
start = 0.0
end = 0.45

[places.bottom]   # angle 180, end 0.45
[places.left]     # angle 270, end 0.45
[places.right]    # angle 90,  end 0.45

[places.center]
words = ["center", "centre", "middle"]
shape = "oval"
cx = 0.5
cy = 0.5
rx = 0.32
ry = 0.36
feather = 0.45

[places.edges]
words = ["edges", "corners", "sides", "border", "outside"]
shape = "oval_outside"        # a vignette
rx = 0.55
ry = 0.55
feather = 0.5

[places.light]
words = ["sun", "sunlight", "light source", "where the sun is", "where the light comes from"]
shape = "glow"
radius_scale = 4.0
min_radius = 0.08
```

New verbs and filler, in the same file:

- `[verbs.glow]` `words = ["glow", "add a glow", "add glow", "warm glow", "add a warm glow", "sun glow"]`,
  `exposure = 0.15`, `temp = 8`; a clause with this verb and no target targets `light`.
- `[verbs.vignette]` `words = ["vignette", "add a vignette"]`, `exposure = -0.25`; with no target,
  targets `edges`.
- Filler words added to `stop`: "with", "gradient", "graduated", "where", "comes", "from",
  "shines", "area", "part", "side", "half" (only when they are not part of a place phrase: place
  phrases are taken first, the way verb phrases are).

Parsing: place phrases are matched like verb phrases (longest first) before the target is
canonicalised. An adjustment to a place has the target `place:<name>` (for example `place:top`).
Places are not segmentation concepts: `extra_concepts` skips them, except that `place:light`
adds the concept `sun`.

Applying (`pipeline.py`, after `compose.plan`): each place target becomes one compositional
layer `instruction:<place>` with the summed deltas (the same `[limits]` as any target), its
alpha built from the place's shape. `place:light` with no `Light` found is reported
`not_found` and adds no layer. Statuses and notes as in F2 (`applied`, `clamped`, `not_found`);
the "What it did" window in Studio lists the layers under "Light shaping" (it reads
`Result.composition`, which gains these entries).

Required parses (golden test):

| Text | Result |
|---|---|
| `darken the top with a gradient` | `place:top`, exposure -0.30 |
| `brighten the bottom a little` | `place:bottom`, exposure +0.30 × the amount for "a little" |
| `add a warm glow where the sun is` | `place:light`, the glow deltas |
| `add a glow` | `place:light` |
| `brighten the center` | `place:center`, exposure +0.30 |
| `add a vignette` | `place:edges`, exposure -0.25 |
| `darken the edges` | `place:edges`, exposure -0.30 |
| `warm the left side` | `place:left`, temp +10 |
| `darken the sky` | the concept `sky`, unchanged F2 behaviour |
| `don't darken the top` | rejected (negation), unchanged F2 behaviour |

## Files

| File | Change |
|---|---|
| `src/autoedit/edit/mask.py` | `gradient_alpha`, `glow_alpha` |
| `src/autoedit/edit/__init__.py` | export them |
| `src/autoedit/light.py` | new: `Light`, `find_light` |
| `src/autoedit/compose.py` | `shape = "glow"`, `at = "light"`, `requires`, `radius_scale`/`min_radius` for glows |
| `src/autoedit/configs/compose.toml` | `[light]`, `[sun_glow]` |
| `src/autoedit/contract.py` | `RunConfig.light_shaping: bool = False` |
| `src/autoedit/cli.py` | `--light-shaping` on `run`, `[run] light_shaping` |
| `src/autoedit/configs/defaults.toml` | `light_shaping = false` |
| `src/autoedit/configs/settings.toml` | the setting's label, group `quality`, help |
| `src/autoedit/web/service.py` | `light_shaping` in `_ALLOWED` |
| `src/autoedit/instructions.py` | place phrases, `place:` targets, `extra_concepts` |
| `src/autoedit/configs/instructions.toml` | `[places.*]`, the two verbs, filler words |
| `src/autoedit/pipeline.py` | `sun` concept when needed, `find_light`, instruction layers, statuses |
| `tests/test_shaped_light.py` | new |
| `tests/snapshots/settings_schema.json` | regenerated for the new setting |

## Tasks

Two Builders, one after the other (task 2 uses task 1's shapes and light finder).

1. **Shapes, light finder, automatic glow** (Sonnet, high): `edit/mask.py`, `edit/__init__.py`,
   `light.py`, `compose.py`, `compose.toml`, `contract.py`, `cli.py`, `defaults.toml`,
   `settings.toml`, `web/service.py`, the `pipeline.py` part that adds `sun` and runs
   `find_light` for the automatic move. Tests 1 to 6.
2. **Shapes by instruction** (Sonnet, high): `instructions.py`, `instructions.toml`, the
   `pipeline.py` part for instruction layers. Tests 7 to 10.

Proving command for both:
`PYTHONPATH=src python3 -m pytest tests/test_shaped_light.py tests/test_instructions.py tests/test_apply_groups.py tests/test_settings_schema.py tests/test_cli.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`, then `PYTHONPATH=src .venv/bin/python tests/run_all.py`.

## Tests: `tests/test_shaped_light.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_gradient_alpha_math` | At angle 0 the alpha is 1 above `start`, 0 below `end`, monotonic in between; angles 90, 180, 270 are the matching rotations of angle 0 (compare on a square frame); an oblique angle (45) has its 0.5 line on the diagonal; `start == end` is a hard step without NaN |
| 2 | `test_glow_alpha_math` | 1 at the centre, 0.5 at `radius` (± 0.02), below 0.01 beyond `falloff * radius`, radially symmetric, no NaN at the centre; a centre outside the frame still gives finite values |
| 3 | `test_find_light_brightness_fallback` | A synthetic frame with a small white disc inside a sky mask: found at the disc's centre (± 0.02), source `brightness`; the same disc outside the sky: none; a fully bright sky with no core: none; an all-black frame: none |
| 4 | `test_find_light_prefers_the_concept` | With a `sun` mask in `union`, its centroid is used, source `concept` |
| 5 | `test_light_shaping_is_off_by_default` | `RunConfig().light_shaping` is false; a default pipeline run on `test-corpus` is byte-identical with and without the new code path (compare with a run where `compose.toml` has no `[sun_glow]`); `sun` is not among the segmented concepts |
| 6 | `test_light_shaping_on` | With `light_shaping=True` on a synthetic sky-with-sun photo: the result's `composition` has `sun_glow`, the pixels around the sun are warmer (R minus B up) and brighter than without; on a photo without a light: no `sun_glow`, and a note says no light was found |
| 7 | `test_place_parse_golden` | The table "Required parses" above, exactly |
| 8 | `test_place_layer_is_applied` | `--edit "darken the top with a gradient"` on a flat grey synthetic photo: the top rows are darker than the bottom rows, the bottom 40 % are unchanged (± 1 level), the adjustment's status is `applied`, `composition` lists `instruction:top` |
| 9 | `test_glow_without_light_is_not_found` | `add a glow` on a photo with no light: status `not_found`, pixels identical to the run without the instruction |
| 10 | `test_place_limits_and_sum` | `darken the top. darken the top` sums to -0.60; a sum beyond `[limits]` is clamped and the status is `clamped` |

## Acceptance checks (the session runs these)

1. Both suites green; default renders byte-identical to the baseline.
2. `find_light` on the Dolomites and Switzerland corpora: how many photos get a light, and a
   contact sheet of 12 of them with the found point marked, plus 6 photos with a visible sun
   that it missed, if any. Tune `[light]` once if needed; record the numbers.
3. Tone gate with `--set light_shaping=true`: 0 dark to darker, 0 pushed into clipping, no new
   safety fallback against the default run.
4. A sheet of the instruction examples above on three photos (one with sun, one portrait, one
   landscape without sun).
5. A blind gallery of the automatic glow against the default for the owner's vote
   (`out-features/galleries/light-shaping/`).

## Result

Built on 2026-11-15. Status: done; the automatic sun glow is built and off by default.

- **Built** by two Builders (Sonnet, high), one per task.
- **Instructions work in Studio** (checked in the browser with SAM 3): "darken the top with a
  gradient" darkens the sky side with a soft ramp; "add a warm glow where the sun is" on a photo
  with a visible sun puts a warm lift around the sun; on an overcast photo it reports "no light
  source found in this photo" and changes nothing.
- **Changed by the session: how the sun is found.** The brightness fallback was checked on 152
  photos of the Switzerland shoot. It marked a light in 49, and looking at them, almost none was
  the sun: blown overcast skies, cloud edges and snow on peaks. Requiring the sky around the
  spot to be darker cut that to 7, still none of them a sun, and lost the one real sun. SAM 3's
  concept "sun" found that sun (score 0.65) and none in the others. So the light comes from SAM 3
  only; the brightness fallback stays in the code behind `[light] brightness_fallback = false`
  (with the stub segmenter no sun is ever found). Also: a frame without sky never has a light by
  brightness.
- **Default output:** byte-identical to the baseline renders; 684 tests on both interpreters.
- **Not done yet:** the tone gate and the blind gallery for the automatic glow
  (`--light-shaping`). Both need SAM 3 on a corpus with suns in it; the RAW corpus is mostly
  without a visible sun. Open for the owner: whether to try it, and on which photos.
