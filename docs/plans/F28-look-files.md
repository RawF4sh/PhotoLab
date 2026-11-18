# F28. Look files: export, import, preview card

Status: spec (2026-11-17). Size M. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F28. Written in an
unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked; the owner's decisions are in the table
below. Build it after [F27](F27-style-from-edits.md): a look file carries F27's habit table and calls four F27 functions.

The design was built once on a scratch copy of the tree (F23, F24 and the first task of F25 in it; F27's habit pieces
as a stand-in written from its spec), deleted again. The reference code is that code and every number is measured with
the stub (SAM 3 was not run). `tests/run_all.py` gave 847 passed and 1 failed on both interpreters: the 24 tests of this
spec passed; one F25 test, mid-build when the copy was taken, failed. Of 38 rules removed in turn, 37 made a test fail.

## Goal

A look can be given away as one small file and taken in from one, and a picture shows what it does: `looks export`
writes `NAME.aelook`, `looks import` checks a file from someone else and puts its look into the profile, `looks card`
renders one before-and-after sheet (PNG and HTML). No config file, run setting or route changes; default output is unchanged.

## What was found in the code

1. `looks.py`: `Look` has 22 flat fields, 21 numbers (F23's `grain`, F24's `dehaze_add`, `bw` and its six weights among
   them) and the boolean `preserve_exposure`. `load` skips every other key; with F27 a look may also hold `habit`.
2. `paths.py::_check_looks` and `looks save --set` check a look for known fields and finite numbers, not for a range.
3. Nothing holds what a look shapes inside an op's bounds: `looks.py::shape` multiplies and adds, and `contrast`,
   `curve`, `clarity`, `dehaze` and `vibrance` in `edit/ops.py` take any amount. The packaged rules give at most
   contrast 16, curve 30, clarity 12, dehaze 18 and a lift of 1.0 EV.
4. A look's `preserve_exposure = true` switches off the review gate's darkness check (`pipeline.py::process_one`).
5. A palette is no part of a look: `palette.py::Palette` is seven numbers and `protected` (names no code reads yet),
   chosen per run with `--palette NAME`; `auto`, `none`, `off` are words of that setting. The auto picker takes packaged
   palettes by name: one that replaced `warm_gold` would change default runs. A profile `palettes.toml` is unchecked.
6. `cli.py::write_look` writes a look with all its fields into `<profile>/looks.toml`. `_cmd_looks` handles `list`,
   `delete`, then `save` as what is left. `cli.main` lets a missing `--profile` folder pass for `looks save` and `calibrate`.
7. A profile look's name is any TOML key (`["../x"]` is one): a name that becomes a file name is checked where it is used.
8. `tomllib` (Python 3.13, both interpreters): brackets a thousand deep raise `RecursionError`, a whole number of over
   4300 digits a plain `ValueError`; one of 400 digits is read, and `math.isfinite` of it raises `OverflowError`. `nan`
   and `inf` are valid TOML, a byte order mark is not, `true == 1`. A dotted key of 16 kB takes 0.36 s, of 40 kB 2 s.
9. `Pipeline.process_one(path, image=...)` edits a picture that is handed in; `path` then only names the result
   (`calibrate.py::_render_round` does so at 1600 px). `emit.grain_args` with `ops.grain` adds grain after a resize.
10. `scripts/contact_sheet.py` is outside the package: a command of the product cannot import its sheet code.
11. `settings_schema.py::_choices` lists the looks and palettes of the merged files and `web/service.py::_ALLOWED` has
    `look` and `palette`: an imported look can be chosen in every API request. `api_v1.json` records routes only.
12. No photo is committed. A test photo of flat colours trips the banding gate under `natural` (0.35); with noise of 3
    levels it does not. `tests/run_all.py` has `tmp_path` and `pytest.raises`, no `capsys` and no `parametrize`.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Which photos does a card show? (the plan's owner decision; it stays open) | The photos and folders named on the command line, at most 12. With none named: one frame drawn by code. Nothing is downloaded and no photo is shipped | The owner's answer becomes the default: one line in `lookcard.pick` that takes a folder when none is named |
| Routes in the API | None: the three commands are CLI only; `web/app.py` and `api_v1.json` do not change. An import through the API is a write into the profile from a request, and belongs with the screen that shows author, licence and a name clash first. `look` and `palette` in a request already take an imported look (fact 11) | Three routes over `lookfile` and `lookcard`, with that screen |
| A palette in the file | Optional `[palette]`: one palette, stored under the look's name and chosen with `--palette NAME`. A look does not name its palette: that is a new look field and a change in `pipeline.py` | A look field that names a palette, a feature of its own |
| `preserve_exposure` | Not in the format: a file cannot switch off the darkness check. Export leaves it out and says so. Whether any look may set it is the owner's open decision (F8) | Remove the name from `NOT_CARRIED` |
| Limits of a look field, and of the file | A multiplier 0 to 3: at 3 the largest value the packaged rules give (curve 30) is still inside the op's bounds. `exposure_mul` 0 to 1.4, where a calibrated look stops (`calibrate.toml`): above 1 it lifts past the headroom the rules measured. Every other field: the bounds of its op in `ops.OP_BOUNDS`. The file: 16 kB, four times the largest honest file; it bounds the slowest input (fact 8), and a file of 10 MB is refused unread, in 0.14 ms. Constants of the module, not a config file a profile could loosen | `_MUL`, `MAX_BYTES` in `lookfile.py` |
| Limits of a palette and a habit | Hue 0 to 360, chroma 0 to 0.15 (packaged: at most 0.055), protection 0 to 1, at most 8 protected names. A habit: F27's own check, and only ops with an entry under `[limits]` in `instructions.toml`, each value inside it | `PALETTE_LIMITS`, `check_habit_limits` |
| A name that is taken | A packaged look or palette: refused, also with `--force`. One of the profile: refused without `--force`, replaced with it. `surprise`, and for a palette `auto`, `none`, `off`: refused. `--name NEW` is the way out every time | `lookfile.install` |
| What export writes, and what import keeps | Every field, also at its default, so a file means the same after a default changes; it writes over its `-o` file, as `looks lut` does. Author and licence are printed at import and not stored: the profile holds numbers | `dumps`; keeping the imported file in the profile is a task of its own |
| What a file cannot hold | A field this version does not know: the file is refused whole, with the valid keys named, and nothing is skipped silently. A LUT (F25 left it open): it stays a run setting with its own reader, and a table would need a limit 60 times larger | A later format version |
| What a card holds, and how it is rendered | Pictures, the look's name, `before`, `after`, one line with the version and the segmenter; no file name and no value of the look. The rules decide, whatever the profile says. The segmenter is the run's default, the stub today, or `--backend sam3` | `_sheet`, `_page`, `render`; the owner's open decision on the default backend |

## Design
### 1. The file and its reader (`lookfile.py`, new)
A look file is TOML, read with `tomllib` and nothing else: TOML has no include, and no value in the file is a path or
is run. The reader works on bytes: size, parse, version, then every key and value. An error is a `ValueError` that
starts with the file's name without its folder; a name from the file is shown cut and through `repr`, a value never.

```python
VERSION, EXT = 1, ".aelook"
MAX_BYTES = 16 * 1024      # a file with every field, a palette and twelve habits is 2 kB
MAX_TEXT, MAX_PROTECTED = 200, 8         # author and licence, one line each; names in a palette's `protected`
NAME = re.compile(r"[a-z0-9_]{1,40}")
LOOK_WORDS = ("surprise",)               # the word of --look that picks a look (F26): no file's look takes it
PALETTE_WORDS = ("auto", "none", "off")  # words of --palette: no file's palette takes them
NOT_CARRIED = ("preserve_exposure",)     # a file cannot switch off a check of the review gate
_MUL = {"contrast_mul": 3.0, "clarity_mul": 3.0, "dehaze_mul": 3.0, "curve_mul": 3.0, "exposure_mul": 1.4}
_ADD_OP = {"vibrance_add": "vibrance", "saturation_add": "saturation", "curve_add": "curve", "dehaze_add": "dehaze"}
PALETTE_LIMITS = {"shadow_hue": (0.0, 360.0), "shadow_chroma": (0.0, 0.15), "mid_hue": (0.0, 360.0), "mid_chroma": (0.0, 0.15),
                  "highlight_hue": (0.0, 360.0), "highlight_chroma": (0.0, 0.15), "neutral_protection": (0.0, 1.0)}
_KEYS = ("aelook", "name", "author", "licence", "look", "palette", "habit")
_WORD = re.compile(r"[a-z][a-z0-9 _-]{0,39}")

def parse(data: bytes, where: str = "look file") -> LookFile:
    """The look in the bytes of a look file, or a ValueError that says which rule broke."""
    def bad(why: str) -> ValueError:
        return ValueError(f"{where}: {why}")
    def table(value, what: str, known) -> dict:
        if not isinstance(value, dict):
            raise bad(f"{what} must be a table")
        for key in value:
            if key not in known:
                raise bad(f"unknown key {_show(key)} in {what}; valid keys: {', '.join(known)}")
        return value
    def number(value, lo: float, hi: float, what: str) -> float:
        # nan fails the comparison, as inf and a whole number of any length do: no float() before it
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise bad(f"{what} must be a number from {lo:g} to {hi:g}")
        return float(value)
    def text(key: str) -> str:
        value = doc.get(key, "")
        if key in doc and not (isinstance(value, str) and 0 < len(value) <= MAX_TEXT and value.isprintable()):
            raise bad(f"{key} must be one line of text, at most {MAX_TEXT} characters")
        return value
    if len(data) > MAX_BYTES:
        raise bad(f"larger than {MAX_BYTES // 1024} kB, not read")
    try:
        doc = tomllib.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise bad("not a text file") from None
    except tomllib.TOMLDecodeError as e:
        raise bad(f"not valid TOML ({e})") from None
    except (ValueError, RecursionError):   # a number of thousands of digits; brackets a thousand deep
        raise bad("not valid TOML") from None
    if type(doc.get("aelook")) is not int:
        raise bad("no `aelook = 1` line: this is not an AutoEdit look file")
    if doc["aelook"] != VERSION:
        raise bad(f"this AutoEdit reads look file format {VERSION}, not the format of this file")
    table(doc, "the file", _KEYS)
    name = doc.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise bad("name must be 1 to 40 lowercase letters, digits or underscores")
    lim = limits()
    body = doc.get("look")
    for key in NOT_CARRIED:
        if isinstance(body, dict) and key in body:
            raise bad(f"{key} cannot be in a look file: a file does not switch off a check")
    body = table(body, "[look]", tuple(lim))
    look = looks_mod.Look(**{k: number(v, *lim[k], f"{k} in [look]") for k, v in body.items()})
    palette = None
    if "palette" in doc:
        body = dict(table(doc["palette"], "[palette]", (*PALETTE_LIMITS, "protected")))
        words = body.pop("protected", [])
        if not (isinstance(words, list) and len(words) <= MAX_PROTECTED
                and all(isinstance(w, str) and _WORD.fullmatch(w) for w in words)):
            raise bad(f"protected in [palette] must be a list of at most {MAX_PROTECTED} concept names")
        palette = Palette(name=name, protected=tuple(words),
                          **{k: number(v, *PALETTE_LIMITS[k], f"{k} in [palette]") for k, v in body.items()})
    habit = looks_mod.check_habit(doc["habit"], where) if "habit" in doc else {}
    return LookFile(name, look, palette, habit, text("author"), text("licence"))
```

- `limits()` is `{f: (0.0, _MUL[f]) if f in _MUL else OP_BOUNDS[_ADD_OP.get(f, f)] for f in looks_mod.FIELDS if f not
  in NOT_CARRIED}`, with `OP_BOUNDS` imported from `.edit.ops` inside the function.
- `LookFile` is a dataclass: `name`, `look`, `palette = None`, `habit` (an empty dict), `author = ""`, `licence = ""`.
  `_show(v)` is `repr(str(v)[:40])`; `_HEAD` is the two comment lines the example below starts with. `check_name(name)`
  returns a string that fully matches `NAME`, else raises `ValueError(f"look name {_show(name)}: use 1 to 40 lowercase
  letters, digits and underscores")`. The module imports `looks` as `looks_mod`, `paths` and `palette.Palette`;
  `instructions`, `load_palettes` and `cli.write_look` are imported inside their functions (`cli` imports this module).
- `read(path)`, with `name = os.path.basename(path)`: the name must end in `.aelook` in any case (`NAME: a look file
  must end in .aelook`) and `os.path.isfile(path)` must hold (`NAME: look file not found`). It reads `MAX_BYTES + 1`
  bytes, turns an `OSError` into `NAME: cannot be read (...)` with its `strerror`, and returns `parse(data, name)`.
- `dumps(name, look, palette=None, habit=None, author="", licence="")` is `_HEAD` plus `paths.dump_toml` of a dict built
  in this order: `aelook`, `name`, `author` and `licence` when given, `look` (`{f: getattr(look, f) for f in limits()}`),
  `palette` (`_palette_table(pal)`: the seven values of `PALETTE_LIMITS` by `getattr`, then `"protected":
  list(pal.protected)`), `habit` when not empty. Before it returns: `parse(text.encode("utf-8"), f"look {name}")`, so a
  file that import would refuse is never written. `write(path, text)` refuses a path that does not end in `.aelook`,
  writes UTF-8 with `newline="\n"` to `path + ".part"` and swaps it in with `os.replace`.

### 2. Export and import (`lookfile.py`)
`check_habit_limits(habit, config, where)`, `config` from `paths.config_paths(...)`: a habit repeats only what a typed
instruction may do. `vocab = instructions.load_vocab(config.get("instructions_path") or
paths.config_path("instructions.toml", packaged_fallback=True))`. `instructions.habit_adjustments(habit, "x", vocab)`
runs in a `try`; its `ValueError` (an op without a limit, an unknown place) is raised again as `f"{where}: {e}"`. Then
`abs(value) > vocab.limits[op]`, for any scene, target and op, raises
`f"{where}: habit {scene}/{target}: {op} must be from {-vocab.limits[op]:g} to {vocab.limits[op]:g}"`.

`export(name, configs_dir=None, profile=None, palette=None, author="", licence="") -> (text, notes)`. `config =
paths.config_paths(configs_dir, profile)`; `library = looks_mod.load(config["looks_path"])`; a name not in it is
`look 'NAME' not found (known: ...)`; then `check_name(name)`. `palette` names one of
`load_palettes(config["palettes_path"])`, else `palette 'P' not found (known: ...)`. `habit =
looks_mod.load_habits(config["looks_path"]).get(name) or {}`, with `check_habit_limits(habit, config, f"look {name}")` when
it is not empty. `notes`: for a name of `NOT_CARRIED` that is true in the look, `not in the file: preserve_exposure (a
look file does not switch off a check)`. It returns `dumps(name, library[name], pal, habit, author, licence)`, `notes`.

`install(lf, profile, name=None, force=False, configs_dir=None) -> dict`. Every check runs before the first write:
1. `name = check_name(name or lf.name)`. `name` in `LOOK_WORDS`: `'NAME' is a word of --look; import the file under
   another name with --name`. The file has a palette and `name` is in `PALETTE_WORDS`: the same with `--palette`.
2. For the kind `look`, and for `palette` when the file has one, with the file `looks.toml` or `palettes.toml`: `name`
   in `paths.read_config` of that file in `configs_dir or paths.configs_dir()` gives `'NAME' is a packaged KIND; import
   the file under another name with --name`; `name` in `paths.read_config` of it in the profile gives, without `force`,
   `the profile already has a KIND 'NAME': --force replaces it, --name NEW keeps both`, and with it joins `replaced`.
3. With a habit: `check_habit_limits(lf.habit, paths.config_paths(configs_dir, profile), f"look {name}")`.
4. The palette first: `os.makedirs(profile, exist_ok=True)`, `data = paths.read_config(path)`, `data[name] =
   _palette_table(lf.palette)`, written with `paths.dump_toml`. Then `write_look(profile, name, asdict(lf.look),
   habit=lf.habit or None)`. The look is last, so a look in the profile has its palette. It returns a dict: `name`,
   `look_path`, `palette_path` (None without a palette), `replaced`, `rewritten` (the second value of `write_look`).

### 3. The commands (`cli.py`)
- **Parsers**, next to `save` and `delete`: `export NAME [-o FILE.aelook] [--palette P] [--author TEXT] [--licence
  TEXT]`; `import FILE.aelook [--name NEW] [--force]`, the name with `dest="new_name"`; `card NAME [PHOTO_OR_FOLDER ...]
  -o DIR [--palette P] [--backend stub|sam3|auto]`, with `photos` as `nargs="*"` and `-o/--out` required. `--author`
  and `--licence` default to `""`. The three join the tuple that gives each action its own `--profile`.
- `cli.main`: the condition that lets a missing `--profile` folder pass becomes `args.looks_cmd in ("save", "import")`.
  `_cmd_looks` imports `lookfile` and gets three branches before the comment `# save`, each ending in `return 0`; the
  function's `except (ValueError, OSError)` prints the message and returns 2.
- **export:** `text, notes = lookfile.export(args.name, args.configs_dir, args.profile, args.palette, args.author,
  args.licence)`, `out = args.out or args.name + lookfile.EXT`, `lookfile.write(out, text)`. It prints
  `wrote OUT: look NAME`, with ` with palette P` when one was named, then each note.
- **import:** `lf = lookfile.read(args.file)`, `r = lookfile.install(lf, args.profile or paths.default_profile_dir(),
  args.new_name, args.force, args.configs_dir)`. It prints, each line only when it applies:
  `imported look NAME into LOOK_PATH`; `  author: TEXT`; `  licence: TEXT`;
  ``  it repeats changes of its own on some scenes (habits); `autoedit looks list` shows them``;
  `  with the palette NAME in PALETTE_PATH`; per replaced kind `  it replaced the KIND 'NAME' that was in the profile`;
  `note: the file was rewritten, so comments in it are gone`; and last
  `use it with: autoedit run <in> <out> --look NAME`, with ` --palette NAME` when it brought one.
- **card:** `cfg = load_run_config(argparse.Namespace(configs_dir=args.configs_dir, profile=args.profile,
  backend=args.backend, look=args.name, palette=args.palette))`, `r = lookcard.render(args.name, args.photos, args.out,
  cfg, log=print)`. It prints `no photo was named: the card shows a drawn sample frame` when `r["photos"]` is 0, per
  flagged photo `photo N is flagged for review with this look: REASON`, then `wrote PNG` and
  `wrote HTML  (N before-and-after, segmenter: BACKEND)`.

### 4. The card (`lookcard.py`, new)
The edit is the product's own, `Pipeline.process_one` with `look=NAME`, on a copy of 1600 px (fact 9); before is the
decoded photo. Both are brought to 1200 px, and a look's grain is added to the after picture then, as the export does.

```python
MAX_PHOTOS = 12
RENDER_LONG_EDGE = 1600     # the edit is rendered at this size, as `autoedit calibrate` renders its pairs
PAGE_LONG_EDGE = 1200       # a picture in the HTML page
CELL = (480, 360)           # the box of a picture on the PNG sheet
_COLOURS = np.array([[1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 1, 1], [0, 0, 1], [1, 0, 1]], np.float32)

def sample_frame(w: int = 1200, h: int = 800) -> np.ndarray:
    """A drawn frame for a card without photos. Nothing in it is near white or black: no packaged look is flagged on it."""
    yy, xx = np.mgrid[0:h, 0:w]
    y, x = (yy / h).astype(np.float32), (xx / w).astype(np.float32)
    t = np.clip(y / 0.55, 0.0, 1.0)[..., None]
    a = (1 - t) * np.float32([0.22, 0.40, 0.72]) + t * np.float32([0.55, 0.66, 0.78])            # sky
    g = np.clip((y - 0.55) / 0.33, 0.0, 1.0)[..., None]
    a = np.where((y >= 0.55)[..., None], (1 - g) * np.float32([0.35, 0.50, 0.22]) + g * np.float32([0.14, 0.26, 0.10]), a)
    a[((x - 0.5) / 0.13) ** 2 + ((y - 0.52) / 0.2) ** 2 <= 1.0] = [0.80, 0.60, 0.48]              # a skin tone
    a = a + np.random.RandomState(7).normal(0.0, 0.012, (h, w, 1))                                 # a little texture
    ramp = np.repeat((0.04 + 0.71 * np.clip(x / 0.5, 0.0, 1.0))[..., None], 3, axis=-1)
    patches = 0.15 + 0.45 * _COLOURS[np.clip(((x - 0.5) * 12).astype(int), 0, 5)]
    a = np.where((y >= 0.88)[..., None], np.where((x < 0.5)[..., None], ramp, patches), a)   # a grey ramp, six patches
    return np.clip(a, 0.0, 1.0).astype(np.float32)

def render(name: str, photos: list[str], out_dir: str, cfg, log=None) -> dict:
    """Write <out_dir>/<name>.png and <name>.html for look `name` of `cfg`'s library. Returns
    {"png", "html", "photos", "flagged": [(photo number, the review gate's reason)], "backend"}."""
    from .pipeline import Pipeline
    lookfile.check_name(name)       # it becomes a file name
    # The rules decide, whatever the profile says: a card can be made again, and no photo leaves the machine.
    pipe = Pipeline.build(cfg.model_copy(update={"look": name, "decider": "rules"}))
    if name not in pipe.looks:
        raise ValueError(f"look {name!r} not found (known: {', '.join(pipe.looks)})")
    files = pick(photos)
    pairs, flagged = [], []
    for i, path in enumerate(files or [None], 1):
        if log:
            log(f"card: photo {i} of {max(len(files), 1)}")
        img = (downscale_long_edge(load_image(path, decode=raw_opts_from(pipe.cfg)), RENDER_LONG_EDGE)
               if path else Image(rgb=sample_frame()))
        rgb, res = pipe.process_one(path or "sample", image=img)
        grain = emit.grain_args(res, pipe.cfg)
        after = resize_long_edge(rgb, PAGE_LONG_EDGE)
        pairs.append((_to_u8(resize_long_edge(img.rgb, PAGE_LONG_EDGE)),
                      _to_u8(ops.grain(after, *grain) if grain else after)))
        if res.needs_review:
            flagged.append((i, res.review_reason))
    os.makedirs(out_dir, exist_ok=True)
    foot = f"AutoEdit {__version__}, segmenter: {pipe.cfg.backend}"
    out = {"png": os.path.join(out_dir, name + ".png"), "html": os.path.join(out_dir, name + ".html"),
           "photos": len(files), "flagged": flagged, "backend": pipe.cfg.backend}
    _sheet(name, pairs, foot).save(out["png"])
    with open(out["html"], "w", encoding="utf-8") as fh:
        fh.write(_page(name, pairs, foot))
    return out
```

- `pick(photos) -> list[str]`. A folder gives `batch.list_images(folder)`; a file must exist and pass
  `imageio.is_supported`; anything else is `ValueError(f"not a photo or a folder: {p}")`. Names given and no photo
  found: `ValueError("no photo in the folders that were named")`. Over 12: `[files[i * (n - 1) // 11] for i in range(12)]`.
- `_sheet(name, pairs, foot) -> PIL image`, RGB on (18, 18, 20). One photo to a row up to three photos, two from four
  on. Photo `i` (from 0) has its before box, of `CELL`, at `(12 + (i % per_row) * 984, 60 + (i // per_row) * 396)` and
  its after box 492 px to the right; the sheet is `12 + per_row * 984` wide and `60 + rows * 396 + 24` high. A picture
  is fitted into its box with `thumbnail(CELL, LANCZOS)` and centred. The name stands in the 60 px above (size 28),
  `before` and `after` 4 px under their boxes (size 14), the foot line 24 px above the bottom (size 13). Fonts:
  `ImageFont.load_default(size=N)`, without the size where that raises `TypeError`.
- `_page(name, pairs, foot) -> str`. One file and no script. Head: charset, viewport, a small `<style>` and `<meta
  http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`. Body:
  `<h1>NAME</h1>`, per photo a `<figure>` of two columns with `<img alt="Photo N, before">`, `<img alt="Photo N, with
  the look">` and a `<figcaption>` holding `before` and `after`, then `<footer>FOOT</footer>`. Every picture is inline:
  `data:image/jpeg;base64,` at quality 88 with `subsampling=0`. Name and foot pass `html.escape`.
- Measured with the stub: 12 photos picked from 110 JPEGs of 22 megapixels take 32 s and 1.7 GB; the sheet is 1980 by
  2460 px and 5.3 MB, the page 8.5 MB. The drawn frame takes 2 s.

## File format

`looks export evening_road --palette evening_road --author "A. Example" --licence "CC BY 4.0"` writes (813 bytes):

```toml
# AutoEdit look file. Data only: numbers and short text; nothing in it is run.
# Read it with: autoedit looks import FILE
aelook = 1
name = "evening_road"
author = "A. Example"
licence = "CC BY 4.0"

[look]
contrast_mul = 0.9
vibrance_add = 0.0
saturation_add = -8.0
clarity_mul = 0.6
dehaze_mul = 0.5
exposure_mul = 1.0
curve_mul = 1.0
curve_add = 3.0
grade_shadows_temp = -6.0
grade_shadows_tint = 0.0
grade_highlights_temp = 12.0
grade_highlights_tint = 2.0
grain = 20.0
dehaze_add = -6.0
bw = 0.0
bw_red = 0.0
bw_yellow = 0.0
bw_green = 0.0
bw_cyan = 0.0
bw_blue = 0.0
bw_magenta = 0.0

[palette]
shadow_hue = 250.0
shadow_chroma = 0.02
mid_hue = 70.0
mid_chroma = 0.01
highlight_hue = 70.0
highlight_chroma = 0.03
neutral_protection = 0.85
protected = ["skin", "snow"]

[habit.landscape.sky]
exposure = -0.3
```

`aelook`, `name` and `[look]` are required; a field left out of `[look]` or `[palette]` takes its neutral default.
`author`, `licence`, `[palette]` and `[habit.SCENE.TARGET]` are optional. Nothing else may stand in the file.

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/lookfile.py` | new: Design 1 (T1); `check_habit_limits`, `export`, `install` (T2) | T1, T2 |
| `src/autoedit/lookcard.py` | new: Design 4 | T3 |
| `src/autoedit/cli.py` | Design 3: the parsers, the tuple, the condition in `main`, the branches `export` and `import` (T2); the parser and the branch `card` (T3) | T2, T3 |
| `tests/test_look_files.py` | new: tests 1 to 11 (T1), 12 to 19 (T2), 20 to 24 (T3) | T1 to T3 |

Not changed: every file under `configs/`, `contract.py`, `pipeline.py`, `looks.py`, `paths.py`, `palette.py`, `emit.py`,
`web/`, both snapshots; so `config_digest` and every sidecar stay. Relied on: F23 (`Look.grain`, `emit.grain_args`,
`ops.grain`); F24 (the `bw` fields, `dehaze_add`, the looks `bw_classic`, `film_warm`, `film_faded`); F27's task T2:
`looks.check_habit` in `parse`, `looks.load_habits` in `export`, `cli.write_look(..., habit=)` in `install`, and
`instructions.habit_adjustments` in `check_habit_limits`, which must raise for an op without a limit and for an unknown
place. If F27's "Result" names one otherwise, these four places change. `_cmd_looks` is shared with F25 and F27.

## Tasks

One after the other: they share `lookfile.py`, `cli.py` and the test file. `.venv` has no pytest: `tests/run_all.py`.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high (a file from someone else is read here) | Design 1; tests 1 to 11 | `PYTHONPATH=src python3 -m pytest tests/test_look_files.py tests/test_profile.py tests/test_style.py tests/test_calibrate.py -q` |
| T2 | Builder, Sonnet, medium | Design 2; Design 3 without `card`; tests 12 to 19 | `PYTHONPATH=src python3 -m pytest tests/test_look_files.py tests/test_profile.py tests/test_cli.py tests/test_style.py tests/test_calibrate.py tests/test_lut.py tests/test_defaults.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | Design 4; the `card` part of Design 3; tests 20 to 24 | `PYTHONPATH=src python3 -m pytest tests/test_look_files.py tests/test_cli.py tests/test_grain.py tests/test_bw.py tests/test_calibrate.py tests/test_api_contract.py tests/test_settings_schema.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec. With care: every way a value of a file reaches a file name, a path, the terminal or the page; whether a check can be passed by the order or the kind of a key; what `--force` can replace | none |
| Docs | The session, inline | `README.md` (the three commands, the example file), `APP_GUIDE.md` (the `looks` commands), `SECURITY.md` (what a look file is, what import checks, what a card holds), `ARCHITECTURE.md` (the two modules), `CHANGELOG.md`, the plan row and the tables | none |

## Tests

`tests/test_look_files.py`, without `pytest.mark` (fact 12); no existing test changes. `HEAD` is `aelook = 1` and `name
= "mine"`; `GOOD` adds `[look]` with `contrast_mul = 1.2`, `curve_add = 8`; `PAL` adds `[palette]` with `mid_hue = 200`,
`mid_chroma = 0.03`. `_refused(text, part)`: `parse(text, "bad.aelook")` raises a `ValueError` that starts with
`bad.aelook: ` and holds `part`. `_run(*argv)`: `cli.main` with stdout and stderr captured; it returns code and text.
`_scene(path, w=600, h=400, dark=1.0)`: the scene of `tests/test_grain.py` times `dark` plus seeded noise of 3 levels, a
PNG. `_photos(tmp_path, n)`: `IMG_PRIVATE_n.png`, 600 by 450, in `Client_Folder`. `_cells(png, n)`: the before and after
boxes of a sheet of up to three photos. "Levels" are of 255.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_dumps_then_parse_gives_the_same_look` | For every packaged look `parse(dumps(name, look).encode())` equals `LookFile(name, look)`. For every packaged palette, with a habit of two targets (`sky`, `place:top`), the author `Jané Doe` and a licence: the palette is equal apart from its name, which is the look's; look, habit, author and licence are equal |
| 2 | `test_a_look_file_holds_exactly_this` | The text starts with the two comment lines, ends with a newline and holds no `/` and no backslash. As TOML its keys are `aelook`, `name`, `look`, in this order; `[look]` has exactly the keys of `limits()`, which are `looks.FIELDS` without `preserve_exposure`, in that order. With everything given the keys are `aelook`, `name`, `author`, `licence`, `look`, `palette`, `habit`, and `[palette]` has the seven numbers and `protected`. `write` gives a file with that text and leaves no `.part`; a path that ends in `.toml` raises. The largest honest file (a name of 40 letters, a palette, 12 habit targets, two lines of 200 characters) is under a quarter of `MAX_BYTES` |
| 3 | `test_every_look_field_has_a_limit` | `set(limits()) \| set(NOT_CARRIED) == set(looks.FIELDS)`; four multipliers are (0, 3), `exposure_mul` (0, 1.4); `vibrance_add`, `saturation_add`, `curve_add`, `dehaze_add`, `grain`, `bw`, `bw_blue`, `grade_shadows_temp` have the bounds of their op in `ops.OP_BOUNDS`; every packaged look lies inside the limits; every pair of `calibrate.load_config()["bounds"]` lies inside; `MAX_BYTES == 16 * 1024`, `VERSION == 1`, `EXT == ".aelook"` |
| 4 | `test_refuses_unknown_keys` | `photo = "IMG_1.jpg"` at the top: `unknown key 'photo' in the file`, with `valid keys: aelook, name, author, licence, look, palette, habit`. `contrast_mull` in `[look]`: named, with the valid fields, `preserve_exposure` not among them. `hue` in `[palette]`; a table `[extra]`; `include`, `source_path`, `history`, `lut`, `exposure`, `sharpen` in `[look]`. A key of 300 letters is cut to 40 in the message; a key with an escape character is not printed raw |
| 5 | `test_refuses_numbers_outside_the_limits` | For every field of `limits()`: 0.01 above the upper and below the lower limit is refused with `FIELD in [look] must be a number from LO to HI`, the value not in the message; both limits themselves are read. For `curve_add`: `1e308`, `-1e308`, `1e400`, a whole number of 400 digits and its negative, `0x7fffffffffffffff`, `1_000_000` give the same message, 5000 digits give `not valid TOML`, and `"8"`, `true`, `[8]`, a date and an inline table give `must be a number`. `curve_add = 8` reads as 8.0 |
| 6 | `test_refuses_nan_and_inf` | `nan`, `+nan`, `-nan`, `inf`, `+inf`, `-inf` in `curve_add`, in `contrast_mul`, in `mid_chroma` of a palette (each with its range message) and in a habit value (`finite`) |
| 7 | `test_refuses_a_file_that_is_too_large` | A file of 10 MB (`GOOD` and one long comment): `read` raises exactly `big.aelook: larger than 16 kB, not read`. `GOOD` padded to exactly `MAX_BYTES` is read; one byte more is refused; `parse` of `MAX_BYTES + 1` bytes is refused |
| 8 | `test_refuses_names_that_look_like_paths` | `name` as `../evil`, `a/b`, `a\\b`, `/abs`, `C:evil`, `.hidden`, `evil.toml`, empty, `Evil`, `a b`, `é`, 41 letters, the number 3, or left out: `name must be 1 to 40 lowercase letters, digits or underscores`. `a_1` and 40 letters are read. `check_name` raises for `../evil`, `a/b`, `Evil`, `a` with a line break, empty, 3 and None, and returns `a_1` |
| 9 | `test_refuses_a_wrong_version` | No `aelook` line, or `"1"`, `1.0`, `true`, `[1]`: ``no `aelook = 1` line: this is not an AutoEdit look file``; the same for another TOML file (`[build-system]`). 2, 0, -1 and a number of 300 digits: `this AutoEdit reads look file format 1`, the number not in the message; the same when the file also has an unknown key (the version is read first) |
| 10 | `test_refuses_a_bad_palette` | `shadow_hue = 361`, `mid_hue = -1`, `highlight_chroma = 0.16`, `shadow_chroma = -0.01`, `neutral_protection = 1.5`, a string, `1e308`: the range message with `in [palette]`. `strength` and `name`: unknown key. `protected` as a string, with a number, with `../x`, with nine names: `protected in [palette] must be a list of at most 8 concept names`. `palette = 3` and `[[palette]]`: `[palette] must be a table`. A palette at its limits (hue 360, chroma 0.15, `protected = ["blue sky", "snow"]`) is read: its name is the look's, and a value left out is the palette's default |
| 11 | `test_refuses_what_is_not_a_look_file` | An empty file; `[look` alone; the bytes FF FE 00 01 (`not a text file`); a zero byte; a key twice; `[` 8000 deep and `{a = ` 2000 deep (`not valid TOML`); no `[look]`, `look = 3`, `[[look]]` (`[look] must be a table`); `preserve_exposure = true` and `= false` (`preserve_exposure cannot be in a look file`); an author with a line break, with an escape character, with U+202E, a licence of 201 characters, an empty one, the number 3 (`must be one line of text`); `license` (unknown key); `habit = 3` inside `[look]`. A file with a byte order mark and CRLF is read. `read` raises exactly `mine.txt: a look file must end in .aelook`, `none.aelook: look file not found`, and the latter for a folder named `dir.aelook`; `UPPER.AELOOK` is read |
| 12 | `test_export_then_import_gives_the_same_look` | `looks export film_warm -o F --palette teal_orange --author "Jané Doe" --licence "CC BY 4.0"` into a folder `Client Folder`: 0, and the file does not hold that name. `looks import F --name mine --profile P`, P not existing yet: 0; it prints the author, the licence and `--look mine --palette mine`. In the merged files `mine` equals the packaged `film_warm`, and the palette `mine` equals `teal_orange` apart from its name; the profile's look table holds every name of `looks.FIELDS`. A profile look with a habit of two targets, exported and imported as `hab2`: the same look and the same habit (`looks.load_habits`), and the import says `habits`; `lookfile.export("hab2", None, P)` gives the text of the first file with the name changed |
| 13 | `test_refuses_a_habit_outside_the_instruction_limits` | Imports of `GOOD` plus a habit, each with exit code 2 and without creating the profile folder: `dehaze = 5` (`[limits]`), `exposure = -2` (`exposure must be from -0.75 to 0.75`), `saturation = 25.5` (`saturation must be from -25 to 25`), the place `nowhere`, a mark as target, the op `warp`, the scene `../x`, 13 targets (`at most 12`). `exposure = -0.75` on `sky` with `temp = 50` on `place:top` imports, and `load_habits` gives both back |
| 14 | `test_import_name_clashes` | A file named `natural` (`'natural' is a packaged look`) or `surprise` (`a word of --look`), and with a palette `teal_orange` (`a packaged palette`), `auto`, `none`, `off` (`a word of --palette`): exit code 2 with and without `--force` and when the name comes from `--name`; the message names `--name`; the profile folder is not created. A file named `teal_orange` without a palette imports. `PAL` a second time: 2, `the profile already has a look 'mine'`, `--force` named, the profile unchanged. `--name second` keeps both. A changed file with `--force` replaces look and palette and says `it replaced` for each; `second` and the packaged entries are as before. After `looks delete mine` the palette is still there: the import is 2 (`the profile already has a palette 'mine'`) and no look `mine` is written |
| 15 | `test_import_changes_only_the_profile` | A profile with a look `other` and a `defaults.toml`. An unknown key, the 10 MB file, `--name ../evil`, `--name a/b`, `--name Evil` and a missing file each return 2 and change no file under `tmp_path`; nothing named `evil` exists beside it. `PAL` imports with 0 and prints `comments in it are gone`; the only files that differ are the profile's `looks.toml` and `palettes.toml`; `other` is unchanged; the packaged configs keep their bytes. With `AUTOEDIT_PROFILE=""`, `XDG_CONFIG_HOME` and `APPDATA` pointed at a folder that does not exist, and no `--profile`: 2, `profiles are switched off`, nothing created (environment restored in `finally`) |
| 16 | `test_a_look_from_someone_else_stays_inside_the_limits` | A file with every field at its upper limit, and `bw = 0` so the photo keeps its colour, imports; every value of the look in the merged library lies inside `limits()` and `preserve_exposure` is false. `_scene` with the stub: under the default look the photo is not flagged; under the imported look the pixels are finite, inside 0..1 and different, `needs_review` is true, `quality_score < 0.8` (measured 0.58), a note starts with `oversaturation gate`, and `global_settings` holds only names of `ops.OP_BOUNDS` |
| 17 | `test_a_look_file_cannot_switch_off_the_darkness_check` | A file with `exposure_mul = 0.0` and `curve_add = 60.0`, imported as `mine`; then `looks save own --from mine --set preserve_exposure=true`. `_scene(dark=0.35)` with the stub: `mine` is flagged and its reason holds `dark source made darker` (measured: foreground median 0.18 to 0.12, score 0.75); `own` is not flagged. `looks export own` prints `not in the file: preserve_exposure` and the file does not hold the word; imported as `back` it has the flag false and equals `mine` |
| 18 | `test_export_refuses_what_import_would_refuse` | A profile `looks.toml` with `[wild] contrast_mul = 9`, a look named `"../x"` and a look `hab` with `habit.landscape.sky exposure = -2`. With `tmp_path` as the current folder (restored in `finally`), each exit code 2: `wild` (`contrast_mul in [look] must be a number from 0 to 3`), `nope` (`look 'nope' not found`), `../x` (`look name`), `hab` (`exposure must be from -0.75 to 0.75`), `--palette nope`, an author of 201 characters, a licence with a line break, `-o natural.txt` (`must end in .aelook`). No file was written, there or one folder up. `looks export natural` then writes `natural.aelook` there and says `wrote natural.aelook`; a second time writes over it |
| 19 | `test_default_output_is_unchanged` | One photo and a profile with an imported look and palette `mine`. Stub batches with one worker: without a profile and with the profile the export bytes are equal; with `look="mine", palette="mine"` they differ |
| 20 | `test_card_renders_with_the_stub` | `lookcard.render("film_faded", [folder], out, RunConfig(backend="stub"))` on three photos: `photos == 3`, `backend == "stub"`, `flagged` is a list of (number, text). `out` holds exactly `film_faded.html` and `film_faded.png`. The PNG is RGB, 996 by 1272, with an empty `info`. The page holds six inline JPEGs of 600 by 450, and each before differs from its after by more than 2 levels on average. The page without its pictures holds `<h1>film_faded</h1>`, `segmenter: stub` and `default-src 'none'`; neither it nor the bytes of the PNG hold `IMG_PRIVATE`, `Client_Folder`, the folder's path, `<script`, `http://`, `https://`, `dehaze` or `curve`. On the sheet each before box differs from its after box |
| 21 | `test_card_shows_what_the_look_carries` | Two photos; a profile with `[plain] curve_add = 5.0` and `[grainy]`, the same with `grain = 100`. `bw_classic`: every after box of the sheet has `max - min == 0` in every pixel, every before box more than 30 somewhere. `plain` against `grainy`: the before boxes are equal, the after boxes differ by more than 1 level on average. `plain` with `palette="teal_orange"` in the config: its first after box differs from the one without by more than 0.5 |
| 22 | `test_card_without_photos_shows_the_drawn_frame` | `sample_frame()` is 800 by 1200 by 3, float32, inside 0..1, and the same twice. With `Pipeline.build` wrapped to record its config (restored in `finally`): for every packaged look `render(name, [], out, RunConfig(backend="stub", decider="llm", allow_cloud=True))` gives `photos == 0` and `flagged == []`, and every recorded config has `decider == "rules"` and the look's name. The page holds two pictures, the sheet is 996 by 480, `out` holds two files per look |
| 23 | `test_card_takes_at_most_twelve_photos` | A folder of 30 small PNGs and a `notes.txt`: `pick([folder])` gives 12 different files in sorted order, the folder's first and last among them. A file named twice stays twice; `pick([]) == []`; a file and the folder together give 12. `notes.txt`, a missing file and an empty folder each raise `ValueError` |
| 24 | `test_card_cli` | `looks card film_warm PHOTO -o DIR --backend stub`: 0, and the output names both files and `segmenter: stub`. `looks card natural -o DIR --backend stub --palette teal_orange`: 0 and `drawn sample frame`; DIR holds four files. Exit code 2 and no new file for an unknown look (`look 'nope' not found`), a missing photo (`not a photo or a folder`) and a profile look named `"../../escaped"` (`look name`); nothing named `escaped` exists |

## Acceptance checks (the session runs these)

1. `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` are green, with 24
   tests more than before. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest.
2. With a scratch profile under `.tmp/` as `--profile`: `autoedit looks export film_faded -o .tmp/f28/film_faded.aelook
   --palette muted_film`, `autoedit looks import .tmp/f28/film_faded.aelook --name faded2`, then the smoke run with
   `--look film_faded --palette muted_film` and with `--look faded2 --palette faded2`: the same export bytes (measured).
3. Default output is byte-identical to the baseline renders (the reference photos and the smoke corpus, maximum
   difference 0), and a default sidecar differs in its timings only: no config file changed, so `config_digest` stays.
   No tone gate is due: no exposure, tone or decode step changes, and no default.
4. By hand against the scratch profile, each with exit code 2, its message, and a profile that keeps its bytes: a file
   of 10 MB, `name = "../../x"`, `contrast_mul = 1e9`, `curve_add = nan`, `aelook = 2`, `preserve_exposure = true`.
5. The cards, into `out-features/F28/` with `--backend stub`: `looks card film_faded -o out-features/F28/drawn` (the
   drawn frame), `looks card film_faded test-corpus -o out-features/F28/smoke`, and twelve photos of the local corpus,
   `looks card film_warm curated-dolomites-summer-2026/selected_jpeg -o out-features/F28/film_warm` (measured: 32 s).
   When no other SAM 3 job runs: four named photos through `.venv/bin/python -m autoedit.cli looks card ... --backend
   sam3`, its time into the "Result". Each PNG and page is opened in a browser: the pairs, the name and the segmenter
   line are there, no file name is, and the network panel shows no request. The Checker looks at them too.

## Known limits

- The numbers of a file are checked one by one. A sum can still leave an op's own bounds, as a profile look can today
  (fact 3): with every field at its upper limit `curve` reached 136 of 100 and `dehaze` 105; the pixels stayed valid and
  the review gate flagged 6 of 7 test photos. Holding what a look shapes inside the op bounds is proposed below.
- A palette is chosen by its own flag: `--look NAME` alone runs without it. `looks delete NAME` removes the look and
  leaves the palette; a second import then needs `--force`. Author and licence are not kept, and a file is not signed.
- A file with a field this version does not know is refused whole, so a look from a newer AutoEdit needs a newer
  AutoEdit. A look outside the limits, or with a habit op that has no limit, cannot be exported (the message names the
  field); one with `preserve_exposure` is exported without it. A habit's limits are those of the importing machine.
- A card needs a look of the library: a file is imported first, and removed with `looks delete` when the card
  disappoints. With the stub its regions are colour thresholds (the foot line says so); with SAM 3 every card segments
  its photos again, because a card folder keeps no mask cache.
- A card decodes each photo at full size before it shrinks it (1.7 GB for 22 megapixels), takes 12 photos at most, and
  its page is 0.7 MB per photo. The drawn frame shows tone and colour, not a face or a real sky.
- Found on the way, not changed: a profile `palettes.toml` is merged without a check; an import writes checked values.

## Proposed plan row

`| F28 | Look files: export, import, preview card | M | done (`autoedit looks export`, `import`, `card`; CLI only); which sample photos a card shows by default waits for the owner |`

- For the owner's table: the row "Sample photos for look cards (F28)" stays; its evidence is now the cards in
  `out-features/F28/`, its change the default in `lookcard.pick`. "May a look file carry `preserve_exposure`?" joins the
  row on moody looks (F8). New: "Should what a look shapes be held inside the op bounds?" (the first known limit; the
  change is a clamp at the end of `looks.shape`, with a byte check).
- Changed against the plan: a palette travels in the file and is still chosen with `--palette` (fact 5); a file never
  carries `preserve_exposure`, and a habit only inside the instruction limits; a card without photos shows a drawn
  frame, because the owner's decision on sample photos is open. Reverse each by its row in the decisions table.

## Result

Built on 2026-11-17 in the unattended run. Status: done (CLI only).

- **Built** by three Builders (Sonnet; T1 at high effort) from this spec, after F27.
- **The Checker (Opus, high)** found two things that had to be fixed, fixed by the session with
  tests: the packaged-name guard read only the active configs folder, so with `--configs-dir` or a
  stale `./configs` a file named `natural` or `warm_gold` went into the profile and replaced the
  packaged look or palette for later runs; it now checks the packaged folder too. And a habit value
  that is a 400-digit whole number crashed the import with `OverflowError`; `looks.check_habit` now
  compares against the op's bounds before any float conversion (infinity and NaN are still
  refused). Also done: test 13 uses the path-shaped scene `../x`; names from a file are cut to 40
  characters in habit errors. Notes kept: the file's own name is printed as given in errors; test
  14 checks the packaged entries by presence (test 15 checks their bytes); `--profile` can point at
  the configs folder itself (older, as `looks save`); an imported habit can name up to 12 concept
  words, each an extra SAM 3 prompt.
- **What held:** path-like names, control and bidi characters, type tricks, huge numbers, NaN and
  inf in `[look]` and `[palette]`, duplicate keys and wrong versions are refused, a 10 MB file in
  6 ms; every check runs before the first write; export then import gives byte-identical exports;
  the card's page has a strict content policy, no script, no file names; default output is
  byte-identical to the committed code.
- **Checks:** a card of `film_faded` on the two smoke photos with the stub in 0.5 s
  (`out-features/F28/card-stub/`). Not run: a card with SAM 3, and the owner's choice of sample
  photos.
