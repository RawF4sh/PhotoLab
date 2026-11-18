"""F30 T1: genre packs (genres.toml), opt-in rule sets a genre requires."""

import json
import os
import shutil

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, batch, genres, paths, rules
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline

_CONF = os.path.join(os.path.dirname(paths.__file__), "configs")
_SEVEN = ["sky", "mountain", "snow", "grass", "trees", "water", "rock"]
_TIMING = ("elapsed_s", "timings", "stage_timings_ms", "timing_ms")


def _configs(tmp_path):
    d = str(tmp_path / "configs")
    shutil.copytree(_CONF, d)
    with open(os.path.join(d, "genres.toml"), "a") as fh:
        fh.write('\n[demo]\npreset = "wedding"\nlook = "punchy"\nrule_sets = ["demo"]\n'
                 'reveal = ["plate"]\n\n[demo.run]\ndenoise = true\nintensity = 0.5\nconsistency = "bursts"\n')
    with open(os.path.join(d, "rules.toml"), "a") as fh:
        for scene in ("people", "landscape", "other"):
            fh.write(f'\n[opt_in.demo.{scene}.global]\nvibrance = "9"\n')
    return d


def _photos(d, n=2):
    d.mkdir()
    for i in range(n):
        rng = np.random.default_rng(i)
        a = np.zeros((128, 96, 3), np.float32)
        a[:64] = [100, 140, 200]
        a[64:] = [170, 120, 90]
        a = np.clip(a + rng.integers(0, 20, a.shape), 0, 255).astype(np.uint8)
        PILImage.fromarray(a, "RGB").save(str(d / f"p{i}.jpg"), quality=95)
    return str(d)


def _write(tmp_path, name, text):
    p = str(tmp_path / name)
    with open(p, "w") as fh:
        fh.write(text)
    return p


def _batch(tmp_path, tag, **kw):
    out = str(tmp_path / tag)
    batch.run(RunConfig(backend="stub", **kw), str(tmp_path / "in"), out, workers=1)
    return out


def _side(out, name="p0"):
    with open(os.path.join(out, f"{name}.autoedit.json")) as fh:
        d = json.load(fh)
    for k in _TIMING:
        d.pop(k, None)
    return d


def _bytes(out, name="p0.jpg"):
    with open(os.path.join(out, name), "rb") as fh:
        return fh.read()


def test_packaged_packs_golden():
    path = os.path.join(_CONF, "genres.toml")
    g = genres.load(path)
    assert list(g) == ["landscape", "alpine", "astro"]
    assert genres.run_kwargs("landscape", {}) == {"preset": "landscape"}
    assert genres.run_kwargs("alpine", {}) == {"preset": "landscape", "highlight_hold": True}
    assert genres.run_kwargs("astro", {}) == {"preset": "landscape", "look": "astro", "night_sky": True, "denoise": True}
    opt_in = paths._load(os.path.join(_CONF, "rules.toml")).get("opt_in", {})
    kinds = paths._load(os.path.join(_CONF, "reveal.toml"))["kind"]
    for name, body in g.items():
        assert body["tested"] is False and body["description"]   # see the comment in genres.toml
        assert all(s in opt_in for s in body.get("rule_sets", []))
        assert all(k in kinds for k in body["reveal"])
        pipe = Pipeline.build(RunConfig(backend="stub", **genres.run_kwargs(name, {})))
        assert pipe.concepts_for(None) == _SEVEN
        assert pipe.highlight_hold == ((0.80, 0.985) if name == "alpine" else None)


def test_default_is_unchanged(tmp_path):
    assert RunConfig().genre is None and RunConfig().rule_sets == []
    path = os.path.join(_CONF, "rules.toml")
    assert rules.load(path) == rules.load(path, require=())
    _photos(tmp_path / "in")
    out = _batch(tmp_path, "a")
    run_cfg = _side(out)["audit"]["run_config"]
    assert run_cfg["genre"] is None and run_cfg["rule_sets"] == []
    assert not any(n.startswith("genre") for n in _side(out)["notes"])
    d = RunConfig().model_dump()
    d["genre"] = "alpine"
    assert RunConfig(genre="alpine").model_dump() == d


def test_a_bad_file_is_refused(tmp_path):
    bad = {
        "x = 1": "table",
        "[a]\nb = 1": "unknown key",
        "[Bad-Name]\ntested = true": "Bad-Name",
        '[a]\ntested = "yes"': "tested",
        "[a]\npreset = 3": "preset",
        '[a]\nrule_sets = "a"': "rule_sets",
        '[a]\nreveal = ["../x"]': "reveal",
        f'[a]\ndescription = "{"x" * 201}"': "description",
        "[a]\nrun = 1": "run",
    }
    for key in ("backend", "api_key", "allow_cloud", "decider", "out_subdir", "reference", "preset"):
        bad[f'[a.run]\n{key} = "x"'] = "run." + key
    for line in ("intensity = 7", "intensity = nan", 'denoise = "yes"', 'intensity = "0.5"', 'consistency = "x"'):
        bad[f"[a.run]\n{line}"] = "run." + line.split(" ")[0]
    for i, (text, word) in enumerate(bad.items()):
        p = _write(tmp_path, f"g{i}.toml", text + "\n")
        with pytest.raises(ValueError) as e:
            genres.load(p)
        assert p in str(e.value) and word in str(e.value), (text, str(e.value))
    with pytest.raises(ValueError) as e:
        genres.load(_write(tmp_path, "k.toml", "[a]\nbogus = 1\n"))
    assert all(k in str(e.value) for k in genres.KEYS)
    with pytest.raises(ValueError) as e:
        genres.load(_write(tmp_path, "r.toml", '[a.run]\nbackend = "x"\n'))
    assert all(k in str(e.value) for k in genres.RUN_KEYS)
    assert genres.load(str(tmp_path / "missing.toml")) == {}
    d = _configs(tmp_path)
    with open(os.path.join(d, "genres.toml"), "a") as fh:
        fh.write('\n[nope]\npreset = "zzz"\n\n[nolook]\npreset = "landscape"\nlook = "yyy"\n')
    kw = paths.config_paths(d)
    with pytest.raises(ValueError) as e:
        genres.run_kwargs("nope", kw)
    assert "zzz" in str(e.value)
    with pytest.raises(ValueError) as e:     # the look is checked on its own, not only after a bad preset
        genres.run_kwargs("nolook", kw)
    assert "yyy" in str(e.value)
    with pytest.raises(ValueError):          # a list from a run file is a refusal, not a TypeError
        genres.run_kwargs(["alpine"], kw)
    with open(os.path.join(d, "genres.toml"), "a") as fh:
        fh.write('\n[norules]\nrule_sets = ["levelz"]\n')
    with pytest.raises(ValueError) as e:
        Pipeline.build(RunConfig(backend="stub", **kw, **genres.run_kwargs("norules", kw)))
    assert "levelz" in str(e.value) and "levels" in str(e.value)


def test_rule_sets_join_the_rules(tmp_path):
    d = _configs(tmp_path)
    path = os.path.join(d, "rules.toml")
    assert rules.load(path, require=["demo"])["landscape"]["global"]["vibrance"] == "9"
    assert rules.load(path)["landscape"]["global"].get("vibrance") != "9"
    assert "opt_in" not in rules.load(path, require=["demo"])
    with pytest.raises(ValueError) as e:
        rules.load(path, require=["demo", "nope"])
    assert all(w in str(e.value) for w in ("nope", "demo", "levels"))
    loose = _write(tmp_path, "loose.toml", "[opt_in.loose.quality_gate]\ndark_regress = 0.5\n")
    with pytest.raises(ValueError) as e:
        rules.load(loose, require=["loose"])
    assert "loose" in str(e.value) and loose in str(e.value)
    assert "opt_in" not in rules.load(loose)
    _photos(tmp_path / "in")
    kw = paths.config_paths(d)
    plain = _batch(tmp_path, "plain", **kw)
    with_set = _batch(tmp_path, "set", rule_sets=["demo"], **kw)
    sp, ss = _side(plain), _side(with_set)
    assert ss["audit"]["run_config"]["rule_sets"] == ["demo"]
    assert ss["audit"]["config_digest"] == sp["audit"]["config_digest"]
    assert _bytes(plain) != _bytes(with_set)
    before = _bytes(with_set)
    r = adjust.rerender(os.path.join(with_set, "p0.autoedit.json"), configs_dir=d)
    assert _bytes(with_set) == before
    assert not (r.get("warnings") if isinstance(r, dict) else None)


# ---- T2: the command line, the API, the tone report ----

def _cli_args(configs=None, **kw):
    from types import SimpleNamespace
    base = dict(configs_dir=configs, config=None, profile=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _cfg(configs=None, **kw):
    from autoedit import cli
    return cli.load_run_config(_cli_args(configs, **kw))


def _cli_run(tmp_path, tag, *flags, configs=None):
    from autoedit import cli
    out = str(tmp_path / tag)
    base = ["run", str(tmp_path / "in"), out, "--backend", "stub", "--workers", "1"]
    if configs:
        base += ["--configs-dir", configs]
    assert cli.main(base + list(flags)) == 0
    return out


def test_genre_landscape_is_the_preset_byte_for_byte(tmp_path):
    _photos(tmp_path / "in")
    a = _cli_run(tmp_path, "a", "--preset", "landscape")
    b = _cli_run(tmp_path, "b", "--genre", "landscape")
    c = _cli_run(tmp_path, "c")
    assert _bytes(a) == _bytes(b) and _bytes(a) != _bytes(c)
    sa, sb = _side(a), _side(b)
    assert sb["audit"]["run_config"].pop("genre") == "landscape"
    assert sa["audit"]["run_config"].pop("genre") is None
    sb["notes"].remove("genre: landscape")
    assert sa == sb


def test_what_the_user_wrote_wins(tmp_path):
    from autoedit import cli
    d = _configs(tmp_path)
    c = _cfg(d, genre="demo")
    assert (c.preset, c.look, c.rule_sets, c.denoise, c.intensity, c.consistency) == (
        "wedding", "punchy", ["demo"], True, 0.5, "bursts")
    c = _cfg(d, genre="demo", intensity=0.9, look="natural", preset="landscape", consistency="brightness")
    assert (c.intensity, c.look, c.preset, c.consistency, c.denoise) == (0.9, "natural", "landscape", "brightness", True)
    c = _cfg(d, genre="demo", style="dramatic")
    assert c.look is None and c.preset == "wedding"
    run = _write(tmp_path, "run.toml", '[run]\ngenre = "demo"\nintensity = 0.3\ndenoise = false\n')
    c = _cfg(d, config=run)
    assert (c.intensity, c.denoise, c.preset) == (0.3, False, "wedding")
    assert _cfg(d, config=run, genre="landscape").preset == "landscape"
    assert _cfg(d, config=run, genre="landscape").genre == "landscape"
    default = _cfg(d)
    only = _write(tmp_path, "only.toml", '[run]\ngenre = "demo"\n')
    assert _cfg(d, config=only).preset == "wedding"
    assert _cfg(d, config=only, genre="").model_dump() == default.model_dump()
    run2 = _write(tmp_path, "run2.toml", '[run]\ngenre = "demo"\nstyle = "dramatic"\n')
    assert _cfg(d, config=run2).look is None
    c = _cfg(d, genre="demo", export="web")
    assert c.preset == "wedding" and c.out_format == "jpeg" and c.out_long_edge == 2048
    prof = tmp_path / "prof"
    prof.mkdir()
    (prof / "defaults.toml").write_text('[run]\ngenre = "demo"\ndenoise = false\n')
    c = _cfg(d, profile=str(prof))
    assert c.preset == "wedding" and c.denoise is False
    with pytest.raises(ValueError) as e:
        _cfg(d, genre="nope")
    assert "landscape" in str(e.value)
    _photos(tmp_path / "in")
    assert cli.main(["run", str(tmp_path / "in"), str(tmp_path / "o"), "--backend", "stub",
                     "--genre", "nope"]) == 2
    cd = _configs(tmp_path / "x")
    with open(os.path.join(cd, "defaults.toml")) as fh:
        text = fh.read()
    with open(os.path.join(cd, "defaults.toml"), "w") as fh:
        fh.write(text.replace("[run]\n", '[run]\ngenre = "demo"\n', 1))
    assert _cfg(cd).preset == "wedding"
    assert _cfg(cd, intensity=0.9).intensity == 0.9
    assert _cfg(cd, genre="").genre is None and _cfg(cd, genre="").preset is None


def test_api_field_and_listing():
    from autoedit import settings_schema
    from autoedit.web import service
    c = service.make_cfg({"genre": "alpine"})
    assert c.preset == "landscape" and c.highlight_hold is True and c.genre == "alpine"
    assert service.make_cfg({"genre": "alpine", "highlight_hold": False}).highlight_hold is False
    assert service.make_cfg({"genre": None}).model_dump() == service.make_cfg({}).model_dump()
    assert service.make_cfg({"genre": ""}).model_dump() == service.make_cfg({}).model_dump()
    assert service.make_cfg({"rule_sets": ["x"]}).rule_sets == []
    with pytest.raises(ValueError):
        service.make_cfg({"genre": "nope"})
    rows = service.list_genres()
    assert [r["name"] for r in rows] == ["landscape", "alpine", "astro"]
    assert set(rows[1]) == set(genres.listing()[1])
    a = rows[1]
    assert (a["tested"], a["origin"], a["offered"], a["concepts"], a["run"]) == (
        False, "packaged", False, _SEVEN, {"highlight_hold": True})
    ch = next(s for s in settings_schema.schema()["settings"] if s["key"] == "genre")["choices"]
    assert ch == []
    try:
        from fastapi.testclient import TestClient
        from autoedit.web import app as webapp
    except Exception:
        return
    cl = TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")
    for url in ("/api/v1/genres", "/api/genres"):
        assert cl.get(url).json() == rows


def test_api_with_demo(tmp_path):
    from autoedit.web import service
    d = _configs(tmp_path)
    c = service.make_cfg({"genre": "demo"}, d)
    assert (c.look, c.preset, c.rule_sets, c.denoise) == ("punchy", "wedding", ["demo"], True)
    c = service.make_cfg({"genre": "demo", "style": "dramatic", "export": "web", "concepts": "sky"}, d)
    assert c.look is None and c.out_long_edge == 2048 and c.concepts == ["sky"] and c.preset == "wedding"


def test_a_genre_run_and_its_replay(tmp_path):
    d = _configs(tmp_path)
    _photos(tmp_path / "in")
    g = _cli_run(tmp_path, "g", "--genre", "demo", configs=d)
    f = _cli_run(tmp_path, "f", "--preset", "wedding", "--look", "punchy", "--intensity", "0.5",
                 "--consistency", "bursts", "--denoise", configs=d)
    sg = _side(g)
    assert _bytes(g) != _bytes(f)
    rc = sg["audit"]["run_config"]
    assert (rc["genre"], rc["preset"], rc["look"], rc["rule_sets"], rc["denoise"]) == (
        "demo", "wedding", "punchy", ["demo"], True)
    assert "genre: demo" in sg["notes"]
    before = _bytes(g)
    os.remove(os.path.join(d, "genres.toml"))
    r = adjust.rerender(os.path.join(g, "p0.autoedit.json"), configs_dir=d)
    assert _bytes(g) == before
    assert not (r.get("warnings") if isinstance(r, dict) else None)


def _profile_with(tmp_path):
    prof = tmp_path / "prof"
    prof.mkdir()
    (prof / "genres.toml").write_text('[mine]\npreset = "landscape"\n[alpine]\nlook = "dramatic"\n')
    return str(prof)


def test_profile_genres(tmp_path):
    from autoedit.web import service
    from autoedit import settings_schema
    prof = _profile_with(tmp_path)
    packaged = open(os.path.join(_CONF, "genres.toml"), "rb").read()
    p = paths.config_paths(None, prof)
    merged = genres.load(p["genres_path"])
    base = genres.load(os.path.join(_CONF, "genres.toml"))
    assert list(merged) == ["landscape", "alpine", "astro", "mine"]
    assert {k: v for k, v in merged["alpine"].items() if base["alpine"].get(k) != v} == {"look": "dramatic"}
    rows = {r["name"]: r for r in genres.listing(None, prof)}
    assert [rows[n]["origin"] for n in ("landscape", "alpine", "mine")] == [
        "packaged", "packaged, changed by the profile", "profile"]
    assert rows["mine"]["tested"] is False and rows["mine"]["offered"] is True
    assert _cfg(None, profile=prof, genre="mine").preset == "landscape"
    assert service.make_cfg({"genre": "mine"}, None, prof).preset == "landscape"
    ch = lambda pr: next(s for s in settings_schema.schema(None, pr)["settings"] if s["key"] == "genre")["choices"]
    assert "mine" in ch(prof) and "mine" not in ch(None)
    assert open(os.path.join(_CONF, "genres.toml"), "rb").read() == packaged
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "genres.toml").write_text('[mine.run]\nbackend = "sam3"\n')
    with pytest.raises(ValueError) as e:
        paths.config_paths(None, str(bad))
    assert str(bad / "genres.toml") in str(e.value) and "backend" in str(e.value)


def test_an_untested_pack_is_listed_and_not_offered(tmp_path):
    import contextlib
    import io
    from autoedit import cli, settings_schema
    d = _configs(tmp_path)
    with open(os.path.join(d, "genres.toml")) as fh:
        text = fh.read()
    with open(os.path.join(d, "genres.toml"), "w") as fh:     # a tested pack beside the untested ones
        fh.write(text.replace("tested = false", "tested = true", 1))
    rows = {r["name"]: r for r in genres.listing(d)}
    assert rows["demo"]["tested"] is False and rows["demo"]["offered"] is False
    assert rows["alpine"]["offered"] is False and rows["astro"]["offered"] is False and rows["landscape"]["offered"] is True
    ch = next(s for s in settings_schema.schema(d)["settings"] if s["key"] == "genre")["choices"]
    assert ch == ["landscape"]
    assert genres.run_kwargs("demo", paths.config_paths(d))["denoise"] is True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(["genres", "list", "--configs-dir", d]) == 0
    out = buf.getvalue()
    assert "demo  (packaged, untested)" in out and "landscape  (packaged, tested)" in out and "alpine  (packaged, untested)" in out
    assert "highlight_hold=true" in out and rows["alpine"]["description"] in out
    with open(os.path.join(d, "genres.toml"), "a") as fh:
        fh.write("\n[broken]\nbogus = 1\n")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        assert cli.main(["genres", "list", "--configs-dir", d]) == 2
    assert "broken" in err.getvalue()


def test_a_configs_folder_from_before_f30_gets_the_packaged_genres(tmp_path):
    d = str(tmp_path / "configs")
    shutil.copytree(_CONF, d)
    os.remove(os.path.join(d, "genres.toml"))
    assert "genres_path" not in paths.config_paths(d)
    c = _cfg(d, genre="alpine")
    assert c.preset == "landscape" and c.highlight_hold is True
    assert [r["name"] for r in genres.listing(d)] == ["landscape", "alpine", "astro"]


def test_the_tone_report_takes_a_genre():
    import importlib.util
    import pathlib
    from types import SimpleNamespace
    tp = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "tone_report.py"
    spec = importlib.util.spec_from_file_location("tone_report_f30", str(tp))
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    a = SimpleNamespace(backend="stub", genre="alpine", raw_mode=None, style=None, intensity=None)
    assert tr._render_kwargs(a, {}) == {"backend": "stub", "preset": "landscape", "highlight_hold": True,
                                        "genre": "alpine"}
    assert tr._render_kwargs(a, {"highlight_hold": False})["highlight_hold"] is False
    a.genre = None
    assert tr._render_kwargs(a, {}) == {"backend": "stub"}
    errs = []
    assert tr._parse_sets(["genre=alpine", "levels=true"], errs.append) == {"levels": True}
    assert len(errs) == 1 and "--genre alpine" in errs[0]
