"""F48: the gallery page."""
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from PIL import Image as PILImage

from autoedit import batch, cli, gallery
from autoedit.contract import RunConfig


def _make(path):
    a = np.zeros((128, 128, 3), np.float32)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    PILImage.fromarray(a.astype(np.uint8), "RGB").save(path, quality=95)


def _run(tmp_path, names=("a.jpg", "b.jpg")):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    for n in names:
        _make(src / n)
    batch.run(RunConfig(backend="stub"), str(src), str(out))
    return src, out


def _hrefs(page):
    return [unquote(h) for h in re.findall(r'<a href="([^"]*)"', page)]


def test_gallery_page(tmp_path):
    src, out = _run(tmp_path)
    dry = tmp_path / "dsrc"
    dry.mkdir()
    _make(dry / "c.jpg")
    batch.run(RunConfig(backend="stub"), str(dry), str(out / "dry"), do_apply=False)
    r = gallery.write(str(out))
    assert r["photos"] == 2 and r["skipped"] == 1
    page = Path(r["page"]).read_text()
    assert page.count("<figure>") == 2
    for s in re.findall(r'<img src="([^"]*)"', page):
        assert os.path.isfile(out / unquote(s)) and unquote(s).startswith("gallery/")
    for h in _hrefs(page):
        assert os.path.isfile(out / h)
    for bad in ("http", "<script", "base64", str(tmp_path)):
        assert bad not in page
    first = sorted(os.listdir(out / "gallery"))
    gallery.write(str(out))
    assert sorted(os.listdir(out / "gallery")) == first


def test_gallery_escapes_and_warns(tmp_path):
    _, out = _run(tmp_path, ("x<&>.jpg", "b.jpg"))
    side = next(p for p in out.glob("*.autoedit.json") if "x" in p.name[:1])
    d = json.loads(side.read_text())
    d["reveal"] = {"picture": "checked", "findings": [{"kind": "plate", "label": "number plate", "word": "plate",
                                                        "score": 0.9, "box": [0, 0, 0.1, 0.1], "share": 0.01}]}
    side.write_text(json.dumps(d))
    r = gallery.write(str(out))
    page = Path(r["page"]).read_text()
    assert "x&lt;&amp;&gt;.jpg" in page
    assert "x%3C%26%3E" in page
    assert page.count('class="warn"') == 1 and "number plate" in page
    assert r["warned"] == 1


def test_gallery_elsewhere(tmp_path):
    _, out = _run(tmp_path)
    r = gallery.write(str(out), str(tmp_path / "share"))
    share = tmp_path / "share"
    page = Path(r["page"]).read_text()
    hs = _hrefs(page)
    assert len(hs) == 2
    for h in hs:
        assert h.startswith("../") and os.path.isfile(share / h)
    assert len(os.listdir(share / "gallery")) == 2


def test_cli_gallery(tmp_path):
    import contextlib, io
    _, out = _run(tmp_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(["gallery", str(out)]) == 0
    lines = buf.getvalue().strip().splitlines()
    assert lines[0].endswith("gallery.html") and lines[1].startswith("2 photos")
    assert cli.main(["gallery", str(tmp_path / "nope")]) == 2
