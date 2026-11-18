"""F25: LUT files. Tests 1 to 13 (T1): the file (lut.py) and the op (edit/ops.py::lut_map).
Tests 14 to 26 (T2): from the setting to the picture.

Tables come from formulas, so no file is downloaded."""

import hashlib
import os

import numpy as np
import pytest

from autoedit import lut
from autoedit.edit import apply_image, ops, par


# ---- tables from formulas ----------------------------------------------------------------------

def _l(x):
    return 0.2126 * x[..., 0:1] + 0.7152 * x[..., 1:2] + 0.0722 * x[..., 2:3]


def _swap(x):
    return x[..., ::-1]


def _curves(x):
    return np.stack([x[..., 0] ** 2, x[..., 1] ** 1.5, x[..., 2]], axis=-1)


def _half_sat(x):
    return _l(x) + (x - _l(x)) * 0.5


def _sat(x):
    return _l(x) + (x - _l(x)) * 1.5


def _sepia(x):
    l = _l(x)
    return np.concatenate([l ** 0.9, l, l ** 1.25], axis=-1)


def _bright(x):
    return np.minimum(1.0, 1.6 * x)


def _steps(x):
    return np.floor(4 * x) / 4


def _table(fn, n=33):
    x = lut.identity(n).astype(np.float64)
    return np.clip(fn(x), 0.0, 1.0).astype(np.float32)


def _lut(fn, n=33):
    return lut.Lut(_table(fn, n))


def _cube(folder, name, fn, n=33):
    path = os.path.join(str(folder), name)
    lut.write_cube(path, _table(fn, n), "test")
    return path


def _frame(shape=(64, 96), lo=0.0, hi=1.0, seed=0):
    return np.random.default_rng(seed).uniform(lo, hi, shape + (3,)).astype(np.float32)


def _levels(a, b):
    return float(np.abs(np.asarray(a, np.float64) - np.asarray(b, np.float64)).max()) * 255.0


def _rows3():
    """Rows of a 3-point identity-like table, each one distinct."""
    return [f"{i / 26:.4f} {(26 - i) / 26:.4f} {(i % 5) / 4:.4f}" for i in range(27)]


# ---- 1 ---------------------------------------------------------------------------------------

def test_reads_a_plain_file(tmp_path):
    rows = _rows3()
    text = ("# a comment\r\n\r\nTITLE \"Plain\"\r\nDOMAIN_MIN 0.0 0.0 0.0\r\nDOMAIN_MAX 1.0 1.0 1.0\r\n"
            "LUT_3D_SIZE\t3\r\n" + "\r\n".join(r.replace(" ", "\t") if i % 2 else r for i, r in enumerate(rows)) + "\r\n")
    data = b"\xef\xbb\xbf" + text.encode()
    p = tmp_path / "plain.cube"
    p.write_bytes(data)
    t = lut.read_cube(str(p))
    assert t.table.shape == (3, 3, 3, 3) and t.table.dtype == np.float32
    assert not t.table.flags.writeable
    for r in range(3):
        for g in range(3):
            for b in range(3):
                want = np.float32([float(v) for v in rows[r + 3 * g + 9 * b].split()])
                assert np.array_equal(t.table[b, g, r], want), (r, g, b)
    assert t.name == "plain.cube" and t.points == 3
    assert len(t.digest) == 16 and int(t.digest, 16) >= 0
    assert t.digest == hashlib.blake2b(data, digest_size=8).hexdigest()
    p.write_bytes(data[:-3] + b"9" + data[-2:])
    assert lut.read_cube(str(p)).digest != t.digest


# ---- 2 ---------------------------------------------------------------------------------------

def test_accepts_what_real_files_hold():
    ident = [f"{r / 1:.1f} {g / 1:.1f} {b / 1:.1f}" for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    head = b"# " + b"x" * 396 + b"\xa9\x85\n"
    body = b"TITLE My film\nLUT_3D_INPUT_RANGE 0.0 1.0\nLUT_3D_SIZE 2\n" + "\n".join(ident).encode()
    assert lut.parse_cube(head + body.replace(b"\n", b"\r")).points == 2      # CR ends, no last newline
    rows = ["+0.5 .5 1.", "1e-3 1E0 5e-1"] + ["0 0 0"] * 6
    t = lut.parse_cube(b"LUT_3D_SIZE 2\n" + "\n".join(rows).encode())
    assert np.allclose(t.table[0, 0, 0], [0.5, 0.5, 1.0]) and np.allclose(t.table[0, 0, 1], [0.001, 1.0, 0.5])
    rows = ["-0.01 1.02 0"] + ["0 0 0"] * 7
    t = lut.parse_cube(b"LUT_3D_SIZE 2\n" + "\n".join(rows).encode())
    assert t.table[0, 0, 0, 0] == 0.0 and t.table[0, 0, 0, 1] == 1.0
    big = b"TITLE " + b"t" * 5000 + b"\nDOMAIN_MAX 2 2 4\nLUT_3D_SIZE 2\n" + "\n".join(ident).encode() + b"\n"
    assert lut.parse_cube(big).hi == (2.0, 2.0, 4.0)


# ---- 3 ---------------------------------------------------------------------------------------

_R = "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1))
_H = "LUT_3D_SIZE 2"
_R7 = "\n".join(_R.split("\n")[:7])
_BROKEN = [
    ("# SECRET123\nTITLE \"SECRET123\"\n", "no LUT_3D_SIZE"),
    ("", "no LUT_3D_SIZE"),
    ("0 0 0\n" + _H + "\n" + _R, "a table row before LUT_3D_SIZE"),
    ("LUT_3D_SIZE 1\n" + _R, "whole number from 2 to 65"),
    ("LUT_3D_SIZE 66\n" + _R, "whole number from 2 to 65"),
    ("LUT_3D_SIZE 2.0\n" + _R, "whole number from 2 to 65"),
    ("LUT_3D_SIZE\n" + _R, "whole number from 2 to 65"),
    ("LUT_3D_SIZE 2 2\n" + _R, "whole number from 2 to 65"),
    (_H + "\n" + _H + "\n" + _R, "LUT_3D_SIZE is given twice"),
    ("LUT_1D_SIZE 4\n" + _R, "1D LUT"),
    ("# SECRET123\nDOMAIN_MIN 999999.9999 999999.9999 999999.9999\nDOMAIN_MAX 1000000 1000000 1000000\n" + _H + "\n" + _R,
     "end above its start"),       # a range that collapses in float32 would divide by zero
    ("SECRET123 4\n" + _H + "\n" + _R, "not a keyword"),
    ("title \"x\"\n" + _H + "\n" + _R, "not a keyword"),
    ("LUT_2D_SIZE 4\n" + _H + "\n" + _R, "not a keyword"),
    (_H + "\nnan 0 0\n" + _R, "not a keyword"),
    (_H + "\n" + _R7, "the table has 7 rows; LUT_3D_SIZE 2 needs 8"),
    (_H + "\n" + _R + "\n0 0 0", "more table rows than LUT_3D_SIZE 2 allows (8)"),
] + [(_H + "\n" + row + "\n" + _R, "three plain numbers") for row in (
    "0 0", "0 0 0 0", "0 nan 0", "0 inf 0", "0 1_0 0", "0 0x10 0", "0,5 0 0", "0 1e 0", "0 0 0 # SECRET123")] + [
    (_H + "\n" + row + "\n" + _R, "outside -1 to 2") for row in ("2.5 0 0", "0 -1.5 0", "1e39 0 0", "0 0 1e400")] + [
    (_H + "\n" + "\n".join(_R.split("\n")[:4]) + "\nDOMAIN_MAX 1 1 1\n" + "\n".join(_R.split("\n")[4:]),
     "a keyword after the table began"),
    ("DOMAIN_MIN 0 0\n" + _H + "\n" + _R, "needs three plain numbers"),
    ("DOMAIN_MAX 1 1 x\n" + _H + "\n" + _R, "needs three plain numbers"),
    ("LUT_3D_INPUT_RANGE 0\n" + _H + "\n" + _R, "needs two plain numbers"),
    ("DOMAIN_MIN 0 1 0\n" + _H + "\n" + _R, "input range"),
    ("DOMAIN_MAX 1 1 1e9\n" + _H + "\n" + _R, "input range"),
    ("LUT_3D_INPUT_RANGE 1 0\n" + _H + "\n" + _R, "input range"),
]


def test_every_broken_file_has_its_own_message(tmp_path):
    folder = tmp_path / "SECRET-folder"
    folder.mkdir()
    for text, part in _BROKEN:
        # the hostile word sits in a comment of every file (and in a title, a keyword or a row of some)
        data = ("# SECRET123\n" + text).encode() + b"\n# SECRET123"
        with pytest.raises(ValueError) as e:
            lut.parse_cube(data, "bad.cube")
        msg = str(e.value)
        assert msg.startswith("bad.cube: "), msg
        assert part in msg, (part, msg)
        assert "SECRET123" not in msg and "SECRET-folder" not in msg, msg
    with pytest.raises(ValueError) as e:
        lut.parse_cube(b"\0LUT_3D_SIZE 2", "bad.cube")
    assert "not a text file" in str(e.value)
    # the line numbers
    with pytest.raises(ValueError) as e:
        lut.parse_cube(("# c\n" + _H + "\n" + _H + "\n").encode(), "bad.cube")
    assert str(e.value).startswith("bad.cube: line 3: ")
    rows = _R.split("\n")
    with pytest.raises(ValueError) as e:
        lut.parse_cube((_H + "\n" + "\n".join(rows[:2]) + "\n0 0 SECRET123\n").encode(), "bad.cube")
    assert str(e.value).startswith("bad.cube: line 4: ")
    # the same through a real file: neither the folder nor the content in the error
    p = folder / "bad.cube"
    p.write_bytes(("# SECRET123\n" + _H + "\n" + _R7 + "\n").encode())
    with pytest.raises(ValueError) as e:
        lut.read_cube(str(p))
    assert "SECRET" not in str(e.value) and str(e.value).startswith("bad.cube: ")


# ---- 4 ---------------------------------------------------------------------------------------

def test_size_limit_ending_and_missing_file(tmp_path):
    folder = tmp_path / "SECRET-folder"
    folder.mkdir()
    p = _cube(folder, "ok.cube", _swap, n=17)
    old = lut.MAX_BYTES
    try:
        lut.MAX_BYTES = os.path.getsize(p) - 1
        with pytest.raises(ValueError) as e:
            lut.read_cube(p)
        assert "larger than" in str(e.value) and "SECRET" not in str(e.value)
    finally:
        lut.MAX_BYTES = old
    assert lut.MAX_BYTES == 16 * 1024 * 1024 and lut.MAX_POINTS == 65
    txt = folder / "x.txt"
    txt.write_bytes(open(p, "rb").read())
    with pytest.raises(ValueError) as e:
        lut.read_cube(str(txt))
    assert "must end in .cube" in str(e.value) and "SECRET" not in str(e.value)
    (folder / "dir.cube").mkdir()
    for missing in (str(folder / "nothing.cube"), str(folder / "dir.cube")):
        with pytest.raises(ValueError) as e:
            lut.read_cube(missing)
        assert "not found" in str(e.value) and "SECRET" not in str(e.value)
    assert lut.read_cube(_cube(folder, "big.cube", _swap, n=65)).points == 65


# ---- 5 ---------------------------------------------------------------------------------------

def test_writer_round_trips(tmp_path):
    t = _table(_curves)
    p = str(tmp_path / "w.cube")
    lut.write_cube(p, t, 'A "quoted" títle', ("first line", "second"))
    raw = open(p, "rb").read()
    text = raw.decode("ascii")
    assert b"\r" not in raw
    lines = text.split("\n")
    assert lines[0] == "# first line" and lines[1] == "# second" and lines[2] == "TITLE \"A quoted ttle\""
    assert len(lines) - 1 == 6 + 33 ** 3 and lines[-1] == ""
    assert np.abs(lut.read_cube(p).table - t).max() <= 1e-6
    assert not os.path.exists(p + ".part") and sorted(os.listdir(tmp_path)) == ["w.cube"]
    with pytest.raises(ValueError):
        lut.write_cube(str(tmp_path / "w.txt"), t, "x")
    with pytest.raises(ValueError):
        lut.write_cube(p, t[:, :, :5], "x")
    with pytest.raises(ValueError):
        lut.write_cube(p, np.zeros((1, 1, 1, 3), np.float32), "x")
    assert not os.path.exists(str(tmp_path / "w.txt"))


# ---- 6 ---------------------------------------------------------------------------------------

def test_zero_is_the_identity():
    x = _frame()
    shift = _lut(_swap).shift
    assert ops.lut_map(x, shift, 0) is x
    assert ops.lut_map(x, shift, -5) is x


# ---- 7 ---------------------------------------------------------------------------------------

def test_identity_lut_is_a_no_op(tmp_path):
    x = _frame((40, 50), -0.3, 1.3, seed=1)
    for n in (2, 3, 17, 32, 33, 64, 65):
        lt = lut.Lut(lut.identity(n))
        assert not lt.shift.any(), n
        for amt in (100, 37):
            assert np.array_equal(ops.lut_map(x, lt.shift, amt), x), (n, amt)
    for n in (17, 32, 33, 65):
        p = str(tmp_path / f"id{n}.cube")
        lut.write_cube(p, lut.identity(n), "identity")
        back = lut.read_cube(p)
        out = ops.lut_map(x, back.shift, 100)
        if n == 32:
            assert np.abs(out - x).max() < 1e-6
        else:
            assert np.array_equal(out, x), n


# ---- 8 ---------------------------------------------------------------------------------------

def test_known_luts_within_one_level():
    x = _frame((120, 160), seed=2)
    assert np.abs(ops.lut_map(x, _lut(_swap).shift, 100) - x[..., ::-1]).max() < 1e-6
    for fn in (_curves, _half_sat):
        want = np.clip(fn(x.astype(np.float64)), 0.0, 1.0)
        assert _levels(ops.lut_map(x, _lut(fn).shift, 100), want) <= 1.0, fn.__name__


# ---- 9 ---------------------------------------------------------------------------------------

def test_grey_stays_grey():
    ramp = np.repeat(np.linspace(0.0, 1.0, 4001, dtype=np.float32)[None, :, None], 3, axis=2)
    for n in (17, 33):
        out = ops.lut_map(ramp, _lut(_sat, n).shift, 100)
        assert float((out.max(-1) - out.min(-1)).max()) <= 1e-6, n
        assert np.abs(out - ramp).max() <= 1e-6, n


# ---- 10 --------------------------------------------------------------------------------------

def test_amount_blends():
    x = _frame(seed=3)
    shift = _lut(_swap).shift
    full = ops.lut_map(x, shift, 100)
    assert np.abs(ops.lut_map(x, shift, 50) - (x + full) / 2).max() <= 1e-6
    assert np.abs(ops.lut_map(x, shift, 25) - (x + 0.25 * (full - x))).max() <= 1e-6
    assert np.array_equal(ops.lut_map(x, shift, 250), full)


# ---- 11 --------------------------------------------------------------------------------------

def test_domain_and_values_outside():
    x = _frame((50, 60), 0.0, 2.0, seed=4)
    lt = lut.Lut(_table(_curves), hi=(2.0, 2.0, 2.0))
    out = ops.lut_map(x, lt.shift, 100, lt.lo, lt.hi)
    want = np.clip(_curves(x.astype(np.float64) / 2.0), 0.0, 1.0)
    assert _levels(out, want) <= 1.0
    px = np.array([[[-0.5, 0.5, 1.7]]], np.float32)
    assert np.abs(ops.lut_map(px, _lut(_swap).shift, 100) - np.array([[[0.5, 0.5, 0.7]]])).max() < 1e-6
    shift = _lut(_sat).shift
    for shape in ((1, 1), (3, 5), (301, 127)):
        y = ops.lut_map(_frame(shape, seed=5), shift, 80)
        assert y.shape == shape + (3,) and y.dtype == np.float32 and np.isfinite(y).all(), shape
        assert y.min() >= -1e-6 and y.max() <= 1.0 + 1e-6, shape


# ---- 12 --------------------------------------------------------------------------------------

def test_strips_are_bit_identical():
    x = _frame((700, 900), seed=6)
    lt = _lut(_sat)

    def fn(a):
        return ops.lut_map(a, lt.shift, 80)

    one = fn(x)
    old = (par._threads, par.MIN_PIXELS)
    try:
        par.MIN_PIXELS = 0
        par.set_threads(4)
        assert len(par.each(lambda a: a.shape[0], x)) > 1
        four = par.rows(fn, x)
    finally:
        par.MIN_PIXELS = old[1]
        par.set_threads(old[0])
    assert np.array_equal(one, four)


# ---- 13 --------------------------------------------------------------------------------------

def test_registered_as_a_finishing_step_that_apply_ops_does_not_render():
    assert "lut" in ops.OP_NAMES and ops.OP_BOUNDS["lut"] == (0.0, 100.0)
    assert "lut" not in ops.SPATIAL_OPS and "lut" not in ops._COLOUR_KEYS and "lut" not in ops._TONE_KEYS
    i = ops.STEPS.index(("lut",))
    assert ops.STEPS[i - 1] == ("sharpen", "sharpen_stars") and ops.STEPS[i + 1] == ("grain",)
    x = _frame(seed=7)
    assert ops.apply_ops(x, {"lut": 100.0}) is x
    assert np.array_equal(ops.apply_ops(x, {"sharpen": 20, "lut": 60}), ops.apply_ops(x, {"sharpen": 20}))
    h, w = x.shape[:2]
    alpha = np.zeros((h, w), np.float32)
    alpha[:, : w // 2] = 1.0
    base = {"exposure": 0.2, "saturation": 10.0}
    plain = apply_image(x, [(alpha, {"contrast": 10.0})], dict(base))
    with_lut = apply_image(x, [(alpha, {"contrast": 10.0, "lut": 70.0})], {**base, "lut": 50.0})
    assert np.array_equal(plain, with_lut)


# ---- Tests 14 to 26 (T2): from the setting to the picture -------------------------------------

import json
from types import SimpleNamespace

from PIL import Image as PILImage

from autoedit import adjust, batch, cli
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline
from autoedit.web import service


def _scene(path, w=600, h=400):
    a = np.zeros((h, w, 3), np.float32)
    a[: h * 55 // 100] = [100, 140, 200]   # sky
    a[h * 55 // 100:] = [60, 110, 70]      # foliage
    yy, xx = np.mgrid[0:h, 0:w]
    a[(((xx - w / 2) / (w * 0.22)) ** 2 + ((yy - h * 0.62) / (h * 0.28)) ** 2) <= 1.0] = [200, 150, 120]
    a += np.random.default_rng(1).normal(0, 2.0, a.shape)
    PILImage.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(path, quality=95)


def _folder(tmp_path, name, photos=("a.jpg",)):
    d = tmp_path / name
    d.mkdir()
    for photo in photos:
        _scene(str(d / photo))
    return d


def _bytes(p):
    with open(p, "rb") as fh:
        return fh.read()


def _sidecar(p):
    with open(p) as fh:
        return json.load(fh)


def _stub(**kw):
    return RunConfig(backend="stub", **kw)


def _process(p, **kw):
    return Pipeline.build(_stub(**kw)).process_one(p, do_apply=True)


def _spread(x):
    return float((x.max(axis=-1) - x.min(axis=-1)).mean())


def _put(folder, name, fn):
    return _cube(folder, name, fn)


def _lut_notes(res):
    return [n for n in res.notes if n.startswith("lut")]


# ---- 14 --------------------------------------------------------------------------------------

def test_default_is_off_and_byte_identical(tmp_path):
    cfg = RunConfig()
    assert cfg.lut is None and cfg.lut_amount == 100.0
    src = _folder(tmp_path, "in")
    batch.run(_stub(), str(src), str(tmp_path / "o1"), workers=1)
    batch.run(_stub(lut="", lut_amount=40.0), str(src), str(tmp_path / "o2"), workers=1)
    assert _bytes(tmp_path / "o1" / "a.jpg") == _bytes(tmp_path / "o2" / "a.jpg")
    side = _sidecar(tmp_path / "o1" / "a.autoedit.json")
    assert "lut" not in side["global_settings"] and "lut" not in side["audit"]
    assert "lut" not in side["audit"]["run_config"] and side["audit"]["run_config"]["lut_amount"] == 100.0
    assert set(side["audit"]["run_inputs"]) == {"reference", "recipe_model"}
    assert not any(n.startswith("lut") for n in side["notes"])


# ---- 15 --------------------------------------------------------------------------------------

def test_identity_lut_run_is_byte_identical(tmp_path):
    src = _folder(tmp_path, "in")
    folder = tmp_path / "my luts"
    folder.mkdir()
    path = os.path.join(str(folder), "identity.cube")
    lut.write_cube(path, lut.identity(33), "identity")
    batch.run(_stub(), str(src), str(tmp_path / "o1"), workers=1)
    batch.run(_stub(lut=path), str(src), str(tmp_path / "o2"), workers=1)
    assert _bytes(tmp_path / "o1" / "a.jpg") == _bytes(tmp_path / "o2" / "a.jpg")
    plain = _sidecar(tmp_path / "o1" / "a.autoedit.json")
    side = _sidecar(tmp_path / "o2" / "a.autoedit.json")
    assert side["global_settings"]["lut"] == 100
    assert side["audit"]["run_inputs"]["lut"] == "identity.cube"
    a = side["audit"]["lut"]
    assert a["name"] == "identity.cube" and a["points"] == 33 and len(a["digest"]) == 16
    assert "lut" not in side["audit"]["run_config"]
    text = json.dumps(side)
    assert str(folder) not in text and "my luts" not in text
    mine = [n for n in side["notes"] if n.startswith("lut: identity.cube (33 points, digest ")]
    assert len(mine) == 1
    assert not any("lut" in m["ops"] for m in side["masks"] + side["composition"])
    for k in ("post_edit_stats", "quality_score", "needs_review", "warnings"):
        assert side[k] == plain[k], k


# ---- 16 --------------------------------------------------------------------------------------

def test_a_lut_run_is_the_default_picture_with_the_lut_over_it(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    f = _put(tmp_path, "swap.cube", _swap)
    table = lut.read_cube(f)
    base_rgb, base = _process(p)
    rgb, res = _process(p, lut=f)
    assert np.array_equal(rgb, ops.lut_map(base_rgb, table.shift, 100.0, table.lo, table.hi))
    assert res.global_settings == {**base.global_settings, "lut": 100.0}
    assert [m.ops for m in res.masks] == [m.ops for m in base.masks]
    rgb, res = _process(p, lut=f, lut_amount=50.0)
    assert np.array_equal(rgb, ops.lut_map(base_rgb, table.shift, 50.0, table.lo, table.hi))
    assert res.global_settings["lut"] == 50.0
    rgb, res = _process(p, lut=f, lut_amount=0.0)
    assert np.array_equal(rgb, base_rgb)
    assert "lut" not in res.global_settings and res.audit["run_inputs"]["lut"] == "swap.cube"
    none, res = Pipeline.build(_stub(lut=f)).process_one(p, do_apply=False)
    assert none is None and res.global_settings["lut"] == 100.0
    sepia = _put(tmp_path, "sepia.cube", _sepia)
    st = lut.read_cube(sepia)
    bw_rgb, _ = _process(p, look="bw_classic")
    assert float((bw_rgb.max(-1) - bw_rgb.min(-1)).max()) == 0.0      # the look alone is neutral
    rgb, _ = _process(p, look="bw_classic", lut=sepia)
    assert np.array_equal(rgb, ops.lut_map(bw_rgb, st.shift, 100.0, st.lo, st.hi))
    assert _spread(rgb) > 0.05


# ---- 17 --------------------------------------------------------------------------------------

def test_the_reference_match_comes_before_the_lut(tmp_path):
    src = _folder(tmp_path, "in", ("a.jpg", "ref.jpg"))
    p, ref = str(src / "a.jpg"), str(src / "ref.jpg")
    f = _put(tmp_path, "swap.cube", _swap)
    table = lut.read_cube(f)
    base_rgb, base = _process(p, reference=ref)
    rgb, res = _process(p, reference=ref, lut=f)
    assert np.array_equal(rgb, ops.lut_map(base_rgb, table.shift, 100.0, table.lo, table.hi))
    assert any(n.startswith("reference match") for n in res.notes)


# ---- 18 --------------------------------------------------------------------------------------

def test_a_broken_file_stops_the_run(tmp_path):
    src = _folder(tmp_path, "in")
    folder = tmp_path / "secret folder"
    folder.mkdir()
    bad = folder / "bad.cube"
    bad.write_text("LUT_3D_SIZE 2\n" + "\n".join(["0 0 0"] * 7) + "\n")
    with pytest.raises(ValueError) as e:
        Pipeline.build(_stub(lut=str(bad)))
    assert str(e.value).startswith("bad.cube: ") and "secret folder" not in str(e.value)
    photo_as_lut = str(src / "a.jpg")
    for k, path in enumerate((str(bad), str(folder / "missing.cube"), photo_as_lut)):
        out = tmp_path / f"out{k}"
        assert cli.main(["run", str(src), str(out), "--backend", "stub", "--lut", path]) == 2
        assert not out.exists() or not [n for n in os.listdir(out) if n.endswith((".jpg", ".json"))]


# ---- 19 --------------------------------------------------------------------------------------

def test_a_decider_cannot_set_it(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    f = _put(tmp_path, "swap.cube", _swap)

    def run_with(**kw):
        pipe = Pipeline.build(_stub(**kw))
        inner = pipe.decider

        class Adds:
            name = inner.name

            def decide(self, ctx):
                g, r = inner.decide(ctx)
                g["lut"] = 80.0
                for s in r.values():
                    s["lut"] = 80.0
                return g, r
        pipe.decider = Adds()
        return pipe.process_one(p, do_apply=True)[1]
    res = run_with()
    assert "lut" not in res.global_settings and res.masks
    assert not any("lut" in m.ops for m in res.masks) and not _lut_notes(res)
    res = run_with(lut=f, lut_amount=30.0)
    assert res.global_settings["lut"] == 30.0 and res.masks
    assert not any("lut" in m.ops for m in res.masks)


# ---- 20 --------------------------------------------------------------------------------------

def test_the_review_gate_reads_the_picture_after_the_lut(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    f = _put(tmp_path, "bright.cube", _bright)
    _, base = _process(p)
    _, res = _process(p, lut=f)
    assert res.post_edit_stats.mean_lum > base.post_edit_stats.mean_lum + 0.05
    assert not base.needs_review
    assert res.needs_review and "clipping" in res.review_reason


# ---- 21 --------------------------------------------------------------------------------------

def test_the_oversaturation_gate_cannot_pull_a_lut_back(tmp_path):
    d = tmp_path / "grey"
    d.mkdir()
    g = np.random.default_rng(2).normal(0.0, 0.01, (300, 400, 1)) + np.linspace(0.2, 0.8, 400)[None, :, None]
    PILImage.fromarray((np.clip(g, 0, 1).repeat(3, 2) * 255).astype(np.uint8), "RGB").save(str(d / "g.jpg"), quality=95)
    p = str(d / "g.jpg")
    f = _put(tmp_path, "sepia.cube", _sepia)
    base_rgb, base = _process(p)
    rgb, res = _process(p, lut=f)
    assert _spread(rgb) > 0.08 > 0.03 > _spread(base_rgb)
    gate = lambda r: [n for n in r.notes if n.startswith("oversaturation gate")]
    assert gate(res) == gate(base)
    assert sum("after the oversaturation gate" in n for n in res.notes) == 1


# ---- 22 --------------------------------------------------------------------------------------

def test_the_banding_verdict_reads_the_picture_after_the_lut(tmp_path):
    d = tmp_path / "ramp"
    d.mkdir()
    ramp = np.linspace(0.35, 0.75, 384)[None, :, None].repeat(256, 0).repeat(3, 2)
    ramp = ramp + np.random.default_rng(3).normal(0.0, 0.004, ramp.shape)
    PILImage.fromarray((np.clip(ramp, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB").save(str(d / "r.png"))
    p = str(d / "r.png")
    f = _put(tmp_path, "steps.cube", _steps)
    _, base = _process(p)
    _, res = _process(p, lut=f)
    assert not any("smooth-region banding" in w for w in base.warnings) and not base.needs_review
    assert any("smooth-region banding" in w for w in res.warnings)
    assert res.needs_review and "banding" in res.review_reason


# ---- 23 --------------------------------------------------------------------------------------

def test_cli_and_api_carry_the_setting(tmp_path):
    import argparse
    f = _put(tmp_path, "swap.cube", _swap)
    parser = argparse.ArgumentParser()
    cli._add_run_flags(parser)

    def parsed(*flags):
        args = parser.parse_args(["in", "out", *flags])
        args.configs_dir = None
        return cli.load_run_config(args)
    cfg = parsed("--lut", f, "--lut-amount", "60")
    assert (cfg.lut, cfg.lut_amount) == (f, 60.0)
    cfg = parsed()
    assert cfg.lut is None and cfg.lut_amount == 100.0

    run_file = tmp_path / "run.toml"
    run_file.write_text(f'[run]\nlut = "{f}"\nlut_amount = 60\n')
    cfg = cli.load_run_config(SimpleNamespace(configs_dir=None, config=str(run_file)))
    assert (cfg.lut, cfg.lut_amount) == (f, 60.0)

    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    try:
        service.approve_root(str(tmp_path))
        cfg = service.make_cfg({"lut": f, "lut_amount": 60})
        assert (cfg.lut, cfg.lut_amount) == (f, 60.0)
        assert service.make_cfg({}).lut is None
    finally:
        service._ALLOWED_ROOTS.clear()
    for amount in (150, -1):
        with pytest.raises(ValueError):
            service.make_cfg({"lut_amount": amount})
        with pytest.raises(ValueError):
            parsed("--lut-amount", str(amount))


# ---- 24 --------------------------------------------------------------------------------------

def test_api_path_must_be_approved(tmp_path):
    folder = _folder(tmp_path, "picked")
    other = tmp_path / "other"
    other.mkdir()
    inside = _put(folder, "swap.cube", _swap)
    outside = _put(other, "out.cube", _swap)
    link = str(folder / "link.cube")
    os.symlink(outside, link)
    photo = str(folder / "a.jpg")
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service._ALLOWED_ROOTS.clear()
    try:
        with pytest.raises(PermissionError):
            service.make_cfg({"lut": inside})
        service.approve_root(str(folder))
        assert service.make_cfg({"lut": inside}).lut == inside
        assert service.make_cfg({"lut": ""}).lut == "" and service.make_cfg({"lut": None}).lut is None
        for bad in (outside, link, str(folder / ".." / "other" / "out.cube"), str(other / "nothing.cube")):
            with pytest.raises(PermissionError):
                service.make_cfg({"lut": bad})
            with pytest.raises(PermissionError):
                service.preview(photo, {"backend": "stub", "lut": bad})
        shown = service.preview(photo, {"backend": "stub", "lut": inside})
        assert shown["result"]["global_settings"]["lut"] == 100.0
        with pytest.raises(ValueError):
            service.preview(photo, {"backend": "stub", "lut": photo})
    finally:
        service._ALLOWED_ROOTS.clear()


# ---- 25 --------------------------------------------------------------------------------------

def test_rerender_asks_for_the_lut_again(tmp_path):
    src = _folder(tmp_path, "in")
    f = _put(tmp_path, "sepia.cube", _sepia)
    out = tmp_path / "o"
    batch.run(_stub(lut=f, lut_amount=40.0), str(src), str(out), workers=1)
    img, side_path = str(out / "a.jpg"), str(out / "a.autoedit.json")
    first = _bytes(img)
    with pytest.raises(ValueError) as e:
        adjust.rerender(img)
    assert "--lut sepia.cube" in str(e.value)
    wrong = _put(tmp_path, "other.cube", _sepia)
    with pytest.raises(ValueError):
        adjust.rerender(img, lut=wrong)
    r = adjust.rerender(img, lut=f)
    assert r["warnings"] == [] and _bytes(img) == first
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    changed = _put(elsewhere, "sepia.cube", _curves)
    r = adjust.rerender(img, lut=changed)
    assert len(r["warnings"]) == 1 and "LUT file differs" in r["warnings"][0]
    batch.run(_stub(lut=f, lut_amount=40.0), str(src), str(out), workers=1)
    adjust.rerender(img, lut=f, sets=["global.lut=20"])
    assert _sidecar(side_path)["global_settings"]["lut"] == 60
    assert cli.main(["adjust", img, "--lut", f, "--reset"]) == 0
    assert cli.main(["adjust", img, "--reset"]) == 2
    with pytest.raises(ValueError) as e:
        adjust.parse_set("sky.lut=20")
    assert "lut is for the whole photo, use global.lut" in str(e.value)
    with pytest.raises(ValueError) as e:
        adjust.parse_set("sky.grain=20")
    assert "grain is for the whole photo, use global.grain" in str(e.value)

    plain = tmp_path / "plain"
    batch.run(_stub(), str(src), str(plain), workers=1)
    adjust.rerender(str(plain / "a.jpg"), sets=["global.lut=50"])
    side = _sidecar(str(plain / "a.autoedit.json"))
    assert "lut" not in side["global_settings"]
    assert "lut: this run has no LUT file, the amount is ignored" in side["notes"]


# ---- 26 --------------------------------------------------------------------------------------

def test_workers_write_the_same_files(tmp_path):
    src = _folder(tmp_path, "in", ("a.jpg", "b.jpg", "c.jpg"))
    f = _put(tmp_path, "sat.cube", _sat)
    batch.run(_stub(lut=f), str(src), str(tmp_path / "w1"), workers=1)
    batch.run(_stub(lut=f), str(src), str(tmp_path / "w2"), workers=2)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        assert _bytes(tmp_path / "w1" / name) == _bytes(tmp_path / "w2" / name), name


# ---- 27 to 29 (T3): a look as a LUT ----------------------------------------------------------

from autoedit import looks as looks_mod, paths


def _packaged_looks():
    return looks_mod.load(os.path.join(paths.configs_dir(), "looks.toml"))


def test_look_settings_holds_the_per_pixel_part_only():
    L = looks_mod.Look
    full = L(contrast_mul=1.4, clarity_mul=1.3, dehaze_mul=1.4, exposure_mul=0.5, curve_mul=1.2,
             vibrance_add=8.0, saturation_add=6.0, curve_add=12.0,
             grade_shadows_temp=-6.0, grade_highlights_temp=3.0, grain=30.0)
    got = lut.look_settings(full)
    assert got == {"vibrance": 8.0, "saturation": 6.0, "curve": 12.0,
                   "grade_shadows_temp": -6.0, "grade_highlights_temp": 3.0}
    assert not set(got) & ops.SPATIAL_OPS
    assert lut.look_settings(L(contrast_mul=1.4)) == {}
    assert lut.look_settings(L(dehaze_add=-10.0)) == {"dehaze": -10.0}
    assert lut.look_settings(L(dehaze_add=10.0)) == {}
    both = lut.look_settings(L(bw=100.0, bw_red=30.0))
    assert both == {"bw": 100.0, "bw_red": 30.0}
    assert lut.look_settings(L(bw_red=30.0)) == {}


def test_look_lut_matches_the_look():
    mid = _frame((60, 80), 0.2, 0.8, seed=3)
    full = _frame((60, 80), 0.0, 1.0, seed=4)
    for name, look in _packaged_looks().items():
        table, comments = lut.look_cube(name, look)
        shift = lut.Lut(table).shift
        settings = lut.look_settings(look)
        for x, worst, mean in ((mid, 3.0, 0.05), (full, 4.0, 0.25)):
            want = np.clip(ops.apply_ops(x, settings), 0.0, 1.0)
            d = np.abs(np.clip(ops.lut_map(x, shift, 100.0), 0.0, 1.0) - want) * 255.0
            assert d.max() <= worst and d.mean() <= mean, (name, d.max(), d.mean())
        assert name in comments[0] and comments[2].startswith("Not in this file")
    table, comments = lut.look_cube("flat", looks_mod.Look(contrast_mul=1.4))
    assert np.abs(table - lut.identity(lut.EXPORT_POINTS)).max() < 1e-6
    assert "nothing" in comments[1]


def test_cli_looks_lut(tmp_path):
    f = str(tmp_path / "my luts" / "p.cube")
    os.makedirs(os.path.dirname(f))
    assert cli.main(["looks", "lut", "punchy", "-o", f]) == 0
    text = open(f, encoding="ascii").read()
    assert lut.read_cube(f).points == 33
    assert "my luts" not in text and str(tmp_path) not in text
    for part in ("AutoEdit look punchy. The whole-picture colour part only", "saturation +6",
                 "Not in this file: edits of single regions", "sRGB", 'TITLE "AutoEdit punchy"'):
        assert part in text, part
    x = _frame((40, 40), seed=5)
    assert np.abs(ops.lut_map(x, lut.read_cube(f).shift, 100.0) - x).mean() > 0.005
    bad = str(tmp_path / "x.txt")
    assert cli.main(["looks", "lut", "nope", "-o", f]) == 2
    assert cli.main(["looks", "lut", "punchy", "-o", bad]) == 2
    assert not os.path.exists(bad)
