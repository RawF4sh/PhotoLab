"""F23: film grain. Tests 1 to 11: the op (edit/ops.py::grain). Tests 12 to 20: from the settings to the export."""

import numpy as np
import pytest

from autoedit.edit import ops
from autoedit.imageio import luma, resize_long_edge
from autoedit.quality import assess_pixels
from autoedit.quality_runner import render_safe


def _flat(v, shape=(400, 600)):
    return np.full(shape + (3,), v, np.float32)


def _std(x, out):
    return float(luma(out - x).std())


def test_zero_is_the_identity():
    x = _flat(0.5)
    assert ops.grain(x, 0) is x
    assert ops.grain(x, 0, size=3.0, seed=7) is x
    assert ops.apply_ops(x, {"grain": 80.0}) is x


def test_luma_only_and_mean_kept():
    x = np.random.default_rng(1).uniform(0.2, 0.8, (400, 600, 3)).astype(np.float32)
    out = ops.grain(x, 100, size=4.0)
    d = out - x
    assert np.abs(d[..., 0] - d[..., 1]).max() < 1e-6 and np.abs(d[..., 0] - d[..., 2]).max() < 1e-6
    assert np.abs(d.mean(axis=(0, 1))).max() < 0.5 / 255
    assert out.dtype == np.float32 and out.shape == x.shape


def test_same_input_same_output():
    x = _flat(0.5)
    a = ops.grain(x, 60, size=4.0, seed=3)
    assert np.array_equal(a, ops.grain(x, 60, size=4.0, seed=3))
    assert not np.array_equal(a, ops.grain(x, 60, size=4.0, seed=4))
    assert not np.array_equal(a, ops.grain(x, 60, size=2.0, seed=3))


def test_more_amount_more_variance():
    x = _flat(0.5)
    s25, s50, s100 = (_std(x, ops.grain(x, a, size=4.0)) for a in (25, 50, 100))
    assert s25 < s50 < s100
    assert abs(s100 / ops._GRAIN_SIGMA - 1.0) < 0.03
    assert abs(s50 / (0.5 * ops._GRAIN_SIGMA) - 1.0) < 0.03


def test_strongest_in_the_middle_tones():
    s = {v: _std(_flat(v), ops.grain(_flat(v), 100, size=4.0)) for v in (0.5, 0.2, 0.9)}
    assert s[0.5] > s[0.2] > s[0.9] > 0
    assert abs(s[0.2] / s[0.5] - 0.609) < 0.02
    for v in (0.0, 0.015, 0.985, 1.0):
        x = _flat(v)
        assert np.array_equal(ops.grain(x, 100, size=4.0), x), v


def test_luma_never_crosses_a_clip_level():
    ramp = np.linspace(0.0, 1.0, 600, dtype=np.float32)
    x = np.repeat(np.repeat(ramp[None, :, None], 3, axis=2), 400, axis=0)
    out = ops.grain(x, 100, size=4.0)
    lx, lo = luma(x), luma(out)
    inside = (lx >= ops._CLIP_LO) & (lx <= ops._CLIP_HI)
    assert ((lo[inside] >= 0.02) & (lo[inside] <= 0.98)).all()
    assert np.array_equal(out[~inside], x[~inside])
    assert (lx < 0.02).mean() == (lo < 0.02).mean() and (lx > 0.98).mean() == (lo > 0.98).mean()


def _b(x, out, k):
    d = luma(out - x)
    h, w = (d.shape[0] // k) * k, (d.shape[1] // k) * k
    blocks = d[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))
    return float(blocks.std() / d.std())


def test_size_follows_the_long_edge():
    small, big = _flat(0.5, (400, 600)), _flat(0.5, (800, 1200))
    b_small2 = _b(small, ops.grain(small, 100, size=4.0), 2)
    b_big2 = _b(big, ops.grain(big, 100, size=4.0), 2)
    b_big4 = _b(big, ops.grain(big, 100, size=4.0), 4)
    assert abs(b_small2 - b_big4) < 0.08
    assert b_big2 > b_small2 + 0.1
    assert abs(_b(small, ops.grain(small, 100, size=8.0), 2) - b_big2) < 0.08


def test_web_and_full_size_look_alike():
    big = _flat(0.5, (800, 1200))
    full = ops.grain(big, 100)
    for edge in (600, 300):
        small = _flat(0.5, (edge * 2 // 3, edge))
        want = _std(small, ops.grain(small, 100))
        got = float(luma(resize_long_edge(full, edge) - 0.5).std())
        assert 0.85 < got / want < 1.15, (edge, got / want)


def test_finer_than_a_pixel_is_weaker():
    x = _flat(0.5, (200, 300))
    assert abs(_std(x, ops.grain(x, 100)) / (0.15 * ops._GRAIN_SIGMA) - 1.0) < 0.05


def test_shapes_and_small_frames():
    for shape in ((1, 1), (16, 16), (33, 70), (301, 127)):
        x = np.random.default_rng(3).random(shape + (3,), dtype=np.float32)
        y = ops.grain(x, 100)
        assert y.shape == x.shape and y.dtype == np.float32 and np.isfinite(y).all(), shape
        assert y.min() >= 0.0 and y.max() <= 1.0, shape


def test_registered_last_and_not_rendered_by_apply_ops():
    assert "grain" in ops.OP_NAMES and "grain" in ops.SPATIAL_OPS
    assert ops.OP_BOUNDS["grain"] == (0.0, 100.0)
    assert ops.STEPS[-1] == ("grain",) and ops.STEPS[-2] == ("lut",) and ops.STEPS[-3] == ("sharpen", "sharpen_stars")
    x = np.random.default_rng(2).random((64, 64, 3), dtype=np.float32)
    assert np.array_equal(ops.apply_ops(x, {"sharpen": 20, "grain": 80}), ops.apply_ops(x, {"sharpen": 20}))


# ---- Tests 12 to 20 (T2): from the settings to the export ------------------------------------

import json
import os
from types import SimpleNamespace

from PIL import Image as PILImage

from autoedit import adjust, batch, cli, emit, looks as looks_mod, stats
from autoedit.contract import RunConfig
from autoedit.edit import banding, gates
from autoedit.imageio import Image, _to_u8
from autoedit.pipeline import Pipeline
from autoedit.web import service


def _scene(path, w=600, h=400):
    a = np.zeros((h, w, 3), np.float32)
    a[: h * 55 // 100] = [100, 140, 200]   # sky
    a[h * 55 // 100:] = [60, 110, 70]      # foliage
    yy, xx = np.mgrid[0:h, 0:w]
    a[(((xx - w / 2) / (w * 0.22)) ** 2 + ((yy - h * 0.62) / (h * 0.28)) ** 2) <= 1.0] = [200, 150, 120]
    PILImage.fromarray(a.astype(np.uint8), "RGB").save(path, quality=95)


def _folder(tmp_path, name, photo="a.jpg"):
    d = tmp_path / name
    d.mkdir()
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


def test_default_is_off_and_byte_identical(tmp_path):
    cfg = RunConfig()
    assert cfg.grain is None and cfg.grain_size == 1.0
    assert all(look.grain == 0.0 for look in looks_mod.load(cfg.looks_path).values())

    x = np.random.default_rng(5).random((90, 120, 3), dtype=np.float32)
    a, b = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    emit.write_image(x, a, "png", long_edge=60)
    emit.write_image(x, b, "png", long_edge=60, grain=None)
    assert _bytes(a) == _bytes(b)

    src = _folder(tmp_path, "in")
    batch.run(_stub(), str(src), str(tmp_path / "o1"), workers=1)
    batch.run(_stub(grain=0.0, grain_size=3.0), str(src), str(tmp_path / "o2"), workers=1)
    assert _bytes(tmp_path / "o1" / "a.jpg") == _bytes(tmp_path / "o2" / "a.jpg")
    side = _sidecar(tmp_path / "o1" / "a.autoedit.json")
    assert "grain" not in side["global_settings"]
    assert not any(n.startswith("grain") for n in side["notes"])


def test_the_checks_do_not_see_the_grain(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    plain_rgb, plain = Pipeline.build(_stub()).process_one(p, do_apply=True)
    grain_rgb, grained = Pipeline.build(_stub(grain=60.0)).process_one(p, do_apply=True)
    assert np.array_equal(plain_rgb, grain_rgb)

    def rest(r):
        d = r.model_dump()
        for k in [k for k in d if "timing" in k] + ["global_settings", "audit"]:
            d.pop(k)
        return d
    assert rest(plain) == rest(grained)
    assert grained.global_settings == {**plain.global_settings, "grain": 60.0}


def test_export_adds_it_after_the_resize(tmp_path):
    x = np.random.default_rng(6).random((200, 300, 3), dtype=np.float32) * 0.6 + 0.2
    g = (50.0, 2.0, 123)
    path = str(tmp_path / "g.png")
    emit.write_image(x, path, "png", long_edge=150, grain=g)
    want = _to_u8(ops.grain(resize_long_edge(x, 150), *g))
    assert np.array_equal(np.asarray(PILImage.open(path)), want)

    p = str(_folder(tmp_path, "in") / "a.jpg")
    cfg = _stub(grain=40.0, out_long_edge=300)
    pipe = Pipeline.build(cfg)
    rgb, res = pipe.process_one(p, do_apply=True)
    out, side = str(tmp_path / "e.jpg"), str(tmp_path / "e.autoedit.json")
    emit.write_outputs(rgb, res, out, side, cfg, p)
    assert PILImage.open(out).size == (300, 200)
    got = _sidecar(side)
    assert got["global_settings"]["grain"] == 40
    assert "grain: 40 of 100, size 1, added to the export after the checks and the resize" in got["notes"]


def test_rerun_and_rerender_are_byte_identical(tmp_path):
    cfg = _stub(grain=100.0, grain_size=4.0)
    one, two = _folder(tmp_path, "one"), _folder(tmp_path, "two")
    other = _folder(tmp_path, "three", photo="b.jpg")
    for src, name in ((one, "o1"), (one, "o2"), (two, "o3"), (other, "o4")):
        batch.run(cfg, str(src), str(tmp_path / name), workers=1)
    o1 = _bytes(tmp_path / "o1" / "a.jpg")
    assert o1 == _bytes(tmp_path / "o2" / "a.jpg")
    assert o1 == _bytes(tmp_path / "o3" / "a.jpg")
    assert o1 != _bytes(tmp_path / "o4" / "b.jpg")
    adjust.rerender(str(tmp_path / "o1" / "a.jpg"))
    assert o1 == _bytes(tmp_path / "o1" / "a.jpg")


def _looks_file(tmp_path, grain):
    path = str(tmp_path / "looks.toml")
    with open(RunConfig().looks_path) as fh:
        text = fh.read()
    with open(path, "w") as fh:
        fh.write(text + f"\n[grainy]\ngrain = {grain}\n")
    return path


def test_run_setting_look_and_decider(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    lf = _looks_file(tmp_path, 30)

    def grain_of(**kw):
        cfg = _stub(looks_path=lf, look="grainy", **kw)
        return Pipeline.build(cfg).process_one(p, do_apply=True)[1].global_settings.get("grain")
    assert grain_of(intensity=0.8) == pytest.approx(24.0)
    assert grain_of(intensity=1.0) == pytest.approx(30.0)
    assert grain_of(intensity=0.8, grain=10.0) == 10.0
    assert grain_of(intensity=0.8, grain=0.0) is None

    pipe = Pipeline.build(_stub())
    inner = pipe.decider

    class Adds:
        name = inner.name

        def decide(self, ctx):
            g, r = inner.decide(ctx)
            g["grain"] = 50.0
            for s in r.values():
                s["grain"] = 50.0
            return g, r
    pipe.decider = Adds()
    res = pipe.process_one(p, do_apply=True)[1]
    assert "grain" not in res.global_settings and res.masks
    assert not any("grain" in m.ops for m in res.masks)


def test_cli_and_api_carry_the_setting(tmp_path):
    import argparse
    parser = argparse.ArgumentParser()
    cli._add_run_flags(parser)

    def parsed(*flags):
        args = parser.parse_args(["in", "out", *flags])
        args.configs_dir = None
        return load_run_config(args)
    load_run_config = cli.load_run_config

    cfg = parsed("--grain", "30", "--grain-size", "2")
    assert (cfg.grain, cfg.grain_size) == (30.0, 2.0)
    assert parsed().grain is None

    run_file = tmp_path / "run.toml"
    run_file.write_text("[run]\ngrain = 30\ngrain_size = 2\n")
    args = SimpleNamespace(configs_dir=None, config=str(run_file))
    cfg = load_run_config(args)
    assert (cfg.grain, cfg.grain_size) == (30.0, 2.0)

    cfg = service.make_cfg({"grain": 30, "grain_size": 2})
    assert (cfg.grain, cfg.grain_size) == (30.0, 2.0)
    assert service.make_cfg({}).grain is None

    for bad in ({"grain": 150}, {"grain": -1}, {"grain_size": 9}, {"grain_size": 0.1}):
        with pytest.raises(ValueError):
            service.make_cfg(bad)
        with pytest.raises(ValueError):
            parsed(*(a for k, v in bad.items() for a in ("--" + k.replace("_", "-"), str(v))))


def test_preview_shows_the_grain(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    plain = service.preview(p, {"backend": "stub"})
    opts = {"backend": "stub", "grain": 60, "grain_size": 4}
    grained = service.preview(p, opts)
    assert plain["before"] == grained["before"] and plain["after"] != grained["after"]
    cfg = service.make_cfg(opts)
    out, res = Pipeline.build(cfg).process_one(p, do_apply=True)
    assert grained["after"] == service._img_b64(out, 2560, fmt="webp", grain=emit.grain_args(res, cfg))


def test_gates_do_not_fire_on_grain_alone():
    h, w, sky_h = 400, 600, 260
    g = np.linspace(0.45, 0.72, sky_h)[:, None].repeat(w, 1)
    sky = np.stack([g * 0.8, g * 0.95, g * 1.1], -1)
    noise = np.random.default_rng(0).normal(0.35, 0.12, (h - sky_h, w, 1))
    ground = np.clip(noise, 0, 1).repeat(3, 2) * np.array([1.0, 0.95, 0.8])
    x = np.clip(np.concatenate([sky, ground], 0), 0, 1).astype(np.float32)
    mask = np.zeros((h, w), bool)
    mask[:sky_h] = True

    heavy = ops.grain(x, 100, size=4.0)
    score, action, smooth = banding.assess(x, heavy)
    assert (score, action) == (0.0, "pass") and smooth > 0.3
    pre, post = (stats.compute(Image(rgb=a), {"sky": mask}, {"sky": float(mask.mean())})[0] for a in (x, heavy))
    assert gates.assess(pre, post) == (1.0, False, "", [])

    light = assess_pixels(x, ops.grain(x, 25, size=4.0), masks={"sky": mask})
    assert light["passed"], light["failures"]


def test_safety_workflow_adds_no_grain(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")
    plain_rgb, _ = render_safe(p, _stub())
    rgb, res = render_safe(p, _stub(grain=40.0))
    assert np.array_equal(plain_rgb, rgb)
    assert "grain" not in res.global_settings
    assert "grain: not added by the safety workflow, which checks the pixels it writes" in res.notes


# ---- Tests 21 and 22 (T3): the words -------------------------------------------------------

def _grain_parse(text, known=("sky",)):
    from autoedit import instructions
    from autoedit.paths import configs_dir
    vocab = instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))
    adjs, problems = instructions.parse(text, vocab, set(known))
    return [(a.target, dict(a.ops)) for a in adjs], problems


def test_instruction_words_golden():
    from autoedit import instructions, translate_local
    from autoedit.paths import configs_dir
    for t in ("add grain", "more grain", "make it grainy", "add some grain", "a grainy look"):
        assert _grain_parse(t) == ([("global", {"grain": 25.0})], []), t
    assert _grain_parse("a little grain")[0] == [("global", {"grain": 12.5})]
    assert _grain_parse("much more grain")[0] == [("global", {"grain": 40.0})]
    got, problems = _grain_parse("add grain and brighten the sky")
    assert not problems and [g[0] for g in got] == ["global", "sky"]
    assert got[0][1] == {"grain": 25.0} and got[1][1]["exposure"] == pytest.approx(0.30)
    got, problems = _grain_parse("brighten the sky and add grain")
    assert not problems and [g[0] for g in got] == ["sky", "global"]
    assert got[0][1]["exposure"] == pytest.approx(0.30) and got[1][1] == {"grain": 25.0}
    for t in ("add grain to the sky", "add grain to the top", "make the jacket grainy"):
        got, problems = _grain_parse(t)
        assert not got and "grain is for the whole photo" in problems[0], t
    got, problems = instructions.parse("add grain", instructions.load_vocab(
        os.path.join(configs_dir(), "instructions.toml")), {"sky"}, mark=((0.1, 0.1, 0.5, 0.5), "area"))
    assert not got and "grain is for the whole photo" in problems[0]
    got, problems = _grain_parse("no grain")
    assert not got and "negation or exclusion" in problems[0]

    vocab = instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))
    assert "grain" not in vocab.limits
    cfg = RunConfig(backend="stub")
    assert "grain" not in translate_local._system(sorted(vocab.limits), ["top"], cfg)
    assert adjust.parse_set("global.grain=20").ops == {"grain": 20.0}
    with pytest.raises(ValueError) as e:
        adjust.parse_set("sky.grain=20")
    assert "global.grain" in str(e.value)


def test_typed_grain_reaches_the_export(tmp_path):
    p = str(_folder(tmp_path, "in") / "a.jpg")

    def run(**kw):
        res = Pipeline.build(_stub(**kw)).process_one(p, do_apply=True)[1]
        return res, res.global_settings.get("grain")
    res, g = run(edit="add grain")
    assert g == 25.0 and [a.status for a in res.adjustments] == ["applied"]
    assert run(edit="add grain", grain=30.0)[1] == 55.0
    res, g = run(edit="add grain, more grain, some grain, film grain, grainy")
    assert g == 100.0 and "clamped" in [a.status for a in res.adjustments]
    assert run(edit="add grain", look="grainy", looks_path=_looks_file(tmp_path, 30))[1] == 49.0

    src = tmp_path / "in"
    batch.run(_stub(), str(src), str(tmp_path / "o1"), workers=1)
    batch.run(_stub(edit="add grain"), str(src), str(tmp_path / "o2"), workers=1)
    assert _bytes(tmp_path / "o1" / "a.jpg") != _bytes(tmp_path / "o2" / "a.jpg")
    notes = _sidecar(tmp_path / "o2" / "a.autoedit.json")["notes"]
    assert any(n.startswith("instruction ") for n in notes) and any(n.startswith("grain: ") for n in notes)
