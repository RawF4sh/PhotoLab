# F33. Merge frames: brackets, stacks, focus series, star trails

Status: spec (2026-11-17). Size L. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F33. Written in an
unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked; the owner's decisions are in the
table below. Built once on a scratch copy of the tree, deleted again: the reference code is that code, every number
is measured, both suites passed there on both interpreters (974 tests, 18 of them this spec's). The corpora hold no
real bracket, night series or focus series: the plan's real-set bar stays open.

## Goal

`autoedit merge FOLDER [-o DIR]` finds the frames of a folder that were shot to become one picture and merges each
group into one TIFF (16 bits with `tifffile`) with a record of the frames beside it: `average` (repeats, against
noise), `brackets`, `focus` (a focus series), `trails` (star trails). The TIFF is then a photo like any other for
`autoedit run`. A new command and module: no stage, run setting or default pixel changes; `pipeline.py` is not
touched. Content rule: every pixel of a result is made from the user's own frames at the same place; frames move by
whole pixels, nothing is resampled or generated. A frame more than 1 % of the long edge off refuses its group
(tripod frames only); something that moved is flagged, not repaired. No photo is written, moved or deleted; no file
a merge did not write is overwritten.

## What was found in the code

| # | Fact |
|---|---|
| 1 | `consistency.py::find_bursts(paths, max_gap_s, min_similarity, max_span_s=None)` returns runs of consecutive shots as flat file lists in capture order. The gap runs start to start; a RAW file and a JPEG of one capture second are one shot, both in the run; a file without `DateTimeOriginal` is in none. It compares `thumb_signature` (24 x 16 grey, row means out, unit variance) through the module name, because `test_consistency.py` replaces it. Callers: `_burst_plan` (F9), `pick.groups_of` (F35, four positional arguments) |
| 2 | `pick.shots_of` pairs RAW and JPEG by name, which the renamed corpora defeat (F35, known limits). `pick._target` is the inode check that `emit.output_paths(..., sources=)` makes already; `pick._not_ours` knows pick's four files only. Merge reuses `find_bursts`, `emit.source_ids` and `emit.output_paths`, and marks its own files by the record beside each TIFF |
| 3 | `imageio.load_image(path, {"raw_mode": "linear_srgb"})` develops a RAW file linear under the exact sRGB curve: `srgb_to_linear(Image.rgb)` is light. The default `none` uses libraw's 2.222/4.5 curve, which is not. A JPEG has the camera's tone curve. The option does nothing for other files |
| 4 | Exposure time (0x829A), f-number (0x829D) and ISO (0x8827) are read nowhere (`_read_exif` reads ISO and the bias dial). Pillow reads the Exif directory of JPEG, CR2, DNG, NEF and ARW, not of CR3, RAF, ORF and RW2 (F35, fact 3) |
| 5 | `save_image(fmt="tiff")` writes 16 bits through `tifffile` (the `accel` extra) and silently 8 bits without it; `load_image` reads 16 bits only with it. tifffile keeps the first directory's text tags only: a 16-bit TIFF has no capture time and no exposure tags. The system Python has pytest and no tifffile; `.venv` has tifffile and no pytest (`tests/run_all.py` runs there) |
| 6 | `emit.output_paths(out_dir, image, "tiff", taken, name=, sources=)` builds `<name>.tif`, numbers a second one in a run, creates the folder and raises `ValueError` ("would overwrite a source photo") when the target is an input by (device, inode) or `samefile`. Another program's file in the output folder is not protected. `emit.write_image(..., source=, metadata="shareable")` writes the sRGB profile and the source's EXIF without GPS and serials |
| 7 | `paths.read_config(path, profile)` merges a profile's file over one file; `paths.merge` is the deep merge. `pick.load_settings` reads one file, so a partial local `pick.toml` loses keys. `cli._cmd_doctor` lists the known profile files. `edit/filters.py::gaussian_blur` is 2-D only (OpenCV when installed, numpy else, 5e-6 apart) |
| 8 | `.gitignore`, `MANIFEST.in` and `scripts/check_public_repo.py::private_path` keep `*.tif`, `out-*/` and `*.autoedit.json` private. A `.merge.json` names the owner's photos and is covered by none |

## Measured

Without the owner. Photo folders were only read; results went to scratch.

| What | Result |
|---|---|
| Real sets in the corpora | None. Switzerland (800 files): no frame with the exposure mode "auto bracket"; its 8 manual long exposures (2 to 20 s) are single frames minutes to hours apart, neighbours alike at 0.56 or less. Dolomites: nothing longer than 0.1 s; its runs are one file stored twice. `testImages`: ten scenes, seven of them one frame of a bracket |
| The burst finder on brackets | 22 made-up brackets from real RAW files (linear development; 0, -2, +2 EV as JPEG with exposure tags): the finder alone keeps 17 whole, the step from -2 to +2 EV is under 0.8 in 5 (lowest 0.55). Compared at one exposure (`_alike`): 22 of 22, lowest 0.92. 1386 pairs of different pictures 2 to 4 stops apart: highest 0.61 |
| Phase correlation alone | Two frames each sharp in one half: pure phase correlation 23 px off (truth 2). The cross spectrum divided by the square root of its size: within 2 px, and a far shift (40 px) still exact; plain correlation lost the far shift. A squared-difference climb from there: exact. A thing that moved 60 px pulled the phase answer onto itself and the plain squared difference 1 to 2 px off: hence the start at no shift and the capped cost |
| Alignment, 8 real 22 MP photos | 144 made-up frames, shifts up to 40 px with 2 stops darker or brighter, noise 0.02, blur 3 px, half the frame blurred by 2 or 6 px, a pasted block that moved: 140 exact, the 4 misses one pixel off (all half blurred by 6 px, snow). Frames 66 px off: 8 of 8 refused (limit 57 there). 1.2 s per frame |
| Moved, or another picture? | Moved share of real hand-held pairs of one picture: 0.12 to 0.39; of real different pictures: 0.34 upward (median 0.66); made-up different scenes: 0.08 upward. No threshold separates them: no "another picture" refusal, the flag stays |
| Full size: brackets made from 4 real RAW files; focus and average | 0, -2, +2 EV with sensor-like noise: exposures measured 2.000 and 4.000 (worst 2.001); 0 of 22.4 million pixels clipped where a frame holds detail; dark tones 6.6 to 7.2 times cleaner than the darkest frame; no flag; 7.5 s per merge in memory. As JPEG files through `merge.run`: 4 groups, all `brackets`, 37 s. Focus and average: two frames each sharp in one half (blur 6 px): 0.00006 off the sharp photo against 0.017 for one frame, 4.9 s; on a snow photo one frame landed a pixel off (0.007 against 0.015). Eight frames, noise 0.0100: 0.0035 merged (the square root of 8 gives 0.0035), 15 s |
| The real shoot through the command | 11 groups in 50 s (dry run). The run: 6 refused (frames 60 to 648 px off, limit 58), 5 merged and all 5 flagged (hand-held), none failed; 118 s, peak memory 6.4 GB (a render needs about 5.3). Names, sizes and times of the 800 files unchanged; no path in a record. Dolomites `selected_raw` and `test-corpus`: no group |
| Merged brackets through the pipeline | The 4 full-size merges, `scripts/tone_report.py --render --backend stub --fail-on-regression`: 0 dark made darker, 0 pushed into clipping, 0 hard failures, `safety_fallback` 1.0 on all. The run lifts them 1.8 EV (median); they still end 0.65 to 1.0 EV under the middle frame run alone |
| The tests | 18, green under pytest (system Python) and under the stand-in of `run_all.py` on both interpreters, also with OpenCV, rawpy and tifffile hidden; 26 of 26 deliberate breaks of the code made one fail; two merges of one folder write the same bytes |

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| How bright is a merged bracket? | As bright as its darkest frame: linear, nothing a frame holds clipped, no tone curve invented. The rules lift it; it ends about 0.7 EV under the middle frame after a run (Measured) | A shoulder at the reference frame's exposure: a `[brackets]` key and about ten lines in pass 2. A look decision for a real bracket |
| Merge or exposure fusion? | A merge in linear light with well-exposedness weights, as the plan says. Fusion (Mertens) needs no linear frames but invents a tone map | A mode `fusion` of its own |
| Exposure offsets from EXIF or from the frames? | Measured (per channel, the median ratio where both expose well). The settings choose the mode and the reference and are recorded beside it (`settings_ev`): shutter speeds are rounded and a JPEG is not linear | `stops` into `merge_frames`, `gains` from `2 ** (stop - min)` |
| How is the shift found? | Whole pixels: phase correlation of small copies, then a capped squared-difference climb at full size. No resampling | A resampling step after `_climb` |
| A frame too far off | Refused: the group is not merged, the line says how far, the run goes on, exit code 1 | `max_shift` in `merge.toml` |
| Something moved | Merged, written, flagged on the console and in the record, exit code 0 | Two lines in `run` that skip a flagged group |
| Mode without `--mode` | `brackets` when every frame has settings more than 0.5 EV apart, else `average`; `focus` and `trails` only when named | `pick_mode`; `bracket_min_ev` |
| May the burst finder change? | It gains `alike=None` and two helpers split out of `thumb_signature`; without `alike` it is the finder of F9 and F35, unchanged | `alike=` out of `find_groups`: one bracket in five splits |
| Which files are frames? | One kind per group: the RAW files when there are at least two and at least as many as the others, else the others; RAW developed linear whatever `[decode] raw_mode` says; a file stored twice counts once | `find_groups`; `_RAW` |
| Where do results go, in how many bits? | `-o DIR`, default `out-merge` (the repo ignores `out-*`); `<first frame>_<mode>.tif` and `.merge.json`; the reference frame's EXIF, `shareable`; without `tifffile` 8 bits, said on the console and in the record | The parser default; `merge_group`; raise in `run` without `tifffile` |
| Studio and the API | Nothing: a command | The test UI |

## Design

### 1. Groups and the mode of each

`consistency.py` keeps its behaviour. The body of `thumb_signature` up to the resize becomes `thumb_grey(path) ->
np.ndarray | None` (the 24 x 16 grey thumbnail, 0 to 255; its docstring keeps the paragraph on the cheap decode),
the last two lines become `signature(g) -> np.ndarray` (row means out, unit variance; the paragraph on why the row
means go), and `thumb_signature(path)` is `g = thumb_grey(path)`, `return None if g is None else signature(g)`.
`find_bursts` gets a last parameter `alike=None`; its line `sigs = ...` becomes the lines below and the last
condition of its `if` becomes `and score(i, cur[-1]) >= min_similarity:`.

```python
    seen = {i: sorted(shots[i][1], key=is_raw)[0] for i in near}     # the file a shot is judged by
    sigs = {i: thumb_signature(p) for i, p in seen.items()}

    def score(i: int, j: int) -> float:
        s = float((sigs[i] * sigs[j]).mean())
        return s if alike is None or s >= min_similarity else max(s, alike(seen[j], seen[i]))
```

New `src/autoedit/merge.py`, core imports only (`gaussian_blur` from `.edit.filters`; `is_raw, linear_to_srgb,
load_image, luma, register_reader, srgb_to_linear` from `.imageio`): `MODES = ("average", "brackets", "focus",
"trails")`, `_RAW = {"raw_mode": "linear_srgb"}`.

- `exposure(path) -> (stops | None, seconds)`: `register_reader(path)`, `PILImage.open`,
  `getexif().get_ifd(0x8769)`; any exception gives `(None, 0.0)`. `_num(v)`: the float of `v` (of its first item for
  a tuple or list), None unless finite and above 0. Stops `math.log2(t * iso / 100.0 / n ** 2)` from 0x829A, 0x8827,
  0x829D, None if one is missing; seconds `t or 0.0`.
- `_distinct(files) -> (keep, copies)`: a file of the size of a kept one and equal to it by `filecmp.cmp(k, f,
  shallow=False)` is a copy.
- `find_groups(images, thr, everything=False) -> [{"frames", "left_out", "copies"}]`: the runs are
  `consistency.find_bursts(images, thr["max_gap_s"] + longest, thr["min_similarity"], None, alike=_alike)`,
  `longest` the largest `exposure(p)[1]` of the images (a night series starts one exposure time apart); with
  `everything` one run, `sorted(images, key=lambda p: (consistency.capture_time(p) or 0.0, p))`. Per run `files,
  copies = _distinct(files)`; `raw` the RAW files; `frames = raw if len(raw) >= max(2, len(files) - len(raw)) else`
  the others; `left_out` the rest; a group needs two frames.
- `pick_mode(stops, thr) -> str`: `"brackets"` when every stop is known and `max - min > thr["bracket_min_ev"]`,
  else `"average"`.

```python
def _alike(a: str, b: str) -> float:              # -1.0 when the settings are equal or unknown: the plain score stands
    (ea, _), (eb, _) = exposure(a), exposure(b)
    if ea is None or eb is None or abs(ea - eb) < 0.1:
        return -1.0
    if ea > eb:
        a, b, ea, eb = b, a, eb, ea
    ga, gb = consistency.thumb_grey(a), consistency.thumb_grey(b)
    if ga is None or gb is None or ga.shape != gb.shape:
        return -1.0
    lifted = linear_to_srgb(srgb_to_linear(ga / 255.0) * 2.0 ** (eb - ea)) * 255.0
    return float((consistency.signature(lifted) * consistency.signature(gb)).mean())
```

### 2. Alignment in whole pixels, and movement

Every frame is aligned to the reference frame at one exposure: the darker of the two is lifted and clips where the
brighter clips. Phase correlation of small copies (long edge `proxy_px`) searches the whole picture; a climb at full
size in a window (`window_px`, where the reference has most fine detail) gives the exact whole pixels, from no shift
(a tripod) and from that answer. The cost is the squared difference of the blurred lumas capped at the moved
tolerance, so a thing that moved weighs the same at every shift. A climb that runs into the limit means the frame is
too far off. Movement is judged on the aligned small copies.

- `_blocks(a, f, top=False)`: `a[:h, :w]` (sizes cut to multiples of f) reshaped to `(h // f, f, w // f, f,
  *a.shape[2:])`, then `.max(axis=(1, 3))` with `top`, else `.mean(axis=(1, 3))`.
- `_well(lin, top, b)`: `(top < b["high"][0]) & (luma(linear_to_srgb(lin)) > b["low"][1])`.
- `_gain(a, b, both)`: b's exposure over a's per channel, `np.median(b[both], axis=0) /
  np.maximum(np.median(a[both], axis=0), 1e-6)` as float32.
- `_lumas(a, b, g)`: at one exposure, `a = np.clip(a * g, 0, 1)` when `g.mean() >= 1.0`, else `b = np.clip(b / g, 0,
  1)`; returns `luma(linear_to_srgb(a)), luma(linear_to_srgb(b))`.
- `_slices(h, w, dy, dx)`: `(target, source)` with `out[target] = frame[source]`, i.e. `((slice(max(0, -dy), h -
  max(0, dy)), slice(max(0, -dx), w - max(0, dx))), (slice(max(0, dy), h - max(0, -dy)), slice(max(0, dx), w -
  max(0, -dx))))`.
- `_small(lin, rgb, m, dy, dx, f)`: with `dy, dx` clipped to `[-m, m]` and `win = (slice(m + dy, h - m + dy),
  slice(m + dx, w - m + dx))` (the reference's inner region), `(_blocks(lin[win], f), _blocks(rgb[win].max(axis=2),
  f, top=True))`.
- `_phase` divides the cross spectrum by the square root of its size: full whitening gave the fine detail, where a
  sharp and a soft frame disagree, as much say as the shapes (Measured).

```python
def _phase(a, b) -> tuple[int, int]:
    """Whole pixels (dy, dx) by which b's content sits further down and right than a's."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1])).astype(np.float32)
    cross = np.fft.rfft2((b - b.mean()) * win) * np.conj(np.fft.rfft2((a - a.mean()) * win))
    c = np.fft.fftshift(np.fft.irfft2(cross / (np.sqrt(np.abs(cross)) + 1e-12), s=a.shape))
    y, x = divmod(int(np.argmax(c)), c.shape[1])
    return y - a.shape[0] // 2, x - a.shape[1] // 2           # zero shift sits in the middle

def _climb(a, b, dy: int, dx: int, reach: int, cap: float) -> tuple[float, int, int]:
    """(cost, dy, dx); b is a's place with a rim of `reach`: b[reach + y + dy, reach + x + dx] lies on a[y, x]."""
    h, w = a.shape
    seen: dict = {}
    def cost(y: int, x: int) -> float:
        if (y, x) not in seen:
            d = b[reach + y:reach + y + h, reach + x:reach + x + w] - a
            seen[(y, x)] = float(np.minimum(d * d, cap * cap).mean())
        return seen[(y, x)]
    while True:
        best = min((cost(dy + j, dx + i), dy + j, dx + i) for j in (-1, 0, 1) for i in (-1, 0, 1)
                   if max(abs(dy + j), abs(dx + i)) <= reach)
        if best[1:] == (dy, dx):
            return best
        dy, dx = best[1:]

def _moved(a, b, both, thr: dict) -> float:       # b gets a's tones rank for rank: exposure and tone curve are out
    la, lb = (gaussian_blur(luma(linear_to_srgb(s)), thr["blur"]) for s in (a, b))
    av, bv = la[both], lb[both]
    matched = np.empty_like(bv)
    matched[np.argsort(bv, kind="stable")] = np.sort(av)
    return float((np.abs(matched - av) > thr["tolerance"]).mean())

class _Reference:
    def __init__(self, lin, rgb, f: int, m: int, thr: dict):
        self.f, self.m = f, m
        self.small, self.top = _blocks(lin, f), _blocks(rgb.max(axis=2), f, top=True)
        h, w = lin.shape[:2]
        r = m + 1                              # every frame must hold the window plus this rim
        s = min(int(thr["align"]["window_px"]), h - 2 * r, w - 2 * r)
        self.win = None
        if s >= 16:
            d = luma(linear_to_srgb(self.small))
            detail = (d - gaussian_blur(d, 1.0)) ** 2
            spots = [(y, x) for y in np.linspace(r, h - r - s, 3).astype(int)
                     for x in np.linspace(r, w - r - s, 3).astype(int)]
            self.y0, self.x0 = max(spots, key=lambda p: float(
                detail[p[0] // f:(p[0] + s) // f + 1, p[1] // f:(p[1] + s) // f + 1].mean()))
            self.s, self.win = s, lin[self.y0:self.y0 + s, self.x0:self.x0 + s].copy()

    def shift(self, lin, rgb, b: dict, cap: float) -> tuple[int, int]:
        small, top = _blocks(lin, self.f), _blocks(rgb.max(axis=2), self.f, top=True)
        both = _well(self.small, self.top, b) & _well(small, top, b)
        g = _gain(self.small, small, both) if both.mean() >= b["min_overlap"] else np.ones(3, np.float32)
        dy, dx = (v * self.f for v in _phase(*_lumas(self.small, small, g)))
        if self.win is None:
            return dy, dx
        y, x, s, r = self.y0, self.x0, self.s, self.m + 1
        a, bb = (gaussian_blur(v, 1.0) for v in _lumas(self.win, lin[y - r:y + s + r, x - r:x + s + r], g))
        starts = {(0, 0)} | ({(dy, dx)} if max(abs(dy), abs(dx)) <= self.m else set())
        _, ry, rx = min(_climb(a, bb, *p, r, cap) for p in starts)
        if max(abs(ry), abs(rx)) > self.m and max(abs(dy), abs(dx)) > self.m:
            return dy, dx                      # too far off: say how far, as the small copies have it
        return ry, rx
```

### 3. The merge

`merge_frames(load, n, mode, thr, align=True, ref=None) -> (rgb, found)`. `load(k)` returns frame k as float32 RGB
in 0..1 under the sRGB curve; each frame is loaded twice, one held at a time. `found` is `{"reference", "frames":
[{"shift", "moved", "exposure_ev" (brackets)}], "flags", "cut"}`. First: `ValueError(f"unknown merge mode {mode!r};
choose one of {MODES}")`, and `ValueError("a merge needs at least two frames")` for n < 2; `ref = n // 2 if ref is
None else ref`; `a, b, mv = thr["align"], thr["brackets"], thr["moved"]`; the inner `frame(k)` returns `load(k)` and
raises `ValueError(f"frame {k + 1} has another size than the others")` when its shape differs from the first frame
loaded (a `nonlocal shape`).

```python
    check = mode != "trails"                  # trails: the sky moves by design; a search could follow it
    shifts, smalls = [(0, 0)] * n, [None] * n
    reference = sharpest = choice = None
    for k in [ref] + [k for k in range(n) if k != ref] if check else ():   # pass 1: where each frame sits
        rgb = frame(k)
        h, w = rgb.shape[:2]
        f = max(1, math.ceil(max(h, w) / a["proxy_px"]))
        m = max(1, math.ceil(a["max_shift"] * max(h, w)))
        lin = srgb_to_linear(rgb)
        if k == ref:
            reference = _Reference(lin, rgb, f, m, thr) if align else None
        elif align:
            dy, dx = reference.shift(lin, rgb, b, mv["tolerance"])
            if max(abs(dy), abs(dx)) > m:
                raise ValueError(f"frame {k + 1} sits {max(abs(dy), abs(dx))} px off frame {ref + 1}; the limit is "
                                 f"{m} px ({a['max_shift']:.1%} of the long edge): frames from a tripod only")
            shifts[k] = (dy, dx)
        smalls[k] = _small(lin, rgb, m, *shifts[k], f)
        del lin
        if mode == "focus":                   # per pixel of the reference grid: which frame is sharpest
            fo = thr["focus"]
            l = luma(rgb)
            sharp = gaussian_blur((l - gaussian_blur(l, fo["detail_sigma"])) ** 2, fo["window"] * max(h, w))
            if sharpest is None:
                sharpest, choice = np.full((h, w), -1.0, np.float32), np.zeros((h, w), np.int16)
            tgt, src = _slices(h, w, *shifts[k])
            better = sharp[src] > sharpest[tgt]
            sharpest[tgt][better] = sharp[src][better]
            choice[tgt][better] = k
    reference = sharpest = None
    info = [{"shift": list(shifts[k]), "moved": None} for k in range(n)]
    flags = []
    if check:
        well = [_well(*s, b) for s in smalls]
        for k in range(n):
            both = well[ref] & well[k]
            if k != ref and both.mean() >= b["min_overlap"]:
                info[k]["moved"] = round(_moved(smalls[ref][0], smalls[k][0], both, mv), 4)
        if any((i["moved"] or 0.0) > mv["max_share"] for i in info):
            flags.append("moved")
    gains = [np.ones(3, np.float32)] * n
    order = list(range(n))
    if mode == "brackets":                    # darkest first; each frame's exposure against the darkest
        order = sorted(range(n), key=lambda k: float(np.median(luma(smalls[k][0]))))
        for lo, hi in zip(order, order[1:]):
            both = well[lo] & well[hi]
            if both.mean() < b["min_overlap"]:
                raise ValueError("two neighbouring exposures share too little that both expose well: "
                                 "the steps of the bracket are too far apart")
            gains[hi] = gains[lo] * _gain(smalls[lo][0], smalls[hi][0], both)
        for k in range(n):
            info[k]["exposure_ev"] = round(float(np.log2(gains[k].mean())), 3)
    dys, dxs = [s[0] for s in shifts], [s[1] for s in shifts]
    acc = cnt = None
    for k in range(n):                        # pass 2: the picture, on the part every frame shows
        full = frame(k)
        if acc is None:
            h, w = full.shape[:2]
            y0, y1, x0, x1 = max(0, -min(dys)), h - max(0, max(dys)), max(0, -min(dxs)), w - max(0, max(dxs))
            acc = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
            cnt = np.full(acc.shape[:2], -1.0 if mode == "trails" else 0.0, np.float32)
        dy, dx = shifts[k]
        rgb = full[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
        del full
        lin = srgb_to_linear(rgb)
        if mode == "average":
            acc += lin
        elif mode == "trails":                # cnt holds the brightest luma so far; a tie keeps the earlier frame
            l = luma(rgb)
            brighter = l > cnt
            acc[brighter], cnt[brighter] = lin[brighter], l[brighter]
        elif mode == "focus":
            wgt = gaussian_blur((choice == k).astype(np.float32), thr["focus"]["soften"] * max(h, w))[y0:y1, x0:x1]
            acc += wgt[..., None] * lin
            cnt += wgt
        else:                                 # brackets
            wgt = np.full(lin.shape[:2], float(gains[k].mean()), np.float32)   # more light, less noise
            if k != order[0]:                 # near white in any channel: only the darkest frame may speak
                wgt *= np.clip((b["high"][1] - rgb.max(axis=2)) / (b["high"][1] - b["high"][0]), 0.0, 1.0)
            if k != order[-1]:                # lost in the dark: only the brightest frame may speak
                wgt *= np.clip((luma(rgb) - b["low"][0]) / (b["low"][1] - b["low"][0]), 0.0, 1.0)
            if k == order[0]:
                wgt = np.maximum(wgt, 1e-4)   # some frame always speaks
            acc += wgt[..., None] * (lin / gains[k])
            cnt += wgt
    if mode == "average":
        acc /= n
    elif mode != "trails":
        acc /= np.maximum(cnt, 1e-9)[..., None]
    return linear_to_srgb(acc), {"reference": ref, "frames": info, "flags": flags,
                                 "cut": [h - (y1 - y0), w - (x1 - x0)]}
```

### 4. Files, the record, the command

- `_bits()`: 16 when `import tifffile` succeeds (as `imageio._save_tiff16` tries it), else 8.
- `load_config(configs_dir=None, profile=None)`: `paths.merge(paths.read_config(<packaged merge.toml>),
  paths.read_config(<configs_dir or paths.configs_dir()>/merge.toml, profile))`, so a partial file never drops a
  key. A top-level value that is not a table, or a value that is not a number above 0 or a list of them (`bool` is
  not), raises `ValueError("merge.toml: [SEC] KEY must be a number above 0, not V")`.
- `_record_path(image)`: the path without `.tif` plus `.merge.json`. `_ours(image)`: that file loads as JSON with
  `merge == 1` and `output == basename(image)`. `photos(folder)`: `batch.list_images(folder)` without what `_ours`
  accepts, so own results are neither frames nor sources.
- `merge_group(group, out_dir, mode, thr, align, taken, sources) -> record`, the checks before any decode:
  `image_out, _ = emit.output_paths(out_dir, frames[0], "tiff", taken, name="{stem}_" + mode, sources=sources)`; a
  TIFF or record there that `_ours` does not accept raises `ValueError(f"{p} exists and was not written by merge; it
  is left alone: choose another output folder")`. Then `stops`; for brackets with every stop known `ref =
  sorted(range(n), key=stops.__getitem__)[n // 2]`, else None; `merge_frames(lambda k: load_image(frames[k],
  _RAW).rgb, n, mode, thr, align, ref)`; `emit.write_image(rgb, image_out, "tiff",
  source=frames[found["reference"]], metadata="shareable")`; the record through `<record>.part` and `os.replace`
  (`json.dump(..., indent=2, ensure_ascii=False)` and a newline).
- The record holds file names only and nothing from the clock: `merge` (1), `engine_version`, `output`, `mode`,
  `bits`, `reference`, `frames` (per frame `file`, the keys of `found` and, when every frame has stops,
  `settings_ev`: its stops minus the lowest, rounded to 3), `left_out`, `copies`, `cut`, `flags`, `settings` (the
  merged `merge.toml`), `notes`: a sentence per mode (`_WHY`; for `trails` that the frames are neither moved nor
  checked), then as they apply the flag ("something moved between the frames; it is in the picture as a ghost, not
  repaired"), the cut in rows and columns, for `average` with an exposure of 1 s or more that stars smear and
  `--mode trails` keeps them, the count of left-out files and copies, and at 8 bits "8-bit TIFF: install the accel
  extra (tifffile) for 16 bits".
- `run(folder, out_dir, mode="auto", everything=False, align=True, dry_run=False, configs_dir=None, profile=None,
  say=print) -> {"groups", "merged", "flagged", "failed"}`: an unknown mode is a `ValueError`; `thr =
  load_config(...)`, `images = photos(folder)`, `groups = find_groups(images, thr["groups"], everything)`,
  `sources = emit.source_ids(images)`, one `taken` set. It says how many photos it searches, that there is nothing to merge
  (naming `--all`), and with groups and `_bits() == 8` that the TIFFs will be 8-bit. Per group one line `[i/n] FIRST
  to LAST: k frames, exposure settings X.X EV apart, MODE` (or "no exposure settings"; `pick_mode` under `auto`),
  ending `-> OUTPUT` (plus `  FLAGGED: moved`) or `-> NOT MERGED: ERROR`; a dry run stops at the line. `merge_group`
  runs in `try`/`except Exception`; a failure appends `{"first": name, "error": str(e)}`.
- `autoedit merge FOLDER`, parsed after the `pick` block: `-o/--out` (default `out-merge`), `--mode` with the
  literal choices `auto, average, brackets, focus, trails` (default `auto`), `--all`, `--no-align`, `--dry-run`,
  `--configs-dir`, `--profile`. `_cmd_merge`: a missing folder prints `folder not found: ...`, 2; then
  `merge.run(args.folder, args.out, mode=args.mode, everything=args.all, align=not args.no_align,
  dry_run=args.dry_run, configs_dir=args.configs_dir, profile=args.profile)`; a `ValueError` or `OSError` is
  printed, 2. A dry run prints the count and that nothing was written, 0; else `M of N groups merged into ABS_OUT`
  with the flagged and the not merged, "No photo was moved or changed.", and 1 when a group failed, else 0. The
  module docstring gains a line; `_cmd_doctor` knows `merge.toml`.

## Config

`src/autoedit/configs/merge.toml` (new; packaged by the existing `configs/*.toml` rule), with a blank line before
each table.

```toml
# Merge frames (F33): `autoedit merge FOLDER`. Tuning values of that command only; `autoedit run` reads none.
[groups]                # which frames belong together: the burst finder of F9 (consistency.find_bursts)
max_gap_s = 30          # pause between the starts of two frames; the folder's longest exposure time is added
min_similarity = 0.8    # how alike two thumbnails must be (at one exposure when the settings differ)
bracket_min_ev = 0.5    # --mode auto: settings further apart than this (stops) make a group a bracket
[align]                 # whole pixels, against the reference frame, by phase correlation
proxy_px = 512          # long edge of the small copies: the first search, and the movement and exposure checks
window_px = 1024        # side of the full-size window the answer is refined in, where the reference has most detail
max_shift = 0.01        # share of the long edge; a frame further off refuses its group (frames from a tripod)
[moved]                 # judged on the small copies, the tones of each frame matched to the reference's
blur = 1.0              # sigma, in pixels of the small copy
tolerance = 0.08        # a difference in display luma larger than this counts as changed
max_share = 0.01        # share of the judged pixels that may change before the group is flagged
[brackets]              # what "well exposed" means, in the frame's own display values (0..1)
low = [0.02, 0.10]      # a frame counts not at all below low[0], fully from low[1] (the brightest frame always)
high = [0.90, 0.98]     # fully up to high[0], not at all from high[1] in any channel (the darkest frame always)
min_overlap = 0.005     # share of the small copy two frames must both expose well to be compared
[focus]                 # sharpness: the fine detail of the luma, squared, averaged over a neighbourhood
detail_sigma = 1.0      # pixels: what counts as fine detail
window = 0.004          # share of the long edge: the neighbourhood sharpness is judged over
soften = 0.002          # share of the long edge: blur of the map that says which frame a pixel comes from
```

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/merge.py` | new: `MODES`, `_RAW`, `_WHY`, `load_config`, Design 2 and 3 (T1); `_bits`, Design 1 and 4 (T2) | T1, T2 |
| `src/autoedit/configs/merge.toml` | new, as in Config | T1 |
| `tests/test_merge.py` | new: tests 1 to 11 (T1), 12 to 18 (T2) | T1, T2 |
| `src/autoedit/consistency.py` | `thumb_grey`, `signature`, `alike=` (Design 1); shared with F9 and F35 | T2 |
| `src/autoedit/cli.py` | `_cmd_merge`, the parser block, one docstring line, `merge.toml` in `_cmd_doctor` | T2 |
| `.gitignore`, `MANIFEST.in`, `scripts/check_public_repo.py`, `tests/test_public_repo_guard.py` | `*.merge.json` private like `*.autoedit.json` (fact 8) | Docs |

## Tasks

T2 needs T1. A Builder runs only its own command; the session runs the suites. No other Builder may hold `cli.py` or
`consistency.py` while T2 runs (F32 touches `cli.py`).

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, `high` (the merge math) | `merge.py`: `MODES`, `_RAW`, `_WHY`, `load_config`, Design 2 and 3 as written; `merge.toml`; tests 1 to 11 with the helpers of "Tests" | `PYTHONPATH=src python3 -m pytest tests/test_merge.py -q` |
| T2 | Builder, Sonnet, `medium` | Design 1 and 4; tests 12 to 18 | `PYTHONPATH=src python3 -m pytest tests/test_merge.py tests/test_consistency.py tests/test_pick.py tests/test_input_formats.py tests/test_cli.py -q` |
| Check | Checker, Opus, `high` | The diff against this spec; that nothing can write over a photo or a file merge did not write; that `find_bursts` without `alike` is unchanged; no path in a record; the acceptance checks | none |
| Docs | The session, inline | `*.merge.json` in `.gitignore` and in `MANIFEST.in`'s `global-exclude`; `path.name.endswith((".autoedit.json", ".merge.json"))` in `private_path`; `"nested/a0_brackets.merge.json"` in the guard test's private list. `README.md`, `APP_GUIDE.md` (command, config file), `ARCHITECTURE.md` (a tool before the pipeline, not a stage), `CHANGELOG.md`, the plan row | none |

## Tests

`tests/test_merge.py`; no fixture but `tmp_path`, no `capsys`, `parametrize` or `raises(match=)`. `THR =
merge.load_config()`; `_thr(**over)`: a deep copy of THR with keys changed per table. `_frames(lins)`: the `load` of
`merge_frames`, `linear_to_srgb(np.clip(f, 0, 1))` of each, a copy per call. `_sky(h=120, w=160, seed=10,
split=0.4)`: `_scene`, then `0.35 + 0.75 * s` above row `int(h * split)`, `0.03 + 0.28 * s` below. `_blur(x, s)`:
`gaussian_blur` per channel. `_subpixel(x, dy, dx)`: per channel `real(ifft2(fft2(c) * exp(-2j * pi * (ky * dy +
kx * dx))))`, `ky, kx` the `fftfreq` of the two sizes. `_oval(base, cx, seed)`: `base` with `((x - cx) / 28) ** 2 +
((y - 120) / 40) ** 2 <= 1` set to `[0.75, 0.7, 0.6]`, plus noise 0.01 (`default_rng(seed)`). `_hashes(d)`: file name to
SHA-256. `_save(path, lin, sec, seconds=0.01, fnum=8.0, iso=100)` writes a JPEG (`quality=95, subsampling=0`) of
`np.clip(linear_to_srgb(np.clip(lin, 0, 1)) * 255.0 + 0.5, 0, 255).astype(np.uint8)` with 0x9003 = `f"2026:05:01
10:{sec // 60:02d}:{sec % 60:02d}"`, 0x829A = `seconds`, 0x829D = `fnum`, 0x8827 = `iso` in the Exif sub-directory
(`exif.tobytes()`) and returns the path. `_shoot(d)` (made with parents): `a0..a2.jpg` of `_sky()` times 0.25, 1, 4
at seconds 0, 1, 2, exposure time 0.01 times that; `b0.jpg`, `b1.jpg` of `_scene(seed=11)` plus noise 0.02 (one
`default_rng(12)`) at 100 and 102; `b1_copy.jpg` the bytes of `b1.jpg`; `lone.jpg` of `_scene(seed=13)` at 400.

```python
def _scene(h=120, w=160, seed=0):                 # linear light, 0.08 to 0.6: soft structure and hard-edged patches
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w), np.float32)
    for sigma in (1, 3, 9):
        g = gaussian_blur(rng.random((h, w)).astype(np.float32), sigma)
        a += (g - g.min()) / (g.max() - g.min())
    for _ in range(14):
        y, x = int(rng.integers(0, h - 8)), int(rng.integers(0, w - 8))
        a[y:y + int(rng.integers(6, h // 3)), x:x + int(rng.integers(6, w // 3))] += float(rng.uniform(-1.0, 1.0))
    a = (a - a.min()) / (a.max() - a.min())
    return np.stack([0.08 + 0.52 * a, 0.08 + 0.45 * a, 0.10 + 0.40 * a], axis=-1).astype(np.float32)
def _bracket(noise=0.002, seed=2):               # 13 stops of range; frames at 0, -2, +2 EV (a camera's order)
    h, w = 120, 160
    ramp = np.linspace(-9.0, 4.0, w, dtype=np.float32)[None, :].repeat(h, 0)
    radiance = (2.0 ** ramp)[..., None] * np.array([1.0, 0.9, 0.8], np.float32)
    radiance = radiance * (0.4 + 2.0 * _scene(h, w, seed)[..., :1])
    rng = np.random.default_rng(seed)
    frames = [np.clip(radiance * g, 0.0, 1.0) + rng.normal(0.0, noise, radiance.shape).astype(np.float32)
              for g in (1.0, 0.25, 4.0)]
    return radiance, frames
```

| # | Test | Asserts |
|---|---|---|
| 1 | `test_average_cuts_noise_by_root_n` | 16 frames of `_scene()` plus noise 0.02 (`default_rng(1)`): std of `srgb_to_linear(out) - scene` is 0.02 / 4 within 10 %, mean within 0.0005; float32, the frames' shape, no flag, every shift `[0, 0]`. The first two frames: 0.02 / sqrt(2) within 10 % |
| 2 | `test_brackets_keep_what_a_frame_holds` | `_bracket()`: where the smallest of the frames' largest display channels is under 0.98 (over 70 % of the frame; the 0 EV frame alone is at 0.98 or more on over 10 %), no output channel is above 0.98; `exposure_ev` rounded to 1 is `[2.0, 0.0, 4.0]`; where `radiance / 4` is in (0.002, 0.9) the median relative error is under 0.02; where in (0.001, 0.01) the error's std is under 0.10 of the -2 EV frame's; no flag, `cut == [0, 0]` |
| 3 | `test_brackets_trust_only_what_is_well_exposed` | `radiance` of `_bracket()` at 0.5 and 1, the darker frame set to 0 where its display luma is under 0.05 (over 3 %), `align=False`: there the median relative error against `radiance * 0.5` is under 0.02; `exposure_ev` `[0.0, 1.0]` |
| 4 | `test_brackets_fail_when_the_steps_are_too_far_apart` | That radiance at 2^-9 and 2^3, `align=False`: `ValueError` with "too far apart" |
| 5 | `test_focus_picks_the_sharp_frame` | `_scene(160, 200, 3)` and `_blur(.., 3.0)`; frame 0 soft from column 100, frame 1 before it: the mean error against the scene is under a tenth of frame 1's in columns 0 to 90 and of frame 0's in 110 to 200, over all under a quarter of either; no flag, shifts zero |
| 6 | `test_trails_take_the_brightest_frame` | Six frames `0.02 + 0.02 * _scene(80, 120, 4)` plus noise 0.002 (`default_rng(k)`), pixel `(20 + 5k, 30 + 10k)` of frame k `[0.9, 0.85, 0.7]`: each above 0.69 in every channel of the result (linear); every pixel equals within 1e-5 that pixel of the frame with the largest luma; no flag, `moved` None and shift zero |
| 7 | `test_a_shifted_frame_is_aligned` | `_phase(a, np.roll(a, (3, -5), axis=(0, 1))) == (3, -5)`. Reference `big[20:260, 20:340]` of `_scene(280, 360, 5)`, `max_shift` 0.05: content moved by (5, -3) at gain 1, (-7, 6) at 2, (0, 0) at 1, (1, 0) at 0.25, (-12, 14) at 0.5 (gain 1 `average`, else `brackets`, `ref=0`) gives that shift, `cut` its sizes, no flag. `big[15:255, 23:343]`: shift `[5, -3]`, moved under 0.002, shape (235, 317, 3), equal to `ref[0:235, 3:320]` within 1e-4; with `align=False` flagged `moved`. `_subpixel` by (2.4, -1.7) and (-0.3, 3.6): the rounded shift. `_sky(260, 340, 16, split=0.6)`, the frame 4 times brighter and moved (-3, 4): `[-3, 4]`. `_blur(_scene(240, 320, 30), 10)` twice with noise 0.01: `[0, 0]` |
| 8 | `test_a_frame_too_far_off_refuses_the_group` | Reference `big[10:250, 10:330]` of `_scene(260, 340, 5)`: frame `big[4:244, 10:330]` raises with "6 px off" and "the limit is 4 px"; `big[10:250, 0:320] * 0.5` as brackets with "10 px off". `_scene(240, 320, 99)`, `align=False`: flagged `moved`, its share above 0.05 |
| 9 | `test_a_moved_group_is_flagged` | `_oval` on `_scene(240, 320, 6)` at x 100 three times: no flag, moved under 0.002. At 100, 160, 220: `["moved"]`, the reference's `moved` None, frame 0 above 0.03, the frames' shape. The still frames at 0.5, 1, 1.6 (`average`) and 0.25, 1, 1.6 (`brackets`): no flag; the moving ones as brackets: `["moved"]` |
| 10 | `test_frames_of_another_size_and_bad_modes_are_refused` | `ValueError` for 120 x 160 with 120 x 161, for the mode `"hdr"`, for one frame |
| 11 | `test_config_is_layered_and_checked` | The five tables and the values of Config (`groups`, `align`, `moved` whole, `high`); `[align] max_shift = 0.05` in a configs folder changes that key and keeps `window_px`; a profile `[moved] max_share = 0.05` over both, `focus` as packaged; `max_share = "a lot"`: `ValueError` naming `[moved] max_share` |
| 12 | `test_exposure_data_groups_and_modes` | `_shoot`: stops of a0..a2 are 0, 2, 4 apart, 0.04 s for a2, `(None, 0.0)` for a missing file; groups `a0, a1, a2` and `b0, b1` with `b1_copy.jpg` under `copies`; `pick_mode` `brackets`, `average`, and `average` for `[None, 1.0]` and `[0.0, 0.4]`; `everything`: one group of 6 frames, 1 copy. Three 30 s exposures 31 s apart: one group |
| 13 | `test_a_bracket_with_a_burnt_out_frame_stays_one_group` | `_scene(120, 160, 14)`, rows under 80 `0.25 + 1.2 * s`, the rest `0.04 + 0.05 * s`, saved at 1, 0.25, 4 (seconds 0.01 times that, at 0, 1, 2 s): `find_bursts(paths, 30.0, 0.8)` is not one run of 3, `find_groups` is; `_alike` of frames 1 and 2 above 0.9 both ways. Frames 0, 1 and `_sky(seed=40) * 4` at 3 s: one group of 2 |
| 14 | `test_raw_files_and_the_rest_are_merged_apart` | `s0..s2.jpg` and `.dng` twins (JPEG bytes of `_scene(seed=20) * 0.9`, renamed) 10 s apart: frames the three `.dng`, `left_out` the `.jpg`; with two `.dng` removed the JPEGs are the frames |
| 15 | `test_run_writes_a_tiff_and_a_record_and_leaves_the_sources` | `run(_shoot, out)`: source hashes unchanged; `merged == ["a0_brackets.tif", "b0_average.tif"]`, no failure or flag; the folder holds exactly those and their records. Bracket record: mode, output, `merge == 1`, the three names, reference `a1.jpg`, each frame `file, shift, moved, exposure_ev, settings_ev`, `settings_ev` `[0.0, 2.0, 4.0]`, the third `exposure_ev` within 0.3 of 4.0, the first 0.0; no `os.sep` and no `tmp_path` in its text; `bits == merge._bits()`, at 8 a note with "8-bit"; `load_image` of the TIFF 120 x 160 with `source_bits == bits`. The stack's `copies == ["b1_copy.jpg"]`. `batch.run(RunConfig(backend="stub"), out, edited)`: 2 processed, 0 failed, a sidecar. A second run into another folder: the same bytes |
| 16 | `test_nothing_is_written_over_a_photo_or_a_file_merge_did_not_write` | `run(d, d)`: 2 merged, the photos unchanged; again: 2 groups, 2 merged, none failed. `out/a0_brackets.tif` holding `b"not ours"`: only `b0_average.tif` merged, the error says "not written by merge", the file unchanged. A folder with `b0.jpg`, `b1.jpg` and a photo `b0_trails.tif`, `run(e, e, mode="trails")`: nothing merged, "overwrite a source", every hash unchanged |
| 17 | `test_one_bad_group_does_not_stop_the_run` | `b0.jpg` saved at twice the size: `merged == ["a0_brackets.tif"]`, one failure with "another size" |
| 18 | `test_cli` | `--dry-run` returns 0 and creates no folder; a plain call 0 and both TIFFs; `--all --mode trails` writes `a0_trails.tif` and its record; a missing folder 2; the bad group of test 17 gives 1; `--mode hdr` raises `SystemExit` |

## Acceptance checks (the session runs these)

| # | Check | Expected |
|---|---|---|
| 1 | `PYTHONPATH=src python3 tests/run_all.py`, the same with `.venv/bin/python`, the stub smoke run of `CLAUDE.md` | Green; sidecars and a manifest |
| 2 | Default renders against the baseline; `tests/test_consistency.py`, `tests/test_pick.py` | Byte-identical (no stage or setting changed); both pass unchanged |
| 3 | `PYTHONPATH=src:tests python3 -c "import pathlib, test_merge; test_merge._shoot(pathlib.Path('.tmp/f33/shoot'))"`, then `autoedit merge .tmp/f33/shoot -o .tmp/f33/merged`, `autoedit run .tmp/f33/merged .tmp/f33/out --backend stub`, the merge again into `.tmp/f33/again` and `cmp` of the files | 2 merged, 0 flagged, exit 0; 2 edited with sidecars and a manifest; the same bytes |
| 4 | `autoedit merge curated-switzerland-summer-2026/selected -o out-features/F33/shoot`, with `find . -type f -exec shasum -a 256 {} + \| sort` in the photo folder before and after; `--dry-run` on `curated-dolomites-summer-2026/selected_raw` and on `test-corpus` | 11 groups: 6 not merged (60 to 648 px off), 5 merged and flagged; about two minutes; exit 1; the same hash text. No group in the other two |
| 5 | Made-up tripod brackets from at least 4 real RAW files of the Swiss folder, by a script of the session that is not committed: the linear development at 1, 0.25 and 4 times with sensor-like noise, as JPEG with exposure tags, merged with `.venv/bin/python` (16 bits); `scripts/tone_report.py FOLDER --render --backend stub --fail-on-regression` on the merges | Brackets found, no flag, exposures within 0.01 of 2 and 4, nothing above 0.98 where a frame holds detail; tone report 0 and 0, `safety_fallback` 1.0 on every row |
| 6 | The Checker's review (an L feature), with a look at one merge of check 5 beside its frames | No finding that must be fixed |
| 7 | Open, the owner's: one real tripod bracket, one real night series and one focus series through `autoedit merge` and `autoedit run` | A look at each. The corpora hold none |

## Known limits

| Limit | What it means |
|---|---|
| Tripod frames only; movement | Whole pixels, at most 1 % of the long edge. Turn, zoom, lens breathing and parallax are not corrected: on the real shoot every hand-held group was refused or flagged. Frames that differ strongly in focus on a scene with little fine detail can land a pixel off (4 of 144; one of two at full size, on snow). Movement is judged against the reference frame only, one threshold; a thing smaller than a few blocks of the small copy is missed. A frame of another picture is flagged, not refused (Measured) |
| A moving sky is not followed | `average` on a night series smears the stars without a flag (the movement check sees a 512 px copy, stars vanish in it); a note says so for exposures of a second or longer. `trails` is neither aligned nor checked; the gaps between exposures show, and the brightest noise of many frames lifts the sky |
| A merged bracket is dark | Decision 1: about 0.7 EV under the middle frame after a run. JPEG brackets are not linear: smooth, not exact. Neighbouring exposures that share under 0.5 % well-exposed area fail with "too far apart" |
| `auto` knows two modes | A focus series without `--mode focus` is averaged and comes out soft; without exposure settings a group is a stack. A focus merge can leave a seam where blur widths differ by more than `window`. No real focus series was tried |
| Files | 16 bits need `tifffile`; the TIFF has no capture time and no exposure tags (fact 5). CR3, RAF, ORF and RW2 have neither: only `--all` merges them, in name order, as a stack unless a mode is named. Sub-folders are not read |
| Cost and bookkeeping | Every frame is decoded twice (a RAW file 1.7 s); peak memory like one render (6.4 GB measured). One pause for the folder: its longest exposure time widens the gap for every pair. The run that edits a merged TIFF knows nothing of the merge; the record stays beside it |

## Proposed plan row

`| F33 | Merge frames: brackets, stacks, focus series, trails | L | done as a tool (`autoedit merge FOLDER [-o DIR]`, modes `average`, `brackets`, `focus`, `trails`); tested on made-up frames and on brackets made from real RAW files; a real bracket, night series and focus series are open, the corpora hold none |`

For the tables at the end of `FEATURE_PLAN.md`. For the owner: "A merged bracket is as bright as its darkest frame
and ends about 0.7 EV under the middle frame after a run. Keep it linear, or add a shoulder at the middle frame's
brightness?" Evidence: acceptance check 5 and a real bracket. The change: a `[brackets]` key and about ten lines in
pass 2. Changed against the plan: "The burst finder alone splits one bracket in five; `merge` adds a comparison at
one exposure. A frame too far off refuses its group." Why: Measured. Reverse it by: `alike=` in `find_groups`;
`max_shift`.

## Result

Built on 2026-11-17 in the unattended run. Status: done as a tool. The spec was written on Opus at
max effort (Fable's quota had run out); an earlier draft of a cut-off Fable planner was replaced.

- **Built** by two Builders (Sonnet) from this spec. `_distinct` treats only files of the same kind as
  copies (the spec's test 14 needs that); the CLI also has `--all` and `--no-align`.
- **The Checker (Opus, high)** found two things that had to be fixed, both fixed by the session with
  tests. A frame far off on fine texture (snow, grass, rock) was merged at a wrong small shift where
  the full-size climb settled in a local dip: on the Swiss shoot `0377` was written as a double
  image of two framings 252 px apart. Now, when the first search says the frame is past the limit,
  the two shifts are compared on the small copies and the far one wins when its difference is under
  `phase_wins` (0.8, in `merge.toml`) of the near one's; the group is then refused. And a colour
  channel with no signal in any frame (deep red light) got a gain of 0, which made the alignment
  NaN; such a channel now takes the mean gain of the others. Also fixed: `low`, `high`, `proxy_px`
  and `window_px` are checked for shape and order; the test of the untouched sources compares all
  hashes. Kept as known limits: a frame that shares too little well-exposed area with the
  reference (wide brackets) is not checked for movement; a stale `.merge.json.part` is written over;
  focus with a shifted frame and `_ours` are weakly tested.
- **Checks:** both suites green before the fixes (1020 on each interpreter); default output and
  `find_bursts` without `alike` unchanged; the made-up shoot: 2 merged, 0 flagged, edited by
  `autoedit run`, a second merge byte-identical. The real Swiss folder after the fix
  (`out-features/F33/shoot.log`): 11 groups, 9 refused (60 to 648 px off, all hand-held), 2 merged
  and flagged, exit 1, photo folder hashes unchanged; before the fix 6 were refused. No group in the
  Dolomites RAW folder or the smoke corpus. Made-up tripod brackets from 4 real RAW files
  (`out-features/F33/brackets/`): exposures measured at 1.998 and 3.998 EV, no flag, nothing above
  0.98, 16-bit; the tone report 0 and 0 with `safety_fallback` 1.0 on all four; by eye the merge is
  clean (no seams, halos or colour shift) and as bright as its darkest frame.
- **Open:** a real tripod bracket, night series and focus series (the owner's); the brightness of a
  merged bracket is an owner question.
