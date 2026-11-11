"""Style profiles + brief: loading, brief combination, look-driving, concept resolution."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import RunConfig
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.styles import Style, combine_brief, load_styles, style_for


def _make(path):
    a = np.zeros((128, 128, 3), np.uint8)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    yy, xx = np.mgrid[0:128, 0:128]
    ell = (((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0
    a[ell] = [200, 150, 120]
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def test_styles_load():
    styles = load_styles(config_path("styles.toml"))
    assert "natural" in styles and "editorial" in styles
    assert styles["natural"].look == "natural"
    assert styles["editorial"].brief  # non-empty


def test_combine_brief():
    assert combine_brief("warm film", "lift shadows") == "warm film. lift shadows"
    assert combine_brief("warm film", None) == "warm film"
    assert combine_brief("", None) is None


def test_style_for_unknown_falls_back():
    s = style_for("does-not-exist", {})
    assert isinstance(s, Style) and s.look == "natural"


def test_concept_resolution_priority():
    pipe = Pipeline.build(RunConfig(backend="stub"))
    style = Style(name="x", concepts=["subject", "sky"])
    # explicit concepts win
    pipe.cfg.concepts = ["foliage"]
    assert pipe.concepts_for(style) == ["foliage"]
    # Explicitly turning every region off must not restore the style's defaults.
    pipe.cfg.concepts = []
    assert pipe.concepts_for(style) == []
    # then preset
    pipe.cfg.concepts = None
    pipe.cfg.preset = "wedding"
    assert "dress" in pipe.concepts_for(style)
    # then the style's own set
    pipe.cfg.preset = None
    assert pipe.concepts_for(style) == ["subject", "sky"]


def test_style_changes_the_look(tmp_path):
    img = tmp_path / "p.jpg"
    _make(str(img))
    g_natural = Pipeline.build(RunConfig(backend="stub", style="natural")).process_one(str(img), False)[1].global_settings
    g_editorial = Pipeline.build(RunConfig(backend="stub", style="editorial")).process_one(str(img), False)[1].global_settings
    # editorial -> punchy look adds vibrance, so the global vibrance must differ
    assert g_natural.get("vibrance") != g_editorial.get("vibrance")


def test_look_exposure_and_curve_multipliers():
    """`exposure_mul` holds back only the rules' exposure LIFT and `curve_mul` scales the rules' own
    curve before `curve_add`; both default to 1.0, so a looks.toml without them is unchanged."""
    from autoedit.looks import Look, load as load_looks, shape

    base = {"exposure": 0.8, "curve": 20.0, "contrast": 5.0}
    plain = dict(base)
    shape(plain, Look(), 1.0, is_global=True)
    assert plain == base                                           # defaults are the identity

    moody = dict(base)
    shape(moody, Look(exposure_mul=0.5, curve_mul=1.5, curve_add=4.0), 0.8, is_global=True)
    assert abs(moody["exposure"] - 0.8 * 0.5 * 0.8) < 1e-9          # lift halved, then intensity
    assert abs(moody["curve"] - (20.0 * 1.5 + 4.0) * 0.8) < 1e-9    # rule curve scaled, look curve added
    assert abs(moody["contrast"] - 5.0 * 0.8) < 1e-9                # untouched ops only see intensity

    cut = {"exposure": -0.2}
    shape(cut, Look(exposure_mul=0.5), 1.0, is_global=True)
    assert cut["exposure"] == -0.2                                  # a darkening rule is never weakened

    region = {"exposure": 0.4}
    shape(region, Look(exposure_mul=0.5), 1.0)                      # regional lifts follow the same mood
    assert region["exposure"] == 0.2

    looks = load_looks(config_path("looks.toml"))
    assert looks["natural"].exposure_mul == 1.0 and looks["natural"].curve_mul == 1.0


def test_cinematic_preserved_gets_depth_from_the_rules_not_from_colour():
    """Golden (improvement loop iter-11). The look follows the owner's notes on the reference set:
    no cool shadow grade ("too blue"), no saturation push ("too saturated"), no extra dehaze. Its
    depth comes from `curve_mul` on the rules' lift-scaled curve, so it deepens a photo the rules
    lift and leaves an unlifted (possibly dark) photo's curve exactly where `natural` has it — the
    previous unconditional curve/dehaze made dark photos darker."""
    from autoedit.contract import GlobalStatsModel
    from autoedit.looks import load as load_looks, shape
    from autoedit.rules import apply as rules_apply, load as load_rules

    looks = load_looks(config_path("looks.toml"))
    cine, natural = looks["cinematic_preserved"], looks["natural"]
    assert cine.grade_shadows_temp == 0.0 and cine.grade_shadows_tint == 0.0
    assert cine.saturation_add == 0.0 and cine.vibrance_add <= 4.0
    assert cine.dehaze_mul <= 1.0 and cine.exposure_mul == 1.0
    assert cine.curve_mul > 1.0 and cine.curve_add == natural.curve_add

    rules = load_rules(config_path("rules.toml"))

    def curve_for(look, **stats):
        base = dict(mean_lum=0.40, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.2,
                    pro_stats_valid=1.0, lum_p10=0.12, lum_p50=0.40, lum_p90=0.55, dark_cov=0.05)
        g, _ = rules_apply(rules, "landscape", GlobalStatsModel(**{**base, **stats}), {}, {}, 0.8)
        shape(g, look, 0.8, is_global=True)
        return g["curve"]

    lifted = dict(fg_p50=0.15, headroom_ev=2.0)                 # the rules spend +0.8 EV here
    assert curve_for(cine, **lifted) > curve_for(natural, **lifted) + 10
    unlifted = dict(fg_p50=0.27, headroom_ev=0.0, lum_p10=0.17)  # headroom-limited, dark foreground
    assert curve_for(cine, **unlifted) == curve_for(natural, **unlifted)

