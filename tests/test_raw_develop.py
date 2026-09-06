"""RAW development modes: the EV solve is pure math (tested on synthetic linear arrays), the
default mode is byte-identical to before, JPEG input ignores the decode options, and — only when
a RAW corpus is available (AUTOEDIT_RAW_CORPUS) — a real CR2 lifts without adding clipping."""

import glob
import os

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import RunConfig
from autoedit.imageio import (RAW_MODES, camera_preview, develop_ev, linear_to_srgb, load_image,
                              raw_opts_from, srgb_to_linear)


def _lin(value: float, shape=(40, 40, 3)) -> np.ndarray:
    return np.full(shape, value, np.float32)


def test_preview_modes_match_the_camera_median():
    lin = _lin(0.05)                                   # linear median 0.05
    cam = np.full((20, 20, 3), 0.5, np.float32)        # camera preview: gamma 0.5 → linear ≈ 0.214
    want = np.log2(float(srgb_to_linear(np.float32(0.5))) / 0.05)
    assert abs(develop_ev(lin, cam, {"raw_mode": "preview_ev"}) - want) < 1e-3
    # plenty of headroom in a flat 0.05 frame → the headroom mode asks for the same lift
    assert abs(develop_ev(lin, cam, {"raw_mode": "preview_ev_headroom"}) - want) < 1e-3
    # a lift that would land the 99.5th percentile exactly on white stops 0.15 EV short of it
    tight = _lin(0.05)
    tight[:4] = float(srgb_to_linear(np.float32(0.98))) / 2.0      # 10% of pixels one stop under white
    ev = develop_ev(tight, cam, {"raw_mode": "preview_ev_headroom"})
    assert abs(ev - (1.0 - 0.15)) < 0.02


def test_headroom_caps_the_lift_when_highlights_are_near_white():
    lin = _lin(0.05)
    lin[:2] = 0.90                                     # 5% of pixels already near white (linear)
    cam = np.full((20, 20, 3), 0.5, np.float32)
    uncapped = develop_ev(lin, cam, {"raw_mode": "preview_ev"})
    capped = develop_ev(lin, cam, {"raw_mode": "preview_ev_headroom", "raw_clip_budget": 0.005})
    assert uncapped > 1.5
    assert capped == 0.0                               # p99.5 at 0.90 linear → 0.08 stops, inside the 0.15 margin
    assert capped >= 0.0


def test_tone_target_ignores_camera_and_never_darkens():
    lin = _lin(0.05)
    ev = develop_ev(lin, None, {"raw_mode": "tone_target", "raw_tone_target_p50": 0.40})
    want = np.log2(float(srgb_to_linear(np.float32(0.40))) / 0.05)
    assert abs(ev - want) < 1e-3
    bright = _lin(0.5)                                 # already brighter than the target → 0, not negative
    assert develop_ev(bright, None, {"raw_mode": "tone_target"}) == 0.0


def test_fallback_max_and_none():
    lin = _lin(0.05)
    assert develop_ev(lin, None, {"raw_mode": "preview_ev", "raw_fallback_ev": 1.4}) == 1.4
    assert develop_ev(lin, None, {"raw_mode": "none"}) == 0.0
    assert develop_ev(lin, None, {"raw_mode": "libraw_auto"}) == 0.0
    dark = _lin(0.001)
    cam = np.full((8, 8, 3), 0.9, np.float32)
    assert develop_ev(dark, cam, {"raw_mode": "preview_ev", "raw_max_ev": 2.5}) == 2.5
    try:
        develop_ev(lin, None, {"raw_mode": "bogus"})
        raise AssertionError("unknown mode must raise")
    except ValueError:
        pass


def test_run_config_defaults_to_none_and_exposes_raw_opts():
    cfg = RunConfig()
    assert cfg.raw_mode == "none"
    opts = raw_opts_from(cfg)
    assert opts["raw_mode"] == "none" and set(opts) >= {"raw_max_ev", "raw_clip_budget", "raw_fallback_ev"}
    assert set(RAW_MODES) == {"none", "libraw_auto", "preview_ev", "preview_ev_headroom", "tone_target"}


def test_jpeg_ignores_decode_options(tmp_path):
    a = (np.random.default_rng(1).random((12, 16, 3)) * 255).astype(np.uint8)
    p = tmp_path / "x.jpg"
    PILImage.fromarray(a, "RGB").save(str(p), quality=95)
    plain = load_image(str(p))
    lifted = load_image(str(p), decode={"raw_mode": "preview_ev_headroom"})
    assert np.array_equal(plain.rgb, lifted.rgb) and lifted.decode_ev == 0.0
    assert camera_preview(str(p)) is None


def test_linear_roundtrip_helpers():
    x = np.linspace(0, 1, 11, dtype=np.float32)
    assert np.abs(linear_to_srgb(srgb_to_linear(x)) - x).max() < 1e-5


def test_real_raw_lift_when_corpus_available():
    corpus = os.environ.get("AUTOEDIT_RAW_CORPUS")
    if not corpus:
        return  # opt-in: needs real RAW files (never committed)
    files = sorted(glob.glob(os.path.join(corpus, "*.cr2")) + glob.glob(os.path.join(corpus, "*.CR2"))
                   + glob.glob(os.path.join(corpus, "*.dng")))[:2]
    assert files, f"no RAW files in {corpus}"
    from autoedit.imageio import luma

    for f in files:
        base = load_image(f, decode={"raw_mode": "none", "half_size": True})
        lift = load_image(f, decode={"raw_mode": "preview_ev_headroom", "half_size": True})
        assert base.decode_ev == 0.0 and lift.decode_ev >= 0.0
        if lift.decode_ev > 0.05:
            assert luma(lift.rgb).mean() > luma(base.rgb).mean()
        # the headroom mode may not add more than the clip budget of new white pixels
        assert (luma(lift.rgb) > 0.98).mean() <= (luma(base.rgb) > 0.98).mean() + 0.006
