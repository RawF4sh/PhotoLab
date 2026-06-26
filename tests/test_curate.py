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


def test_cli_curate_command(tmp_path):
    src = tmp_path / "library"
    src.mkdir()
    _write_rgb(src / "one.jpg", (30, 60, 190))
    _write_rgb(src / "two.jpg", (220, 180, 90))
    out = tmp_path / "curated"

    rc = main(["curate", str(src), str(out), "--count", "1"])

    assert rc == 0
    assert len(list((out / "selected").iterdir())) == 1
    assert (out / "candidates.json").exists()
