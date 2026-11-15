"""F6: highlight hold (edit/gates.py::highlight_hold) and its setting."""

import pathlib
from types import SimpleNamespace

import numpy as np
from PIL import Image as PILImage

from autoedit import cli
from autoedit.contract import RunConfig
from autoedit.edit import gates
from autoedit.edit.apply import apply_image
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.web import service

_BLOCK = "[highlight_hold]\nstart = 0.80\nfull = 0.985\n"


def test_blown_pixels_stay_white():
    src = np.full((30, 30, 3), 0.5, np.float32)
    src[:10] = 1.0
    src[10:20] = 0.96
    edited = src * 0.9
    out, frac = gates.highlight_hold(src, edited)
    assert np.allclose(out[:10], src[:10], atol=1e-6)
    assert (out[10:20] > edited[10:20]).all() and (out[10:20] < src[10:20]).all()
    assert np.array_equal(out[20:], edited[20:])
    assert abs(frac - 20 / 30) < 1e-6


def test_never_lowers_and_ignores_coloured_highlights():
    rng = np.random.default_rng(0)
    src = rng.uniform(0.85, 1.0, (64, 64, 3)).astype(np.float32)
    edited = rng.uniform(0.0, 1.0, (64, 64, 3)).astype(np.float32)
    out, _ = gates.highlight_hold(src, edited)
    assert (out >= edited).all()
    assert np.array_equal(out[edited >= src], edited[edited >= src])
    s = np.array([[[1.0, 1.0, 0.5]]], np.float32)
    e = np.array([[[0.7, 0.7, 0.3]]], np.float32)
    s = np.concatenate([s, np.ones((1, 1, 3), np.float32)], axis=1)   # a second, white pixel is near white
    e = np.concatenate([e, np.full((1, 1, 3), 0.7, np.float32)], axis=1)
    out, _ = gates.highlight_hold(s, e)
    assert np.array_equal(out[0, 0], e[0, 0])
    assert np.allclose(out[0, 1], 1.0, atol=1e-6)


def test_ramp_is_monotonic():
    n = 2001
    ramp = np.linspace(0.70, 1.0, n, dtype=np.float32)
    src = np.repeat(ramp[None, :, None], 3, axis=2)
    out, _ = gates.highlight_hold(src, src - 0.1)
    v = out[0, :, 0]
    d = np.diff(v)
    assert (d >= -1e-7).all()
    # The smoothstep is the steepest at its middle: slope 1 + 0.1 * 1.5 / (full - start) at most.
    step = float(ramp[1] - ramp[0])
    assert d.max() <= step * (1.0 + 0.1 * 1.5 / (0.985 - 0.80)) * 1.01
    assert abs(v[0] - (ramp[0] - 0.1)) < 1e-6           # below start: untouched
    assert abs(v[-1] - 1.0) < 1e-6                      # at full and above: back to the source


def test_identity_without_near_white():
    rng = np.random.default_rng(1)
    src = rng.uniform(0.0, 0.9, (20, 20, 3)).astype(np.float32)
    edited = (src * 0.8).astype(np.float32)
    out, frac = gates.highlight_hold(src, edited)
    assert out is edited and frac == 0.0


def _sky_frame():
    img = np.full((40, 60, 3), 0.5, np.float32)
    img[:20] = 1.0
    return img


def test_apply_image_runs_the_hold_only_when_asked():
    img = _sky_frame()
    g = {"exposure": -0.5}
    plain = apply_image(img, [], g)
    assert np.array_equal(plain, apply_image(img, [], g, highlight_hold=None))
    assert plain[:20].mean() < 0.9
    report: dict = {}
    held = apply_image(img, [], g, report=report, highlight_hold=(0.94, 0.985))
    assert held[:20].min() > 0.98
    assert report["highlight_held"] > 0.0
    # the sidecar note needs both numbers: what was blown, and what the hold moved
    blown = float((img.min(axis=-1) >= 0.985).mean())
    assert blown > 0.0 and abs(report["highlight_blown"] - blown) < 1e-9
    assert np.array_equal(held[20:], plain[20:])


def _sky_jpeg(path):
    a = np.full((160, 200, 3), 128, np.uint8)
    a[:60] = 255
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def test_setting_gates_the_hold(tmp_path):
    src = str(tmp_path / "sky.jpg")
    _sky_jpeg(src)
    default, r0 = Pipeline.build(RunConfig(backend="stub")).process_one(src)
    off, _ = Pipeline.build(RunConfig(backend="stub", highlight_hold=False)).process_one(src)
    assert np.array_equal(default, off)
    assert not any("highlight hold" in n for n in r0.notes)
    on, r1 = Pipeline.build(RunConfig(backend="stub", highlight_hold=True)).process_one(src)
    sky_off, sky_on = float(default[:50].mean()), float(on[:50].mean())
    assert sky_on > sky_off and sky_on >= 0.98
    assert np.abs(on[80:] - default[80:]).max() < 1e-6
    assert any("highlight hold" in n for n in r1.notes)


def test_thresholds_come_from_the_rules_file(tmp_path):
    text = pathlib.Path(config_path("rules.toml")).read_text()
    assert text.count(_BLOCK) == 1
    changed = tmp_path / "changed.toml"
    changed.write_text(text.replace(_BLOCK, "[highlight_hold]\nstart = 0.5\nfull = 0.6\n"))
    missing = tmp_path / "missing.toml"
    missing.write_text(text.replace(_BLOCK, ""))
    build = lambda path, on: Pipeline.build(  # noqa: E731
        RunConfig(backend="stub", rules_path=str(path), highlight_hold=on)).highlight_hold
    assert build(changed, True) == (0.5, 0.6)
    assert build(missing, True) == (0.80, 0.985)
    assert build(changed, False) is None
    assert Pipeline.build(RunConfig(backend="stub", highlight_hold=True)).highlight_hold == (0.80, 0.985)


def test_cli_and_api_carry_the_settings(tmp_path):
    def args(**kw):
        return SimpleNamespace(**{"configs_dir": None, "config": None, **kw})

    base = cli.load_run_config(args())
    assert base.highlight_hold is False
    assert cli.load_run_config(args(highlight_hold=True)).highlight_hold is True
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[run]\nhighlight_hold = true\n')
    assert cli.load_run_config(args(config=str(cfg_file))).highlight_hold is True
    assert service.make_cfg({"highlight_hold": True}).highlight_hold is True
    # LibRaw's highlight modes were built, measured and removed (see the F6 spec): the option
    # must not come back half-wired.
    assert "raw_highlight" not in RunConfig.model_fields
    assert service.make_cfg({"raw_highlight": "blend"}).model_dump().get("raw_highlight") is None


def test_the_hold_runs_again_after_a_reference_match(tmp_path):
    # The reference match greys the whites again, so the pipeline holds them a second time. A
    # dark reference pulls the blown patch down to about 0.56 when only the first hold runs.
    import autoedit.pipeline as pipeline_mod
    src = str(tmp_path / "sky.jpg")
    _sky_jpeg(src)
    ref = np.full((120, 160, 3), 50, np.uint8)
    ref[:60] = 90
    PILImage.fromarray(ref, "RGB").save(str(tmp_path / "ref.jpg"), quality=95)

    def sky(**kw):
        cfg = RunConfig(backend="stub", reference=str(tmp_path / "ref.jpg"), highlight_hold=True, **kw)
        out, result = Pipeline.build(cfg).process_one(src)
        assert any(n.startswith("reference match") for n in result.notes)   # the match really ran
        return float(out[:50].mean())

    held = sky()
    assert held >= 0.98

    class _FirstHoldOnly:           # the pipeline's own calls of the hold become no-ops
        def __getattr__(self, name):
            return getattr(gates, name)

        @staticmethod
        def highlight_hold(src_rgb, out_rgb, *a):
            return out_rgb, 0.0

    real = pipeline_mod.gates
    pipeline_mod.gates = _FirstHoldOnly()
    try:
        skipped = sky()
    finally:
        pipeline_mod.gates = real
    assert skipped < held - 0.2, (skipped, held)
