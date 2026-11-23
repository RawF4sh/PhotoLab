"""F51 fast analysis: a RAW file's analysis picture is its half-size development; the export still
develops at full size. Driven through a fake rawpy (postprocess records half_size)."""

import sys
import types

import numpy as np
from PIL import Image as PILImage

from autoedit import consistency, imageio, pipeline
from autoedit.contract import RunConfig
from autoedit.imageio import analysis_opts, load_image
from autoedit.web import service

FULL = np.full((64, 96, 3), 20000, np.uint16)
HALF = np.full((32, 48, 3), 50000, np.uint16)


class _Fake:
    def __init__(self):
        self.calls = []
        outer = self

        class Raw:
            camera_whitebalance = [2.0, 1.0, 1.5, 1.0]
            sizes = types.SimpleNamespace(width=96, height=64)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def postprocess(self, **kw):
                outer.calls.append(bool(kw.get("half_size")))
                return HALF if kw.get("half_size") else FULL

        self.mod = types.SimpleNamespace(imread=lambda path: Raw())

    def __enter__(self):
        self.prev = sys.modules.get("rawpy")
        sys.modules["rawpy"] = self.mod
        return self

    def __exit__(self, *a):
        if self.prev is None:
            sys.modules.pop("rawpy", None)
        else:
            sys.modules["rawpy"] = self.prev


def _raw(tmp_path):
    p = tmp_path / "x.cr2"
    p.write_bytes(b"fake")
    return str(p)


def test_half_size_decode_skips_lens_and_says_so(tmp_path):
    p = _raw(tmp_path)
    with _Fake():
        half = load_image(p, {"lens_ca": True, "half_size": True})
        full = load_image(p, {})
    assert "lens" not in half.decode_info and half.decode_info["lens_skipped"] and half.decode_info["half_size"]
    assert half.rgb.shape == (32, 48, 3) and np.allclose(half.rgb, 50000 / 65535.0)
    assert "half_size" not in full.decode_info
    png = tmp_path / "a.png"
    PILImage.fromarray(np.full((40, 40, 3), 90, np.uint8)).save(png)
    assert "lens" in load_image(str(png), {"lens_ca": True, "half_size": True}).decode_info


def test_process_one_decodes_full_for_apply_and_half_for_analysis(tmp_path):
    p = _raw(tmp_path)
    off = pipeline.Pipeline.build(RunConfig(backend="stub"))
    on = pipeline.Pipeline.build(RunConfig(backend="stub", fast_analysis=True))
    with _Fake() as f:
        off.process_one(p)
        assert f.calls == [False]
        f.calls.clear()
        rgb, res = on.process_one(p)
        assert f.calls == [True, False]   # the half-size analysis picture first, so a full-size DNG is decoded once
        assert rgb.shape[:2] == (64, 96)
        assert abs(res.global_stats.mean_lum - 50000 / 65535.0) < 0.02
        assert any("fast analysis" in n for n in res.notes)
        f.calls.clear()
        on.process_one(p, do_apply=False)
        assert f.calls == [True]


def test_presegment_and_brightness_plan_decode_half(tmp_path):
    p = _raw(tmp_path)
    pl = pipeline.Pipeline.build(RunConfig(backend="stub", fast_analysis=True))
    with _Fake() as f:
        pl.presegment(p)
        consistency._brightness_plan([p], half=True)
    assert f.calls and all(f.calls)


def test_preview_cache_key_includes_the_mode(tmp_path):
    p = _raw(tmp_path)
    service._PREVIEW_CACHE.clear()
    with _Fake() as f:
        service._preview_images(p, RunConfig(fast_analysis=True), 2560)
        assert f.calls == [True]
        service._preview_images(p, RunConfig(), 2560)
        assert f.calls == [True, False]
    service._PREVIEW_CACHE.clear()


def test_setting_reaches_runconfig(tmp_path):
    from autoedit import cli
    p = cli.argparse.ArgumentParser()
    cli._add_run_flags(p)
    def cfg(*args):
        ns = p.parse_args(["in", *args])
        ns.configs_dir = ns.config = None
        if "--config" in args:
            ns.config = args[-1]
        return cli.load_run_config(ns)
    assert cfg().fast_analysis is False
    assert cfg("--fast-analysis").fast_analysis is True
    conf = tmp_path / "c.toml"
    conf.write_text("[decode]\nfast_analysis = true\n")
    assert cfg("--config", str(conf)).fast_analysis is True
    assert service.make_cfg({"fast_analysis": True}).fast_analysis is True
    assert analysis_opts(RunConfig(), "x.cr2") is None and analysis_opts(RunConfig(fast_analysis=True), "x.jpg") is None


def test_linear_dng_full_size_counts_as_full(tmp_path):
    """Asked for half size, LibRaw returned full size: no half_size flag, lens_ca runs, no skip note."""
    p = _raw(tmp_path)
    with _Fake() as fk:
        fk_mod = fk.mod
        full_postprocess = lambda self, **kw: FULL
        type(fk_mod.imread(p)).postprocess = full_postprocess
        img = load_image(p, {"lens_ca": True, "half_size": True})
    assert "half_size" not in img.decode_info and "lens_skipped" not in img.decode_info and "lens" in img.decode_info
