"""The two editor entry points share the source and keep RAW working pixels private and precise."""
import hashlib
import io
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

from autoedit import imageio
from autoedit.web import editor_files, projects, service


def test_editor_import_preserves_raw_depth_settings_identity_and_direct_formats(tmp_path):
    pytest.importorskip("tifffile")
    import tifffile
    path = tmp_path / "Camera.CR2"
    path.write_bytes(b"camera source")
    service.approve_root(str(tmp_path))
    ramp = np.linspace(0, 1, 1024, dtype=np.float32)
    pixels = np.tile(ramp[None, :, None], (4, 1, 3))
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}), patch.object(
            imageio, "load_image", return_value=imageio.Image(pixels, source_bits=16)) as load:
        opts = {"raw_mode": "preview_ev"}
        first = editor_files.import_photo(str(path), opts)
        again = editor_files.import_photo(str(path), opts)
        assert first == again and load.call_count == 1
        assert first["path"] == str(path) and first["working_copy"]
        with editor_files.open_picture(first["id"])[0] as stream:
            with tifffile.TiffFile(stream) as tif:
                assert tif.pages[0].dtype == np.uint16 and 34675 in tif.pages[0].tags
                assert np.unique(tif.asarray()).size > 256
        assert editor_files.import_photo(str(path), {"raw_mode": "linear_srgb"})["id"] != first["id"]
        path.write_bytes(b"replacement")
        assert editor_files.import_photo(str(path), opts)["id"] != first["id"]
        for suffix in ("JpG", "JPEG", "PNG", "TIFF", "WebP"):
            direct = tmp_path / ("photo." + suffix)
            direct.write_bytes(b"untouched")
            assert not editor_files.import_photo(str(direct), {})["working_copy"]
    assert path.read_bytes() == b"replacement"


def test_editor_cache_evicts_oldest_managed_copy_and_never_export(tmp_path):
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}), patch.object(editor_files, "CACHE_BYTES", 6):
        root = editor_files.root()
        old, keep, export = root / ("a" * 64 + ".tif"), root / ("b" * 64 + ".tif"), tmp_path / "print.tif"
        old.write_bytes(b"old")
        keep.write_bytes(b"new!")
        export.write_bytes(b"original print")
        editor_files.prune({keep})
        assert not old.exists() and keep.exists() and export.read_bytes() == b"original print"
        with pytest.raises(ValueError):
            editor_files.open_picture("../print")


def test_editor_drop_uses_local_copy_and_rejects_empty_or_bad_name(tmp_path):
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return
    from autoedit.web import app
    data = io.BytesIO()
    Image.new("RGB", (16, 10)).save(data, format="JPEG")
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}):
        client = TestClient(app.create_app(profile=str(tmp_path)), base_url="http://localhost")
        def upload(name, blob):
            return client.post("/api/v1/editor/drop", params={"name": name}, content=blob)
        assert upload("../outside.jpg", data.getvalue()).status_code == 400
        assert upload("empty.jpg", b"").status_code == 400
        photo = upload("My photo.JPG", data.getvalue())
        assert photo.status_code == 200 and photo.json()["dropped"]
        path = Path(photo.json()["path"])
        assert path.parent.name == hashlib.sha256(data.getvalue()).hexdigest()
        assert client.get(photo.json()["url"]).content == data.getvalue()
        original = tmp_path / "original.JPG"
        original.write_bytes(data.getvalue())
        service.approve_root(str(tmp_path))
        location = "/api/v1/editor/import"
        assert client.post(location, json={"path": str(original), "expected_drop": "bad"}).status_code == 400
        assert client.post(location, json={"path": str(original), "expected_drop": photo.json()["sha256"]}).status_code == 200
        with patch.object(Path, "home", return_value=tmp_path):
            dest = Path(editor_files.default_export_path(str(path)))
            assert dest.is_relative_to(tmp_path / "Pictures" / "AutoEdit Exports") and dest.is_dir()
        view = client.post("/api/v1/editor/source-preview", json={"path": str(original), "options": {}})
        assert view.status_code == 200 and view.json()["before"].startswith("data:image/webp;base64,")



def test_editor_stale_request_never_writes_working_picture(tmp_path):
    path = tmp_path / "one.png"
    Image.new("RGB", (32, 32)).save(path)
    service.approve_root(str(tmp_path))
    session = "editor-stale-" + tmp_path.name
    service._preview_request({"preview_session": session, "preview_revision": 2}, register=True)
    with pytest.raises(ValueError, match="superseded"):
        editor_files.working_picture(str(path), {"preview_session": session, "preview_revision": 1})


def test_handoff_keeps_rotated_crop_in_source_coordinates():
    import math
    from autoedit import geometry
    width, height = 601, 400
    g, _ = geometry.manual({"angle": 9, "crop": [.18, .12, .68, .78]}, (height, width))
    frame = editor_files.native_crop(g, (height, width, 3))
    rect, angle = frame["rect"], math.radians(-frame["angle"])
    out_h, out_w = geometry.size(g, (height, width))
    assert (round((rect["y1"]-rect["y0"])*height), round((rect["x1"]-rect["x0"])*width)) == (out_h, out_w)
    # LightCraft's inverse rotation must land back on the source centre chosen in Guided.
    x = ((rect["x0"] + rect["x1"]) / 2 - .5) * width
    y = ((rect["y0"] + rect["y1"]) / 2 - .5) * height
    assert math.isclose(width/2 + math.cos(angle)*x - math.sin(angle)*y, g.cx*width)
    assert math.isclose(height/2 + math.sin(angle)*x + math.cos(angle)*y, g.cy*height)


def test_camera_tiff_uses_raw_development_in_the_picker_and_native_editor(tmp_path):
    pytest.importorskip("tifffile")
    import tifffile
    camera = tmp_path / 'capture.tif'
    jpeg = tmp_path / 'capture.jpg'
    print_tiff = tmp_path / 'print.tif'
    for file in (camera, jpeg, print_tiff):
        Image.new('RGB', (8, 6)).save(file)
    service.approve_root(str(tmp_path))
    # Recognition is tested against real tags in the decoder suite. Here it succeeds
    # only when the picker supplies the actual camera path, as a content probe requires.
    def raw(path):
        return Path(path) == camera
    sensor = imageio.Image(np.full((48, 64, 3), .21, np.float32), source_bits=16)
    with patch.dict('os.environ', {'XDG_CACHE_HOME': str(tmp_path / 'cache')}), \
            patch.object(service, 'is_raw', raw), patch.object(imageio, 'is_raw', raw), \
            patch.object(imageio, 'load_image', return_value=sensor) as decode:
        groups = {g['stem']: g for g in service.group_images(str(tmp_path))}
        assert groups['capture']['raw'] == camera.name and groups['capture']['pixel'] == jpeg.name
        assert groups['print']['raw'] is None
        assert not editor_files.import_photo(str(print_tiff), {})['working_copy']
        opened = editor_files.import_photo(str(camera), {'raw_mode': 'preview_ev'}, full_size=True)
        assert opened['working_copy'] and decode.call_count == 1
        with editor_files.open_picture(opened['id'])[0] as stream, tifffile.TiffFile(stream) as tif:
            assert tif.pages[0].shape == (48, 64, 3) and tif.pages[0].dtype == np.uint16
        with patch.object(service, '_preview_images', return_value=(sensor, sensor)):
            assert editor_files.source_preview(str(camera), {})['working_copy']
            assert not editor_files.source_preview(str(print_tiff), {})['working_copy']


def test_a_photos_saved_hide_reaches_every_picture_made_for_the_editor(tmp_path):
    pytest.importorskip("tifffile")
    path = tmp_path / "input" / "frame.jpg"
    path.parent.mkdir()
    Image.new("RGB", (60, 40), "#918365").save(path)
    service.approve_root(str(tmp_path))
    profile = str(tmp_path / "profile")
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}):
        assert editor_files.import_photo(str(path), {}, None, profile)["url"].startswith("/api/v1/editor/original")
        projects.save(str(path.parent), {"version": 1, "photos": {str(path): {"hide": ["face"], "hideMethod": "blur"}}}, profile)
        seen = []
        real = service._edit
        with patch.object(service, "_edit", lambda p, opts, *a, **k: (seen.append(opts), real(p, opts, *a, **k))[1]):
            picture = editor_files.import_photo(str(path), {}, None, profile)
            editor_files.import_photo(str(path), {"hide": "people, face"}, None, profile)
            editor_files.working_picture(str(path), {"editor_original": True}, None, profile)
        assert picture["url"].startswith("/api/v1/editor/pictures/")
        assert [o["hide"] for o in seen] == [["face"], ["people", "face"], ["face"]]
        assert {o["hide_method"] for o in seen} == {"blur"}
