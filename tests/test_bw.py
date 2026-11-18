"""F24: black and white. Tests 1 to 11: the op (edit/ops.py::bw)."""

import numpy as np

from autoedit.edit import ops
from autoedit.imageio import luma

NEUTRAL = (0.0,) * 6
RED_CHANNEL = (100.0, 100.0, -100.0, -100.0, -100.0, 100.0)
RED_FILTER = (30.0, 15.0, -10.0, -45.0, -60.0, 10.0)
WEIGHTS = (NEUTRAL, RED_CHANNEL, RED_FILTER, (-100.0,) * 6, (100.0,) * 6, (37.0, -12.0, 55.0, -80.0, 3.0, -64.0))
_COLOURS = np.array([[1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 1, 1], [0, 0, 1], [1, 0, 1]], np.float32)


def _frame(shape=(64, 96), lo=0.0, hi=1.0, seed=0):
    return np.random.default_rng(seed).uniform(lo, hi, shape + (3,)).astype(np.float32)


def _grey(x):
    assert np.array_equal(x[..., 0], x[..., 1]) and np.array_equal(x[..., 1], x[..., 2])
    return x[..., 0]


def test_zero_is_the_identity():
    x = _frame()
    assert ops.bw(x, 0) is x
    assert ops.bw(x, 0, *RED_FILTER) is x
    assert ops.apply_ops(x, {"bw": 0.0}) is x
    assert np.array_equal(ops.apply_ops(x, {"bw_red": 50.0, "bw_blue": -50.0}), x)


def test_grey_input_is_unchanged():
    vals = np.concatenate([np.random.default_rng(1).uniform(0, 1, 500),
                           [0, 0.02, 0.0203, 0.0205, 0.5, 0.9795, 0.9797, 0.98, 1]]).astype(np.float32)
    x = np.repeat(vals[None, :, None], 3, axis=-1)
    for w in WEIGHTS:
        for amt in (100, 60):
            assert np.array_equal(ops.bw(x, amt, *w), x), (w, amt)


def test_neutral_weights_keep_the_luma():
    x = _frame(seed=2)
    out = ops.bw(x, 100)
    assert out.dtype == np.float32 and out.shape == x.shape
    y = _grey(out)
    l = luma(x)
    ok = (x.max(axis=-1) >= 0.0205) & (l >= 0.0205)
    assert ok.sum() > 1000
    assert np.abs(y - l)[ok].max() < 1e-6
    x = _frame(lo=0.05, hi=0.95, seed=3)
    out = ops.bw(x, 50)
    assert np.abs(luma(out) - luma(x)).max() < 1e-6
    assert np.abs((out.max(axis=-1) - out.min(axis=-1)) - 0.5 * (x.max(axis=-1) - x.min(axis=-1))).max() < 1e-6


def test_a_weight_moves_its_own_colour_only():
    x = (0.4 + 0.5 * _COLOURS)[None]
    l = luma(x)[0]
    base = _grey(ops.bw(x, 100))[0]
    for i in range(6):
        prev = -1.0
        for v in (-100.0, -50.0, 0.0, 50.0, 100.0):
            w = [0.0] * 6
            w[i] = v
            y = _grey(ops.bw(x, 100, *w))[0]
            others = np.arange(6) != i
            assert np.abs(y - base)[others].max() < 1e-6, (i, v)
            assert v == -100.0 or y[i] > prev + 1e-4, (i, v, y[i], prev)   # rises with the weight
            prev = y[i]
            if v == 100.0:
                assert abs(y[i] - 0.9) < 1e-6
            if v == -100.0:
                want = l[i] - (l[i] - 0.4) * min(1.0, 2.0 * (l[i] - 0.30) / l[i])
                assert abs(y[i] - want) < 1e-6, i


def test_a_red_filter_darkens_a_blue_sky():
    x = _frame(lo=0.05, hi=0.95, seed=4)
    y = _grey(ops.bw(x, 100, *RED_CHANNEL))
    red, l = x[..., 0], luma(x)
    dark = l - (l - red) * np.minimum(1.0, 2.0 * np.maximum(l - 0.30, 0.0) / l)
    want = np.where(red >= l, red, np.maximum(dark, np.minimum(l, 0.30)))
    assert np.abs(y - want).max() < 1e-6
    px = {k: np.array([[v]], np.float32) for k, v in {"sky": (0.35, 0.55, 0.85), "skin": (0.85, 0.62, 0.50),
                                                      "foliage": (0.30, 0.45, 0.20)}.items()}
    d = {k: float(_grey(ops.bw(v, 100, *RED_FILTER))[0, 0] - _grey(ops.bw(v, 100, *NEUTRAL))[0, 0]) for k, v in px.items()}
    assert d["sky"] < -0.06
    assert d["skin"] > 0.02
    assert abs(d["foliage"]) < 0.02


def test_a_dark_pixel_is_never_made_darker():
    x = _frame(seed=5)
    l = luma(x)
    for w in WEIGHTS:
        y = _grey(ops.bw(x, 100, *w))
        low = l <= 0.30
        assert (y[low] >= l[low] - 1e-6).all(), w
        assert (y[~low] >= 0.30 - 1e-6).all(), w
    d = _frame(lo=0.0, hi=0.45, seed=6)
    assert np.median(luma(d)) < 0.30
    for w in WEIGHTS:
        assert np.median(_grey(ops.bw(d, 100, *w))) >= np.median(luma(d)) - 1e-6, w


def test_the_grey_value_lies_between_the_channels():
    x = _frame(shape=(128, 128), seed=7)
    lo, hi = x.min(axis=-1), x.max(axis=-1)
    for w in WEIGHTS:
        y = _grey(ops.bw(x, 100, *w))
        assert (y >= lo - 1e-6).all() and (y <= hi + 1e-6).all(), w


def test_continuous_across_band_edges():
    rng = np.random.default_rng(8)
    n = 200_000
    a = rng.uniform(0, 1, (40_000, 3))
    two = rng.uniform(0, 1, (100_000, 3))
    i = rng.integers(0, 3, 100_000)
    j = (i + rng.integers(1, 3, 100_000)) % 3
    rows = np.arange(100_000)
    two[rows, j] = np.clip(two[rows, i] + rng.uniform(-1e-4, 1e-4, 100_000), 0, 1)
    edge = rng.uniform(0, 0.03, (40_000, 3))
    flip = rng.random((40_000, 3)) < 0.5
    edge = np.where(flip, 1.0 - edge, edge)
    mids = rng.uniform(0.25, 0.35, (20_000, 3))
    x = np.concatenate([a, two, edge, mids]).astype(np.float32)
    assert len(x) == n
    moved = np.clip(x + rng.uniform(-1e-4, 1e-4, x.shape), 0, 1).astype(np.float32)
    assert np.abs(moved - x).max() <= 1e-4 + 1e-7
    for w in WEIGHTS:
        y0 = _grey(ops.bw(x[None], 100, *w))
        y1 = _grey(ops.bw(moved[None], 100, *w))
        assert np.abs(y1 - y0).max() <= 3e-4, w


def test_nothing_is_pushed_into_clipping():
    rng = np.random.default_rng(9)
    pool = np.array([0.0, 0.01, 0.0199, 0.02, 0.0203, 0.0205, 0.03, 0.5, 0.97, 0.9795, 0.98, 0.9805, 0.99, 1.0])
    x = np.concatenate([pool[rng.integers(0, len(pool), (20_000, 3))], rng.uniform(0, 1, (5_000, 3)),
                        np.where(rng.random((5_000, 3)) < 0.5, rng.uniform(0, 0.05, (5_000, 3)),
                                 rng.uniform(0.95, 1, (5_000, 3)))]).astype(np.float32)[None]
    t = 1e-6
    for amt in (100, 50):
        for w in WEIGHTS:
            out = ops.bw(x, amt, *w)
            assert not ((x >= 0.02) & (out < 0.02 - t)).any(), (amt, w)
            lx, lo = luma(x), luma(out)
            assert not ((lx >= 0.02) & (lo < 0.02 - t)).any(), (amt, w)
            assert not ((lx <= 0.98) & (lo > 0.98 + t)).any(), (amt, w)
            up = (x <= 0.98) & (out > 0.98 + t)
            assert not (up & (lx[..., None] <= 0.98)).any(), (amt, w)


def test_shapes_and_small_frames():
    for shape in ((1, 1), (16, 16), (33, 70), (301, 127)):
        x = _frame(shape, seed=10)
        for amt in (100, 40):
            out = ops.bw(x, amt, *RED_FILTER)
            assert out.shape == x.shape and out.dtype == np.float32
            assert np.isfinite(out).all() and out.min() >= 0.0 and out.max() <= 1.0


def test_registered_as_the_last_colour_step():
    assert ops.BW_KEYS == ("bw", "bw_red", "bw_yellow", "bw_green", "bw_cyan", "bw_blue", "bw_magenta")
    assert ops.OP_BOUNDS["bw"] == (0.0, 100.0)
    for k in ops.BW_KEYS[1:]:
        assert ops.OP_BOUNDS[k] == (-100.0, 100.0)
    for k in ops.BW_KEYS:
        assert k in ops.OP_NAMES and k not in ops.SPATIAL_OPS and k in ops._COLOUR_KEYS
    i = ops.STEPS.index(ops.BW_KEYS)
    assert ops.STEPS[i - 1] == ops._GRADE_KEYS and ops.STEPS[i + 1] == ("denoise", "denoise_stars")
    x = _frame((48, 64), seed=11)
    colour = {"vibrance": 25.0, "saturation": 15.0, "hsl_blue_sat": 30.0, "grade_shadows_temp": 20.0,
              "grade_highlights_temp": -15.0}
    w = {"bw_red": 30.0, "bw_cyan": -45.0, "bw_blue": -60.0}
    out = ops.apply_ops(x, {**colour, "bw": 100.0, **w, "sharpen": 20.0})
    want = ops.sharpen(ops.bw(ops.apply_ops(x, colour), 100.0, w["bw_red"], 0.0, 0.0, w["bw_cyan"], w["bw_blue"], 0.0), 20.0)
    assert np.array_equal(out, want)
    # sharpen acts on a neutral picture with the same amount in each channel: the result stays neutral
    _grey(out)


# ---- Tests 12 to 19 (T2): the looks, the pipeline, the gates ---------------------------------

import pytest
from PIL import Image as PILImage

from autoedit import adjust, looks as looks_mod
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline

GOLDEN_BASE = {"exposure": 0.4, "contrast": 10.0, "curve": 10.0, "clarity": 10.0, "dehaze": 10.0, "vibrance": 4.0}
_BW_WEIGHTS = {"bw_red": 30.0, "bw_yellow": 15.0, "bw_green": -10.0, "bw_cyan": -45.0, "bw_blue": -60.0,
               "bw_magenta": 10.0}
GOLDEN = {
    "bw_classic": {"contrast": 10.0, "curve": 19.0, "clarity": 12.0, "dehaze": 8.0, "bw": 100.0},
    "bw_red_filter": {"contrast": 10.0, "curve": 19.0, "clarity": 12.0, "dehaze": 8.0, "bw": 100.0, **_BW_WEIGHTS},
    "bw_soft": {"contrast": 7.0, "curve": 0.0, "clarity": 5.0, "dehaze": -8.0, "bw": 100.0,
                "bw_red": 15.0, "bw_yellow": 10.0},
    "film_warm": {"contrast": 8.5, "curve": 12.0, "clarity": 6.0, "dehaze": -2.0, "saturation": -6.0,
                  "grade_shadows_temp": 8.0, "grade_highlights_temp": 15.0, "grade_highlights_tint": 2.0},
    "film_cool": {"contrast": 10.0, "curve": 15.0, "clarity": 9.0, "dehaze": 3.0, "saturation": -24.0,
                  "grade_shadows_temp": -15.0, "grade_shadows_tint": -3.0, "grade_highlights_temp": -10.0},
    "film_faded": {"contrast": 7.5, "curve": -1.0, "clarity": 5.0, "dehaze": -14.0, "saturation": -18.0,
                   "grade_shadows_temp": -5.0, "grade_highlights_temp": 5.0},
    "cross_process": {"contrast": 12.0, "curve": 20.0, "clarity": 10.0, "dehaze": 4.0,
                      "grade_shadows_temp": -14.0, "grade_shadows_tint": -8.0,
                      "grade_highlights_temp": 8.0, "grade_highlights_tint": -4.0},
    "instant": {"contrast": 8.5, "curve": 7.0, "clarity": 5.0, "dehaze": -10.0, "saturation": -10.0,
                "grade_shadows_temp": -8.0, "grade_shadows_tint": -5.0,
                "grade_highlights_temp": 10.0, "grade_highlights_tint": 2.0},
}
_OLD_LOOKS = {"natural", "punchy", "soft_portrait", "dramatic", "cinematic_preserved"}
_NIGHT_LOOKS = {"astro"}   # the look of the astro pack (F31): clarity and dehaze eased, nothing else, no preserve_exposure
_BW_LOOKS = {"bw_classic", "bw_red_filter", "bw_soft"}


def _packaged():
    return looks_mod.load(RunConfig().looks_path)


def _bw_scene(path, w=600, h=400, patch=True, tint=(0, 0, 0)):
    a = np.zeros((h, w, 3), np.float32)
    a[: h * 55 // 100] = [100, 140, 200]   # sky
    a[h * 55 // 100:] = [60, 110, 70]      # foliage
    yy, xx = np.mgrid[0:h, 0:w]
    a[(((xx - w / 2) / (w * 0.22)) ** 2 + ((yy - h * 0.62) / (h * 0.28)) ** 2) <= 1.0] = [200, 150, 120]
    if patch:
        a[:60, :200] = [255, 250, 225]     # a blown warm patch, for the highlight hold
    a = np.clip(a + np.asarray(tint, np.float32), 0, 255)
    PILImage.fromarray(a.astype(np.uint8), "RGB").save(path, quality=95)


def _photo(tmp_path, name="a.jpg", **kw):
    p = str(tmp_path / name)
    _bw_scene(p, **kw)
    return p


def _run(p, adjustments=None, **kw):
    return Pipeline.build(RunConfig(backend="stub", **kw)).process_one(p, do_apply=True, adjustments=adjustments)


def _is_neutral(x):
    return np.array_equal(x[..., 0], x[..., 1]) and np.array_equal(x[..., 1], x[..., 2])


def _spread(x):
    return x.max(axis=-1) - x.min(axis=-1)


def test_the_default_is_unchanged(tmp_path):
    zero = looks_mod.Look()
    assert all(getattr(zero, k) == 0.0 for k in ("dehaze_add", *ops.BW_KEYS))
    assert looks_mod._BW_FIELDS == ops.BW_KEYS
    packaged = _packaged()
    assert set(packaged) == _OLD_LOOKS | _NIGHT_LOOKS | set(GOLDEN)
    for name, look in packaged.items():
        has_bw = any(getattr(look, k) for k in ops.BW_KEYS)
        assert has_bw == (name in _BW_LOOKS), name
        assert not look.preserve_exposure and look.grain == 0.0, name
        if name in _OLD_LOOKS:
            assert look.dehaze_add == 0.0, name
    assert packaged["natural"] == looks_mod.Look(clarity_mul=0.7, dehaze_mul=0.8, curve_add=5.0)
    rgb, res = _run(_photo(tmp_path))
    assert "bw" not in res.global_settings and not any(k in res.global_settings for k in ops.BW_KEYS)
    assert not any(n.startswith("black and white") for n in res.notes)
    assert not _is_neutral(rgb) and _spread(rgb).max() > 0.1


def test_dehaze_add_is_global_and_scaled():
    look = looks_mod.Look(dehaze_mul=0.5, dehaze_add=-8.0)
    g, r = {"dehaze": 10.0}, {"dehaze": 10.0}
    looks_mod.shape(g, look, 0.8, is_global=True)
    looks_mod.shape(r, look, 0.8)
    assert g["dehaze"] == pytest.approx((10 * 0.5 - 8) * 0.8)
    assert r["dehaze"] == pytest.approx(10 * 0.5 * 0.8)
    g = {}
    looks_mod.shape(g, look, 1.0, is_global=True)
    assert g == {"dehaze": -8.0}
    g = {"dehaze": 10.0, "contrast": 5.0}
    looks_mod.shape(g, looks_mod.Look(), 1.0, is_global=True)
    assert g == {"dehaze": 10.0, "contrast": 5.0}


def test_a_bw_look_ends_neutral(tmp_path):
    p = _photo(tmp_path)
    ref = _photo(tmp_path, "ref.jpg", patch=False, tint=(30, 0, -30))
    look = _packaged()["bw_red_filter"]
    for intensity in (0.3, 0.8, 1.5):
        for name in sorted(_BW_LOOKS):
            rgb, res = _run(p, look=name, intensity=intensity)
            assert _is_neutral(rgb), (name, intensity)
        for name in sorted(_BW_LOOKS):          # every black and white look: its values, unscaled, and no others
            want = _packaged()[name]
            rgb, res = _run(p, look=name, intensity=intensity)
            for k in ops.BW_KEYS:
                assert res.global_settings.get(k, 0.0) == pytest.approx(getattr(want, k)), (name, k)
        rgb, res = _run(p, look="bw_red_filter", intensity=intensity)
        assert ("black and white: 100 of 100 (red +30, yellow +15, green -10, cyan -45, blue -60, "
                "magenta +10)") in res.notes
    rgb, res = _run(p, look="bw_classic", palette="warm_gold")
    assert _is_neutral(rgb)
    assert "palette: none, the photo is black and white" in res.notes
    assert not any(n.startswith("palette: warm_gold") for n in res.notes)
    assert any(n.startswith("palette: warm_gold") for n in _run(p, look="natural", palette="warm_gold")[1].notes)
    rgb, res = _run(p, look="bw_classic", highlight_hold=True)
    assert _is_neutral(rgb) and any(n.startswith("highlight hold") for n in res.notes)
    rgb, res = _run(p, look="bw_classic", reference=ref)
    assert _is_neutral(rgb)
    rgb, res = _run(p, look="natural", adjustments=[adjust.parse_set("global.saturation=-100")])
    assert not _is_neutral(rgb)


def test_a_decider_cannot_set_or_drop_it(tmp_path):
    p = _photo(tmp_path)

    def run_with(add, look):
        pipe = Pipeline.build(RunConfig(backend="stub", look=look))
        inner = pipe.decider

        class Adds:
            name = inner.name

            def decide(self, ctx):
                g, r = inner.decide(ctx)
                g.update(add)
                for s in r.values():
                    s.update(add)
                return g, r
        pipe.decider = Adds()
        return pipe.process_one(p, do_apply=True)
    rgb, res = run_with({"bw": 60.0, "bw_red": 30.0}, "natural")
    assert not any(k in res.global_settings for k in ops.BW_KEYS)
    assert not any(k in m.ops for m in res.masks for k in ops.BW_KEYS)
    assert not _is_neutral(rgb)
    rgb, res = run_with({"bw": 10.0, "bw_blue": -90.0}, "bw_classic")
    assert res.global_settings["bw"] == 100.0 and "bw_blue" not in res.global_settings
    assert _is_neutral(rgb)


def test_region_edits_cannot_bring_colour_back(tmp_path):
    p = _photo(tmp_path)
    sets = ["sky.saturation=80", "skin.temp=40", "global.vibrance=60", "global.grade_shadows_temp=-60"]
    rgb, res = _run(p, [adjust.parse_set(s) for s in sets], look="bw_red_filter", prompt="vibrant warm sunset")
    assert _is_neutral(rgb)
    assert len(res.adjustments) == 4 and all(a.status in ("applied", "clamped") for a in res.adjustments)


def test_set_and_instructions_reach_the_op(tmp_path):
    p = _photo(tmp_path)
    rgb, res = _run(p, [adjust.parse_set("global.bw=100")], look="natural")
    assert _is_neutral(rgb) and res.global_settings["bw"] == 100.0
    rgb, res = _run(p, [adjust.parse_set("global.bw=100"), adjust.parse_set("global.bw=100")], look="natural")
    assert _is_neutral(rgb) and res.global_settings["bw"] == 100.0
    rgb, res = _run(p, [adjust.parse_set("global.bw=-40")], look="bw_classic")
    assert _is_neutral(rgb) and res.global_settings["bw"] == 100.0
    assert [a.status for a in res.adjustments] == ["clamped"]
    for s in ("global.bw=-40", "global.bw_blue=-90"):
        rgb, res = _run(p, [adjust.parse_set(s)], look="natural")
        assert "bw" not in res.global_settings and not _is_neutral(rgb), s
    _, res = _run(p, [adjust.parse_set("global.bw_blue=-90")], look="natural")
    assert "bw_blue" not in res.global_settings                     # a weight alone does nothing, and is dropped
    assert any("bw_blue dropped" in n for n in res.notes)
    rgb, res = _run(p, [adjust.parse_set("sky.bw=100")], look="natural")
    assert [a.status for a in res.adjustments] == ["applied"] and "bw" not in res.global_settings
    assert _spread(rgb[70:130]).mean() < 0.01 and _spread(rgb[300:]).mean() > 0.05


def test_look_golden():
    packaged = _packaged()
    astro = dict(GOLDEN_BASE)
    looks_mod.shape(astro, packaged["astro"], 1.0, is_global=True)
    assert astro == {**GOLDEN_BASE, "clarity": pytest.approx(7.0), "dehaze": pytest.approx(8.0)}
    for name, want in GOLDEN.items():
        look = packaged[name]
        got = dict(GOLDEN_BASE)
        looks_mod.shape(got, look, 1.0, is_global=True)
        got.update({k: getattr(look, k) for k in ops.BW_KEYS if getattr(look, k)})
        expect = {**GOLDEN_BASE, **want}
        assert set(got) == set(expect), name
        for k, v in expect.items():
            assert got[k] == pytest.approx(v), (name, k)
        half = dict(GOLDEN_BASE)
        looks_mod.shape(half, look, 0.5, is_global=True)
        assert not any(k in half for k in ops.BW_KEYS), name   # shape leaves bw to the pipeline: never scaled


def _chart():
    ramp = np.repeat(np.linspace(0.0, 1.0, 101, dtype=np.float32)[:, None], 3, axis=-1)
    patches = np.concatenate([np.tile(np.array(c, np.float32), (n, 1)) for c, n in
                              (((0.35, 0.55, 0.85), 34), ((0.85, 0.62, 0.50), 34), ((0.30, 0.45, 0.20), 33))])
    return np.stack([ramp, patches])


def _shaped(name, base):
    s = dict(base)
    look = _packaged()[name]
    looks_mod.shape(s, look, 0.8, is_global=True)
    s.update({k: getattr(look, k) for k in ops.BW_KEYS if getattr(look, k)})
    return s


def _chart_out(name, base):
    return np.clip(ops.apply_ops(_chart(), _shaped(name, base)), 0.0, 1.0)


def _measure(name):
    out = _chart_out(name, {"curve": 10.0})
    ramp, patch, y = out[0], out[1], luma(out)
    return {"black": float(y[0, 0]), "warm": {i: float(ramp[i, 0] - ramp[i, 2]) for i in (15, 80)},
            "colour": float(_spread(patch).mean()), "sky": float(y[1, 10]), "skin": float(y[1, 50]),
            "ramp0": ramp[0], "ramp80": ramp[80], "grey": _is_neutral(out)}


def _spread_of(name):
    y = luma(_chart_out(name, {"contrast": 8.0, "curve": 10.0}))[0]
    return float(y[75] - y[25])


def test_looks_do_what_their_names_say():
    m = {n: _measure(n) for n in ("natural", *GOLDEN)}
    for n in _BW_LOOKS:
        assert m[n]["grey"], n
    for n in ("natural", "bw_classic", "bw_red_filter"):
        assert m[n]["black"] == 0.0, n
    assert m["bw_red_filter"]["sky"] < m["bw_classic"]["sky"] - 0.05
    assert m["bw_red_filter"]["skin"] > m["bw_classic"]["skin"] + 0.02
    assert m["bw_soft"]["black"] > 0.02
    assert _spread_of("bw_soft") < _spread_of("bw_classic") - 0.03
    assert m["bw_soft"]["skin"] > m["bw_classic"]["skin"]
    assert m["film_warm"]["warm"][15] > 0.004 and m["film_warm"]["warm"][80] > 0.004
    assert m["film_cool"]["warm"][15] < -0.008 and m["film_cool"]["warm"][80] < -0.002
    assert m["film_warm"]["colour"] < m["natural"]["colour"] and m["film_cool"]["colour"] < m["natural"]["colour"]
    assert m["film_faded"]["black"] > 0.04
    assert _spread_of("film_faded") < _spread_of("natural") - 0.03
    assert m["film_faded"]["colour"] < 0.85 * m["natural"]["colour"]
    assert _spread_of("cross_process") > _spread_of("natural") + 0.002
    assert m["cross_process"]["warm"][15] < -0.008 and m["cross_process"]["warm"][80] > 0.004
    assert m["cross_process"]["ramp80"][1] > m["cross_process"]["ramp80"][2]
    assert m["instant"]["black"] > 0.03
    assert m["instant"]["warm"][15] < -0.004 and m["instant"]["warm"][80] > 0.004
    for n in ("film_warm", "film_cool", "film_faded", "cross_process", "instant"):
        assert m[n]["black"] > 0.01 and (m[n]["ramp0"] > 0).all(), n
