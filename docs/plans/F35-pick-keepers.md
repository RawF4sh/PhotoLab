# F35. Pick the keepers

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F35.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and
the decisions that are the owner's are in the table below.

The design was built once on a scratch copy of the tree, which is deleted again. The reference
code is that code and every number is measured. On the copy both suites passed, on the system
Python and on `.venv`: the 837 tests of that day and the 22 of this spec.

## Goal

`autoedit pick FOLDER [-o DIR]` finds the bursts of a folder and ranks the frames of each: by how
sharp the subject is, by exposure, and by the scorer when a model is given. It writes a ranking, a
list of the picks and a contact sheet with the pick marked, and copies the picks only when asked.
It is a new command and a new module; `autoedit run` is not touched, so default output stays
byte-identical. No source file is moved, renamed, changed or deleted.

## What was found in the code

1. `consistency.py::find_bursts(paths, max_gap_s, min_similarity, max_span_s)` returns each burst
   as one flat list of files. A frame needs a capture time (`capture_time`: whole seconds, through
   Pillow). The thresholds are `[consistency]` in `rules.toml` over `BURST_DEFAULTS` (30, 120, 0.8).
2. The finder treats a RAW file and a JPEG of the same second as one shot, but its result does not
   say which files belong together; in a renamed folder with two exposures in one second the pairs
   crossed (seen in the Switzerland corpus; harmless for F9).
3. Pillow's TIFF reader refuses the headers of CR3, RAF, ORF and RW2 (`TiffImagePlugin._accept`):
   such a file has no capture time and is in no burst. `rawpy` has the time (`raw.other.timestamp`).
4. `segment/stub.py::_subject` is an oval in the middle of the frame (37.6 % of it), the same in
   every frame. `segment/resolve.py::resolve` drops a mask under the concept's `min_coverage` (3 %
   for `subject`): a small bird would be gone, so pick gates the raw masks itself.
5. `stats.py::_veil_and_detail` gives `detail_rms` for the whole frame only, and it scales with
   exposure. `stats._HIGHLIGHT` (0.98) is the level for blown. `web/app.py` already has
   `POST /api/v1/pick`: the folder picker of the operating system.
6. `imageio.py::camera_preview(path, max_px)`: the camera's own JPEG inside a RAW file, upright.
   In the corpora's CR2 files it is full size and reaches 2048 px in 0.19 s (a development: 1.15 s,
   0.46 s at half size). A DNG of `testImages` has a 1024 px preview only.
7. `curate.py::curate` is the precedent: copy-only, a JSON, a CSV, `thumbs/`, `contact-sheet.html`;
   its XMP files go next to the copies. `image_features(path)` decodes the file itself. The scorer
   (`aesthetic.predict`) is a linear model on 14 numbers of a 256 px picture: it cannot see focus.
8. `emit.py::source_ids(paths)` gives (device, inode) of every input, to refuse a write that lands
   on a source. `batch.py::list_images` is the flat, sorted listing of `autoedit run`.
   `cli.py::load_run_config` reads each flag with `getattr(args, name, None)`: few flags suffice.
9. `calibrate.py::load_config` is the pattern for a tool with its own TOML file. `cli.py::_cmd_doctor`
   calls a profile file it does not know "not a config file, ignored", also `calibrate.toml`.
10. `edit/filters.py::box_blur` runs through OpenCV when installed, else numpy (5e-6 apart). Pillow
    leaves an `Exif` object without top-level tags out of a PNG; as `exif.tobytes()` it is written.

## Measured

**The corpora, through the burst finder** (all of this section without the owner; photo folders
were only read). The Switzerland selection (800 files) has 12 bursts with 34 files: 25 exposures,
two per burst and three in one. In the Dolomites folders the four "bursts" are each one file saved
under two names. No folder holds an action shoot: the check against the owner's picks stays open.

**The measure, on 30 real photos** (22 megapixel JPEGs; changed at full size, saved as JPEG 92,
judged at 2048 px in the stub's oval). Sharpness against the unchanged frame:

| Change | Median | Lowest to highest |
|---|---|---|
| Blur, radius 0.5 / 0.7 / 1.0 px | 0.943 / 0.885 / 0.765 | 0.927 to 0.974 / 0.864 to 0.929 / 0.739 to 0.815 |
| Blur, radius 1.5 / 2.5 px; shake of 5 px | 0.572 / 0.313; 0.794 | 0.525 to 0.658 / 0.251 to 0.428; 0.658 to 0.855 |
| 1.5 / 3 EV darker | 1.001 / 1.001 | 0.804 to 1.005 / 0.508 to 1.008; under 0.95 in 2 / 9 photos, those with the least local contrast |
| 0.7 / 1.5 EV brighter (median 19 % / 32 % blown) | 1.006 / 1.024 | 0.966 to 1.101 / 0.102 (blown all over) to 1.271 |
| Noise of 0.02 added; blur 1.0 px under that noise | 1.034; 0.798 | 1.006 to 1.195; 0.748 to 0.888 |
| Three equally sharp frames (two moved by 2 to 5 px, each with its own noise) | 0.9994 | lowest over highest; worst 0.9973 |

**Made-up bursts, scored with `rank_burst`** (30 photos each). The unchanged frame wins against
three frames with blur 0.5 px (30 of 30, smallest lead 0.026), blur 0.7 px (30, 0.071), blur
1.0 px (30, 0.185), shake (30, 0.144), and in a noisy pair (30, 0.112). A sharp frame 1.5 EV
darker beats blur 1.0 px in 30, one 3 EV darker in 23. In a bracket of -1.5, 0 and +1.5 EV the
brighter frame is never the pick with more than 1 % blown. As files through the command: 8 of 8.

**The 12 real bursts** (stub; the Planner looked at the windows of the sheet). In three, one frame
is plainly out of focus in the judged area and is ranked last, at 0.41, 0.35 and 0.80 of the
pick's sharpness (leads 0.58, 0.65, 0.50). In three, the frames are equally sharp (0.96 to 1.00)
and differ in exposure: the frame with less blown area is the pick (1.2 % against 15 %, 0.7 %
against 9.4 %), or the brighter one when nothing is blown. One is two frames of a moving animal in
one second: a tie (lead 0.002), the pose would decide. For the other five nothing is claimed.

**Why this measure.** Fine detail over the median luma: in two equally sharp real pairs the brighter
frame read 16 % and 39 % less sharp (the camera's tone curve). Over the standard deviation: a frame
1.5 EV brighter read up to 63 % sharper. Over local contrast (chosen): those pairs are within 4 %.

**Cost and safety.** 800 files: 42 s, 27 s of it the burst search; a judged frame takes 0.49 s as
a JPEG, 0.25 s as a CR2 (preview), 0.69 s as a DNG that needs a development. All 800 files had the
same hash, size and modification time after the run. Each of 25 deliberate breaks of the module
made a test fail; the tests also pass without OpenCV, rawpy and tifffile. Not measured: SAM 3 (the
Planner may not run it), high-ISO frames, the owner's picks.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Picks copied or tagged? | A list (`picks.txt`) always; copies into `DIR/picked` only with `--copy`. No XMP: next to a source it writes into the photo folder and can meet a sidecar of another program. One pick per burst; the ranking file has every rank | A later task with `curate._write_xmp_sidecar`; a later `--keep N` |
| Where do the results go? | `-o DIR`, default `out-picks` in the current folder (the repo already ignores `out-*`). Never into the photo folder unless `-o` says so | The default in `cli.py` |
| What is a burst? | F9's finder with `[consistency]` of `rules.toml`. A `[burst]` block in `pick.toml` replaces the values for pick only; it ships commented out | That block |
| A frame outside every burst, and one that cannot be judged | Each is a pick: the first is not opened, the second is listed under `failed`. What was not judged is kept | The `picks` line in `run` |
| A RAW file and its JPEG | One shot when they share their name; the JPEG is judged; both are picked | `shots_of` |
| The subject | `concepts = ["subject"]` in `pick.toml`, or `--concepts`. With the stub: the oval | `pick.toml` |
| The weights | Sharpness counts 1, the full exposure penalty 0.3, the scorer's range 0.15. The penalty is full at 10 % blown or at a median luma of 0 (it starts under 0.25) | `pick.toml`, also in a profile |
| A black share in the penalty | No. On the real frames it punished silhouettes; a dark frame is caught by its median | `exposure_penalty` |
| API and Studio | Nothing now. `run` returns the ranking as plain data, so a route is a few lines; `/api/v1/pick` is taken (fact 5) | The test UI |

## Design

### 1. Shots, bursts, the picture that is judged

A burst is two or more shots (exposures) the finder put together; every other shot is a single.
`consistency.py` is not changed. Every frame is judged at one size (2048 px, never enlarged): that
keeps frame size from deciding and averages the noise of single pixels away. `shots_of(files) ->
list[list[str]]` groups the sorted files by lower-cased name without extension: a group of two or
more that holds a RAW file is one shot, sorted with `key=lambda p: (is_raw(p), p)` (the judged file
first, never the RAW file); every other file is its own shot; the result is sorted.

```python
def groups_of(images: list[str], thr: dict) -> tuple[list[list[list[str]]], list[list[str]]]:
    """(bursts, singles). A burst is a list of two or more shots, a single is one shot."""
    shots = shots_of(images)
    shot_of = {f: i for i, shot in enumerate(shots) for f in shot}
    bursts, used = [], set()
    for files in consistency.find_bursts(images, thr["max_gap_s"], thr["min_similarity"], thr["max_span_s"]):
        mine = sorted({shot_of[f] for f in files} - used)
        if len(mine) > 1:
            used.update(mine)
            bursts.append([shots[i] for i in mine])
    return bursts, [s for i, s in enumerate(shots) if i not in used]

def look_picture(path: str, long_edge: int, decode: dict | None = None) -> np.ndarray:
    """Upright float RGB, long edge at most `long_edge`. A RAW file gives the camera's own preview
    when that is large enough, else a half-size development."""
    if is_raw(path):
        rgb = camera_preview(path, max_px=long_edge)
        if rgb is not None and max(rgb.shape[:2]) >= long_edge:
            return rgb
        return resize_long_edge(load_image(path, {**(decode or {}), "half_size": True}).rgb, long_edge)
    # ponytail: a full decode, then the shrink (0.4 s for a 22 megapixel JPEG). Decode at reduced
    # size, as consistency.thumb_signature does, when a shoot of thousands takes too long.
    return resize_long_edge(load_image(path).rgb, long_edge)
```

### 2. Sharpness, exposure, score

```python
def sharpness(rgb, mask, radius: int = 16, min_contrast: float = 0.02) -> float:
    """RMS of the luma's Laplacian over RMS of the local contrast (a pixel against the mean of the
    2 * radius + 1 square around it), both inside `mask`. Under `min_contrast` an area is flat: it
    is not divided by less, so its noise cannot look sharp."""
    y = luma(rgb)
    lap = 4.0 * y[1:-1, 1:-1] - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:]
    local = (y - box_blur(y, radius))[1:-1, 1:-1]
    m = mask[1:-1, 1:-1]
    if not m.any():
        return 0.0
    rms = lambda a: float(np.sqrt(np.mean(np.square(a[m], dtype=np.float64))))  # noqa: E731
    return rms(lap) / max(rms(local), min_contrast)

def exposure_penalty(rgb, mask, clip_full: float, dark_p50: float) -> tuple[float, dict]:
    """(0..1, its numbers), inside `mask`: blown share over `clip_full`, or the median under `dark_p50`."""
    blown = float((rgb.max(axis=2)[mask] > _HIGHLIGHT).mean())
    p50 = float(np.median(luma(rgb)[mask]))
    penalty = max(min(1.0, blown / clip_full), min(1.0, max(0.0, (dark_p50 - p50) / dark_p50)))
    return penalty, {"blown": blown, "p50": p50}

def judge(path, seg, st, concepts, decode=None, model=None) -> tuple[dict, np.ndarray, np.ndarray]:
    """(the numbers of one shot, the picture it was judged on, the judged area)."""
    m = st["measure"]
    rgb = look_picture(path, int(m["long_edge"]), decode)
    area = np.zeros(rgb.shape[:2], dtype=bool)
    for r in seg.segment(Image(rgb=rgb, path=path), list(concepts)):
        if r.score >= m["min_score"]:
            area |= r.mask
    on = "mask"
    if area.mean() < m["min_coverage"]:      # nothing found, or a speck: the whole frame
        area[:], on = True, "frame"
    penalty, numbers = exposure_penalty(rgb, area, st["exposure"]["clip_full"], st["exposure"]["dark_p50"])
    scorer = aesthetic.predict(curate.features_of(resize_long_edge(rgb, 256)), model) if model else None
    return ({"judged_on": on, "coverage": float(area.mean()),
             "sharpness": sharpness(rgb, area, int(m["local_radius"]), m["min_contrast"]),
             "exposure_penalty": penalty, **numbers, "scorer": scorer}, rgb, area)

def rank_burst(judged: list[dict], weights: dict) -> list[dict]:
    """The shots of one burst, best first, each with `sharp_rel`, `score`, `rank` and `pick`.
    The best possible score is 1. Ties go to the sharper shot, then to the file name."""
    best = max(j["sharpness"] for j in judged) or 1.0
    top = max((j["scorer"] for j in judged if j["scorer"] is not None), default=None)
    for j in judged:
        j["sharp_rel"] = j["sharpness"] / best
        j["score"] = j["sharp_rel"] - weights["exposure"] * j["exposure_penalty"]
        if top is not None and j["scorer"] is not None:
            j["score"] -= weights["scorer"] * (top - j["scorer"])
    order = sorted(judged, key=lambda j: (-j["score"], -j["sharpness"], j["files"][0]))
    for n, j in enumerate(order, start=1):
        j["rank"], j["pick"] = n, n == 1
    return order
```

`box_blur` comes from `.edit.filters`, `_HIGHLIGHT` from `.stats`. No torch in `pick.py`: SAM 3
comes through `segment.make_segmenter`. In `curate.py` the body of `image_features` moves into
`features_of(rgb: np.ndarray) -> dict` (first line `rgb = np.clip(rgb.astype(np.float32), 0.0, 1.0)`)
and `image_features` returns `features_of(downscale_long_edge(load_image(path), long_edge).rgb)`.

### 3. `configs/pick.toml` (new)

Each key carries its meaning as a comment. `load_settings(configs_dir=None, profile=None)` reads
the file the way `calibrate.load_config` does: from `configs_dir or paths.configs_dir()`, else the
packaged one, through `paths.read_config(base, profile)`, so a profile's `pick.toml` is merged over it.

| Key | Value | Meaning (the comment) |
|---|---|---|
| `[measure] long_edge` | 2048 | every frame is judged on a picture of this size (never enlarged), so frame size does not decide |
| `[measure] local_radius` | 16 | sharpness is fine detail over local contrast; local means a square of 33 pixels of that picture |
| `[measure] min_contrast` | 0.02 | a judged area with less local contrast (RMS, of 1) is flat: its noise must not count as sharpness |
| `[measure] concepts` | `["subject"]` | what the segmenter is asked for; a frame is judged inside these masks (`--concepts` replaces the list) |
| `[measure] min_score` | 0.4 | a mask the segmenter is less sure of than this is not used |
| `[measure] min_coverage` | 0.002 | masks that cover less of the frame than this are not used: the whole frame is judged |
| `[exposure] clip_full` | 0.1 | this share of the judged area blown (a channel above 0.98) is the full penalty |
| `[exposure] dark_p50` | 0.25 | a judged area with a median luma below this (two stops under mid grey) counts as dark; the penalty is full at 0 |
| `[weights] exposure` | 0.3 | the full exposure penalty: what a frame loses against the sharpest frame of its burst, whose sharpness counts as 1 |
| `[weights] scorer` | 0.15 | the whole range of the scorer, 0 to 1 (only with `--aesthetic-model`) |
| `[burst]`, commented out | `max_gap_s = 2`, `max_span_s = 20`, `min_similarity = 0.8` | values written here replace those of `[consistency]` for `autoedit pick` only |
| `[sheet] thumb`, `crop` | 360, `[360, 240]` | long edge of a frame's picture on the sheet; the window of the judged picture at full scale |

### 4. The run and what it writes

`run(folder, out_dir, cfg, model_path=None, copy=False, configs_dir=None, profile=None, log=None) -> dict`:

1. `st = load_settings(configs_dir, profile)`; `concepts = list(cfg.concepts or st["measure"]["concepts"])`.
2. With `copy`, when `out_dir/picked` holds anything: `ValueError` ("already holds files; empty it
   or choose another output folder"). Nothing is ever deleted to make room.
3. `aesthetic.load_model(model_path)` when a model is given; `OSError`, `ValueError`, `KeyError`
   and `TypeError` become `ValueError(f"{model_path}: not a scorer model ({e})")`.
4. `images = batch.list_images(folder)`; `sources = emit.source_ids(images)`;
   `thr = {**consistency.BURST_DEFAULTS, **rules_mod.load(cfg.rules_path).get("consistency", {}), **st.get("burst", {})}`;
   `bursts, singles = groups_of(images, thr)`.
5. `backend = segment.resolve_backend(cfg.backend)`. Only when there are bursts:
   `seg = segment.make_segmenter(backend, cfg.device)`, wrapped in
   `segment.DiskMaskCache(seg, os.path.join(out_dir, segment.MASK_CACHE_DIR, backend))` when the
   backend is not the stub and `cfg.mask_cache` is on. For the stub one log line names the oval.
6. Per burst (`b1`, `b2`, ...) and shot: `judge(shot[0], seg, st, concepts, raw_opts_from(cfg), model)`,
   then `thumbs/f0.jpg.jpg` for `f0.jpg` (`resize_long_edge(rgb, thumb)`) and `crops/f0.jpg.jpg` (a
   window `crop` wide and high on the mean position of the judged area, kept inside the picture),
   both with `save_image(..., fmt="jpeg", quality=90)`. `OnAPhoto` is raised on; any other
   exception, a `ValueError` too, puts `{"files", "burst", "error"}` into `failed` and the burst
   goes on. Then `rank_burst`, each shot's `why`, and the burst's `margin` (the pick's score minus
   the next one's; `None` with one shot).
7. `picks`: the sorted names of every file of the best shot of each burst, of every single and of
   every failed shot. Then the four files; with `copy`, `shutil.copy2` of every pick into `picked/`.

Every path that is written goes through `_target(path, sources)`. It raises `OnAPhoto`, a
`ValueError` of this module ("this is one of the photos; choose another output folder"), when the
path exists with a (device, inode) in `sources`. Nothing written depends on the clock; floats are
rounded to four places.

| File | Content |
|---|---|
| `ranking.json` | `engine_version`, `input_dir` (absolute), `backend`, `concepts`, `scorer` (absolute path or ""), `settings` (the merged `pick.toml`), `burst_finder` (`max_gap_s`, `max_span_s`, `min_similarity`), `photos`, `bursts`, `singles`, `failed`, `picks`. A burst: `id`, `margin`, `shots` (best first). A shot: `files` (names, the judged one first), the keys of `judge`, `sharp_rel`, `score`, `rank`, `pick`, `why` |
| `ranking.csv` | Columns `burst, rank, pick, file, other_files, score, sharp_rel, sharpness, exposure_penalty, blown, p50, scorer, judged_on, why`. The shots of each burst in rank order (`pick` is `yes` or empty), then the failed (no rank, `pick` yes, why `could not be judged and is kept: ERROR`), then the singles (no burst, rank 1, `pick` yes, why `single frame`) |
| `picks.txt` | One absolute path per line: `os.path.join(input_dir, name)` for every name in `picks` |
| `contact-sheet.html` | One section per burst (`b1 · 3 shots · the pick is ahead by 0.21`), one card per shot with the two pictures, `rank · PICK · score`, the file names and the why line; the pick's card has `class="pick"` (a green frame). A list of the failed. The intro says how many singles are not shown, that no photo was moved or changed and, for the stub, that the oval was judged. Text through `html.escape`, picture addresses through `urllib.parse.quote`. Style as in `curate._write_contact_sheet` |

`why` holds `sharpness 0.87 of the burst's sharpest, judged on the mask of subject` (or `... on
the whole frame (no mask found)`), then `exposure penalty 0.24: 1.2% blown, median luma 0.40` when
the penalty is above 0, then `scorer 0.61` with a model. `FILES` names the four files.

### 5. The command and the owner's check

`autoedit pick FOLDER` with `-o/--out DIR` (default `out-picks`), `--copy`, `--aesthetic-model FILE`,
`--concepts A,B`, `--backend stub|sam3|auto`, `--device`, `--configs-dir`, `--profile`. `_cmd_pick`
returns 2 with `folder not found: ...` for a missing folder, else calls `pick.run(args.folder, args.out,
load_run_config(args), model_path=args.aesthetic_model, copy=args.copy, configs_dir=args.configs_dir,
profile=args.profile, log=print)`; a `ValueError` or `OSError` is printed and returns 2. It ends with
the counts, "No photo was moved or changed." and the paths of the files. The module docstring gains
one line; `_cmd_doctor` knows `calibrate.toml` and `pick.toml`.

`scripts/eval_picks.py RANKING.json MY_PICKS.txt [--top 2]` is for the open check: the owner picks
a keeper per burst in their own viewer first and lists the file names in a text file, one per line
(extension optional; `#` starts a comment). `agreement(ranking, names, top=2)` compares lower-cased
names without extension against every file of a shot and takes the best rank per burst. It returns
`bursts`, `agree` (rank at most `top`), `first`, `misses` (burst, file, rank, the tool's pick),
`skipped` (bursts without a name) and `unknown` (names in no burst). The script prints "your keeper
is among the tool's first 2 in N of M bursts" and the misses, and writes nothing.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/pick.py` | new: `load_settings`, `shots_of` and Design 1 and 2 (T1); `run`, `OnAPhoto`, `_why`, `_target`, `_window`, `_write`, `_sheet`, `FILES` (T2) | T1, T2 |
| `src/autoedit/configs/pick.toml` | new (packaged by the existing `configs/*.toml` rule) | T1 |
| `src/autoedit/curate.py` | `features_of` split out of `image_features` | T1 |
| `tests/test_pick.py` | new: tests 1 to 9 (T1), 10 to 22 (T2) | T1, T2 |
| `src/autoedit/cli.py` | `_cmd_pick`, the `pick` parser, one docstring line, two names in `_cmd_doctor`. With `curate.py` the only shared file; neither snapshot changes | T2 |
| `scripts/eval_picks.py` | new | T2 |

## Tasks

T2 needs T1. The session runs the two suites; a Builder runs its own command only.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, medium | Design 1 to 3 as written, with the helpers of "Tests"; tests 1 to 9 | `PYTHONPATH=src python3 -m pytest tests/test_pick.py tests/test_curate.py tests/test_critic.py tests/test_aesthetic.py -q` |
| T2 | Builder, Sonnet, medium | Design 4 and 5; tests 10 to 22 | `PYTHONPATH=src python3 -m pytest tests/test_pick.py tests/test_cli.py tests/test_curate.py tests/test_consistency.py -q` |
| Check | Checker, Opus, high | The diff against this spec; above all that nothing can write over, move or delete a photo; the acceptance checks; the sheet of the real bursts | none |
| Docs | The session, inline | `README.md` (an example next to `curate`, the command list, the file map), `APP_GUIDE.md` (a part after "Culling", the CLI reference, the config files, the file map), `ARCHITECTURE.md`, `CHANGELOG.md`, the plan row | none |

## Tests

`tests/test_pick.py`. Made-up frames are 160 by 240 unless said. `_save(path, pixels, sec=None)`
writes with `quality=95` (JPEG, or PNG when the pixels must stay exact) and, with `sec`, the capture
time `2026:05:01 10:MM:SS` in tag 0x9003 of the Exif sub-directory, as `exif.tobytes()` (fact 10).
`_burst(folder, frames, start=0, prefix="f", ext="jpg")` saves frames two seconds apart as `f0.jpg`,
`f1.jpg`, ...; `_blur` is Pillow's `GaussianBlur`; `_run` is `pick.run(folder, out,
RunConfig(backend="stub"), **kw)`; `_hashes` maps each file under a folder to its SHA-256 and mtime.

```python
def _texture(seed=0, h=160, w=240):
    rng = np.random.RandomState(seed)   # large shapes, patches of local contrast, fine detail; inside 30..225
    yy, xx = np.mgrid[0:h, 0:w]
    a = np.zeros((h, w, 3), np.float32)
    a[: h // 3] = [120, 150, 200]
    a[h // 3:] = [70, 110, 70]
    a[(((xx - 0.3 * w) / (0.15 * w)) ** 2 + ((yy - 0.6 * h) / (0.25 * h)) ** 2) <= 1.0] = [190, 150, 120]
    coarse = PILImage.fromarray(rng.uniform(-30, 30, (h // 8, w // 8)).astype(np.float32), "F").resize((w, h), PILImage.BICUBIC)
    return np.clip(a + np.asarray(coarse)[..., None] + rng.uniform(-12, 12, (h, w, 1)), 30, 225)
```

| # | Test | Asserts |
|---|---|---|
| 1 | `test_settings_file` | `load_settings()` has exactly the values of Design 3 in `measure`, `exposure` and `weights` |
| 2 | `test_profile_file_is_merged` | A profile `pick.toml` with `[weights] exposure = 0.0`: that value is 0, `scorer` and `long_edge` keep theirs |
| 3 | `test_sharpness_falls_with_blur_and_ignores_exposure` | Whole-frame mask on `_texture() / 255` (measured 1.83): blur 0.5, 1.0, 2.0 fall in that order and the first is under 0.8 of the sharp value (0.56); `x * gain + lift` for (0.4, 0), (0.7, 0), (0.8, 0.1), (0.5, 0.4) is equal within 1e-3; `x * 0.1` is under half (the floor); an all-black frame, an all-white frame and an empty mask give 0.0 |
| 4 | `test_noise_on_a_flat_area_is_not_sharpness` | Noise of 0.002 on flat 0.5 and on flat 0.01: under half of the texture's sharpness; with `min_contrast=0.0` above it |
| 5 | `test_sharpness_is_measured_inside_the_mask` | Frame A sharp inside `stub._subject` and blurred (2.0) outside, frame B the other way round: in the oval A is more than twice B; on the whole frame B is above A |
| 6 | `test_exposure_penalty` | Flat frames with `(0.05, 0.25)`: 0.5 and 0.25 give 0; 0.99 gives 1; 0.0 gives 1; 0.1 gives 0.6 with `blown == 0`; one blown row of 40 gives 0.5; one blown channel counts; the blown row outside the mask gives 0; a quarter black with a median of 0.5 gives 0 |
| 7 | `test_rank_burst_scores_and_ties` | Weights 0.25 and 0.15. Equal shots go by name; ranks 1, 2, 3 and one pick; half the sharpness scores 0.5; a full penalty loses to a shot 10 % softer and beats one 30 % softer; scorer 0.2 against 0.9 costs 0.105 and loses to a shot 5 % softer; two shots of sharpness 0 do not raise |
| 8 | `test_shots_pair_a_raw_file_with_its_jpeg` | `b.jpg, a.JPG, a.cr2, c.jpg, c.png, d.dng` give `[a.JPG, a.cr2], [b.jpg], [c.jpg], [c.png], [d.dng]` |
| 9 | `test_the_mask_of_the_segmenter_is_what_is_judged` | `look_picture` replaced by a fixed texture; a fake segmenter that returns the top left quarter with score 0.9: `judged_on == "mask"`, coverage 0.25, it was asked for `["bird"]`, the sharpness is that of the quarter. With score 0.2, and with no mask: `"frame"`, coverage 1 |
| 10 | `test_the_sharp_frame_is_always_picked` | 36 bursts of four JPEG frames: three textures, the sharp frame at each position, the others with blur 0.5, with blur 2.0, or the mean of four copies shifted 0 to 3 px. Each time one burst, the sharp frame is the pick with score 1.0, the next has `sharp_rel < 0.9` (measured at most 0.57, 0.09, 0.41), `picks` is that file |
| 11 | `test_exposure_decides_between_equally_sharp_frames` | PNG frames `x * 0.4`, `x`, `clip(x * 2.4)`: the middle one is the pick with penalty 0; the dark one is as sharp within 1 %, has a penalty in 0.15..0.35 (0.24) and `score == sharp_rel - 0.3 * penalty`; the blown one has penalty 1; the dark one's `why` names the penalty, the pick's does not. With `[weights] exposure = 0.0` in a profile every score equals `sharp_rel` |
| 12 | `test_frame_size_does_not_decide` | One picture (texture 320 by 480, blurred 1.2) and its Lanczos copy of 160 by 240, `long_edge = 240` in a profile: both `sharp_rel` above 0.9 (0.955); at their own sizes `sharpness` differs by more than a quarter (0.41) |
| 13 | `test_no_source_file_changes` | A burst, a single, a text file and `f1.dng` (a renamed frame: the twin of `f1.jpg`, never opened): after `run(..., copy=True)` `_hashes` and the listing are what they were; `picked/` holds `f1.dng`, `f1.jpg`, `lone.jpg`, byte for byte. A second run with the output inside the photo folder: the photos unchanged again |
| 14 | `test_nothing_is_written_over_a_photo` | Photos in `out/picked`, `run(out/picked, out, copy=True)`: `ValueError`, hashes unchanged. Photos in `other/thumbs`, one of them named `f0.jpg.jpg`: `run(other/thumbs, other)` raises `pick.OnAPhoto`, hashes unchanged |
| 15 | `test_the_same_folder_gives_the_same_bytes` | Two runs into two folders: the four `FILES` are byte-identical; `thumbs/` holds `f0.jpg.jpg`, `f1.jpg.jpg`, `f2.jpg.jpg`; no `picked/` without `copy` |
| 16 | `test_singles_are_picks_and_are_not_opened` | A burst of two, a frame 15 minutes later and one without a capture time: `look_picture` was called for the two burst frames only; `singles` and `picks` hold the others; the CSV has 5 lines, the last ends with `single frame`; `picks.txt` has the three absolute paths |
| 17 | `test_one_bad_frame_does_not_stop_the_run` | `look_picture` raises `ValueError("cannot read")` for `f0.jpg`: `failed == [{"files": ["f0.jpg"], "burst": "b1", "error": "cannot read"}]`, the two others are ranked, `picks == ["f0.jpg", "f2.jpg"]`, both are copied, the sheet and the CSV row (no rank, `yes`, the why text) name the error |
| 18 | `test_the_scorer_joins_only_when_a_model_is_given` | PNG frames `x * 0.75` and `x`: without a model `scorer` is None and the scores differ by less than 0.005. With a model of one feature (`lum_mean`, weight -2.0, bias 1.3) the darker frame is the pick (0.60 against 0.36) and the other scores `sharp_rel - 0.15 * gap`; `why` names the scorer. A model file `{}` raises `ValueError` |
| 19 | `test_contact_sheet_marks_the_pick` | Two bursts, one with files named `a #1 f0.jpg`: two `class="pick"`, the stub sentence, `src="thumbs/a%20%231%20f1.jpg.jpg"`, every thumb and crop exists, the pick's card names the sharp file; `margin` is the difference of the first two scores; `ranking.json` equals what `run` returned and has `burst_finder == {"max_gap_s": 30, "max_span_s": 120, "min_similarity": 0.8}` |
| 20 | `test_pick_can_have_its_own_idea_of_a_burst` | `[burst] max_gap_s = 1` in a profile: the two frames, two seconds apart, are singles; without it they are one burst |
| 21 | `test_cli_pick` | `main(["pick", folder, "-o", out, "--copy", "--backend", "stub"])` returns 0, the four files exist, `picked/` holds the pick; the same call again returns 2; a missing folder returns 2; `--concepts person` reaches `ranking.json` |
| 22 | `test_your_keepers_against_the_ranking` | `scripts/eval_picks.py` loaded from its file: four bursts; the names `a1.cr2` (the pick's RAW twin), a comment, an empty line, `B3`, `c2.jpg`, `lone.jpg` give `bursts 3, agree 2, first 1`, one miss `("b2", "B3.JPG", 3, "B1.JPG")`, `skipped ["b4"]`, `unknown ["lone"]`; with `top=3` the miss agrees |

## Acceptance checks (the session runs these)

1. Both suites are green (`PYTHONPATH=src python3 tests/run_all.py`, and with `.venv/bin/python`);
   the stub smoke run of `CLAUDE.md` completes with sidecars and a manifest.
2. Default output is byte-identical to the baseline renders (maximum difference 0), sidecars too.
3. `autoedit pick test-corpus -o out-features/F35/smoke --backend stub`: exit 0, two single frames.
4. Made-up bursts from real photos, written under `.tmp/` by a script of the session that is not
   committed: per photo the unchanged frame, `GaussianBlur(0.7)`, the mean of five copies shifted
   0 to 4 px and `GaussianBlur(2.5)`, as JPEG 92 with capture times two seconds apart, the bursts
   1000 s apart. The unchanged frame is the pick each time (measured: 8 of 8, leads 0.09 to 0.13).
5. The real bursts, when the corpus is there:
   `autoedit pick curated-switzerland-summer-2026/selected -o out-features/F35/switzerland --backend stub`.
   Expected: 12 bursts, 766 single frames, about 45 s. `find . -type f -exec shasum -a 256 {} + | sort`
   in the photo folder gives the same text before and after. The Checker reviews the diff and looks
   at the sheet: the three out-of-focus frames are ranked last.
6. That folder once with `--backend sam3` through `.venv/bin/python`, one job: in how many of the 34
   frames `subject` gave a mask, and in how many bursts the pick differs. A result, not a pass mark.
7. Open, the owner's: on a shoot of at least 30 bursts, choose the keepers first, then run
   `scripts/eval_picks.py`. Done when the keeper is among the first two in 8 of 10 bursts.

## Known limits

- The stub judges an oval in the middle of the frame. A frame focused on something near the edge
  loses: in two of the real bursts the "out of focus" frame was focused on flowers in front.
- No eyes, smiles or pose: a moving subject caught twice equally sharp is a tie. Noise counts as
  fine detail (3 % more at a noise of 0.02), so at high ISO the gaps shrink.
- A blown frame can read sharper (27 % at worst, 1.5 EV over); the penalty of 0.3 covers what was
  measured. A sharp frame 3 EV under loses to a frame blurred by 1 px in 7 of 30.
- A RAW file is judged on its preview and reads 8 to 9 % under its JPEG (8 pairs), so a shot kept
  as RAW only is behind one kept as JPEG only. The two are paired by name only: in a folder renamed
  by `autoedit curate` (the repo's corpora) the runner-up can be the pick's own RAW file.
- The finder's limits apply: CR3, RAF, ORF and RW2 files are in no burst (fact 3; one with a JPEG
  of the same name joins through it); a burst may span 120 s and still gets one pick.
- A single frame is never opened, so a broken file can be a pick. Sub-folders are not read. One
  process: 0.5 s per judged JPEG, after the burst search (27 s for 800 files). The same bytes on a
  rerun are promised for the stub only: SAM 3 masks can differ a little from run to run.

## Proposed plan row

`| F35 | Pick the keepers | M | done as a tool (`autoedit pick FOLDER [-o DIR] [--copy]`); the check against the owner's own keepers on a shoot of 30 bursts is open |`

For the tables at the end of `FEATURE_PLAN.md`:

- For the owner: "Pick the keepers: are the weights right, one pick per burst or more, and should
  picks be tagged for Lightroom?" Evidence: `out-features/F35/switzerland/contact-sheet.html`. The
  change: the values in `pick.toml`; a later task for `--keep N` and for XMP. Only the owner can
  choose the keepers of a real shoot and run `scripts/eval_picks.py`.
- Proposed, not built: a capture time for CR3, RAF, ORF and RW2 through `rawpy` in
  `consistency.capture_time` (needs such a file to test); pairing by the sub-second tag too.

## Result

Built on 2026-11-17 in the unattended run. Status: done as a tool (`autoedit pick`).

- **Built** by Builders (Sonnet) from this spec. `curate.features_of` was checked against the
  committed code on four photos: identical numbers.
- **The Checker (Opus, high)** found nothing that had to be fixed and seven notes; the session
  fixed four, with tests: `pick` no longer overwrites a `ranking.json`, `ranking.csv`, `picks.txt`
  or `contact-sheet.html` in the output folder that an earlier pick run did not write (curate's
  own sheet in a corpus folder was the case); the values of `pick.toml` are checked (a string
  weight gave a traceback, `min_coverage = 0` or `dark_p50 = 0` failed every frame with NaN); and
  `scripts/eval_picks.py` accepts the lines of `picks.txt` (full paths) as well as file names.
- **What held:** no photo is moved, changed or deleted (the 800 files of the real folder hash the
  same before and after); links to a photo are refused as outputs; file names on the sheet are
  escaped; a broken frame becomes a single and is kept; default output of `autoedit run` is
  byte-identical to the committed code.
- **Checks:** the smoke run (exit 0, two single frames, `out-features/F35/smoke/`). The real
  bursts with the stub (`out-features/F35/swiss/`): 12 bursts of 34 shots, 766 single frames,
  12 picks that match the expected ones, the soft frames ranked last. Not run: acceptance 6 with
  SAM 3, and 7, the owner's own keepers on a shoot of 30 bursts (only the owner can choose them).
