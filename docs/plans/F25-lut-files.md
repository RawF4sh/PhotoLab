# F25. LUT files

Status: spec (2026-11-17). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F25.
Written in an unattended run ([UNATTENDED_RUN.md](../UNATTENDED_RUN.md)): nothing was asked, and
the decisions that are the owner's are in the table below. Built on [F23](F23-grain.md) and
[F24](F24-bw-film-looks.md), which are built before it.

The design was built on two scratch copies of the tree, one with F23 and one with F24's code on
top; both are deleted again. The reference code below is that code and every number is measured
(stub backend). On both copies both suites passed, on the system Python and on `.venv`: 808
tests without F24 (the 779 of today and the 29 of this spec), 827 with it.

## Goal

A `.cube` LUT file, the colour filter format of photo and video programs, can be laid over every
photo of a run: `--lut film.cube`, with `--lut-amount` for its strength. The file comes from
somewhere else, so a strict reader checks it. The other way round, the whole-picture colour part
of a look can be written as a LUT for other programs: `autoedit looks lut NAME -o FILE.cube`.
Nothing is on by default, and default output stays byte-identical.

## What was found in the code

1. `edit/ops.py::apply_ops` renders more than the final picture (F23, fact 1): every region,
   layer and cell, the reference tuner on the proxy. Its bundle holds numbers only
   (`contract.Settings`): a table cannot ride in it.
2. After the global pass come the palette grade, a clip to 0..1, `gates.highlight_hold` and
   `gates.chroma_guard` (`edit/apply.py::apply_image`), then in `pipeline.py::process_one` a
   banding re-render, a reference match with a second hold and gate, and F24's block C. Last,
   `stats.compute` and `gates.assess` judge the finished picture for the review flag.
3. `gates.chroma_guard` holds each pixel's `max - min` to 1.30 times the source's plus 0.02. A
   table's colour does not pass it (Design 3).
4. The banding check reports a first "warn" only when nothing later changes the picture, and
   gives its final verdict only after a re-render or a reference match: two conditions on
   `cfg.reference` in `process_one`.
5. A typed delta is held inside the op's bounds (`instructions._summed`): for 0 to 100 it can
   add, not take away. `adjust.py::parse_set` refuses an op of `instructions.WHOLE_PHOTO_OPS` on
   a region, with a text that names grain.
6. `process_one` stores the run settings without `pipeline._NOT_STORED`, and the file names of
   `reference` and `recipe_model` as `audit.run_inputs`. `adjust.py::rerender` asks for each
   name in `run_inputs` again by its flag and compares the name.
   `tests/test_adjust.py::test_sidecar_holds_no_local_path` pins `run_inputs` to those two keys.
7. `Pipeline.build` runs once per run, per render worker and per API request. A `ValueError`
   there ends `autoedit run` with exit code 2 before a photo is read, and is a 400 in the API
   (`web/app.py::guard`); a `PermissionError` is a 403.
8. Every API request becomes a `RunConfig` in `web/service.py::make_cfg`. `web/app.py` checks
   photo and output paths with `service.ensure_allowed` (the real path lies in a folder picked
   in this session). `reference` and `recipe_model` pass `_ALLOWED` without that check.
9. A look multiplies what the rules decided for a photo and adds values that do not look at the
   photo. `looks.py::shape({}, look, 1.0, is_global=True)` returns exactly the adds, F24's
   `dehaze_add` included; F24 sets `bw` in the pipeline, as written. A negative `dehaze` is a
   per-pixel veil (F24, fact 4), though `ops.SPATIAL_OPS` lists `dehaze` whole.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Where does the file come from? | The run setting only: `--lut`, `[run] lut`, `lut` in an API request. No look field: a look is numbers and is meant to be shared (F28); a path in it would name a file on one machine | F28 can carry a table inside a look file |
| Where is the LUT applied? | On the finished picture, in `process_one`: after the gates, after a reference match and after F24's block C. `lut` is an op name and the step before `grain` in `ops.STEPS`, but `apply_ops` does not render it | Not meant to be reversed: Design 3 |
| May the oversaturation gate pull a LUT back? | No. It judges the edit and stands before the LUT. The banding verdict and the review check read the picture after it | Three lines after the block of Design 4: `gates.chroma_guard` once more, as after a reference match. Measured: it then takes the table's colour back on 33 to 66 % of the frame for three of five tables |
| How strong? | `lut_amount`, 0 to 100, default 100. Not scaled by `--intensity`, like `--grain` | The default in `contract.py`; `* cfg.intensity` in block A |
| Interpolation | Tetrahedral (Design 2) | The body of `ops.lut_map` |
| A black and white look with a LUT | The LUT lies over the black and white picture. A toning table gives a toned black and white, which F24 left as a feature of its own; a table with neutral greys leaves the photo neutral | Move the block above F24's block C: the photo then ends neutral |
| Keywords read | `TITLE`, `LUT_3D_SIZE`, `DOMAIN_MIN`, `DOMAIN_MAX`, and `LUT_3D_INPUT_RANGE`, the range line DaVinci Resolve writes (from the planner's memory of that format: nothing was downloaded to check). A 1D LUT is refused by name | `_KEYWORDS` in `lut.py` |
| Limits | 2 to 65 points, 16 MB. A table value from -1 to 2 is read and clamped to 0..1; one beyond refuses the file | The constants in `lut.py` |
| Who may set the op? | The run setting, and one photo's `--set global.lut=N` on top. No decider, no look, no region, no typed words | A `[verbs.*]` entry with `lut = N` in `instructions.toml` |
| `looks lut` | 33 points, the look's own numbers (a default run applies 0.8 of them). It writes over its `-o` file, which must end in `.cube` | `EXPORT_POINTS`; an `--intensity` flag |
| The safety workflow (`quality_runner.py`) | Unchanged: it judges a LUT as part of the edit and blends a picture it refuses toward the source | none intended |
| A control in Studio | None, and no file picker. The API takes `lut` (a file inside a picked folder) and `lut_amount` | The Studio rebuild |

## Design

### 1. The file (`lut.py`, new)

`src/autoedit/lut.py` reads and writes the format and holds the table. No new dependency. The
reader works on bytes. A line that starts with `#` is a comment of any bytes; `TITLE` is free
text that is not kept; everything else is a known keyword or three plain decimal numbers.
An error is a `ValueError` of the form `NAME: line N: rule`. It never holds the folder or a piece
of the file: over the API a wrong file must not be readable through its error.

```python
MAX_BYTES = 16 * 1024 * 1024   # a 65-point file with 10 digits per number is 10.7 MB
MAX_POINTS = 65
VALUE_LO, VALUE_HI = -1.0, 2.0  # a table value outside is refused; inside, it is clamped to 0..1
EXPORT_POINTS = 33
_NUM = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"     # no nan, inf, hex or underscore
_ROW = re.compile(rb"(%s)[ \t]+(%s)[ \t]+(%s)" % (_NUM, _NUM, _NUM))
_PAIR = re.compile(rb"(%s)[ \t]+(%s)" % (_NUM, _NUM))
_SIZE = re.compile(rb"[0-9]{1,3}")
_KEYWORDS = (b"TITLE", b"LUT_3D_SIZE", b"DOMAIN_MIN", b"DOMAIN_MAX", b"LUT_3D_INPUT_RANGE")


@dataclass(frozen=True, eq=False)
class Lut:
    table: np.ndarray                    # (n, n, n, 3) float32 in 0..1, indexed [blue, green, red]; read-only
    lo: tuple[float, float, float] = (0.0, 0.0, 0.0)    # DOMAIN_MIN
    hi: tuple[float, float, float] = (1.0, 1.0, 1.0)    # DOMAIN_MAX
    name: str = ""                       # the file's name without its folder
    digest: str = ""                     # blake2b-8 of the file's bytes

    @property
    def points(self) -> int:
        return self.table.shape[0]

    @functools.cached_property
    def shift(self) -> np.ndarray:
        """The table as offsets: each entry minus the colour of its own grid point. What
        `ops.lut_map` interpolates; all zero for a table that changes nothing."""
        return self.table - identity(self.points, self.lo, self.hi)


def parse_cube(data: bytes, name: str = "LUT") -> Lut:
    """The LUT in the bytes of a `.cube` file, or a ValueError that says which rule broke."""
    def bad(why: str, line: int | None = None) -> ValueError:
        return ValueError(f"{name}: " + (f"line {line}: " if line else "") + why)

    if len(data) > MAX_BYTES:
        raise bad(f"larger than {MAX_BYTES // (1024 * 1024)} MB, not read")
    if b"\0" in data:
        raise bad("not a text file")
    n, seen, rows = 0, set(), []
    lo, hi = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    for k, line in enumerate(body.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"), start=1):
        line = line.strip(b" \t")
        if not line or line.startswith(b"#"):
            continue
        if line[:1] in b"0123456789+-.":                       # a table row
            m = _ROW.fullmatch(line)
            if m is None:
                raise bad("a table row must be three plain numbers", k)
            if not n:
                raise bad("a table row before LUT_3D_SIZE", k)
            if len(rows) == n ** 3:
                raise bad(f"more table rows than LUT_3D_SIZE {n} allows ({n ** 3})", k)
            r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if not (VALUE_LO <= r <= VALUE_HI and VALUE_LO <= g <= VALUE_HI and VALUE_LO <= b <= VALUE_HI):
                raise bad(f"a value outside {VALUE_LO:g} to {VALUE_HI:g}; this is not a LUT for display colours", k)
            rows.append((r, g, b))
            continue
        word, _, rest = line.replace(b"\t", b" ").partition(b" ")
        rest = rest.strip(b" ")
        if word == b"LUT_1D_SIZE":
            raise bad("this file holds a 1D LUT; only 3D LUTs are read", k)
        if word not in _KEYWORDS:
            raise bad("not a keyword this reader knows (" + b", ".join(_KEYWORDS).decode() + ")", k)
        if rows:
            raise bad("a keyword after the table began", k)
        if word in seen:
            raise bad(f"{word.decode()} is given twice", k)
        seen.add(word)
        if word == b"TITLE":
            continue                                           # free text: not used and not kept
        if word == b"LUT_3D_SIZE":
            n = int(rest) if _SIZE.fullmatch(rest) else 0
            if not 2 <= n <= MAX_POINTS:
                raise bad(f"LUT_3D_SIZE must be a whole number from 2 to {MAX_POINTS}", k)
            continue
        m = (_PAIR if word == b"LUT_3D_INPUT_RANGE" else _ROW).fullmatch(rest)
        if m is None:
            raise bad(f"{word.decode()} needs {'two' if word == b'LUT_3D_INPUT_RANGE' else 'three'} plain numbers", k)
        nums = tuple(float(v) for v in m.groups())
        if word == b"LUT_3D_INPUT_RANGE":
            lo, hi = (nums[0],) * 3, (nums[1],) * 3
        elif word == b"DOMAIN_MIN":
            lo = nums
        else:
            hi = nums
    if not n:
        raise bad("no LUT_3D_SIZE line; this is not a 3D LUT")
    if not all(abs(v) <= 1e6 for v in (*lo, *hi)) or not all(b - a >= 1e-6 for a, b in zip(lo, hi)):
        raise bad("the input range must be finite, and its end above its start in every channel")
    if len(rows) != n ** 3:
        raise bad(f"the table has {len(rows)} rows; LUT_3D_SIZE {n} needs {n ** 3}")
    # File order: red changes fastest, then green, then blue. So the array is indexed [b, g, r].
    table = np.clip(np.asarray(rows, dtype=np.float32), 0.0, 1.0).reshape(n, n, n, 3)
    table.setflags(write=False)
    return Lut(table, lo, hi, name, hashlib.blake2b(data, digest_size=8).hexdigest())


def identity(n: int = EXPORT_POINTS, lo=(0.0, 0.0, 0.0), hi=(1.0, 1.0, 1.0)) -> np.ndarray:
    """The (n, n, n, 3) table that changes nothing over the input range `lo`..`hi`."""
    r, g, b = (np.linspace(lo[c], hi[c], n, dtype=np.float32) for c in range(3))
    bb, gg, rr = np.meshgrid(b, g, r, indexing="ij")
    return np.stack([rr, gg, bb], axis=-1)
```

Three more functions, described:

- `read_cube(path) -> Lut`. With `name = os.path.basename(path)`: the name must end in `.cube`
  in any case (`NAME: a LUT file must end in .cube`); `os.path.isfile(path)` must hold
  (`NAME: LUT file not found`); it reads `MAX_BYTES + 1` bytes in binary mode, turns an
  `OSError` into `NAME: cannot be read (...)` and returns `parse_cube(data, name)`.
- `cube_text(table, title, comments=()) -> str`. It refuses a table that is not `(n, n, n, 3)`
  with n from 2 to 65. One `# ` line per comment, then `TITLE "..."`, `LUT_3D_SIZE n`,
  `DOMAIN_MIN 0.0 0.0 0.0`, `DOMAIN_MAX 1.0 1.0 1.0`, then the rows of `table.reshape(-1, 3)`
  as `f"{r:.6f} {g:.6f} {b:.6f}"`, clipped to 0..1, and a last newline. Title and comments keep
  printable ASCII without `"`; the title at most 80 characters.
- `write_cube(path, table, title, comments=())`. It refuses a path that does not end in `.cube`,
  writes ASCII with LF to `path + ".part"` and swaps it in with `os.replace`.

The number pattern cannot be made slow: a hostile line of 16 MB is refused in 1.2 s at most.
Six decimals hold the grid values of 17, 33 and 65 points exactly. Reading takes 9 ms for 17
points, 57 ms for 33 (0.97 MB) and 0.45 s for 65 (7.4 MB).

### 2. The op (`edit/ops.py`)

Protected file. Add below `grain`:

```python
def lut_map(rgb: np.ndarray, shift: np.ndarray, amt: float,
            lo=(0.0, 0.0, 0.0), hi=(1.0, 1.0, 1.0)) -> np.ndarray:
    """A 3D LUT (F25): every colour is moved as the table says, by `amt` of 100.
    (Docstring: the four points below, in the words of this spec.)"""
    a = min(max(float(amt), 0.0), 100.0) / 100.0
    if a == 0.0:
        return rgb
    n = shift.shape[0]
    flat = shift.reshape(-1, 3)
    lo = np.asarray(lo, dtype=np.float32)
    hi = np.asarray(hi, dtype=np.float32)
    x = (np.clip(rgb, lo, hi) - lo) * (np.float32(n - 1) / (hi - lo))
    cell = np.clip(x.astype(np.int32), 0, n - 2)              # the last cell ends at n - 1
    f = x - cell.astype(np.float32)                           # position inside the cell, 0..1
    one, row, plane = np.int32(1), np.int32(n), np.int32(n * n)   # table steps along red, green, blue
    base = cell[..., 0] + row * cell[..., 1] + plane * cell[..., 2]
    del x, cell
    fr, fg, fb = f[..., 0], f[..., 1], f[..., 2]
    big = np.maximum(np.maximum(fr, fg), fb)
    small = np.minimum(np.minimum(fr, fg), fb)
    mid = np.maximum(np.minimum(fr, fg), np.minimum(np.maximum(fr, fg), fb))   # the median, exact
    step_big = np.where(fr >= big, one, np.where(fg >= big, row, plane))
    step_small = np.where(fb <= small, plane, np.where(fg <= small, row, one))
    far = one + row + plane
    d = np.take(flat, base, axis=0) * (np.float32(1.0) - big)[..., None]
    d += np.take(flat, base + step_big, axis=0) * (big - mid)[..., None]
    d += np.take(flat, base + (far - step_small), axis=0) * (mid - small)[..., None]
    d += np.take(flat, base + far, axis=0) * small[..., None]
    if a < 1.0:
        d *= np.float32(a)
    return (rgb + d).astype(np.float32, copy=False)
```

- **Input.** `shift` is the table as offsets (`Lut.shift`), `(n, n, n, 3)` indexed
  `[blue, green, red]`; `lo` and `hi` are the file's input range. The values are the working
  values of the module, sRGB-encoded, as LUT files for display pictures expect.
- **Offsets.** The result is the input plus `amt` of the offset found for it. A table that
  changes nothing has offsets of exactly 0, so the picture comes back bit for bit. A value
  outside the range is looked up at its edge and keeps its distance from it.
- **Tetrahedral.** The cell around a colour is cut into six tetrahedra along its grey diagonal;
  the offset is mixed from the four corners of the one the colour lies in: the cell's origin,
  the far corner, and the two reached by stepping along the axis of the largest fraction and
  then the middle one. Against a trilinear version in the same style: on a grey ramp under a
  saturation table of 1.5, trilinear adds colour (0.64 of 255 at 33 points, 1.28 at 17) and
  tetrahedral none; and it reads four table entries per pixel, not eight (2.1 s against 4.5 s
  on 24 megapixels, one thread).
- **Per pixel**, so it runs on row strips; 0 is the identity. Not called by `apply_ops`: a LUT
  is laid over the finished picture once (Design 3).

Registrations: `"lut"` in `OP_NAMES` after `"denoise", "sharpen",` with the comment `# a LUT file
(F25): how much of it is laid over the finished picture, by the pipeline`; `"lut": (0.0, 100.0)`
in `OP_BOUNDS` after `"sharpen"`; a step `("lut",)` in `STEPS` between `("sharpen",)` and
`("grain",)`. Not in `SPATIAL_OPS`. The comment above `STEPS` now says: the last two steps are
values in the bundle that `apply_ops` does not render; the pipeline lays the LUT over the finished
picture, after the gates, and `emit.py` adds the grain to the picture it writes. The docstring of
`apply_ops` gets the same in one sentence; its code does not change.

Measured: tables built from a formula at 33 points give a channel swap exactly, `(r², g^1.5, b)`
within 0.21 of 255 and a half desaturation exactly. 1.64 s on 22 megapixels on one thread, 0.33 s
on ten.

### 3. Where the LUT is applied, and the gates

Three places for the gate were measured. 14 photos (the first 12 of `selected_jpeg` in the Dolomites corpus at
1600 px, the two of the smoke corpus), five tables made from formulas: a sepia tone, a saturation
of 1.5, a teal and orange split, a fade, a channel swap. A default run pulls chroma back on 7 %
of the frame on average (0 to 33 %) and flags 2 of the 14.

| Where the oversaturation gate stands | Sepia | Saturation 1.5 | Teal and orange |
|---|---|---|---|
| After the LUT, against the source: share of the frame pulled back (largest), mean change of the picture in levels | 39 % (100 %), 1.2 | 66 % (94 %), 2.2 | 33 % (77 %), 1.1 |
| After the LUT, against the source as the LUT shows it (the LUT as a step after the colour steps) | 9 % (48 %), 0.13 | 13 % (53 %), 0.13 | 14 % (52 %), 0.28 |
| Before the LUT (chosen) | as the default run: the table is not touched | the same | the same |

- Against the source, the gate takes the table's colour away. On a grey photo a sepia tone is
  cut to the gate's allowance of 0.02.
- Against the source as the LUT shows it, the gate is confused by brightness. A table's colour
  depends on how bright a pixel is, the edit changes that, and the gate reads the difference as
  added colour: 30 % of one frame, where the default run pulls back nothing.
- So the LUT is laid over the finished picture. The gate, the highlight hold, the palette grade
  and a reference match do to the edit exactly what they do without a LUT.

What follows, and tests hold it:

- **A run with a LUT is the run without it, with the LUT over its picture.** `process_one` with
  a LUT equals `ops.lut_map` of the result without, `array_equal`, on all 14 photos and all five
  tables. With an identity file the exports are byte-identical.
- **The checks read the picture after the LUT.** The final banding verdict runs when a LUT is on,
  and `stats.compute` with `gates.assess` judges the result against the source. Flagged for
  review, of 14: sepia 3, saturation 6, teal and orange 5, fade 0, swap 2 (`saturation rose`,
  `RGB highlight clipping increased`).
- The banding gate repairs what the edit did, before the LUT. Banding that a table adds is
  reported and marks the photo for review; the table is not weakened.
- Sharpening and noise reduction come before the LUT, grain after it.

### 4. From the setting to the picture

`contract.py`, after `grain_size`:

```python
    lut: str | None = None  # path of a .cube 3D LUT laid over the finished photo (F25); None or "" = none
    lut_amount: float = Field(default=100.0, ge=0.0, le=100.0)  # how much of the LUT is blended in; not scaled by intensity
```

`pipeline.py`: import `lut as lut_mod`; `"lut"` joins `_NOT_STORED`; a last field
`lut: lut_mod.Lut | None = None` on `Pipeline`. In `build`, directly after `par.set_threads(...)`:
`lut = lut_mod.read_cube(cfg.lut) if cfg.lut else None`, passed on as `lut=lut`. A broken file
stops the run there (fact 7), before a model is loaded.

Block A, directly after F24's block A (before the comment "Free-text look intent"):

```python
        # A LUT (F25) is asked for by the run setting, never by a decider or a look. Here it is a
        # value in the global settings and no pixel: it is laid over the finished picture below.
        for s in (global_settings, *region_settings.values()):
            s.pop("lut", None)
        if self.lut is not None and cfg.lut_amount > 0.0:
            global_settings["lut"] = cfg.lut_amount
```

Block B, directly after F24's block B (before the comment "Shoot-consistency"):

```python
        # The LUT amount (F25): the run setting plus what this photo's own --set asked for, inside
        # 0..100. A LUT is for the whole photo, and without a LUT file there is nothing to blend.
        lut_amount = min(max(global_settings.pop("lut", 0.0), 0.0), ops.OP_BOUNDS["lut"][1])
        for rs in region_settings.values():
            rs.pop("lut", None)
        if lut_amount > 0.0 and self.lut is None:
            policy_notes.append("lut: this run has no LUT file, the amount is ignored")
        elif lut_amount > 0.0:
            global_settings["lut"] = lut_amount
            policy_notes.append(
                f"lut: {self.lut.name} ({self.lut.points} points, digest {self.lut.digest}), {lut_amount:g} of 100, "
                "laid over the finished picture after the oversaturation gate; the review check reads the result")
```

The block that lays it, directly after F24's block C (before the comment "Final banding verdict"):

```python
        # A LUT file (F25) is laid over the finished picture: after the gates and after a reference
        # match, which judged and shaped the edit, not the LUT. The banding verdict and the review
        # check below read the result.
        if do_apply and out_rgb is not None and global_settings.get("lut", 0.0):
            shift = self.lut.shift      # made on first use, here and not in a strip thread
            out_rgb = par.rows(lambda a: ops.lut_map(a, shift, global_settings["lut"], self.lut.lo, self.lut.hi),
                               out_rgb)
```

Two conditions of the banding check (fact 4) count a LUT as something that still changes the
picture: `elif baction == "warn" and not cfg.reference:` becomes
`... and not (cfg.reference or global_settings.get("lut")):`, and the final verdict runs on
`(band_remediated or cfg.reference or global_settings.get("lut"))`.

The sidecar, directly after the `result.audit.update(run_config=...)` call. A run without a LUT
gets no new key there, so the test of fact 6 stays as it is:

```python
        if self.lut is not None:   # the file's name and digest, never its folder; `adjust` asks for the file again
            result.audit["run_inputs"]["lut"] = self.lut.name
            result.audit["lut"] = {"name": self.lut.name, "digest": self.lut.digest, "points": self.lut.points}
```

`instructions.py`: `"lut"` joins `WHOLE_PHOTO_OPS`. `adjust.py`:

- `parse_set` builds its message from the op: `f"--set {text!r}: {op} is for the whole photo, use
  global.{op}"`. For grain the text is the same as today.
- `rerender` gains a last parameter `lut: str | None = None`, and `given` gains `"lut": lut`.
  The loop over `run_inputs` then asks for `--lut NAME` and refuses another name (fact 6).
- Before `result.notes += [f"re-render: {w}" ...]`: when
  `(old.audit.get("lut") or {}).get("digest") != (result.audit.get("lut") or {}).get("digest")`,
  append the warning `the LUT file differs from the one the batch used; the re-render may differ
  from the batch output`.

`web/service.py`: `"lut", "lut_amount"` join `_ALLOWED`. In `make_cfg`, first lines after
`kw = paths.config_paths(...)`:

```python
    if opts.get("lut"):     # a file path from a request: inside a folder the user picked, like every photo
        ensure_allowed(str(opts["lut"]))
```

That is the trust boundary for the API, at the one place every request passes (fact 8). A path
outside the picked folders, through `..` or through a link, is a 403 before anything is opened,
also when the file does not exist. Inside a picked folder the reader decides (400). On the command
line the path is the user's own, like `--reference`; the reader's rules are the same.

### 5. A look as a LUT

`lut.py`, after the writer:

```python
_LEFT_OUT = ("Not in this file: edits of single regions (sky, skin, ...); everything the rules decide from each",
             "photo (exposure, contrast, white balance) and what the look multiplies of it; and the steps that",
             "read neighbouring pixels (clarity, haze removal, sharpening, noise reduction, grain).",
             "For sRGB pictures: values in and out are sRGB-encoded, 0 to 1.")


def look_settings(look) -> dict[str, float]:
    """What a look adds to every photo, whatever the photo: its global adds at intensity 1, as a
    settings bundle of per-pixel ops. A LUT sees one colour at a time, so an op that reads other
    pixels (`ops.SPATIAL_OPS`) is left out; a negative dehaze is kept, it is a per-pixel veil."""
    from . import looks as looks_mod
    from .edit import ops

    s: dict[str, float] = {}
    looks_mod.shape(s, look, 1.0, is_global=True)
    if getattr(look, "bw", 0.0) > 0.0:       # F24: taken as written, as block A of pipeline.py does
        s.update({k: getattr(look, k) for k in ops.BW_KEYS if getattr(look, k)})
    return {k: float(v) for k, v in s.items()
            if v and (k not in ops.SPATIAL_OPS or (k == "dehaze" and v < 0.0))}


def look_cube(name: str, look, points: int = EXPORT_POINTS):
    """(table, comment lines) of the whole-picture colour part of a look: the lattice of every
    colour the table holds, rendered through `apply_ops` with `look_settings`."""
    from .edit import ops

    settings = look_settings(look)
    grid = identity(points).reshape(points * points, points, 3)
    table = np.clip(ops.apply_ops(grid, settings), 0.0, 1.0).astype(np.float32)
    listed = ", ".join(f"{k} {v:+g}" for k, v in settings.items()) or "nothing (this look only scales what the rules decide)"
    comments = (f"AutoEdit look {name}. The whole-picture colour part only:", f"  {listed}", *_LEFT_OUT)
    return table.reshape(points, points, points, 3), comments
```

In the file: the look's adds (`vibrance_add`, `saturation_add`, `curve_add`, the four `grade_*`,
F24's negative `dehaze_add` and its `bw` with the six weights), in the order of `apply_ops`. Not
in it, because a LUT maps one colour and knows no place and no neighbour: region edits; whatever
the rules decide per photo, and so the look's multipliers; `clarity`, `sharpen`, `denoise`,
`grain`, the black and white point and a positive `dehaze`, which read the frame or the pixels
around. The file says so in its first lines.

`cli.py`: a parser `lut` next to `list`, `save` and `delete` (also in the tuple that gives each
of them `--profile`), with `name` and `-o/--out` (required, `metavar="FILE.cube"`). In
`_cmd_looks`, a branch before the comment `# save`: an unknown name prints `look NAME not found
(known: ...)` as `save` does and returns 2. Then
`table, comments = lut_mod.look_cube(name, merged[name])`,
`lut_mod.write_cube(args.out, table, f"AutoEdit {name}", comments)`, and three printed lines:
`wrote OUT: the whole-picture colour part of look NAME, 33 points`, the settings
(`comments[1].strip()`), and `not in the file: region edits, what the rules decide per photo,
clarity, sharpening, grain`. The function's `except (ValueError, OSError)` gives exit code 2.

Measured for the 13 looks (five of today, eight of F24), the file against `apply_ops` with the
same settings: on colours from 0.2 to 0.8 within 0.58 of 255 (0.017 on average); on 12 photos
within 2.4 (0.07 on average). The largest differences sit where a look clips a channel at white:
a table cannot follow that corner between two of its points. A file is 0.97 MB.

## Config keys

- `RunConfig`: `lut` and `lut_amount` (Design 4). No key in `rules.toml`, `looks.toml`,
  `instructions.toml` or `lessons.toml`, so `config_digest` does not change.
- `cli.py`: both keys in the `[run]` list of `_flatten` and in `overrides`. `--lut` ("a .cube 3D
  LUT file laid over the finished photo") and `--lut-amount` (`dest="lut_amount"`, `type=float`,
  "how much of the LUT is blended in, 0 to 100 (default 100). Not scaled by --intensity").
  `adjust` gets `--lut` ("the LUT file, if the run used one"), passed to `rerender`.
- `defaults.toml`, under `[run]`: `lut_amount = 100.0` and a commented `# lut = "film.cube"`.
- `settings.toml`, at the end of the Look group, both with `group = "look"`: `[setting.lut]`,
  label "LUT file", help "A .cube colour filter file laid over the finished photo. Empty uses
  none."; `[setting.lut_amount]`, label "LUT strength", help "How much of the LUT file is blended
  in, 0 to 100." The schema wants every `RunConfig` field there (`settings_schema.schema`).

## Files

| File | Change | Task |
|---|---|---|
| `src/autoedit/lut.py` | new: Design 1 (T1); `_LEFT_OUT`, `look_settings`, `look_cube` (T3) | T1, T3 |
| `src/autoedit/edit/ops.py` | `lut_map`, the three registrations, the comment above `STEPS`, one docstring sentence | T1 |
| `tests/test_parallel.py`, `tests/test_grain.py` | the two changed asserts (see Tests) | T1 |
| `tests/test_lut.py` | new: tests 1 to 13 (T1), 14 to 26 (T2), 27 to 29 (T3) | T1 to T3 |
| `src/autoedit/contract.py` | the two run settings | T2 |
| `src/autoedit/pipeline.py` | import, `_NOT_STORED`, the field, two lines in `build`, blocks A and B, the block that lays the LUT, two conditions, the sidecar lines | T2 |
| `src/autoedit/instructions.py`, `src/autoedit/adjust.py` | one name; the message, `rerender(lut=)`, the warning | T2 |
| `src/autoedit/web/service.py` | `_ALLOWED`, two lines in `make_cfg` | T2 |
| `src/autoedit/cli.py` | the run flags, `adjust --lut` (T2); `looks lut` (T3) | T2, T3 |
| `src/autoedit/configs/defaults.toml`, `src/autoedit/configs/settings.toml`, `tests/snapshots/settings_schema.json` | keys, words, regenerated | T2 |

F23 pieces this relies on: `("grain",)` as the last step of `STEPS` and the comment above it,
`instructions.WHOLE_PHOTO_OPS` with its check in `parse_set`, the two grain blocks of
`process_one`. F24 pieces: its blocks A, B and C in `process_one` (ours follow them),
`ops.BW_KEYS`, the look fields `bw*` and `dehaze_add`. Not touched: `edit/apply.py`,
`edit/gates.py`, `edit/banding.py`, `looks.py`, `looks.toml`, `tests/test_bw.py`.

## Tasks

One after the other: each task needs the one before it. `.venv` has no pytest; there the proving
command is `tests/run_all.py`.

| Task | Tier | Content | Proving command |
|---|---|---|---|
| T1 | Builder, Sonnet, high (protected file) | Design 1 and 2; tests 1 to 13; the two changed asserts | `PYTHONPATH=src python3 -m pytest tests/test_lut.py tests/test_parallel.py tests/test_grain.py tests/test_bw.py tests/test_ops.py tests/test_ops_phase2.py tests/test_levels.py tests/test_denoise.py tests/test_llm_refresh.py -q` |
| T2 | Builder, Sonnet, high (`pipeline.py`) | Design 4; "Config keys"; tests 14 to 26; the snapshot (`AUTOEDIT_UPDATE_SNAPSHOTS=1 PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py -q`) | `PYTHONPATH=src python3 -m pytest tests/test_lut.py tests/test_pipeline.py tests/test_adjust.py tests/test_web_service.py tests/test_security.py tests/test_api_contract.py tests/test_settings_schema.py tests/test_cli.py tests/test_grain.py tests/test_bw.py tests/test_instructions.py tests/test_follow_up.py tests/test_quality_runner.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` |
| T3 | Builder, Sonnet, medium | Design 5; tests 27 to 29 | `PYTHONPATH=src python3 -m pytest tests/test_lut.py tests/test_cli.py tests/test_profile.py tests/test_bw.py -q`, then `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py` |
| Check | Checker, Opus, high | The diff against this spec (`ops.py` and `pipeline.py` are protected). With care: no error of the reader holds a piece of the file, the check in `make_cfg`, the place of the block that lays the LUT | none |
| Docs | The session, inline | `ARCHITECTURE.md` section 8 and `APP_GUIDE.md`: `→ sharpen → (lut) → (grain)`, with "laid over the finished picture after the gates; `apply_ops` does not render it"; `README.md`: one example with `--lut` and one with `looks lut`; `SECURITY.md`: a LUT path in an API request must lie in a picked folder; `CHANGELOG.md`; the plan row | none |

## Tests

`tests/test_lut.py`, without `pytest.mark` (`tests/run_all.py` has none). Tables come from
formulas, so no file is downloaded: `_table(fn, n=33)` is `fn` of `lut.identity(n)` in float64,
clipped to 0..1, as float32; `_lut(fn)` is `lut.Lut(_table(fn))`; `_cube(folder, name, fn)` writes
it with `write_cube`. With `l` the Rec.709 luma: `_swap` gives `(b, g, r)`, `_curves`
`(r**2, g**1.5, b)`, `_half_sat` and `_sat` `l + (x - l) * 0.5` and `* 1.5`, `_sepia`
`(l**0.9, l, l**1.25)`, `_bright` `min(1, 1.6 * x)`, `_steps` `floor(4 * x) / 4`. Frames are
seeded random float32 in 0..1. Photos are written by the test (128 by 128, sky over grass with a
little noise). "Levels" are of 255; `shift` is `Lut.shift`.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_reads_a_plain_file` | A 3-point file with a UTF-8 mark, CRLF, a comment, a blank line, `TITLE`, both `DOMAIN` lines, tabs: shape `(3, 3, 3, 3)`, float32, read-only; `table[b, g, r]` is row `r + 3 g + 9 b`; `name` is the file name, `points` 3, `digest` 16 hex characters and another one after one byte changes |
| 2 | `test_accepts_what_real_files_hold` | A comment of 400 characters with the bytes 0xA9 and 0x85, a `TITLE` without quotes, `LUT_3D_INPUT_RANGE 0.0 1.0`, CR line ends, no last newline, the numbers `+0.5 .5 1.` and `1e-3 1E0 5e-1`: read. `-0.01` and `1.02` come back as 0 and 1. A `TITLE` of 5000 characters and `DOMAIN_MAX 2 2 4`: read, `hi == (2.0, 2.0, 4.0)` |
| 3 | `test_every_broken_file_has_its_own_message` | The list below. Every message starts with `bad.cube: `, holds its part, and holds neither the folder's name nor the word `SECRET123` that the files carry in a comment, a title, a keyword and a row. Two files check `line 2: ` and `line 12: ` |
| 4 | `test_size_limit_ending_and_missing_file` | With `lut.MAX_BYTES` set one below the file's size: "larger than". `MAX_BYTES` is 16 MB, `MAX_POINTS` 65. A `.txt`: "must end in .cube". A missing file and a folder named `x.cube`: "not found". No message holds the folder. A 65-point file reads |
| 5 | `test_writer_round_trips` | Written and read back: the table within 1e-6; the text is ASCII with LF, starts with the comment lines and `TITLE`, has `6 + 33**3` lines; the title `A "quoted" títle` becomes `A quoted ttle`; no `.part` file is left; a `.txt` path and a table of another shape raise `ValueError` |
| 6 | `test_zero_is_the_identity` | `ops.lut_map(x, shift, 0) is x`, also for -5 |
| 7 | `test_identity_lut_is_a_no_op` | A frame with values from -0.3 to 1.3. For 2, 3, 17, 32, 33, 64 and 65 points: `Lut(identity(n)).shift` is all zero and the result is `array_equal` to the input, at 100 and at 37. Read back from a written file: `array_equal` for 17, 33 and 65 points, within 1e-6 for 32 |
| 8 | `test_known_luts_within_one_level` | `_swap` within 1e-6 of `x[..., ::-1]`; `_curves` and `_half_sat` within 1 level of the formula in float64 (measured 0.21 and 0.0) |
| 9 | `test_grey_stays_grey` | A grey ramp of 4001 values through `_sat` at 17 and at 33 points: `max - min` at most 1e-6, and the ramp itself within 1e-6 |
| 10 | `test_amount_blends` | With `_swap`: 50 is the mean of input and full result, 25 a quarter of the way, both within 1e-6; 250 equals 100 |
| 11 | `test_domain_and_values_outside` | `Lut(_table(_curves), hi=(2, 2, 2))`: the result is `_curves(x / 2)` within 1 level. The pixel `(-0.5, 0.5, 1.7)` through `_swap` is `(0.5, 0.5, 0.7)`: looked up at the edge, the distance kept. Frames of 1 by 1, 3 by 5, 301 by 127 at 80: same shape, float32, finite, inside 0..1 |
| 12 | `test_strips_are_bit_identical` | 700 by 900 through `_sat` at 80: one call equals `par.rows` over the same call with four threads and `par.MIN_PIXELS = 0`, more than one strip |
| 13 | `test_registered_as_a_finishing_step_that_apply_ops_does_not_render` | In `OP_NAMES`, bounds (0, 100); not in `SPATIAL_OPS`, `_COLOUR_KEYS` or `_TONE_KEYS`; the step sits between `("sharpen",)` and `("grain",)`. `apply_ops(x, {"lut": 100.0}) is x`; a bundle with `sharpen` and `lut` equals the one with `sharpen`; `apply_image` with `lut` in a region and in the global settings equals the call without |
| 14 | `test_default_is_off_and_byte_identical` | `RunConfig().lut is None`, `lut_amount == 100.0`. A stub batch with `lut=""` and `lut_amount=40` writes the bytes of a default batch. The default sidecar: no `lut` in `global_settings`, in `audit` or in `audit.run_config`; `run_inputs` is the two keys of fact 6; `run_config.lut_amount` is 100; no note that starts with "lut" |
| 15 | `test_identity_lut_run_is_byte_identical` | A batch with an identity file in a folder named `my luts`: the exports have the bytes of the default batch. The sidecar: `global_settings.lut == 100`, `run_inputs.lut == "identity.cube"`, `audit.lut` with the name, 33 points and a digest of 16 characters, no `lut` in `run_config`, neither the folder nor `my luts` in its text, one note that starts with `lut: identity.cube (33 points, digest `, no `lut` in a mask or a layer; `post_edit_stats`, `quality_score`, `needs_review` and `warnings` are those of the default sidecar |
| 16 | `test_a_lut_run_is_the_default_picture_with_the_lut_over_it` | `process_one` with `_swap` is `array_equal` to `ops.lut_map` of the default result; the settings without `lut` and the region settings are the default's. At `lut_amount=50` the same with 50. At 0: the default pixels, no `lut` setting, `run_inputs.lut` still set. `do_apply=False`: no pixels, `lut == 100`. With `look="bw_classic"` (F24) and `_sepia`: the look alone is neutral, the result is `lut_map` of it and has a mean `max - min` above 0.05 |
| 17 | `test_the_reference_match_comes_before_the_lut` | With `reference=` a second photo: the result with `_swap` is `array_equal` to `lut_map` of the result without, and a note starts with "reference match" |
| 18 | `test_a_broken_file_stops_the_run` | `Pipeline.build` with a file of seven rows raises `ValueError` that starts with `bad.cube: ` and holds no folder. `cli.main(["run", ...])` with that file, with a missing file and with a photo as `--lut` returns 2 and writes no export and no sidecar |
| 19 | `test_a_decider_cannot_set_it` | A decider that adds `lut: 80` to the global and the region settings: no `lut` anywhere without a file; with a file and `lut_amount=30` the value is 30 and no region has one |
| 20 | `test_the_review_gate_reads_the_picture_after_the_lut` | `_bright`: `post_edit_stats.mean_lum` is more than 0.05 above the default's; the default is not flagged, the run is, and its reason holds "clipping" |
| 21 | `test_the_oversaturation_gate_cannot_pull_a_lut_back` | A grey photo with `_sepia`: mean `max - min` above 0.08 (default below 0.03; the gate's own limit there is 0.02); the notes that start with "oversaturation gate" are those of the default run; one note holds "after the oversaturation gate" |
| 22 | `test_the_banding_verdict_reads_the_picture_after_the_lut` | A PNG of 384 by 256, a ramp from 0.35 to 0.75 across with noise of 0.004, and `_steps`: the default has no banding warning and no flag; the run has a warning with "smooth-region banding" (measured 0.27, "reduce") and is flagged with "banding" in the reason |
| 23 | `test_cli_and_api_carry_the_setting` | `--lut F --lut-amount 60`, `[run]` in a `--config` file and `service.make_cfg` (after `approve_root`) each set both; without them `lut` is None; `lut_amount` 150 and -1 raise `ValueError` |
| 24 | `test_api_path_must_be_approved` | With no folder picked `make_cfg({"lut": f})` raises `PermissionError`. After `approve_root(folder)` it passes; `""` and `None` pass as no LUT. A file in another folder, a link in the folder to that file, a path through `..` and a path that does not exist outside: `PermissionError` from `make_cfg` and from `service.preview`. `preview` with the file shows `lut == 100`; with a photo as `lut`: `ValueError` |
| 25 | `test_rerender_asks_for_the_lut_again` | A batch with `_sepia` at 40. `adjust.rerender(img)` raises `ValueError` with `--lut sepia.cube`; another file name raises; with the file: no warning and the bytes of the batch; a file of that name with other content: one warning with "LUT file differs". `--set global.lut=20` gives 60. `cli.main(["adjust", img, "--lut", f, ...])` is 0, without `--lut` 2. `parse_set("sky.lut=20")` raises with `lut is for the whole photo, use global.lut`; for `sky.grain=20` the text is `grain is for the whole photo, use global.grain`. On a run without a file `--set global.lut=50` leaves no `lut` setting and the note `lut: this run has no LUT file, the amount is ignored` |
| 26 | `test_workers_write_the_same_files` | A batch with `_sat` and `workers=2` writes the bytes of `workers=1` |
| 27 | `test_look_settings_holds_the_per_pixel_part_only` | A look with every multiplier set, `vibrance_add=8`, `saturation_add=6`, `curve_add=12`, two grade values and `grain=30`: exactly the five adds, no key of `SPATIAL_OPS`. `Look(contrast_mul=1.4)` gives `{}`. `dehaze_add=-10` gives `{"dehaze": -10.0}`, `dehaze_add=10` nothing; `bw=100, bw_red=30` gives both, `bw_red=30` alone nothing |
| 28 | `test_look_lut_matches_the_look` | For every packaged look, `lut_map` with `look_cube`'s table against `apply_ops` with `look_settings`, clipped: on colours of 0.2..0.8 at most 3 levels and 0.05 on average (measured 0.58 and 0.017); on colours of 0..1 at most 4 and 0.25 (measured 3.0 and 0.13). The first comment names the look, the third starts with "Not in this file". A look with multipliers only gives the identity table and "nothing" |
| 29 | `test_cli_looks_lut` | `looks lut punchy -o F` returns 0; the file reads with 33 points, holds no folder name, and its head has `AutoEdit look punchy. The whole-picture colour part only`, `saturation +6`, `Not in this file: edits of single regions`, `sRGB` and `TITLE "AutoEdit punchy"`; it changes a frame by more than 0.005 on average. An unknown look and a `-o` that ends in `.txt` return 2, and no `.txt` file is written |

Test 3, with `H` the line `LUT_3D_SIZE 2` and `R` the eight rows of the identity. What the file
holds, and the part of the message:

- only a comment and a `TITLE`; the empty file: `no LUT_3D_SIZE`
- a row before `H`: `a table row before LUT_3D_SIZE`
- the size `1`, `66`, `2.0`, none, `2 2`: `whole number from 2 to 65`
- `H` twice: `LUT_3D_SIZE is given twice`. `LUT_1D_SIZE 4`: `1D LUT`
- `SECRET123 4`, `title "x"`, `LUT_2D_SIZE 4`, a row `nan 0 0`: `not a keyword`
- seven rows: `the table has 7 rows; LUT_3D_SIZE 2 needs 8`. Nine: `more table rows than
  LUT_3D_SIZE 2 allows (8)`
- a row `0 0`, `0 0 0 0`, `0 nan 0`, `0 inf 0`, `0 1_0 0`, `0 0x10 0`, `0,5 0 0`, `0 1e 0`,
  `0 0 0 # SECRET123`: `three plain numbers`
- a value `2.5`, `-1.5`, `1e39`, `1e400`: `outside -1 to 2`
- `DOMAIN_MAX 1 1 1` after four rows: `a keyword after the table began`
- `DOMAIN_MIN 0 0`, `DOMAIN_MAX 1 1 x`: `needs three plain numbers`. `LUT_3D_INPUT_RANGE 0`:
  `needs two plain numbers`
- `DOMAIN_MIN 0 1 0`, `DOMAIN_MAX 1 1 1e9`, `LUT_3D_INPUT_RANGE 1 0`: `input range`
- a zero byte: `not a text file`

Changed in existing tests, nothing else. On the scratch builds exactly these failed first:

- `tests/test_parallel.py::test_a_bundle_can_be_cut_between_any_two_steps`: the bundle gains
  `"lut": 50.0`.
- `tests/test_grain.py::test_registered_last_and_not_rendered_by_apply_ops`: the assert on
  `STEPS` becomes `ops.STEPS[-1] == ("grain",) and ops.STEPS[-2] == ("lut",) and ops.STEPS[-3] ==
  ("sharpen",)`.
- `tests/snapshots/settings_schema.json`: two more settings.

## Acceptance checks (the session runs these)

1. `PYTHONPATH=src python3 tests/run_all.py` and `PYTHONPATH=src .venv/bin/python tests/run_all.py`
   are green.
2. The stub smoke run of `CLAUDE.md` completes with sidecars and a manifest. Twice more into new
   folders. With an identity file (`lut.write_cube(path, lut.identity(33), "identity")`, one
   line of `python3 -c`): the exports have the bytes of the default run. With
   `autoedit looks lut dramatic -o out-features/F25/dramatic.cube` and then
   `--lut out-features/F25/dramatic.cube --lut-amount 60`: the exports differ, and each sidecar
   has `lut: 60`, the note and `audit.lut`.
3. Default output is byte-identical to the baseline renders (the reference photos and the smoke
   corpus, maximum difference 0). Expected and not pixels: sidecars gain `lut_amount` under
   `audit.run_config`. `config_digest` does not change.
4. A real shoot: twelve photos of `curated-dolomites-summer-2026/selected_jpeg`, copied into a
   folder of their own, at full size with the stub backend, once without and once with `--lut`
   of a file written by `autoedit looks lut film_faded -o ...`. Both complete. For three of the photos, by a few lines
   of `python3 -c`: `process_one` with the file is `array_equal` to `ops.lut_map` of the result
   without. The number of photos flagged for review, with and without, goes into the "Result",
   and the time per photo: measured 6.0 to 6.7 s without and 6.4 to 7.1 s with (22 megapixels).
5. The tone gate of `CLAUDE.md` is not due: no default changes, and with a LUT the tone is what
   the file says. One run proves the path through the safety workflow:
   `PYTHONPATH=src python3 scripts/tone_report.py curated-dolomites-summer-2026/selected_raw --render --fail-on-regression --limit 12 --set lut=PATH`
   with the identity file exits 0 and gives the rows of the same command without `--set`, apart
   from the timings. Measured on six photos: equal.
6. The Checker's review.

## Known limits

- 3D `.cube` files only. No 1D LUT, no file with a 1D part in front of the table, no `.3dl`,
  `.look` or picture LUT.
- The table must be made for display pictures (sRGB or Rec.709). A table for log footage gives a
  wrong picture, and nothing can detect that.
- Nothing limits a LUT. The oversaturation gate and the highlight hold stand before it; the
  review check reports what it did (Design 3), and a strong table marks many photos for review.
  Banding that a table adds is reported, not repaired.
- The safety workflow, and so `scripts/tone_report.py`, judges a LUT as part of the edit and
  blends a picture it refuses toward the source. The two experimental workflows of
  `workflows.py` (`global-first`, `optimized`) do not go through `process_one` and apply no LUT.
- One photo's `--set global.lut=N` can raise the amount, not lower it (fact 5). Lower
  `--lut-amount`.
- The file is read at every `Pipeline.build` (fact 7): 0.06 s for 33 points, 0.45 s for 65. It is
  not cached; a cache by file stamp belongs to the Studio control.
- A file from `looks lut` is not the look. It holds what the look adds to every photo; for
  `natural` that is `curve +5`. The rules' part and the multipliers are missing.
- The cloud decider is still offered the op name (its list is `OP_BOUNDS`); its value is dropped.
  A verb that a profile maps to `lut` and aims at a part of the photo is refused with the words
  for grain (`instructions.WHOLE_PHOTO`).
- Studio has no control. Over the API the file must lie in a folder that was picked, for example
  next to the photos. `/api/jobs` builds its settings in the job, so there a refused file is the
  job's error, with the same message, and not a 403.

## Proposed plan row

`| F25 | LUT files | S | done, off by default (`--lut FILE.cube`, `--lut-amount N`; `autoedit looks lut NAME -o FILE.cube`); the owner tries a LUT of their own |`

For the two tables at the end of `FEATURE_PLAN.md`:

- For the owner: "A LUT goes past the oversaturation gate. Is that right?" Evidence: Design 3.
  The change: the row in the decisions table above.
- For the owner: "A black and white look with a toning LUT gives a toned black and white. Or
  should it end neutral?" The change: the row in the decisions table above.
- Changed against the plan: "`lut` is the step before `grain` in `ops.STEPS`, not a step after
  the colour steps, and `apply_ops` does not render it: the pipeline lays the LUT over the
  finished picture. The review check and the banding verdict run after it, the oversaturation
  gate before." Why: after the colour steps the gate takes a table's colour back, or misreads
  it (Design 3). Reverse it by: the row in the decisions table.

## Result

Built on 2026-11-17 in the unattended run. Status: done, off by default.

- **Built** by three Builders (Sonnet; T1 and T2 at high effort, T3 at medium) from this spec,
  which the Planner (Fable, max) wrote after building the feature twice on scratch copies.
- **The Checker (Opus, high)** found one thing that had to be fixed, fixed by the session: a file
  whose input range is open in float64 but collapses in float32 (`DOMAIN_MIN 999999.9999`,
  `DOMAIN_MAX 1000000`) passed the reader, and `lut_map` divided by zero: an all-black export with
  no review flag. `parse_cube` now also checks the range in float32, and the file is in test 3's
  broken list. Also done from its notes: test 16 checks all three channels for neutral; a look
  LUT says its values are at intensity 1. Notes kept: conflicting range keywords take the last
  line; a domain that does not cover 0..1 gives values outside 0..1 that the export clips; the
  web path checks a LUT's folder before `Pipeline.build` opens it (the same window as for photo
  paths). Everything else held: every path byte-identical with no LUT and with an identity LUT;
  16 MB hostile files refused in 1.1 s or less, with messages that name no folder and no content;
  a LUT outside the picked folders is a 403 on preview, run, run-one and variants (the reader is
  never called) and a failed job on `/jobs`; `lut_map` within 3.9e-7 of an independent float64
  reference.
- **Checks 2 and 3:** the stub smoke run completes; the default and the identity-LUT exports are
  byte-identical to the baseline; with `looks lut dramatic` at amount 60 the exports differ and
  the sidecar has `lut: 60`, the note and `audit.lut` (name, digest, 33 points), no path.
- **Check 4,** twelve photos of `selected_jpeg` at full size, stub: both runs complete; flagged
  for review 2 of 12 without the LUT and 0 of 12 with `film_faded` as a LUT; 102.9 s against
  111.7 s for the twelve (8.6 s and 9.3 s per photo, on a machine busy with other agents); for
  three photos the LUT run is `array_equal` to `ops.lut_map` of the run without.
- **Check 5:** the tone report on 12 RAW photos with the identity LUT exits 0 and gives the same
  rows as without, apart from the timings.
- Files: `out-features/F25/` (the LUTs, the runs, `accept.sh`).
