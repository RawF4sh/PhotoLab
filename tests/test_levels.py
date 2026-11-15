"""F5: black and white point (edit/ops.py::levels) and the opt-in setting that gates its rule."""

import argparse
import importlib.util
import pathlib

import numpy as np
from PIL import Image as PILImage

from autoedit import cli
from autoedit.contract import Adjustment, RunConfig
from autoedit.edit import ops, par
from autoedit.imageio import luma
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.rules import eval_expr
from autoedit.rules import load as load_rules
from autoedit.web import service

_TR = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "tone_report.py"


def _frame(lo, hi, shape=(300, 400), seed=0):
    """A grey-ish frame whose luma spans [lo, hi] (equal channels, so luma == the value)."""
    v = np.random.default_rng(seed).uniform(lo, hi, shape).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=2)


def _pct(x, q):
    return float(np.percentile(luma(x), q))


def test_curve_is_pinned_and_monotonic():
    rng = np.random.default_rng(1)
    l = np.linspace(0.0, 1.0, 4001, dtype=np.float32)
    for _ in range(200):
        f = rng.uniform(0.031, 0.45)
        b = rng.uniform(1e-4, 1.0) * min(f - 0.03, 0.3 * (0.5 - f))
        uf = rng.uniform(0.031, 0.45)             # 1 - w, the mirror of f
        a = rng.uniform(1e-4, 1.0) * min(uf - 0.03, 0.3 * (0.5 - uf))
        y = ops._levels_curve(l, f, b, 1.0 - uf, a)
        assert y[0] == 0.0 and y[-1] == 1.0
        assert np.diff(y).min() >= -1e-6
        assert y.min() >= 0.0 and y.max() <= 1.0
    # The op moves the pivots to the frame's own 10th and 90th percentile: anywhere between the
    # measured point and mid-grey, with the amount capped by the slope limit for that pivot.
    for _ in range(400):
        f, uf = rng.uniform(0.031, 0.45), rng.uniform(0.031, 0.45)
        p, q = rng.uniform(f + 1e-4, 0.5), rng.uniform(uf + 1e-4, 0.5)
        b = rng.uniform(1e-4, 1.0) * min(f - 0.03, 0.3 * (p - f))
        a = rng.uniform(1e-4, 1.0) * min(uf - 0.03, 0.3 * (q - uf))
        y = ops._levels_curve(l, f, b, 1.0 - uf, a, p, q)
        assert y[0] == 0.0 and y[-1] == 1.0 and np.isfinite(y).all()
        assert np.diff(y).min() >= -1e-6
        body = (l >= p) & (l <= 1.0 - q)
        assert np.array_equal(y[body], l[body])              # nothing between the pivots moves
        assert float(np.diff(y).max() / (l[1] - l[0])) <= 1.65   # the slope limit holds


def test_curve_values():
    y = lambda l, *k: float(ops._levels_curve(np.array([l], np.float32), *k)[0])
    k = (0.10, 0.07, 0.8, 0.0)
    assert abs(y(0.10, *k) - 0.03) < 1e-6
    assert abs(y(0.5, *k) - 0.5) < 1e-6 and abs(y(0.8, *k) - 0.8) < 1e-6
    assert abs(y(0.3, *k) - (0.3 - 0.07 * 0.25)) < 1e-6
    k = (0.10, 0.0, 0.80, 0.09)
    assert abs(y(0.80, *k) - 0.89) < 1e-6
    assert abs(y(0.2, *k) - 0.2) < 1e-6


def test_identity():
    x = _frame(0.10, 0.90)
    assert ops.levels(x, 0, 0) is x
    assert ops.apply_ops(x, {}) is x
    dark = _frame(0.01, 0.90, seed=1)
    dark[0, 0] = 0.0
    assert ops.levels(dark, 100, 0) is dark          # already has a true black
    high_key = _frame(0.60, 0.95, seed=2)
    assert ops.levels(high_key, 100, 0) is high_key  # floor at or above mid-grey: the op fades out


def _tailed(body_lo, body_hi, tail_to, shape=(300, 400), seed=0, share=0.06):
    """A frame whose body spans [body_lo, body_hi] and where `share` of the pixels form a tail
    that reaches `tail_to` (darker than the body, or brighter): what a real photo looks like."""
    rng = np.random.default_rng(seed)
    v = rng.uniform(body_lo, body_hi, shape).astype(np.float32)
    tail = rng.random(shape) < share
    lo, hi = (tail_to, body_lo) if tail_to < body_lo else (body_hi, tail_to)
    v[tail] = rng.uniform(lo, hi, int(tail.sum())).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=2)


def test_black_point_moves_the_floor():
    x = _tailed(0.30, 0.90, 0.10)
    y = ops.levels(x, 100, 0)
    floor = _pct(y, 0.1)
    assert 0.028 <= floor <= 0.045, floor
    body = luma(x) >= _pct(x, 12.0)          # above the darkest tenth (the op measures it on a sample)
    assert np.array_equal(y[body], x[body])
    half = _pct(ops.levels(x, 50, 0), 0.1)
    assert 0.055 <= half <= 0.075, half
    print(f"levels black: floor {floor:.4f} at 100, {half:.4f} at 50")


def test_white_point_moves_the_white():
    x = _tailed(0.10, 0.50, 0.80)
    y = ops.levels(x, 0, 100)
    white = _pct(y, 99.9)
    assert 0.86 <= white <= 0.945, white
    body = luma(x) <= _pct(x, 88.0)          # below the brightest tenth
    assert np.array_equal(y[body], x[body])
    print(f"levels white: {white:.4f} at 100, {_pct(ops.levels(x, 0, 50), 99.9):.4f} at 50")


def test_the_body_of_a_dark_frame_does_not_move():
    # The quality gate fails an edit that makes the median of a dark photo's bottom third (or of
    # its foreground) darker. With the pivot at mid-grey the black point did that on real photos
    # (bottom third down by up to 0.03). The darkest part of this frame IS its bottom third.
    rng = np.random.default_rng(7)
    v = rng.uniform(0.40, 0.90, (300, 400)).astype(np.float32)
    v[200:] = rng.uniform(0.08, 0.26, (100, 400)).astype(np.float32)
    x = np.repeat(v[..., None], 3, axis=2)
    y = ops.levels(x, 100, 0)
    assert _pct(y, 0.1) < _pct(x, 0.1) - 0.01                      # the floor does move
    assert float(np.median(luma(y)[200:])) == float(np.median(luma(x)[200:]))
    for share in (0.25, 0.33, 0.5):                                 # the darkest quarter, third, half
        cut = np.sort(luma(x).ravel())[int(share * v.size)]
        area = luma(x) <= cut
        assert float(np.median(luma(y)[area])) == float(np.median(luma(x)[area])), share


def test_a_flat_dark_end_is_left_alone():
    # Fog: the darkest tenth of the frame is one flat tone, so there is no black to anchor.
    x = _tailed(0.200, 0.204, 0.90, share=0.5)
    y = ops.levels(x, 100, 0)
    assert abs(_pct(y, 0.1) - _pct(x, 0.1)) < 0.002
    assert float(np.abs(y - x).max()) < 0.002


def test_no_new_clipping():
    rng = np.random.default_rng(3)
    x = rng.uniform(0.021, 0.979, (300, 400, 3)).astype(np.float32)
    # push the luma floor up to about 0.08 and the white down, keeping saturated channels
    x = np.where(luma(x)[..., None] < 0.08, np.clip(x + 0.12, 0.021, 0.979), x)
    x = np.where(luma(x)[..., None] > 0.9, np.clip(x - 0.05, 0.021, 0.979), x).astype(np.float32)
    assert x.min() >= 0.021 and x.max() <= 0.979
    y = ops.levels(x, 100, 100)
    assert not np.array_equal(y, x)
    assert y.min() >= 0.02 and y.max() <= 0.98, (y.min(), y.max())
    # The statistics count clipping on the luma. A pixel with one channel already at 0 has no
    # channel to hold it, so the luma itself must be held. These pixels lie below the floor (the
    # toe pulls them hardest) and off the op's sampling grid, so the measured floor is the same.
    z = x.copy()
    rows, cols = np.full(60, 1), np.arange(1, 301, 5)
    z[rows, cols] = (0.05, 0.06, 0.0)
    assert (luma(z)[rows, cols] >= 0.0205).all()
    out = luma(ops.levels(z, 100, 100))[rows, cols]
    assert float(out.min()) >= 0.02, float(out.min())


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


def test_measurement_is_whole_frame_and_strips_are_identical():
    x = _frame(0.10, 0.90, shape=(700, 900), seed=4)
    x[:350] = np.clip(x[:350] + 0.25, 0.0, 1.0)      # top half is brighter: its own floor differs
    with _Pinned(4):
        four = ops.levels(x, 100, 100)
    with _Pinned(1):
        one = ops.levels(x, 100, 100)
        top_alone = ops.levels(x[:350], 100, 100)
    assert np.array_equal(four, one)
    assert not np.array_equal(top_alone, one[:350])


def test_registered_in_the_frozen_order():
    for n in ("black_point", "white_point"):
        assert n in ops.OP_NAMES and ops.OP_BOUNDS[n] == (0.0, 100.0) and n in ops.SPATIAL_OPS
    names = [k for step in ops.STEPS for k in step]
    assert names.index("clarity") < names.index("black_point") < names.index("vibrance")
    assert ops._TONE_KEYS == {k for step in ops.STEPS[:5] for k in step}
    assert ops._TONE_KEYS.isdisjoint({"dehaze", "black_point"})
    assert {"vibrance", "hsl_skin_sat", "grade_shadows_temp"} <= ops._COLOUR_KEYS
    assert ops._COLOUR_KEYS.isdisjoint({"clarity", "black_point", "white_point", "sharpen"})
    x = _frame(0.10, 0.90, shape=(96, 96), seed=5)
    assert np.array_equal(ops.apply_ops(x, {"black_point": 100}), ops.levels(x, 100, 0))
    bundle = {"clarity": 20.0, "black_point": 70.0, "white_point": 40.0, "vibrance": 25.0, "contrast": 10.0}
    want = ops.apply_ops(x, bundle)
    assert not np.array_equal(want, ops.apply_ops(x, {k: v for k, v in bundle.items() if k != "black_point"}))
    for cut in range(len(ops.STEPS) + 1):
        first = {k for step in ops.STEPS[:cut] for k in step}
        pre = {k: v for k, v in bundle.items() if k in first}
        post = {k: v for k, v in bundle.items() if k not in first}
        assert np.array_equal(ops.apply_ops(ops.apply_ops(x, pre), post), want), cut


def test_rule_golden():
    plain = load_rules(config_path("rules.toml"))
    assert "opt_in" not in plain   # a plain load never sees an opt-in rule
    assert all("black_point" not in plain[scene]["global"] for scene in ("people", "landscape", "other"))
    rule = load_rules(config_path("rules.toml"), enable=["levels"])
    exprs = {scene: rule[scene]["global"]["black_point"] for scene in ("people", "landscape", "other")}
    assert len(set(exprs.values())) == 1
    e = exprs["people"]
    # Full strength whenever the statistics are valid: the op protects the picture by itself
    # (see test_the_body_of_a_dark_frame_does_not_move), so the rule no longer holds it back.
    ctx = dict(pro_stats_valid=1, fg_p50=0.45, band_bottom_p50=0.40, planned_exposure=0.0)
    assert eval_expr(e, ctx) == 100.0
    assert eval_expr(e, dict(ctx, fg_p50=0.20, band_bottom_p50=0.0)) == 100.0
    assert eval_expr(e, dict(ctx, pro_stats_valid=0)) == 0.0


def _washed(path):
    rng = np.random.default_rng(7)
    v = rng.uniform(0.12, 0.85, (160, 200)).astype(np.float32)
    a = np.repeat(v[..., None], 3, axis=2) * np.array([1.0, 0.97, 0.94], np.float32)
    PILImage.fromarray((a * 255).astype(np.uint8), "RGB").save(path, quality=95)


def test_setting_gates_the_rule(tmp_path):
    src = str(tmp_path / "washed.jpg")
    _washed(src)
    text = pathlib.Path(config_path("rules.toml")).read_text()
    stripped = tmp_path / "rules_without.toml"
    stripped.write_text("\n".join(l for l in text.split("\n") if not l.startswith("black_point")))
    off, r_off = Pipeline.build(RunConfig(backend="stub")).process_one(src)
    ref, _ = Pipeline.build(RunConfig(backend="stub", rules_path=str(stripped))).process_one(src)
    assert "black_point" not in r_off.global_settings and "white_point" not in r_off.global_settings
    assert np.array_equal(off, ref)
    on, r_on = Pipeline.build(RunConfig(backend="stub", levels=True)).process_one(src)
    assert r_on.global_settings.get("black_point", 0.0) > 0.0
    assert _pct(on, 0.1) < _pct(off, 0.1)


def test_adjustment_works_without_the_setting(tmp_path):
    src = str(tmp_path / "washed.jpg")
    _washed(src)
    pipe = Pipeline.build(RunConfig(backend="stub"))
    base, _ = pipe.process_one(src)
    adj = Adjustment(target="global", ops={"black_point": 80}, scope="photo")
    out, res = pipe.process_one(src, adjustments=[adj])
    assert res.global_settings.get("black_point", 0.0) > 0.0
    assert _pct(out, 0.1) < _pct(base, 0.1)


def _tone_report():
    spec = importlib.util.spec_from_file_location("tone_report", _TR)
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    return tr


def test_cli_api_and_report_carry_the_setting(tmp_path):
    p = argparse.ArgumentParser()
    cli._add_run_flags(p)
    ns = p.parse_args(["in", "--levels"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).levels is True
    ns = p.parse_args(["in"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).levels is False
    conf = tmp_path / "run.toml"
    conf.write_text("[run]\nlevels = true\n")
    ns.config = str(conf)
    assert cli.load_run_config(ns).levels is True
    assert service.make_cfg({"levels": True}).levels is True

    tr = _tone_report()
    errs = []

    def boom(msg):
        errs.append(msg)

    assert tr._parse_sets(["levels=true", "intensity=1.0", "raw_mode=preview_ev_headroom"], boom) == {
        "levels": True, "intensity": 1.0, "raw_mode": "preview_ev_headroom"}
    assert not errs
    tr._parse_sets(["nope=1"], boom)
    assert len(errs) == 1
