"""RAW+JPEG pairing: files sharing a basename collapse into one logical photo so a paired
shoot isn't edited twice. group_images preserves listing order and names raw/pixel per group."""

import os
import tempfile

from autoedit.web.service import group_images


def _touch(d, name):
    open(os.path.join(d, name), "wb").close()


def test_raw_jpeg_pair_groups_into_one():
    with tempfile.TemporaryDirectory() as d:
        for n in ("IMG_001.DNG", "IMG_001.jpg", "IMG_002.jpg", "IMG_003.nef", "notes.txt"):
            _touch(d, n)
        groups = group_images(d)
        by_stem = {g["stem"].lower(): g for g in groups}

        assert len(groups) == 3                      # 001 (paired), 002 (jpeg), 003 (raw) — txt ignored
        pair = by_stem["img_001"]
        assert pair["raw"] == "IMG_001.DNG"          # case-insensitive RAW match
        assert pair["pixel"] == "IMG_001.jpg"
        assert len(pair["members"]) == 2

        assert by_stem["img_002"]["raw"] is None and by_stem["img_002"]["pixel"] == "IMG_002.jpg"
        assert by_stem["img_003"]["raw"] == "IMG_003.nef" and by_stem["img_003"]["pixel"] is None


def test_empty_or_missing_folder():
    assert group_images("") == []
    assert group_images("/no/such/dir") == []
