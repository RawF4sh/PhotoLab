"""F46: lateral chromatic aberration (lens.py), its decode hook and its settings."""

import argparse

import numpy as np
from PIL import Image as PILImage

from autoedit import cli, lens
from autoedit.contract import RunConfig
from autoedit.edit.filters import gaussian_blur
from autoedit.imageio import load_image, raw_opts_from
from autoedit.web import service


def _scene(h=800, w=1200, seed=0):
    rng = np.random.default_rng(seed)
    g = np.full((h, w), 0.3, np.float32)
    for _ in range(300):
        y0, x0 = rng.integers(0, h), rng.integers(0, w)
        g[y0:y0 + rng.integers(5, 80), x0:x0 + rng.integers(5, 80)] = rng.random()
    return gaussian_blur(g, 0.8)


def _R(g):
    return float(np.hypot((g.shape[0] - 1) / 2, (g.shape[1] - 1) / 2))


def _fringed(g, px):
    return lens.scale_channel(g, 1 / (1 + px / _R(g)))


def _mad(a, b, m=40):
    return float(np.abs(a[m:-m, m:-m] - b[m:-m, m:-m]).mean())


def test_scale_channel():
    g = _scene()
    assert lens.scale_channel(g, 1.0) is g
    flat = np.full((200, 300), 0.5, np.float32)
    assert np.abs(lens.scale_channel(flat, 1.003) - 0.5).max() < 1e-6
    s, (h, w) = 1.003, g.shape
    cy, cx = (h - 1) / 2, (w - 1) / 2
    ys, xs = np.mgrid[10:h - 10, 10:w - 10].astype(np.float32)
    want = lens._bilinear(g, cy + s * (ys - cy), cx + s * (xs - cx))
    assert np.abs(lens.scale_channel(g, s)[10:h - 10, 10:w - 10] - want).max() < 5e-5


def test_round_trip():
    g = _scene()
    assert _mad(lens.scale_channel(lens.scale_channel(g, 1.001), 1 / 1.001), g) < 0.002


def test_ca_scale_finds_known_fringes():
    g = _scene()
    for px in (-0.7, 0.9, 3.0, -5.0):
        s, R = lens.ca_scale(g, _fringed(g, px))
        assert abs((s - 1) * R - px) < 0.05, (px, (s - 1) * R)


def test_correct_ca_red_only():
    g = _scene()
    red, blue = _fringed(g, 0.9), _fringed(g, 0.14)
    rgb = np.stack([red, g, blue], -1)
    out, rep = lens.correct_ca(rgb)
    assert rep["red"]["applied"] and not rep["blue"]["applied"]
    assert abs(rep["red"]["corner_px"] - 0.9) < 0.05
    assert _mad(out[..., 0], g) <= 0.5 * _mad(red, g)
    assert np.array_equal(out[..., 2], blue)


def test_grey_frame_untouched():
    g = _scene()
    rgb = np.stack([g, g, g], -1)
    out, rep = lens.correct_ca(rgb)
    assert out is rgb and not rep["red"]["applied"] and not rep["blue"]["applied"]


def test_load_image_hook(tmp_path):
    g = _scene()
    rgb = np.clip(np.stack([_fringed(g, 2.0), g, g], -1), 0, 1)
    p = str(tmp_path / "f.png")
    PILImage.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(p)
    plain = load_image(p)
    on = load_image(p, decode={"lens_ca": True})
    assert on.decode_info["lens"]["red"]["applied"]
    gg = plain.rgb[..., 1]
    assert _mad(on.rgb[..., 0], gg) < _mad(plain.rgb[..., 0], gg)
    for d in (None, {"lens_ca": False}):
        off = load_image(p, decode=d)
        assert np.array_equal(off.rgb, plain.rgb) and "lens" not in off.decode_info


def test_cli_and_api_carry_the_setting(tmp_path):
    p = argparse.ArgumentParser()
    cli._add_run_flags(p)
    ns = p.parse_args(["in", "--lens-ca"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).lens_ca is True
    ns = p.parse_args(["in"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).lens_ca is False
    conf = tmp_path / "run.toml"
    conf.write_text("[decode]\nlens_ca = true\n")
    ns.config = str(conf)
    assert cli.load_run_config(ns).lens_ca is True
    assert service.make_cfg({"lens_ca": True}).lens_ca is True
    assert raw_opts_from(RunConfig(lens_ca=True))["lens_ca"] is True
