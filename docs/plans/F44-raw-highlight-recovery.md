# F44. RAW highlight recovery

Status: spec (2026-11-19; colour fade revised the same day after the Checker's crops, see Result).
Size M. Off by default. Plan entry: "RAW highlight recovery" under
"Later, or not planned" in [FEATURE_PLAN.md](../FEATURE_PLAN.md).

## Goal

Snow and sky that the default RAW decode clips to flat white, or to a cyan or yellow cast where
one or two channels clip, get back the tones the RAW file holds. Every pixel below a knee keeps its
value bit for bit; above it one shoulder maps the rebuilt range under white. RAW files with
`raw_mode = "none"` (the default) only.

## Why F6's attempt failed, and what changes

- LibRaw's highlight modes other than `clip` normalise the white balance by the largest multiplier
  instead of the smallest, so nothing clips but the whole frame is darker by
  `log2(max / min)` of the multipliers: 0.93 to 1.19 stops on this corpus (median 1.11). That is
  the darkening F6 measured (109 of 110 photos darker), and why `raw_highlight` was removed.
- F44 puts the exposure back: the blend decode times `max(wb) / min(wb)`, with
  `wb = raw.camera_whitebalance[:3]`. Measured below: this equals the default linear decode in
  unclipped mid-tones to 0.06 %.
- The rebuilt values pass the old white by up to about half (the shoulder's top on the photos
  below: 1.04 to 1.52 times the old white). A shoulder maps them under white, so they do not clip
  again.
- LibRaw's blend gets the colour of clipped pixels wrong where green clips (green is
  under-estimated): a clipped sunset sky turns pink, a clipped blue sky lavender. The camera's own
  JPEG shows both near white. So a rebuilt pixel takes only its brightness from the blend decode;
  its colour is the default decode's at the edge of the clipped area and fades to neutral deeper in,
  by the distance from that edge as well as by how far the RAW passes white (a fade by value alone
  drew a blue rim round foliage and white blotches in a sunset glow, see Result).

## Measured (2026-11-19)

Dolomites corpus, 110 CR2 files (Canon EOS 5D Mark III). Experiments in the planner's scratch
folder, not committed.

**The corpus** (half-size decodes, all 110)

- `max(wb) / min(wb)` against the measured ratio of the default linear decode to the blend decode
  in unclipped mid-tones: median 1.00014, worst file 1.00056.
- A channel at white in the default decode: at least 0.2 % of the frame in 49 photos, at least 1 %
  in 36, at least 10 % in 19, at most 35.9 %.
- All three channels at white: at most 0.07 % of any frame. Nearly all clipping is one or two
  channels (green and blue on snow and bright sky), which the other channels can still describe.
- LibRaw's curve: a numpy copy of its `gamma_curve(1/2.222, 4.5)` table gives the default decode's
  16-bit values bit for bit (0 differences on 3 photos).

**This design, full resolution** (knee 0.75, fade 0.25, edge 0.02; "white" = all three channels at
or above 0.985; "spread" = luma p1 to p99 inside the clipped area, 8-bit levels)

| Photo | Clipped | White: default → recovered | Spread | Changed pixels | Their mean change |
|---|---|---|---|---|---|
| 0012_snow | 35.9 % | 32.2 % → 18.9 % | 6.9 → 16.5 | 36.0 % | 2.0 darker |
| 0045_snow (sunset sky) | 19.7 % | 6.4 % → 2.1 % | 56.7 → 63.8 | 24.0 % | 2.7 darker |
| 0109_dark (blue sky) | 15.2 % | 0.35 % → 0.03 % | 69.8 → 72.0 | 19.1 % | 2.4 darker |
| 0039_bright | 23.8 % | 0.10 % → 0.09 % | 35.4 → 37.4 | 46.6 % | 1.9 darker |
| 0078_snow | 13.7 % | 0.0 % → 1.2 % | 44.0 → 52.8 | 19.4 % | 4.1 darker |
| 0042_blue-sky | 0.12 % | below the 0.2 % floor: unchanged, byte for byte | | 0 | |
| 0002_blue-sky | 0 % | unchanged, byte for byte | | 0 | |

- The median luma moved by at most 0.2 of a level (0039); on the others not at all. "Their mean
  change" is luma of the encoded values, which reads lower for a coloured pixel than for a grey one of
  the same luminance: the edge fade keeps colour, so 0109 and 0078 read darker than in the first
  build (0.4 brighter and 2.9 darker there) at the same rebuilt luminance.
- Colour of the clipped area, mean RGB, default → recovered: 0045 (255, 239, 221) → (249, 240, 237);
  0109 (185, 239, 255) → (213, 230, 243): the default's colour near the edges, neutral inside. The
  camera's JPEG over the same pixels: (255, 253, 250) and (247, 251, 253). LibRaw's blend colours
  unchanged would have given pink and lavender.
- What comes back is broad shading of snow fields and clouds, not fine texture: the 8 × 8 block
  standard deviation inside the clipped area of 0012 goes from 0.00 to 0.05 levels. Expect a white
  field to become a gently shaded one, not a textured one.
- The mean luma step between neighbouring clipped and unclipped pixels, default → recovered
  (same photo order): 8.9 → 7.7, 1.7 → 1.7, 7.2 → 5.9, 0.9 → 0.9, 1.9 → 1.9 levels: at or under
  the default decode's. The chroma step, which caught the first build's rim, is in Result.
- Time on a 22 MP frame: the second LibRaw decode about 1.2 s and the rebuild 1.3 to 2.5 s (the
  edge fade's box blur 0.05 s of it with OpenCV, 0.3 s without), only on photos with at least 0.2 %
  clipped.

**The knee** (0012 / 0045 / 0078; measured with the first build's colour fade)

| Knee, linear (8-bit) | White left | Spread | Mean change of the changed pixels |
|---|---|---|---|
| 0.60 (197) | 11.6 / 1.1 / 0.9 % | 22.4 / 61.0 / 53.2 | 4.6 / 5.6 / 5.6 darker |
| 0.75 (221) | 19.5 / 2.4 / 1.3 % | 16.4 / 59.5 / 50.2 | 2.0 / 2.1 / 2.9 darker |
| 0.85 (235) | 27.2 / 5.7 / 2.0 % | 11.8 / 57.9 / 47.6 | 0.0 / 0.0 / 0.6 darker |

0.75 is the default: it brings back most of the range for about 2 levels of darkening of the bright
tones above the knee.

## Design

### Where it runs

Stage 1 of the pipeline, decode: inside `imageio._decode_raw`, while the rawpy file is still open
(the second LibRaw decode needs it). The order of the stages does not change. Every caller that
decodes with `decode=raw_opts_from(cfg)` (the pipeline, `presegment`, the web preview, the tone
report's render) gets the same picture. JPEG and the other formats are not affected.

### `src/autoedit/imageio.py`

Reference code (checked on the photos above and on the synthetic cases of the tests). Put the
constants and the helpers above `_decode_raw`.

```python
# RAW highlight recovery (F44). Constants of the decode, like _HEADROOM_MARGIN_EV (the decode reads
# no rules file).
_RECOVER_KNEE = 0.75        # linear; a pixel whose largest channel is at or below it is never changed (221 of 255)
_RECOVER_FADE = 0.25        # rebuilt colour fades to neutral over this much above the old white (linear)
_RECOVER_EDGE = 0.02        # share of the long side: the colour fade reaches full strength this far inside a clipped area
_RECOVER_MIN_SHARE = 0.002  # a frame with less of its area clipped is left as decoded
_RECOVER_TOP_Q = 99.9       # percentile of the rebuilt pixels that the shoulder maps to white


def _libraw_gamma(pwr: float = 1 / 2.222, ts: float = 4.5) -> tuple[float, float]:
    """(g2, g4) of LibRaw's gamma_curve(pwr, ts), found by the same 48-step bisection LibRaw runs:
    g2 is where the straight toe (slope ts) meets the power segment, on the encoded side; g4 is the
    offset of the power segment."""
    lo, hi = 0.0, 1.0
    g2 = 0.0
    for _ in range(48):
        g2 = (lo + hi) / 2.0
        if ((g2 / ts) ** (-pwr) - 1.0) / pwr - 1.0 / g2 > -1.0:
            hi = g2
        else:
            lo = g2
    return g2, g2 * (1.0 / pwr - 1.0)


_BT709_PWR, _BT709_TS = 1 / 2.222, 4.5
_BT709_G2, _BT709_G4 = _libraw_gamma(_BT709_PWR, _BT709_TS)   # 0.081226, 0.099258


def libraw_curve(lin):
    """LibRaw's output curve for gamma=(2.222, 4.5), the transfer of the default decode: lin * 4.5
    below lin = g2 / 4.5, else lin ** (1/2.222) * (1 + g4) - g4. Float32, input clipped to [0, 1]."""
    lin = np.clip(np.asarray(lin, dtype=np.float64), 0.0, 1.0)
    return np.where(lin < _BT709_G2 / _BT709_TS, lin * _BT709_TS,
                    lin ** _BT709_PWR * (1.0 + _BT709_G4) - _BT709_G4).astype(np.float32)


def libraw_curve_inv(v):
    """The inverse of libraw_curve."""
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    return np.where(v < _BT709_G2, v / _BT709_TS,
                    ((v + _BT709_G4) / (1.0 + _BT709_G4)) ** (1.0 / _BT709_PWR)).astype(np.float32)


def shoulder(m, knee: float, top: float):
    """F44's shoulder on linear values: m itself up to the knee; above it knee + d / (1 + d * c) with
    d = m - knee and c = (top - 1) / ((1 - knee) * (top - knee)). Slope 1 at the knee, increasing,
    exactly 1 at top. top <= 1 returns m itself."""
    if top <= 1.0:
        return m
    c = (top - 1.0) / ((1.0 - knee) * (top - knee))
    d = np.maximum(m - knee, 0.0)
    return np.where(m > knee, knee + d / (1.0 + d * c), m)


def recover_highlights(rgb, rebuilt, clipped, knee=_RECOVER_KNEE, fade=_RECOVER_FADE, top_q=_RECOVER_TOP_Q,
                       edge=_RECOVER_EDGE):
    """F44's rebuild, testable without a RAW file. rgb: the default decode (LibRaw's curve, [0, 1]);
    clipped: HxW bool, its pixels with a channel at 1.0; rebuilt: (N, 3) linear values of those pixels,
    in the same order, from LibRaw's blend decode with the exposure put back (they may exceed 1).
    A clipped pixel takes the luminance of `rebuilt` and the colour of the default decode, faded towards
    neutral (the camera renders blown highlights white) by w = (how far the largest rebuilt channel is
    past 1.0, full at 1 + fade) x (how far the pixel is inside the clipped area, from a box blur of
    `clipped`: 0 at its edge, full at edge x the long side in). So a clipped sky meets the unclipped
    pixels around it in their own colour, with no rim and no blotch, and only the inside of a large
    clipped area goes neutral. One shoulder on the largest channel then maps [knee, top] onto [knee, 1],
    top = the top_q percentile of the rebuilt pixels; all three channels get the same factor, so hue and
    saturation stay. A pixel whose largest channel is at or below the knee keeps its value bit for bit.
    Returns (rgb, top); when nothing rebuilt passes the old white, (rgb itself, 1.0)."""
    from .edit.filters import box_blur   # here, not at the top: edit imports this module
    old = libraw_curve_inv(rgb[clipped])
    y_new = rebuilt @ _LUMA
    y_old = np.maximum(old @ _LUMA, 1e-6)
    r = max(1, round(edge * max(clipped.shape)))
    inside = np.clip(2.0 * box_blur(clipped.astype(np.float32), r)[clipped] - 1.0, 0.0, 1.0)
    w = (np.clip((rebuilt.max(axis=-1) - 1.0) / fade, 0.0, 1.0) * inside)[:, None]
    rec = y_new[:, None] * ((1.0 - w) * old / y_old[:, None] + w)
    top = max(1.0, float(np.percentile(rec.max(axis=-1), top_q)))
    if top <= 1.0:
        return rgb, 1.0
    hi = clipped | (rgb.max(axis=-1) > float(libraw_curve(knee)))
    lin = libraw_curve_inv(rgb[hi])
    lin[clipped[hi]] = rec               # boolean indexing keeps row-major order: the same pixels as rgb[clipped]
    m = lin.max(axis=-1)
    scale = shoulder(m, knee, top) / np.maximum(m, 1e-9)
    out = rgb.copy()
    out[hi] = np.where((m > knee)[:, None], libraw_curve(np.minimum(lin * scale[:, None], 1.0)), rgb[hi])
    return out, top


def _recover(raw, rgb, common: dict, info: dict):
    """F44 on an open rawpy file and its default decode: asks LibRaw for the blend decode only when
    enough of the frame is clipped, puts its exposure back, and rebuilds. Records what it did in `info`."""
    clipped = rgb.max(axis=-1) >= 1.0
    share = float(clipped.mean())
    info.update(highlight="clip", recover_clipped=round(share, 4))
    if share < _RECOVER_MIN_SHARE:
        info["recover_skipped"] = f"{share * 100:.2f}% of the frame is clipped, less than {_RECOVER_MIN_SHARE * 100:g}%"
        return rgb
    wb = [float(v) for v in raw.camera_whitebalance[:3]]
    if min(wb) <= 0.0:
        info["recover_skipped"] = "the file has no camera white balance"
        return rgb
    blend = raw.postprocess(no_auto_bright=True, gamma=(1.0, 1.0), highlight_mode=2, **common)
    rebuilt = blend[clipped].astype(np.float32) * np.float32(max(wb) / min(wb) / 65535.0)
    out, top = recover_highlights(rgb, rebuilt, clipped)
    if top > 1.0:
        info.update(highlight="recover", recover_top=round(top, 3))
    return out
```

`box_blur` is the repo's O(N) box blur in `edit/filters.py` (OpenCV when the `accel` extra is there,
which is also what brings rawpy; numpy otherwise). On a straight edge `inside` rises linearly from 0
at the first clipped pixel to 1 at `r` px in (116 px on a 22 MP frame, 58 at half size); a clipped
area narrower than 2 `r` never goes fully neutral, and next to a thin unclipped feature (a branch)
`inside` drops by that feature's share of the window.

`_LUMA` is the module's Rec.709 weight vector; here it weighs linear values (luminance). rawpy
accepts `highlight_mode=2` as an int (checked: the same output as `rawpy.HighlightMode.Blend`), so
a fake rawpy in the tests needs no enum.

Changes to existing code in the same file:

- `_RAW_DEFAULTS` gets `"raw_recover": False` (so `raw_opts_from` carries it).
- `_decode_raw`, right after the first `info.update(...)`:

```python
    if o["raw_recover"] and mode != "none":
        # ponytail: recovery for raw_mode none only; in the linear modes the shoulder would replace their final clip
        info.update(highlight="clip", recover_skipped=f"raw_mode {mode} keeps its own highlights")
```

- `_decode_raw`, the `none`/`libraw_auto` branch becomes:

```python
        if mode in ("none", "libraw_auto"):
            rgb16 = raw.postprocess(
                no_auto_bright=(mode == "none"),  # "none": feed the true exposure, no stretch
                gamma=(2.222, 4.5),                # sRGB-ish tone curve → our gamma working space
                **common,
            )
            rgb = np.clip(rgb16.astype(np.float32) / 65535.0, 0.0, 1.0)
            if o["raw_recover"] and mode == "none":
                rgb = _recover(raw, rgb, common, info)   # F44: a second decode of the open file
            return rgb, 16, 0.0
```

With `raw_recover` off, the first `postprocess` call and the returned array are exactly today's,
and `decode_info` gets no new key: default output stays byte-identical, sidecars included.

### Settings

`RunConfig.raw_recover: bool = False`, after `raw_fallback_ev`, comment `# rebuild highlights the
default decode clips, from LibRaw's blend decode (F44); off → decoded as before`. Wired as
`highlight_hold` is:

- `configs/defaults.toml`, `[decode]`: `recover = false        # true: rebuild clipped highlights
  from LibRaw's blend decode (F44, opt-in, RAW only)`.
- `cli.py`: `"recover": "raw_recover"` in `_dec_map`; `--raw-recover` (`store_true`,
  `default=None`, help: "rebuild snow and sky that the RAW decode clips to flat white, from the
  channels that did not clip (opt-in, RAW only)"); `"raw_recover": getattr(args, "raw_recover",
  None)` in the override map of `load_run_config`.
- `web/service.py`: `"raw_recover"` in `_ALLOWED`.
- `configs/settings.toml`: `[setting.raw_recover]`, label "Recover blown highlights", group "raw",
  help "Brings back the tones a RAW file holds in snow and sky that the normal decode turns flat
  white. RAW files only; a little slower.", `advanced = true`; then the snapshot:
  `AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`.
- The name is not `raw_highlight`: F6 removed that option, and `tests/test_highlight_hold.py`
  checks it stays removed.

### The sidecar note (`pipeline.py`)

Next to the `raw develop` note:

```python
        di = img.decode_info or {}
        if di.get("highlight") == "recover":
            notes.append(f"raw recover: {di['recover_clipped'] * 100:.1f}% of the frame had a clipped channel; "
                         f"rebuilt from LibRaw's blend decode, {di['recover_top']:.2f} times the old white "
                         "mapped to white")
        elif di.get("recover_skipped"):
            notes.append(f"raw recover: not applied, {di['recover_skipped']}")
```

The decode_info keys reach the sidecar's `audit.decode_info` as they are.

### The mask cache

Nothing changes in `segment/cache.py`. Both caches key on the proxy's pixels: `DiskMaskCache`
names its file by `pixel_digest(img.rgb)`, `CachingSegmenter` keys on path, shape, dtype, mtime and
a pixel digest. A recovered photo is a new key, so SAM 3 segments it once more and caches it; a
photo below the 0.2 % floor decodes to the same pixels and hits the old entry. Reusing the masks of
the default decode was considered and rejected: the masks and the stats must describe the same
picture.

### With the rest of the edit

Nothing to change; two effects to watch in the acceptance.

- The highlight hold (F6) protects pixels that are white in the source. With recovery on, fewer
  source pixels are white, so it holds only what stays white after the rebuild.
- The stats see less clipping, so `headroom_ev` rises on recovered photos. The exposure, contrast
  and whites rules are capped by it, so they may lift those photos a little more; the edit's own
  highlight knee (`highlight_rolloff`, asymptote 0.98) still compresses what comes near white. The
  per-photo comparison with the default run in the acceptance shows how much.

## Tasks

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder (Sonnet, high: decode math, hard) | everything under "imageio.py" above; tests 1 to 5 and 7 | `PYTHONPATH=src python3 -m pytest tests/test_raw_recover.py tests/test_raw_develop.py tests/test_workflows.py tests/test_imageio_fidelity.py -q` |
| T2 | Builder (Sonnet, medium) | "Settings" and "The sidecar note"; test 6; the snapshot | `PYTHONPATH=src python3 -m pytest tests/test_raw_recover.py tests/test_settings_schema.py tests/test_cli.py tests/test_highlight_hold.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Tweaker (Haiku, low) | a CHANGELOG line | none |
| T4 | Session | the acceptance below | — |
| T5 | Builder (Sonnet, medium) | the edge fade on top of T1's build: `_RECOVER_EDGE` and the new body and docstring of `recover_highlights` above (the signature gains the keyword `edge`, default `_RECOVER_EDGE`; nothing else in the file changes); test 4b | as T1, then `PYTHONPATH=src python3 tests/run_all.py` |

T1 and T2 share `tests/test_raw_recover.py`: run them one after the other. T5 comes from the Checker's
crops of T1's first build (Result); the acceptance (T4) runs after it.

## Tests (`tests/test_raw_recover.py`)

The synthetic frame of tests 4 and 5:

```python
rgb = np.zeros((40, 60, 3), np.float32)
rgb[:, :30] = np.linspace(0.30, 0.95, 40, dtype=np.float32)[:, None, None]   # grey ramp, partly above the knee
rgb[:, 30:] = np.array([1.0, 1.0, 0.9], np.float32)                          # red and green clipped
clipped = rgb.max(-1) >= 1.0
v = np.linspace(1.0, 1.6, 30, dtype=np.float32)                              # what the RAW holds, along x
rebuilt = np.repeat(np.tile(v, 40)[:, None], 3, axis=1)                      # neutral, row-major like rgb[clipped]
```

1. `libraw_curve`: `_BT709_G2` = 0.0812257 and `_BT709_G4` = 0.0992578 (±1e-6); values 0 → 0,
   0.018 → 0.081 (the toe, 4.5 × x), 0.18 → 0.408830, 0.5 → 0.705421, 0.75 → 0.866507, 1 → 1
   (±1e-5); `libraw_curve(libraw_curve_inv(v))` within 1e-6 of `v` on 1001 points of [0, 1]; the
   two segments meet at the break (difference below 1e-6).
2. Against LibRaw's own table: `min(floor(65536 × libraw_curve(i / 65536)), 65535)` is within 1 of
   these entries, read from LibRaw through rawpy (its 16-bit default decode against its linear
   decode, 3 photos, 0 differences): i = 100 → 450, 1000 → 4500, 1182 → 5319, 1183 → 5323,
   10000 → 24407, 32768 → 46230, 60000 → 62730, 65535 → 65535.
3. `shoulder` with knee 0.75, top 1.5: equal to `m` for every `m` at or below the knee; 1.0 at top
   (±1e-9); slope at the knee `(f(knee + 1e-6) − knee) / 1e-6` in [0.999, 1.0]; strictly increasing on
   (knee, top]; with top 1.0 the same object comes back.
4. `recover_highlights` on the synthetic frame: top 1.6 (±1e-3); pixels whose largest channel is at
   or below `libraw_curve(0.75)` are `array_equal` to the input; output at most 1.0; in every row the
   luma of the clipped block rises strictly along x; the last column is (1, 1, 1); the first column
   is below 1.0 in every channel (it was white); where `v` ≥ 1.25 the three channels are equal
   (±1e-6); the left ramp stays non-decreasing and no pixel of it gets brighter. With `rebuilt` all
   0.9 the input object comes back with top 1.0. (Unchanged by the edge fade: on this 60 px frame
   `r` is 1, full strength from 1 px in, and `v` passes 1.25 only 13 px in.)
4b. A saturated clip edge keeps its colour (the first build's rim round foliage on 0109), on its own
   frame:

   ```python
   sky = np.array([0.53, 0.88, 1.0], np.float32)          # linear; blue clipped in the default decode
   rgb = np.full((20, 500, 3), 0.2, np.float32)            # a dark branch on the left, unclipped
   rgb[:, 100:] = libraw_curve(sky)
   clipped = rgb.max(-1) >= 1.0
   rebuilt = np.tile(np.array([0.6, 0.95, 1.5], np.float32), (int(clipped.sum()), 1))  # blue far over white at once
   ```

   `recover_highlights` returns top > 1. Along row 0 from x = 100 inwards, in linear
   (`libraw_curve_inv` of the output), each pixel's channels divided by their largest: within 0.05 of
   `sky / sky.max()` at x = 100; equal (±1e-5) from x = 100 + round(`_RECOVER_EDGE` × 500) = 110 on.
   The output's max − min along that row never rises (±1e-6), and x < 100 is `array_equal` to the
   input. The first build fails the first of these (neutral at x = 100).
5. `_decode_raw` with a fake rawpy (as in `test_linear_srgb_decode_and_preview_fallback_have_auditable_provenance`
   in `tests/test_workflows.py`; the fake gets `camera_whitebalance = [2.0, 1.0, 1.5, 1.0]` and returns
   the default frame as uint16 for a call without `highlight_mode`, and `v / 2 × 65535` on the clipped
   block for `highlight_mode == 2`):
   - `raw_recover` off: one `postprocess` call with today's keyword arguments, the result equal to
     the default frame, `decode_info` without `highlight`.
   - On, with a frame that has no channel at 65535: one call, the same pixels.
   - On, with the synthetic frame: a second call with `highlight_mode == 2`, `gamma == (1.0, 1.0)`,
     `no_auto_bright` true; `decode_info["highlight"] == "recover"`, `recover_top` 1.6; every pixel
     whose largest channel is at or below `libraw_curve(0.75)` equal to the off decode.
   - On, with one clipped pixel in 2400 (0.04 %): one call, `recover_skipped` mentions 0.2%.
   - On with `raw_mode = "linear_srgb"`: no call with `highlight_mode`, `recover_skipped` names the
     mode.
6. Settings, following `test_cli_and_api_carry_the_setting` in `tests/test_denoise.py`:
   `--raw-recover`, `[decode] recover = true`, `service.make_cfg({"raw_recover": True})` each reach
   `RunConfig`; the default is False; `raw_opts_from(RunConfig(raw_recover=True))["raw_recover"]` is
   True.
7. Only with `AUTOEDIT_RAW_CORPUS` set (as `test_real_raw_lift_when_corpus_available` in
   `tests/test_raw_develop.py`): for 2 files, half size, recovery off and on: the pixels at or below
   the knee are `array_equal`; the median luma differs by less than 1/255; the share of white pixels
   (all channels ≥ 0.985) is not higher with recovery.

## Acceptance (the session)

- Both suites (system Python and `.venv`); the stub smoke run; a default render byte-identical to
  the baseline.
- Tone gate, default and with the setting on:
  `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render
  --fail-on-regression --json <file>`, then the same with `--set raw_recover=true`: 0 dark to darker
  and 0 pushed into clipping in both.
- The gate compares the edit with the recovered decode (`common = work` when `raw_mode` is `none`),
  so also compare the two runs photo by photo (UNATTENDED_RUN.md, section 8): the output's median
  luma, its share of white pixels, and `safety_fallback`. Expected: medians within a level, less
  white, no photo newly pulled back.
- A sheet of 100 % crops of the clipped areas of 0012, 0045, 0066 and 0109, default against
  recovered, judged by the Checker; then a blind gallery for the owner.
- The decode time per photo, with and without the setting.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Default | Off (`--raw-recover`, `[decode] recover`) | `recover = true` under `[decode]` in `defaults.toml`, after the blind vote |
| Colour of rebuilt pixels | The default decode's colour at the edge of a clipped area, fading to neutral with how far the RAW passes white (full at 0.25 above the old white) times the distance into the area (full at 2 % of the long side): the camera renders these areas near white; LibRaw's blend colours were pink and lavender; the fade by value alone drew a rim and blotches (Result) | `_RECOVER_FADE`, `_RECOVER_EDGE` in `imageio.py` (a very large fade keeps the default decode's colour; a tiny edge gives back the fade by value alone) |
| Knee | 0.75 linear (221 of 255): about 2 levels darker above it for most of the range back (table) | `_RECOVER_KNEE` |
| Small clipped areas | A frame with less than 0.2 % clipped is left as decoded: a few specular pixels are not worth reshaping every bright tone | `_RECOVER_MIN_SHARE` |
| Other `raw_mode`s | Not applied, and `decode_info` says so: they are benchmark candidates; `libraw_auto` stretches on its own | A task: in the linear modes the shoulder replaces the final `np.clip`. It comes first if the owner makes `preview_ev_headroom` the default (the open F8 decision) |
| Where the constants live | Module constants in `imageio.py`, like `_HEADROOM_MARGIN_EV` (the decode reads no rules file) | RunConfig fields, if the owner wants them as settings |
| Mask cache | Unchanged: a recovered photo is segmented once more | none intended |
| Name | `raw_recover`; F6's `raw_highlight` stays removed | a rename |

## Result

### Colour fade, after the Checker's crops of the first build (2026-11-19)

The first build faded colour by value alone, `w = clip((max(rebuilt) − 1) / fade)`. On a saturated
sky the RAW holds one channel far over white right at the edge of the clipped area, so `w` jumped to
1 within a pixel or two: on 0109 a blue rim round the foliage against a now-white sky, on 0045 white
blotches inside the peach glow. The revision (T5) multiplies `w` by the distance into the clipped
area (`_RECOVER_EDGE`): the edge keeps the default decode's colour, the inside still goes neutral.

Re-measured in the planner's scratch folder, full resolution, renders of the stub pipeline,
default → first build → revision:

| Photo | Chroma step at the clip edge, mean (p90) | Chroma 32 px or more inside | Render median luma |
|---|---|---|---|
| 0109 | 7.0 (16.9) → 13.9 (37.3) → 7.5 (20.0) | 50.9 → 3.2 → 7.0 | 76.32 → 76.30 → 76.16 |
| 0045 | 2.5 (4.7) → 4.9 (9.0) → 3.7 (6.1) | 17.7 → 2.5 → 2.8 | 100.51 → 100.75 → 100.58 |
| 0012 | 10.3 (24.5) → 15.8 (33.6) → 12.7 (31.9) | 2.2 → 0.8 → 0.8 | 115.22 → 115.03 → 115.04 |
| 0066 | 8.4 (22.5) → 9.8 (24.3) → 9.0 (22.4) | 0.6 → 0.2 → 0.2 | 95.45 → 95.44 → 95.45 |

- Chroma step: |Δ(max − min)| of the render, 8-bit levels, between horizontal neighbours of which
  exactly one is clipped in the default decode (the pairs of the Checker's luma step; the Checker's
  own chroma figures came from a script that was not kept, so their absolute values differ). Chroma
  inside: the casts (cyan on 0012, 0066 and 0109, yellow near the sun on 0045).
- 0045's blotches, as the 99th percentile of band-pass chroma (3 px box minus 24 px box) inside the
  clipped area: 4.5 → 10.6 → 5.1. In the crops the glow is peach again, with no white patches.
- 0109: the sky next to the foliage keeps the default's cyan and fades to near white over `r`
  (116 px), so the blue fringe sits in cyan as in the default; the open sky stays near white.
  0012 and 0066 look as in the first build.
- Kept: pixels at or below the knee bit-identical (full and half size, 0012, 0039, 0045, 0066, 0078,
  0109); render white share 0 except 0109's 0.02 % (as in the first build); shoulder top within 0.03;
  no pink or lavender (the colour is the default decode's or neutral). Cost: the box blur takes
  0.05 s on 22 MP with OpenCV, 0.3 s with numpy.
- Rejected after measuring: fading by the smallest channel (no rim, no blotch, but the casts came
  back: 0012's inside chroma 2.3, the default's, and a yellow patch with a noisy edge near 0045's sun);
  the blurred mask without the value term (0045 step 8.4: barely clipped pixels between cloud wisps
  went neutral); eroding the mask before the blur (0109 step 6.1, but cyan and yellow halos round small
  unclipped specks, depending on a threshold).

### Tone gate and run comparison, after T5 (2026-11-19)

Tone gate with `raw_recover` on: 0 dark made darker, 0 pushed into clipping, 0 hard failures (the
same as the default run). The photo-by-photo comparison with the default run does **not** meet this
spec's expectation "no photo newly pulled back": the safety step now pulls back 5 of 110 photos
that it left alone before.

| Photo | Render median, default → recovered (of 255) | Edit kept |
|---|---|---|
| 0109 (dark) | 76.8 → 64.2 | 50 % |
| 0033 (dark) | 43.6 → 38.5 | 75 % |
| 0066 (snow) | 94.7 → 89.8 | 75 % |
| 0028 (blue sky) | 86.7 → 83.1 | 75 % |
| 0103 (snow) | 157.8 → 155.5 | 75 % |

Every other photo's median moves by at most 2 levels. The render's white share falls on 12
photos, rises on 21 (by 0.04 % of the frame at most). The feature stays off by default and is not
ready for a blind vote until the two causes below are fixed.

### Why the five are pulled back (2026-11-21)

Each photo rendered with the stub on a 1024 px proxy, recovery off and on; the check that failed
on the full edit:

| Photo | Failed check | Highlights / whites, off → on | Source pixels with a clipped channel, off → on |
|---|---|---|---|
| 0109 | `introduced_rgb_high_clipping` (2.8 % of the frame) | −8 / −3.2 → 0 / 0 | 15.4 % → 0.06 % |
| 0033 | `introduced_rgb_high_clipping` (0.54 %) | −0.2 / 0 → 0 / 0 | 0.27 % → 0.01 % |
| 0028 | `introduced_rgb_high_clipping` (0.68 %) | −8 / −3.2 → −0.1 / 0 | 12.7 % → 0.15 % |
| 0066 (snow) | `encoded_highlight_texture_loss` | −8 / −3.2 → −8 / −3.2 | 31.5 % → 27.2 % |
| 0103 (snow) | `encoded_highlight_texture_loss` | −8 / −3.2 → −5 / −0.9 | 34.8 % → 7.4 % |

1. **Clipping (0109, 0033, 0028).** The `highlights` and `whites` rules pull down in proportion to
   the share of clipped pixels (`highlight_clip`, `channel_highlight_clip`). Recovery puts those
   pixels just under white, so the rules see nothing to protect and the pull drops to about 0. The
   exposure rule still allows about +0.25 EV on a frame with no headroom (its low-headroom
   allowance, which relies on that pull holding the top). The lift pushes the recovered pixels back
   over 0.98 in one channel, on more than the 0.5 % budget.
2. **Texture (0066, 0103).** Recovery gives the snow shading where the default decode had flat white.
   The edit's strong pull (still driven by the snow that stays clipped) flattens that shading below
   the check's 10 % retention in at least one patch. With the default decode there was no shading
   to lose, so the check had nothing to measure.

### Fix for cause 1 (2026-11-21)

A new global stat, `recovered_highlight`: the share of the frame that was clipped before recovery
rebuilt it (`decode_info["recover_clipped"]`, only when `highlight == "recover"`, else 0). The six
global `highlights` and `whites` rules in `rules.toml` read `max(highlight_clip,
channel_highlight_clip, recovered_highlight)`, so recovered pixels are held as the clipped ones
were. Without recovery the stat is 0 and the rules give the same values (the smoke renders are
byte-identical). Test: `test_raw_recover.py::test_recovered_share_drives_the_highlight_rules`.

Tone gate with `raw_recover` on, after the fix: 0 dark made darker, 0 pushed into clipping, no
errors. Edit kept, default → before the fix → after:

| Photo | Edit kept | Render median of 255, default → after |
|---|---|---|
| 0109 | 100 % → 50 % → **100 %** | 76.8 → 76.8 |
| 0028 | 100 % → 75 % → **100 %** | 86.7 → 86.4 |
| 0033 | 100 % → 75 % → 75 % | 43.6 → 38.5 |
| 0066 (snow) | 100 % → 75 % → 75 % | 94.7 → 89.8 |
| 0103 (snow) | 100 % → 75 % → 75 % | 157.8 → 154.8 |

No other photo's edit kept changed.

### 0033: recovery no longer adds headroom (2026-11-22)

0033 still failed narrowly after the first fix: 0.52 % of the frame newly clipped against a 0.5 %
budget. The shoulder also darkens the unclipped pixels above the knee, which raised `headroom_ev`,
so exposure rose from +0.23 to +0.26 EV. Fix: when recovery ran, `stats.compute` undoes the
shoulder on its own p99.5 (`imageio.unshoulder`, the inverse of `shoulder`, capped at 1, with
`decode_info["recover_top"]`), so headroom is what the default decode had. It works on the picture's
own percentile, so it holds at any proxy size. A first version took the default decode's p99.5 at
full resolution; the Opus check found that a full-resolution value sits higher than a proxy's (small
clipped spots average down on the proxy), which removed headroom the default had on frames with
small clipped spots. 0033 now keeps 100 % of its edit (exposure +0.25); 0109 and 0028 stay at
100 %. Test: `test_recovery_keeps_the_default_headroom`.

### Cause 2: the two "snow" photos fail in the sky (2026-11-22)

Crop sheets (1024 px proxy: default decode, default edit, recovered decode, recovered edit before the
safety blend, the 75 % blend, and the edit with the global highlights and whites halved):
- **0066:** one patch fails, in the top sky (retention 0.086; default edit 0.444). Halving the pull
  passes (0.206).
- **0103:** four patches fail, all sky and cloud by the summit (0.083 to 0.092; default edit
  0.134). Halving the pull makes it worse (0.070).
- What is lost is faint cloud near white. The default decode has no cloud there (flat white) and the
  default edit turns that white into flat light grey. Recovery brings the cloud back and the edit's
  pull (global highlights −8, whites −3.2, driven by the snow and sky that stay clipped) flattens it
  into the same grey. Rock edges stay crisp; this is not noise.
- So a smaller pull is not the fix. A pull that keeps local contrast in bright sky would be, and that
  is op math (`edit/ops.py`), a protected area: it needs a Planner spec and the owner's look decision.

### Tone gate after both fixes (2026-11-22)

`raw_recover` on: 0 dark made darker, 0 pushed into clipping, no errors. Against the default run,
the only photos whose edit kept changes are 0066 and 0103 (100 % → 75 %, cause 2); every other
photo's render median moves by at most 2 levels. A blind gallery of the 48 photos whose decode
recovery changes (stub backend, recovery off vs on, no safety blend) is built for the owner's vote
with `scripts/blindtest.py --configs-a/--configs-b`.

### F54 follow-up: retain recovered detail (2026-11-22)

[F54](F54-editing-workspace.md) adds a bounded, configurable restoration of existing source
luminance detail only after actual RAW recovery. Independent crop checks improve encoded
texture retention on both 0066 and 0103. The final integrated 110-photo recovery report keeps
0066 at 100%; 0103 still uses the existing 75% safety blend. Default and recovery-enabled runs
both finish with zero dark photos made darker, zero pushed into clipping and no errors.
RAW recovery stays off by default, and quality thresholds are unchanged.
