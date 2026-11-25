"""F62 landscape scenes, shoot consistency, and byte-exact Simple fallbacks."""
import tomllib

import numpy as np
import pytest

from autoedit import consistency, scene
from autoedit.contract import GlobalStatsModel, RegionStatsModel, RunConfig
from autoedit.edit import style_target
from autoedit.imageio import Image
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline


def _stats(**kw):
    defaults = dict(mean_lum=.40, mean_sat=.22, shadow_clip=0, highlight_clip=0, haze=0,
                    sky_cov=.30, sky_lum=.72, sky_sat=.36, lum_p10=.12, lum_p50=.41, lum_p90=.79)
    return GlobalStatsModel(**{**defaults, **kw})


def _thresholds():
    with open(config_path("auto.toml"), "rb") as fh:
        return tomllib.load(fh)["scenes"]


def test_landscape_light_signatures_ambiguity_and_override():
    cfg = _thresholds()
    samples = [("sunset", _stats(), .12, .25), ("twilight", _stats(sky_lum=.38, lum_p50=.19, lum_p90=.43), -.12, 0),
               ("daylight", _stats(), -.06, 0), ("mist", _stats(veil=.5), 0, 0),
               ("landscape", _stats(sky_cov=0, lum_p50=.24), 0, 0)]
    for name, stats, warmth, warm_share in samples:
        sc = scene.classify(stats, {"sky"}, thresholds=cfg, sky_warmth=warmth, warm_share=warm_share)
        assert sc.name == "landscape" and sc.finer == name and sc.reason
        assert (sc.confidence < cfg["min_confidence"]) == (name == "landscape")
    sc = scene.classify(_stats(), {"sky"}, override="mist", thresholds=cfg)
    assert (sc.name, sc.finer, sc.confidence) == ("landscape", "mist", 1.0)


def test_simple_three_scene_golden():
    skin = RegionStatsModel(name="skin", lum=.4, sat=.3, clip_low=0, clip_high=0, coverage=.12)
    for present, regions, name, confidence in [({"sky"}, {}, "landscape", .6),
                                               ({"skin", "sky"}, {"skin": skin}, "people", .7),
                                               ({"desk"}, {}, "other", .5)]:
        result = scene.classify(_stats(), present, regions)
        assert (result.name, result.confidence, result.finer) == (name, confidence, "")


def test_shoot_mist_consensus_preserves_sunset_twilight_transition_and_people():
    seq = list(zip("abcdefgh", ["mist", "daylight", "mist", "people", "sunset", "landscape", "twilight", "twilight"]))
    assert consistency.shoot_scenes(seq) == [("mist", ["a", "b", "c"]), ("sunset", ["e", "f"]),
                                            ("twilight", ["g", "h"])]
    cases = [{"global": {"exposure": .4}, "sky": {"exposure": -.6}}, {"global": {"exposure": .5}},
             {"global": {"exposure": .6}, "sky": {"exposure": -.8}}]
    medians = consistency.median_fit(cases)
    results = [consistency.pull_fit(plan["global"], {}, medians, .5, {"exposure": 2})[0]["exposure"]
               for plan in cases]
    assert np.allclose(results, [.45, .5, .55]) and medians["sky"]["exposure"] == -.7


def _synthetic():
    # Smooth moderately underexposed scene, with enough highlight/headroom and a coloured sky.
    lum = np.linspace(.11, .42, 120, dtype=np.float32)[None, :]
    rgb = np.zeros((80, 120, 3), np.float32) + lum[..., None]
    rgb[:37] *= [1.02, .97, 1.08]
    rgb[37:] *= [.93, 1.11, .97]
    return Image(rgb=rgb)


def _render(img, **kw):
    pipe = Pipeline.build(RunConfig(backend="stub", **kw))
    return pipe.process_one("sample.jpg", image=img)


def test_auto_unreleased_portrait_and_zero_return_simple_exactly():
    img = _synthetic()
    for overrides in ({"scene":"daylight"}, {"scene":"people", "auto_unreleased":True},
                      {"scene":"other", "auto_unreleased":True},
                      {"scene":"daylight", "auto_unreleased":True, "intensity":0}):
        simple, baseline = _render(img, style="standard", scene=overrides["scene"])
        out, r = _render(img, style="auto", **overrides)
        assert np.array_equal(simple, out)
        assert r.global_settings == baseline.global_settings
        assert r.audit["auto"]["target"] is None and "Simple" in r.audit["auto"]["fallback"]
        assert r.audit["auto"]["scene"] == overrides["scene"]


def test_auto_provisional_goal_fit_controls_and_strength_bounds():
    img = _synthetic()
    simple, _ = _render(img, style="standard", concepts=[], compose=False)
    out, result = _render(img, style="auto", scene="daylight", auto_unreleased=True, concepts=[], compose=False)
    audit = result.audit["auto"]
    assert audit["target"] == "auto_landscape" and audit["controls"] and audit["photos"] == 0
    target = style_target.load(config_path("targets.toml"), audit["target"])["global"]
    def distance(rgb):
        features = style_target.features(rgb)
        return sum(max(0, lo-features[key], features[key]-hi) for key,(lo,hi) in target.items())
    assert distance(out) < distance(simple)
    _, stronger = _render(img, style="auto", scene="daylight", auto_unreleased=True, intensity=1.5,
                          concepts=[], compose=False)
    with open(config_path("auto.toml"), "rb") as fh:
        cfg = tomllib.load(fh)
    for values in stronger.audit["auto"]["controls"].values():
        assert all(abs(value) <= cfg["limits"]["landscape"][key] for key,value in values.items())
    with pytest.raises(ValueError):
        RunConfig(style="auto", intensity=1.51)


def test_auto_scene_setting_persists_on_replay_and_sunset_has_a_distinct_budget():
    img = _synthetic()
    pipe = Pipeline.build(RunConfig(style="auto", backend="stub", auto_unreleased=True, scene="sunset", intensity=.7))
    _, result = pipe.process_one("sample.jpg", image=img, do_apply=False)
    assert result.audit["run_config"]["scene"] == "sunset"
    assert result.audit["run_config"]["intensity"] == .7
    assert result.audit["local_style"]["clip_budget"] == pipe.auto_cfg["scene"]["sunset"]["clip_budget"]


def test_auto_proxy_backoff_never_reintroduces_settings_beyond_a_fit_limit():
    from autoedit.quality import load_quality_config
    source = np.full((32, 32, 3), .2, dtype=np.float32)
    base = {"exposure": 1.0}
    result, moves = style_target.fit(source, base, {"p50": [.26, .26]}, 1.5, {"exposure": .1},
                                     load_quality_config(), auto=True)
    assert (result == base and not moves) or abs(result["exposure"]) <= .1


def test_auto_land_and_clouds_remain_disjoint_from_skin_and_water():
    from autoedit.segment.base import RegionMask
    from autoedit.segment.resolve import landscape_regions
    from autoedit.concepts import load_concepts
    _, meta = load_concepts(config_path("concepts.toml"))
    y, x = np.indices((32, 32))
    masks = [RegionMask("sky", y < 5), RegionMask("cloud", (y >= 5) & (y < 9)),
             RegionMask("water", y >= 24), RegionMask("skin", (x < 8) & (y >= 9) & (y < 20))]
    found = landscape_regions(masks, ["sky", "skin", "water"], meta, y.shape)
    assert np.array_equal(found.union["sky"], y < 9)
    assert "land" in found.union and not np.any(found.union["land"] & (found.union["skin"] | found.union["water"]))
    assert np.max(sum(r.mask.astype(int) for r in found.regions)) == 1


def test_shaded_daylight_diffuse_overcast_and_twilight_ambiguity():
    cfg = _thresholds()
    shaded = _stats(sky_lum=.37, lum_p50=.17, lum_p90=.68)
    def detect(g, **kwargs):
        return scene.classify(g, {"sky"}, thresholds=cfg, sky_warmth=-.10, **kwargs).finer
    assert detect(shaded, capture_ev=13) == "daylight"
    assert detect(shaded, capture_ev=None) == "landscape"
    assert detect(_stats(sky_lum=.84, sky_sat=.07), capture_ev=12) == "mist"
    dim = _stats(sky_lum=.34, lum_p50=.19, lum_p90=.58)
    assert detect(dim, capture_ev=6, rose_share=.2) == "twilight"
    assert detect(dim, capture_ev=None, rose_share=.2) == "landscape"


def test_capture_ev_requires_shutter_aperture_and_iso():
    from autoedit.imageio import _read_exif
    class Picture:
        def __init__(self, values):
            self.values = values
        def getexif(self):
            return self
        def get_ifd(self, _key):
            return self.values
    meta = {0x8827: 100, 0x829A: 1/125, 0x829D: 8, 0x9204: -2}
    assert _read_exif(Picture(meta))["capture_ev100"] == pytest.approx(np.log2(8000))
    assert "capture_ev100" not in _read_exif(Picture({0x9204:-2}))


def test_frame_and_region_share_auto_limits_in_both_directions():
    frame, regions = {"exposure": -1.8}, {"sky":{"exposure":-.6}, "mountain":{"exposure":.4}}
    style_target.bound_fit(frame, regions, {"exposure":0}, {"sky":{"exposure":-.6}}, {"exposure":2})
    assert regions["sky"]["exposure"] == pytest.approx(-.2)
    assert regions["mountain"]["exposure"] == .4


def test_an_impossible_combined_budget_never_adds_out_of_bounds_settings():
    from autoedit.quality import load_quality_config
    src = np.full((32, 32, 3), .2, dtype=np.float32)
    result, _ = style_target.fit(src, {}, {"p50":[.23,.23]}, 1.5, {"exposure":.1}, load_quality_config(),
                                 global_after={"exposure":2}, auto=True)
    assert "exposure" not in result
    _, regions = consistency.pull_fit({"exposure":2}, {"sky":{"exposure":.05}}, {"sky":{"exposure":.01}},
                                      .5, {"exposure":.1})
    assert regions["sky"]["exposure"] == .05


def test_stale_shoot_or_profile_cannot_opt_portraits_into_landscape_goals():
    from autoedit.styles import AutoPhoto, Style
    cfg = {"auto":{"fallback_strength":.6}, "scene":{"people":{"released":True}, "sunset":{"released":True}}}
    style = Style("auto", targets={"landscape":"land"})
    choice = AutoPhoto.select(scene.Scene("people",.9), style, cfg, 1, True, shoot={"scene":"sunset"})
    assert choice.scene == "people" and not choice.target
