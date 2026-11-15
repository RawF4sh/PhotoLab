"""F11: the user config layer (profile folder merged over the packaged config files)."""

import glob
import os
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, batch, cli, looks, paths
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline


def _profile(tmp_path, **files):
    d = tmp_path / "profile"
    d.mkdir(exist_ok=True)
    for name, text in files.items():
        (d / f"{name}.toml").write_text(text)
    return str(d)


def _args(**kw):
    base = dict(configs_dir=None, config=None, profile=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _env(**values):
    """Set environment variables, returning what to restore."""
    old = {k: os.environ.get(k) for k in values}
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return old


def _restore(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _photos(folder):
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(3)
    for i in range(3):
        a = np.zeros((96, 128, 3), np.uint8)
        a[:48] = [100, 140, 200]
        a[48:] = [60, 110, 70]
        a = np.clip(a + rng.integers(0, 20, a.shape), 0, 255).astype(np.uint8)
        PILImage.fromarray(a, "RGB").save(os.path.join(folder, f"p{i}.jpg"), quality=95)


def _images(folder):
    return {os.path.basename(p): open(p, "rb").read() for p in sorted(glob.glob(os.path.join(folder, "*.jpg")))}


def test_no_profile_changes_nothing():
    base = paths.configs_dir()
    before = list(paths._TMP)
    plain = paths.config_paths(base)
    assert paths.config_paths(base, None) == plain
    assert plain["looks_path"] == os.path.join(base, "looks.toml")
    assert paths._TMP == before            # no temporary folder was made
    cfg = cli.load_run_config(_args())
    assert cfg == RunConfig(**{**cli._flatten(cli._read_toml(os.path.join(base, "defaults.toml"))), **plain})


def test_override_one_key(tmp_path):
    prof = _profile(tmp_path, looks="[natural]\ncontrast_mul = 1.3\n")
    base = paths.config_paths()["looks_path"]
    before = open(base, "rb").read()
    merged = looks.load(paths.config_paths(None, prof)["looks_path"])
    packaged = looks.load(base)
    assert merged["natural"].contrast_mul == 1.3
    assert merged["natural"].vibrance_add == packaged["natural"].vibrance_add
    assert {k: v for k, v in merged.items() if k != "natural"} == {k: v for k, v in packaged.items() if k != "natural"}
    assert open(base, "rb").read() == before


def test_add_one_look(tmp_path):
    prof = _profile(tmp_path, looks="[mine]\ncontrast_mul = 1.1\nvibrance_add = 4\n")
    merged = looks.load(paths.config_paths(None, prof)["looks_path"])
    assert set(merged) == set(looks.load(paths.config_paths()["looks_path"])) | {"mine"}
    src = str(tmp_path / "in")
    _photos(src)
    cfg = RunConfig(**paths.config_paths(None, prof), backend="stub", look="mine")
    m = batch.run(cfg, src, str(tmp_path / "out"))
    assert m["processed"] == 3 and m["failed"] == 0


def test_reject_a_bad_file(tmp_path):
    def err(text):
        prof = _profile(tmp_path, looks=text)
        with pytest.raises(ValueError) as e:
            paths.config_paths(None, prof)
        return str(e.value), os.path.join(prof, "looks.toml")

    msg, where = err("[natural\ncontrast_mul = 1")
    assert where in msg
    msg, where = err("[natural]\ncontrast_mull = 1.3\n")
    assert "contrast_mull" in msg and "contrast_mul" in msg and "vibrance_add" in msg and where in msg
    msg, where = err('natural = "x"\n')
    assert "natural" in msg and where in msg
    (tmp_path / "profile" / "looks.toml").unlink()
    with open(paths.config_paths()["styles_path"], "rb") as fh:
        style = next(iter(tomllib.load(fh)))
    prof = _profile(tmp_path, styles=f'{style} = 3\n')   # a value where the packaged file has a table
    with pytest.raises(ValueError) as e:
        paths.config_paths(None, prof)
    assert style in str(e.value) and "styles.toml" in str(e.value)


def test_dump_toml_roundtrip():
    for f in glob.glob(os.path.join(paths._packaged(), "*.toml")):
        with open(f, "rb") as fh:
            data = tomllib.load(fh)
        assert tomllib.loads(paths.dump_toml(data)) == data, f
    odd = {"top": 1, "a b": {"x": 'he said "hi"\\ and é', "e": {}, "ll": [[1, 2], [3]],
                             "f": 0.1, "t": True, "n": {"deep": {"v": -2.5}}}}
    assert tomllib.loads(paths.dump_toml(odd)) == odd
    with pytest.raises(TypeError):
        paths.dump_toml({"a": None})


def test_profile_defaults(tmp_path):
    prof = _profile(tmp_path, defaults='[run]\nstyle = "dramatic"\n')
    assert cli.load_run_config(_args(profile=prof)).style == "dramatic"
    assert cli.load_run_config(_args(profile=prof, style="natural")).style == "natural"
    assert cli.load_run_config(_args()).style != "dramatic"


def test_profile_resolution(tmp_path):
    home = tmp_path / "xdg"
    (home / "autoedit").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError):
        paths.profile_dir(str(tmp_path / "missing"))
    assert paths.profile_dir(str(other)) == str(other)
    old = _env(AUTOEDIT_PROFILE=str(other), XDG_CONFIG_HOME=str(home), APPDATA=str(home))
    try:
        assert paths.profile_dir() == str(other)
        os.environ["AUTOEDIT_PROFILE"] = ""
        assert paths.profile_dir() is None
        del os.environ["AUTOEDIT_PROFILE"]
        assert paths.profile_dir() == str(home / "autoedit")
    finally:
        _restore(old)


def test_merged_files_follow_changes(tmp_path):
    prof = _profile(tmp_path, looks="[natural]\ncontrast_mul = 1.3\n")
    first = paths.config_paths(None, prof)["looks_path"]
    stamp = os.stat(first).st_mtime_ns
    assert paths.config_paths(None, prof)["looks_path"] == first
    assert os.stat(first).st_mtime_ns == stamp          # nothing changed: not rewritten
    over = os.path.join(prof, "looks.toml")
    with open(over, "w") as fh:
        fh.write("[natural]\ncontrast_mul = 1.7\n")
    later = os.stat(over).st_mtime + 5
    os.utime(over, (later, later))
    second = paths.config_paths(None, prof)["looks_path"]
    assert looks.load(second)["natural"].contrast_mul == 1.7


def test_looks_commands(tmp_path):
    prof = str(tmp_path / "newprofile")
    old = _env(AUTOEDIT_PROFILE="", XDG_CONFIG_HOME=str(tmp_path / "nohome"), APPDATA=str(tmp_path / "nohome"))
    try:
        run = lambda *a: cli.main(["looks", "--profile", prof, *a])  # noqa: E731
        assert run("save", "warm2", "--from", "natural", "--set", "vibrance_add=6") == 0
        assert os.path.isfile(os.path.join(prof, "looks.toml"))
        merged = looks.load(paths.config_paths(None, prof)["looks_path"])
        assert merged["warm2"].vibrance_add == 6.0
        assert run("save", "other", "--set", "contrast_mul=1.2") == 0
        assert {"warm2", "other"} <= set(looks.load(paths.config_paths(None, prof)["looks_path"]))
        assert run("list") == 0
        after = lambda *a: cli.main(["looks", *a, "--profile", prof])  # noqa: E731  the option after the action
        assert after("save", "late", "--set", "curve_add=3") == 0
        assert "late" in looks.load(paths.config_paths(None, prof)["looks_path"])
        assert after("list") == 0 and after("delete", "late") == 0
        assert "late" not in looks.load(paths.config_paths(None, prof)["looks_path"])
        assert run("delete", "warm2") == 0
        assert "warm2" not in looks.load(paths.config_paths(None, prof)["looks_path"])
        assert run("delete", "natural") == 2
        assert run("save", "bad", "--set", "nope=1") == 2
        assert run("save", "bad", "--set", "contrast_mul=abc") == 2
        assert run("save", "Bad Name") == 2
    finally:
        _restore(old)


def test_digest_sees_the_profile(tmp_path):
    prof = _profile(tmp_path, looks="[natural]\ncontrast_mul = 1.3\n")
    plain = Pipeline.build(RunConfig(**paths.config_paths(), backend="stub")).config_digest
    one = Pipeline.build(RunConfig(**paths.config_paths(None, prof), backend="stub")).config_digest
    assert one != plain
    over = os.path.join(prof, "looks.toml")
    with open(over, "w") as fh:
        fh.write("[natural]\ncontrast_mul = 1.5\n")
    later = os.stat(over).st_mtime + 5
    os.utime(over, (later, later))
    assert Pipeline.build(RunConfig(**paths.config_paths(None, prof), backend="stub")).config_digest not in (one, plain)


def test_workers_and_adjust_use_the_profile(tmp_path):
    prof = _profile(tmp_path, looks="[mine]\ncontrast_mul = 1.4\n[natural]\ncontrast_mul = 1.4\n")
    src = str(tmp_path / "in")
    _photos(src)
    one, two = str(tmp_path / "one"), str(tmp_path / "two")
    for out, w in ((one, 1), (two, 2)):
        cfg = RunConfig(**paths.config_paths(None, prof), backend="stub", look="mine", workers=w)
        batch.run(cfg, src, out)
    assert _images(one) == _images(two) and len(_images(one)) == 3
    cfg = RunConfig(**paths.config_paths(None, prof), backend="stub", look="natural")
    batch.run(cfg, src, str(tmp_path / "nat"))
    img = os.path.join(str(tmp_path / "nat"), "p0.jpg")
    before = open(img, "rb").read()
    r = adjust.rerender(img, source=os.path.join(src, "p0.jpg"), profile=prof)
    assert open(img, "rb").read() == before and r["warnings"] == []
    r = adjust.rerender(img, source=os.path.join(src, "p0.jpg"))
    assert any("config files differ" in w for w in r["warnings"])


def test_saved_look_replaces_a_packaged_one(tmp_path):
    prof = str(tmp_path / "newprofile")
    old = _env(AUTOEDIT_PROFILE="", XDG_CONFIG_HOME=str(tmp_path / "nohome"), APPDATA=str(tmp_path / "nohome"))
    try:
        run = lambda *a: cli.main(["looks", "--profile", prof, *a])  # noqa: E731
        assert looks.load(paths.config_paths()["looks_path"])["natural"].curve_add == 5.0
        assert run("save", "natural", "--set", "curve_add=0") == 0
        merged = looks.load(paths.config_paths(None, prof)["looks_path"])
        assert merged["natural"].curve_add == 0
        # a table with every field: the packaged values cannot come back
        with open(os.path.join(prof, "looks.toml"), "rb") as fh:
            assert set(tomllib.load(fh)["natural"]) == set(looks.FIELDS)
        assert run("save", "dramatic", "--from", "natural") == 0
        merged = looks.load(paths.config_paths(None, prof)["looks_path"])
        assert merged["dramatic"] == merged["natural"]
    finally:
        _restore(old)


def test_a_look_value_must_be_finite(tmp_path):
    for bad in ("nan", "inf", "-inf"):
        prof = _profile(tmp_path, looks=f"[natural]\ncurve_add = {bad}\n")
        with pytest.raises(ValueError) as e:
            paths.config_paths(None, prof)
        assert "curve_add" in str(e.value) and "natural" in str(e.value)


def test_looks_save_with_profiles_switched_off(tmp_path):
    home = tmp_path / "xdg"
    old = _env(AUTOEDIT_PROFILE="", XDG_CONFIG_HOME=str(home), APPDATA=str(home))
    try:
        assert cli.main(["looks", "save", "x"]) == 2
        with pytest.raises(ValueError):
            paths.default_profile_dir()
    finally:
        _restore(old)
    assert not home.exists()


def test_an_overlay_that_cannot_be_written_names_its_file(tmp_path):
    for text in ("[[x]]\na = 1\n", "when = 2020-01-01\n"):
        prof = _profile(tmp_path, rules=text)
        with pytest.raises(ValueError) as e:
            paths.config_paths(None, prof)
        assert os.path.join(prof, "rules.toml") in str(e.value)
