"""F8: backend "auto" and the look flag preserve_exposure. No default changes."""

import argparse
import importlib.util
import json
import os
import tomllib

import numpy as np
from PIL import Image as PILImage

from autoedit import batch, cli, looks, paths, segment
from autoedit.contract import Adjustment, RunConfig
from autoedit.pipeline import Pipeline


def _make(path, gain=1.0):
    a = np.zeros((128, 128, 3), np.float32)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    a += np.random.default_rng(0).normal(0, 12, a.shape).astype(np.float32)   # texture: no banding flag
    PILImage.fromarray(np.clip(a * gain, 0, 255).astype(np.uint8), "RGB").save(path, quality=95)


class _Ready:
    """Replace segment.sam3_ready; put the original back on exit."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.old = segment.sam3_ready
        segment.sam3_ready = lambda *a, **k: self.value

    def __exit__(self, *exc):
        segment.sam3_ready = self.old


def test_auto_resolves_to_the_stub_without_weights(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _make(str(src / "a.jpg"))
    with _Ready(False):
        assert segment.resolve_backend("auto") == "stub"
        assert Pipeline.build(RunConfig(backend="auto")).cfg.backend == "stub"
        batch.run(RunConfig(backend="auto"), str(src), str(out))
    assert json.load(open(out / "run.json"))["backend"] == "stub"
    sc = json.load(open(out / "a.autoedit.json"))
    assert sc["audit"]["run_config"]["backend"] == "stub"


def test_auto_resolves_to_sam3_when_ready():
    with _Ready(True):
        assert segment.resolve_backend("auto") == "sam3"
        assert segment.resolve_backend("stub") == "stub"
        assert segment.resolve_backend("sam3") == "sam3"
    with _Ready(False):
        assert segment.resolve_backend("sam3") == "sam3"
        assert segment.resolve_backend("stub") == "stub"


def test_sam3_ready_never_raises():
    old = importlib.util.find_spec

    def boom(*a, **k):
        raise RuntimeError("boom")

    importlib.util.find_spec = boom
    try:
        assert segment.sam3_ready() is False
    finally:
        importlib.util.find_spec = old


def test_sam3_needs_the_model_code_and_the_weights(tmp_path):
    # Each check is tested while all the others pass, so removing any one of them fails a line.
    from autoedit.segment import sam3 as sam3_mod
    seen = {"transformers.models.sam3": None}
    old = importlib.util.find_spec
    probe = sam3_mod._IMPORTABLE
    importlib.util.find_spec = lambda name, *a, **k: seen.get(name, object())
    sam3_mod._IMPORTABLE = True        # the import check passes: it has its own line at the end
    try:
        cache = tmp_path / "hub"
        snap = cache / "models--facebook--sam3" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (cache / "models--facebook--sam3" / "refs").mkdir()
        (cache / "models--facebook--sam3" / "refs" / "main").write_text("abc")
        (snap / "config.json").write_text("{}")
        (snap / "model.safetensors").write_text("x")
        # weights in the cache, but this transformers has no SAM 3: not ready, so "auto" is the stub
        assert segment.sam3_ready(cache_dir=str(cache)) is False
        assert segment.resolve_backend("auto") == "stub"
        # the model code is there, the config is cached, the weights are not: it would download
        del seen["transformers.models.sam3"]
        (snap / "model.safetensors").unlink()
        assert segment.sam3_ready(cache_dir=str(cache)) is False
        (snap / "model.safetensors").write_text("x")
        assert segment.sam3_ready(cache_dir=str(cache)) is True
        # Everything is in place; the last word has the import itself. On one machine of this
        # project the packages are installed and the import still fails (a broken binary in a
        # dependency), and "auto" must then be the stub, not a backend that fails every photo.
        sam3_mod._IMPORTABLE = False
        assert segment.sam3_ready(cache_dir=str(cache)) is False
    finally:
        sam3_mod._IMPORTABLE = probe
        importlib.util.find_spec = old


def test_make_segmenter_auto_is_the_stub_when_not_ready():
    from autoedit.segment import StubSegmenter
    with _Ready(False):
        assert isinstance(segment.make_segmenter("auto"), StubSegmenter)


def test_default_backend_is_unchanged():
    assert RunConfig().backend == "stub"
    with open(os.path.join(paths.configs_dir(), "defaults.toml"), "rb") as fh:
        assert tomllib.load(fh)["segment"]["backend"] == "stub"


def test_look_flag_opts_out_of_the_darkness_check(tmp_path):
    text = open(os.path.join(paths.configs_dir(), "looks.toml")).read()
    # natural's own values plus the flag, nothing else: the flag is the only difference
    body = text.split("[natural]")[1].split("\n[")[0]
    lp = tmp_path / "looks.toml"
    lp.write_text(text + "\n[moody_test]" + body + "\npreserve_exposure = true\n")
    src = tmp_path / "dark.jpg"
    _make(str(src), gain=0.3)
    adj = [Adjustment(target="global", ops={"exposure": -1.5})]
    res, pix = {}, {}
    for name in ("natural", "moody_test"):
        pipe = Pipeline.build(RunConfig(looks_path=str(lp), look=name))
        pix[name], res[name] = pipe.process_one(str(src), adjustments=adj)
    ls = looks.load(str(lp))
    assert ls["moody_test"].preserve_exposure and not ls["natural"].preserve_exposure
    assert {**vars(ls["moody_test"]), "preserve_exposure": False} == vars(ls["natural"])
    assert res["natural"].needs_review and "darker" in res["natural"].review_reason
    assert "darker" not in res["moody_test"].review_reason
    # the flag alone changes no pixel
    assert np.array_equal(pix["natural"], pix["moody_test"])


def test_no_packaged_look_sets_the_flag():
    ls = looks.load(os.path.join(paths.configs_dir(), "looks.toml"))
    assert ls and all(l.preserve_exposure is False for l in ls.values())


def test_cli_accepts_auto():
    parser = argparse.ArgumentParser()
    cli._add_run_flags(parser)
    args = parser.parse_args(["in", "--backend", "auto"])
    args.configs_dir = None
    assert cli.load_run_config(args).backend == "auto"


def test_the_web_service_resolves_auto():
    # The service builds its own segmenter from cfg.backend, so "auto" must not reach it.
    from autoedit.web import service
    with _Ready(False):
        assert service.make_cfg({"backend": "auto"}).backend == "stub"
    with _Ready(True):
        assert service.make_cfg({"backend": "auto"}).backend == "sam3"
    assert service.make_cfg({}).backend == "stub"
