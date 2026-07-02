"""EXIF orientation: numpy helper matches Pillow's transpose semantics exactly, and a rotated
TIFF loads upright regardless of which decode path (tifffile or Pillow) handles it."""

import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps

from autoedit.imageio import _apply_exif_orientation, load_image

# The exact PIL ops ImageOps.exif_transpose applies per orientation value.
_PIL_OPS = {
    2: PILImage.Transpose.FLIP_LEFT_RIGHT,
    3: PILImage.Transpose.ROTATE_180,
    4: PILImage.Transpose.FLIP_TOP_BOTTOM,
    5: PILImage.Transpose.TRANSPOSE,
    6: PILImage.Transpose.ROTATE_270,
    7: PILImage.Transpose.TRANSVERSE,
    8: PILImage.Transpose.ROTATE_90,
}


def test_orientation_helper_matches_pillow_for_all_values():
    rng = np.random.default_rng(7)
    base = (rng.random((6, 4, 3)) * 255).astype(np.uint8)   # non-square → transposes visible
    pil = PILImage.fromarray(base, "RGB")
    assert np.array_equal(_apply_exif_orientation(base, 1), base)
    for o, op in _PIL_OPS.items():
        expected = np.asarray(pil.transpose(op))
        got = _apply_exif_orientation(base, o)
        assert np.array_equal(got, expected), f"orientation {o} mismatch"


def test_rotated_tiff_loads_upright(tmp_path):
    # 8x16 with a red top-left marker; orientation 6 (ROTATE_270 to display) → 16x8 upright
    a = np.zeros((8, 16, 3), np.uint8)
    a[0, 0] = [255, 0, 0]
    p = str(tmp_path / "rot.tif")
    PILImage.fromarray(a, "RGB").save(p, tiffinfo={274: 6})

    expected = np.asarray(ImageOps.exif_transpose(PILImage.open(p)).convert("RGB"))
    img = load_image(p)
    got = np.round(img.rgb * 255).astype(np.uint8)
    assert got.shape == expected.shape == (16, 8, 3)   # dims swapped = orientation honored
    assert np.array_equal(got, expected)
