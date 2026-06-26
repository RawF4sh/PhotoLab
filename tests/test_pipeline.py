"""End-to-end smoke: synthetic image → Result with regions, via the stub segmenter."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import RunConfig
from autoedit.edit.ops import apply_ops
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


def test_pipeline_auto_palette_note_for_cool_landscape(tmp_path):
    img_path = tmp_path / "winter.jpg"
    a = np.full((128, 128, 3), 210, np.uint8)
    a[:72] = [220, 224, 226]     # bright low-chroma sky
    a[72:] = [115, 128, 132]     # cool low-chroma water/trees
    PILImage.fromarray(a, "RGB").save(str(img_path), quality=95)

    cfg = RunConfig(backend="stub", style="natural", concepts=["sky", "water"])
    result = Pipeline.build(cfg).process_one(str(img_path), do_apply=True)[1]

    assert any("palette: auto cool_blue" in n for n in result.notes)


def test_pipeline_reference_feedback_updates_settings_and_notes(tmp_path):
    img_path = tmp_path / "source.jpg"
    ref_path = tmp_path / "reference.jpg"
    _make(str(img_path))

    src = np.asarray(PILImage.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
    ref = apply_ops(src, {
        "exposure": 0.12,
        "contrast": 20.0,
        "curve": 10.0,
        "vibrance": 14.0,
        "temp": 8.0,
    })
    PILImage.fromarray((np.clip(ref, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB").save(
        str(ref_path), quality=95)

    cfg = RunConfig(backend="stub", reference=str(ref_path), reference_strength=0.8)
    result = Pipeline.build(cfg).process_one(str(img_path), do_apply=False)[1]

    assert any("reference feedback" in n for n in result.notes)
    assert any(k in result.global_settings for k in ("exposure", "contrast", "curve", "temp", "vibrance"))


def test_pipeline_reference_sky_gradient_records_composition(tmp_path):
    img_path = tmp_path / "source.jpg"
    ref_path = tmp_path / "reference.jpg"
    _make(str(img_path))

    src = np.asarray(PILImage.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
    ref = src.copy()
    ref[:60] = apply_ops(ref[:60], {"exposure": -0.45, "contrast": 24.0, "clarity": 8.0})
    PILImage.fromarray((np.clip(ref, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB").save(
        str(ref_path), quality=95)

    cfg = RunConfig(backend="stub", reference=str(ref_path), reference_strength=0.9)
    result = Pipeline.build(cfg).process_one(str(img_path), do_apply=False)[1]

    assert any(c.target == "reference_sky_grad" for c in result.composition)
    assert any("reference sky gradient" in n for n in result.notes)


def test_pipeline_reference_skips_auto_palette_and_lifts_foreground(tmp_path):
    img_path = tmp_path / "source.jpg"
    ref_path = tmp_path / "reference.jpg"
    _make(str(img_path))

    src = np.asarray(PILImage.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
    ref = src.copy()
    ref[70:] = apply_ops(ref[70:], {
        "exposure": 0.35,
        "shadows": 18.0,
        "hsl_green_lum": 12.0,
        "hsl_green_sat": 12.0,
    })
    PILImage.fromarray((np.clip(ref, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB").save(
        str(ref_path), quality=95)

    cfg = RunConfig(backend="stub", reference=str(ref_path), reference_strength=0.9)
    result = Pipeline.build(cfg).process_one(str(img_path), do_apply=False)[1]

    assert not any("palette: auto" in n for n in result.notes)
    assert any(c.target == "reference_foreground_lift" for c in result.composition)
    assert any("reference foreground lift" in n for n in result.notes)


def test_pipeline_reference_midground_lift_records_composition(tmp_path):
    img_path = tmp_path / "source.jpg"
    ref_path = tmp_path / "reference.jpg"
    _make(str(img_path))

    src = np.asarray(PILImage.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
    ref = src.copy()
    ref[78:108] = apply_ops(ref[78:108], {
        "exposure": 0.35,
        "shadows": 15.0,
        "hsl_green_lum": 12.0,
    })
    PILImage.fromarray((np.clip(ref, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB").save(
        str(ref_path), quality=95)

    cfg = RunConfig(backend="stub", reference=str(ref_path), reference_strength=0.9)
    result = Pipeline.build(cfg).process_one(str(img_path), do_apply=False)[1]

    assert any(c.target == "reference_midground_lift" for c in result.composition)
    assert any("reference midground lift" in n for n in result.notes)


def test_pipeline_reference_mountain_highlight_records_composition(tmp_path):
    img_path = tmp_path / "source.jpg"
    ref_path = tmp_path / "reference.jpg"
    _make(str(img_path))

    src = np.asarray(PILImage.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
    ref = src.copy()
    ref[44:76] = apply_ops(ref[44:76], {
        "exposure": 0.20,
        "whites": 22.0,
        "highlights": 12.0,
    })
    PILImage.fromarray((np.clip(ref, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB").save(
        str(ref_path), quality=95)

    cfg = RunConfig(backend="stub", reference=str(ref_path), reference_strength=0.9)
    result = Pipeline.build(cfg).process_one(str(img_path), do_apply=False)[1]

    assert any(c.target == "reference_mountain_highlight" for c in result.composition)
    assert any("reference mountain highlight" in n for n in result.notes)
