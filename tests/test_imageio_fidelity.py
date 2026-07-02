"""Decode fidelity: 16-bit input must NOT be collapsed to 8-bit at load (Codex review F1)."""

import numpy as np
from PIL import Image as PILImage

from autoedit.imageio import load_image, save_image


def test_16bit_tiff_survives_load(tmp_path):
    # a smooth gradient with 1024 distinct levels — impossible to represent in 8-bit (256)
    levels = np.linspace(0, 65535, 1024, dtype=np.uint16)[None, :].repeat(8, axis=0)
    p = tmp_path / "grad16.tif"
    PILImage.fromarray(levels, mode="I;16").save(str(p))

    img = load_image(str(p))
    assert img.source_bits == 16
    uniq = np.unique(np.round(img.rgb[..., 0] * 65535).astype(np.int64))
    assert uniq.size > 256, f"16-bit gradient collapsed to {uniq.size} levels (8-bit quantization)"


def test_8bit_jpeg_still_loads(tmp_path):
    a = (np.random.default_rng(0).random((16, 16, 3)) * 255).astype(np.uint8)
    p = tmp_path / "x.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))
    img = load_image(str(p))
    assert img.source_bits == 8 and img.rgb.shape == (16, 16, 3)
    assert 0.0 <= img.rgb.min() and img.rgb.max() <= 1.0


def test_grayscale_rgba_cmyk_decode_to_rgb(tmp_path):
    # decode robustness: unusual-but-real modes must land as HxWx3 float [0,1], not crash
    PILImage.new("L", (8, 6), 128).save(str(tmp_path / "gray.jpg"))
    PILImage.new("RGBA", (8, 6), (200, 100, 50, 255)).save(str(tmp_path / "alpha.png"))
    PILImage.new("CMYK", (8, 6), (10, 20, 30, 0)).save(str(tmp_path / "cmyk.jpg"))
    for name in ("gray.jpg", "alpha.png", "cmyk.jpg"):
        img = load_image(str(tmp_path / name))
        assert img.rgb.shape == (6, 8, 3), name
        assert 0.0 <= img.rgb.min() and img.rgb.max() <= 1.0, name


def test_proxy_downscale_keeps_16bit_precision(tmp_path):
    # the analysis proxy must NOT round-trip through uint8 (+dither) — a 16-bit gradient
    # downscaled 2x should keep far more than 256 distinct levels
    from autoedit.imageio import downscale_long_edge

    levels = np.linspace(0, 65535, 1024, dtype=np.uint16)[None, :].repeat(8, axis=0)
    p = tmp_path / "grad16.tif"
    PILImage.fromarray(levels, mode="I;16").save(str(p))

    proxy = downscale_long_edge(load_image(str(p)), 512)
    assert proxy.rgb.shape[1] == 512
    uniq = np.unique(np.round(proxy.rgb[..., 0] * 65535).astype(np.int64))
    assert uniq.size > 256, f"proxy collapsed to {uniq.size} levels (8-bit quantization)"
