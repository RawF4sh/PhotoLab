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
