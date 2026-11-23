"""Saved editor controls, durable projects and reusable preview sources."""

import base64
import io
import json
import os
import time
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit.web import jobs, projects, service


def _photo(path, shape=(64, 96)):
    h, w = shape
    ramp = np.tile(np.linspace(40, 190, w, dtype=np.uint8), (h, 1))
    PILImage.fromarray(np.stack([ramp, ramp, ramp], axis=-1)).save(path)


def test_editor_controls_preview_save_and_batch_agree(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _photo(a); _photo(b)
    opts = {"backend": "stub", "concepts": [], "compose": False, "consistent": False,
            "photo_settings": {"exposure": .2, "temp": 3, "saturation": -5}, "intensity": .5}
    preview = service.preview(str(a), opts)
    saved = service.run_one(str(a), str(tmp_path / "single"), opts)
    assert preview["result"]["global_settings"] == saved["result"]["global_settings"]
    assert any(s["op"] == "exposure" and s["value"] == .2 for s in preview["editor"]["sliders"])
    assert any(x["scope"] == "photo" and x["ops"].get("exposure") == .2 for x in saved["result"]["adjustments"])
    base = {"backend": "stub", "concepts": [], "compose": False, "consistent": False}
    job = jobs.start(str(tmp_path), [str(a), str(b)], str(tmp_path / "batch"), base,
                     photo_options={str(a): {"intensity": .5, "photo_settings": opts["photo_settings"]}})
    until = time.monotonic() + 20
    while job.status not in ("done", "error", "cancelled") and time.monotonic() < until:
        time.sleep(.02)
    assert job.status == "done" and all(x.status != "failed" for x in job.items)
    got = json.loads((tmp_path / "batch" / "a.autoedit.json").read_text())
    untouched = json.loads((tmp_path / "batch" / "b.autoedit.json").read_text())
    assert got["global_settings"] == saved["result"]["global_settings"]
    assert not untouched["adjustments"]


def test_editor_controls_reject_unknown_and_invalid_values(tmp_path):
    for bad in ({"api_key": 1}, {"exposure": 3}, {"temp": True}, {"temp": float("nan")}, []):
        with pytest.raises(ValueError):
            service.check_photo_settings(bad)
    assert service.check_photo_settings({"exposure": 0}) == {}
    for bad in ({str(tmp_path / "other.jpg"): {}}, {str(tmp_path / "a.jpg"): {"backend": "sam3"}}):
        with pytest.raises(ValueError):
            jobs._check_photo_options(bad, [str(tmp_path / "a.jpg")], {"backend": "stub"}, None, None)


def test_superseded_preview_is_skipped():
    newer = {"preview_session": "workspace-unit-test", "preview_revision": 8}
    service._preview_request(newer, register=True)
    service._preview_request(newer)
    with pytest.raises(ValueError, match="superseded"):
        service._preview_request({**newer, "preview_revision": 7})
    with pytest.raises(ValueError):
        service._preview_request({"preview_session": "x", "preview_revision": True}, register=True)


def test_look_variants_keep_saved_controls(tmp_path):
    from autoedit.pipeline import Pipeline
    path = tmp_path / "a.png"; _photo(path)
    original, seen = Pipeline.process_one, []
    def render(self, *args, **kwargs):
        seen.append(kwargs.get("adjustments"))
        return original(self, *args, **kwargs)
    with patch.object(Pipeline, "process_one", render):
        result = service.make_variants(str(path), {"backend": "stub", "concepts": [],
            "photo_settings": {"temp": 4}, "variants": [{"name": "natural", "look": "natural"}]})
    assert result["variants"] and seen
    assert seen[0][0].ops == {"temp": 4.0} and seen[0][0].scope == "photo"


def test_reference_requires_approval_for_every_render_path(tmp_path):
    folder = tmp_path / "photos"; folder.mkdir()
    elsewhere = tmp_path / "references"; elsewhere.mkdir()
    source, reference = folder / "a.png", elsewhere / "reference.png"
    _photo(source); _photo(reference)
    opts = {"backend": "stub", "reference": str(reference), "concepts": []}
    with patch.object(service, "_ALLOWED_ROOTS", {os.path.realpath(folder)}), \
            patch.dict(os.environ, {"AUTOEDIT_ALLOW_ANY_PATH": ""}):
        for render in (
            lambda: service.preview(str(source), opts),
            lambda: service.run_one(str(source), str(folder / "single"), opts),
            lambda: service.make_variants(str(source), opts),
            lambda: service.run_batch(str(folder), str(folder / "batch"), opts),
            lambda: jobs.start(str(folder), [str(source)], str(folder / "job"), opts),
            lambda: jobs.start(str(folder), [str(source)], str(folder / "job"), {"backend": "stub"},
                               photo_options={str(source): {"reference": str(reference)}}),
        ):
            with pytest.raises(PermissionError):
                render()
        assert not (folder / "single").exists() and not (folder / "job").exists()
        # Loading the saved filename does not grant access; selecting it through the picker does.
        service.approve_root(str(reference))
        assert service.make_cfg(opts).reference == str(reference)


def test_photo_default_look_matches_genre_in_preview_save_and_batch(tmp_path):
    source = tmp_path / "a.png"; _photo(source)
    base = {"backend": "stub", "genre": "astro", "concepts": [], "consistent": False}
    opts = {**base, "look": ""}
    assert service.make_cfg(opts).look == service.make_cfg(base).look == "astro"
    assert opts["look"] == ""   # normalization does not mutate a job's shared options
    preview = service.preview(str(source), base)
    saved = service.run_one(str(source), str(tmp_path / "single"), opts)
    assert json.loads((tmp_path / "single" / "run.json").read_text())["input_dir"] == str(tmp_path)
    job = jobs.start(str(tmp_path), [str(source)], str(tmp_path / "batch"), {**base, "look": "vivid"},
                     photo_options={str(source): {"look": ""}})
    until = time.monotonic() + 20
    while job.status not in ("done", "error", "cancelled") and time.monotonic() < until:
        time.sleep(.02)
    assert job.status == "done" and job.items[0].status != "failed"
    batched = json.loads((tmp_path / "batch" / "a.autoedit.json").read_text())
    assert preview["result"]["global_settings"] == saved["result"]["global_settings"] == batched["global_settings"]


def test_imported_photo_base_survives_project_preview_save_batch_and_new_edits(tmp_path):
    source = tmp_path / "a.png"; _photo(source)
    base = {"adjustments": [{"target": "global", "scope": "photo", "ops": {"exposure": .2, "saturation": -4}}],
            "exposure_bias": .15, "wb": [3, -2], "wb_bias": [1, 2]}
    opts = {"backend": "stub", "concepts": [], "compose": False, "consistent": False,
            "neutral": [.5, .5], "photo_base": base, "photo_settings": {"exposure": .1}}
    cfg = service.make_cfg(opts)
    assert cfg.wb == [3, -2] and cfg.neutral is None
    service.approve_root(str(tmp_path))
    profile = tmp_path / "profile"; profile.mkdir()
    project = {"version": 1, "photos": {str(source): {"base": base, "history": [{"base": base}]}}}
    projects.save(str(tmp_path), project, str(profile))
    assert projects.load(str(tmp_path), str(profile)) == project
    preview = service.preview(str(source), opts)
    saved = service.run_one(str(source), str(tmp_path / "single"), opts)
    plain_opts = {k: v for k, v in opts.items() if k not in ("photo_base", "photo_settings")}
    job = jobs.start(str(tmp_path), [str(source)], str(tmp_path / "batch"), plain_opts,
                     photo_options={str(source): {"photo_base": base, "photo_settings": opts["photo_settings"]}})
    until = time.monotonic() + 20
    while job.status not in ("done", "error", "cancelled") and time.monotonic() < until:
        time.sleep(.02)
    assert job.status == "done" and job.items[0].status != "failed"
    batched = json.loads((tmp_path / "batch" / "a.autoedit.json").read_text())
    for result in (preview["result"], saved["result"], batched):
        assert result["exposure_bias"] == .15
        assert result["wb_bias"] == [0.0, 0.0]  # an absolute neutral WB replaces the older batch offset
        assert abs(sum(a["ops"].get("exposure", 0) for a in result["adjustments"]) - .3) < 1e-9
        assert result["global_settings"] == saved["result"]["global_settings"]
    # Practice mode starts independently, including when the imported base had a neutral WB.
    tried = service.preview(str(source), {**opts, "try": {"sliders": {}}})
    control = service.preview(str(source), {**plain_opts, "try": {"sliders": {}}})
    assert tried["after"] == control["after"]
    assert tried["result"]["exposure_bias"] == 0


def test_imported_photo_base_validates_scopes_ops_and_no_external_fields():
    from autoedit.web.photo_base import check
    adj = {"target": "global", "scope": "photo", "ops": {"exposure": .2}}
    bad = [
        {"reference": "https://invalid.example/reference.jpg"},
        {"adjustments": [{**adj, "scope": "run"}]},
        {"adjustments": [{**adj, "ops": {"exposure": 99}}]},
        {"adjustments": [{**adj, "ops": {"unsupported": 1}}]},
        {"adjustments": [{**adj, "target": "mark:area:0,0,1,1"}]},
        {"adjustments": [adj] * 201},
        {"exposure_bias": float("nan")}, {"exposure_bias": True}, {"exposure_bias": 4},
        {"wb": [1]}, {"wb": [0, float("inf")]}, {"wb_bias": None}, {"wb_bias": [0, True]},
    ]
    for value in bad:
        with pytest.raises(ValueError):
            check(value)
    assert "exposure_bias" not in check({"adjustments": [adj]})


def test_imported_base_without_bias_keeps_batch_consistency(tmp_path):
    from autoedit.consistency import Bias
    source = tmp_path / "a.png"; _photo(source)
    base = {"adjustments": [{"target": "global", "scope": "photo", "ops": {"temp": 2}}], "wb_bias": [3, -1]}
    with patch.object(jobs.consistency, "plan", return_value={str(source): Bias(exposure=.25, temp=8, tint=4)}):
        job = jobs.start(str(tmp_path), [str(source)], str(tmp_path / "batch"),
                         {"backend": "stub", "concepts": [], "compose": False},
                         photo_options={str(source): {"photo_base": base}})
        until = time.monotonic() + 20
        while job.status not in ("done", "error", "cancelled") and time.monotonic() < until:
            time.sleep(.02)
    assert job.status == "done" and job.items[0].status != "failed"
    result = json.loads((tmp_path / "batch" / "a.autoedit.json").read_text())
    assert result["exposure_bias"] == .25
    assert result["wb_bias"] == [3.0, -1.0]
    opts = {"backend": "stub", "concepts": [], "compose": False, "photo_base": {**base, "exposure_bias": .25}}
    assert service.preview(str(source), opts)["result"]["global_settings"] == result["global_settings"]
    saved = service.run_one(str(source), str(tmp_path / "single"), opts)
    assert saved["result"]["global_settings"] == result["global_settings"]


def test_preview_cache_invalidation_and_owned_arrays(tmp_path):
    p = tmp_path / "a.png"
    _photo(p, (72, 120))
    cfg = service.make_cfg({"backend": "stub"})
    real = service.load_image
    with patch.object(service, "load_image", wraps=real) as decoder:
        work, proxy = service._preview_images(str(p), cfg, 640)
        want = work.rgb.copy()
        work.rgb[:] = 0
        if proxy is not None:
            proxy.rgb[:] = 0
        again, _ = service._preview_images(str(p), cfg, 2560)
        assert np.array_equal(again.rgb, want) and decoder.call_count == 1
        _photo(p, (73, 120))
        changed, _ = service._preview_images(str(p), cfg, 640)
        assert changed.height == 73 and decoder.call_count == 2
        alternate = cfg.model_copy(update={"raw_mode": "camera"})
        service._preview_images(str(p), alternate, 640)
        assert decoder.call_count == 3


def test_quick_preview_dimensions_and_plan(tmp_path):
    p = tmp_path / "a.png"
    _photo(p, (480, 800))
    opts = {"backend": "stub", "concepts": [], "compose": False, "photo_settings": {"temp": 2}}
    quick = service.preview(str(p), {**opts, "preview_edge": 640})
    sharp = service.preview(str(p), opts)
    image = PILImage.open(io.BytesIO(base64.b64decode(quick["after"].split(",", 1)[1])))
    assert max(image.size) == 640 and quick["preview_edge"] == 640
    assert quick["result"]["global_settings"] == sharp["result"]["global_settings"]
    with pytest.raises(ValueError):
        service.preview(str(p), {**opts, "preview_edge": 123})


def test_projects_restore_history_without_port_or_credentials(tmp_path):
    folder = tmp_path / "photos"; folder.mkdir()
    profile = tmp_path / "profile"; profile.mkdir()
    path = str(folder / "a.jpg")
    service.approve_root(str(folder))
    data = {"version": 1, "folder": {"look": "natural", "settings": {"raw_recover": True}},
            "photos": {path: {"sliders": {"temp": 2}, "history": [{"sliders": {}}], "future": []}},
            "files": {path: {"checked": False, "status": "finished", "out": str(folder / "out.jpg")}}, "last_index": 0}
    assert projects.load(str(folder), str(profile)) is None
    projects.save(str(folder), data, str(profile))
    assert projects.load(str(folder / "."), str(profile)) == data
    data["folder"]["settings"]["api_key"] = "not-for-disk"
    with pytest.raises(ValueError):
        projects.save(str(folder), data, str(profile))
    assert "not-for-disk" not in next((profile / "projects").glob("*.json")).read_text()
    data["folder"]["settings"].pop("api_key")
    data["photos"][str(tmp_path / "outside.jpg")] = {}
    with pytest.raises(ValueError):
        projects.save(str(folder), data, str(profile))


def test_projects_routes_reopen_and_guard(tmp_path):
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return
    from autoedit import paths
    from autoedit.web.app import create_app
    folder = tmp_path / "photos"; folder.mkdir()
    profile = tmp_path / "profile"; profile.mkdir()
    service.approve_root(str(folder))
    data = {"version": 1, "folder": {}, "photos": {}, "files": {}}
    first = TestClient(create_app(paths.configs_dir(), profile=str(profile)), base_url="http://localhost:6100")
    assert first.post("/api/v1/project", json={"dir": str(folder), "project": data}).json() == {"saved": True}
    second = TestClient(create_app(paths.configs_dir(), profile=str(profile)), base_url="http://localhost:6200")
    assert second.get("/api/v1/project", params={"dir": str(folder)}).json() == {"project": data}
    with patch.object(service, "_ALLOWED_ROOTS", set()), patch.dict(os.environ, {"AUTOEDIT_ALLOW_ANY_PATH": ""}):
        assert second.get("/api/v1/project", params={"dir": str(folder)}).status_code == 403
