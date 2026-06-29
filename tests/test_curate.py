import json
import os

import numpy as np
from PIL import Image

from autoedit import curate
from autoedit.cli import main


def _write_rgb(path, rgb):
    arr = np.zeros((36, 48, 3), dtype=np.uint8)
    arr[:, :] = rgb
    Image.fromarray(arr, mode="RGB").save(path)


def test_curate_copies_diverse_subset_without_moving_sources(tmp_path):
    src = tmp_path / "library"
    nested = src / "nested"
    nested.mkdir(parents=True)
    _write_rgb(src / "dark.jpg", (18, 20, 24))
    _write_rgb(src / "warm.jpg", (210, 145, 75))
    _write_rgb(nested / "green.jpg", (40, 150, 55))
    _write_rgb(nested / "snow.jpg", (230, 232, 235))

    out = tmp_path / "curated"
    manifest = curate.curate(str(src), str(out), count=3)

    assert manifest["selected"] == 3
    assert (out / "selected").is_dir()
    assert (out / "thumbs").is_dir()
    assert (out / "candidates.json").exists()
    assert (out / "contact-sheet.html").exists()
    assert len(list((out / "selected").iterdir())) == 3
    assert (src / "dark.jpg").exists()
    assert (src / "warm.jpg").exists()
    assert (nested / "green.jpg").exists()
    assert (nested / "snow.jpg").exists()
    buckets = {item["bucket"] for item in manifest["items"]}
    assert len(buckets) >= 2
    with open(out / "candidates.json") as fh:
        saved = json.load(fh)
    assert saved["input_dir"] == os.path.abspath(src)
    assert saved["copy_originals"] is True


def test_curate_non_recursive_scans_top_level_only(tmp_path):
    src = tmp_path / "library"
    nested = src / "nested"
    nested.mkdir(parents=True)
    _write_rgb(src / "top.jpg", (120, 120, 120))
    _write_rgb(nested / "nested.jpg", (230, 230, 230))

    paths = curate.list_images(str(src), recursive=False)

    assert paths == [str(src / "top.jpg")]


def test_curate_best_mode_scores_likely_keepers(tmp_path):
    src = tmp_path / "library"
    src.mkdir()
    _write_rgb(src / "flat_dark.jpg", (18, 18, 18))

    textured = np.zeros((48, 64, 3), dtype=np.uint8)
    textured[:, :32] = (35, 70, 120)
    textured[:, 32:] = (225, 210, 170)
    Image.fromarray(textured, mode="RGB").save(src / "keeper.jpg")

    out = tmp_path / "curated"
    manifest = curate.curate(str(src), str(out), count=1, mode="best")

    assert manifest["mode"] == "best"
    assert manifest["selected"] == 1
    assert manifest["items"][0]["source_path"].endswith("keeper.jpg")
    assert (out / "selected.csv").exists()


def test_curate_can_emit_xmp_sidecars_next_to_copies(tmp_path):
    src = tmp_path / "library"
    src.mkdir()
    _write_rgb(src / "one.jpg", (80, 130, 190))

    out = tmp_path / "curated"
    manifest = curate.curate(str(src), str(out), count=1, mode="best", emit_xmp=True)

    assert manifest["emit_xmp"] is True
    xmp_path = manifest["items"][0]["xmp_path"]
    assert xmp_path.endswith(".xmp")
    assert os.path.exists(xmp_path)
    text = open(xmp_path).read()
    assert "AutoEdit Pick" in text
    assert "xmp:Rating" in text


def test_curate_skips_missing_source_during_copy(tmp_path):
    src = tmp_path / "library"
    src.mkdir()
    missing = src / "gone.jpg"
    feats = {
        "lum_mean": 0.5,
        "lum_std": 0.1,
        "lum_p10": 0.3,
        "lum_p90": 0.7,
        "warmth": 0.0,
        "green": 0.0,
        "blue_top": 0.0,
        "top_lum": 0.5,
        "dark_frac": 0.0,
        "bright_frac": 0.0,
        "snow_frac": 0.0,
        "aspect": 1.5,
        "edge_mean": 0.0,
        "edge_p90": 0.0,
    }
    original_scan = curate.scan
    try:
        curate.scan = lambda *args, **kwargs: [curate.Candidate(str(missing), feats, "neutral")]
        manifest = curate.curate(str(src), str(tmp_path / "curated"), count=1)
    finally:
        curate.scan = original_scan

    assert manifest["selected"] == 0
    assert manifest["skipped"] == 1
    assert manifest["skipped_items"][0]["source_path"] == os.path.abspath(missing)


def test_cli_curate_command(tmp_path):
    src = tmp_path / "library"
    src.mkdir()
    _write_rgb(src / "one.jpg", (30, 60, 190))
    _write_rgb(src / "two.jpg", (220, 180, 90))
    out = tmp_path / "curated"

    rc = main(["curate", str(src), str(out), "--count", "1", "--mode", "best", "--emit-xmp"])

    assert rc == 0
    assert len([p for p in (out / "selected").iterdir() if p.suffix.lower() != ".xmp"]) == 1
    assert (out / "candidates.json").exists()
    assert len(list((out / "selected").glob("*.xmp"))) == 1
