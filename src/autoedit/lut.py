"""3D LUT files (F25): the `.cube` format, read strictly and written, and a look as a LUT.

A LUT file comes from somewhere else, so the reader works on bytes and checks every line. An
error is a `ValueError` of the form `NAME: line N: rule`; it never holds the folder or a piece of
the file, so over the API a wrong file cannot be read through its error. The op that lays a table
over a picture is `edit/ops.py::lut_map`.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
from dataclasses import dataclass

import numpy as np

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
    # The op works in float32: the range must still be open there, or it divides by zero.
    if not all(abs(v) <= 1e6 for v in (*lo, *hi)) or not all(
            b - a >= 1e-6 and np.float32(b) - np.float32(a) >= np.float32(1e-6) for a, b in zip(lo, hi)):
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


def read_cube(path: str) -> Lut:
    """The LUT in the `.cube` file at `path`. Every error names the file, never its folder."""
    name = os.path.basename(str(path))
    if not name.lower().endswith(".cube"):
        raise ValueError(f"{name}: a LUT file must end in .cube")
    if not os.path.isfile(path):
        raise ValueError(f"{name}: LUT file not found")
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise ValueError(f"{name}: cannot be read ({type(e).__name__})") from None
    return parse_cube(data, name)


def _ascii(text: str, limit: int | None = None) -> str:
    text = "".join(c for c in str(text) if " " <= c <= "~" and c != '"')
    return text[:limit] if limit else text


def cube_text(table: np.ndarray, title: str, comments=()) -> str:
    """The text of a `.cube` file for an (n, n, n, 3) table over 0..1."""
    t = np.asarray(table)
    if t.ndim != 4 or t.shape[3] != 3 or t.shape[0] != t.shape[1] or t.shape[1] != t.shape[2] \
            or not 2 <= t.shape[0] <= MAX_POINTS:
        raise ValueError(f"a LUT table must be (n, n, n, 3) with n from 2 to {MAX_POINTS}")
    n = t.shape[0]
    head = [f"# {_ascii(c)}" for c in comments]
    head += [f'TITLE "{_ascii(title, 80)}"', f"LUT_3D_SIZE {n}",
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    body = np.clip(t.reshape(-1, 3), 0.0, 1.0)
    return "\n".join(head + [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in body.tolist()]) + "\n"


def write_cube(path: str, table: np.ndarray, title: str, comments=()) -> None:
    """Write a `.cube` file (ASCII, LF), swapped in whole so no half file is left."""
    path = str(path)
    if not path.lower().endswith(".cube"):
        raise ValueError("a LUT file must end in .cube")
    text = cube_text(table, title, comments)
    part = path + ".part"
    with open(part, "wb") as fh:
        fh.write(text.encode("ascii"))
    os.replace(part, path)


_LEFT_OUT = ("Not in this file: edits of single regions (sky, skin, ...) and a look's habits; everything the rules decide from each",
             "photo (exposure, contrast, white balance) and what the look multiplies of it; and the steps that",
             "read neighbouring pixels (clarity, haze removal, sharpening, noise reduction, grain).",
             "For sRGB pictures: values in and out are sRGB-encoded, 0 to 1.",
             "The look's values are taken at intensity 1; a run at another intensity scales them.")


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
