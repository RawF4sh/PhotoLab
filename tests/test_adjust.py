"""F3: disk mask cache, per-photo re-render (`autoedit adjust`), sidecar run record."""

import json
import os

import numpy as np
from PIL import Image as PILImage

from autoedit import adjust, batch, cli
from autoedit.contract import RunConfig
from autoedit.imageio import Image
from autoedit.pipeline import Pipeline
from autoedit.segment import MASK_CACHE_DIR, DiskMaskCache
from autoedit.segment.base import RegionMask


def _make(path, gain=1.0):
    a = np.zeros((128, 128, 3), np.float32)
    a[:70] = [100, 140, 200]   # sky
    a[70:] = [60, 110, 70]     # foliage
    yy, xx = np.mgrid[0:128, 0:128]
    a[(((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0] = [200, 150, 120]   # skin subject
    PILImage.fromarray(np.clip(a * gain, 0, 255).astype(np.uint8), "RGB").save(path, quality=95)


class _Fake:
    """Counts the concepts it is asked for; finds everything except 'ghost'."""
    name = "fake"

    def __init__(self):
        self.calls = []

    def segment(self, img, concepts):
        self.calls.append(list(concepts))
        out = []
        for c in concepts:
            if c == "ghost":
                continue
            m = np.zeros(img.rgb.shape[:2], bool)
            m[:10, :10] = True
            out.append(RegionMask(name=c, mask=m, score=0.75, coverage=0.5, instance_count=2))
        return out


def _img(v=0.5):
    return Image(rgb=np.full((16, 16, 3), v, np.float32), path="x.jpg")


def test_disk_mask_cache(tmp_path):
    root = str(tmp_path / "m")
    inner = _Fake()
    first = DiskMaskCache(inner, root).segment(_img(), ["sky", "ghost"])
    assert inner.calls == [["sky", "ghost"]] and [r.name for r in first] == ["sky"]
    inner2 = _Fake()
    cache = DiskMaskCache(inner2, root)
    again = cache.segment(_img(), ["sky", "ghost"])
    assert inner2.calls == []                       # hit, and "ghost" stays remembered as absent
    assert again[0].score == first[0].score == 0.75 and again[0].instance_count == 2
    assert again[0].coverage == 0.5 and (again[0].mask == first[0].mask).all()
    assert cache.name == "fake"                     # attribute pass-through
    cache.segment(_img(), ["sky", "grass"])
    assert inner2.calls == [["grass"]]              # only the new concept
    cache.segment(_img(0.6), ["sky"])
    assert inner2.calls[-1] == ["sky"]              # other pixels, own file
    files = sorted(os.listdir(root))
    assert len(files) == 2 and all(f.endswith(".npz") for f in files)
    for f in files:
        np.load(os.path.join(root, f), allow_pickle=False).close()
    flat_a = Image(rgb=np.full((16, 32, 3), 0.5, np.float32), path="x.jpg")
    flat_b = Image(rgb=np.full((32, 16, 3), 0.5, np.float32), path="x.jpg")
    cache = DiskMaskCache(_Fake(), root)
    assert cache._path(flat_a) != cache._path(flat_b)
    wrong = RegionMask(name="sky", mask=np.ones((8, 8), bool), score=0.9, coverage=1.0, instance_count=1)
    cache._write(cache._path(_img()), {"sky": wrong})
    inner4 = _Fake()
    DiskMaskCache(inner4, root).segment(_img(), ["sky"])
    assert inner4.calls == [["sky"]]                # a mask of another shape is dropped
    files = sorted(os.listdir(root))
    assert not [f for f in files if f.endswith(".part")]
    with open(os.path.join(root, files[0]), "wb") as fh:
        fh.write(b"garbage")
    inner3 = _Fake()
    DiskMaskCache(inner3, root).segment(_img(0.6), ["sky"])
    DiskMaskCache(inner3, root).segment(_img(), ["sky"])
    assert len(inner3.calls) == 1                   # the garbage file is ignored, the good one served


def test_use_mask_cache(tmp_path):
    out = str(tmp_path)
    pipe = Pipeline.build(RunConfig(backend="stub"))
    seg = pipe.segmenter
    pipe.use_mask_cache(out)
    assert pipe.segmenter is seg
    pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})
    fake = pipe.segmenter = _Fake()
    pipe.use_mask_cache(out)
    wrapped = pipe.segmenter
    assert isinstance(wrapped, DiskMaskCache) and wrapped.inner is fake
    assert wrapped.root == os.path.join(out, MASK_CACHE_DIR, "sam3")
    pipe.use_mask_cache(out)
    assert pipe.segmenter is wrapped
    pipe.segmenter = fake
    pipe.cfg = pipe.cfg.model_copy(update={"mask_cache": False})
    pipe.use_mask_cache(out)
    assert pipe.segmenter is fake


def _batch(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _make(str(src / "a.jpg"), 1.0)
    _make(str(src / "b.jpg"), 0.85)
    batch.run(RunConfig(backend="stub"), str(src), str(out))
    return src, out


def _norm(path):
    d = json.load(open(path))
    d.pop("timing_ms", None)
    d.pop("stage_timings_ms", None)
    return d


def _bytes(p):
    return open(p, "rb").read()


def _luma(path):
    return np.asarray(PILImage.open(path).convert("L"), np.float32)


def test_rerender_without_adjustment_is_identical(tmp_path):
    _, out = _batch(tmp_path)
    biases = [json.load(open(out / f"{n}.autoedit.json"))["exposure_bias"] for n in "ab"]
    assert any(b != 0 for b in biases)
    for n in "ab":
        img, sc = str(out / f"{n}.jpg"), str(out / f"{n}.autoedit.json")
        before_img, before_sc = _bytes(img), _norm(sc)
        adjust.rerender(img)
        assert _bytes(img) == before_img
        assert _norm(sc) == before_sc


def test_adjustment_changes_only_its_target(tmp_path):
    _, out = _batch(tmp_path)
    img = str(out / "a.jpg")
    before = _luma(img)
    adjust.rerender(img, sets=["sky.exposure=-0.4"])
    after = _luma(img)
    assert before[:40].mean() - after[:40].mean() > 5
    assert np.abs(before[-20:] - after[-20:]).max() <= 2
    adjs = json.load(open(out / "a.autoedit.json"))["adjustments"]
    assert len(adjs) == 1 and adjs[0]["scope"] == "photo"
    assert adjs[0]["status"] == "applied" and adjs[0]["target"] == "sky"


def test_adjustments_accumulate_and_reset(tmp_path):
    _, out = _batch(tmp_path)
    img, sc = str(out / "a.jpg"), str(out / "a.autoedit.json")
    original = _bytes(img)
    adjust.rerender(img, sets=["sky.exposure=-0.4"])
    adjust.rerender(img, sets=["global.contrast=5"])
    assert [a["scope"] for a in json.load(open(sc))["adjustments"]] == ["photo", "photo"]
    adjust.rerender(img, reset=True)
    assert json.load(open(sc))["adjustments"] == []
    assert _bytes(img) == original


def test_resume_keeps_adjusted_photo(tmp_path):
    src, out = _batch(tmp_path)
    img, sc = str(out / "a.jpg"), str(out / "a.autoedit.json")
    adjust.rerender(img, sets=["sky.exposure=-0.4"])
    img_bytes, sc_bytes = _bytes(img), _bytes(sc)
    batch.run(RunConfig(backend="stub"), str(src), str(out), resume=True)
    assert _bytes(img) == img_bytes and _bytes(sc) == sc_bytes


def test_parse_set():
    a = adjust.parse_set("sky.exposure=-0.3")
    assert (a.target, a.ops, a.scope) == ("sky", {"exposure": -0.3}, "photo")
    b = adjust.parse_set(" Red Jacket.temp = 4 ")
    assert b.target == "red jacket" and b.ops == {"temp": 4.0}
    assert adjust.parse_set("global.contrast=5").target == "global"
    for bad in ("sky.exposure", "exposure=1", "sky.nope=1", "sky.exposure=abc",
                "sky.exposure=nan", "sky.exposure=inf", "sky.exposure=-inf"):
        try:
            adjust.parse_set(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def test_rerender_errors(tmp_path):
    src, out = _batch(tmp_path)
    img, sc = str(out / "a.jpg"), out / "a.autoedit.json"
    good = sc.read_text()
    d = json.loads(good)
    d["audit"].pop("run_config")
    sc.write_text(json.dumps(d))
    _raises(ValueError, lambda: adjust.rerender(img))
    d = json.loads(good)
    d["audit"]["run_inputs"]["reference"] = "ref.jpg"
    sc.write_text(json.dumps(d))
    _raises(ValueError, lambda: adjust.rerender(img))
    ref = str(tmp_path / "ref.jpg")
    _make(ref)
    _raises(ValueError, lambda: adjust.rerender(img, reference=str(tmp_path / "other.jpg")))
    adjust.rerender(img, reference=ref)
    d = json.loads(good)
    d["audit"]["run_config"]["decider"] = "llm"      # stops before any pipeline is built
    sc.write_text(json.dumps(d))
    _raises(ValueError, lambda: adjust.rerender(img))
    d = json.loads(good)
    d["audit"]["output"] = "../x.jpg"
    sc.write_text(json.dumps(d))
    adjust.rerender(img)
    assert (out / "x.jpg").is_file() and not (tmp_path / "x.jpg").exists()
    sc.write_text(good)
    os.remove(out / "run.json")
    _raises(FileNotFoundError, lambda: adjust.rerender(img))
    adjust.rerender(img, source=str(src / "a.jpg"))


def test_rerender_warns_when_configs_changed(tmp_path):
    import shutil
    from autoedit.paths import configs_dir
    _, out = _batch(tmp_path)
    img = str(out / "a.jpg")
    same = tmp_path / "same"
    shutil.copytree(configs_dir(), same)
    assert adjust.rerender(img, configs_dir=str(same))["warnings"] == []
    other = tmp_path / "other"
    shutil.copytree(configs_dir(), other)
    with open(other / "looks.toml", "a") as fh:
        fh.write("\n# edited\n")
    r = adjust.rerender(img, configs_dir=str(other))
    assert len(r["warnings"]) == 1
    assert any(n.startswith("re-render:") for n in json.load(open(out / "a.autoedit.json"))["notes"])


def test_rerender_through_the_disk_cache(tmp_path):
    src = str(tmp_path / "p.jpg")
    _make(src)
    pipe = Pipeline.build(RunConfig(backend="stub"))
    pipe.cfg = pipe.cfg.model_copy(update={"backend": "sam3"})
    fake = pipe.segmenter = _Fake()
    pipe.use_mask_cache(str(tmp_path))
    first, _ = pipe.process_one(src)
    calls = len(fake.calls)
    assert calls > 0
    second, _ = pipe.process_one(src)
    assert len(fake.calls) == calls and np.array_equal(first, second)


def test_cli_adjust(tmp_path):
    _, out = _batch(tmp_path)
    img = str(out / "a.jpg")
    before = _bytes(img)
    assert cli.main(["adjust", img, "--set", "sky.exposure=-0.2"]) == 0
    assert _bytes(img) != before
    assert cli.main(["adjust", img, "--set", "nonsense"]) == 2
    assert cli.main(["adjust", img, "--edit", "brighten the unicorn"]) == 0
    adjs = json.load(open(out / "a.autoedit.json"))["adjustments"]
    assert any(a["status"] == "not_found" for a in adjs)


def test_sidecar_holds_no_local_path(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _make(str(src / "a.jpg"), 1.0)
    ref = tmp_path / "refdir" / "look.jpg"
    ref.parent.mkdir()
    _make(str(ref))
    batch.run(RunConfig(backend="stub", reference=str(ref), prompt="calm morning"), str(src), str(out))
    text = (out / "a.autoedit.json").read_text()
    assert str(src) not in text and str(out) not in text and str(ref.parent) not in text
    d = json.loads(text)
    rc = d["audit"]["run_config"]
    assert not [k for k in rc if k.endswith("_path")]
    assert not {"api_key", "reference", "recipe_model"} & set(rc)
    RunConfig(**rc)
    assert d["audit"]["output"] == "a.jpg"
    assert d["audit"]["run_inputs"] == {"reference": "look.jpg", "recipe_model": None}
    assert isinstance(d["exposure_bias"], float)
