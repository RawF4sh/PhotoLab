"""Native edits survive a lost browser catalog and cannot silently disappear from an export."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

from autoedit import batch, declare, paths
from autoedit.web import editor_files, manual_stage, projects, service


def _source(tmp_path):
    path = tmp_path / "input" / "frame.jpg"
    path.parent.mkdir()
    Image.new("RGB", (60, 40), "#918365").save(path)
    service.approve_root(str(tmp_path))
    return str(path), str(tmp_path / "profile")


def test_manual_state_rebuild_cas_undo_redo_and_automatic_skip(tmp_path):
    path, profile = _source(tmp_path)
    folder = str(Path(path).parent)
    base = {"light": {"exposure": 0}}
    settings = {"light": {"exposure": .7}}
    pointer = manual_stage.save(path, settings, "cached-1", None, profile, baseline=base)["manual"]
    assert manual_stage.load(path, profile)["changed"] == ["light.exposure"]
    assert manual_stage.save(path, settings, "cached-1", None, profile, baseline=base)["manual"] == pointer
    with pytest.raises(ValueError, match="another window"):
        manual_stage.save(path, {"light": {"exposure": .8}}, "cached-1", None, profile)
    # An unrelated autosave without the new marker or history preserves both.
    projects.save(folder, {"version": 1, "photos": {path: {"look": "test"}}}, profile)
    saved = projects.load(folder, profile)
    assert saved["photos"][path]["history"] == [{}]
    assert projects.manual_sources(folder, profile) == {str(Path(path).resolve())}
    with pytest.raises(ValueError, match="Finish in Photo editor"):
        service.run_one(path, str(tmp_path / "out"), {"backend": "stub"}, profile=profile)
    cfg = service.make_cfg({"backend": "stub"}, paths.configs_dir(), profile)
    result = batch.run(cfg, folder, str(tmp_path / "out"), consistent=False, workers=1)
    assert result["processed"] == 0 and "Finish in Photo editor" in str(result["failures"])
    undo = copy.deepcopy(saved)
    undo["photos"][path] = {"future": [{"manual": pointer}]}
    projects.save(folder, undo, profile, manual_expected={path: pointer["sha256"]})
    assert manual_stage.load(path, profile) == {"manual": None, "baseline": base}
    projects.save(folder, saved, profile, manual_expected={path: None})
    assert manual_stage.load(path, profile)["settings"] == settings
    blob = next(Path(profile).rglob(pointer["sha256"] + ".json"))
    blob.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        manual_stage.load(path, profile)


def test_manual_export_rejects_wrong_frame_applies_metadata_and_declares_spots(tmp_path):
    try:
        import tifffile
    except ImportError:
        return
    path, profile = _source(tmp_path)
    exif = Image.Exif()
    exif[0x010f] = "Test camera"
    exif[0x8769] = {0xA431: "camera serial", 0xA435: "lens serial"}
    exif[0x8825] = {1: "N", 2: (1, 0, 0), 3: "E", 4: (1, 0, 0)}
    Image.new("RGB", (60, 40), "#483655").save(path, exif=exif)
    original_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    stage = {"light": {"exposure": .3}, "spots": [{"mode": "heal"}], "masks": [{}]}
    checksum = manual_stage.save(path, stage, "test-base", None, profile, baseline={"light":{"exposure":0}})["manual"]["sha256"]
    options = {"editor_original": True, "metadata": "shareable", "export": "web", "backend": "stub"}
    cfg = paths.configs_dir()
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}):
        full = editor_files.working_picture(path, options, cfg, profile, full_size=True)
        # A 100 MP RGB16 working TIFF can exceed the source-drop limit.
        large = manual_stage.issue(path, options, editor_files.identity(path), None, (10000, 10000, 3), profile)
        assert editor_files.UPLOAD_BYTES < 600_000_000 <= manual_stage.upload_limit(path, large) <= editor_files.CACHE_BYTES
        assert manual_stage.upload_limit(path, full["ticket"]) < editor_files.UPLOAD_BYTES
        with pytest.raises(ValueError, match="this photo"):
            manual_stage.upload_limit(path + ".other", large)
        file = tmp_path / "returned.tif"
        out_dir = str(tmp_path / "exports")
        def export(key):
            return manual_stage.export(path, full["ticket"], key, file, out_dir, cfg, profile)
        tifffile.imwrite(file, np.full((4, 6, 3), 1200, dtype=np.uint16))
        with pytest.raises(ValueError, match="full source dimensions"):
            export(checksum)
        tifffile.imwrite(file, np.full((40, 60, 3), 26000, dtype=np.uint16))
        with pytest.raises(ValueError, match="changed"):
            export("")
        answer = export(checksum)
        with pytest.raises(ValueError, match="Prepare"):
            export(checksum)
    assert Path(answer["image_out"]).name == "frame_web.jpg"
    with Image.open(answer["image_out"]) as image:
        meta = image.getexif()
        assert meta[0x010f] == "Test camera" and not meta.get_ifd(0x8825)
        assert not {0xA431, 0xA435}.intersection(meta.get_ifd(0x8769))
        assert image.size == (60, 40)
    audit = json.loads(Path(answer["sidecar"]).read_text())["audit"]["manual"]
    assert not audit["safety_checked"] and audit["spot_tools"] == ["heal"] and audit["masks"] == 1
    assert declare.declaration(answer["sidecar"])["manual"] == audit
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == original_hash


def test_project_1000_manual_records_remain_small_and_blobs_are_bounded(tmp_path):
    path, profile = _source(tmp_path)
    with pytest.raises(ValueError, match="per-photo size"):
        manual_stage.save(path, {"masks": ["x" * manual_stage.MAX_BYTES]}, "base", None, profile)
    pointer = {"sha256": "0" * 64, "version": "0.21.1"}
    folder = Path(path).parent
    project = {"version": 1, "photos": {str(folder / f"{i}.jpg"): {"manual": pointer} for i in range(1000)}}
    assert len(projects.check_project(str(folder), project)) < projects.MAX_BYTES / 10
