"""Camera-original TIFF containers use sensor data. A developed TIFF is already a picture."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image, TiffImagePlugin

from autoedit import imageio


def test_recognized_raw_containers_use_raw_decode_and_invalidate_replacements(tmp_path):
    camera = tmp_path / "origin.TIF"
    Image.new("RGB", (20, 10), "teal").save(camera)
    assert not imageio.is_raw(str(camera))
    for header in (
        bytes.fromhex("4d4d002a00000010bab0acbb00000000"),
        b"II*\x00\x20\x00\x00\x00IIIICwaR",
    ):
        camera.write_bytes(header)
        assert imageio.is_raw(str(camera))
        with (
            patch.dict("sys.modules", {"rawpy": None}),
            pytest.raises(RuntimeError, match="rawpy decoder"),
        ):
            imageio.load_image(str(camera))
    camera.unlink()
    Image.new("CMYK", (20, 10)).save(camera)
    assert not imageio.is_raw(str(camera))


def test_kodak_reduced_ifd_needs_sensor_metadata_and_developed_tiff_keeps_pixels(
    tmp_path,
):
    p = tmp_path / "still.tiff"
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[271] = "Kodak"
    tags[272] = "DCS Synthetic"
    tags[254] = 1
    tags[33405] = "synthetic"
    rgb = Image.new("RGB", (32, 22), "orange")
    rgb.save(
        p,
        tiffinfo=tags,
        save_all=True,
        append_images=[Image.new("RGB", (15, 12), "white")],
    )
    assert not imageio.is_raw(str(p))
    assert (
        imageio.load_image(str(p)).width,
        imageio.load_image(str(p)).height,
    ) == rgb.size
    tags[65535] = b"data"
    rgb.save(p, tiffinfo=tags)
    assert imageio.is_raw(str(p))
    p.unlink()
    a = np.arange(17 * 19, dtype=np.uint16).reshape(17, 19) * 197
    Image.fromarray(a).save(p)
    photo = imageio.load_image(str(p))
    assert photo.source_bits == 16 and (photo.width, photo.height) == (19, 17)
    assert not photo.decode_info.get("decoder")


def test_grouped_colour_report_recognizes_legacy_name_with_recorded_raw_decode(
    tmp_path,
):
    import json
    from autoedit.consistency import colour_report

    path = tmp_path / "shot.autoedit.json"
    path.write_text(
        json.dumps(
            {
                "image": "archived.tif",
                "batch_group": "Pair",
                "audit": {
                    "decode_info": {"decoder": "rawpy/libraw"},
                    "subject_colour": {"lab": [0.5, 0.1, 0.2]},
                },
                "post_edit_stats": {},
            }
        )
    )
    other = tmp_path / "other.autoedit.json"
    other.write_text(path.read_text().replace("archived.tif", "second.tif"))
    assert "Pair raw" in colour_report([str(path), str(other)], 0.0)["groups"]


def test_monochrome_raw_decoder_preserves_gray_at_full_depth_across_modes(tmp_path):
    import types
    import sys

    gray = (np.arange(42, dtype=np.uint16).reshape(6, 7) * 1300)[..., None]

    class Raw:
        num_colors = 1
        sizes = types.SimpleNamespace(width=7)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kw):
            return gray

        def extract_thumb(self):
            raise ValueError("no preview")

    module = types.SimpleNamespace(imread=lambda path: Raw())
    with patch.dict(sys.modules, {"rawpy": module}):
        for mode in ("none", "libraw_auto", "preview_ev"):
            info = {}
            rgb, bits, ev = imageio._decode_raw(
                str(tmp_path / "film.dng"),
                {"raw_mode": mode, "raw_recover": True},
                info,
            )
            assert rgb.shape == (6, 7, 3) and bits == 16
            assert np.array_equal(rgb[..., 0], rgb[..., 1]) and np.array_equal(
                rgb[..., 1], rgb[..., 2]
            )
            if mode == "none":
                assert (
                    np.allclose(rgb[..., 0], gray[..., 0] / 65535)
                    and "monochrome" in info["recover_skipped"]
                )
