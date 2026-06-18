"""Output sharpening (unsharp mask) + universal frame vignette + their on/off toggles."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import RunConfig
from autoedit.edit.ops import apply_ops, sharpen
from autoedit.pipeline import Pipeline


def test_sharpen_noops_flat_and_rings_edges():
    flat = np.full((24, 24, 3), 0.5, np.float32)
    assert np.allclose(sharpen(flat, 80.0), flat, atol=1e-5)        # no detail → no change
    step = np.full((24, 24, 3), 0.4, np.float32)
    step[:, 12:] = 0.6
    out = sharpen(step, 80.0)
    assert out.max() > 0.6 + 1e-3 and out.min() < 0.4 - 1e-3        # unsharp over/undershoot
    assert not np.allclose(apply_ops(step, {"sharpen": 80.0}), step)  # routed by apply_ops


def _landscape_img(tmp_path):
    a = np.zeros((48, 48, 3), np.uint8)
    a[:24] = [100, 140, 200]        # bright bluish top → stub detects sky → landscape scene
    p = tmp_path / "x.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))
    return str(p)


def test_vignette_on_by_default_and_toggles_off(tmp_path):
    p = _landscape_img(tmp_path)
    on = Pipeline.build(RunConfig(backend="stub", vignette=True)).process_one(p, True)[1]
    off = Pipeline.build(RunConfig(backend="stub", vignette=False)).process_one(p, True)[1]
    assert "frame_vignette" in [c.target for c in on.composition]   # every frame gets it
    assert "frame_vignette" not in [c.target for c in off.composition]


def test_sharpen_on_by_default_and_toggles_off(tmp_path):
    p = _landscape_img(tmp_path)
    on = Pipeline.build(RunConfig(backend="stub", sharpen=True)).process_one(p, False)[1].global_settings
    off = Pipeline.build(RunConfig(backend="stub", sharpen=False)).process_one(p, False)[1].global_settings
    assert on.get("sharpen", 0) > 0
    assert "sharpen" not in off
