"""F48 watermark: drawn on the export only, off by default, recorded and declared."""

import json
import os

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import adjust, batch, cli, declare, exports, paths, watermark
from autoedit.contract import RunConfig
from test_adjust import _make


def _changed(a, b):
    ys, xs = np.nonzero((a != b).any(axis=2))
    return ys, xs


def test_draw_places_the_mark_in_a_corner():
    rgb = np.full((800, 1200, 3), 0.4, np.float32)
    out = watermark.draw(rgb, "© Test 2026")
    assert out.dtype == np.float32 and 0.0 <= out.min() and out.max() <= 1.0
    ys, xs = _changed(out, rgb)
    assert len(ys) and ys.min() >= 400 and xs.min() >= 600
    assert ys.max() <= 800 - 24 and xs.max() <= 1200 - 24
    assert 12 <= ys.max() - ys.min() + 1 <= 27
    top = watermark.draw(rgb, "© Test 2026", corner="top_left")
    ys, xs = _changed(top, rgb)
    assert ys.min() >= 24 and xs.min() >= 24


def test_opacity_and_fit():
    rgb = np.full((800, 1200, 3), 0.4, np.float32)
    assert watermark.draw(rgb, "WWW", opacity=1.0).max() >= 0.99
    assert watermark.draw(rgb, "WWW", opacity=0.3).max() <= 0.6
    small = np.full((60, 90, 3), 0.4, np.float32)
    ys, xs = _changed(watermark.draw(small, "x" * 100), small)
    assert len(ys) and ys.min() > 0 and xs.min() > 0 and ys.max() < 59 and xs.max() < 89


def test_logo(tmp_path):
    PILImage.new("RGBA", (40, 20), (255, 0, 0, 255)).save(tmp_path / "logo.png")
    rgb = np.full((800, 1200, 3), 0.4, np.float32)
    a = watermark.args(RunConfig(watermark_logo="logo.png", profile_dir=str(tmp_path)))
    out = watermark.draw(rgb, **a)
    red = (out[..., 0] > 0.6) & (out[..., 1] < 0.3)
    assert red[400:, 600:].any() and not red[:400].any()
    a = watermark.args(RunConfig(watermark="x", watermark_logo="logo.png", profile_dir=str(tmp_path)))
    out = watermark.draw(rgb, **a)
    assert ((out[..., 0] > 0.6) & (out[..., 1] < 0.3)).any() and (out.min(axis=2) > 0.75).any()
    with pytest.raises(FileNotFoundError, match="nope.png"):
        watermark.logo_file("nope.png", str(tmp_path))
    PILImage.new("RGB", (8, 8)).save(tmp_path / "fake.png", format="JPEG")
    with pytest.raises(ValueError):
        watermark.args(RunConfig(watermark_logo="fake.png", profile_dir=str(tmp_path)))


def test_validation(tmp_path):
    for bad in ({"watermark": "a\nb"}, {"watermark_size": 0.5}, {"watermark_size": 25},
                {"watermark_opacity": 0}, {"watermark_opacity": 1.5}, {"watermark_corner": "middle"},
                *({"watermark_logo": v} for v in ("../logo.png", "a/logo.png", "logo.jpg", ".png"))):
        with pytest.raises(ValueError):
            RunConfig(**bad)
    assert RunConfig(watermark="").watermark is None and RunConfig(watermark="  ").watermark is None
    p = tmp_path / "exports.toml"
    p.write_text('[m]\nwatermark = "x"\nwatermark_logo = "l.png"\nwatermark_corner = "top_left"\n'
                 'watermark_size = 4.0\nwatermark_opacity = 0.5\n')
    kw = exports.preset_kwargs("m", str(p))
    assert kw["watermark"] == "x" and kw["watermark_corner"] == "top_left" and len(kw) == 5


def _runs(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _make(str(src / "a.jpg"))
    outs = {}
    for k, kw in (("plain", {}), ("marked", {"watermark": "© Test"})):
        outs[k] = tmp_path / k
        batch.run(RunConfig(backend="stub", out_format="png", **kw), str(src), str(outs[k]))
    return src, outs


def _side(out):
    return json.load(open(out / "a.autoedit.json"))


def _px(out):
    return np.asarray(PILImage.open(out / "a.png").convert("RGB"))


def test_mark_on_the_export_only(tmp_path):
    src, outs = _runs(tmp_path)
    a, b = _px(outs["plain"]).astype(int), _px(outs["marked"]).astype(int)
    ys, xs = np.nonzero((a != b).any(axis=2))
    assert len(ys) and ys.min() >= a.shape[0] // 2 and xs.min() >= a.shape[1] // 2
    sp, sm = _side(outs["plain"]), _side(outs["marked"])
    for k in ("global_stats", "post_edit_stats", "quality_score"):
        assert sp[k] == sm[k]
    assert sm["audit"]["watermark"]["text"] == "© Test"
    assert any(n.startswith("watermark:") for n in sm["notes"])
    before = (outs["marked"] / "a.png").read_bytes()
    adjust.rerender(str(outs["marked"] / "a.png"))
    assert (outs["marked"] / "a.png").read_bytes() == before
    prof = tmp_path / "prof"
    prof.mkdir()
    (prof / "exports.toml").write_text('[marked]\nformat = "png"\nwatermark = "© Test"\n', encoding="utf-8")
    cli_out = tmp_path / "cli"
    assert cli.main(["run", str(src), str(cli_out), "--backend", "stub", "--export", "marked",
                     "--profile", str(prof)]) == 0
    c = next(cli_out.rglob("a.png"))
    ys, xs = np.nonzero((np.asarray(PILImage.open(c).convert("RGB")).astype(int) != a).any(axis=2))
    assert len(ys) and ys.min() >= a.shape[0] // 2 and xs.min() >= a.shape[1] // 2


def test_no_mark_by_default(tmp_path):
    assert watermark.args(RunConfig()) is None
    _, outs = _runs(tmp_path)
    s = _side(outs["plain"])
    assert "watermark" not in s["audit"] and not any(n.startswith("watermark:") for n in s["notes"])
    for name, p in exports.load(os.path.join(paths.configs_dir(), "exports.toml")).items():
        assert "watermark" not in p and "watermark_logo" not in p, name


def test_declaration_lists_the_mark(tmp_path):
    _, outs = _runs(tmp_path)
    d = declare.declaration(str(outs["marked"] / "a.autoedit.json"))
    assert any(s.startswith("watermark:") for s in d["steps"]) and d["watermark"]
    declare.write(str(outs["marked"]), None)
    assert declare.WATERMARK_NOTE in open(outs["marked"] / "declaration.html", encoding="utf-8").read()
    declare.write(str(outs["plain"]), None)
    assert declare.WATERMARK_NOTE not in open(outs["plain"] / "declaration.html", encoding="utf-8").read()
