"""Native edits survive a lost browser catalog and cannot silently disappear from an export."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

from autoedit import adjust, batch, declare, emit, paths
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
    assert "checksum" in manual_stage.load(path, profile)["lost"]     # fix 5: removed, not stuck


def test_manual_export_rejects_wrong_frame_applies_metadata_and_declares_spots(tmp_path):
    pytest.importorskip("tifffile")
    import tifffile
    path, profile = _source(tmp_path)
    exif = Image.Exif()
    exif[0x010f] = "Test camera"
    exif[0x8769] = {0xA431: "camera serial", 0xA435: "lens serial"}
    exif[0x8825] = {1: "N", 2: (1, 0, 0), 3: "E", 4: (1, 0, 0)}
    Image.new("RGB", (60, 40), "#483655").save(path, exif=exif)
    original_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    stage = {"light": {"exposure": .3}, "spots": [{"mode": "heal"}, {"size": 3}], "masks": [{}]}
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
    assert not audit["safety_checked"] and audit["masks"] == 1
    assert audit["spots"] == 2 and audit["spot_tools"] == ["heal", "spot"]     # a spot with no mode is still recorded
    assert declare.declaration(answer["sidecar"])["manual"] == audit
    assert "manual content-editing tools" in declare._page([declare.declaration(answer["sidecar"])])
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == original_hash


def test_project_1000_manual_records_remain_small_and_blobs_are_bounded(tmp_path):
    path, profile = _source(tmp_path)
    with pytest.raises(ValueError, match="per-photo size"):
        manual_stage.save(path, {"masks": ["x" * manual_stage.MAX_BYTES]}, "base", None, profile)
    pointer = {"sha256": "0" * 64, "version": "0.21.1"}
    folder = Path(path).parent
    project = {"version": 1, "photos": {str(folder / f"{i}.jpg"): {"manual": pointer} for i in range(1000)}}
    assert len(projects.check_project(str(folder), project)) < projects.MAX_BYTES / 10


def _stage(n):
    return {"light": {"exposure": n / 10}}


def test_a_run_of_manual_saves_is_one_undo_step_and_leaves_one_file(tmp_path):
    path, profile = _source(tmp_path)
    folder = str(Path(path).parent)
    projects.save(folder, {"version": 1, "photos": {path: {"look": "warm"}}}, profile)
    before = projects.load(folder, profile)["photos"][path]
    expected = None
    for n in range(1, 6):
        expected = manual_stage.save(path, _stage(n), "cached-1", expected, profile,
                                     baseline={"light": {"exposure": 0}})["manual"]["sha256"]
    photo = projects.load(folder, profile)["photos"][path]
    assert photo["history"] == [*before.get("history", []), {"look": "warm"}]    # one new entry, no manual in it
    assert photo["manual"]["sha256"] == expected
    assert [p.name for p in (Path(profile) / "projects").rglob("*.json") if len(p.stem) == 64 and "manual" in str(p)] \
        == [expected + ".json"]
    undo = {**projects.load(folder, profile), "photos": {path: {**photo["history"][-1], "history": photo["history"][:-1],
                                                                "future": [{k: v for k, v in photo.items() if k not in ("history", "future")}]}}}
    projects.save(folder, undo, profile, manual_expected={path: expected})
    assert manual_stage.load(path, profile) == {"manual": None, "baseline": {"light": {"exposure": 0}}}
    assert projects.load(folder, profile)["photos"][path]["look"] == "warm"

def test_a_reset_after_manual_saves_is_its_own_undo_step(tmp_path):
    path, profile = _source(tmp_path)
    folder = str(Path(path).parent)
    one = manual_stage.save(path, _stage(1), "cached-1", None, profile)["manual"]["sha256"]
    two = manual_stage.save(path, _stage(2), "cached-1", one, profile)["manual"]["sha256"]
    history = projects.load(folder, profile)["photos"][path]["history"]
    assert len(history) == 1
    manual_stage.save(path, None, "cached-1", two, profile)
    after = projects.load(folder, profile)["photos"][path]
    assert len(after["history"]) == 2 and after["history"][-1]["manual"]["sha256"] == two and "manual" not in after


def test_a_lost_manual_file_is_removed_once_and_the_photo_works_again(tmp_path):
    path, profile = _source(tmp_path)
    folder = str(Path(path).parent)
    pointer = manual_stage.save(path, _stage(3), "cached-1", None, profile)["manual"]
    out = str(tmp_path / "out")
    with pytest.raises(ValueError, match="Finish in Photo editor"):
        service.run_one(path, out, {"backend": "stub"}, profile=profile)
    next(Path(profile).rglob(pointer["sha256"] + ".json")).unlink()
    lost = manual_stage.load(path, profile)
    assert lost["manual"] is None and "missing" in lost["lost"]
    assert "manual" not in projects.load(folder, profile)["photos"][path]
    assert manual_stage.load(path, profile) == {"manual": None}
    assert Path(service.run_one(path, out, {"backend": "stub"}, profile=profile)["image_out"]).is_file()


def test_a_lost_current_file_keeps_the_baseline_of_a_readable_snapshot(tmp_path):
    path, profile = _source(tmp_path)
    base = {"light": {"exposure": 0}}
    first = manual_stage.save(path, _stage(1), "cached-1", None, profile, baseline=base)["manual"]
    manual_stage.save(path, None, "cached-1", first["sha256"], profile)          # reset: history keeps the first
    second = manual_stage.save(path, _stage(2), "cached-1", None, profile)["manual"]
    next(Path(profile).rglob(second["sha256"] + ".json")).unlink()
    assert manual_stage.load(path, profile)["baseline"] == base
    for blob in Path(profile).rglob(first["sha256"] + ".json"):
        blob.unlink()
    assert manual_stage.load(path, profile) == {"manual": None}     # an unreadable snapshot is skipped, never raised


def test_a_command_line_rerender_cannot_drop_a_manual_stage(tmp_path):
    path, profile = _source(tmp_path)
    first = service.run_one(path, str(tmp_path / "out"), {"backend": "stub"}, paths.configs_dir(), profile)
    exported = Path(first["image_out"]).read_bytes()
    manual_stage.save(path, _stage(3), "cached-1", None, profile, baseline={"light": {"exposure": 0}})
    with pytest.raises(ValueError, match="Finish in Photo editor"):
        adjust.rerender(first["sidecar"], sets=["global.exposure=0.1"], source=path, profile=profile)
    assert Path(first["image_out"]).read_bytes() == exported
    assert adjust.rerender(first["sidecar"], sets=["global.exposure=0.1"], source=path, profile=profile, apply=False)


def test_a_single_export_never_replaces_the_original_or_another_photos_export(tmp_path):
    path, profile = _source(tmp_path)                    # input/frame.jpg
    folder = Path(path).parent
    png = folder / "frame.png"
    Image.new("RGB", (60, 40), "#112233").save(png)
    original = Path(path).read_bytes()
    with pytest.raises(ValueError, match="did not write"):
        service.run_one(str(png), str(folder), {"backend": "stub"}, profile=profile)
    assert Path(path).read_bytes() == original
    out = str(tmp_path / "out")
    a = service.run_one(str(png), out, {"backend": "stub"}, profile=profile)
    b = service.run_one(path, out, {"backend": "stub"}, profile=profile)
    assert a["image_out"] != b["image_out"] and Path(a["image_out"]).is_file() and Path(a["sidecar"]).is_file()
    assert Path(b["image_out"]).name == "frame-2.jpg"
    assert service.run_one(str(png), out, {"backend": "stub"}, profile=profile)["image_out"] == a["image_out"]
