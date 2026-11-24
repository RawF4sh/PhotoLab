"""emit: same-stem output disambiguation + atomic sidecar/image writes (no silent overwrite,
no truncated partial output that resume would mistake for complete)."""

import json
import os

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import emit
from autoedit.imageio import save_image


def test_output_paths_disambiguates_same_stem(tmp_path):
    taken: set[str] = set()
    img_a, side_a = emit.output_paths(str(tmp_path), "/cam/IMG_001.dng", "jpeg", taken)
    img_b, side_b = emit.output_paths(str(tmp_path), "/other/IMG_001.jpg", "jpeg", taken)
    assert img_a != img_b and side_a != side_b            # neither image nor sidecar collides
    assert os.path.basename(img_b) == "IMG_001-2.jpg"
    assert os.path.basename(side_b) == "IMG_001-2.autoedit.json"   # sidecar tracks the image stem


def test_output_paths_without_taken_is_legacy_stable(tmp_path):
    img, side = emit.output_paths(str(tmp_path), "/x/p.jpg", "jpeg")
    assert os.path.basename(img) == "p.jpg"
    assert os.path.basename(side) == "p.autoedit.json"


def test_write_sidecar_atomic_valid_no_partfile(tmp_path):
    class _Fake:
        def model_dump_json(self, indent=2):
            return '{"image": "x.jpg"}'

    p = str(tmp_path / "s.autoedit.json")
    emit.write_sidecar(_Fake(), p)
    assert json.load(open(p))["image"] == "x.jpg"
    assert not os.path.exists(p + ".part")               # temp cleaned up by os.replace


def test_save_image_atomic_no_partfile(tmp_path):
    p = str(tmp_path / "o.jpg")
    save_image(np.zeros((8, 8, 3), np.float32), p, "jpeg")
    assert os.path.exists(p)
    assert not os.path.exists(p + ".part.jpg")
    assert PILImage.open(p).size == (8, 8)


def _own_export(folder, source, fmt="jpeg", image_field=None):
    """Make the files an earlier single-photo export of `source` would have left."""
    image, sidecar = emit.output_paths(str(folder), source, fmt)
    open(image, "wb").write(b"export")
    json.dump({"image": image_field or os.path.basename(source)}, open(sidecar, "w"))
    return image, sidecar


def test_single_output_paths_free_names_and_own_earlier_export(tmp_path):
    img, side = emit.single_output_paths(str(tmp_path), "/cam/IMG_1.CR2", "jpeg")
    assert os.path.basename(img) == "IMG_1.jpg" and os.path.basename(side) == "IMG_1.autoedit.json"
    _own_export(tmp_path, "/cam/IMG_1.CR2")
    assert emit.single_output_paths(str(tmp_path), "/cam/IMG_1.CR2", "jpeg") == (img, side)   # replaces its own


def test_single_output_paths_never_replaces_another_photos_export(tmp_path):
    _own_export(tmp_path, "/cam/IMG_1.CR2")
    img, side = emit.single_output_paths(str(tmp_path), "/cam/IMG_1.JPG", "jpeg")
    assert os.path.basename(img) == "IMG_1-2.jpg" and os.path.basename(side) == "IMG_1-2.autoedit.json"
    (tmp_path / "IMG_1-2.jpg").write_bytes(b"export")       # the JPG's own export now sits at -2 and is replaced
    (tmp_path / "IMG_1-2.autoedit.json").write_text('{"image": "IMG_1.JPG"}')
    assert emit.single_output_paths(str(tmp_path), "/cam/IMG_1.JPG", "jpeg")[0].endswith("IMG_1-2.jpg")
    assert emit.single_output_paths(str(tmp_path), "/x/IMG_1.TIF", "jpeg")[0].endswith("IMG_1-3.jpg")


def test_single_output_paths_unreadable_sidecar_takes_the_next_name(tmp_path):
    image, sidecar = _own_export(tmp_path, "/cam/IMG_1.CR2")
    open(sidecar, "w").write("{not json")
    assert os.path.basename(emit.single_output_paths(str(tmp_path), "/cam/IMG_1.CR2", "jpeg")[0]) == "IMG_1-2.jpg"


def test_single_output_paths_refuses_a_file_autoedit_did_not_write(tmp_path):
    original = tmp_path / "IMG_1.jpg"
    original.write_bytes(b"the original")
    with pytest.raises(ValueError, match="did not write.*another output folder"):
        emit.single_output_paths(str(tmp_path), str(tmp_path / "IMG_1.png"), "jpeg")
    assert original.read_bytes() == b"the original"
