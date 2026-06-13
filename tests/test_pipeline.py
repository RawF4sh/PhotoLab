"""End-to-end smoke: synthetic image → Result with regions, via the stub segmenter."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline


def _make(path):
    a = np.zeros((128, 128, 3), np.uint8)
    a[:70] = [100, 140, 200]   # sky
    a[70:] = [60, 110, 70]     # foliage
    yy, xx = np.mgrid[0:128, 0:128]
    ell = (((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0
    a[ell] = [200, 150, 120]   # skin subject
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def test_pipeline_smoke(tmp_path):
    img_path = tmp_path / "p.jpg"
    _make(str(img_path))
    cfg = RunConfig(backend="stub")
    pipe = Pipeline.build(cfg)
    out_rgb, result = pipe.process_one(str(img_path), do_apply=True)

    assert out_rgb is not None
    assert out_rgb.shape == (128, 128, 3)
    assert out_rgb.min() >= 0.0 and out_rgb.max() <= 1.0
    assert result.scene == "people"
    assert {m.target for m in result.masks} & {"skin", "subject"}
    assert result.global_stats is not None
