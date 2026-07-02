"""emit: same-stem output disambiguation + atomic sidecar/image writes (no silent overwrite,
no truncated partial output that resume would mistake for complete)."""

import json
import os

import numpy as np
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
