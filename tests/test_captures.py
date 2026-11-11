"""Selection must not duplicate captures or traverse outside explicit local inputs."""
import json
from pathlib import Path

from PIL import Image

from autoedit.captures import assert_separate_output, discover_captures, verify_sources


def test_recursive_raw_preference_and_corrupt_raw_still_selected(tmp_path):
    folder = tmp_path / "photos" / "event"
    folder.mkdir(parents=True)
    (folder / "IMG.CR2").write_bytes(b"corrupt raw")
    Image.new("RGB", (20, 20)).save(folder / "IMG.JPG")
    Image.new("RGB", (20, 20), "blue").save(folder / "other.jpeg")
    (folder / "IMG.xmp").write_text("ignored")
    report = discover_captures(tmp_path / "photos")
    assert report["counts"]["captures"] == 2
    raw = next(e for e in report["entries"] if e["source_format"] == "cr2")
    assert raw["camera_reference"].endswith("IMG.JPG")
    assert len(raw["companions"]) == 1
    assert not verify_sources(report["entries"])
    (folder / "IMG.CR2").write_bytes(b"changed")
    assert verify_sources(report["entries"])


def test_curated_rank_names_pair_by_original_identity(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    raw = selected / "0002_dark__X.cr2"
    jpg = selected / "0999_snow__X.jpg"
    raw.write_bytes(b"raw")
    Image.new("RGB", (20, 20)).save(jpg)
    (tmp_path / "candidates.json").write_text(json.dumps({"items": [
        {"source_path": "/never/read/event/X.CR2", "copied_path": str(raw), "bucket": "dark"},
        {"source_path": "/never/read/event/X.JPG", "copied_path": str(jpg), "bucket": "snow"}]}))
    report = discover_captures(tmp_path, split="holdout")
    assert len(report["entries"]) == 1
    entry = report["entries"][0]
    assert entry["source"] == str(raw.resolve()) and entry["split"] == "holdout"
    assert entry["category"] == "dark"


def test_same_filename_different_events_not_paired(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        Image.new("RGB", (20, 20)).save(tmp_path / name / "same.jpg")
    assert len(discover_captures(tmp_path)["entries"]) == 2


def test_output_overlap_and_symlink_rejected(tmp_path):
    import pytest
    source = tmp_path / "photos"
    source.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError):
        assert_separate_output(source, alias / "out")
    with pytest.raises(ValueError):
        assert_separate_output(source, tmp_path)
    assert_separate_output(source, tmp_path / "exports")


def test_supported_formats_are_discovered_recursively_and_raw_preferred(tmp_path):
    from autoedit.imageio import _SUPPORTED
    from autoedit.captures import EXTENSIONS
    assert EXTENSIONS == _SUPPORTED
    folder = tmp_path / "event"
    folder.mkdir()
    (folder / "capture.NEF").write_bytes(b"individually isolated corrupt NEF")
    Image.new("RGB", (20, 20), "red").save(folder / "capture.TIFF")
    Image.new("RGB", (20, 20), "blue").save(folder / "independent.PNG")
    Image.new("RGB", (20, 20), "green").save(folder / "separate.tif")
    (folder / "capture.xmp").write_text("ignored")
    report = discover_captures(tmp_path)
    assert report["counts"]["files"] == 4 and report["counts"]["captures"] == 3
    assert report["counts"]["raw"] == 1 and report["counts"]["other_pixel"] == 2
    raw = next(e for e in report["entries"] if e["source_format"] == "nef")
    assert raw["companions"][0]["format"] == "tiff"


def test_renamed_exact_duplicates_coalesce_even_without_saved_hashes(tmp_path):
    a, b = tmp_path / "original.jpg", tmp_path / "renamed.jpeg"
    Image.new("RGB", (20, 20), "blue").save(a)
    b.write_bytes(a.read_bytes())
    report = discover_captures(tmp_path, hash_sources=False)
    assert report["counts"]["captures"] == 1
    entry = report["entries"][0]
    assert entry["companions"][0]["reason"] == "exact-content duplicate"
    assert entry["source_sha256"] is None and entry["companions"][0]["sha256"] is None


def test_distinct_curated_identities_remain_authoritative_for_identical_bytes(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    Image.new("RGB", (20, 20)).save(a)
    b.write_bytes(a.read_bytes())
    (tmp_path / "candidates.json").write_text(json.dumps({"items": [
        {"source_path": "/event/one.jpg", "copied_path": str(a)},
        {"source_path": "/event/two.jpg", "copied_path": str(b)}]}))
    assert discover_captures(tmp_path)["counts"]["captures"] == 2


def test_renamed_raw_jpeg_pair_requires_unambiguous_precise_metadata(tmp_path):
    from autoedit import captures
    raw, jpg = tmp_path / "raw_name.cr2", tmp_path / "camera_name.jpg"
    raw.write_bytes(b"raw capture one")
    Image.new("RGB", (20, 20), "red").save(jpg)
    base = {"camera": "Camera A", "captured_at": "2026-06-20T10:00:00", "exposure_bias": None}
    original = captures.metadata
    try:
        # A whole-second time shared by a burst does not prove capture identity.
        captures.metadata = lambda path: dict(base)
        assert discover_captures(tmp_path)["counts"]["captures"] == 2
        captures.metadata = lambda path: {**base, "camera_serial": "body-1", "capture_subseconds": "123"}
        paired = discover_captures(tmp_path)
        assert paired["counts"]["captures"] == 1
        assert paired["entries"][0]["source"] == str(raw.resolve())
        assert paired["entries"][0]["camera_reference"] == str(jpg.resolve())
        # More than one RAW at the same precise timestamp is ambiguous, never many-to-one.
        (tmp_path / "second_raw.cr2").write_bytes(b"raw capture two")
        assert discover_captures(tmp_path)["counts"]["captures"] == 3
    finally:
        captures.metadata = original
