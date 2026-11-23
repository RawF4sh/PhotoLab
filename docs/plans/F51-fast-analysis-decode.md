# F51. Fast analysis decode: a half-size RAW to decide on, and a faster preview encode

Status: T1, T2 and T3 built (2026-11-22); T4, T5 open. Size M. Builds on F54's preview cache (`web/service.py::_preview_images`,
uncommitted when this was written). Default output stays byte-identical: `fast_analysis` is off by
default (`--fast-analysis`), and the encode change gives the browser the same pixels.

## Goal

A sharp preview of a 22 MP RAW in the interface takes about 13 s; the analysis needs only a 1536 px
picture, yet every decode develops the full 22 MP.

1. **`fast_analysis` (opt-in).** A RAW file's analysis picture is LibRaw's half-size development
   (`half_size=True`, already a knob of `_decode_raw`). An export still develops at full size and
   applies there; the preview and the analysis-only passes never develop at full size.
2. **The preview encode** (found while measuring, the largest cost): the two lossless WebP pictures
   of a preview at the fast effort instead of the slowest. Same pixels. The other idea, a cache of
   full decodes, is not built (see "Not built").

## Measured (2026-11-22)

Code of commit cab4da0 (before F54); Canon 5D Mark III CR2 (5796 × 3870); the first 10 files of
`curated-dolomites-summer-2026/selected_raw` (one per category) unless a row says 110; M1 Max,
system Python, stub backend. Other jobs loaded the machine (load 6 to 200): compare within a table.
Scripts and raw numbers stayed in the planner's scratch folder.

| A RAW load, median ms | Full size | Half size |
|---|---|---|
| LibRaw unpack (decompression, the same for both) / `postprocess` | 442 / 941 | 442 / 103 |
| `load_image`: default / `raw_recover` (3 of 10 recover) / `lens_ca` | 1442 / 1887 / 2681 | 590 / 693 / 1169 |
| the 1536 px analysis proxy made from it | 174 | 65 |

| A request, median ms | Decode | The rest | Total |
|---|---|---|---|
| Export, `process_one` at 22 MP | 1442 | apply 6388; proxy, stats, checks 1292 | 9272 |
| Sharp interface preview (`service._edit`) | 1442 | downscales 499; process at 4096 px 4488; two WebP 7010 | 13149 |
| The same from the half-size development | 590 | process at 2898 px 2911; two WebP 5281 | 8672 |
| Photo screen placeholder, `thumb?px=1600` | 1442 | 279 | 1721 |

- The demosaic is 64 % of a load and the unpack 32 %: half size is 2.4 times faster, not 9 (257 MiB
  of float32 at full size, 64 MiB at half). The decode is 11 % of a preview and 16 % of an export.
- The two lossless WebP pictures are 52 % of a preview. One 2560 px picture: `quality=100, method=4`
  (today) 3191 ms, 4.79 MB; `quality=50, method=0` 321 ms, 5.09 MB, the same pixels decoded (checked).
- Projected for a sharp preview (F54 already skips the decode of a repeat): first 13.1 → 7.9 s and
  repeat 11.5 → 6.3 s with the encode change; 5.1 and 4.4 s with `fast_analysis` as well.

**What the half-size development changes.** Each photo analysed on the proxy of each development
(`process_one(do_apply=False)`); |Δ| of the slider ops in their ±100 units.

| Options | Photos | Proxy \|Δ\| of 255, median mean (p99) | exposure \|Δ\| max | Other ops \|Δ\| max | Flips |
|---|---|---|---|---|---|
| default | 110 | 0.79 (6.6) | 0.009 EV | whites 1.0, rolloff 0.8, temp 0.7, rest ≤ 0.5 (0051 aside) | 1 scene |
| `raw_recover` | 10 | 0.85 (6.3) | 0.002 EV | whites 0.5, rest ≤ 0.1 | 0 |
| `lens_ca` at full size, skipped at half | 10 | 0.88 (6.8) | 0.002 EV | whites 0.7, rest ≤ 0.1 | 1 region |

- Flips are the stub's colour guesses at a threshold. On 0051 `skin` passes its minimum coverage and
  the scene turns from landscape to people (blacks −14.5 → −10.0, shadows 12.8 → 9.7, temp and tint
  dropped): its export moves 1.3 of 255 on average, 28 % of pixels by more than 2 levels. On 0004
  with `lens_ca` the stub's `water` falls under its minimum coverage.
- Exports analysed at half size and applied at full size, against today (the 10 photos, default
  options and `raw_recover` alike): mean |Δ| 0.005 to 0.11 of 255 (median 0.04), p99 at most 1.4,
  at most 1 % of pixels by more than 2 levels.
- The gray-card white-balance stats move the most (median 0.35; more than 10 units on 7 photos, up to
  73), all in frames the stub calls people, where the rules set no white balance.
- `raw_recover` works at half size: the same verdict on all 10 (3 recover, 7 under the 0.2 % floor),
  `recover_top` within 0.001, the clipped share within 0.14 points. `lens_ca` does not: its fringes
  there are unrelated to the full-size ones (0002: −0.03 px at full size, −1.74 at half; 0006: −0.03,
  −2.18; 0007 blue: −0.33, +1.41), so it applies a wrong correction, probably because half size takes
  red, green and blue from different photosites of each 2 × 2 cell. F46's open nit is this bug.
- Consistency pass 1 (`quick_lum`, 110 photos): |Δ| at most 0.0005; the same groups, biases within
  0.0005.
- The 10 linear DNGs in `testImages`: half size returns the full picture, bit-identical.

| Tone gate, 110 RAWs, `--render` ("half": every decode of the report at half size, by a patched `_RAW_DEFAULTS`) | Dark to darker | Pushed into clipping | Safety fallbacks |
|---|---|---|---|
| full, default options (today) | 0 | 0 | 12 |
| half, default options | 0 | 0 | the same 12, by the same factors |
| half, `raw_recover` | 0 | 0 | those 12 and 0015 (0.75: `encoded_highlight_texture_loss`; at full size with `raw_recover` it keeps its edit) |

**Verdict.** In tone the mode is as safe as today: 0 and 0 in every arm, and with default options
the same photos fall back by the same amounts. In look it moves the decided settings by at most 1 of
±100, exposure by at most 0.02 EV and an export by 0.04 of 255 on average, except where a decision
sits on a threshold: one scene flip in 110 (stub), and with `raw_recover` one more safety fallback.
So it ships opt-in; only the owner's blind vote, with SAM 3 (not measured here), can make it the
default (D2). Its gain is the interface and the analysis-only passes; a batch export gains little.

## Design

### 1. `fast_analysis`

`imageio.py`: `_decode_raw` adds `info["half_size"] = True` when it develops at half size (nothing
otherwise). `load_image` skips `lens_ca` on a half-size RAW development (`decode.get("half_size") and
is_raw(path)`) and records `decode_info["lens_skipped"] = "half-size development"`; other formats
ignore `half_size` and are still corrected. Beside `raw_opts_from`, a new
`analysis_opts(cfg, path) -> dict | None` returns `{**raw_opts_from(cfg), "half_size": True}` for a
RAW file (`is_raw`) when `getattr(cfg, "fast_analysis", False)`, else None.

`pipeline.py::process_one`, stage 1 only (the order of the stages does not change):

```python
        # F51: a RAW file's analysis picture is its half-size development. With nothing to apply that is
        # the whole decode; otherwise the full development is decoded as well, and the edit applied to it.
        fast = analysis_opts(cfg, path) if image is None and proxy is None else None
        img: Image = image if image is not None else load_image(
            path, decode=fast if fast is not None and not do_apply else raw_opts_from(cfg))
        ...  # on_stage as today
        given_proxy = proxy is not None
        if fast is not None and do_apply:
            proxy, given_proxy = downscale_long_edge(load_image(path, decode=fast), cfg.analysis_long_edge), True
        if proxy is None:
            proxy = downscale_long_edge(img, cfg.analysis_long_edge)
```

Beside the `raw develop` note: `if proxy.decode_info.get("half_size"): notes.append("fast analysis:
the edit was decided on the half-size development of the RAW file")`.

`analysis_opts(cfg, path) or raw_opts_from(cfg)` replaces `raw_opts_from(cfg)` in:
- `pipeline.py::presegment`: a render worker then makes the same proxy, so the mask cache hits.
- `web/service.py::_preview_images` (F54), for the decode and in its cache key (without F54: where
  `_edit` and `make_variants` decode). Previews and variants then start from the half-size picture
  (2898 px, more than a sharp preview's 2560; the ops scale their radii with the frame) and analyse
  the export's proxy.
- `scripts/tone_report.py::rows_by_render`, so `--set fast_analysis=true` gates the mode.

`consistency.py`: `quick_lum(path, px=384, half=False)` decodes `{"half_size": True}` when `half`,
and `plan` passes `half=getattr(cfg, "fast_analysis", False)` to `_brightness_plan`; the other plans
call `process_one(do_apply=False)`. The other decoders (`quality_runner`, `experiments`, look cards,
`calibrate`, `curate`, `pick`) stay as they are.

Settings, wired as `lens_ca` is: `RunConfig.fast_analysis: bool = False`; `defaults.toml` `[decode]
fast_analysis = false`; `cli.py` `_dec_map`, `--fast-analysis` (`store_true`, `default=None`) and the
override map; `"fast_analysis"` in `service._ALLOWED`; `settings.toml` `[setting.fast_analysis]`,
label "Faster RAW previews", group "raw", help "Decides the edit on a half-size development of the
RAW file, so previews come faster. The saved photo is still developed at full size; its edit can
differ very slightly."; then `AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest
tests/test_settings_schema.py -q`.

### 2. Preview encode (`web/service.py::_img_b64`)

`pil.save(buf, format="WEBP", lossless=True, quality=50, method=0)` instead of `quality=100,
method=4`; the comment says why (the same pixels, ten times faster, 6 % larger).

### Not built: a cache of full decodes

A save decodes the photo again at full size (1.4 s of a 9.3 s save; keeping one costs 257 MiB), and
with the mode on also at half size (0.6 s) though F54's cache holds that proxy. `# ponytail: run_one
could pass F54's cached proxy; a byte-bounded LRU of full decodes in load_image if saves feel slow.`

## Tasks

F22, F53 and F54 change `pipeline.py`, `contract.py`, `cli.py`, `web/` and `settings.toml`: start
after they are committed.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Session (one line) | Design 2, test 6 | `PYTHONPATH=src python3 -m pytest tests/test_web_service.py -q` |
| T2 | Builder (Sonnet, medium) | Design 1, tests 1 to 5 | `PYTHONPATH=src python3 -m pytest tests/test_fast_analysis.py tests/test_lens.py tests/test_raw_recover.py tests/test_raw_develop.py tests/test_consistency.py tests/test_web_service.py tests/test_settings_schema.py tests/test_cli.py tests/test_workflows.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Session, after the owner's yes on D1 | `thumb_bytes`: `load_image(path, {"half_size": True})`; test 7 | the T1 command |
| T4 | Tweaker (Haiku, low) | A CHANGELOG line; the F51 row of `FEATURE_PLAN.md` | none |
| T5 | Checker (Opus, high) | The diff against this spec and `CLAUDE.md` (`pipeline.py` is protected); the definition of done | as in `CLAUDE.md` |

## Tests (`tests/test_fast_analysis.py`, new; 6 and 7 in `tests/test_web_service.py`)

RAW cases use a dummy `x.cr2` in `tmp_path` (F54's cache stats the file) and a fake `rawpy` as
`tests/test_raw_recover.py::_decode` does: `postprocess` records `half_size` and returns a 64 × 96
frame, or a 32 × 48 frame of other values at half size.

1. The fake RAW with `{"lens_ca": True, "half_size": True}`: no `"lens"`, a `"lens_skipped"`, the
   plain half-size pixels, `decode_info["half_size"]`; a PNG with the same options still gets
   `"lens"`; a full-size decode has no `"half_size"` key.
2. `process_one(fake RAW)`, stub: off → one full-size `postprocess`; on → full then half,
   `global_stats.mean_lum` from the half frame, the output at the full frame's shape, the note; on
   with `do_apply=False` → one call, at half size.
3. With the mode on, `presegment` and `consistency._brightness_plan` decode at half size.
4. `_preview_images` with the mode on decodes at half size; the same photo with it off decodes again,
   at full size (the key differs).
5. `--fast-analysis`, `[decode] fast_analysis = true` and `service.make_cfg({"fast_analysis": True})`
   reach `RunConfig`; the default is False.
6. (T1) `_img_b64(..., fmt="webp")` decodes to `_to_u8` of its input.
7. (T3) `thumb_bytes` of the fake RAW decodes at half size.

## Acceptance (the session)

Before T1 the session copies `src/` to its scratch folder: the old code, run with `PYTHONPATH`.
1. The definition of done in `CLAUDE.md`, on system Python and `.venv`.
2. Default byte-identical: `run` with the old and the new code (stub) on `test-corpus` and the first
   5 Dolomites RAWs: the same image bytes, the same `global_settings` and `masks` in every sidecar.
3. The mode on: `PYTHONPATH=src python3 scripts/tone_report.py
   curated-dolomites-summer-2026/selected_raw --render --fail-on-regression --set fast_analysis=true`,
   and again with `--set raw_recover=true` added: 0 dark to darker, 0 pushed into clipping, and the
   safety fallbacks of the half rows above (12; with `raw_recover` 13).
4. Speed, old and new code alternating per photo, first 10 RAWs, medians: `service.preview(path, {})`
   twice, once more with `{"fast_analysis": True}`, and `process_one(path)` with the mode off and on.
   Against the old code: first preview ≤ 70 %, repeat ≤ 65 %, first with the mode ≤ 50 %; an export
   with the mode ≤ 115 % of one without.
5. The interface (`studio-verify`): open a RAW, change the look twice, save; mode off and on; no error.

## Owner decisions

- **D1 (for T3 only; T1 and T2 do not wait):** RAW thumbnails in the interface always from the
  half-size development. A thumbnail is not an export; it takes about 0.7 s instead of 1.7 s, and the
  Folder grid decodes every RAW that has no JPEG beside it. Recommended: yes.
- **D2 (after F51):** `fast_analysis` on by default, only by the owner's blind vote with SAM 3 (see
  the verdict). Recommended: a blind sheet of 24 exports, half-size against full-size analysis.

## Decided here (reverse it by)

- The mode holds in a batch too, so a Folder export matches its previews: an export batch gains
  little (consistency pass −0.85 s a photo, second decode +0.5 s); the reveal check decodes 2.4×
  faster (ignore the mode in `batch.run`).
- A half-size RAW development is never lens-corrected, for every caller (drop the guard).
- WebP effort `quality=50, method=0` (the old values in `_img_b64`).
- Limits: only CR2 measured (X-Trans and other mosaics untested; a linear DNG pays a second decode at
  export for nothing); other `raw_mode`s and SAM 3 not measured; with the mode on the preview shows no
  lens correction (the export has it).
