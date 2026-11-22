# F45. Distance dehaze: covered, nothing to build

Status: spec (2026-11-19). No build. The existing dehaze already works harder on far regions than
near ones; this page gives the evidence.

## Goal (as proposed)

Dehaze that is stronger on far regions (sky, mountains) and weaker near the camera.

## Finding

The engine does this today, at two levels.

1. **Per pixel, inside the op.** `ops.dehaze` is the dark-channel prior. Its transmission `t` is a
   distance estimate: the haze model is `I = J·t + A·(1 − t)` with `t = e^(−β·distance)`. The op
   recovers `J = (I − A) / t + A`, so a pixel moves more the lower its `t`. A near, dark-shadowed
   pixel has `t` close to 1 and barely moves.
2. **Per region, in the rules.** `[landscape.mountain] dehaze = 8 × clamp((region_lum − 0.20) / 0.15,
   0, 1)` (at most 5.1 after the `natural` look and the default intensity) and
   `[landscape.water] dehaze = 2` add dehaze where far things are. Near regions (grass, foliage, trees, snow) get no region dehaze. The sky gets none on purpose:
   local tone moves on coarse sky masks print contour shapes (comment at `[landscape.sky]`). The
   global `dehaze = clamp((haze − 0.32) × 70, 0, 18)` then runs over the whole frame, and the op's
   fragile-area taper and the banding gate protect smooth bright skies.

## Measured (2026-11-19)

The 16 panel photos (`out-f36/panel`), their cached SAM 3 masks (`out-features/F32/panel`), the
analysis picture (1536 px), `ops.dehaze` at 18, the cap of the global rule. Mean change per region
in 8-bit levels, and in brackets the op's mean transmission there (lower = hazier, further):

| Photo | mountain | grass | sky |
|---|---|---|---|
| 0034_dark | 2.8 (0.88) | 0.6 (0.98) | 5.2 (0.72) |
| 0047_blue-sky | 4.0 (0.75) | 4.7 (0.78) | 5.6 (0.61) |
| 0090_bright | 5.2 (0.88) | 1.6 (0.97) | 11.0 (0.64) |
| 05_dolomites_mountain | 3.7 (0.86) | 1.4 (0.94) | 8.7 (0.37) |
| 3D6A1613 | 10.9 (0.55) | 2.4 (0.94) | 12.2 (0.28) |
| 3D6A1624 | 10.0 (0.70) | 4.9 (0.88) | 11.7 (0.28) |
| 3D6A1639 | 12.0 (0.38) | 7.3 (0.81) | 2.8 (0.15) |
| 3D6A1736 | 5.8 (0.67) | 1.3 (0.95) | 10.2 (0.29) |
| 3D6A2036 | 7.0 (0.79) | 7.1 (0.83) | 2.1 (0.14) |
| 01_tree_field_sun | none | 0.03 (1.00) | 11.4 (0.37) |

- In all 9 photos with a mountain and a grass mask, the mountain's transmission is lower: 0.38 to
  0.88 against 0.78 to 0.98.
- The mountain changes 2.6 times as much as the grass (median of the 9 ratios). In 2 of 9 they
  change alike; there the grass reads as nearly as hazy as the mountain (transmission 0.78 and
  0.83 against the mountain's 0.75 and 0.79), most likely far meadows.
- The sky changes most, except where it is blown (0.14, 0.15): there the op's taper holds it.
- What the rules set on the same photos (the panel run's sidecars, `natural` style): 13 photos have
  a mountain region; it gets 5.1 on 6, 1.4 to 4.6 on 4 and 0 on 3 (dark mountains: the rule fades
  out below a region luma of 0.35, because dehaze crushed dark hillsides, loop iter-07). Water 1.3;
  sky, grass and snow 0. The global value is 0 on 7 of 16 (haze below 0.32) and 1.4 to 11.3 on the
  rest. A mountain gets its region pass and then the global pass.

## What is not covered, and why that is not a feature

- A mountain that stays hazy on a photo whose `haze` is below 0.32 gets only its region's value
  (at most 5.1). More would be a rule value (`[landscape.mountain] dehaze`, or the 0.32 of the global
  rule): a change of default pixels for a blind vote, not new code.
- SAM 3 returns one `mountain` mask for near and far ridges. The per-pixel transmission already
  tells them apart.
- `veil` (stats.py) was tried as the global signal and lost the blind test to `haze` (comment at
  `[landscape.global] dehaze`).

## Tasks

None. The session marks F45 as covered in `FEATURE_PLAN.md`.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Build F45? | No: the op's transmission and the region rules already do it (table above) | A spec for a per-region haze rule: one region stat (the mean transmission inside the mask, in `stats.py`) and `dehaze = k × (1 − region_t)` in `[landscape.mountain]`, then the tone gate and a blind vote |
| More dehaze on far ridges by default? | Not changed | Raise `[landscape.mountain] dehaze` in `rules.toml`; a golden test and a blind vote |

## Result

Measured on 2026-11-19, nothing built: the existing dark-channel dehaze already works harder on far regions (mountains changed 2.6 times as much as grass, median, 9 of 9 photos), and the region rules already add mountain dehaze.
