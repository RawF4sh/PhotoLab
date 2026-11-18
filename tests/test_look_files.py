"""F28 T1: look files, the reader and the writer (lookfile.py). Tests 1 to 11: what a file may hold, and
that nothing else gets through. Run through tests/run_all.py as well as pytest (no capsys, no parametrize)."""

import dataclasses
import json
import os
import tomllib

import pytest

from autoedit import calibrate, looks, lookfile, paths
from autoedit.edit import ops
from autoedit.lookfile import EXT, MAX_BYTES, LookFile, check_name, dumps, limits, parse, read, write
from autoedit.palette import load_palettes

HEAD = 'aelook = 1\nname = "mine"\n'
GOOD = HEAD + "\n[look]\ncontrast_mul = 1.2\ncurve_add = 8\n"
PAL = GOOD + "\n[palette]\nmid_hue = 200\nmid_chroma = 0.03\n"
HEAD_LINES = ("# AutoEdit look file. Data only: numbers and short text; nothing in it is run.\n"
              "# Read it with: autoedit looks import FILE\n")


def _library():
    return looks.load(os.path.join(paths.configs_dir(), "looks.toml"))


def _palettes():
    return load_palettes(os.path.join(paths.configs_dir(), "palettes.toml"))


def _refused(text, part):
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    with pytest.raises(ValueError) as e:
        parse(data, "bad.aelook")
    msg = str(e.value)
    assert msg.startswith("bad.aelook: "), msg
    assert part in msg, msg
    return msg


def _q(s):
    return json.dumps(s)   # a TOML basic string


def test_dumps_then_parse_gives_the_same_look():
    for name, look in _library().items():
        assert parse(dumps(name, look).encode()) == LookFile(name, look)
    habit = {"landscape": {"sky": {"exposure": -0.3}, "place:top": {"exposure": 0.2}}}
    for name, pal in _palettes().items():
        got = parse(dumps(name, looks.Look(), pal, habit, "Jané Doe", "CC BY 4.0").encode())
        assert got.palette == dataclasses.replace(pal, name=name)
        assert (got.look, got.habit, got.author, got.licence) == (looks.Look(), habit, "Jané Doe", "CC BY 4.0")


def test_a_look_file_holds_exactly_this(tmp_path):
    text = dumps("mine", looks.Look())
    assert text.startswith(HEAD_LINES) and text.endswith("\n")
    assert "/" not in text and "\\" not in text
    doc = tomllib.loads(text)
    assert list(doc) == ["aelook", "name", "look"]
    want = [f for f in looks.FIELDS if f != "preserve_exposure"]
    assert list(doc["look"]) == list(limits()) == want
    pal = _palettes()["teal_orange"]
    full = tomllib.loads(dumps("mine", looks.Look(), pal, {"landscape": {"sky": {"exposure": -0.3}}}, "A", "B"))
    assert list(full) == ["aelook", "name", "author", "licence", "look", "palette", "habit"]
    assert list(full["palette"]) == [*lookfile.PALETTE_LIMITS, "protected"]
    out = tmp_path / ("mine" + EXT)
    write(str(out), text)
    assert out.read_text(encoding="utf-8") == text
    assert os.listdir(tmp_path) == [out.name]
    with pytest.raises(ValueError):
        write(str(tmp_path / "mine.toml"), text)
    habit = {f"s{i}": {"sky": {"exposure": -0.3, "temp": 4.0}} for i in range(12)}
    big = dumps("a" * 40, looks.Look(), pal, habit, "x" * 200, "y" * 200)
    assert len(big.encode("utf-8")) < MAX_BYTES // 4


def test_every_look_field_has_a_limit():
    lim = limits()
    assert set(lim) | set(lookfile.NOT_CARRIED) == set(looks.FIELDS)
    for f in ("contrast_mul", "clarity_mul", "dehaze_mul", "curve_mul"):
        assert lim[f] == (0.0, 3.0)
    assert lim["exposure_mul"] == (0.0, 1.4)
    for f, op in (("vibrance_add", "vibrance"), ("saturation_add", "saturation"), ("curve_add", "curve"),
                  ("dehaze_add", "dehaze"), ("grain", "grain"), ("bw", "bw"), ("bw_blue", "bw_blue"),
                  ("grade_shadows_temp", "grade_shadows_temp")):
        assert lim[f] == ops.OP_BOUNDS[op], f
    for name, look in _library().items():
        for f, (lo, hi) in lim.items():
            assert lo <= getattr(look, f) <= hi, (name, f)
    for f, (lo, hi) in calibrate.load_config()["bounds"].items():
        assert lim[f][0] <= lo <= hi <= lim[f][1], f
    assert (MAX_BYTES, lookfile.VERSION, EXT) == (16 * 1024, 1, ".aelook")


def test_refuses_unknown_keys():
    msg = _refused('photo = "IMG_1.jpg"\n' + GOOD, "unknown key 'photo' in the file")
    assert "valid keys: aelook, name, author, licence, look, palette, habit" in msg
    msg = _refused(GOOD + "contrast_mull = 1\n", "unknown key 'contrast_mull' in [look]")
    assert "contrast_mul" in msg and "preserve_exposure" not in msg
    _refused(PAL + "hue = 3\n", "unknown key 'hue' in [palette]")
    _refused(GOOD + "\n[extra]\na = 1\n", "unknown key 'extra' in the file")
    for key in ("include", "source_path", "history", "lut", "exposure", "sharpen"):
        _refused(GOOD + f'{key} = "x"\n', f"unknown key '{key}' in [look]")
    msg = _refused(HEAD + "\n[look]\n" + "a" * 300 + " = 1\n", "unknown key")
    assert "a" * 40 in msg and "a" * 41 not in msg
    msg = _refused(HEAD + '"a\\u001bb" = 1\n[look]\n', "unknown key")
    assert "\x1b" not in msg


def test_refuses_numbers_outside_the_limits():
    for f, (lo, hi) in limits().items():
        part = f"{f} in [look] must be a number from {lo:g} to {hi:g}"
        for v in (hi + 0.01, lo - 0.01):
            msg = _refused(HEAD + f"[look]\n{f} = {v!r}\n", part)
            assert repr(v) not in msg
        for v in (hi, lo):
            assert getattr(parse((HEAD + f"[look]\n{f} = {v!r}\n").encode()).look, f) == v
    part = "curve_add in [look] must be a number from -100 to 100"
    for v in ("1e308", "-1e308", "1e400", "9" * 400, "-" + "9" * 400, "0x7fffffffffffffff", "1_000_000"):
        _refused(HEAD + f"[look]\ncurve_add = {v}\n", part)
    _refused(HEAD + "[look]\ncurve_add = " + "9" * 5000 + "\n", "not valid TOML")
    for v in ('"8"', "true", "[8]", "2020-01-01", "{a = 1}"):
        _refused(HEAD + f"[look]\ncurve_add = {v}\n", "must be a number")
    got = parse(GOOD.encode()).look.curve_add
    assert got == 8.0 and isinstance(got, float)


def test_refuses_nan_and_inf():
    for v in ("nan", "+nan", "-nan", "inf", "+inf", "-inf"):
        _refused(HEAD + f"[look]\ncurve_add = {v}\n", "curve_add in [look] must be a number from -100 to 100")
        _refused(HEAD + f"[look]\ncontrast_mul = {v}\n", "contrast_mul in [look] must be a number from 0 to 3")
        _refused(GOOD + f"[palette]\nmid_chroma = {v}\n", "mid_chroma in [palette] must be a number from 0 to 0.15")
        _refused(GOOD + f"[habit.landscape.sky]\nexposure = {v}\n", "finite")


def test_refuses_a_file_that_is_too_large(tmp_path):
    big = tmp_path / "big.aelook"
    big.write_bytes(GOOD.encode() + b"# " + b"x" * (10 * 1024 * 1024) + b"\n")
    with pytest.raises(ValueError) as e:
        read(str(big))
    assert str(e.value) == "big.aelook: larger than 16 kB, not read"
    base = GOOD.encode()
    pad = MAX_BYTES - len(base) - 3
    ok = base + b"# " + b"x" * pad + b"\n"
    assert len(ok) == MAX_BYTES
    ok_file = tmp_path / "ok.aelook"
    ok_file.write_bytes(ok)
    assert read(str(ok_file)).look.curve_add == 8.0
    ok_file.write_bytes(ok + b"x")
    with pytest.raises(ValueError):
        read(str(ok_file))
    _refused(ok + b"x", "larger than 16 kB, not read")


def test_refuses_names_that_look_like_paths():
    part = "name must be 1 to 40 lowercase letters, digits or underscores"
    for n in ("../evil", "a/b", "a\\b", "/abs", "C:evil", ".hidden", "evil.toml", "", "Evil", "a b", "é", "a" * 41):
        _refused(f"aelook = 1\nname = {_q(n)}\n[look]\n", part)
    _refused("aelook = 1\nname = 3\n[look]\n", part)
    _refused("aelook = 1\n[look]\n", part)
    for n in ("a_1", "a" * 40):
        assert parse(f"aelook = 1\nname = {_q(n)}\n[look]\n".encode()).name == n
    for n in ("../evil", "a/b", "Evil", "a\n", "", 3, None):
        with pytest.raises(ValueError):
            check_name(n)
    assert check_name("a_1") == "a_1"


def test_refuses_a_wrong_version():
    no = "no `aelook = 1` line: this is not an AutoEdit look file"
    for v in ("", 'aelook = "1"\n', "aelook = 1.0\n", "aelook = true\n", "aelook = [1]\n", "[build-system]\nrequires = []\n"):
        _refused(v + 'name = "mine"\n' if v.startswith("aelook") else v, no)
    part = "this AutoEdit reads look file format 1"
    for v in ("2", "0", "-1", "9" * 300):
        msg = _refused(f'aelook = {v}\nname = "mine"\n[look]\n', part)
        assert v not in msg
        msg = _refused(f'aelook = {v}\nname = "mine"\nphoto = "x"\n[look]\n', part)
        assert v not in msg


def test_refuses_a_bad_palette():
    for k, v in (("shadow_hue", 361), ("mid_hue", -1), ("highlight_chroma", 0.16), ("shadow_chroma", -0.01),
                 ("neutral_protection", 1.5), ("mid_hue", '"x"'), ("mid_hue", "1e308")):
        _refused(GOOD + f"[palette]\n{k} = {v}\n", f"{k} in [palette] must be a number from")
    _refused(GOOD + "[palette]\nstrength = 1\n", "unknown key 'strength' in [palette]")
    _refused(GOOD + '[palette]\nname = "x"\n', "unknown key 'name' in [palette]")
    part = "protected in [palette] must be a list of at most 8 concept names"
    for v in ('"skin"', '["skin", 3]', '["../x"]', "[" + ", ".join(f'"w{chr(97 + i)}"' for i in range(9)) + "]"):
        _refused(GOOD + f"[palette]\nprotected = {v}\n", part)
    _refused(HEAD + "palette = 3\n[look]\n", "[palette] must be a table")
    _refused(GOOD + "[[palette]]\nmid_hue = 1\n", "[palette] must be a table")
    pal = parse((GOOD + '[palette]\nshadow_hue = 360\nhighlight_chroma = 0.15\nprotected = ["blue sky", "snow"]\n').encode()).palette
    assert pal.name == "mine" and pal.shadow_hue == 360.0 and pal.highlight_chroma == 0.15
    assert pal.protected == ("blue sky", "snow")
    assert pal.mid_hue == 0.0 and pal.neutral_protection == 0.85


def test_refuses_what_is_not_a_look_file(tmp_path):
    _refused("", "no `aelook = 1` line")
    _refused("[look", "not valid TOML")
    _refused(b"\xff\xfe\x00\x01", "not a text file")
    _refused(b"aelook = 1\n\x00", "not valid TOML")
    _refused(GOOD + "curve_add = 9\n", "not valid TOML")
    _refused("[" * 8000, "not valid TOML")
    _refused("aelook = 1\nx = " + "{a = " * 2000 + "1" + "}" * 2000 + "\n", "not valid TOML")
    _refused(HEAD, "[look] must be a table")
    _refused(HEAD + "look = 3\n", "[look] must be a table")
    _refused(HEAD + "[[look]]\ncurve_add = 1\n", "[look] must be a table")
    for v in ("true", "false"):
        _refused(HEAD + f"[look]\npreserve_exposure = {v}\n", "preserve_exposure cannot be in a look file")
    for key, v in (("author", '"a\\nb"'), ("author", '"a\\u001bb"'), ("author", '"a\\u202eb"'), ("licence", _q("x" * 201)),
                   ("licence", '""'), ("author", "3")):
        _refused(f'{HEAD}{key} = {v}\n[look]\n', f"{key} must be one line of text")
    _refused(HEAD + 'license = "MIT"\n[look]\n', "unknown key 'license' in the file")
    _refused(GOOD + "habit = 3\n", "unknown key 'habit' in [look]")
    assert parse(b"\xef\xbb\xbf" + GOOD.replace("\n", "\r\n").encode()).look.curve_add == 8.0
    for fname, part in (("mine.txt", "mine.txt: a look file must end in .aelook"),
                        ("none.aelook", "none.aelook: look file not found")):
        with pytest.raises(ValueError) as e:
            read(str(tmp_path / fname))
        assert str(e.value) == part
    (tmp_path / "dir.aelook").mkdir()
    with pytest.raises(ValueError) as e:
        read(str(tmp_path / "dir.aelook"))
    assert str(e.value) == "dir.aelook: look file not found"
    (tmp_path / "UPPER.AELOOK").write_text(GOOD, encoding="utf-8")
    assert read(str(tmp_path / "UPPER.AELOOK")).name == "mine"


# ---------------------------------------------------------------------------------------------
# T2: export and import (tests 12 to 19)

import base64
import contextlib
import io
from dataclasses import asdict

import numpy as np
from PIL import Image as PILImage

from autoedit import batch, cli
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline

HAB2 = {"landscape": {"sky": {"exposure": -0.5}, "place:top": {"temp": 5.0}}}


def _run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


def _put(path, text):
    path = str(path)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _lib(prof):
    return looks.load(paths.config_paths(None, str(prof))["looks_path"])


def _pals(prof):
    return load_palettes(paths.config_paths(None, str(prof))["palettes_path"])


def _tree(root):
    out = {}
    for d, _dirs, fs in os.walk(str(root)):
        for f in fs:
            with open(os.path.join(d, f), "rb") as fh:
                out[os.path.relpath(os.path.join(d, f), str(root))] = fh.read()
    return out


def _scene(path, w=600, h=400, dark=1.0):
    a = np.zeros((h, w, 3), np.float32)
    a[: h * 55 // 100] = [100, 140, 200]
    a[h * 55 // 100:] = [60, 110, 70]
    yy, xx = np.mgrid[0:h, 0:w]
    a[(((xx - w / 2) / (w * 0.22)) ** 2 + ((yy - h * 0.62) / (h * 0.28)) ** 2) <= 1.0] = [200, 150, 120]
    a = a * dark + np.random.RandomState(3).normal(0, 3, a.shape)
    PILImage.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(str(path))
    return str(path)


def _process(prof, path, **kw):
    cfg = RunConfig(backend="stub", **paths.config_paths(None, str(prof)), **kw)
    return Pipeline.build(cfg).process_one(path, do_apply=True)


def _sorted_files(root):
    return sorted(_tree(root))


def test_export_then_import_gives_the_same_look(tmp_path):
    folder = tmp_path / "Client Folder"
    folder.mkdir()
    f = str(folder / "F.aelook")
    code, text = _run("looks", "export", "film_warm", "-o", f, "--palette", "teal_orange",
                      "--author", "Jané Doe", "--licence", "CC BY 4.0")
    assert code == 0, text
    assert "Client Folder" not in open(f, encoding="utf-8").read()
    prof = tmp_path / "P"
    code, text = _run("looks", "import", f, "--name", "mine", "--profile", str(prof))
    assert code == 0, text
    assert "Jané Doe" in text and "CC BY 4.0" in text and "--look mine --palette mine" in text
    packaged = looks.load(os.path.join(paths.configs_dir(), "looks.toml"))
    assert _lib(prof)["mine"] == packaged["film_warm"]
    tealo = load_palettes(os.path.join(paths.configs_dir(), "palettes.toml"))["teal_orange"]
    assert _pals(prof)["mine"] == dataclasses.replace(tealo, name="mine")
    assert set(paths.read_config(str(prof / "looks.toml"))["mine"]) == set(looks.FIELDS)
    # a look with a habit
    p1, p2 = tmp_path / "P1", tmp_path / "P2"
    cli.write_look(str(p1), "hab", asdict(packaged["natural"]), habit=HAB2)
    f1 = str(tmp_path / "hab.aelook")
    assert _run("looks", "export", "hab", "-o", f1, "--profile", str(p1))[0] == 0
    code, text = _run("looks", "import", f1, "--name", "hab2", "--profile", str(p2))
    assert code == 0 and "habits" in text
    assert _lib(p2)["hab2"] == _lib(p1)["hab"]
    assert looks.load_habits(paths.config_paths(None, str(p2))["looks_path"])["hab2"] == HAB2
    first = open(f1, encoding="utf-8").read()
    assert lookfile.export("hab2", None, str(p2))[0] == first.replace('name = "hab"', 'name = "hab2"')


def test_refuses_a_habit_outside_the_instruction_limits(tmp_path):
    cases = [("dehaze = 5", "[limits]"), ("exposure = -2", "exposure must be from -0.75 to 0.75"),
             ("saturation = 25.5", "saturation must be from -25 to 25")]
    bad = [("landscape", "sky", cases[0]), ("landscape", "sky", cases[1]), ("landscape", "sky", cases[2]),
           ("landscape", "place:nowhere", ("exposure = 0.1", "nowhere")),
           ("landscape", "mark:point:0.5:0.5", ("exposure = 0.1", "target")),
           ("landscape", "sky", ("warp = 1", "warp")),
           ("../x", "sky", ("exposure = 0.1", "scene"))]
    for i, (scene, target, (line, part)) in enumerate(bad):
        prof = tmp_path / f"P{i}"
        f = _put(tmp_path / f"b{i}.aelook", f'{GOOD}\n[habit."{scene}"."{target}"]\n{line}\n')
        code, text = _run("looks", "import", f, "--profile", str(prof))
        assert code == 2 and part in text, (i, text)
        assert not prof.exists()
    many = "".join(f"\n[habit.landscape.thing{i}]\nexposure = 0.1\n" for i in range(13))
    prof = tmp_path / "Pm"
    code, text = _run("looks", "import", _put(tmp_path / "many.aelook", GOOD + many), "--profile", str(prof))
    assert code == 2 and "more than 12" in text and not prof.exists()
    huge = _put(tmp_path / "huge.aelook", GOOD + "\n[habit.landscape.sky]\nexposure = " + "9" * 400 + "\n")
    code, text = _run("looks", "import", huge, "--profile", str(tmp_path / "Ph"))   # no OverflowError
    assert code == 2 and "exposure" in text and not (tmp_path / "Ph").exists()
    ok = _put(tmp_path / "ok.aelook", GOOD + '\n[habit.landscape.sky]\nexposure = -0.75\n'
              '\n[habit.landscape."place:top"]\ntemp = 50\n')
    prof = tmp_path / "Pok"
    assert _run("looks", "import", ok, "--profile", str(prof))[0] == 0
    got = looks.load_habits(paths.config_paths(None, str(prof))["looks_path"])["mine"]
    assert got == {"landscape": {"sky": {"exposure": -0.75}, "place:top": {"temp": 50.0}}}


def test_import_name_clashes(tmp_path):
    def imp(text, prof, *more):
        return _run("looks", "import", _put(tmp_path / "x.aelook", text), "--profile", str(prof), *more)

    for i, (name, pal, part) in enumerate([("natural", False, "a packaged look"), ("surprise", False, "a word of --look"),
                                           ("teal_orange", True, "a packaged palette"), ("auto", True, "a word of --palette"),
                                           ("none", True, "a word of --palette"), ("off", True, "a word of --palette")]):
        prof = tmp_path / f"P{i}"
        text = f'aelook = 1\nname = "{name}"\n\n[look]\ncurve_add = 8\n' + ("\n[palette]\nmid_hue = 200\n" if pal else "")
        for more in ((), ("--force",)):
            code, out = imp(text, prof, *more)
            assert code == 2 and part in out and "--name" in out, (name, out)
        code, out = imp(text.replace(f'name = "{name}"', 'name = "fine"'), prof, "--name", name)
        assert code == 2 and part in out
        assert not prof.exists()
    assert imp('aelook = 1\nname = "teal_orange"\n\n[look]\ncurve_add = 8\n', tmp_path / "Pt")[0] == 0
    prof = tmp_path / "P"
    assert imp(PAL, prof)[0] == 0
    before = _tree(prof)
    code, out = imp(PAL, prof)
    assert code == 2 and "the profile already has a look 'mine'" in out and "--force" in out
    assert _tree(prof) == before
    assert imp(PAL, prof, "--name", "second")[0] == 0
    changed = PAL.replace("curve_add = 8", "curve_add = 9").replace("mid_hue = 200", "mid_hue = 100")
    code, out = imp(changed, prof, "--force")
    assert code == 0 and "it replaced the look" in out and "it replaced the palette" in out
    assert _lib(prof)["mine"].curve_add == 9.0 and _pals(prof)["mine"].mid_hue == 100.0
    assert _lib(prof)["second"].curve_add == 8.0 and _pals(prof)["second"].mid_hue == 200.0
    assert "film_warm" in _lib(prof) and "teal_orange" in _pals(prof)
    assert _run("looks", "delete", "mine", "--profile", str(prof))[0] == 0
    before = _tree(prof)
    code, out = imp(PAL, prof)
    assert code == 2 and "the profile already has a palette 'mine'" in out
    assert _tree(prof) == before and "mine" not in _lib(prof)
    # a packaged name is refused also when the active configs folder lacks it (--configs-dir, ./configs)
    empty = tmp_path / "cfg"
    empty.mkdir()
    (empty / "looks.toml").write_text("")
    for name in ("natural", "film_warm"):
        code, out = _run("looks", "--configs-dir", str(empty), "import",
                         _put(tmp_path / "n.aelook", f'aelook = 1\nname = "{name}"\n\n[look]\ncurve_add = 8\n'),
                         "--profile", str(tmp_path / "Pc"))
        assert code == 2 and "a packaged look" in out, (name, out)
    assert not (tmp_path / "Pc").exists()


def test_import_changes_only_the_profile(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    prof = root / "P"
    cli.write_look(str(prof), "other", {"curve_add": 4.0})
    _put(prof / "defaults.toml", "# mine\n")
    big = _put(root / "big.aelook", GOOD + "# " + "x" * 10_000_000 + "\n")
    cases = [(GOOD + "photo = 1\n", ()), (None, ()), (GOOD, ("--name", "../evil")), (GOOD, ("--name", "a/b")),
             (GOOD, ("--name", "Evil"))]
    before = _tree(tmp_path)
    for text, more in cases:
        f = big if text is None else _put(root / "x.aelook", text)
        assert _run("looks", "import", f, "--profile", str(prof), *more)[0] == 2
        if os.path.exists(str(root / "x.aelook")):
            os.remove(str(root / "x.aelook"))
    assert _run("looks", "import", str(root / "missing.aelook"), "--profile", str(prof))[0] == 2
    os.remove(big)
    after = _tree(tmp_path)
    assert after == {k: v for k, v in before.items() if not k.endswith("big.aelook")}
    assert not any("evil" in k.lower() for k in after)
    packaged = _tree(paths.configs_dir())
    before = _tree(prof)
    f = _put(root / "pal.aelook", PAL)
    code, out = _run("looks", "import", f, "--profile", str(prof))
    assert code == 0 and "comments in it are gone" in out
    after = _tree(prof)
    assert sorted(k for k in after if after[k] != before.get(k)) == ["looks.toml", "palettes.toml"]
    assert _lib(prof)["other"].curve_add == 4.0
    assert _tree(paths.configs_dir()) == packaged
    keep = {k: os.environ.get(k) for k in ("AUTOEDIT_PROFILE", "XDG_CONFIG_HOME", "APPDATA")}
    try:
        os.environ["AUTOEDIT_PROFILE"] = ""
        os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"] = str(tmp_path / "nowhere")
        seen = _tree(tmp_path)
        code, out = _run("looks", "import", f)
        assert code == 2 and "profiles are switched off" in out
        assert _tree(tmp_path) == seen and not (tmp_path / "nowhere").exists()
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_a_look_from_someone_else_stays_inside_the_limits(tmp_path):
    lim = limits()
    body = {k: hi for k, (lo, hi) in lim.items()}
    body["bw"] = 0.0
    f = _put(tmp_path / "wild.aelook", 'aelook = 1\nname = "mine"\n\n[look]\n' + "".join(f"{k} = {v!r}\n" for k, v in body.items()))
    prof = tmp_path / "P"
    assert _run("looks", "import", f, "--profile", str(prof))[0] == 0
    mine = _lib(prof)["mine"]
    for k, (lo, hi) in lim.items():
        assert lo <= getattr(mine, k) <= hi, k
    assert mine.preserve_exposure is False
    photo = _scene(tmp_path / "s.png")
    _, base = _process(prof, photo)
    assert not base.needs_review
    rgb, res = _process(prof, photo, look="mine")
    plain, _ = _process(prof, photo)
    assert np.isfinite(rgb).all() and rgb.min() >= 0 and rgb.max() <= 1 and not np.array_equal(rgb, plain)
    assert res.needs_review and res.quality_score < 0.8
    assert any(n.startswith("oversaturation gate") for n in res.notes), res.notes
    assert set(res.global_settings) <= set(ops.OP_BOUNDS)


def test_a_look_file_cannot_switch_off_the_darkness_check(tmp_path):
    f = _put(tmp_path / "m.aelook", 'aelook = 1\nname = "mine"\n\n[look]\nexposure_mul = 0.0\ncurve_add = 60.0\n')
    prof = tmp_path / "P"
    assert _run("looks", "import", f, "--profile", str(prof))[0] == 0
    assert _run("looks", "save", "own", "--from", "mine", "--set", "preserve_exposure=true", "--profile", str(prof))[0] == 0
    photo = _scene(tmp_path / "d.png", dark=0.35)
    _, a = _process(prof, photo, look="mine")
    assert a.needs_review and "dark source made darker" in a.review_reason, (a.review_reason, a.quality_score)
    _, b = _process(prof, photo, look="own")
    assert not b.needs_review
    out = tmp_path / "x.aelook"
    code, text = _run("looks", "export", "own", "-o", str(out), "--profile", str(prof))
    assert code == 0 and "not in the file: preserve_exposure" in text
    assert "preserve_exposure" not in open(str(out), encoding="utf-8").read()
    prof2 = tmp_path / "P2"
    assert _run("looks", "import", str(out), "--name", "back", "--profile", str(prof2))[0] == 0
    back = _lib(prof2)["back"]
    assert back.preserve_exposure is False and back == _lib(prof)["mine"]


def test_export_refuses_what_import_would_refuse(tmp_path):
    prof = tmp_path / "P"
    prof.mkdir()
    cli.write_look(str(prof), "hab", {}, habit={"landscape": {"sky": {"exposure": -2.0}}})
    _put(prof / "looks.toml", open(str(prof / "looks.toml"), encoding="utf-8").read()
         + '\n[wild]\ncontrast_mul = 9\n\n["../x"]\ncurve_add = 1\n')
    cwd = os.getcwd()
    work = tmp_path / "w" / "inner"
    work.mkdir(parents=True)
    os.chdir(str(work))
    try:
        P = ["--profile", str(prof)]
        cases = [(["wild"], "contrast_mul in [look] must be a number from 0 to 3"), (["nope"], "look 'nope' not found"),
                 (["../x"], "look name"), (["hab"], "exposure must be from -0.75 to 0.75"),
                 (["natural", "--palette", "nope"], "palette 'nope' not found"),
                 (["natural", "--author", "a" * 201], "author must be one line"),
                 (["natural", "--licence", "a\nb"], "licence must be one line"),
                 (["natural", "-o", "natural.txt"], "must end in .aelook")]
        for argv, part in cases:
            code, text = _run("looks", "export", *argv, *P)
            assert code == 2 and part in text, (argv, text)
        assert _tree(tmp_path / "w") == {}
        code, text = _run("looks", "export", "natural", *P)
        assert code == 0 and "wrote natural.aelook" in text
        assert _sorted_files(tmp_path / "w") == [os.path.join("inner", "natural.aelook")]
        assert _run("looks", "export", "natural", *P)[0] == 0
    finally:
        os.chdir(cwd)


def test_default_output_is_unchanged(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _scene(src / "a.jpg")
    prof = tmp_path / "P"
    f = _put(tmp_path / "m.aelook", PAL)
    assert _run("looks", "import", f, "--profile", str(prof))[0] == 0

    def go(name, **kw):
        cfg = RunConfig(backend="stub", workers=1, **kw)
        batch.run(cfg, str(src), str(tmp_path / name), workers=1)
        return [_tree(tmp_path / name)[k] for k in sorted(_tree(tmp_path / name)) if k.endswith(".jpg")]

    a = go("a")
    b = go("b", **paths.config_paths(None, str(prof)))
    c = go("c", look="mine", palette="mine", **paths.config_paths(None, str(prof)))
    assert a and a == b and a != c


# ---- F28 T3: the look card (lookcard.py), tests 20 to 24 -------------------------------------------------

import re

from autoedit import lookcard


def _photos(tmp_path, n):
    d = tmp_path / "Client_Folder"
    d.mkdir()
    for i in range(n):
        _scene(d / f"IMG_PRIVATE_{i}.png", 600, 450)
    return str(d)


def _cells(png, n):
    im = np.asarray(PILImage.open(png).convert("RGB")).astype(np.int32)
    out = []
    for i in range(n):
        y = 60 + i * 396
        out.append((im[y:y + 360, 12:492], im[y:y + 360, 504:984]))
    return out


def _page_jpegs(text):
    return [PILImage.open(io.BytesIO(base64.b64decode(m))) for m in re.findall(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", text)]


def test_card_renders_with_the_stub(tmp_path):
    folder = _photos(tmp_path, 3)
    out = str(tmp_path / "out")
    r = lookcard.render("film_faded", [folder], out, RunConfig(backend="stub"))
    assert r["photos"] == 3 and r["backend"] == "stub"
    assert isinstance(r["flagged"], list) and all(isinstance(n, int) and isinstance(t, str) for n, t in r["flagged"])
    assert sorted(os.listdir(out)) == ["film_faded.html", "film_faded.png"]
    png = PILImage.open(r["png"])
    assert png.mode == "RGB" and png.size == (996, 1272) and not png.info
    with open(r["html"], encoding="utf-8") as fh:
        page = fh.read()
    jpegs = _page_jpegs(page)
    assert len(jpegs) == 6 and all(j.size == (600, 450) for j in jpegs)
    for i in range(3):
        d = np.abs(np.asarray(jpegs[2 * i].convert("RGB"), np.int32) - np.asarray(jpegs[2 * i + 1].convert("RGB"), np.int32))
        assert d.mean() > 2
    bare = re.sub(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", "", page)
    for part in ("<h1>film_faded</h1>", "segmenter: stub", "default-src 'none'"):
        assert part in bare
    with open(r["png"], "rb") as fh:
        raw = fh.read()
    for bad in ("IMG_PRIVATE", "Client_Folder", folder, "<script", "http://", "https://", "dehaze", "curve"):
        assert bad not in bare and bad.encode() not in raw, bad
    for before, after in _cells(r["png"], 3):
        assert np.abs(before - after).mean() > 0


def test_card_shows_what_the_look_carries(tmp_path):
    folder = _photos(tmp_path, 2)
    prof = tmp_path / "P"
    prof.mkdir()
    _put(prof / "looks.toml", "[plain]\ncurve_add = 5.0\n\n[grainy]\ncurve_add = 5.0\ngrain = 100\n")
    conf = paths.config_paths(None, str(prof))

    def card(name, sub, **kw):
        return lookcard.render(name, [folder], str(tmp_path / sub), RunConfig(backend="stub", **conf, **kw), None)["png"]

    bw = _cells(card("bw_classic", "bw"), 2)
    for before, after in bw:
        assert (after.max(axis=-1) - after.min(axis=-1)).max() == 0
        assert (before.max(axis=-1) - before.min(axis=-1)).max() > 30
    plain, grainy = _cells(card("plain", "plain"), 2), _cells(card("grainy", "grainy"), 2)
    for (b1, a1), (b2, a2) in zip(plain, grainy):
        assert np.array_equal(b1, b2)
        assert np.abs(a1 - a2).mean() > 1
    tinted = _cells(card("plain", "pal", palette="teal_orange"), 2)
    assert np.abs(tinted[0][1] - plain[0][1]).mean() > 0.5


def test_card_without_photos_shows_the_drawn_frame(tmp_path):
    f = lookcard.sample_frame()
    assert f.shape == (800, 1200, 3) and f.dtype == np.float32 and f.min() >= 0 and f.max() <= 1
    assert np.array_equal(f, lookcard.sample_frame())
    seen = []
    real = Pipeline.build.__func__

    def spy(cls, cfg, *a, **k):
        seen.append(cfg)
        return real(cls, cfg, *a, **k)

    Pipeline.build = classmethod(spy)
    try:
        for name in _library():
            out = str(tmp_path / name)
            r = lookcard.render(name, [], out, RunConfig(backend="stub", decider="llm", allow_cloud=True))
            assert r["photos"] == 0 and r["flagged"] == []
            assert len(os.listdir(out)) == 2
            assert seen[-1].decider == "rules" and seen[-1].look == name
            assert PILImage.open(r["png"]).size == (996, 480)
            with open(r["html"], encoding="utf-8") as fh:
                assert len(_page_jpegs(fh.read())) == 2
    finally:
        Pipeline.build = classmethod(real)


def test_card_takes_at_most_twelve_photos(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for i in range(30):
        PILImage.new("RGB", (16, 12), (i, 9, 9)).save(str(d / f"p{i:02d}.png"))
    (d / "notes.txt").write_text("x")
    got = lookcard.pick([str(d)])
    assert len(got) == 12 == len(set(got)) and got == sorted(got)
    assert got[0].endswith("p00.png") and got[-1].endswith("p29.png")
    one = str(d / "p03.png")
    assert lookcard.pick([one, one]) == [one, one]
    assert lookcard.pick([]) == []
    assert len(lookcard.pick([one, str(d)])) == 12
    empty = tmp_path / "empty"
    empty.mkdir()
    for bad in (str(d / "notes.txt"), str(tmp_path / "missing.png"), str(empty)):
        with pytest.raises(ValueError):
            lookcard.pick([bad])


def test_card_cli(tmp_path):
    photo = os.path.join(_photos(tmp_path, 1), "IMG_PRIVATE_0.png")
    out = tmp_path / "o1"
    code, text = _run("looks", "card", "film_warm", photo, "-o", str(out), "--backend", "stub")
    assert code == 0 and "film_warm.png" in text and "film_warm.html" in text and "segmenter: stub" in text
    out2 = tmp_path / "o2"
    code, text = _run("looks", "card", "natural", "-o", str(out2), "--backend", "stub", "--palette", "teal_orange")
    assert code == 0 and "drawn sample frame" in text
    assert sorted(os.listdir(out2)) == ["natural.html", "natural.png"]
    assert len(os.listdir(out)) + len(os.listdir(out2)) == 4
    prof = tmp_path / "P"
    prof.mkdir()
    _put(prof / "looks.toml", '["../../escaped"]\ncurve_add = 1.0\n')
    for argv, part in ((["nope"], "look 'nope' not found"), (["natural", str(tmp_path / "missing.png")], "not a photo or a folder"),
                       (["../../escaped"], "look name")):
        o = tmp_path / "o3" / "x"
        code, text = _run("looks", "card", *argv, "-o", str(o), "--backend", "stub", "--profile", str(prof))
        assert code == 2 and part in text, (argv, text)
        assert not o.exists()
    assert not any("escaped" in n for _d, _s, fs in os.walk(str(tmp_path)) for n in fs)
