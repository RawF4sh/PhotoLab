"""M3 dataset builder: stem pairing + preservation validation (non-generative gate) + copy-only
workspace build."""

import os
import tempfile

import numpy as np
from PIL import Image

from autoedit import dataset
from autoedit.edit.ops import apply_ops
from autoedit.imageio import load_image


def _structured(seed=0):
    """An image with real structure (gradient + blocks) so edge correlation is meaningful."""
    rng = np.random.default_rng(seed)
    a = np.zeros((180, 240, 3), np.float32)
    a[:] = np.linspace(0.2, 0.7, 240)[None, :, None]
    for _ in range(8):
        y, x = rng.integers(0, 150), rng.integers(0, 210)
        a[y:y + 28, x:x + 28] = rng.uniform(0.1, 0.9, 3)
    return np.clip(a, 0, 1)


def _save(rgb, path):
    Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype("uint8"), "RGB").save(path)


def test_normalize_stem_strips_edit_suffixes():
    assert dataset.normalize_stem("IMG_1024.jpg") == dataset.normalize_stem("img_1024_edit.jpg")
    assert dataset.normalize_stem("a-b_c.NEF") == dataset.normalize_stem("a b c_edited.jpg")


def test_pair_by_stem():
    pairs, uo, ut = dataset.pair_by_stem(["/x/IMG_1.jpg", "/x/IMG_2.jpg"],
                                         ["/y/IMG_1_edit.jpg", "/y/IMG_3_final.jpg"])
    assert [(os.path.basename(o), os.path.basename(t)) for o, t in pairs] == [("IMG_1.jpg", "IMG_1_edit.jpg")]
    assert [os.path.basename(p) for p in uo] == ["IMG_2.jpg"]
    assert [os.path.basename(p) for p in ut] == ["IMG_3_final.jpg"]


def test_pair_by_stem_burst_names_never_mispair():
    """IMG_1-2.jpg is a DIFFERENT shot (camera burst / macOS duplicate), not an edit of IMG_1.
    Its target must reach its own original; exact matches always win over suffix stripping."""
    originals = ["/x/IMG_1.jpg", "/x/IMG_1-2.jpg"]
    targets = ["/y/IMG_1-2_edit.jpg", "/y/IMG_1_edit.jpg"]
    pairs, uo, ut = dataset.pair_by_stem(originals, targets)
    by_target = {os.path.basename(t): os.path.basename(o) for o, t in pairs}
    assert by_target == {"IMG_1-2_edit.jpg": "IMG_1-2.jpg", "IMG_1_edit.jpg": "IMG_1.jpg"}
    assert not uo and not ut

    # a target literally named IMG_1-2.jpg exact-matches the burst original, never IMG_1
    pairs, _, _ = dataset.pair_by_stem(originals, ["/y/IMG_1-2.jpg"])
    assert [os.path.basename(o) for o, _t in pairs] == ["IMG_1-2.jpg"]


def test_normalize_stem_requires_separator_before_copy():
    assert dataset.normalize_stem("hardcopy.jpg") == "hardcopy"     # real stem, not "hard"
    assert dataset.normalize_stem("IMG_9 copy.jpg") == dataset.normalize_stem("IMG_9.jpg")
    assert dataset.normalize_stem("IMG_9_copy.jpg") == dataset.normalize_stem("IMG_9.jpg")


def test_real_edit_passes_preservation():
    d = tempfile.mkdtemp()
    src = _structured(1)
    op, tg = os.path.join(d, "p.jpg"), os.path.join(d, "p_edit.jpg")
    _save(src, op)
    _save(apply_ops(src, {"exposure": -0.2, "contrast": 8, "vibrance": 6}), tg)   # same pixels, real edit
    v = dataset.validate_pair(op, tg)
    assert v["ok"], v["reasons"]
    assert v["metrics"]["edge_corr"] > 0.7                # structure preserved


def test_content_change_rejected():
    d = tempfile.mkdtemp()
    op, tg = os.path.join(d, "p.jpg"), os.path.join(d, "p_edit.jpg")
    _save(_structured(1), op)
    _save(_structured(99), tg)                            # a different scene → generated/content drift
    v = dataset.validate_pair(op, tg)
    assert not v["ok"]
    assert "content drift (structure changed)" in v["reasons"]


def test_aspect_crop_rejected():
    d = tempfile.mkdtemp()
    op, tg = os.path.join(d, "p.jpg"), os.path.join(d, "p_edit.jpg")
    src = _structured(2)
    _save(src, op)
    _save(src[:, :120], tg)                               # cropped → aspect changed
    v = dataset.validate_pair(op, tg)
    assert "aspect/crop changed" in v["reasons"]


def test_build_dataset_disambiguates_colliding_workspace_stems():
    """Two kept pairs whose normalized stems collide (burst: IMG_1 + IMG_1-2 both normalize to
    img1) must land as distinct workspace files — never a silent overwrite — with originals/ and
    targets/ stems still matching so recipe training pairs them back up."""
    d = tempfile.mkdtemp()
    od, td, out = (os.path.join(d, x) for x in ("orig", "tgt", "ds"))
    os.makedirs(od); os.makedirs(td)
    _save(_structured(1), os.path.join(od, "IMG_1.jpg"))
    _save(_structured(2), os.path.join(od, "IMG_1-2.jpg"))
    _save(apply_ops(_structured(1), {"exposure": -0.1}), os.path.join(td, "IMG_1_edit.jpg"))
    _save(apply_ops(_structured(2), {"exposure": -0.1}), os.path.join(td, "IMG_1-2_edit.jpg"))

    m = dataset.build_dataset(od, td, out)
    assert m["pairs"] == 2 and m["kept"] == 2
    o_files = sorted(os.listdir(os.path.join(out, "originals")))
    t_files = sorted(os.listdir(os.path.join(out, "targets")))
    assert len(o_files) == 2 and len(t_files) == 2          # no silent overwrite
    assert [os.path.splitext(f)[0] for f in o_files] == [os.path.splitext(f)[0] for f in t_files]


def test_build_dataset_is_copy_only_and_sorts_pairs():
    d = tempfile.mkdtemp()
    od, td, out = (os.path.join(d, x) for x in ("orig", "tgt", "ds"))
    os.makedirs(od); os.makedirs(td)
    src = _structured(3)
    _save(src, os.path.join(od, "shot.jpg"))
    _save(apply_ops(src, {"exposure": -0.15}), os.path.join(td, "shot_edit.jpg"))   # good
    _save(src, os.path.join(od, "bad.jpg"))
    _save(_structured(50), os.path.join(td, "bad_edit.jpg"))                          # content drift
    before = set(os.listdir(od))

    m = dataset.build_dataset(od, td, out)
    assert m["pairs"] == 2 and m["kept"] == 1 and m["rejected"] == 1
    assert os.listdir(os.path.join(out, "originals")) == ["shot.jpg"]
    assert os.listdir(os.path.join(out, "targets")) == ["shot.jpg"]                  # kept target
    assert os.listdir(os.path.join(out, "rejected")) == ["bad.jpg"]                  # failed target
    assert os.path.isfile(os.path.join(out, "manifest.json"))
    assert os.path.isfile(os.path.join(out, "labels.csv"))
    assert set(os.listdir(od)) == before                                            # copy-only
