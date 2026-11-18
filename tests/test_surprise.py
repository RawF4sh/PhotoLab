"""F26: surprise me. The pick (variants.py), the API (web/service.py) and `--look surprise --seed N`."""

import contextlib
import io
import json
import os
import random
import shutil

import pytest
from PIL import Image as PILImage
import numpy as np

from autoedit import cli, looks as looks_mod, paths, variants
from autoedit.contract import RunConfig
from autoedit.web import service

NAMES = ["natural", "dramatic", "punchy", "soft_portrait", "cinematic_preserved", "film_warm", "film_cool",
         "film_faded", "instant", "cross_process", "bw_classic", "bw_soft", "bw_red_filter"]
ORDER_7 = ["film_warm", "cinematic_preserved", "instant", "bw_red_filter", "cross_process", "film_cool",
           "soft_portrait", "bw_soft", "dramatic", "film_faded", "natural", "punchy", "bw_classic"]
SIX = {
    0: ["instant", "film_faded", "cinematic_preserved", "punchy", "film_warm", "bw_soft"],
    7: ORDER_7[:6],
    42: ["natural", "cross_process", "bw_classic", "instant", "cinematic_preserved", "bw_soft"],
}


def _scene(path, w=300, h=200):
    a = np.zeros((h, w, 3), np.float32)
    a[: h * 55 // 100] = [100, 140, 200]
    a[h * 55 // 100:] = [60, 110, 70]
    yy, xx = np.mgrid[0:h, 0:w]
    a[(((xx - w / 2) / (w * 0.22)) ** 2 + ((yy - h * 0.62) / (h * 0.28)) ** 2) <= 1.0] = [200, 150, 120]
    PILImage.fromarray(a.astype(np.uint8), "RGB").save(path, quality=95)


def _folder(tmp_path, name, photos=("a.jpg",)):
    d = tmp_path / name
    d.mkdir()
    for p in photos:
        _scene(str(d / p))
    return d


def _run(*flags):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["run", *flags, "--backend", "stub"])
    return code, out.getvalue() + err.getvalue()


def _bytes(p):
    with open(p, "rb") as fh:
        return fh.read()


def _side(p):
    with open(p) as fh:
        return json.load(fh)


def _library(cfg=None):
    return looks_mod.load((cfg or RunConfig()).looks_path)


def test_same_seed_same_six_golden():
    for seed, six in SIX.items():
        assert variants.surprise_looks(NAMES, seed) == six
    assert variants.surprise_looks(NAMES, 7, count=99) == ORDER_7
    assert variants.surprise_looks(NAMES, 8) != SIX[7]
    rng = random.Random(1)
    for _ in range(20):
        c = list(NAMES)
        rng.shuffle(c)
        assert variants.surprise_looks(c, 7) == SIX[7]
    assert variants.surprise_looks(set(NAMES), 7) == SIX[7]
    assert variants.surprise_looks({n: 1 for n in reversed(NAMES)}, 7) == SIX[7]


def test_six_different_fewer_when_the_library_is_small():
    sixes = set()
    for seed in range(50):
        got = variants.surprise_looks(NAMES, seed)
        assert len(got) == 6 and len(set(got)) == 6 and set(got) <= set(NAMES)
        sixes.add(tuple(got))
    assert len(sixes) > 40
    assert variants.SURPRISE_COUNT == 6
    assert sorted(variants.surprise_looks(["a", "b", "c"], 1)) == ["a", "b", "c"]
    assert variants.surprise_looks([], 1) == []
    assert variants.surprise_looks(["a"], 1, skip=("a",)) == []


def test_a_look_added_or_skipped_keeps_the_order_of_the_others():
    for seed in (0, 7, 42):
        old = variants.surprise_looks(NAMES, seed, count=99)
        assert [n for n in variants.surprise_looks(NAMES + ["mine"], seed, count=99) if n != "mine"] == old
        for gone in ("natural", old[0], old[5]):
            assert variants.surprise_looks(NAMES, seed, count=99, skip=(gone,)) == [n for n in old if n != gone]
    assert variants.surprise_looks(NAMES, 42, skip=("natural",)) == [
        "cross_process", "bw_classic", "instant", "cinematic_preserved", "bw_soft", "punchy"]


def test_the_seed_is_checked():
    for s in (0, 7, 4294967295):
        assert variants.surprise_seed(s) == s
    for _ in range(20):
        d = variants.surprise_seed(None)
        assert isinstance(d, int) and 0 <= d < 1_000_000
    for bad in (True, -1, 4294967296, "7", 7.0, [7]):
        with pytest.raises(ValueError):
            variants.surprise_seed(bad)


def test_the_pick_for_a_run_config():
    lib = _library()
    n = min(6, len(lib) - 1)
    got = variants.surprise_for(RunConfig(), 7)
    assert len(got) == n and len(set(got)) == n and set(got) <= set(lib)
    assert got == variants.surprise_looks(lib, 7, skip=("natural",))
    assert got == variants.surprise_for(RunConfig(), 7)
    first = got[0]
    assert variants.surprise_for(RunConfig(look=first), 7) == variants.surprise_looks(lib, 7, skip=(first,))
    assert "dramatic" not in variants.surprise_for(RunConfig(style="dramatic"), 7)
    assert variants.surprise_for(RunConfig(look="surprise"), 7) == got
    assert "surprise" not in lib


def test_the_api_gives_six_looks(tmp_path):
    photo = str(_folder(tmp_path, "in") / "a.jpg")
    base = {"backend": "stub", "surprise": True, "seed": 7}
    r = service.make_variants(photo, base)
    want = variants.surprise_for(service.make_cfg({"backend": "stub"}), 7)
    assert r["seed"] == 7 and r["looks"] == want
    assert sorted(v["name"] for v in r["variants"]) == sorted(want)
    afters = []
    for v in r["variants"]:
        assert v["opts"] == {"look": v["name"]}
        assert v["after"].startswith("data:image/jpeg;base64,")
        afters.append(v["after"])
    assert len(set(afters)) == len(afters)
    assert r["best"] in want
    r2 = service.make_variants(photo, base)
    assert r2["looks"] == r["looks"]
    assert {v["name"]: v["after"] for v in r2["variants"]} == {v["name"]: v["after"] for v in r["variants"]}
    r3 = service.make_variants(photo, {**base, "look": want[0]})
    assert r3["looks"] == variants.surprise_looks(_library(), 7, skip=(want[0],))
    r4 = service.make_variants(photo, {"backend": "stub", "surprise": True})
    assert 0 <= r4["seed"] < 1_000_000
    assert r4["looks"] == variants.surprise_for(service.make_cfg({"backend": "stub"}), r4["seed"])
    r5 = service.make_variants(photo, base, with_images=False)
    assert all("after" not in v for v in r5["variants"])
    for bad in (-1, "7", True):
        with pytest.raises(ValueError):
            service.make_variants(photo, {**base, "seed": bad})
    with pytest.raises(ValueError):
        service.make_variants(photo, {**base, "variants": [{"name": "x"}]})


def test_variants_without_surprise_are_unchanged(tmp_path):
    photo = str(_folder(tmp_path, "in") / "a.jpg")
    for extra in ({}, {"seed": 7}, {"surprise": False}):
        r = service.make_variants(photo, {"backend": "stub", **extra}, with_images=False)
        assert set(r) == {"image", "best", "review", "variants"}
        assert sorted(v["name"] for v in r["variants"]) == sorted(s["name"] for s in variants.DEFAULT_VARIANTS)
    for cfg in (RunConfig(), RunConfig(look="punchy")):
        assert cli._surprise(cfg, None) is cfg
        assert cli._surprise(cfg, 7) is cfg


def test_the_cli_run_takes_the_first_and_the_sidecar_names_it(tmp_path):
    src = _folder(tmp_path, "in", photos=("a.jpg", "b.jpg"))
    o = {k: str(tmp_path / k) for k in ("o1", "o2", "o3", "o4", "o5")}
    code, text = _run(str(src), o["o1"], "--look", "surprise", "--seed", "7")
    picks = variants.surprise_for(RunConfig(), 7)
    assert code == 0
    assert f"surprise, seed 7: {', '.join(picks)}" in text and f"this run uses {picks[0]}" in text
    for photo in ("a", "b"):
        path = tmp_path / "o1" / f"{photo}.autoedit.json"
        assert _side(path)["audit"]["run_config"]["look"] == picks[0]
        assert "surprise" not in path.read_text()
    assert _run(str(src), o["o2"], "--look", "surprise", "--seed", "7")[0] == 0
    assert _run(str(src), o["o3"], "--look", picks[0])[0] == 0
    assert _run(str(src), o["o4"])[0] == 0
    run_file = tmp_path / "run.toml"
    run_file.write_text('[run]\nlook = "surprise"\n')
    assert _run(str(src), o["o5"], "--config", str(run_file), "--seed", "7")[0] == 0
    o1 = _bytes(tmp_path / "o1" / "a.jpg")
    assert o1 == _bytes(tmp_path / "o2" / "a.jpg") == _bytes(tmp_path / "o3" / "a.jpg")
    assert o1 == _bytes(tmp_path / "o5" / "a.jpg")
    assert o1 != _bytes(tmp_path / "o4" / "a.jpg")


def test_the_cli_refuses_what_it_cannot_repeat(tmp_path):
    src = _folder(tmp_path, "in")
    code, text = _run(str(src), str(tmp_path / "o1"), "--look", "surprise")
    assert code == 2 and "--seed" in text and not (tmp_path / "o1").exists()
    code, text = _run(str(src), str(tmp_path / "o2"), "--look", "surprise", "--seed", "-1")
    assert code == 2 and not (tmp_path / "o2").exists()
    cdir = tmp_path / "configs"
    shutil.copytree(paths._packaged(), str(cdir))
    (cdir / "looks.toml").write_text("[natural]\n")
    code, text = _run(str(src), str(tmp_path / "o3"), "--look", "surprise", "--seed", "7",
                      "--configs-dir", str(cdir))
    assert code == 2 and "no other look" in text and not (tmp_path / "o3").exists()
    with pytest.raises(ValueError):
        service.make_variants(str(src / "a.jpg"), {"backend": "stub", "surprise": True, "seed": 7},
                              configs_dir=str(cdir))


def test_the_word_is_never_a_run_setting(tmp_path):
    src = _folder(tmp_path, "in")
    photo = str(src / "a.jpg")
    with pytest.raises(ValueError) as e:
        service.make_cfg({"look": "surprise"})
    assert "/variants" in str(e.value)
    with pytest.raises(ValueError):
        service.preview(photo, {"backend": "stub", "look": "surprise"})
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        service.run_one(photo, str(out), {"backend": "stub", "look": "surprise"})
    assert not out.exists()
    third = service.make_variants(photo, {"backend": "stub", "surprise": True, "seed": 7},
                                  with_images=False)["looks"][2]
    r = service.run_one(photo, str(tmp_path / "out2"), {"backend": "stub", "look": third})
    assert r["result"]["audit"]["run_config"]["look"] == third
    text = open(r["sidecar"]).read()
    assert _side(r["sidecar"])["audit"]["run_config"]["look"] == third and "surprise" not in text
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "looks.toml").write_text("[surprise]\ncontrast_mul = 1.1\n")
    cfg = service.make_cfg({"look": "surprise"}, profile=str(prof))
    assert cfg.look == "surprise"
    assert cli._surprise(cfg, None) is cfg
    assert "surprise" in variants.surprise_looks(looks_mod.load(cfg.looks_path), 7, count=99)
