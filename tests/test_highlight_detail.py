"""F50: the highlight knee holds a bright pixel by the level of the bright area around it, so faint cloud
and snow texture keep their contrast. Op math, the base, the goal on a synthetic sky, and strips."""

import numpy as np

from autoedit.edit import ops, par
from autoedit.edit.filters import gaussian_blur
from autoedit.imageio import linear_to_srgb, luma
from autoedit.quality import assess_pixels

_W = ops._WHITE_LIN


def test_gating_changes_nothing_when_off():
    x = np.full((16, 16, 3), 0.5, np.float32)
    assert np.array_equal(ops.apply_ops(x, {"highlight_detail": 100.0}), x)
    assert ops.apply_ops(x, {}) is x
    sky = np.full((32, 32, 3), 0.95, np.float32)
    bundle = {"exposure": 0.24, "highlight_rolloff": 32.0}
    assert np.array_equal(ops.apply_ops(sky, bundle), ops.apply_ops(sky, {**bundle, "highlight_detail": 0.0}))
    rng = np.random.default_rng(0)
    lin = (rng.random((40, 50, 3)) * 1.3).astype(np.float32)
    ratio = (0.9 + 0.2 * rng.random((40, 50))).astype(np.float32)
    plain = ops.highlight_rolloff(lin, 32.0)
    assert np.array_equal(ops.highlight_rolloff(lin, 32.0, ratio, 0.0), plain)
    dark = (rng.random((8, 8, 3)) * 0.5).astype(np.float32)
    assert ops.highlight_rolloff(dark, 32.0) is dark       # nothing over the knee: the same object


def test_knee_math_per_ratio():
    kp = _W * 0.84                                         # amount 32
    xs = np.linspace(0.0, 2.0, 2001, dtype=np.float32)
    for rv in (1 / 1.1, 0.95, 1.0, 1.05, 1.1):
        r = np.full((1, xs.size), rv, np.float32)
        o = ops.highlight_rolloff(xs[None, :, None].repeat(3, -1), 32.0, r, 100.0)[0, :, 0]
        assert o.max() < _W and luma(linear_to_srgb(o[:, None].repeat(3, -1))).max() <= 0.98, rv
        assert np.all(np.diff(o) >= -1e-7), rv
        low = (xs <= kp) & (xs * rv <= kp)
        assert np.array_equal(o[low], xs[low]), rv         # untouched under the knee
        if rv == 1.0:
            assert np.abs(o - ops.highlight_rolloff(xs, 32.0)).max() < 1e-6
        if rv >= 1.0:
            hi = xs * rv > kp
            assert np.allclose(o[hi], ops.highlight_rolloff(xs * rv, 32.0)[hi] / rv, atol=1e-6)
        else:
            assert np.all(o >= ops.highlight_rolloff(xs, 32.0) - 1e-7)   # never darker than today


def test_detail_ratio_base_comes_from_the_bright_pixels():
    assert np.abs(ops.detail_ratio(np.full((64, 64, 3), 0.95, np.float32)) - 1.0).max() < 1e-5
    assert np.array_equal(ops.detail_ratio(np.full((64, 64, 3), 0.6, np.float32)), np.ones((64, 64), np.float32))
    ridge = np.full((100, 100, 3), 0.95, np.float32)
    ridge[60:] = 0.30
    r = ops.detail_ratio(ridge)
    assert np.array_equal(r[60:], np.ones_like(r[60:]))    # the rock is not bright
    assert np.abs(r[:60] - 1.0).max() < 1e-4               # the sky beside it: its base is the sky


def test_faint_cloud_keeps_its_contrast():
    n = gaussian_blur(np.random.default_rng(5).standard_normal((512, 512)).astype(np.float32), 1.5)
    sky = np.repeat((0.972 + 0.006 * n / n.std())[..., None], 3, axis=-1).astype(np.float32)
    bundle = {"exposure": 0.24, "highlight_rolloff": 32.0}
    today = ops.apply_ops(sky, bundle)
    kept = ops.apply_ops(sky, {**bundle, "highlight_detail": 100.0})

    def keep(out):
        return assess_pixels(sky, out)["metrics"]["highlight_texture"]["minimum_retention"]

    assert keep(today) < 0.3 < 0.5 < keep(kept)
    assert abs(float((luma(kept) - luma(today)).mean())) * 255 < 0.5
    assert luma(kept).max() <= 0.975 + 1e-6


class _Pinned:
    def __init__(self, threads):
        self.threads = threads

    def __enter__(self):
        self.old = (par._threads, par.MIN_PIXELS)
        par.MIN_PIXELS = 0
        par.set_threads(self.threads)

    def __exit__(self, *exc):
        par.MIN_PIXELS = self.old[1]
        par.set_threads(self.old[0])


def test_strips_match_one_pass():
    rng = np.random.default_rng(2)
    x = np.clip(0.9 + 0.08 * rng.standard_normal((700, 900, 3)), 0, 1).astype(np.float32)
    bundle = {"exposure": 0.3, "highlight_rolloff": 35.0, "highlight_detail": 100.0}
    outs = []
    for threads in (1, 4):
        with _Pinned(threads):
            outs.append(ops.apply_ops(x, bundle))
    assert np.array_equal(outs[0], outs[1])
    assert not np.array_equal(outs[0], ops.apply_ops(x, {**bundle, "highlight_detail": 0.0}))


# ---- T2: setting, rules, pipeline ----

def test_rules_and_setting_reach_the_run(tmp_path):
    import argparse
    from autoedit import cli
    from autoedit.contract import RunConfig
    from autoedit.paths import config_path
    from autoedit.rules import eval_expr, load as load_rules
    from autoedit.web import service
    path = config_path("rules.toml")
    scenes = ("people", "landscape", "other")
    assert all("highlight_detail" not in load_rules(path)[s]["global"] for s in scenes)
    on = load_rules(path, enable=["highlight_detail"])
    exprs = {s: on[s]["global"]["highlight_detail"] for s in scenes}
    assert len(set(exprs.values())) == 1
    assert abs(eval_expr(exprs["people"], {"intensity": 0.8}) - 125.0) < 1e-4
    p = argparse.ArgumentParser()
    cli._add_run_flags(p)
    ns = p.parse_args(["in", "--highlight-detail"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).highlight_detail is True
    ns = p.parse_args(["in"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).highlight_detail is False
    conf = tmp_path / "run.toml"
    conf.write_text("[run]\nhighlight_detail = true\n")
    ns.config = str(conf)
    assert cli.load_run_config(ns).highlight_detail is True
    assert service.make_cfg({"highlight_detail": True}).highlight_detail is True
    assert RunConfig().highlight_detail is False


def test_pipeline_gates_the_setting(tmp_path):
    from PIL import Image as PILImage
    from autoedit.contract import RunConfig
    from autoedit.pipeline import Pipeline
    rng = np.random.default_rng(3)
    a = np.clip(0.99 + rng.normal(0.0, 0.004, (160, 200, 3)), 0, 1)
    a[100:] = 0.35
    src = str(tmp_path / "sky.jpg")
    PILImage.fromarray((a * 255).astype(np.uint8), "RGB").save(src, quality=95)
    _, off = Pipeline.build(RunConfig(backend="stub")).process_one(src)
    _, on = Pipeline.build(RunConfig(backend="stub", highlight_detail=True)).process_one(src)
    _, dropped = Pipeline.build(RunConfig(backend="stub", rule_sets=["highlight_detail"])).process_one(src)
    assert "highlight_detail" not in off.global_settings
    assert "highlight_detail" not in dropped.global_settings
    assert on.global_settings.get("highlight_rolloff", 0.0) > 0.0
    assert abs(on.global_settings["highlight_detail"] - 100.0) < 1e-6
    assert any("highlight detail" in n for n in on.notes)
