"""F42: the edit declaration."""

import hashlib
import json
import os

import numpy as np
from PIL import Image as PILImage

from autoedit import batch, declare
from autoedit.contract import MaskOps, Result, RunConfig


def _make(path):
    a = np.zeros((128, 128, 3), np.float32)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    PILImage.fromarray(a.astype(np.uint8), "RGB").save(path, quality=95)


def _run(tmp_path, **kw):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _make(str(src / "a.jpg"))
    batch.run(RunConfig(backend="stub", **kw), str(src), str(out))
    return src, out, str(out / "a.autoedit.json")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _hand(path, **fields):
    r = Result(image="a.jpg", scene="x", confidence=1.0, **fields)
    open(path, "w").write(r.model_dump_json())


def test_declaration_names_hashes_and_non_neutral(tmp_path):
    src, out, sc = _run(tmp_path)
    d = declare.declaration(sc)
    assert d["source"] == "a.jpg" and d["export"] == "a.jpg"
    assert d["source_sha256"] == _sha(src / "a.jpg") and d["export_sha256"] == _sha(out / "a.jpg")
    r = Result.model_validate_json(open(sc).read())
    want = {k for k, v in r.global_settings.items() if v and k not in declare._OWN}
    assert {s.split()[0] for s in d["whole_photo"]} == want
    assert not any(" +0 " in s + " " or s.endswith(" +0") for s in d["whole_photo"])


def test_region_instruction_listed(tmp_path):
    _, _, sc = _run(tmp_path, concepts=["sky", "subject"], edit="brighten the sky")
    d = declare.declaration(sc)
    sky = [r for r in d["regions"] if r["name"] == "sky"]
    assert sky and sky[0]["settings"]


def test_repairs_listed_apart(tmp_path):
    p = str(tmp_path / "a.autoedit.json")
    _hand(p, global_settings={"hot_pixels": 1.0, "denoise": 30.0, "exposure": 0.3})
    d = declare.declaration(p)
    assert len(d["repairs"]) == 2 and d["whole_photo"] == ["exposure +0.30 EV"]
    assert d["source_sha256"] is None and d["notes"]


def test_page_escapes_and_is_clean(tmp_path):
    p = str(tmp_path / "<script>x.autoedit.json")
    _hand(p, masks=[MaskOps(target="<b>sky</b>", ops={"exposure": 0.2}, coverage=0.3)])
    jp, hp = declare.write(str(tmp_path), None)
    page = open(jp if jp.endswith(".html") else hp).read()
    assert "<script" not in page.lower() and "&lt;b&gt;sky" in page
    assert declare.STATEMENT in page
    assert "http" not in page and str(tmp_path) not in page
    # a name with <script> as the source image
    q = tmp_path / "q"
    q.mkdir()
    r = Result(image="<script>alert(1)</script>.jpg", scene="x", confidence=1.0)
    (q / "z.autoedit.json").write_text(r.model_dump_json())
    page = open(declare.write(str(q), None)[0]).read()
    assert "<script" not in page.lower() and "&lt;script&gt;" in page


def test_cmd_declare_writes_both_and_counts_broken(tmp_path):
    src, out, sc = _run(tmp_path)
    (out / "broken.autoedit.json").write_text("{not json")
    text = declare.cmd_declare(str(out), None)
    assert os.path.isfile(out / "declaration.html") and os.path.isfile(out / "declaration.json")
    assert "1 photos declared, 1 sidecar skipped" in text
    assert json.load(open(out / "declaration.json"))["photos"][0]["source"] == "a.jpg"


def test_cli_declare(tmp_path):
    from autoedit import cli
    _src, out, _sc = _run(tmp_path)
    dest = tmp_path / "decl"
    assert cli.main(["declare", str(out), "-o", str(dest)]) == 0
    assert (dest / "declaration.html").is_file() and (dest / "declaration.json").is_file()


def test_other_pixel_steps_listed_and_bw_once(tmp_path):
    p = str(tmp_path / "a.autoedit.json")
    _hand(p, global_settings={"bw": 100.0, "exposure": 0.2}, decode_ev=0.7, exposure_bias=0.3, wb_bias=[2.0, -1.0],
          notes=["reference match → ref.jpg (strength 0.50)", "post-reference midground lift: x",
                 "highlight hold: nothing in the frame is blown in the source; 9% eased", "oversaturation gate: pulled chroma back on 4% of frame",
                 "palette: sunset (40%)", "palette: none, the photo is black and white", "unrelated note"],
          audit={"backdrop": {"applied": True, "coverage": 0.4, "white": 0.96}, "neutral": {"temp": 3.0, "tint": -2.0},
                 "run_config": {"out_long_edge": 2000}})
    d = declare.declaration(p)
    steps = " | ".join(d["steps"])
    for want in ("RAW decode: +0.70 EV", "exposure +0.30 EV", "white balance +2.0 temp", "picked spot", "reference match → ref.jpg",
                 "post-reference midground", "highlight hold", "oversaturation gate", "palette: sunset", "backdrop evened out on 40%",
                 "245 of 255", "2000 px"):
        assert want in steps, want
    assert "palette: none" not in steps and "unrelated" not in steps
    assert d["black_and_white"] == 100.0 and not any(x.startswith("bw") for x in d["whole_photo"])
    hp, jp = declare.write(str(tmp_path), None)
    page = open(hp).read()
    assert "Other steps" in page and "backdrop evened out" in page and page.count("black and white") == 1
    assert json.load(open(jp))["photos"][0]["steps"] == d["steps"]
