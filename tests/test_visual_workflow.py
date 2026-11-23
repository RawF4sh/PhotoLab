"""Visual region controls, mask arbitration, framing and true native-pixel inspection."""

import base64
import io
import json
import math
import os
import time
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import geometry
from autoedit.concepts import ConceptMeta
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask
from autoedit.segment import refine
from autoedit.segment.resolve import resolve
from autoedit.web import service, jobs, projects


def _image(path, h=100, w=160):
    y, x = np.indices((h, w))
    rgb = np.stack([70+x/4, 90+y/3, 120+x/8], axis=-1).astype(np.uint8)
    PILImage.fromarray(rgb).save(path)
    return rgb


def _decode(url):
    return np.asarray(PILImage.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))))


def _build(cfg):
    class Segmenter:
        def segment(self, image, concepts):
            h, w = image.rgb.shape[:2]
            sky = np.zeros((h, w), bool); sky[:h//2] = True
            subject = np.zeros((h, w), bool); subject[h//4:3*h//4, w//3:2*w//3] = True
            masks = {"sky": sky, "subject": subject}
            return [RegionMask(n, masks[n], .99, float(masks[n].mean())) for n in concepts if n in masks]
    pipe = Pipeline.build(cfg)
    pipe.segmenter = Segmenter()
    return pipe


def _stroke(mode="add", x=.2, y=.7):
    return {"mode": mode, "radius": .08, "points": [[x, y]]}


def test_refinement_copies_masks_and_arbitrates_once():
    sky = np.zeros((80, 120), bool); sky[:40] = True
    subject = np.zeros_like(sky); subject[20:60, 35:80] = True
    raw = [RegionMask("sky", sky, .99), RegionMask("subject", subject, .99)]
    meta = {"sky": ConceptMeta("sky", priority=10), "subject": ConceptMeta("subject", priority=90)}
    strokes = refine.check({"sky": [_stroke(), _stroke("subtract", .8, .2)], "missing": [_stroke()]})
    changed, notes = refine.apply(raw, strokes, meta, sky.shape)
    assert np.array_equal(sky[:40], np.ones_like(sky[:40])) and not sky[40:].any()
    assert changed[0].mask is not sky and changed[0].mask[55, 24] and not changed[0].mask[16, 95]
    assert any("missing was not found" in note for note in notes)
    resolved = resolve(changed, ["sky", "subject", "missing"], meta, sky.shape)
    masks = {r.name: r.mask for r in resolved.regions}
    assert not (masks["sky"] & masks["subject"]).any()
    assert masks["subject"][25, 40] and not masks["sky"][25, 40]
    assert not next(c for c in resolved.detected if c.name == "missing").present
    assert refine.apply(raw, {}, meta, sky.shape)[0] is raw


def test_visual_input_limits_and_geometry_reject_bad_work():
    for bad in ({"global": [_stroke()]}, {"sky": [{**_stroke(), "radius": 1}]},
                {"sky": [{**_stroke(), "points": [[float("nan"), .1]]}]},
                {"sky": [{**_stroke(), "mode": "invent"}]}, {"sky": [_stroke()] * 101}):
        with pytest.raises(ValueError):
            refine.check(bad)
    for bad in ({"angle": True}, {"angle": 16}, {"crop": [0, 0, .001, 1]},
                {"crop": [0, 0, float("nan"), 1]}, {"crop": [0, 0, 1, 2]}, {"fill": "generated"}):
        with pytest.raises(ValueError):
            geometry.check_manual(bad)
    for bad in ({"sky": {"exposure": 3}}, {"mark:area:0,0,1,1": {"temp": 1}}, {"sky": {"temp": True}}):
        with pytest.raises(ValueError):
            service.check_region_settings(bad)
    with pytest.raises(ValueError):
        RunConfig(mask_refinements={"sky": [{**_stroke(), "radius": -1}]})
    from autoedit.contract import Adjustment
    from autoedit.web import photo_base
    for bad in ("global", "mark:area:0,0,1,1", "place:top", " sky", "sky\n", "sky\x7f", "x" * 121,
                "__proto__", "constructor", "prototype"):
        with pytest.raises(ValueError):
            service.check_region_settings({bad: {}})
        with pytest.raises(ValueError):
            RunConfig(selected_regions=[bad])
        with pytest.raises(ValueError):
            refine.check({bad: [_stroke()]})
        with pytest.raises(ValueError):
            photo_base.check({"adjustments": [{"target": bad, "scope": "photo", "ops": {}, "resolved_only": True}]})
    with pytest.raises(ValueError):
        Adjustment(target="sky", ops={}, scope="run", resolved_only=True)


def test_manual_geometry_never_samples_outside_real_pixels():
    original = np.full((160, 240, 3), .8, np.float32)
    assert geometry.warp(original, geometry.manual({"angle": 0, "crop": None}, original.shape[:2])[0]) is original
    for angle in (-15, -3, 3, 15):
        for crop in (None, [0, 0, .3, .4], [.6, .5, 1, 1], [.2, .2, .8, .7]):
            geo, _ = geometry.manual({"angle": angle, "crop": crop}, original.shape[:2])
            result = geometry.warp(original, geo)
            assert result.size and np.allclose(result, .8, atol=1e-5)  # black/generated edge pixels would fail
            h, w = geometry.size(geo, original.shape[:2]); c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            for x in (-w/2, w/2):
                for y in (-h/2, h/2):
                    assert 0 <= geo.cx*240+c*x-s*y <= 240
                    assert 0 <= geo.cy*160+s*x+c*y <= 160
    assert np.array_equal(original, np.full_like(original, .8))


def test_visual_edit_preview_save_batch_and_project_agree(tmp_path):
    path = tmp_path / "a.png"; _image(path)
    own = {"region_settings": {"sky": {"exposure": .2, "temp": -3}},
           "mask_refinements": {"sky": [_stroke()]}, "photo_geometry": {"angle": 3, "crop": [.1, .1, .9, .9]},
           "local_only": True}
    opts = {"backend": "stub", "concepts": ["sky", "subject"], "compose": False,
            "consistent": False, "out_format": "png"}
    service.approve_root(str(tmp_path))
    with patch.object(service, "build_pipeline", _build):
        preview = service.preview(str(path), {**opts, **own})
        source = service.preview(str(path), {**opts, **own, "preview_unframed": True})
        practice = service.preview(str(path), {**opts, **own, "preview_unframed": True, "try": {"sliders": {}, "compare": True}})
        saved = service.run_one(str(path), str(tmp_path / "single"), {**opts, **own})
        job = jobs.start(str(tmp_path), [str(path)], str(tmp_path / "batch"), opts, photo_options={str(path): own})
        until = time.monotonic()+20
        while job.status not in ("done", "error", "cancelled") and time.monotonic()<until:
            time.sleep(.02)
    assert job.status == "done" and job.items[0].status != "failed"
    batched = json.loads((tmp_path / "batch" / "a.autoedit.json").read_text())
    assert preview["result"]["global_settings"] == saved["result"]["global_settings"] == batched["global_settings"]
    assert preview["result"]["masks"] == saved["result"]["masks"] == batched["masks"]
    assert preview["result"]["geometry"] == saved["result"]["geometry"] == batched["geometry"]
    assert np.array_equal(np.asarray(PILImage.open(saved["image_out"])), np.asarray(PILImage.open(job.items[0].out)))
    assert source["coordinate_space"] == "source" and preview["coordinate_space"] == "framed"
    assert _decode(source["after"]).shape[:2] == (100, 160)
    assert _decode(practice["after"]).shape == _decode(practice["compare"]["engine"]).shape == (100, 160, 3)
    masks = [_decode(o["alpha"])[..., 3] > 0 for o in source["objects"] if o["found"]]
    assert len(masks) == 2 and not (masks[0] & masks[1]).any()
    for result in (saved["result"], batched):
        assert result["audit"]["run_config"]["mask_refinements"] == own["mask_refinements"]
        assert result["audit"]["run_config"]["selected_regions"] == ["sky"]
    profile = tmp_path / "profile"; profile.mkdir()
    project = {"version": 1, "folder": {"folderSliders": {"exposure": .1}}, "photos": {str(path): {**own, "history": [own]}}}
    projects.save(str(tmp_path), project, str(profile))
    assert projects.load(str(tmp_path), str(profile)) == project


def test_native_detail_is_exact_export_pixels_after_geometry_and_grain(tmp_path):
    path = tmp_path / "a.png"; _image(path, 150, 210)
    service.approve_root(str(tmp_path))
    opts = {"backend": "stub", "concepts": [], "compose": False, "out_format": "png", "grain": 8,
            "photo_settings": {"exposure": .1}, "photo_geometry": {"angle": 4, "crop": [.05, .1, .95, .9]}}
    result = service.detail(str(path), {**opts, "x": .7, "y": .3, "size": 64})
    saved = service.run_one(str(path), str(tmp_path / "out"), opts)
    full = np.asarray(PILImage.open(saved["image_out"]))
    crop = result["crop"]; x, y, w, h = (crop[k] for k in ("x", "y", "width", "height"))
    assert result["scale"] == 1 and result["full"] == {"width": full.shape[1], "height": full.shape[0]}
    assert _decode(result["before"]).shape == (h, w, 3)
    assert np.array_equal(_decode(result["after"]), full[y:y+h, x:x+w])
    for bad in ({"x": float("nan")}, {"size": 2000}, {"size": True}):
        with pytest.raises(ValueError):
            service.detail(str(path), {**opts, **bad})
    with patch.object(service, "_ALLOWED_ROOTS", set()), patch.dict(os.environ, {"AUTOEDIT_ALLOW_ANY_PATH": ""}):
        with pytest.raises(PermissionError):
            service.detail(str(path), opts)


def test_local_only_overrides_cloud_configuration():
    cfg = service.make_cfg({"backend": "stub", "decider": "llm", "allow_cloud": True, "local_only": True})
    assert cfg.decider == "rules" and cfg.allow_cloud is False
    with pytest.raises(ValueError):
        service.make_cfg({"local_only": "yes"})


def test_imported_visual_regions_replay_once_with_same_requested_concepts(tmp_path):
    path = tmp_path / "a.png"; _image(path)
    opts = {"backend": "stub", "concepts": [], "compose": False, "out_format": "png",
            "region_settings": {"sky": {"exposure": .2}}}
    with patch.object(service, "build_pipeline", _build):
        saved = service.run_one(str(path), str(tmp_path / "before"), opts)
        result = saved["result"]
        assert result["adjustments"][0]["resolved_only"] is True
        base = {"adjustments": result["adjustments"], "exposure_bias": result["exposure_bias"],
                "wb_bias": result["wb_bias"]}  # the persisted marker also requests its concept on replay
        replay = service.run_one(str(path), str(tmp_path / "after"), {**opts, "region_settings": {}, "photo_base": base})
    assert replay["result"]["scene"] == result["scene"]
    assert replay["result"]["masks"] == result["masks"]
    assert replay["result"]["global_settings"] == result["global_settings"]
    assert np.array_equal(np.asarray(PILImage.open(saved["image_out"])), np.asarray(PILImage.open(replay["image_out"])))


def test_visual_offsets_stay_inside_displayed_mask_and_text_keeps_union(tmp_path):
    from autoedit import pipeline, instructions, teach
    path = tmp_path / "a.png"; _image(path)
    opts = {"backend": "stub", "concepts": ["sky", "subject"], "compose": False, "grain": 0, "sharpen": False}
    original_plan, plans = instructions.plan, []
    def plan(*args, **kwargs):
        result = original_plan(*args, **kwargs); plans.append(result); return result
    def no_auto(cfg):
        pipe = _build(cfg); pipe.decider = teach.NoEdit(); pipe.looks = {}; pipe.habits = {}; return pipe
    # Hard alpha isolates ownership from the existing soft boundary and global sharpening.
    with patch.object(service, "build_pipeline", no_auto), patch.object(instructions, "plan", plan), \
            patch.object(pipeline, "build_alpha", lambda mask, hw, feather, guide=None: mask.astype(np.float32)):
        baseline = service._edit(str(path), opts, None, None)
        visual = service._edit(str(path), {**opts, "region_settings": {"sky": {"exposure": .3}}}, None, None)
        alpha = _decode(next(m["alpha"] for m in visual["source_overlays"] if m["name"] == "sky"))[..., 3] > 0
        assert np.array_equal(visual["out"][~alpha], baseline["out"][~alpha])
        assert np.any(visual["out"][alpha] != baseline["out"][alpha])
        cells = [cell.mask for cell in plans[-1].cells]
        assert np.max(np.sum(cells, axis=0)) == 1
        assert plans[-1].visual_hit["sky"] == ["sky"] and not plans[-1].hit
        text = {"adjustments": [{"target": "sky", "scope": "photo", "ops": {"exposure": .1}}]}
        text_only = service._edit(str(path), {**opts, "photo_base": text}, None, None)
        both = service._edit(str(path), {**opts, "photo_base": text, "region_settings": {"sky": {"exposure": .3}}}, None, None)
        assert not alpha[35, 80] and np.any(text_only["out"][35, 80] != baseline["out"][35, 80])
        assert np.array_equal(both["out"][~alpha], text_only["out"][~alpha])
        assert len([w for w in both["walk"] if w["target"] == "sky"]) == 2
        assert len([w for w in both["walk"] if w.get("resolved_only")]) == 1
    # Actual feathering still leaves the interior of the overlapped, higher-priority subject
    # alone, beyond its existing soft transition and the global clarity filter's support.
    _image(path, 600, 960)
    with patch.object(service, "build_pipeline", _build):
        baseline = service._edit(str(path), opts, None, None)
        visual = service._edit(str(path), {**opts, "region_settings": {"sky": {"exposure": .3}}}, None, None)
    assert np.allclose(visual["out"][210, 480], baseline["out"][210, 480], atol=1e-6)


def test_detail_assist_and_creative_preset_routes_keep_existing_concept_presets(tmp_path):
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return
    from autoedit import paths
    from autoedit.web.app import create_app
    from autoedit.web import assist
    path = tmp_path / "a.png"; _image(path)
    service.approve_root(str(tmp_path))
    profile = tmp_path / "profile"; profile.mkdir()
    client = TestClient(create_app(paths.configs_dir(), profile=str(profile)), base_url="http://localhost")
    assert "generic" in client.get("/api/v1/presets").json()
    made = client.post("/api/v1/edit-presets", json={"name": "Quiet colour", "settings": {"sliders": {"saturation": -8}}})
    assert made.status_code == 200, made.text
    preset = made.json()["preset"]
    assert client.get("/api/v1/edit-presets").json()["presets"][0]["id"] == preset["id"]
    assert client.delete("/api/v1/edit-presets/"+preset["id"]).json() == {"deleted": True}
    detail = client.post("/api/v1/detail", json={"file": str(path), "backend": "stub", "size": 64})
    assert detail.status_code == 200 and detail.json()["scale"] == 1
    with patch.object(assist, "suggest", return_value={"status": "unavailable", "reason": "test transport"}) as suggest:
        response = client.post("/api/v1/assist", json={"file": str(path), "assist_prompt": "Warm the grass"})
        assert response.status_code == 200 and response.json()["status"] == "unavailable"
        assert suggest.call_args.args[0] == str(path)
    assert client.post("/api/v1/detail", json={"file": ["not a path"]}).status_code == 400
