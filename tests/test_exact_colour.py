"""F34, the neutral pick (T1) and the clean backdrop (T2).

T1: a spot that is grey or white, and the white balance that makes it neutral.

The file makes its own pictures: a made-up product photo (`_product`) with a grey card, under a colour cast.
Levels are of 255. No pytest feature beyond `raises`: it runs under tests/run_all.py too.
"""

import json
import math
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image as PILImage, ImageFilter

from autoedit import adjust, backdrop, batch, cli, emit, neutral, paths, rules as rules_mod, stats
from autoedit.contract import RunConfig
from autoedit.edit import ops, par
from autoedit.imageio import _to_u8, linear_to_srgb, srgb_to_linear
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.web import service

CARD = [0.12, 0.85]
CASTS = {"warm": (1.10, 1.0, 0.88), "cool": (0.90, 1.0, 1.12), "green": (0.97, 1.06, 0.97),
         "magenta": (1.03, 0.94, 1.03), "strong": (1.22, 1.0, 0.84)}


def _product(h=600, w=900, cast=(1.0, 1.0, 1.0), seed=0, noise=0.004, tilt=0.10, bowl=0.25, level=0.90, gain=1.0,
             card=True):
    """A made-up product photo in gamma 0..1: a light backdrop, a box (red band, green above, blue below) with a
    white label, a shadow under it, a grey card of 0.50 at CARD. The linear values get `cast` and `gain`."""
    v, u = np.meshgrid(np.linspace(0, 1, h), np.linspace(0, 1, w), indexing="ij")
    a = lambda x: np.abs(x - 0.5)   # noqa: E731
    back = level * (1 - bowl * ((u - .5) ** 2 + (v - .5) ** 2) - tilt * (v - .5) - 0.06 * (u - .5))
    img = np.repeat(back[..., None], 3, axis=2)
    box = (a(u) < 0.16) & (a(v) < 0.22)
    img[box & (a(v) < 0.22)] = (0.55, 0.20, 0.18)
    img[box & (v < 0.42)] = (0.20, 0.42, 0.24)
    img[box & (v > 0.60)] = (0.18, 0.25, 0.55)
    img[(a(u) < 0.07) & (a(v) < 0.05)] = 0.93
    img[~box] *= (1 - 0.25 * np.exp(-(((u - .5) / 0.20) ** 2 + ((v - .75) / 0.035) ** 2)))[~box][:, None]
    if card:
        img[(np.abs(u - CARD[0]) < 0.04) & (np.abs(v - CARD[1]) < 0.05)] = 0.50
    lin = srgb_to_linear(np.clip(img, 0, 1).astype(np.float32)).astype(np.float64)
    lin = lin * np.array(cast) * gain
    out = linear_to_srgb(lin).astype(np.float64) + np.random.RandomState(seed).normal(0.0, noise, img.shape)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _save(path, rgb):
    PILImage.fromarray((np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGB").save(str(path))
    return str(path)


def _card(rgb):
    """The card's channel means of a picture, of 255 (the middle of the card: no edge, no JPEG block of it)."""
    h, w = rgb.shape[:2]
    y, x = int(CARD[1] * h), int(CARD[0] * w)
    return rgb[y - int(0.03 * h):y + int(0.03 * h), x - int(0.025 * w):x + int(0.025 * w)].reshape(-1, 3).mean(axis=0) * 255.0


def _off(rgb):
    c = _card(rgb)
    return float(c.max() - c.min())


def _decoded(path):
    return np.asarray(PILImage.open(path).convert("RGB"), np.float32) / 255.0


def _render(tmp_path, name, rgb, **cfg_kw):
    path = _save(tmp_path / f"{name}.png", rgb)
    pipe = Pipeline.build(RunConfig(backend="stub", out_format="png", **cfg_kw))
    out, result = pipe.process_one(path, do_apply=True)
    return pipe, out, result, path


# 1 ----------------------------------------------------------------------------------------------------------------

def test_wb_correction_solves_the_op():
    for lin in ((0.26, 0.25, 0.22), (0.22, 0.25, 0.27), (0.25, 0.26, 0.25), (0.27, 0.25, 0.26)):
        temp, tint = stats.wb_correction(*lin)
        got = ops.light(linear_to_srgb(np.array([[lin]], np.float32)), temp, tint)
        got = srgb_to_linear(got)[0, 0]
        assert abs(temp) < 100 and abs(tint) < 100
        assert float(got.max() - got.min()) < 1e-4
    t, n = stats.wb_correction(1.0, 0.5, 0.1)
    assert t == -100.0 and abs(n + 80.0) < 1e-9
    t, n = stats.wb_correction(1.0, 0.5, 0.1, limit=math.inf)
    assert abs(t + 409.09) < 0.01 and abs(n + 636.36) < 0.01
    assert stats.wb_correction(0.3, 0.3, 0.3) == (0.0, 0.0)


# 2 ----------------------------------------------------------------------------------------------------------------

def test_parse_and_box():
    assert neutral.parse("0.42,0.61") == [0.42, 0.61]
    assert neutral.parse(" 0 , 1 ") == [0.0, 1.0]
    for bad in ("0.4", "a,b", "1.2,0", "nan,0.5", "0.1,0.2,0.3", "-0.1,0.5", ""):
        with pytest.raises(ValueError) as e:
            neutral.parse(bad)
        assert str(e.value).startswith("--neutral needs X,Y as shares of the frame, each from 0 to 1")
    assert neutral.box((1000, 1500), (0.5, 0.5), 0.02) == (485, 516, 735, 766)
    assert neutral.box((1000, 1500), (0, 0), 0.02) == (0, 16, 0, 16)
    y0, y1, x0, x1 = neutral.box((1000, 1500), (1, 1), 0.02)
    assert (y1, x1) == (1000, 1500) and y0 < y1 and x0 < x1
    y0, y1, x0, x1 = neutral.box((40, 60), (0.5, 0.5), 0.02)
    assert (y1 - y0, x1 - x0) == (3, 3)


# 3 ----------------------------------------------------------------------------------------------------------------

def test_measure_refuses_what_it_cannot_use():
    pick, why = neutral.measure(_product(noise=0.0), CARD)
    assert pick is not None and why == ""
    assert abs(pick.temp) < 0.5 and abs(pick.tint) < 0.5
    assert pick.source == (127.5, 127.5, 127.5)
    pick, why = neutral.measure(np.full((200, 300, 3), 0.99, np.float32), (0.5, 0.5))
    assert pick is None and "blown" in why
    pick, why = neutral.measure(np.full((200, 300, 3), 0.03, np.float32), (0.5, 0.5))
    assert pick is None and "too dark" in why
    red = np.empty((200, 300, 3), np.float32)
    red[:] = (0.7, 0.2, 0.2)
    pick, why = neutral.measure(red, (0.5, 0.5))
    assert pick is None and "not grey" in why and "temp -" in why
    pick, why = neutral.measure(_product(noise=0.0, cast=(1.6, 1.0, 0.6)), CARD)
    assert pick is None and "not grey" in why


# 4 ----------------------------------------------------------------------------------------------------------------

def test_picked_spot_is_neutral_through_apply_ops():
    more = {"exposure": 0.3, "shadows": 20.0, "contrast": 15.0, "curve": 10.0, "vibrance": 20.0, "sharpen": 30.0}
    for name, cast in {"plain": (1.0, 1.0, 1.0), **CASTS}.items():
        img = _product(cast=cast)
        pick, why = neutral.measure(img, CARD)
        assert pick is not None, (name, why)
        base = {"temp": pick.temp, "tint": pick.tint}
        assert _off(ops.apply_ops(img, base)) < 0.1, name
        assert _off(ops.apply_ops(img, {**base, **more})) < 0.25, name


# 5 ----------------------------------------------------------------------------------------------------------------

def test_picked_spot_is_neutral_in_the_export(tmp_path):
    for name, cast in CASTS.items():
        pipe, out, result, path = _render(tmp_path, name, _product(cast=cast), neutral=CARD)
        a = result.audit["neutral"]
        assert {"x", "y", "source", "result", "off", "temp", "tint", "rules"} <= set(a)
        assert (a["x"], a["y"]) == (0.12, 0.85) and len(a["source"]) == 3 and len(a["result"]) == 3
        assert a["off"] <= 1 and a["temp"] == round(result.global_settings["temp"], 3)
        assert a["tint"] == round(result.global_settings["tint"], 3)
        assert any(n.startswith("neutral pick at 0.120, 0.850: white balance temp ") for n in result.notes), name
        for fmt, ext in (("png", ".png"), ("jpeg", ".jpg")):
            cfg = pipe.cfg.model_copy(update={"out_format": fmt})
            dest = str(tmp_path / f"{name}-out{ext}")
            emit.write_outputs(out, result, dest, str(tmp_path / f"{name}.autoedit.json"), cfg, path)
            assert _off(_decoded(dest)) < 1.0, (name, fmt)
        assert _off(_decoded(path)) > 3.0, name


# 6 ----------------------------------------------------------------------------------------------------------------

def test_pick_replaces_the_white_balance_of_the_rules_and_the_regions(tmp_path):
    cool = _product(cast=CASTS["cool"])
    _, _, plain, path = _render(tmp_path, "cool", cool)
    assert plain.global_settings["temp"] > 5
    _, _, picked, _ = _render(tmp_path, "cool", cool, neutral=CARD)
    pick, _ = neutral.measure(_decoded(path), CARD, neutral.DEFAULTS)
    a = picked.audit["neutral"]
    assert picked.global_settings["temp"] == pick.temp and picked.global_settings["tint"] == pick.tint
    assert a["rules"] == [round(plain.global_settings["temp"], 3), round(plain.global_settings.get("tint", 0.0), 3)]
    rest = lambda r: {k: v for k, v in r.global_settings.items() if k not in ("temp", "tint")}   # noqa: E731
    assert rest(picked) == rest(plain)

    warm = _product(cast=CASTS["warm"])
    _, _, plain, _ = _render(tmp_path, "warm", warm)
    assert any("temp" in m.ops for m in plain.masks)          # the stub's skin has temp
    _, _, picked, _ = _render(tmp_path, "warm", warm, neutral=CARD)
    assert not any("temp" in m.ops or "tint" in m.ops for m in picked.masks)
    assert any(n.startswith("neutral pick: temp of ") and n.endswith(" dropped, the pick sets the white balance")
               for n in picked.notes)
    _, _, typed, _ = _render(tmp_path, "warm", warm, neutral=CARD, edit="make it warmer")
    assert typed.global_settings["temp"] > picked.global_settings["temp"]


# 7 ----------------------------------------------------------------------------------------------------------------

def test_a_spot_that_cannot_be_used_changes_nothing(tmp_path):
    img = _product(gain=1.3)               # the label is clipped to white: a spot nothing can be solved on
    _, plain_px, plain, _ = _render(tmp_path, "x", img)
    for spot in ([0.5, 0.5], [0.5, 0.35]):          # blown; green and not grey
        _, px, result, _ = _render(tmp_path, "x", img, neutral=spot)
        assert np.array_equal(px, plain_px) and result.global_settings == plain.global_settings
        assert "neutral" not in result.audit
        notes = [n for n in result.notes if n.startswith("neutral pick")]
        assert len(notes) == 1 and notes[0].startswith(f"neutral pick at {spot[0]:.3f}, {spot[1]:.3f}: not used, ")


# 8 ----------------------------------------------------------------------------------------------------------------

def test_wb_given_for_the_run(tmp_path):
    for name, cast in (("warm", CASTS["warm"]), ("cool", CASTS["cool"])):
        img = _product(cast=cast)
        _, _, plain, _ = _render(tmp_path, name, img)
        _, _, r, _ = _render(tmp_path, name, img, wb=[-20, 5])
        assert (r.global_settings["temp"], r.global_settings["tint"]) == (-20.0, 5.0)
        assert set(r.audit["neutral"]) == {"temp", "tint", "rules"}
        assert r.audit["neutral"]["rules"] == [round(plain.global_settings.get("temp", 0.0), 3),
                                               round(plain.global_settings.get("tint", 0.0), 3)]
        assert any(n.startswith("neutral pick: white balance temp -20.0, tint +5.0 (the rules: ")
                   and n.endswith("given for the run") for n in r.notes)
    img = _product(cast=CASTS["warm"])
    _, _, spot, _ = _render(tmp_path, "spot", img, neutral=CARD, wb=[-20, 5])
    assert "x" in spot.audit["neutral"] and spot.global_settings["temp"] != -20.0       # the spot wins
    _, _, lost, _ = _render(tmp_path, "lost", _product(cast=CASTS["warm"], gain=1.3), neutral=[0.5, 0.5], wb=[-20, 5])
    assert lost.global_settings["temp"] == -20.0 and set(lost.audit["neutral"]) == {"temp", "tint", "rules"}
    assert any("not used" in n for n in lost.notes)                                     # and says so


# 9 ----------------------------------------------------------------------------------------------------------------

def _chart(cast):
    rs = np.random.RandomState(3)
    img = np.full((800, 1200, 3), 0.80, np.float32)
    cells = [(r, c) for r in range(6) for c in range(10)]
    for i, (r, c) in enumerate(cells):
        y, x = 40 + r * 100, 60 + c * 110
        img[y:y + 70, x:x + 80] = (0.5 + rs.uniform(-0.07, 0.07, 3)) if i < 30 else rs.uniform(0.1, 0.9, 3)
    img[(np.arange(800)[:, None] > 680) & (np.arange(1200)[None, :] < 300)] = 0.50     # the card, at CARD
    lin = srgb_to_linear(img).astype(np.float64) * np.array(cast)
    out = linear_to_srgb(lin).astype(np.float64) + np.random.RandomState(0).normal(0.0, 0.003, img.shape)
    return np.clip(out, 0, 1).astype(np.float32)


def test_the_oversaturation_gate_does_not_take_the_pick_back(tmp_path):
    free = _chart((1.0, 1.0, 1.0))
    spot = [0.12, 0.90]
    for name in ("warm", "cool", "strong"):
        kw = dict(concepts=[], compose=False, sharpen=False, palette="off")
        _, out, r, _ = _render(tmp_path, "chart-" + name, _chart(CASTS[name]), neutral=spot, **kw)
        assert not any(n.startswith("oversaturation gate") for n in r.notes), name
        s = {k: v for k, v in r.global_settings.items() if k not in ("temp", "tint")}
        want = ops.apply_ops(free, s)
        assert float(np.abs(out - want).max()) * 255.0 < 5.0, name


# 10 ---------------------------------------------------------------------------------------------------------------

def test_the_pick_is_off_by_default(tmp_path):
    assert RunConfig().neutral is None and RunConfig().wb is None
    _, _, r, _ = _render(tmp_path, "d", _product())
    assert "neutral" not in r.audit and not any(n.startswith("neutral pick") for n in r.notes)
    assert r.audit["run_config"]["neutral"] is None and r.audit["run_config"]["wb"] is None
    for bad in ({"neutral": [1.2, 0.5]}, {"neutral": [0.5]}, {"neutral": [0.1, 0.2, 0.3]},
                {"neutral": [float("nan"), 0.5]}, {"wb": [150, 0]}, {"wb": [5]}, {"wb": [float("inf"), 0]}):
        with pytest.raises(ValueError):
            RunConfig(**bad)


# 11 ---------------------------------------------------------------------------------------------------------------

def _args(**kw):
    return SimpleNamespace(**{"configs_dir": None, "config": None, **kw})


def _batch(tmp_path, names=("a_card",), casts=("warm",), **cfg_kw):
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    for n, c in zip(names, casts):
        _save(src / f"{n}.png", _product(cast=CASTS[c]))
    out = tmp_path / "out"
    batch.run(RunConfig(backend="stub", out_format="png", workers=1, **cfg_kw), str(src), str(out))
    return src, out


def test_cli_api_and_adjust_carry_the_pick(tmp_path):
    assert load_cfg(_args(neutral=[0.1, 0.2])).neutral == [0.1, 0.2]
    assert load_cfg(_args()).neutral is None and load_cfg(_args()).wb is None
    conf = tmp_path / "run.toml"
    conf.write_text("[run]\nneutral = [0.3, 0.4]\nwb = [5, -5]\n")
    cfg = load_cfg(_args(config=str(conf)))
    assert cfg.neutral == [0.3, 0.4] and cfg.wb == [5.0, -5.0]
    cfg = service.make_cfg({"neutral": [0.1, 0.2], "wb": [1, 2]})
    assert cfg.neutral == [0.1, 0.2] and cfg.wb == [1.0, 2.0]
    with pytest.raises(ValueError):
        service.make_cfg({"neutral": [2, 0]})
    with redirect_stderr(StringIO()):
        with pytest.raises(SystemExit):
            cli.main(["run", str(tmp_path), str(tmp_path / "o"), "--neutral", "2,0"])

    src, out = _batch(tmp_path)
    img, side = str(out / "a_card.png"), str(out / "a_card.autoedit.json")
    base = open(img, "rb").read()
    adjust.rerender(img, neutral=CARD)
    picked = open(img, "rb").read()
    assert picked != base and json.load(open(side))["audit"]["run_config"]["neutral"] == CARD
    adjust.rerender(img)                                    # the pick is this photo's setting now
    assert open(img, "rb").read() == picked
    adjust.rerender(img, neutral="none")
    assert open(img, "rb").read() == base
    with redirect_stdout(StringIO()):
        assert cli.main(["adjust", img, "--neutral", "0.12,0.85"]) == 0
    assert open(img, "rb").read() == picked


def load_cfg(args):
    return cli.load_run_config(args)


# 12 ---------------------------------------------------------------------------------------------------------------

def test_neutral_from_measures_once(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    card = _save(src / "a_card.png", _product(cast=CASTS["warm"]))
    _save(src / "b.png", _product(cast=CASTS["cool"], seed=1))
    cfg, line = neutral.for_run(RunConfig(backend="stub", neutral=CARD), card)
    pick, _ = neutral.measure(_decoded(card), CARD, rules_mod.block({}, "neutral", neutral.DEFAULTS, neutral.RANGES))
    assert cfg.neutral is None and cfg.wb == [round(pick.temp, 3), round(pick.tint, 3)]
    assert line.startswith("neutral pick in a_card.png at 0.120, 0.850")

    out = tmp_path / "out"
    buf = StringIO()
    with redirect_stdout(buf):
        code = cli.main(["run", str(src), str(out), "--backend", "stub", "--format", "png", "--workers", "1",
                         "--neutral", "0.12,0.85", "--neutral-from", card])
    assert code == 0 and line in buf.getvalue()
    sidecars = [json.load(open(p)) for p in sorted(out.glob("*.autoedit.json"))]
    assert len(sidecars) == 2
    for s in sidecars:
        assert s["audit"]["run_config"]["wb"] == cfg.wb and s["audit"]["run_config"]["neutral"] is None
        assert set(s["audit"]["run_inputs"]) == {"reference", "recipe_model"}
        assert str(tmp_path) not in json.dumps(s)
        assert (s["global_settings"]["temp"], s["global_settings"]["tint"]) == tuple(cfg.wb)
    assert _off(_decoded(str(out / "a_card.png"))) < 1.0

    for argv in (["--neutral-from", card],                                              # no --neutral
                 ["--neutral", "0.12,0.85", "--neutral-from", str(src / "nope.png")],   # no such file
                 ["--neutral", "0.5,0.35", "--neutral-from", card]):                    # a spot that is not used
        gone = tmp_path / "gone"
        err = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(err):
            assert cli.main(["run", str(src), str(gone), "--backend", "stub", *argv]) == 2
        assert not gone.exists() and err.getvalue().startswith("--neutral-from ")


# 13 ---------------------------------------------------------------------------------------------------------------

def test_the_consistency_bias_gives_way_to_the_pick(tmp_path):
    path = _save(tmp_path / "x.png", _product(cast=CASTS["warm"]))
    pipe = Pipeline.build(RunConfig(backend="stub", neutral=CARD))
    _, free = pipe.process_one(path, wb_bias=(0.0, 0.0))
    _, r = pipe.process_one(path, wb_bias=(3.0, -2.0))
    assert r.wb_bias == [0.0, 0.0]
    assert r.global_settings["temp"] == free.global_settings["temp"]
    assert "batch normalize: white balance not moved, the neutral pick sets it" in r.notes


# ---------------------------------------------------------------------------------------------------------------
# T2: the clean backdrop
# ---------------------------------------------------------------------------------------------------------------

def _grid(h=600, w=900):
    v, u = np.meshgrid(np.linspace(0, 1, h), np.linspace(0, 1, w), indexing="ij")
    return u, v, (lambda x: np.abs(x - 0.5))


def _clear(h=600, w=900):
    """The clear zone: backdrop with no box, label, card or shadow near it."""
    u, v, a = _grid(h, w)
    return (v < 0.18) | ((a(u) > 0.40) & (v < 0.70))


def _rules_dir(tmp_path, name="cfg", **blocks):
    """A copy of the packaged configs folder whose rules.toml has `key = value` lines replaced in [backdrop] or
    [neutral]: blocks={"backdrop": {"white": "245"}}."""
    d = tmp_path / name
    shutil.copytree(paths.configs_dir(), str(d))
    lines, block, out = (d / "rules.toml").read_text().split("\n"), None, []
    for line in lines:
        if line.startswith("["):
            block = line.strip("[] \t").split(".")[0]
        key = line.split("=")[0].strip()
        if block in blocks and key in blocks[block] and "=" in line:
            line = f"{key} = {blocks[block][key]}"
        out.append(line)
    (d / "rules.toml").write_text("\n".join(out))
    return str(d / "rules.toml")


# 14 ---------------------------------------------------------------------------------------------------------------

def test_from_edge():
    cand = np.zeros((120, 180), bool)
    cand[20:80, 0:40] = True            # reaches the left edge
    area = cand.copy()
    cand[90:110, 100:130] = True        # an island
    cand[5, 150] = True                 # a speck
    for grid in (60, 256):
        assert np.array_equal(backdrop.from_edge(cand, grid), area)
        assert not backdrop.from_edge(np.zeros((120, 180), bool), grid).any()
    framed = np.ones((120, 180), bool)
    framed[40:80, 60:120] = False       # a hole
    framed[55:65, 80:100] = True        # an island in it
    for grid in (60, 256):
        got = backdrop.from_edge(framed, grid)
        assert not got[55:65, 80:100].any()
        assert got[39, 60:120].all() and got[80, 60:120].all()      # the row along the hole stays
        assert got[0].all() and got[:, 0].all()


# 15 ---------------------------------------------------------------------------------------------------------------

def test_find_needs_light_neutral_and_the_edge():
    img = _product(noise=0.0)
    u, v, a = _grid()
    m = backdrop.find(img)
    assert m[_clear()].mean() > 0.999
    assert not m[(a(u) < 0.16) & (a(v) < 0.22)].any()        # the box, the label with it
    assert not m[(np.abs(u - CARD[0]) < 0.04) & (np.abs(v - CARD[1]) < 0.05)].any()
    assert 0.80 <= m.mean() <= 0.90
    rows = np.zeros(m.shape, bool)
    rows[:100] = True
    assert not backdrop.find(img, keep=rows)[:100].any()
    assert backdrop.find(_product(noise=0.0, level=0.5)).mean() < 0.01
    assert backdrop.find(_product(noise=0.0, cast=(1.3, 1.0, 0.7))).mean() < 0.01


# 16 ---------------------------------------------------------------------------------------------------------------

def test_fit_follows_the_surface_not_the_shadow():
    img = _product(noise=0.0)
    coef = backdrop.fit(img, backdrop.find(img), 4)
    h, w = img.shape[:2]
    ax = lambda n: np.linspace(-1, 1, n, dtype=np.float32)      # noqa: E731
    surf = backdrop.surface(coef, ax(w)[None, :], ax(h)[:, None])
    clear = _clear()
    assert float(np.abs(surf - img)[clear].max()) * 255.0 < 1.0
    row, cols = int(0.75 * (h - 1)), slice(int(0.45 * w), int(0.55 * w))
    assert (img[row, cols] / surf[row, cols]).max() < 0.9
    u, v, _ = _grid()
    formula = 0.90 * (1 - 0.25 * ((u - .5) ** 2 + (v - .5) ** 2) - 0.10 * (v - .5) - 0.06 * (u - .5))
    assert float(np.abs(surf[row, :, 0] - formula[row]).max()) * 255.0 < 2.0
    few = np.zeros((h, w), bool)
    few[:5, :5] = True
    assert backdrop.fit(img, few, 4) is None


# 17 ---------------------------------------------------------------------------------------------------------------

def test_backdrop_is_even_and_white():
    img = _product(noise=0.0)
    out, rep, wt = backdrop.even(img)
    clear = _clear() & (wt == 1.0)
    assert rep["applied"] and rep["white"] == 0.96 and not rep["limited"]
    before = img[clear][:, 1]
    assert float(before.max() - before.min()) * 255.0 > 30
    assert float(np.abs(_to_u8(out)[clear].astype(np.float64) - 244.8).max()) <= 2.0
    ratio = lambda x: float(x[450, 450:460, 1].mean() / x[450, 270:280, 1].mean())   # noqa: E731  shadow / its surround
    assert abs(ratio(out) - ratio(img)) < 0.03
    noisy = _product(noise=0.01)
    out, _, wt = backdrop.even(noisy)
    for y, x in ((30, 100), (60, 450), (300, 40), (300, 860), (80, 800)):
        assert wt[y, x] == 1.0
        assert abs(float(out[y - 8:y + 9, x - 8:x + 9].mean()) * 255.0 - 244.8) < 2.0
    cast = _product(noise=0.0, cast=(1.06, 1.0, 0.93))
    clear = _clear() & (backdrop.even(cast)[2] == 1.0)
    means = cast[clear].mean(axis=0) * 255.0
    assert float(means.max() - means.min()) > 8
    out = backdrop.even(cast)[0]
    assert float(np.abs(_to_u8(out)[clear].astype(np.float64) - 244.8).max()) <= 2.0


# 18 ---------------------------------------------------------------------------------------------------------------

def test_outside_the_grown_mask_nothing_changes(tmp_path):
    img = _product(noise=0.0)
    out, _, wt = backdrop.even(img)
    assert np.array_equal(out[wt == 0.0], img[wt == 0.0])
    u, v, a = _grid()
    inner = (a(u) < 0.15) & (a(v) < 0.21)
    assert (wt[inner] == 0.0).mean() > 0.97
    assert float(np.abs(out - img)[inner].max()) * 255.0 < 0.5

    path = _save(tmp_path / "p.png", _product())
    pipe0 = Pipeline.build(RunConfig(backend="stub", out_format="png"))
    pipe1 = Pipeline.build(RunConfig(backend="stub", out_format="png", backdrop=True))
    out0, r0 = pipe0.process_one(path)
    out1, r1 = pipe1.process_one(path)
    want, _, wt = backdrop.even(out0, pipe1.backdrop_cfg, long_edge=pipe1.cfg.analysis_long_edge)
    assert np.array_equal(out1, want)
    assert 0.10 < float((wt == 0.0).mean()) < 0.20
    for r in (r0, r1):
        emit.write_outputs(out1 if r is r1 else out0, r, str(tmp_path / f"{'b' if r is r1 else 'a'}.png"),
                           str(tmp_path / f"{'b' if r is r1 else 'a'}.autoedit.json"), pipe0.cfg, path)
    same = _decoded(str(tmp_path / "a.png")) == _decoded(str(tmp_path / "b.png"))
    assert same[wt == 0.0].all() and not same.all()
    assert r1.global_settings == r0.global_settings
    assert [m.model_dump() for m in r1.masks] == [m.model_dump() for m in r0.masks]


# 19 ---------------------------------------------------------------------------------------------------------------

def test_backdrop_is_bounded(tmp_path):
    img = _product(noise=0.0, tilt=0.25, bowl=0.45)
    out, rep, wt = backdrop.even(img)
    assert rep["limited"] and rep["gain"][1] == 1.6
    ratio = out[wt > 0] / np.maximum(img[wt > 0], 1e-6)
    assert float(ratio.max()) <= 1.6 + 1e-4 and float(ratio.min()) >= 1 / 1.6 - 1e-4
    _, _, r, _ = _render(tmp_path, "limited", img, backdrop=True)
    assert r.notes and any(n.startswith("backdrop: ") and n.endswith("it stays darker") for n in r.notes)
    assert backdrop.even(_product(noise=0.0, level=1.2))[1]["gain"][0] < 1.0

    h, w = 400, 600
    land = np.empty((h, w, 3), np.float32)
    land[: h // 2], land[h // 2:] = (0.45, 0.62, 0.85), (0.22, 0.38, 0.16)
    land += np.random.RandomState(1).normal(0, 0.004, land.shape).astype(np.float32)
    out, rep, wt = backdrop.even(land)
    assert out is land and wt is None and rep["why"].startswith("no light backdrop found")
    _, plain_px, _, _ = _render(tmp_path, "land", land)
    _, px, r, _ = _render(tmp_path, "land", land, backdrop=True)
    assert np.array_equal(px, plain_px)
    assert any(n.startswith("backdrop: nothing changed, ") for n in r.notes)


# 20 ---------------------------------------------------------------------------------------------------------------

def test_the_review_check_reads_the_backdrop(tmp_path):
    img = _product()
    assert RunConfig().backdrop is False
    _, _, plain, _ = _render(tmp_path, "d", img)
    assert "backdrop" not in plain.audit and not any(n.startswith("backdrop:") for n in plain.notes)
    _, _, r, _ = _render(tmp_path, "d", img, backdrop=True)
    assert r.post_edit_stats.mean_lum > plain.post_edit_stats.mean_lum + 0.05
    assert (r.needs_review, r.quality_score) == (plain.needs_review, plain.quality_score)
    band = lambda x: [n for n in x.notes if n.startswith("banding")]    # noqa: E731
    assert band(r) == band(plain)
    assert any("% of the frame evened out and taken to 245 of 255; it was " in n for n in r.notes)
    assert set(r.audit["backdrop"]) == {"applied", "coverage", "white", "level", "gain", "limited"}
    white = _rules_dir(tmp_path, backdrop={"white": "1.0"})
    _, _, r, _ = _render(tmp_path, "d", img, backdrop=True, rules_path=white)
    assert r.needs_review and "highlight clipping" in r.review_reason
    assert any("taken to 255 of 255" in n for n in r.notes)


# 21 ---------------------------------------------------------------------------------------------------------------

def _pinned(threads, fn):
    old = (par._threads, par.MIN_PIXELS)
    par.MIN_PIXELS = 0
    par.set_threads(threads)
    try:
        return fn()
    finally:
        par.MIN_PIXELS = old[1]
        par.set_threads(old[0])


def _outputs(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.glob("*.png"))}


def test_backdrop_strips_workers_keep_and_thresholds(tmp_path):
    big = _product(h=700, w=900, noise=0.002)
    one = _pinned(1, lambda: backdrop.even(big))
    four = _pinned(4, lambda: backdrop.even(big))
    assert np.array_equal(one[0], four[0]) and np.array_equal(one[2], four[2])

    src = tmp_path / "in"
    src.mkdir()
    for i, c in enumerate(("warm", "cool")):
        _save(src / f"{'a_card' if i == 0 else 'b'}.png", _product(cast=CASTS[c], seed=i, h=400, w=600))
    runs = {}
    for name, workers in (("w1", 1), ("w2", 2)):
        runs[name] = tmp_path / name
        batch.run(RunConfig(backend="stub", out_format="png", workers=workers, backdrop=True, neutral=CARD),
                  str(src), str(runs[name]))
    assert _outputs(runs["w1"]) == _outputs(runs["w2"]) and len(_outputs(runs["w1"])) == 2
    cli_out = tmp_path / "cli"
    with redirect_stdout(StringIO()):
        assert cli.main(["run", str(src), str(cli_out), "--backend", "stub", "--format", "png", "--workers", "1",
                         "--backdrop", "--neutral", "0.12,0.85"]) == 0
    assert _outputs(cli_out) == _outputs(runs["w1"])
    rendered = runs["w1"] / "a_card.png"
    before = rendered.read_bytes()
    adjust.rerender(str(rendered))
    assert rendered.read_bytes() == before
    p = str(src / "a_card.png")
    plain = service.preview(p, {"backend": "stub"})
    both = service.preview(p, {"backend": "stub", "backdrop": True, "neutral": CARD})
    assert "backdrop" in both["result"]["audit"] and "neutral" in both["result"]["audit"]
    assert both["after"] != plain["after"]

    path = _save(tmp_path / "s.png", _product(h=600, w=900))
    pipe = Pipeline.build(RunConfig(backend="stub", backdrop=True))
    _, stub = pipe.process_one(path)
    plain_px = Pipeline.build(RunConfig(backend="stub")).process_one(path)[0]
    pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})
    out, model = pipe.process_one(path)
    u, v, a = _grid()
    oval = (a(u) / 0.30) ** 2 + (a(v) / 0.40) ** 2 <= 1.0       # the stub's subject
    inner = np.asarray(PILImage.fromarray((oval * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(25))) > 0
    assert inner.mean() > 0.1 and np.array_equal(out[inner], plain_px[inner])
    assert model.audit["backdrop"]["coverage"] < stub.audit["backdrop"]["coverage"] - 0.1

    for key, bad in (("white", "245"), ("degree", "40"), ("max_gain", "0"), ("feather", "true")):
        rules = _rules_dir(tmp_path, f"bad-{key}", backdrop={key: bad})
        with pytest.raises(ValueError) as e:
            Pipeline.build(RunConfig(backend="stub", backdrop=True, rules_path=rules))
        assert f"[backdrop] {key} must be a number from" in str(e.value)
        Pipeline.build(RunConfig(backend="stub", rules_path=rules))         # off: the block is not read
    rules = _rules_dir(tmp_path, "bad-patch", neutral={"patch": "2"})
    with pytest.raises(ValueError):
        Pipeline.build(RunConfig(backend="stub", rules_path=rules))
    gone = tmp_path / "gone"
    err = StringIO()
    with redirect_stdout(StringIO()), redirect_stderr(err):
        code = cli.main(["run", str(src), str(gone), "--backend", "stub", "--backdrop",
                         "--configs-dir", str(tmp_path / "bad-white")])
    assert code == 2 and not list(gone.glob("*.png")) and not list(gone.glob("*.json"))


# ---------------------------------------------------------------------------------------------------------------
# T3: one white balance for a group, and the colour report
# ---------------------------------------------------------------------------------------------------------------

from autoedit import consistency                                                  # noqa: E402
from autoedit.edit.oklab import linear_to_oklab                                   # noqa: E402

DRIFT = ((-6, 2), (-3, -2), (0, 0), (2, 3), (4, -3), (7, 1), (-5, 0), (5, 2))


def _drift(t, n):
    return (1 + 0.002 * t, 1 + 0.001 * n, 1 - 0.002 * t)


def _drift_batch(tmp_path, name="in", **cfg_kw):
    cfg_kw = {"workers": 1, **cfg_kw}
    src = tmp_path / name
    src.mkdir(exist_ok=True)
    for i, (t, n) in enumerate(DRIFT):
        _save(src / f"f{i}.png", _product(cast=_drift(t, n), gain=1.0 + 0.01 * ((i % 3) - 1), seed=i))
    out = tmp_path / f"out-{name}-{len(list(tmp_path.glob('out-*')))}"
    batch.run(RunConfig(backend="stub", out_format="png", **cfg_kw), str(src), str(out))
    return src, out


def _sides(out):
    return [json.load(open(p)) for p in sorted(out.glob("*.autoedit.json"))]


def _spread(sides, key):
    v = [s["post_edit_stats"][key] for s in sides]
    return max(v) - min(v)


def _d(temp, tint, conf=0.9, wb=(0.0, 0.0)):
    return {"wb_temp": temp, "wb_tint": tint, "wb_conf": conf, "temp": wb[0], "tint": wb[1]}


# 22 ---------------------------------------------------------------------------------------------------------------

def test_cast_biases():
    got = consistency.cast_biases([_d(-6, 2), _d(0, 0), _d(7, 1)], 8.0, 0.3)
    assert got == [(-6.0, 1.0), (0.0, -1.0), (7.0, 0.0)]
    got = consistency.cast_biases([_d(-6, 2, wb=(-2, 1)), _d(0, 0), _d(7, 1, wb=(3, 0))], 8.0, 0.3)
    assert got == [(-4.0, 0.0), (0.0, -1.0), (4.0, 0.0)]
    assert consistency.cast_biases([_d(-30, 0), _d(0, 0), _d(30, 0)], 8.0, 0.3) == [(-8.0, 0.0), (0.0, 0.0), (8.0, 0.0)]
    assert consistency.cast_biases([_d(-6, 2), _d(0, 0, conf=0.1), _d(7, 1), _d(1, 1)], 8.0, 0.3)[1] == (0.0, 0.0)
    assert consistency.cast_biases([_d(5, 5), _d(0, 0, conf=0.1)], 8.0, 0.3) == [(0.0, 0.0)] * 2
    assert consistency.cast_biases([_d(3, 3)] * 3, 8.0, 0.3) == [(0.0, 0.0)] * 3
    dec = [{"fg_p50": 0.4, "exposure": 0.0, "shadows_ev": 0.0, "headroom_ev": 2.0, **_d(t, 0)} for t in (-6, 0, 7)]
    plain = consistency.kind_biases(["a.jpg", "b.jpg", "c.jpg"], dec, 0.5, 0.0)
    assert [(b[1], b[2]) for b in plain] == [(0.0, 0.0)] * 3
    cast = consistency.kind_biases(["a.jpg", "b.jpg", "c.jpg"], dec, 0.5, 0.0, (8.0, 0.3))
    assert [b[1] for b in cast] == [-6.0, 0.0, 7.0]


# 23 ---------------------------------------------------------------------------------------------------------------

def test_wb_spread_is_at_least_halved(tmp_path):
    _, base = _drift_batch(tmp_path)
    _, even = _drift_batch(tmp_path, consistency_wb=True)
    b, e = _sides(base), _sides(even)
    for key in ("wb_temp_correction", "wb_tint_correction"):
        assert _spread(b, key) > 4.0 and _spread(e, key) <= _spread(b, key) / 2
    assert all(abs(s["wb_bias"][0]) <= 8 and abs(s["wb_bias"][1]) <= 8 for s in e)
    assert sum(1 for s in e if any(s["wb_bias"])) >= 6
    assert any("batch normalize: white balance" in n for s in e for n in s["notes"])
    assert all(s["wb_bias"] == [0.0, 0.0] for s in b)
    img = str(even / "f0.png")
    was = open(img, "rb").read()
    adjust.rerender(img)
    assert open(img, "rb").read() == was
    _, two = _drift_batch(tmp_path, consistency_wb=True, workers=2)
    assert _outputs(two) == _outputs(even)


# 24 ---------------------------------------------------------------------------------------------------------------

def test_bursts_mode_evens_the_measured_cast(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for i, (t, n) in enumerate(DRIFT[:4]):
        exif = PILImage.Exif()
        exif.get_ifd(0x8769)[0x9003] = f"2026:01:01 00:00:{3 * i:02d}"
        PILImage.fromarray((_product(h=300, w=450, cast=_drift(t, n), seed=i) * 255).astype(np.uint8), "RGB").save(
            str(src / f"f{i}.jpg"), quality=95, exif=exif)
    spreads = []
    for flag in (False, True):
        out = tmp_path / f"o{flag}"
        batch.run(RunConfig(backend="stub", out_format="png", workers=1, consistency="bursts", consistency_wb=flag),
                  str(src), str(out))
        sides = _sides(out)
        assert {s["batch_group"] for s in sides} == {"b1"}
        spreads.append(_spread(sides, "wb_temp_correction"))
    assert spreads[1] <= spreads[0] / 2


# 25 ---------------------------------------------------------------------------------------------------------------

def test_the_plan_leaves_the_white_balance_to_a_pick_and_never_calls_the_llm(tmp_path):
    src, _ = _drift_batch(tmp_path)
    images = sorted(str(p) for p in src.glob("*.png"))

    class Boom:
        name = "boom"

        def decide(self, *a, **k):
            raise AssertionError("the plan called the LLM decider")

    cfg = RunConfig(backend="stub", consistency_wb=True)
    pipe = Pipeline.build(cfg)
    pipe.decider = Boom()
    from autoedit import consistency as con
    got = con.plan(images, cfg, pipe)
    assert isinstance(pipe.decider, Boom) and any(b.temp or b.tint for b in got.values())
    for kw in ({"neutral": CARD}, {"wb": [1.0, 1.0]}):
        cfg = RunConfig(backend="stub", consistency_wb=True, **kw)
        pipe = Pipeline.build(cfg)
        pipe.process_one = None
        got = con.plan(images, cfg, pipe)
        assert got and all((b.temp, b.tint) == (0.0, 0.0) for b in got.values())
    cfg = RunConfig(backend="stub")
    got = con.plan(images, cfg, None)
    assert got and all((b.temp, b.tint) == (0.0, 0.0) for b in got.values())


# 26 ---------------------------------------------------------------------------------------------------------------

def _sidecar(folder, name, lab, cast, conf=0.9, group="g1"):
    d = {"image": name, "batch_group": group,
         "post_edit_stats": {"wb_temp_correction": cast[0], "wb_tint_correction": cast[1], "wb_confidence": conf}}
    if lab is not None:
        d["audit"] = {"subject_colour": {"concept": "subject", "lab": lab}}
    p = folder / (name + ".autoedit.json")
    p.write_text(json.dumps(d))
    return str(p)


def test_colour_report(tmp_path):
    side = [_sidecar(tmp_path, "a.jpg", [.50, .10, .05], (2, 1)), _sidecar(tmp_path, "b.jpg", [.50, .10, .05], (5, 1.5)),
            _sidecar(tmp_path, "c.jpg", [.53, .10, .09], (-1, 0))]
    want = {"photos": 3, "delta_e_mean": 1.67, "delta_e_max": 5.0, "furthest": "c.jpg", "wb_spread": [6.0, 1.5]}
    rep = consistency.colour_report(side, 0.3)
    assert rep["delta_e"] == consistency.DELTA_E and rep["groups"] == {"g1": want}
    assert consistency.colour_lines(rep) == [
        "  group g1: 3 photos, subject colour differs by 1.7 on average and 5.0 at most (c.jpg); "
        "white balance spread temp 6.0, tint 1.5"]
    weak = side + [_sidecar(tmp_path, "d.jpg", [.50, .10, .05], (40, 40), conf=0.1)]
    assert consistency.colour_report(weak, 0.3)["groups"]["g1"]["wb_spread"] == [6.0, 1.5]
    extra = side + [_sidecar(tmp_path, "raw1.dng", [.5, .1, .05], (0, 0), group="g1"),
                    _sidecar(tmp_path, "solo.jpg", [.5, .1, .05], (0, 0), group="g9"),
                    _sidecar(tmp_path, "n1.jpg", [.5, .1, .05], (0, 0), group=""),
                    _sidecar(tmp_path, "n2.jpg", [.6, .1, .05], (1, 0), group=""),
                    str(tmp_path / "missing.json")]
    (tmp_path / "broken.json").write_text("{nope")
    extra.append(str(tmp_path / "broken.json"))
    groups = consistency.colour_report(extra, 0.3)["groups"]
    assert set(groups) == {"g1", "all"} and groups["g1"] == want and groups["all"]["photos"] == 2


# 27 ---------------------------------------------------------------------------------------------------------------

def test_subject_colour():
    rgb = _product(noise=0.0)
    h, w = rgb.shape[:2]
    red = np.zeros((h, w), bool)
    red[int(0.45 * h):int(0.57 * h), int(0.36 * w):int(0.42 * w)] = True
    got =consistency.subject_colour(rgb, {"subject": red}, ["subject", "person"])
    want = linear_to_oklab(srgb_to_linear(np.array([0.55, 0.20, 0.18], np.float64)))
    assert got["concept"] == "subject" and np.allclose(got["lab"], want, atol=1e-4)
    green = np.zeros((h, w), bool)
    green[int(0.30 * h):int(0.40 * h), int(0.40 * w):int(0.60 * w)] = True
    assert consistency.subject_colour(rgb, {"person": green, "subject": np.zeros((h, w), bool)},
                                      ["subject", "person"])["concept"] == "person"
    assert consistency.subject_colour(rgb, {"sky": red}, ["subject", "person"]) is None
    assert consistency.subject_colour(rgb, {"subject": red[:10, :10]}, ["subject"]) is None


# 28 ---------------------------------------------------------------------------------------------------------------

def test_the_run_writes_the_colour_report(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for i, (t, n) in enumerate(DRIFT[:4]):
        _save(src / f"f{i}.png", _product(cast=_drift(t, n), seed=i))
    cfg = RunConfig(backend="stub", out_format="png", workers=1, colour_report=True)
    buf = StringIO()
    with redirect_stdout(buf):
        man = batch.run(cfg, str(src), str(tmp_path / "o"))
    rep = json.load(open(tmp_path / "o" / "run.json"))["colour_report"]
    assert rep == man["colour_report"]
    assert sum(g["photos"] for g in rep["groups"].values()) == 4 and all("wb_spread" in g for g in rep["groups"].values())
    assert "Colour report (OKLab distance" in buf.getvalue() and "subject colour differs by" in buf.getvalue()
    assert all("subject_colour" in s["audit"] for s in _sides(tmp_path / "o"))
    gone = sorted((tmp_path / "o").glob("*.autoedit.json"))[0]
    gone.unlink()
    with redirect_stdout(StringIO()):
        again = batch.run(cfg, str(src), str(tmp_path / "o"), resume=True)
    assert again["colour_report"]["groups"] == rep["groups"]

    buf = StringIO()
    with redirect_stdout(buf):
        plain = batch.run(RunConfig(backend="stub", out_format="png", workers=1), str(src), str(tmp_path / "p"))
    assert "colour_report" not in plain and "Colour report" not in buf.getvalue()
    assert all("subject_colour" not in s["audit"] for s in _sides(tmp_path / "p"))
    assert _outputs(tmp_path / "p") == _outputs(tmp_path / "o")
    with redirect_stdout(StringIO()):
        dry = batch.run(cfg, str(src), str(tmp_path / "d"), do_apply=False)
    assert "colour_report" not in dry


# 29 ---------------------------------------------------------------------------------------------------------------

def test_consistency_flags(tmp_path):
    base = load_cfg(_args())
    assert not (base.consistency_wb or base.colour_report or base.backdrop)
    cfg = load_cfg(_args(consistency_wb=True, colour_report=True, backdrop=True))
    assert cfg.consistency_wb and cfg.colour_report and cfg.backdrop
    conf = tmp_path / "run.toml"
    conf.write_text("[run]\nconsistency_wb = true\ncolour_report = true\nbackdrop = true\n")
    cfg = load_cfg(_args(config=str(conf)))
    assert cfg.consistency_wb and cfg.colour_report and cfg.backdrop
    cfg = service.make_cfg({"consistency_wb": True, "colour_report": True})
    assert cfg.consistency_wb and cfg.colour_report
    block = rules_mod.load_rules(None).get("consistency") if hasattr(rules_mod, "load_rules") else None
    if block is None:
        block = Pipeline.build(RunConfig(backend="stub")).rules["consistency"]
    assert block["max_wb_cast"] == 8 and block["wb_min_confidence"] == 0.3 and block["max_wb"] == 4
    assert block["colour_of"] == ["subject", "person"]


def test_checker_notes_hold(tmp_path):
    """The Checker's notes, fixed: a spot of 0 is never measured; block() keeps words and whole numbers;
    the soft edge of the backdrop never reaches into what a model found."""
    from autoedit import backdrop, neutral, rules
    with pytest.raises(ValueError):
        rules.block({"neutral": {"min_level": 0}}, "neutral", neutral.DEFAULTS, neutral.RANGES)
    with pytest.raises(ValueError):
        rules.block({"backdrop": {"keep": "subject"}}, "backdrop", backdrop.DEFAULTS, backdrop.RANGES)
    with pytest.raises(ValueError):
        rules.block({"backdrop": {"degree": 2.5}}, "backdrop", backdrop.DEFAULTS, backdrop.RANGES)
    with pytest.raises(ValueError):
        rules.block({"neutral": 1}, "neutral", neutral.DEFAULTS, neutral.RANGES)
    assert rules.block({"backdrop": {"degree": 3}}, "backdrop", backdrop.DEFAULTS, backdrop.RANGES)["degree"] == 3
    h, w = 300, 200
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.repeat((0.80 + 0.12 * yy / h)[..., None], 3, axis=2).astype(np.float32)   # an uneven light backdrop
    keep = ((yy - 150) / 60.0) ** 2 + ((xx - 100) / 45.0) ** 2 <= 1.0                  # what a model found
    rgb[keep] = 0.3
    out, report, _ = backdrop.even(rgb, backdrop.DEFAULTS, long_edge=h, keep=keep, small=rgb)
    assert not np.array_equal(out, rgb)
    assert np.array_equal(out[keep], rgb[keep])
