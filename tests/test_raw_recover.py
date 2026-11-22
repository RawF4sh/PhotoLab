"""F44 RAW highlight recovery: LibRaw's curve, the shoulder and the rebuild are pure math (tested on
synthetic frames); `_decode_raw` is driven through a fake rawpy; the real-corpus check is opt-in
(AUTOEDIT_RAW_CORPUS)."""

import glob
import os
import sys
import types

import numpy as np

from autoedit import imageio
from autoedit.imageio import (_BT709_G2, _BT709_G4, libraw_curve, libraw_curve_inv, load_image,
                              recover_highlights, shoulder)


def _frame():
    rgb = np.zeros((40, 60, 3), np.float32)
    rgb[:, :30] = np.linspace(0.30, 0.95, 40, dtype=np.float32)[:, None, None]   # grey ramp, partly above the knee
    rgb[:, 30:] = np.array([1.0, 1.0, 0.9], np.float32)                          # red and green clipped
    clipped = rgb.max(-1) >= 1.0
    v = np.linspace(1.0, 1.6, 30, dtype=np.float32)                              # what the RAW holds, along x
    rebuilt = np.repeat(np.tile(v, 40)[:, None], 3, axis=1)                      # neutral, row-major like rgb[clipped]
    return rgb, rebuilt, clipped, v


# 1 ---------------------------------------------------------------------------------------------
def test_libraw_curve_values_and_inverse():
    assert abs(_BT709_G2 - 0.0812257) < 1e-6 and abs(_BT709_G4 - 0.0992578) < 1e-6
    for x, y in [(0, 0), (0.018, 0.081), (0.18, 0.408830), (0.5, 0.705421), (0.75, 0.866507), (1, 1)]:
        assert abs(float(libraw_curve(x)) - y) < 1e-5, (x, y)
    v = np.linspace(0.0, 1.0, 1001)
    assert np.abs(libraw_curve(libraw_curve_inv(v)) - v).max() < 1e-6
    brk = _BT709_G2 / 4.5                                    # the two segments meet at the break
    toe = brk * 4.5
    power = brk ** (1 / 2.222) * (1 + _BT709_G4) - _BT709_G4
    assert abs(toe - power) < 1e-6


# 2 ---------------------------------------------------------------------------------------------
def test_libraw_curve_matches_its_own_table():
    table = {100: 450, 1000: 4500, 1182: 5319, 1183: 5323, 10000: 24407, 32768: 46230,
             60000: 62730, 65535: 65535}
    for i, want in table.items():
        got = min(int(np.floor(65536 * float(libraw_curve(i / 65536)))), 65535)
        assert abs(got - want) <= 1, (i, got, want)


# 3 ---------------------------------------------------------------------------------------------
def test_shoulder_is_identity_below_the_knee_and_one_at_the_top():
    knee, top = 0.75, 1.5
    m = np.linspace(0.0, knee, 200)
    assert np.array_equal(shoulder(m, knee, top), m)
    assert abs(float(shoulder(np.float64(top), knee, top)) - 1.0) < 1e-9
    slope = (float(shoulder(np.float64(knee + 1e-6), knee, top)) - knee) / 1e-6
    assert 0.999 <= slope <= 1.0
    up = shoulder(np.linspace(knee, top, 500), knee, top)
    assert (np.diff(up) > 0).all()
    x = np.array([0.9, 1.2])
    assert shoulder(x, knee, 1.0) is x


# 4 ---------------------------------------------------------------------------------------------
def test_recover_highlights_on_the_synthetic_frame():
    rgb, rebuilt, clipped, v = _frame()
    out, top = recover_highlights(rgb, rebuilt, clipped)
    assert abs(top - 1.6) < 1e-3
    low = rgb.max(-1) <= float(libraw_curve(0.75))
    assert np.array_equal(out[low], rgb[low])
    assert out.max() <= 1.0
    luma = out[:, 30:] @ imageio._LUMA
    assert (np.diff(luma, axis=1) > 0).all()
    assert np.allclose(out[:, -1], 1.0, atol=1e-6)
    assert (out[:, 30] < 1.0).all()
    neutral = out[:, 30:][:, v >= 1.25]
    assert np.abs(neutral - neutral[..., :1]).max() <= 1e-6
    ramp_in, ramp_out = rgb[:, :30], out[:, :30]
    assert (np.diff(ramp_out[..., 0], axis=0) >= 0).all()
    assert (ramp_out <= ramp_in).all()
    flat = np.full_like(rebuilt, 0.9)
    same, top = recover_highlights(rgb, flat, clipped)
    assert same is rgb and top == 1.0
    # unchanged by the edge fade: on this 60 px frame r is 1, full strength from 1 px in, v passes 1.25 13 px in


def test_saturated_clip_edge_keeps_its_colour():
    sky = np.array([0.53, 0.88, 1.0], np.float32)
    rgb = np.full((20, 500, 3), 0.2, np.float32)
    rgb[:, 100:] = libraw_curve(sky)
    clipped = rgb.max(-1) >= 1.0
    rebuilt = np.tile(np.array([0.6, 0.95, 1.5], np.float32), (int(clipped.sum()), 1))
    out, top = recover_highlights(rgb, rebuilt, clipped)
    assert top > 1.0
    lin = libraw_curve_inv(out[0])
    ratio = lin / lin.max(-1, keepdims=True)
    assert np.abs(ratio[100] - sky / sky.max()).max() < 0.05
    edge = 100 + round(imageio._RECOVER_EDGE * 500)
    assert np.abs(ratio[edge:] - ratio[edge]).max() <= 1e-5
    chroma = out[0, 100:].max(-1) - out[0, 100:].min(-1)
    assert (np.diff(chroma) <= 1e-6).all()
    assert np.array_equal(out[:, :100], rgb[:, :100])


# 5 ---------------------------------------------------------------------------------------------
def _fake_rawpy(default16, blend16, calls, wb=(2.0, 1.0, 1.5, 1.0)):
    class Raw:
        camera_whitebalance = list(wb)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def postprocess(self, **kw):
            calls.append(kw)
            return blend16 if kw.get("highlight_mode") == 2 else default16

    return types.SimpleNamespace(imread=lambda path: Raw())


def _decode(opts, default16, blend16=None, calls=None):
    calls = [] if calls is None else calls
    previous = sys.modules.get("rawpy")
    sys.modules["rawpy"] = _fake_rawpy(default16, blend16, calls)
    try:
        return load_image("missing.cr2", opts), calls
    finally:
        if previous is None:
            sys.modules.pop("rawpy", None)
        else:
            sys.modules["rawpy"] = previous


def _frames16():
    rgb, rebuilt, clipped, v = _frame()
    default16 = np.rint(rgb * 65535.0).astype(np.uint16)
    default16[clipped] = np.where(rgb[clipped] >= 1.0, 65535, default16[clipped])
    blend16 = np.zeros_like(default16)
    blend16[clipped] = np.rint(rebuilt / 2.0 * 65535.0).astype(np.uint16)       # wb max/min = 2
    return default16, blend16, clipped


def test_decode_raw_recovery_through_a_fake_rawpy():
    default16, blend16, clipped = _frames16()
    off, calls = _decode({"raw_mode": "none"}, default16, blend16)
    assert len(calls) == 1 and "highlight_mode" not in calls[0]
    assert calls[0]["gamma"] == (2.222, 4.5) and calls[0]["no_auto_bright"]
    assert np.array_equal(off.rgb, default16.astype(np.float32) / 65535.0)
    assert "highlight" not in off.decode_info and "recover_skipped" not in off.decode_info

    # no channel at 65535: one call, the same pixels
    soft = np.minimum(default16, 60000).astype(np.uint16)
    img, calls = _decode({"raw_mode": "none", "raw_recover": True}, soft, blend16)
    assert len(calls) == 1 and np.array_equal(img.rgb, soft.astype(np.float32) / 65535.0)

    # the synthetic frame: a second, blend decode
    img, calls = _decode({"raw_mode": "none", "raw_recover": True}, default16, blend16)
    assert len(calls) == 2
    c2 = calls[1]
    assert c2["highlight_mode"] == 2 and c2["gamma"] == (1.0, 1.0) and c2["no_auto_bright"]
    assert img.decode_info["highlight"] == "recover" and abs(img.decode_info["recover_top"] - 1.6) < 1e-2
    low = off.rgb.max(-1) <= float(libraw_curve(0.75))
    assert np.array_equal(img.rgb[low], off.rgb[low])
    assert img.rgb.max() <= 1.0 and (img.rgb != off.rgb).any()

    # one clipped pixel in 2400 (0.04 %): below the floor
    one = np.full((40, 60, 3), 20000, np.uint16)
    one[0, 0] = 65535
    img, calls = _decode({"raw_mode": "none", "raw_recover": True}, one, blend16)
    assert len(calls) == 1 and "0.2%" in img.decode_info["recover_skipped"]
    assert np.array_equal(img.rgb, one.astype(np.float32) / 65535.0)

    # another raw_mode keeps its own highlights
    img, calls = _decode({"raw_mode": "linear_srgb", "raw_recover": True}, default16, blend16)
    assert all("highlight_mode" not in c for c in calls)
    assert "linear_srgb" in img.decode_info["recover_skipped"]


# 7 ---------------------------------------------------------------------------------------------
def test_real_raw_recovery_when_corpus_available():
    corpus = os.environ.get("AUTOEDIT_RAW_CORPUS")
    if not corpus:
        return  # opt-in: needs real RAW files (never committed)
    files = sorted(glob.glob(os.path.join(corpus, "*.cr2")) + glob.glob(os.path.join(corpus, "*.CR2"))
                   + glob.glob(os.path.join(corpus, "*.dng")))[:2]
    assert files, f"no RAW files in {corpus}"
    for f in files:
        off = load_image(f, decode={"raw_mode": "none", "half_size": True})
        on = load_image(f, decode={"raw_mode": "none", "raw_recover": True, "half_size": True})
        low = off.rgb.max(-1) <= float(libraw_curve(0.75))
        assert np.array_equal(on.rgb[low], off.rgb[low])
        assert abs(float(np.median(imageio.luma(on.rgb))) - float(np.median(imageio.luma(off.rgb)))) < 1 / 255
        white = lambda a: float((a.min(-1) >= 0.985).mean())
        assert white(on.rgb) <= white(off.rgb) + 1e-9


# 6 ---------------------------------------------------------------------------------------------
def test_cli_and_api_carry_the_setting(tmp_path):
    import argparse

    from autoedit import cli
    from autoedit.contract import RunConfig
    from autoedit.web import service

    p = argparse.ArgumentParser()
    cli._add_run_flags(p)
    ns = p.parse_args(["in", "--raw-recover"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).raw_recover is True
    ns = p.parse_args(["in"])
    ns.configs_dir = ns.config = None
    assert cli.load_run_config(ns).raw_recover is False
    conf = tmp_path / "run.toml"
    conf.write_text("[decode]\nrecover = true\n")
    ns.config = str(conf)
    assert cli.load_run_config(ns).raw_recover is True
    assert service.make_cfg({"raw_recover": True}).raw_recover is True
    assert RunConfig().raw_recover is False
    assert imageio.raw_opts_from(RunConfig(raw_recover=True))["raw_recover"] is True


def test_recovered_share_drives_the_highlight_rules():
    # Recovered pixels sit under white, so only `recovered_highlight` tells the rules to hold them.
    import pathlib
    from autoedit import rules, stats
    from autoedit.imageio import Image
    rgb = np.full((40, 60, 3), 0.4, np.float32)
    rgb[:, :12] = 0.95                                        # recovered sky, under the clip level
    plain, _ = stats.compute(Image(rgb=rgb), {})
    rec, _ = stats.compute(Image(rgb=rgb, decode_info={"highlight": "recover", "recover_clipped": 0.2}), {})
    skipped, _ = stats.compute(Image(rgb=rgb, decode_info={"highlight": "clip", "recover_clipped": 0.2}), {})
    assert plain.recovered_highlight == skipped.recovered_highlight == 0.0 and rec.recovered_highlight == 0.2
    rs = rules.load(str(pathlib.Path(rules.__file__).parent / "configs" / "rules.toml"))
    g_plain, _ = rules.apply(rs, "landscape", plain, {}, {}, 1.0)
    g_rec, _ = rules.apply(rs, "landscape", rec, {}, {}, 1.0)
    assert g_rec["highlights"] < g_plain.get("highlights", 0.0) and g_rec["whites"] < g_plain.get("whites", 0.0)


def test_recovery_keeps_the_default_headroom():
    # The shoulder darkens the unclipped top; stats undoes it, so headroom is what the default decode had.
    from autoedit import stats
    from autoedit.imageio import Image, _RECOVER_KNEE as k, unshoulder
    for x in (0.5, 0.8, 0.95, 1.0):
        assert abs(unshoulder(float(shoulder(np.float64(x), k, 1.5)), k, 1.5) - x) < 1e-6
    assert unshoulder(1.0, k, 1.5) == 1.0 and unshoulder(0.9, k, 1.0) == 0.9
    default = np.full((40, 60, 3), 0.4, np.float32)
    default[:, :12] = libraw_curve(0.85)                      # bright, unclipped sky
    recovered = default.copy()
    recovered[:, :12] = libraw_curve(shoulder(np.float64(0.85), k, 1.5))
    def head(rgb, info):
        return stats.compute(Image(rgb=rgb, decode_info=info), {})[0].headroom_ev
    want = head(default, {})
    assert head(recovered, {}) > want + 0.01                   # the shoulder alone would add headroom
    assert abs(head(recovered, {"highlight": "recover", "recover_top": 1.5}) - want) < 1e-3
    assert head(recovered, {"highlight": "clip", "recover_top": 1.5}) == head(recovered, {})
