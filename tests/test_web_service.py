"""Web service layer (framework-agnostic — no FastAPI needed to test)."""

import importlib

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
    assert out["before"].startswith("data:image/png;base64,")
    assert out["after"].startswith("data:image/png;base64,")
    assert out["result"]["scene"] == "people"
    assert out["after"] != out["before"]  # the edit changed the pixels
