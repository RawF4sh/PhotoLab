"""EXIF priors: metadata threads load → stats → rules, degrading to neutral defaults."""

import numpy as np
from PIL import Image as PILImage

from autoedit import stats
from autoedit.imageio import Image, load_image
from autoedit.rules import eval_expr

_ISO_SHARPEN = "12 * (1 - min(iso, 6400) / 12800)"  # the form used in rules.toml


def _img(exif):
    rgb = np.full((16, 16, 3), 0.5, np.float32)
    return Image(rgb=rgb, path=None, exif=exif)


def test_exif_iso_flows_into_global_stats():
    g, _ = stats.compute(_img({"iso": 3200.0, "exposure_ev": -0.5}), {})
    assert g.iso == 3200.0
    assert g.exposure_ev == -0.5


def test_missing_exif_defaults_to_zero():
    for exif in (None, {}):
        g, _ = stats.compute(_img(exif), {})
        assert g.iso == 0.0
        assert g.exposure_ev == 0.0


def test_high_iso_eases_sharpen():
    low = eval_expr(_ISO_SHARPEN, {"iso": 0.0})
    high = eval_expr(_ISO_SHARPEN, {"iso": 6400.0})
    assert low == 12.0           # no metadata → full sharpening (preserves default behavior)
    assert high < low            # noisy frame → restrained
    assert high == 6.0


def test_load_image_returns_exif_dict(tmp_path):
    p = str(tmp_path / "plain.jpg")
    PILImage.fromarray(np.zeros((24, 24, 3), np.uint8), "RGB").save(p, quality=90)
    img = load_image(p)
    assert isinstance(img.exif, dict)  # no EXIF in this file → {}, never None/crash
