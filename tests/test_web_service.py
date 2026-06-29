"""Web service layer (framework-agnostic — no FastAPI needed to test)."""

import base64
import importlib
import io
import os

import numpy as np
from PIL import Image as PILImage

from autoedit.web import service


def test_app_module_imports_without_fastapi():
    # importing the module must not require fastapi (only create_app/serve do)
    importlib.import_module("autoedit.web.app")


def test_make_cfg_applies_options():
    cfg = service.make_cfg({"style": "editorial", "concepts": "sky, skin", "intensity": 0.5})
    assert cfg.style == "editorial"
    assert cfg.concepts == ["sky", "skin"]
    assert cfg.intensity == 0.5
    assert cfg.styles_path.endswith("styles.toml")


def test_list_styles_and_presets():
    names = {s["name"] for s in service.list_styles()}
    assert "natural" in names
    assert "generic" in service.list_presets()


def test_list_images_filters(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert service.list_images(str(tmp_path)) == ["a.jpg"]
    assert service.list_images("/no/such/dir") == []


def test_preview_returns_before_after(tmp_path):
    a = np.zeros((96, 96, 3), np.uint8)
    a[:48] = [100, 140, 200]
    yy, xx = np.mgrid[0:96, 0:96]
    a[(((xx - 48) / 20) ** 2 + ((yy - 60) / 24) ** 2) <= 1.0] = [200, 150, 120]
    p = tmp_path / "p.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))

    out = service.preview(str(p), {"style": "editorial", "backend": "stub"})
    assert out["before"].startswith("data:image/png;base64,")  # lossless PNG → no JPEG banding in skies
    assert out["after"].startswith("data:image/png;base64,")
    assert out["result"]["scene"] == "people"
    assert out["after"] != out["before"]  # the edit changed the pixels


def test_preview_encoder_dithers_after_resize():
    g = np.linspace(0.80, 0.94, 1024)[:, None].repeat(256, 1)
    rgb = np.repeat(g[..., None], 3, axis=2).astype(np.float32)
    url = service._img_b64(rgb, max_px=256, fmt="png")
    raw = base64.b64decode(url.split(",", 1)[1])
    arr = np.asarray(PILImage.open(io.BytesIO(raw)))

    assert arr.shape[:2] == (256, 64)
    mixed_rows = sum(len(np.unique(arr[y, :, 0])) > 1 for y in range(arr.shape[0]))
    assert mixed_rows > arr.shape[0] // 2


def test_run_one_writes_outputs(tmp_path):
    a = np.full((80, 80, 3), 120, np.uint8)
    a[:40] = [100, 140, 200]
    p = tmp_path / "one.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))
    out_dir = tmp_path / "out"
    res = service.run_one(str(p), str(out_dir), {"style": "natural", "backend": "stub"})
    assert os.path.exists(res["image_out"]) and os.path.exists(res["sidecar"])
    assert res["result"]["image"] == "one.jpg"


def test_curate_folder_writes_copy_only_outputs(tmp_path):
    src = tmp_path / "library"
    src.mkdir()
    PILImage.fromarray(np.full((48, 64, 3), [30, 80, 160], np.uint8), "RGB").save(src / "one.jpg")
    PILImage.fromarray(np.full((48, 64, 3), [220, 190, 120], np.uint8), "RGB").save(src / "two.jpg")

    out = tmp_path / "curated"
    res = service.curate_folder(str(src), str(out), {"mode": "best", "count": 1, "emit_xmp": True})

    assert res["manifest"]["selected"] == 1
    assert os.path.isdir(res["selected_dir"])
    assert os.path.exists(res["contact_sheet"])
    assert os.path.exists(res["selected_csv"])
    assert len([p for p in (out / "selected").iterdir() if p.suffix.lower() != ".xmp"]) == 1
    assert len(list((out / "selected").glob("*.xmp"))) == 1
    assert (src / "one.jpg").exists() and (src / "two.jpg").exists()


def test_check_image_rejects_non_image_and_missing(tmp_path):
    import pytest

    xmp = tmp_path / "3D6A1365.xmp"
    xmp.write_text("<x:xmpmeta/>")
    with pytest.raises(ValueError):
        service.check_image(str(xmp))          # sidecar, not an image
    with pytest.raises(ValueError):
        service.check_image(str(tmp_path / "missing.jpg"))
    with pytest.raises(ValueError):
        service.run_one(str(xmp), str(tmp_path / "o"), {"backend": "stub"})
