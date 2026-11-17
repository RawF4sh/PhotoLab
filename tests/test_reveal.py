"""F21 T1: the reveal list, its check, connected areas, findings and the file part."""

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
