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
    assert service.make_cfg({"concepts": []}).concepts == []
    assert service.make_cfg({}).concepts is None


def test_preview_and_export_respect_concepts_and_real_mask_footprints(tmp_path):
    """A named region's overlay matches arbitration; disabling it survives export and re-preview.

    Use a deterministic segmenter to verify geometry without GPU weights or model accuracy.
    """
    from unittest.mock import patch
    from autoedit.pipeline import Pipeline
    from autoedit.segment.base import RegionMask

    rgb = np.full((80, 120, 3), [90, 110, 130], np.uint8)
    p = tmp_path / "regions.png"
    PILImage.fromarray(rgb).save(p)
    sky = np.zeros((80, 120), bool); sky[:40] = True
    jacket = np.zeros_like(sky); jacket[20:70, 30:90] = True

    class Segmenter:
        calls = []

        def segment(self, image, concepts):
            self.calls.append(list(concepts))
            masks = {"sky": sky, "jacket": jacket}
            return [RegionMask(name=name, mask=masks[name], score=0.99,
                               coverage=float(masks[name].mean()))
                    for name in concepts if name in masks]

    seg = Segmenter()

    def build(cfg):
        pipe = Pipeline.build(cfg)
        pipe.segmenter = seg
        return pipe

    opts = {"backend": "stub", "concepts": ["sky", "jacket", "ghost"], "compose": False}
    with patch.object(service, "build_pipeline", build):
        preview = service.preview(str(p), opts)
        assert len(seg.calls) == 1  # overlays must reuse this segmentation, not invoke another pass
        assert [c["name"] for c in preview["result"]["concepts"]] == opts["concepts"]
        assert not preview["result"]["concepts"][-1]["present"]
        masks = {}
        for overlay in preview["mask_overlays"]:
            data = base64.b64decode(overlay["alpha"].split(",", 1)[1])
            mask = np.asarray(PILImage.open(io.BytesIO(data)))[..., 3] > 0
            assert mask.shape == sky.shape
            assert abs(mask.mean() - overlay["coverage"]) < 1e-8
            masks[overlay["name"]] = mask
        assert set(masks) == {"sky", "jacket"}
        assert not (masks["sky"] & masks["jacket"]).any()
        assert np.array_equal(masks["sky"] | masks["jacket"], sky | jacket)
        assert not masks["jacket"][0, 0] and masks["jacket"][60, 60]

        opts["concepts"] = ["sky"]
        enabled = service.preview(str(p), opts)
        exported = service.run_one(str(p), str(tmp_path / "out"), opts)
        assert [c["name"] for c in exported["result"]["concepts"]] == ["sky"]
        assert [m["name"] for m in enabled["mask_overlays"]] == ["sky"]
        assert exported["result"]["masks"] == enabled["result"]["masks"]

        opts["concepts"] = []
        disabled = service.preview(str(p), opts)
        exported = service.run_one(str(p), str(tmp_path / "disabled"), opts)
        assert disabled["mask_overlays"] == []
        for result in (disabled["result"], exported["result"]):
            assert result["concepts"] == [] and result["masks"] == []
        assert [] not in seg.calls  # no model inference when every region is disabled


def test_raw_preview_and_export_share_development(tmp_path):
    """Simulate a RAW decoder with distinct modes; compare pixels and decisions across paths."""
    from unittest.mock import patch
    from autoedit import pipeline, imageio

    p = tmp_path / "camera.cr2"
    p.write_bytes(b"fake raw decoded by the test seam")
    calls = []

    def decode(path, *, decode=None):
        mode = (decode or {}).get("raw_mode", "none")
        calls.append(mode)
        value, ev = (0.5, 1.25) if mode == "preview_ev_headroom" else (0.2, 0.0)
        return imageio.Image(rgb=np.full((64, 96, 3), value, np.float32),
                             path=path, decode_ev=ev)

    opts = {"backend": "stub", "raw_mode": "preview_ev_headroom", "concepts": []}
    written = []
    with patch.object(service, "load_image", decode), patch.object(pipeline, "load_image", decode), \
            patch.object(service.emit, "write_image", lambda rgb, *args: written.append(rgb.copy())):
        preview = service.preview(str(p), opts)
        exported = service.run_one(str(p), str(tmp_path / "out"), opts)
    assert calls == ["preview_ev_headroom", "preview_ev_headroom"]
    assert preview["result"]["decode_ev"] == exported["result"]["decode_ev"] == 1.25
    assert preview["result"]["global_settings"] == exported["result"]["global_settings"]
    assert preview["after"] == service._img_b64(written[0], 2560, fmt="webp")


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
    assert out["before"].startswith("data:image/webp;base64,")  # lossless WebP → no JPEG banding in skies
    assert out["after"].startswith("data:image/webp;base64,")
    assert out["result"]["scene"] == "people"
    assert out["after"] != out["before"]  # the edit changed the pixels


def test_preview_webp_is_lossless(tmp_path):
    # the anti-banding dither must survive the preview encoding, so WebP must be pixel-exact (like PNG)
    g = np.linspace(0.2, 0.85, 512)[:, None].repeat(384, 1)
    rgb = np.repeat(g[..., None], 3, axis=2).astype(np.float32)
    webp = base64.b64decode(service._img_b64(rgb, max_px=512, fmt="webp").split(",", 1)[1])
    png = base64.b64decode(service._img_b64(rgb, max_px=512, fmt="png").split(",", 1)[1])
    aw = np.asarray(PILImage.open(io.BytesIO(webp)))
    ap = np.asarray(PILImage.open(io.BytesIO(png)))
    assert np.array_equal(aw, ap)          # identical decoded pixels → lossless


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


def test_thumb_px_is_clamped(tmp_path):
    # a hostile/errant px must not skip the downscale and return a full-res image
    PILImage.fromarray(np.full((1600, 2400, 3), 128, np.uint8), "RGB").save(str(tmp_path / "big.jpg"))
    im = PILImage.open(io.BytesIO(service.thumb_bytes(str(tmp_path / "big.jpg"), px=100000)))
    assert max(im.size) <= 2048


def test_preview_result_carries_no_secrets(tmp_path):
    import json

    PILImage.fromarray(np.full((64, 64, 3), 120, np.uint8), "RGB").save(str(tmp_path / "p.jpg"))
    out = service.preview(str(tmp_path / "p.jpg"),
                          {"backend": "stub", "api_key": "sk-must-not-leak", "allow_cloud": True})
    blob = json.dumps(out["result"])              # the sidecar payload the UI receives
    assert "sk-must-not-leak" not in blob and "api_key" not in blob


def test_missing_required_field_returns_named_400():
    """A missing body field should name itself, not surface as a bare KeyError. Skipped without
    the [ui] extra so the zero-dep runner stays green."""
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return
    from autoedit import paths
    from autoedit.web import app as webapp

    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True),
                        base_url="http://localhost")
    r = client.post("/api/datasets/validate", json={})
    assert r.status_code == 400 and "originals_dir" in r.json()["error"]
    r2 = client.post("/api/run", json={})
    assert r2.status_code == 400 and "input_dir" in r2.json()["error"]


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
