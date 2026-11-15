"""HEIC/HEIF/AVIF input: extensions, lazy plugin registration, clear error without the plugin."""

import contextlib
import io
import os
import sys
import types

import numpy as np
from PIL import Image as PILImage
from PIL import features
import pytest

from autoedit import batch, imageio
from autoedit.cli import main
from autoedit.contract import RunConfig


def _img(w=48, h=32):
    rng = np.random.default_rng(3)
    return PILImage.fromarray((rng.random((h, w, 3)) * 200 + 20).astype(np.uint8), "RGB")


def test_extensions_are_supported():
    for name in ("a.heic", "a.HEIF", "a.avif"):
        assert imageio.is_supported(name)
        assert not imageio.is_raw(name)


def test_avif_roundtrip(tmp_path):
    if not features.check("avif"):
        print("skipped: this Pillow has no AVIF support")
        return
    src = _img()
    d = tmp_path / "in"
    d.mkdir()
    p = str(d / "a.avif")
    src.save(p, quality=95)
    img = imageio.load_image(p)
    assert (img.width, img.height) == src.size
    assert abs(img.rgb.mean() * 255 - np.asarray(src).mean()) < 3
    res = batch.run(RunConfig(backend="stub"), str(d), str(tmp_path / "out"))
    assert res["failed"] == 0
    names = os.listdir(tmp_path / "out")
    assert any(n.endswith(".jpg") for n in names)
    assert any(n.endswith(".autoedit.json") for n in names)


def _with_plugin(plugin, fn):
    old_mod = sys.modules.get("pillow_heif", "absent")
    old_ready = imageio._heif_ready
    sys.modules["pillow_heif"] = plugin
    imageio._heif_ready = False
    try:
        fn()
    finally:
        imageio._heif_ready = old_ready
        if old_mod == "absent":
            sys.modules.pop("pillow_heif", None)
        else:
            sys.modules["pillow_heif"] = old_mod


def test_heif_registers_once_and_only_when_needed(tmp_path):
    calls = []
    stub = types.ModuleType("pillow_heif")
    stub.register_heif_opener = lambda: calls.append(1)
    png = str(tmp_path / "a.png")
    _img().save(png)

    def body():
        imageio.load_image(png)
        assert calls == []
        imageio._ensure_heif()
        imageio._ensure_heif()
        assert calls == [1]

    _with_plugin(stub, body)


def test_heif_without_the_package_says_what_to_install(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    (d / "x.heic").write_bytes(b"not really a heic")
    _img().save(str(d / "b.jpg"))

    def body():
        with pytest.raises(RuntimeError) as e:
            imageio.load_image(str(d / "x.heic"))
        assert "pillow-heif" in str(e.value)
        with contextlib.redirect_stdout(io.StringIO()):
            res = batch.run(RunConfig(backend="stub"), str(d), str(tmp_path / "out"))
        assert res["processed"] == 1 and res["failed"] == 1
        assert "pillow-heif" in res["failures"][0]["error"]

    _with_plugin(None, body)


def test_real_heic_decode(tmp_path):
    try:
        import pillow_heif
    except Exception:
        print("skipped: pillow-heif is not installed")
        return
    pillow_heif.register_heif_opener()
    src = _img()
    p = str(tmp_path / "a.heic")
    src.save(p, quality=90)
    img = imageio.load_image(p)
    assert (img.width, img.height) == src.size


def test_doctor_reports_the_formats():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["doctor"])
    out = buf.getvalue()
    assert "avif:" in out and "heic/heif:" in out


def test_a_heic_photo_registers_its_reader_before_its_metadata_is_read(tmp_path):
    from autoedit import captures, consistency
    calls = []
    stub = types.ModuleType("pillow_heif")
    stub.register_heif_opener = lambda: calls.append(1)
    heic = tmp_path / "IMG_1.heic"
    heic.write_bytes(b"not a real photo")           # the open fails after the registration; both cope with that
    jpg = str(tmp_path / "a.jpg")
    _img().save(jpg)

    def body():
        assert consistency.capture_time(jpg) is None and calls == []      # other formats: no plugin
        assert consistency.capture_time(str(heic)) is None
        assert calls == [1]
        imageio._heif_ready = False
        captures.metadata(heic)
        assert calls == [1, 1]

    _with_plugin(stub, body)
