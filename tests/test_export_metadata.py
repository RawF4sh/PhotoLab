"""F1: exports keep the source's camera data and an embedded sRGB profile (synthetic fixtures)."""

import io
import json
import os
from types import SimpleNamespace

import numpy as np
from PIL import Image as PILImage
from PIL.TiffImagePlugin import IFDRational

from autoedit import batch, emit
from autoedit.cli import _flatten, _read_toml, load_run_config
from autoedit.contract import RunConfig
from autoedit.imageio import export_exif, load_image, save_image, srgb_profile
from autoedit.web import service

_EXIF, _GPS = 0x8769, 0x8825


def _fixture(path, size=(64, 48), orientation=6, gps=True, extra_exif=None):
    a = np.zeros((size[1], size[0], 3), np.uint8)
    a[: size[1] // 2] = [100, 140, 200]
    a[size[1] // 2:] = [90, 110, 80]
    ex = PILImage.Exif()
    ex[0x010F], ex[0x0110] = "TestMake", "TestModel"
    ex[0x0112] = orientation
    ex[0x0132] = "2020:01:02 03:04:05"
    ex[0x013B], ex[0x8298] = "An Artist", "(c) Someone"
    ex[0x02BC] = b"<xmp/>"
    ex[_EXIF] = {0x9003: "2020:01:01 10:00:00", 0x8827: 400, 0x829D: IFDRational(28, 10),
                 0x9204: IFDRational(-1, 3), 0x927C: b"M" * 200, 0xA002: 1, 0xA003: 1,
                 **(extra_exif or {})}
    if gps:
        ex[_GPS] = {1: "N", 2: (IFDRational(46), IFDRational(0), IFDRational(0)),
                    3: "E", 4: (IFDRational(11), IFDRational(0), IFDRational(0))}
    PILImage.fromarray(a, "RGB").save(str(path), exif=ex.tobytes())
    return str(path)


def _exif_of(path):
    pil = PILImage.open(path)
    e = pil.getexif()
    return e, e.get_ifd(_EXIF), e.get_ifd(_GPS)


def test_keep_copies_camera_data_and_gps(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    out = tmp_path / "o.jpg"
    out.write_bytes(b"")
    data = export_exif(src, "keep", (30, 20), software="AutoEdit X")
    save_image(np.full((20, 30, 3), 0.5, np.float32), str(out), exif=data)
    e, sub, gps = _exif_of(str(out))
    assert e[0x010F] == "TestMake" and e[0x0110] == "TestModel"
    assert sub[0x9003] == "2020:01:01 10:00:00" and sub[0x8827] == 400
    assert float(sub[0x829D]) == 2.8 and abs(float(sub[0x9204]) + 1 / 3) < 1e-3
    assert 2 in gps
    assert e[0x0112] == 1
    assert (sub[0xA002], sub[0xA003]) == (30, 20)
    assert sub[0xA001] == 1
    assert e[0x0131] == "AutoEdit X"
    assert 0x927C not in sub
    assert 0x02BC not in e


def test_no_location_drops_only_gps(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    out = tmp_path / "o.jpg"
    save_image(np.full((20, 30, 3), 0.5, np.float32), str(out),
               exif=export_exif(src, "no_location", (30, 20)))
    e, sub, gps = _exif_of(str(out))
    assert not gps
    assert sub[0x9003] == "2020:01:01 10:00:00" and e[0x0110] == "TestModel"


def test_none_and_missing_sources_write_nothing(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    plain = tmp_path / "p.jpg"
    PILImage.new("RGB", (8, 8)).save(str(plain))
    txt = tmp_path / "t.txt"
    txt.write_text("not an image")
    assert export_exif(src, "none", (8, 8)) is None
    assert export_exif(None, "keep", (8, 8)) is None
    assert export_exif(str(plain), "keep", (8, 8)) is None
    assert export_exif(str(tmp_path / "missing.jpg"), "keep", (8, 8)) is None
    assert export_exif(str(txt), "keep", (8, 8)) is None


def test_unknown_mode_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        export_exif(None, "bogus", (8, 8))


def test_oversized_exif_is_dropped(tmp_path):
    small = _fixture(tmp_path / "small.tif")
    assert export_exif(small, "keep", (8, 8)) is not None      # control: a TIFF source is readable
    src = _fixture(tmp_path / "s.tif", extra_exif={0x9286: b"x" * 70_000})
    assert export_exif(src, "keep", (8, 8)) is None


def test_save_image_default_writes_no_metadata(tmp_path):
    p = tmp_path / "d.jpg"
    save_image(np.full((16, 16, 3), 0.4, np.float32), str(p))
    pil = PILImage.open(str(p))
    assert not pil.info.get("icc_profile")
    assert len(pil.getexif()) == 0


def test_metadata_does_not_change_pixels(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    rgb = np.random.default_rng(0).random((24, 32, 3)).astype(np.float32)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    save_image(rgb, str(a))
    prof = srgb_profile()
    save_image(rgb, str(b), exif=export_exif(src, "keep", (32, 24)), icc_profile=prof)
    assert np.array_equal(np.asarray(PILImage.open(str(a))), np.asarray(PILImage.open(str(b))))
    pb = PILImage.open(str(b))
    assert pb.info.get("icc_profile") == prof
    assert pb.getexif().get_ifd(_EXIF)[0x9003] == "2020:01:01 10:00:00"


def test_srgb_profile_is_stable():
    p = srgb_profile()
    if p is None:
        return
    assert p == srgb_profile()
    import struct
    assert p[24:36] == struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
    from PIL import ImageCms
    ImageCms.ImageCmsProfile(io.BytesIO(p))


def test_tagged_export_reloads_identically(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    rgb = np.random.default_rng(1).random((24, 32, 3)).astype(np.float32)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    save_image(rgb, str(a))
    save_image(rgb, str(b), exif=export_exif(src, "keep", (32, 24)), icc_profile=srgb_profile())
    assert np.array_equal(load_image(str(a)).rgb, load_image(str(b)).rgb)


def test_tiff_export_keeps_basic_tags(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    p = tmp_path / "o.tif"
    prof = srgb_profile()
    save_image(np.full((20, 30, 3), 0.5, np.float32), str(p), "tiff",
               exif=export_exif(src, "keep", (30, 20), software="AutoEdit X"), icc_profile=prof)
    pil = PILImage.open(str(p))
    e = pil.getexif()
    assert e[0x010F] == "TestMake" and e[0x0110] == "TestModel"
    assert "2020:01:01 10:00:00" in (e.get(0x0132), e.get_ifd(_EXIF).get(0x9003))
    if prof:
        assert pil.info.get("icc_profile")


def test_write_image_product_export(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    rgb = np.full((20, 30, 3), 0.5, np.float32)
    p = tmp_path / "o.jpg"
    note = emit.write_image(rgb, str(p), "jpeg", 90, source=src, metadata="no_location")
    assert "no_location" in note
    pil = PILImage.open(str(p))
    e, sub, gps = _exif_of(str(p))
    assert pil.info.get("icc_profile") == srgb_profile()
    assert sub[0x9003] == "2020:01:01 10:00:00" and not gps
    assert e[0x0131].startswith("AutoEdit ")
    q = tmp_path / "q.jpg"
    assert emit.write_image(rgb, str(q), "jpeg", 90) == ""
    pq = PILImage.open(str(q))
    assert not pq.info.get("icc_profile") and len(pq.getexif()) == 0
    n = tmp_path / "n.jpg"
    assert emit.write_image(rgb, str(n), "jpeg", 90, source=src, metadata="none") == ""
    pn = PILImage.open(str(n))
    assert pn.info.get("icc_profile") == srgb_profile() and len(pn.getexif()) == 0
    tiff_note = emit.write_image(rgb, str(tmp_path / "t.tif"), "tiff", 90, source=src, metadata="keep")
    assert tiff_note.startswith("export: EXIF kept") and "16-bit TIFF" in tiff_note


def test_batch_run_carries_metadata(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _fixture(d / "a.jpg", size=(64, 48), orientation=6)
    out = tmp_path / "out"
    cfg = RunConfig(backend="stub")
    batch.run(cfg, str(d), str(out))
    o = str(out / "a.jpg")
    pil = PILImage.open(o)
    assert pil.size == (48, 64)
    e, sub, gps = _exif_of(o)
    assert e[0x0112] == 1
    assert (sub[0xA002], sub[0xA003]) == pil.size
    assert sub[0x9003] == "2020:01:01 10:00:00" and not gps
    side = json.load(open(out / "a.autoedit.json"))
    assert any(n.startswith("export: EXIF kept") for n in side["notes"])


def test_metadata_setting_flows():
    assert RunConfig().metadata == "no_location"
    from autoedit import paths
    flat = _flatten(_read_toml(os.path.join(paths.configs_dir(), "defaults.toml")))
    assert flat["metadata"] == "no_location"
    base = dict(configs_dir=None, config=None, metadata="none")
    assert load_run_config(SimpleNamespace(**base)).metadata == "none"
    assert service.make_cfg({"metadata": "keep"}).metadata == "keep"
