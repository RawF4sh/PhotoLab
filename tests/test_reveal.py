"""F21 T1: the reveal list, its check, connected areas, findings and the file part."""

import contextlib
import io
import json
import os

import numpy as np
import pytest
from PIL import Image as PILImage
from PIL.TiffImagePlugin import IFDRational

from autoedit import light, paths, reveal
from autoedit.edit.mask import blobs
from autoedit.imageio import metadata_report
from autoedit.segment.base import RegionMask
from autoedit.web import service as web_service

_PACKAGED = os.path.join(paths._packaged(), "reveal.toml")


def _cfg(tmp_path, text, name="reveal.toml"):
    p = tmp_path / name
    p.write_text(text)
    return reveal.load(str(p))


def _mask(h, w, *boxes):
    m = np.zeros((h, w), bool)
    for y0, y1, x0, x1 in boxes:
        m[y0:y1, x0:x1] = True
    return m


def _rm(name, mask, score=0.9):
    return RegionMask(name=name, mask=mask, score=score)


def test_config_loads_and_orders_words(tmp_path):
    cfg = reveal.load(_PACKAGED)
    assert [k.name for k in cfg.kinds][:3] == ["plate", "address", "writing"]
    ws = reveal.words(cfg)
    assert ws[0] == "license plate" and len(ws) == len(set(ws)) == 21
    off = _cfg(tmp_path, '[kind.a]\nlabel = "a"\nwords = ["x", "y"]\n[kind.b]\nlabel = "b"\nwords = ["y", "z"]\n'
                         '[kind.c]\nlabel = "c"\nwords = ["w"]\noff = true\n')
    assert reveal.words(off) == ["x", "y", "z"] and [k.name for k in off.kinds] == ["a", "b"]


def test_config_is_checked(tmp_path):
    good = '[kind.a]\nlabel = "a"\nwords = ["x"]\n'
    nine = ", ".join(f'"w{i}"' for i in range(9))
    many = "".join(f'[kind.k{i}]\nlabel = "k"\nwords = ["a{i}a", "b{i}b", "c{i}c", "d{i}d"]\n' for i in range(11))
    bad = [   # (text, a part of the message that names the key)
        ('bogus = 1\n' + good, "bogus"),
        ('[check]\nbogus = 1\n' + good, "check.bogus"),
        (f'[kind.a]\nlabel = "a"\nwords = [{nine}]\n', "kind.a.words"),
        ('[kind.a]\nlabel = "a"\nwords = ["a/b"]\n', "a/b"),
        ('[check]\nmin_score = 2\n' + good, "check.min_score"),
        ('[check]\nmin_side = nan\n' + good, "check.min_side"),
        ('[kind]\na = 3\n', "kind.a"),
        ('[kind.a]\nnope = 1\nlabel = "a"\nwords = ["x"]\n', "kind.a.nope"),
        (many + '[kind.z]\nlabel = "z"\nwords = ["last"]\n', "45 words"),
    ]
    for text, part in bad:
        with pytest.raises(ValueError) as e:
            _cfg(tmp_path, text)
        assert part in str(e.value), (part, str(e.value))
    _cfg(tmp_path, good)


def test_profile_adds_a_kind(tmp_path):
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "reveal.toml").write_text('[kind.logo]\nlabel = "logo"\nwords = ["company logo"]\n'
                                      '[kind.key]\noff = true\n')
    p = paths.config_paths(paths._packaged(), str(prof))["reveal_path"]
    cfg = reveal.load(p)
    names = [k.name for k in cfg.kinds]
    assert "logo" in names and "plate" in names and "key" not in names
    (prof / "reveal.toml").write_text('[kind.logo]\nwords = ["a/b"]\n')
    with pytest.raises(ValueError):
        paths.config_paths(paths._packaged(), str(prof))


def _old_largest_blob(mask, _GRID=128):
    h, w = mask.shape
    b = max(1, -(-max(h, w) // _GRID))
    gh, gw = -(-h // b), -(-w // b)
    padded = np.zeros((gh * b, gw * b), bool)
    padded[:h, :w] = mask
    cells = padded.reshape(gh, b, gw, b).any(axis=(1, 3))
    seen = np.zeros_like(cells)
    best = []
    for y0, x0 in zip(*np.nonzero(cells)):
        if seen[y0, x0]:
            continue
        stack, group = [(int(y0), int(x0))], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            group.append((y, x))
            for yy in range(max(y - 1, 0), min(y + 2, gh)):
                for xx in range(max(x - 1, 0), min(x + 2, gw)):
                    if cells[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        stack.append((yy, xx))
        if len(group) > len(best):
            best = group
    if not best:
        return None
    keep = np.zeros((gh, gw), bool)
    keep[tuple(np.array(best).T)] = True
    return mask & np.kron(keep, np.ones((b, b), bool))[:h, :w]


def test_blobs():
    m = _mask(100, 200, (5, 25, 5, 25), (40, 50, 50, 60), (70, 72, 150, 152))
    got = blobs(m, 64)
    assert len(got) == 3 and [int(g.sum()) for g in got] == [400, 100, 4]
    assert np.array_equal(got[0] | got[1] | got[2], m)
    assert blobs(np.zeros((30, 30), bool), 16) == []
    rng = np.random.default_rng(3)
    masks = [np.zeros((50, 70), bool), np.ones((50, 70), bool), _mask(40, 40, (7, 8, 9, 10)),
             _mask(300, 500, (10, 60, 10, 60), (100, 200, 100, 300))]
    for _ in range(6):
        m = np.zeros((257, 331), bool)
        for _ in range(int(rng.integers(1, 8))):
            y, x, a, b = rng.integers(0, 230), rng.integers(0, 300), rng.integers(1, 40), rng.integers(1, 40)
            m[y:y + a, x:x + b] = True
        masks.append(m)
    for m in masks:
        want, got = _old_largest_blob(m), light._largest_blob(m)
        assert (want is None) == (got is None)
        if want is not None:
            assert np.array_equal(want, got)


def test_findings_from_masks(tmp_path):
    cfg = reveal.load(_PACKAGED)
    h, w = 200, 400
    two = _mask(h, w, (20, 50, 20, 100), (120, 150, 250, 330))
    f, more = reveal.findings([_rm("license plate", two)], cfg, "landscape")
    assert more == 0 and [x.kind for x in f] == ["plate", "plate"]
    cell = 1.0 / 192 * 2
    assert all(abs(a - b) <= cell for a, b in zip(f[0].box, [20 / w, 20 / h, 100 / w, 50 / h])) or \
        all(abs(a - b) <= cell for a, b in zip(f[0].box, [250 / w, 120 / h, 330 / w, 150 / h]))
    assert all(0 <= v <= 1 for x in f for v in x.box)
    assert reveal.findings([_rm("license plate", two, 0.3)], cfg, "landscape")[0] == []
    tiny = _mask(h, w, (10, 11, 10, 11))
    assert reveal.findings([_rm("license plate", tiny)], cfg, "landscape")[0] == []
    # two words of one kind on the same area: one finding, the higher score and its word
    same = _mask(h, w, (20, 80, 20, 140))
    f, _ = reveal.findings([_rm("house number", same, 0.6), _rm("street sign", same, 0.8)], cfg, "landscape")
    assert len(f) == 1 and f[0].word == "street sign" and f[0].score == pytest.approx(0.8)


def test_subject_faces():
    cfg = reveal.load(_PACKAGED)
    m = _mask(300, 600, (10, 110, 10, 110), (150, 180, 200, 230), (150, 175, 400, 425))
    f, _ = reveal.findings([_rm("human face", m)], cfg, "people")
    assert [x.subject for x in f] == [True, False, False]
    f, _ = reveal.findings([_rm("human face", m)], cfg, "landscape")
    assert not any(x.subject for x in f)


def test_max_findings():
    cfg = reveal.load(_PACKAGED)
    assert cfg.max_findings == 30
    m = np.zeros((400, 800), bool)
    for i in range(40):
        y, x = 20 * (i // 10) + 5, 70 * (i % 10) + 10
        m[y:y + 12, x:x + 30] = True
    f, more = reveal.findings([_rm("license plate", m)], cfg, "landscape")
    assert len(f) == 30 and more == 10


def _meta_jpeg(path):
    ex = PILImage.Exif()
    ex[0x010F], ex[0x0132], ex[0x013B] = "TestMake", "2020:01:02 03:04:05", "Zelda Quux"
    ex[0x8769] = {0x9003: "2020:01:01 10:00:00", 0xA431: "SN-7741-ZX", 0xA430: "Owner Name"}
    ex[0x8825] = {1: "N", 2: (IFDRational(46), IFDRational(31), IFDRational(77)),
                  3: "E", 4: (IFDRational(11), IFDRational(22), IFDRational(88))}
    PILImage.fromarray(np.zeros((8, 8, 3), np.uint8), "RGB").save(str(path), exif=ex.tobytes())
    return str(path)


def test_metadata_report_never_holds_values(tmp_path):
    src = _meta_jpeg(tmp_path / "s.jpg")
    r = metadata_report(src, "shareable", "jpeg")
    assert r["gps"] == {"source": True, "export": False}
    assert r["serial"] == {"source": True, "export": False}
    assert r["names"] == {"source": True, "export": True}
    assert r["description"] == {"source": False, "export": False}
    assert all(v["export"] for v in metadata_report(src, "keep", "jpeg").values() if v["source"])
    assert not any(v["export"] for v in metadata_report(src, "none", "jpeg").values())
    t = metadata_report(src, "keep", "tiff")
    try:
        import tifffile  # noqa: F401
        assert t["names"]["export"] and not t["gps"]["export"] and not t["serial"]["export"]   # 16-bit: text tags only
    except ImportError:    # no tifffile: the TIFF is written 8-bit with the whole block
        assert t["gps"]["export"] and t["serial"]["export"]
    dump = json.dumps([r, t])
    for secret in ("7741", "ZX", "Zelda", "Quux", "Owner Name", "46", "88"):
        assert secret not in dump
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    for s in (str(bad), str(tmp_path / "missing.jpg"), None):
        r = metadata_report(s, "keep", "jpeg")
        assert r and not any(v["source"] or v["export"] for v in r.values())


# ---- T2: the check in a run -------------------------------------------------------------------

from autoedit import batch, cli, pipeline as pipeline_mod  # noqa: E402
from autoedit.contract import RunConfig  # noqa: E402
from autoedit.pipeline import Pipeline  # noqa: E402

_PLATE = "license plate"


class _Fake:
    """Open-vocabulary segmenter: finds a plate in a bright photo, nothing else. Records its calls."""
    name = "fake"
    open_vocabulary = True

    def __init__(self):
        self.calls = []

    def segment(self, img, concepts):
        self.calls.append(list(concepts))
        if _PLATE in concepts and img.rgb.mean() > 0.5:
            h, w = img.rgb.shape[:2]
            m = np.zeros((h, w), bool)
            m[h // 4:h // 2, w // 4:w // 2] = True
            return [RegionMask(name=_PLATE, mask=m, score=0.9, coverage=float(m.mean()))]
        return []


def _photos(folder, bright=(1,), n=3, names=None):
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(3)
    for i in range(n):
        base = 0.8 if i in bright else 0.25
        a = np.clip(base + rng.normal(0, 0.03, (96, 128, 3)), 0, 1)
        PILImage.fromarray((a * 255).astype(np.uint8), "RGB").save(os.path.join(folder, (names or {}).get(i, f"p{i}.jpg")))
    return str(folder)


@contextlib.contextmanager
def _faked():
    """Every pipeline built inside gets the fake segmenter (no monkeypatch fixture: run_all has none)."""
    f, old = _Fake(), pipeline_mod.make_segmenter
    pipeline_mod.make_segmenter = lambda *a, **k: f
    try:
        yield f
    finally:
        pipeline_mod.make_segmenter = old


def _run(src, out, check=True, **kw):
    return batch.run(RunConfig(backend="stub", reveal_check=check, workers=1, **kw), str(src), str(out), consistent=False)


def test_presegment_includes_reveal_words(tmp_path):
    src = _photos(tmp_path / "in", n=1)
    path = os.path.join(src, "p0.jpg")
    seen = {}
    for check in (True, False):
        pipe = Pipeline.build(RunConfig(backend="stub", reveal_check=check, workers=1))
        pipe.segmenter = f = _Fake()
        pipe.presegment(path)
        pipe.process_one(path)
        assert f.calls[0] == f.calls[1]
        seen[check] = f.calls[0]
    want = reveal.words(reveal.load(_PACKAGED))
    assert all(w in seen[True] for w in want) and not any(w in seen[False] for w in want)


def test_stub_reports_not_run(tmp_path):
    src = _photos(tmp_path / "in", n=1)
    m = _run(src, tmp_path / "out")
    rep = json.load(open(tmp_path / "out" / "p0.autoedit.json"))["reveal"]
    assert rep["picture"] == "not_run" and rep["reason"] and rep["findings"] == [] and rep["file"]
    assert m["reveal"]["picture"] == "not_run" and m["reveal"]["with_findings"] == 0
    assert "pictures were not checked" in batch.reveal_line(m["reveal"])


def test_check_changes_no_pixel(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        _run(src, tmp_path / "on")
        _run(src, tmp_path / "off", check=False)
        for n in ("p0.jpg", "p1.jpg", "p2.jpg"):
            assert (tmp_path / "on" / n).read_bytes() == (tmp_path / "off" / n).read_bytes()
        assert json.load(open(tmp_path / "off" / "p1.autoedit.json"))["reveal"] is None


def test_sidecar_and_manifest(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        m = _run(src, out)
        f = json.load(open(out / "p1.autoedit.json"))["reveal"]["findings"]
        assert len(f) == 1 and f[0]["kind"] == "plate" and f[0]["box"][:2] == [0.25, 0.25]
        assert m["reveal"]["with_findings"] == 1 and m["reveal"]["by_kind"] == {"plate": 1} and m["reveal"]["photos"] == 3
        assert "1 of 3 photos have findings (plate 1)" in batch.reveal_line(m["reveal"])
        again = batch.run(RunConfig(backend="stub", reveal_check=True, workers=1), src, str(out), resume=True, consistent=False)
        assert again["skipped"] == 3 and again["reveal"] == m["reveal"]


def test_page_wording(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in", names={1: "a<script>x.jpg"})
        out = tmp_path / "out"
        _run(src, out)
        page = (out / "reveal.html").read_text()
        assert "&lt;script&gt;" in page and "<script" not in page and "http" not in page and str(tmp_path) not in page
        assert 'class="box"' in page and "number plate" in page and (out / "reveal").is_dir()
        rest = page.replace("That is not a statement that they are safe to share.", "")
        for w in ("safe", "clean", "OK to share", "private"):
            assert w not in rest
        assert "2 photos: nothing on the list was found. That is not a statement" in page
        assert "The list: number plate" in page


def test_check_command(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        assert cli.main(["check", src, "--out", str(out), "--backend", "stub", "--strict"]) == 2
        assert (out / "reveal.html").exists() and (out / "p1.autoedit.json").exists()
        assert not [p for p in os.listdir(out) if p.endswith(".jpg")]       # no image is written
        plain = _photos(tmp_path / "none", bright=())
        assert cli.main(["check", plain, "--out", str(tmp_path / "o2"), "--backend", "stub", "--strict"]) == 0
        assert cli.main(["check", plain, "--out", str(tmp_path / "o3"), "--backend", "stub"]) == 0


def test_weak_kind_is_marked(tmp_path):
    with _faked():
        cfg = tmp_path / "reveal.toml"
        cfg.write_text('[kind.plate]\nlabel = "number plate"\nwords = ["license plate"]\nweak = true\n')
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        _run(src, out, reveal_path=str(cfg))
        assert json.load(open(out / "p1.autoedit.json"))["reveal"]["findings"][0]["weak"] is True
        assert "may miss" in (out / "reveal.html").read_text()


def test_preview_returns_reveal(tmp_path):
    from unittest.mock import patch

    from autoedit.web import service
    src = _photos(tmp_path / "in")
    path = os.path.join(src, "p1.jpg")
    assert service.preview(path, {"backend": "stub"})["reveal"] is None
    off = service.preview(path, {"backend": "stub", "reveal_check": True})["reveal"]
    assert off["picture"] == "not_run" and off["reason"] and off["findings"] == []   # the stub cannot find named things

    def build(cfg):
        pipe = Pipeline.build(cfg)
        pipe.segmenter = _Fake()
        return pipe
    with patch.object(service, "build_pipeline", build):
        rv = service.preview(path, {"backend": "stub", "reveal_check": True})["reveal"]
    assert rv["picture"] == "checked" and rv["findings"][0]["kind"] == "plate" and rv["findings"][0]["box"][:2] == [0.25, 0.25]


def test_studio_strings(tmp_path):
    page = open(os.path.join(os.path.dirname(web_service.__file__), "static", "studio.html"), encoding="utf-8").read()
    for s in ("Check before sharing", "What it found", "reveal_check", "may miss",
              "the subject", "other people", "was not checked", "It looked for", "Nothing was done to the picture"):
        assert s in page
    mine = page[page.index("/* ---- \"Check before sharing\""):page.index("function applyPreview")]
    mine += page[page.index('id="revealCk"') - 80:page.index('id="revealCk"') + 400]
    for w in ("safe", "clean", "private", "OK to share"):
        assert w not in mine


# ---- review fixes -----------------------------------------------------------------------------

def test_worker_path_checks_pictures(tmp_path):
    """The parent segments, a render worker (a `_NoModel` over the same disk cache) renders: the picture is
    checked and the plate is found, and a worker never asks for a word the parent left out."""
    src = _photos(tmp_path / "in")
    path, out = os.path.join(src, "p1.jpg"), str(tmp_path / "out")
    cfg = RunConfig(backend="sam3", reveal_check=True, mask_cache=True, workers=2)
    with _faked() as fake:
        pipe = Pipeline.build(cfg)
        pipe.use_mask_cache(out)
        assert batch._NoModel(True).open_vocabulary and not batch._NoModel().open_vocabulary
        pipe.presegment(path)
        asked = len(fake.calls)
        batch._worker_init(cfg, out, 1, bool(getattr(pipe.segmenter, "open_vocabulary", False)))
        _, res = batch._WORKER["pipe"].process_one(path, do_apply=False)
    assert len(fake.calls) == asked                      # the worker read the cache only
    assert res.reveal.picture == "checked" and [f.kind for f in res.reveal.findings] == ["plate"]


def test_presegment_asks_for_the_guard_words(tmp_path):
    from autoedit.contract import Adjustment
    from autoedit.segment.base import mark_name
    src = _photos(tmp_path / "in", n=1)
    path = os.path.join(src, "p0.jpg")
    pipe = Pipeline.build(RunConfig(backend="stub", reveal_check=True, workers=1))
    pipe.segmenter = f = _Fake()
    pipe.adjustments = [Adjustment(target=mark_name("thing", (0.3, 0.3, 0.7, 0.7), "guy"),
                                   ops={"exposure": 0.3}, scope="photo")]
    pipe.presegment(path)
    pipe.process_one(path)
    assert f.calls[0] == f.calls[1]
    assert any(n in f.calls[0] for n, cm in pipe.meta.items() if cm.guard_global)


def test_strict_check_fails_when_the_picture_part_did_not_run(tmp_path):
    src = _photos(tmp_path / "in")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert cli.main(["check", src, "--out", str(tmp_path / "a"), "--backend", "stub", "--strict"]) == 2
    assert "did not run" in err.getvalue()
    assert cli.main(["check", src, "--out", str(tmp_path / "b"), "--backend", "stub"]) == 0


def test_studio_clears_reveal_with_the_preview():
    page = open(os.path.join(os.path.dirname(web_service.__file__), "static", "studio.html"), encoding="utf-8").read()
    fn = page[page.index("function showSourceOnly"):page.index("function previewLoading")]
    assert "STATE.preview=null" in fn and "renderReveal()" in fn
    assert "REVEAL_LABELS" not in page and "Boxes not shown: the photo was straightened or cropped" in page


def test_boxes_are_not_drawn_on_a_warped_photo(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        _run(src, out)
        sc = out / "p1.autoedit.json"
        doc = json.load(open(sc))
        doc["geometry"] = {"angle": 1.0, "line": "horizon", "confidence": 0.9}
        sc.write_text(json.dumps(doc))
        batch.run(RunConfig(backend="stub", reveal_check=True, workers=1), src, str(out), resume=True, consistent=False)
        page = (out / "reveal.html").read_text()
    assert 'class="box"' not in page and page.count("Boxes not shown: the photo was straightened or cropped.") == 1
    assert "number plate" in page                      # the finding is still listed


def test_resume_names_photos_made_without_the_check(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        _run(src, out, check=False)
        m = batch.run(RunConfig(backend="stub", reveal_check=True, workers=1), src, str(out), resume=True, consistent=False)
        page = (out / "reveal.html").read_text()
    assert m["reveal"]["unchecked"] == 3
    assert "not checked (made before the check was on)" in batch.reveal_line(m["reveal"])
    assert "Not checked (made before the check was on)" in page and "p0.jpg" in page


def test_page_says_when_no_file_information_was_found(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        _run(src, out)
        page = (out / "reveal.html").read_text()
    assert "No file information was found in the source of any photo. It looked for: GPS position" in page


def test_finding_carries_its_label(tmp_path):
    with _faked():
        src = _photos(tmp_path / "in")
        out = tmp_path / "out"
        _run(src, out)
    rep = json.load(open(out / "p1.autoedit.json"))["reveal"]
    assert rep["findings"][0]["label"] == "number plate"
    assert {k.name: k.label for k in reveal.load(_PACKAGED).kinds}["child"] == "possibly a child"   # rule 4, now only in the toml
    from autoedit.contract import RevealReport
    assert reveal._tell(RevealReport.model_validate(rep), {})[0] == "number plate"
