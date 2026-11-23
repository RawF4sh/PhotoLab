"""F22: hide on request. The content rule: nothing is generated; a region is covered, made into
blocks or blurred, only when asked for, and always reported."""

import contextlib
import json
import os
import struct
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import batch, hide, paths, pipeline as pipeline_mod, reveal
from autoedit.cli import load_run_config
from autoedit.contract import RunConfig
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask

_CONF = reveal.Hide()      # margin 0.1 (at least 3 px), cover ring 0.25, 5 blocks, blur 0.2


def _noise(h=100, w=140, seed=1):
    return np.random.default_rng(seed).random((h, w, 3)).astype(np.float32)


def _rect(h, w, y0, y1, x0, x1):
    m = np.zeros((h, w), bool)
    m[y0:y1, x0:x1] = True
    return m


def _px(box, h, w):
    return round(box[1] * h), round(box[3] * h), round(box[0] * w), round(box[2] * w)


# ---- the pixels --------------------------------------------------------------------------------

def test_cover_gives_identical_pixels_for_two_different_pictures():
    """At the hide step nothing under the grown mask reaches the fill: two pictures that differ only there come out the same."""
    a = _noise(seed=1)
    m = _rect(100, 140, 40, 60, 50, 90)
    out_a, (box,) = hide.apply(a, [m], _CONF, "cover")
    y0, y1, x0, x1 = _px(box, 100, 140)
    b = a.copy()
    b[y0:y1, x0:x1] = _noise(100, 140, seed=2)[y0:y1, x0:x1]          # a different picture under the grown mask
    assert not np.array_equal(a, b)
    out_b, (box_b,) = hide.apply(b, [m], _CONF, "cover")
    assert box_b == box
    assert np.array_equal(out_a, out_b)
    fill = out_a[y0:y1, x0:x1]
    assert (fill == fill[0, 0]).all() and not np.array_equal(fill, a[y0:y1, x0:x1])    # one flat colour
    assert abs(float(fill[0, 0, 0]) - 0.5) < 0.25                                       # a mean of noise, not a copy of a pixel


def test_cover_ignores_the_inside_of_a_second_hidden_region():
    """The surroundings of one region never include another hidden region's inside."""
    a = _noise(seed=3)
    m1, m2 = _rect(100, 140, 40, 60, 20, 50), _rect(100, 140, 40, 60, 54, 90)     # a gap of 4 px: the ring of one reaches the other
    out, boxes = hide.apply(a, [m1, m2], _CONF, "cover")
    b = a.copy()
    y0, y1, x0, x1 = _px(boxes[1], 100, 140)
    b[y0:y1, x0:x1] = 0.0
    out_b, _ = hide.apply(b, [m1, m2], _CONF, "cover")
    assert np.array_equal(out, out_b)


def test_nothing_outside_the_grown_mask_changes_and_the_source_array_is_kept():
    a = _noise(seed=4)
    before = a.copy()
    yy, xx = np.mgrid[:100, :140]
    disc = (yy - 50) ** 2 + (xx - 70) ** 2 < 18 ** 2
    for method in hide.METHODS:
        out, (box,) = hide.apply(a, [disc], _CONF, method)
        y0, y1, x0, x1 = _px(box, 100, 140)
        keep = np.ones((100, 140), bool)
        keep[y0:y1, x0:x1] = False
        assert np.array_equal(out[keep], a[keep]), method                       # byte-identical outside the grown area
        assert (out[disc] != a[disc]).any(), method                             # and the region itself is gone
        assert np.array_equal(a, before)                                        # the input is never written into
        assert y0 < 32 and y1 > 68 and x0 < 52 and x1 > 88                      # grown past the region by a margin
    same, boxes = hide.apply(a, [], _CONF, "cover")
    assert same is a and boxes == []


def test_a_mask_at_the_analysis_size_hides_the_same_area_of_the_full_picture():
    full = _noise(200, 280, seed=5)
    m = _rect(100, 140, 40, 60, 50, 90)                                          # half the size
    out, (box,) = hide.apply(full, [m], _CONF, "cover")
    y0, y1, x0, x1 = _px(box, 200, 280)
    assert y0 <= 80 - 6 and y1 >= 120 + 6 and x0 <= 100 - 6 and x1 >= 180 + 6    # margin of at least 3 analysis pixels
    assert (out[80:120, 100:180] == out[80, 100]).all()


def test_mosaic_and_blur_scale_with_the_region_and_keep_a_trace():
    assert hide.TRACE == {"cover": False, "mosaic": True, "blur": True}
    for size in (20, 60):
        a = _noise(200, 240, seed=6)
        m = _rect(200, 240, 80, 80 + size, 80, 80 + size)
        out, (box,) = hide.apply(a, [m], _CONF, "mosaic")
        y0, y1, x0, x1 = _px(box, 200, 240)
        row = out[(y0 + y1) // 2, x0:x1, 0]
        runs = 1 + int((np.diff(row) != 0).sum())
        assert 4 <= runs <= 8, (size, runs)                                      # about 5 blocks across, whatever the size
        soft, _ = hide.apply(a, [m], _CONF, "blur")
        inner = soft[80:80 + size, 80:80 + size]
        assert inner.std() < 0.5 * a[80:80 + size, 80:80 + size].std()           # noise is smoothed, more for a bigger region
    b = _noise(200, 240, seed=7)
    for method in ("mosaic", "blur"):                                            # a trace: the inside still shows through
        o1, _ = hide.apply(a, [m], _CONF, method)
        o2, _ = hide.apply(np.where(_rect(200, 240, 80, 140, 80, 140)[..., None], b, a), [m], _CONF, method)
        assert not np.array_equal(o1, o2)


def test_a_region_that_is_the_whole_frame_gets_grey():
    a = _noise(40, 60, seed=8)
    out, _ = hide.apply(a, [np.ones((40, 60), bool)], _CONF, "cover")
    assert (out == 0.5).all()


# ---- the config --------------------------------------------------------------------------------

def test_hide_config_loads_and_is_checked(tmp_path):
    cfg = reveal.load(os.path.join(paths._packaged(), "reveal.toml"))
    assert cfg.hide == reveal.Hide("cover", 0.1, 3.0, 0.25, 5, 0.2)
    ok = tmp_path / "ok.toml"
    ok.write_text('[hide]\nmethod = "blur"\n[hide.blur]\nsigma = 0.4\n[kind.a]\nlabel = "a"\nwords = ["x"]\n')
    assert reveal.load(str(ok)).hide.method == "blur" and reveal.load(str(ok)).hide.sigma == 0.4
    for text, part in [('[hide]\nmethod = "paint"\n', "hide.method"), ('[hide]\nmargin = 2\n', "hide.margin"),
                       ('[hide]\nnope = 1\n', "hide.nope"), ('[hide.blur]\nsigma = 0\n', "hide.blur.sigma"),
                       ('[hide.cover]\nblocks = 3\n', "hide.cover.blocks"), ('[hide.paint]\nx = 1\n', "hide.paint")]:
        bad = tmp_path / "bad.toml"
        bad.write_text(text + '[kind.a]\nlabel = "a"\nwords = ["x"]\n')
        with pytest.raises(ValueError) as e:
            reveal.load(str(bad))
        assert part in str(e.value), (part, str(e.value))


def test_run_config_hide_setting():
    assert RunConfig().hide == [] and RunConfig().hide_method is None
    assert RunConfig(hide="plate, face,plate").hide == ["plate", "face"]
    assert RunConfig(hide=["plate"], hide_method="mosaic").hide_method == "mosaic"
    for bad in ("Plate", ["a b"], ["../x"], 3):
        with pytest.raises(ValueError):
            RunConfig(hide=bad)
    with pytest.raises(ValueError):
        RunConfig(hide_method="paint")
    args = SimpleNamespace(configs_dir=None, config=None, hide="plate,screen", hide_method="blur")
    cfg = load_run_config(args)
    assert cfg.hide == ["plate", "screen"] and cfg.hide_method == "blur"
    assert load_run_config(SimpleNamespace(configs_dir=None, config=None)).hide == []


def test_an_unknown_kind_stops_the_run():
    with pytest.raises(ValueError) as e:
        Pipeline.build(RunConfig(backend="stub", hide=["plates"]))
    assert "plates" in str(e.value) and "plate" in str(e.value)
    pipe = Pipeline.build(RunConfig(backend="stub", hide=["plate"]))
    assert pipe.reveal is not None                                              # asking to hide turns the check on
    assert Pipeline.build(RunConfig(backend="stub")).reveal is None


# ---- in a run ----------------------------------------------------------------------------------

_PLATE = "license plate"


class _Fake:
    """Open-vocabulary segmenter: finds a plate in a bright photo, nothing else."""
    name = "fake"
    open_vocabulary = True

    def segment(self, img, concepts):
        if _PLATE in concepts and img.rgb.mean() > 0.5:
            h, w = img.rgb.shape[:2]
            m = np.zeros((h, w), bool)
            m[h // 4:h // 2, w // 4:w // 2] = True
            return [RegionMask(name=_PLATE, mask=m, score=0.9, coverage=float(m.mean()))]
        return []


@contextlib.contextmanager
def _faked():
    old = pipeline_mod.make_segmenter
    pipeline_mod.make_segmenter = lambda *a, **k: _Fake()
    try:
        yield
    finally:
        pipeline_mod.make_segmenter = old


def _exif_with_thumbnail() -> bytes:
    """An Exif block whose second directory carries a small JPEG: the camera's own preview."""
    import io
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 6), (200, 50, 50)).save(buf, "JPEG")
    thumb = buf.getvalue()
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x0112, 3, 1, 1) + struct.pack("<I", 26)
    ifd1 = (struct.pack("<H", 2) + struct.pack("<HHII", 0x0201, 4, 1, 56) + struct.pack("<HHII", 0x0202, 4, 1, len(thumb))
            + struct.pack("<I", 0))
    return b"Exif\0\0" + b"II*\0" + struct.pack("<I", 8) + ifd0 + ifd1 + thumb


def _photos(folder, with_thumbnail=False):
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(3)
    for i, base in enumerate((0.25, 0.8, 0.25)):                                  # p1 is bright: the fake finds a plate there
        a = np.clip(base + rng.normal(0, 0.03, (96, 128, 3)), 0, 1)
        path = os.path.join(folder, f"p{i}.jpg")
        PILImage.fromarray((a * 255).astype(np.uint8), "RGB").save(path)
        if with_thumbnail:
            data = open(path, "rb").read()
            seg = b"\xff\xe1" + struct.pack(">H", 2 + len(_exif_with_thumbnail())) + _exif_with_thumbnail()
            open(path, "wb").write(data[:2] + seg + data[2:])
    return str(folder)


def _run(src, out, **kw):
    kw.setdefault("out_format", "png")             # lossless: a pixel outside the hidden area can be compared exactly
    return batch.run(RunConfig(backend="stub", workers=1, **kw), str(src), str(out), consistent=False)


def _png(path):
    return np.asarray(PILImage.open(path).convert("RGB"))


def test_a_run_hides_the_plate_and_reports_it(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        m = _run(src, tmp_path / "hid", hide=["plate"])
        _run(src, tmp_path / "plain")
    doc = json.load(open(tmp_path / "hid" / "p1.autoedit.json"))
    rep = doc["reveal"]
    assert [f["kind"] for f in rep["findings"]] == ["plate"]                       # the check ran because hide asked for it
    (h,) = rep["hidden"]
    assert h["kind"] == "plate" and h["method"] == "cover" and h["trace"] is False and h["label"] == "number plate"
    assert h["box"][0] < 0.25 and h["box"][2] > 0.5 and h["box"][1] < 0.25 and h["box"][3] > 0.5     # the finding, grown by a margin
    assert any(n.startswith("hidden on request: 1 number plate, by cover") and "nothing under it is copied" in n for n in doc["notes"])
    assert doc["audit"]["run_config"]["hide"] == ["plate"]
    hid, plain = _png(tmp_path / "hid" / "p1.png"), _png(tmp_path / "plain" / "p1.png")
    y0, y1, x0, x1 = _px(h["box"], 96, 128)
    keep = np.ones(hid.shape[:2], bool)
    keep[y0:y1, x0:x1] = False
    assert np.array_equal(hid[keep], plain[keep])                                  # outside the grown mask: byte-identical
    inside = hid[y0:y1, x0:x1]
    assert int(inside.max()) - int(inside.min()) <= 8 and not np.array_equal(inside, plain[y0:y1, x0:x1])   # flat, but for the export's dither
    assert m["reveal"]["hidden"] == {"regions": 1, "photos": 1, "by_kind": {"plate": 1}}
    assert "Hidden on request: 1 regions on 1 photos (plate 1)" in batch.reveal_line(m["reveal"])


def test_default_output_is_byte_identical_and_a_photo_without_a_finding_is_untouched(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        _run(src, tmp_path / "off", out_format="jpeg")
        _run(src, tmp_path / "check", out_format="jpeg", reveal_check=True)
        _run(src, tmp_path / "hide", out_format="jpeg", hide=["plate"])
        _run(src, tmp_path / "face", out_format="jpeg", hide=["face"])           # a kind that finds nothing here
    for n in ("p0.jpg", "p1.jpg", "p2.jpg"):
        off = (tmp_path / "off" / n).read_bytes()
        assert off == (tmp_path / "check" / n).read_bytes() == (tmp_path / "face" / n).read_bytes()
    assert (tmp_path / "hide" / "p0.jpg").read_bytes() == (tmp_path / "off" / "p0.jpg").read_bytes()
    assert (tmp_path / "hide" / "p1.jpg").read_bytes() != (tmp_path / "off" / "p1.jpg").read_bytes()
    doc = json.load(open(tmp_path / "face" / "p1.autoedit.json"))
    assert doc["reveal"]["hidden"] == [] and "none was found, nothing was hidden" in doc["reveal"]["hide_note"]
    assert any("none was found" in n for n in doc["notes"])
    assert json.load(open(tmp_path / "off" / "p1.autoedit.json"))["reveal"] is None      # nothing asked: no trace of F22


def test_the_stub_cannot_find_so_nothing_is_hidden_and_it_says_so(tmp_path):
    src = _photos(tmp_path / "in")
    m = _run(src, tmp_path / "out", hide=["plate"], out_format="jpeg")
    doc = json.load(open(tmp_path / "out" / "p1.autoedit.json"))
    assert doc["reveal"]["hidden"] == [] and "did not run" in doc["reveal"]["hide_note"]
    assert any("nothing was hidden" in n for n in doc["notes"])
    assert "Hide asked for plate: nothing was hidden" in batch.reveal_line(m["reveal"])


def test_a_resume_says_which_photos_were_made_without_the_hide(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        _run(src, tmp_path / "out")
        m = batch.run(RunConfig(backend="stub", workers=1, hide=["plate"], out_format="png"), src, str(tmp_path / "out"),
                      resume=True, consistent=False)
    assert m["reveal"]["not_hidden"] == 3 and "run again without --resume" in batch.reveal_line(m["reveal"])


def test_the_method_comes_from_the_setting_or_the_file(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        _run(src, tmp_path / "a", hide=["plate"], hide_method="blur")
        prof = tmp_path / "profile"
        prof.mkdir()
        (prof / "reveal.toml").write_text('[hide]\nmethod = "mosaic"\n')
        cfg_paths = paths.config_paths(paths._packaged(), str(prof))
        _run(src, tmp_path / "b", hide=["plate"], reveal_path=cfg_paths["reveal_path"])
    a = json.load(open(tmp_path / "a" / "p1.autoedit.json"))["reveal"]["hidden"][0]
    b = json.load(open(tmp_path / "b" / "p1.autoedit.json"))["reveal"]["hidden"][0]
    assert (a["method"], a["trace"], b["method"], b["trace"]) == ("blur", True, "mosaic", True)


def test_the_export_holds_no_embedded_preview(tmp_path):
    """A source with the camera's preview inside its Exif: the export carries no second copy of the picture."""
    with _faked():
        src = _photos(tmp_path / "in", with_thumbnail=True)
        source = open(os.path.join(src, "p1.jpg"), "rb").read()
        assert source.count(b"\xff\xd8") == 2                                      # the test's premise: a preview is inside
        for meta in ("keep", "shareable"):
            _run(src, tmp_path / meta, hide=["plate"], out_format="jpeg", metadata=meta)
            data = (tmp_path / meta / "p1.jpg").read_bytes()
            assert data.count(b"\xff\xd8") == 1, meta                              # one picture, no preview before the hiding
            exif = PILImage.open(tmp_path / meta / "p1.jpg").getexif()
            assert not exif.get_ifd(1) and 0x0201 not in exif and 0x0202 not in exif
    assert (tmp_path / "keep" / "p1.autoedit.json").exists()


def test_preview_shows_the_hidden_picture_and_the_report(tmp_path):
    from unittest.mock import patch

    from autoedit.web import service
    src = _photos(tmp_path / "in")
    path = os.path.join(src, "p1.jpg")

    def build(cfg):
        pipe = Pipeline.build(cfg)
        pipe.segmenter = _Fake()
        return pipe
    with patch.object(service, "build_pipeline", build):
        plain = service.preview(path, {"backend": "stub", "reveal_check": True})
        hidden = service.preview(path, {"backend": "stub", "hide": "plate", "hide_method": "mosaic"})
    assert plain["reveal"]["hidden"] == [] and plain["after"] != hidden["after"]
    assert plain["before"] == hidden["before"]                                     # the original is shown as it is
    (h,) = hidden["reveal"]["hidden"]
    assert h["kind"] == "plate" and h["method"] == "mosaic" and h["trace"] is True


# ---- review fixes ------------------------------------------------------------------------------

def test_a_thin_finding_of_a_kind_to_hide_is_hidden():
    """min_side drops a sliver from the report; a kind the user asked to hide keeps it (a plate 8 x 40 px on the 1536 proxy)."""
    cfg = reveal.load(os.path.join(paths._packaged(), "reveal.toml"))
    m = np.zeros((1024, 1536), bool)
    m[500:508, 700:740] = True
    raw = [RegionMask(name=_PLATE, mask=m, score=0.9)]
    assert reveal.findings(raw, cfg, "other")[0] == []                               # too thin for the report
    (f, mask), = reveal.hide_candidates(raw, cfg, "other", ["plate"])
    assert f.kind == "plate" and mask.sum() == 8 * 40
    assert reveal.hide_candidates(raw, cfg, "other", ["face"]) == []
    out, (box,) = hide.apply(np.full((2048, 3072, 3), 0.7, np.float32), [mask], cfg.hide, "cover")
    assert box[0] < 700 / 1536 and box[2] > 740 / 1536


def test_a_resume_with_another_method_is_reported(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        _run(src, tmp_path / "out", hide=["plate"], hide_method="blur")
        same = batch.run(RunConfig(backend="stub", workers=1, hide=["plate"], hide_method="blur", out_format="png"), src,
                         str(tmp_path / "out"), resume=True, consistent=False)
        other = batch.run(RunConfig(backend="stub", workers=1, hide=["plate"], out_format="png"), src,
                          str(tmp_path / "out"), resume=True, consistent=False)     # the file's default is cover
    assert same["reveal"]["not_hidden"] == 0
    assert other["reveal"]["not_hidden"] == 1 and "another hide setting" in batch.reveal_line(other["reveal"])


def test_a_dry_run_says_it_hid_nothing(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        batch.run(RunConfig(backend="stub", workers=1, hide=["plate"], out_format="png"), src, str(tmp_path / "out"),
                  do_apply=False, consistent=False)
    rep = json.load(open(tmp_path / "out" / "p1.autoedit.json"))["reveal"]
    assert rep["hidden"] == [] and "dry run" in rep["hide_note"]


def test_the_folder_job_carries_per_photo_hide_requests(tmp_path):
    """The photo screen's Hide choices go to POST /jobs as photo_hide; the server renders that photo with them and records them."""
    import time
    from unittest.mock import patch

    from autoedit.web import jobs, service
    src = _photos(tmp_path / "in")
    p0, p1 = os.path.join(src, "p0.jpg"), os.path.join(src, "p1.jpg")

    def build(cfg):
        pipe = Pipeline.build(cfg)
        pipe.segmenter = _Fake()
        return pipe

    def wait(job):
        t0 = time.time()
        while job.status not in ("done", "cancelled", "error") and time.time() - t0 < 60:
            time.sleep(0.05)
        assert job.status == "done", [i.error for i in job.items]
    opts = {"backend": "stub", "out_format": "png", "consistent": False}
    with patch.object(service, "build_pipeline", build):
        job = jobs.start(src, [p0, p1], str(tmp_path / "out"), dict(opts),
                         photo_hide={p1: {"hide": ["plate"], "hide_method": "mosaic"}})
        wait(job)
        plain = jobs.start(src, [p0, p1], str(tmp_path / "plain"), dict(opts))
        wait(plain)
        for bad in ({p1: {"hide": ["plates"]}}, {p1: {"hide": ["plate"], "hide_method": "paint"}}, {"/nope.jpg": {"hide": ["plate"]}},
                    {p1: {"hide": ["plate"], "other": 1}}, {p1: ["plate"]}, [p1]):
            with pytest.raises(ValueError):
                jobs.start(src, [p0, p1], str(tmp_path / "o"), dict(opts), photo_hide=bad)
    mine = json.load(open(tmp_path / "out" / "p1.autoedit.json"))
    (h,) = mine["reveal"]["hidden"]
    assert h["kind"] == "plate" and h["method"] == "mosaic" and mine["audit"]["run_config"]["hide"] == ["plate"]
    assert json.load(open(tmp_path / "out" / "p0.autoedit.json"))["reveal"] is None      # the other photo asked for nothing
    assert _png(tmp_path / "out" / "p0.png").tobytes() == _png(tmp_path / "plain" / "p0.png").tobytes()
    assert _png(tmp_path / "out" / "p1.png").tobytes() != _png(tmp_path / "plain" / "p1.png").tobytes()

    try:
        from fastapi.testclient import TestClient
    except Exception:
        return                                              # no [ui] extra: the HTTP half cannot run
    from autoedit.web import app as webapp
    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service.approve_root(str(tmp_path))
    try:
        r = client.post("/api/v1/jobs", json={**opts, "input_dir": src, "files": [p0, p1], "out_dir": str(tmp_path / "bad"),
                                              "photo_hide": {p1: {"hide": ["plates"]}}})
        assert r.status_code == 400 and r.json()["code"] == "bad_request", r.text
        with patch.object(service, "build_pipeline", build):
            r = client.post("/api/v1/jobs", json={**opts, "input_dir": src, "files": [p0, p1], "out_dir": str(tmp_path / "api"),
                                                  "photo_hide": {p1: {"hide": "plate", "hide_method": "blur"}}})
            assert r.status_code == 200, r.text
            wait(jobs.get(r.json()["id"]))
        (h,) = json.load(open(tmp_path / "api" / "p1.autoedit.json"))["reveal"]["hidden"]
        assert h["method"] == "blur"
    finally:
        service._ALLOWED_ROOTS.clear()
