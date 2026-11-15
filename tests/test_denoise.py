"""F7: noise reduction (edit/ops.py::denoise) and the opt-in setting that gates its rule."""

import argparse
import pathlib

import numpy as np
from PIL import Image as PILImage

from autoedit import cli
from autoedit.contract import RunConfig
from autoedit.edit import ops
from autoedit.edit.filters import gaussian_blur
from autoedit.imageio import luma
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.rules import eval_expr
from autoedit.rules import load as load_rules
from autoedit.web import service

_SCENES = ("people", "landscape", "other")


def _grain(plane):
    return float((plane - gaussian_blur(plane, 2.0)).std())


def _noisy(base, shape=(256, 256), sigma=0.03, seed=0):
    rng = np.random.default_rng(seed)
    base = np.asarray(base, np.float32)
    return (base + rng.normal(0.0, sigma, shape + (3,))).astype(np.float32)


def _flat(rgb, shape=(256, 256)):
    return np.broadcast_to(np.asarray(rgb, np.float32), shape + (3,)).copy()


def test_identity():
    x = _noisy((0.20, 0.18, 0.16))
    assert ops.denoise(x, 0) is x
    assert ops.apply_ops(x, {"denoise": 0.0}) is x
    assert ops.apply_ops(x, {}) is x


def test_reduces_noise_on_a_flat_frame():
    x = _noisy((0.20, 0.18, 0.16))
    y100, y50 = ops.denoise(x, 100), ops.denoise(x, 50)
    g0, g50, g100 = (_grain(luma(a)) for a in (x, y50, y100))
    c0, c100 = _grain(x[..., 0] - x[..., 1]), _grain(y100[..., 0] - y100[..., 1])
    shift = np.abs(y100.mean(axis=(0, 1)) - x.mean(axis=(0, 1))).max()
    print(f"denoise flat: luma grain {g0:.5f} -> {g100:.5f} (50: {g50:.5f}), "
          f"R-G grain {c0:.5f} -> {c100:.6f}, mean shift {shift:.5f}")
    assert g100 < 0.45 * g0
    assert c100 < 0.10 * c0
    assert shift < 0.003
    assert g100 < g50 < g0


def test_keeps_an_edge():
    base = np.empty((256, 256, 3), np.float32)
    base[:, :128], base[:, 128:] = 0.15, 0.55
    x = _noisy(base)
    y = ops.denoise(x, 100)
    l0, l1 = luma(x), luma(y)
    shift = max(abs(float(l1[:, :128].mean() - l0[:, :128].mean())),
                abs(float(l1[:, 128:].mean() - l0[:, 128:].mean())))
    step = float(l1[:, 128].mean() - l1[:, 127].mean())
    print(f"denoise edge: half-mean shift {shift:.5f}, step {step:.4f} of 0.40")
    assert shift < 0.004
    assert step > 0.85 * 0.40


def test_leaves_a_clean_frame_almost_alone():
    ramp = np.linspace(0.1, 0.9, 256, dtype=np.float32)
    x = np.stack([np.tile(ramp, (256, 1)) * f for f in (1.0, 0.9, 0.8)], axis=2)  # a tinted ramp
    d = float(np.abs(ops.denoise(x, 100) - x).max())
    print(f"denoise clean frame: max change {d:.5f}")
    assert d < 0.004


def test_brighter_tones_get_less_luma_smoothing():
    drop = {}
    for tone in (0.15, 0.75):
        x = _noisy((tone,) * 3)
        drop[tone] = 1.0 - _grain(luma(ops.denoise(x, 100))) / _grain(luma(x))
    print(f"denoise luma drop: dark {drop[0.15]:.3f}, bright {drop[0.75]:.3f}")
    assert drop[0.15] > drop[0.75]


def test_shapes_and_small_frames():
    for shape in ((16, 16), (33, 70), (301, 127)):
        x = np.random.default_rng(3).random(shape + (3,), dtype=np.float32)
        y = ops.denoise(x, 80)
        assert y.shape == x.shape and y.dtype == np.float32 and np.isfinite(y).all(), shape


def test_registered_before_sharpen():
    assert "denoise" in ops.OP_NAMES and ops.OP_BOUNDS["denoise"] == (0.0, 100.0)
    assert "denoise" in ops.SPATIAL_OPS
    assert ops.STEPS[-2] == ("denoise",)
    x = _noisy((0.30, 0.30, 0.30), shape=(96, 96), seed=5)
    got = ops.apply_ops(x, {"denoise": 60, "sharpen": 10})
    assert np.array_equal(got, ops.sharpen(ops.denoise(x, 60), 10))


def test_rule_golden():
    path = config_path("rules.toml")
    plain = load_rules(path)
    assert all("denoise" not in plain[s]["global"] for s in _SCENES)
    rule = load_rules(path, enable=["denoise"])
    exprs = {s: rule[s]["global"]["denoise"] for s in _SCENES}
    assert len(set(exprs.values())) == 1
    e = exprs["people"]
    ctx = dict(pro_stats_valid=1, iso=6400, planned_exposure=0.8)
    assert eval_expr(e, dict(ctx, iso=100)) == 0.0
    assert abs(eval_expr(e, ctx) - 100.0) < 1e-4
    assert abs(eval_expr(e, dict(ctx, iso=12800, planned_exposure=0.0)) - 40.0) < 1e-4
    assert abs(eval_expr(e, dict(ctx, iso=3600)) - 50.0) < 1e-4
    assert eval_expr(e, dict(ctx, pro_stats_valid=0)) == 0.0


def _iso_jpeg(path, iso=6400):
    rng = np.random.default_rng(11)
    a = np.clip(0.30 + rng.normal(0.0, 0.05, (160, 200, 3)), 0, 1)
    ex = PILImage.Exif()
    ex[0x8769] = {0x8827: iso}
    PILImage.fromarray((a * 255).astype(np.uint8), "RGB").save(path, quality=95, exif=ex.tobytes())


def test_setting_gates_the_rule(tmp_path):
    src = str(tmp_path / "noisy.jpg")
    _iso_jpeg(src)
    off, r_off = Pipeline.build(RunConfig(backend="stub")).process_one(src)
    on, r_on = Pipeline.build(RunConfig(backend="stub", denoise=True)).process_one(src)
    assert "denoise" not in r_off.global_settings
    assert r_on.global_settings.get("denoise", 0.0) > 0.0
    g_off, g_on = _grain(luma(off.astype(np.float32))), _grain(luma(on.astype(np.float32)))
    print(f"denoise pipeline: output grain {g_off:.5f} -> {g_on:.5f}")
    assert g_on < g_off


def test_cli_and_api_carry_the_setting(tmp_path):
    p = argparse.ArgumentParser()
    cli._add_run_flags(p)
    ns = p.parse_args(["in", "--denoise"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).denoise is True
    ns = p.parse_args(["in"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).denoise is False
    conf = tmp_path / "run.toml"
    conf.write_text("[run]\ndenoise = true\n")
    ns.config = str(conf)
    assert cli.load_run_config(ns).denoise is True
    assert service.make_cfg({"denoise": True}).denoise is True


def test_the_colour_gate_does_not_bring_the_noise_back():
    # The oversaturation gate compares each pixel with the source. With a noisy source as its
    # reference it stamped the source's colour noise back onto the denoised result; the reference
    # now gets the same noise reduction. Without that, five times more colour grain is left here.
    from autoedit.edit.apply import apply_image

    rng = np.random.default_rng(11)
    base = np.array([0.10, 0.12, 0.16], np.float32)            # a dark, slightly blue patch
    src = np.clip(base + rng.normal(0.0, 0.03, (192, 192, 3)), 0.0, 1.0).astype(np.float32)
    settings = {"exposure": 0.8, "denoise": 100.0}
    out = apply_image(src, [], settings, chroma_max_increase=0.30)

    def colour_grain(img):
        plane = img[..., 0] - img[..., 1]
        return float((plane - gaussian_blur(plane, 2.0)).std())

    lifted = apply_image(src, [], {"exposure": 0.8}, chroma_max_increase=0.30)
    # measured: 1.1 % with the denoised reference, 6.1 % with the raw source as reference
    assert colour_grain(out) < 0.03 * colour_grain(lifted), (colour_grain(out), colour_grain(lifted))
    # and the colour of the patch survives: the mean stays blue-ish, close to the plain lift
    assert abs(float(out[..., 2].mean() - out[..., 0].mean()) -
               float(lifted[..., 2].mean() - lifted[..., 0].mean())) < 0.02


def test_a_clean_frame_keeps_its_fine_colour_detail():
    # 3 px stripes of red and green with the same brightness, no noise. The chroma filter is
    # guided by the luma, which is flat here, so at full weight it averages the stripes away
    # (measured before the weight followed the grain: 31 % of the chroma lost at strength 40).
    h, w = 240, 360
    x = np.empty((h, w, 3), np.float32)
    red, green = np.array([0.62, 0.38, 0.40], np.float32), np.array([0.34, 0.50, 0.40], np.float32)
    green *= float(luma(red[None, None])[0, 0] / luma(green[None, None])[0, 0])     # same luma
    x[:] = np.where(((np.arange(w) // 3) % 2 == 0)[None, :, None], red, green)
    chroma = lambda img: float(np.abs(img - luma(img)[..., None]).mean())
    for strength in (40, 100):
        kept = chroma(ops.denoise(x, strength)) / chroma(x)
        assert kept > 0.98, (strength, kept)
    # and with real noise on the same stripes the filter still works at full weight
    noisy = (x + np.random.default_rng(3).normal(0.0, 0.03, x.shape)).astype(np.float32)
    plane = lambda img: img[..., 0] - img[..., 1]
    assert _grain(plane(ops.denoise(noisy, 100))) < 0.5 * _grain(plane(noisy))


def test_gate_reference_can_be_handed_in():
    # The pipeline makes the denoised source once per photo and hands it to every render.
    from autoedit.edit.apply import apply_image

    x = np.clip(_noisy((0.12, 0.13, 0.16), sigma=0.03, seed=5), 0.0, 1.0)
    settings = {"exposure": 0.8, "denoise": 100.0}
    inside = apply_image(x, [], settings, chroma_max_increase=0.30)
    given = apply_image(x, [], settings, chroma_max_increase=0.30, chroma_reference=ops.denoise(x, 100.0))
    assert np.array_equal(inside, given)


def test_same_result_for_any_thread_count():
    from autoedit.edit import par

    x = np.clip(_noisy((0.2, 0.2, 0.25), shape=(700, 900), seed=9), 0.0, 1.0)
    old = (par._threads, par.MIN_PIXELS)
    try:
        par.MIN_PIXELS = 0
        par.set_threads(1)
        one = ops.denoise(x, 80)
        par.set_threads(4)
        four = ops.denoise(x, 80)
    finally:
        par.MIN_PIXELS = old[1]
        par.set_threads(old[0])
    assert np.array_equal(one, four)


def test_the_colour_gate_after_a_reference_match_compares_with_the_denoised_source(tmp_path):
    # The gate that runs again after the reference match is handed the denoised source as its
    # reference. With the plain, noisy source it limits chroma to the noise and brings the grain back
    # (measured: 14 times more colour grain on this frame).
    import autoedit.pipeline as pipeline_mod
    from autoedit.edit import gates
    from autoedit.imageio import load_image

    rng = np.random.default_rng(11)
    noisy = np.clip(0.18 + rng.normal(0.0, 0.04, (160, 200, 3)), 0, 1)
    exif = PILImage.Exif()
    exif[0x8769] = {0x8827: 6400}
    src = str(tmp_path / "noisy.jpg")
    PILImage.fromarray((noisy * 255).astype(np.uint8), "RGB").save(src, quality=95, exif=exif.tobytes())
    ref = np.zeros((120, 160, 3), np.uint8)                 # a saturated reference: the match adds chroma
    ref[:60], ref[60:] = [60, 160, 250], [250, 100, 30]
    PILImage.fromarray(ref, "RGB").save(str(tmp_path / "ref.jpg"), quality=95)
    plain = load_image(src).rgb.astype(np.float32)

    def colour_grain():
        cfg = RunConfig(backend="stub", reference=str(tmp_path / "ref.jpg"), denoise=True)
        out, result = Pipeline.build(cfg).process_one(src)
        assert any(n.startswith("post-reference gate") for n in result.notes)   # the gate really ran
        plane = out[..., 0] - out[..., 1]
        return float((plane - gaussian_blur(plane, 2.0)).std())

    right = colour_grain()

    class _PlainSource:             # the pipeline's own calls of the gate compare with the plain source
        def __getattr__(self, name):
            return getattr(gates, name)

        @staticmethod
        def chroma_guard(source, out, **kw):
            return gates.chroma_guard(plain, out, **kw)

    real = pipeline_mod.gates
    pipeline_mod.gates = _PlainSource()
    try:
        wrong = colour_grain()
    finally:
        pipeline_mod.gates = real
    assert right < 0.3 * wrong, (right, wrong)
