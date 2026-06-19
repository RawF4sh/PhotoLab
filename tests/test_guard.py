"""Skin-safe guard: the masked global pass that holds aggressive ops off faces."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import RunConfig
from autoedit.edit import ops
from autoedit.edit.apply import apply_image
from autoedit.pipeline import Pipeline


def _img(seed=0):
    rng = np.random.default_rng(seed)
    return rng.random((32, 48, 3), dtype=np.float32) * 0.8 + 0.1  # in [0.1, 0.9]


def test_guard_off_is_identity_to_plain_global():
    img = _img()
    g = {"saturation": 50.0, "exposure": 0.3}
    out = apply_image(img, [], g)  # guard_mask/guard_ops default None → unchanged path
    ref = np.clip(ops.apply_ops(img.copy(), g), 0.0, 1.0)
    assert np.allclose(out, ref, atol=1e-6)


def test_guard_skips_ops_over_mask_only():
    img = _img(1)
    h, w, _ = img.shape
    g = {"saturation": 60.0}
    mask = np.zeros((h, w), np.float32)
    mask[:, : w // 2] = 1.0  # protect the left half
    out = apply_image(img, [], g, guard_mask=mask, guard_ops={"saturation"})

    full = np.clip(ops.apply_ops(img.copy(), g), 0.0, 1.0)
    # unguarded right half gets the full global edit
    assert np.allclose(out[:, w // 2:], full[:, w // 2:], atol=1e-6)
    # guarded left half: saturation removed and it was the only op → reverts to the input
    assert np.allclose(out[:, : w // 2], np.clip(img, 0.0, 1.0)[:, : w // 2], atol=1e-6)
    # the two halves actually differ (the guard did something)
    assert not np.allclose(out[:, : w // 2], full[:, : w // 2], atol=1e-3)


def _make_portrait(path):
    a = np.zeros((128, 128, 3), np.uint8)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    yy, xx = np.mgrid[0:128, 0:128]
    ell = (((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0
    a[ell] = [200, 150, 120]  # skin subject
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def test_pipeline_guard_notes_and_is_localized(tmp_path):
    p = str(tmp_path / "portrait.jpg")
    _make_portrait(p)

    pipe = Pipeline.build(RunConfig(backend="stub"))
    assert pipe.guard_ops  # loaded from rules.toml [guard]
    out_on, result = pipe.process_one(p, do_apply=True)
    assert any("skin-safe guard" in n for n in result.notes)

    pipe.guard_ops = set()  # disable the guard, re-render
    out_off, _ = pipe.process_one(p, do_apply=True)

    diff = np.abs(out_on - out_off).sum(axis=2)
    assert diff.max() > 1e-4                  # guard changed the skin pixels
    assert (diff > 1e-4).mean() < 0.6         # but only a localized fraction, not the whole frame
