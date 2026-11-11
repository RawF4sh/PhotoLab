"""Apply-engine op math: identity no-op, exposure, saturation."""

import numpy as np

from autoedit.edit import apply_ops
from autoedit.edit.ops import vibrance
from autoedit.imageio import srgb_to_linear


def test_identity_is_noop():
    rng = np.random.default_rng(0)
    img = rng.random((24, 24, 3)).astype(np.float32)
    out = apply_ops(img, {})
    assert np.abs(out - img).max() == 0.0


def test_exposure_doubles_linear_on_dark_patch():
    img = np.full((8, 8, 3), 0.2, np.float32)  # dark enough that +1 stop won't clip
    out = apply_ops(img, {"exposure": 1.0})
    ratio = srgb_to_linear(out).mean() / srgb_to_linear(img).mean()
    assert abs(ratio - 2.0) < 0.02


def test_saturation_increases_chroma():
    img = np.zeros((4, 4, 3), np.float32)
    img[..., 0], img[..., 1], img[..., 2] = 0.6, 0.4, 0.4  # reddish, some chroma
    out = vibrance(img, vib=0.0, sat=50.0)
    chroma_in = img.max(axis=2) - img.min(axis=2)
    chroma_out = out.max(axis=2) - out.min(axis=2)
    assert chroma_out.mean() > chroma_in.mean()


def test_negative_saturation_reduces_chroma():
    img = np.zeros((4, 4, 3), np.float32)
    img[..., 0], img[..., 1], img[..., 2] = 0.6, 0.4, 0.4
    out = vibrance(img, vib=0.0, sat=-100.0)
    assert (out.max(axis=2) - out.min(axis=2)).mean() < 1e-5


def test_shadow_lift_pins_black_and_is_a_bounded_monotonic_gain():
    """`shadows_ev` math: 2**(ev·w), w = (1 − l/0.5)². Black stays black, mid-grey and above are
    untouched, the deep shadows gain ~ev stops (in the gamma working space), the result is
    monotonic and the gain never exceeds 2**ev (so near-black noise cannot blow up)."""
    from autoedit.edit.ops import OP_BOUNDS, OP_NAMES, shadow_lift

    ramp = np.repeat(np.linspace(0.0, 1.0, 1001, dtype=np.float32)[None, :, None], 3, axis=2)
    assert shadow_lift(ramp, 0.0) is ramp                                  # identity, no copy
    for ev in (0.5, 1.5, OP_BOUNDS["shadows_ev"][1]):
        out = shadow_lift(ramp, ev)[0, :, 0]
        src = ramp[0, :, 0]
        assert out[0] == 0.0                                               # black is pinned
        assert np.abs(out[src >= 0.5] - src[src >= 0.5]).max() == 0.0      # mids/highlights untouched
        assert (np.diff(out) > 0).all()                                    # monotonic: no tone inversion
        assert (out[1:] / src[1:]).max() <= 2.0 ** ev + 1e-4               # bounded gain
        assert abs(out[10] / src[10] - 2.0 ** (ev * (1 - 0.01 / 0.5) ** 2)) < 1e-3   # the formula, at l = 0.01
    darker = shadow_lift(ramp, -1.0)[0, :, 0]                              # negative deepens, still pinned
    assert darker[0] == 0.0 and (darker <= ramp[0, :, 0] + 1e-7).all() and (np.diff(darker) > 0).all()

    # colour: a gain on RGB keeps the hue/saturation ratios of a dark pixel
    px = np.array([[[0.06, 0.04, 0.02]]], np.float32)
    lifted = shadow_lift(px, 1.0)
    assert np.allclose(lifted / px, lifted[0, 0, 0] / px[0, 0, 0], rtol=1e-5)

    # wired into the frozen order, between exposure and the additive tone sliders
    assert "shadows_ev" in OP_NAMES
    img = np.full((8, 8, 3), 0.1, np.float32)
    assert np.allclose(apply_ops(img, {"shadows_ev": 1.0}), shadow_lift(img, 1.0))
    assert np.abs(apply_ops(img, {"shadows_ev": 0.0}) - img).max() == 0.0
