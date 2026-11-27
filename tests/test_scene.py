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
    assert consistency.shoot_blocks(seq) == [["a", "b", "c"], ["e", "f"], ["g", "h"]]
    cases = [{"global": {"exposure": .4}, "sky": {"exposure": -.6}}, {"global": {"exposure": .5}},
             {"global": {"exposure": .6}, "sky": {"exposure": -.8}}]
    medians = consistency.median_fit(cases)
    results = [consistency.pull_fit(plan["global"], {}, medians, .5, {"exposure": 2})[0]["exposure"]
               for plan in cases]
    assert np.allclose(results, [.45, .5, .55]) and medians["sky"]["exposure"] == -.7


def test_a_frame_without_its_own_sky_takes_its_neighbours_and_a_frame_with_one_keeps_it():
    cfg = dict(window_min=10, min_frames=2, min_share=.75)
    take = consistency.neighbour_scenes
    deck = [("a", 0, "mist", True), ("b", 60, "mist", True), ("c", 120, "mist", True)]
    assert take(deck + [("q", 90, "daylight", False)], cfg) == {"q": "mist"}
    assert take(deck + [("q", 3000, "daylight", False)], cfg) == {}       # 48 minutes later
    assert take(deck[:1] + [("q", 90, "daylight", False)], cfg) == {}     # one neighbour is not enough
    assert take(deck + [("o", 100, "daylight", True)], cfg) == {}         # blue openings of its own
    assert take(deck[:2] + [("o", 30, "daylight", True), ("q", 90, "daylight", False)], cfg) == {}   # 2 of 3
    assert take(deck + [("f", 90, "landscape", False)], cfg) == {}        # it fell back: it stays there
    dusk = [("a", 0, "twilight", True), ("b", 60, "twilight", True)]
    assert take(dusk + [("q", 30, "daylight", False), ("f", 40, "landscape", False)], cfg) == {}
    glow = [("a", 0, "sunset", True), ("b", 60, "sunset", True)]
    assert take(glow + [("q", 30, "daylight", False)], cfg) == {"q": "sunset"}


def test_neighbours_never_give_daylight_or_twilight():
    import random
    cfg = dict(window_min=10, min_frames=2, min_share=.75)
    take = consistency.neighbour_scenes
    # F62 ticket 26. A timelapse from dusk light into day, a frame every 10 seconds: 8 twilight
    # frames, 36 that fell back, 30 daylight frames. Ticket 25's rule called the later ones daylight.
    dawn = ([(f"t{i}", 10 * i, "twilight", True) for i in range(8)]
            + [(f"u{i}", 80 + 10 * i, "landscape", False) for i in range(36)]
            + [(f"d{i}", 440 + 10 * i, "daylight", True) for i in range(30)])
    assert take(dawn, cfg) == {}
    assert take([(p, -t, s, d) for p, t, s, d in dawn], cfg) == {}        # the same run at dusk
    rng = random.Random(26)
    for _ in range(200):
        frames = []
        for i in range(rng.randint(2, 40)):
            direct = rng.random() < .6
            kind = rng.choice(scene.LANDSCAPES) if direct else rng.choice(("daylight", "landscape"))
            frames.append((str(i), rng.uniform(0, 1800), kind, direct))
        own = {p: (s, d) for p, _t, s, d in frames}
        taken = take(frames, cfg)
        assert set(taken.values()) <= {"mist", "sunset"}
        assert all(own[p] == ("daylight", False) for p in taken)


def test_direct_marks_a_scene_the_frames_own_sky_or_light_decided():
    cfg = _thresholds()
    deck = _stats(sky_cov=.41, sky_lum=.57, sky_sat=.21, lum_p50=.17, lum_p90=.64, fg_p50=.11)
    assert scene.classify(deck, {"sky"}, thresholds=cfg, capture_ev=12.0, sky_warmth=-.14, clouds="deck").direct
    assert scene.classify(_stats(), {"sky"}, thresholds=cfg, sky_warmth=-.06, capture_ev=13.0, clouds="open").direct
    shade = _stats(sky_cov=0, sky_lum=0, sky_sat=0, lum_p10=.03, lum_p50=.08, lum_p90=.21, fg_p50=.08)
    default = scene.classify(shade, {"water"}, thresholds=cfg, capture_ev=12.3)
    assert default.finer == "daylight" and not default.direct
    fallback = scene.classify(_stats(sky_cov=0, lum_p50=.24), {"sky"}, thresholds=cfg)
    assert fallback.finer == "landscape" and not fallback.direct
    assert not scene.classify(_stats(), {"sky"}, override="mist", thresholds=cfg).direct
    assert not scene.classify(_stats(), {"sky"}).direct      # Simple's three-way answer


def test_auto_shoots_hands_a_frame_without_sky_its_neighbours_scene(tmp_path):
    from types import SimpleNamespace
    times = {"a.jpg": 0, "b.jpg": 60, "m.jpg": 30, "f.jpg": 45, "bad.jpg": 40, "late.jpg": 3600}
    calls = []

    def audit(path, scene, direct):
        return SimpleNamespace(audit={"auto": {"scene": scene, "direct": direct, "target": "t", "fit": {}}})

    canned = {"a.jpg": ("mist", True), "b.jpg": ("mist", True), "m.jpg": ("daylight", False),
              "late.jpg": ("daylight", False), "f.jpg": ("landscape", False)}

    class Pipe:
        auto_cfg = {"shoot": dict(window_min=10, min_frames=2, min_share=.75)}
        rules = {}

        def process_one(self, path, do_apply=True, auto_shoot=None, **kw):
            name = path.split("/")[-1]
            calls.append((name, auto_shoot))
            if name == "bad.jpg":
                raise RuntimeError("damaged")
            scene, direct = canned[name]
            return None, audit(name, auto_shoot["scene"] if auto_shoot else scene, direct)

    images = [str(tmp_path / n) for n in times]
    saved = consistency.shoot_time
    consistency.shoot_time = lambda path: times[path.split("/")[-1]]
    try:
        biases = {}
        consistency._auto_shoots(images, biases, Pipe(), None)
    finally:
        consistency.shoot_time = saved
    got = {k.split("/")[-1]: v.auto_shoot for k, v in biases.items()}
    assert got == {"m.jpg": {"scene": "mist", "median": {}}}
    assert [c for c in calls if c[0] == "m.jpg"] == [("m.jpg", None), ("m.jpg", {"scene": "mist"})]
    assert [c for c in calls if c[0] == "f.jpg"] == [("f.jpg", None)]     # fell back: analysed once, handed nothing


def test_load_auto_refuses_a_bad_shoot_table(tmp_path):
    from autoedit.styles import load_auto
    style = Pipeline.build(RunConfig(style="auto", backend="stub")).styles["auto"]
    text = open(config_path("auto.toml")).read()
    good = tmp_path / "good.toml"
    good.write_text(text)
    load_auto(str(good), style)
    for old, new in (("min_share = 0.75", "min_share = 0.4"), ("min_frames = 2 ", "min_frames = 0 "),
                     ("[shoot]", "[shoot_gone]")):
        assert old in text
        bad = tmp_path / "bad.toml"
        bad.write_text(text.replace(old, new, 1))
        with pytest.raises(ValueError):
            load_auto(str(bad), style)


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


def test_light_on_the_land_not_the_camera_setting_separates_dusk_from_day():
    cfg = _thresholds()
    def detect(land):
        g = _stats(sky_lum=.50, lum_p50=.19, lum_p90=.63, fg_p50=land)
        return scene.classify(g, {"sky"}, thresholds=cfg, sky_warmth=-.03, capture_ev=12.5, clouds="open").finer
    # One camera setting and one clear sky, three amounts of light on the land.
    assert detect(.30) == "daylight"     # 1.3 stops under mid-grey: 11.2
    assert detect(.04) == "twilight"     # 5.9 stops under: 6.6, at sunset light
    assert detect(.08) == "landscape"    # 7.9: between the two it is neither, and never a swap


def test_a_night_frame_rendered_bright_is_night_and_a_dim_cloud_deck_is_overcast():
    cfg = _thresholds()
    # A long exposure lifts a town at night to a mid-grey median; the glow on its sky is gold.
    town = _stats(sky_cov=.68, sky_lum=.38, sky_sat=.20, lum_p50=.37, lum_p90=.47, fg_p50=.30)
    assert scene.classify(town, {"sky"}, thresholds=cfg, capture_ev=.3, sky_warmth=.07, warm_share=.14).finer == "twilight"
    # Standing stones against a storm sky: the land is at dusk light, the camera was set for the sky.
    stones = _stats(sky_cov=.60, sky_lum=.28, sky_sat=.08, lum_p10=.12, lum_p50=.21, lum_p90=.36, fg_p50=.03)
    def detect(clouds):
        return scene.classify(stones, {"sky"}, thresholds=cfg, capture_ev=12.6, sky_warmth=-.02, clouds=clouds).finer
    assert detect("deck") == "mist" and detect("open") == "twilight"


def test_gold_in_the_sky_needs_a_second_witness_only_at_a_sunlit_exposure():
    cfg = _thresholds()
    g = _stats(sky_cov=.33, sky_lum=.68, sky_sat=.21, lum_p50=.21, lum_p90=.74, fg_p50=.16)
    def detect(ev):
        return scene.classify(g, {"sky"}, thresholds=cfg, capture_ev=ev, sky_warmth=.05, warm_share=.34, clouds="open").finer
    assert detect(12.4) == "sunset"      # the sun on the horizon, the land still at 9.4
    assert detect(14.5) == "daylight"    # a sunlit exposure: a warm cast alone is no sunset


def test_fog_across_the_frame_is_mist_whatever_colour_it_took():
    cfg = _thresholds()
    g = _stats(sky_cov=.42, sky_lum=.26, sky_sat=.19, lum_p10=.17, lum_p50=.22, lum_p90=.28, fg_p50=.20)
    def detect(fog):
        return scene.classify(g, {"sky"}, thresholds=cfg, capture_ev=12.9, sky_warmth=-.06, clouds="open", fog=fog).finer
    assert detect(.68) == "mist"         # a blue cast makes fog read as open sky; the fog mask does not care
    assert detect(.20) == "daylight"     # a bank of low cloud in a clear view


def test_a_deck_just_above_sunset_light_is_overcast_and_a_frame_without_sky_keeps_daylight():
    cfg = _thresholds()
    deck = _stats(sky_cov=.41, sky_lum=.57, sky_sat=.21, lum_p50=.17, lum_p90=.64, fg_p50=.11)      # land at 8.05
    assert scene.classify(deck, {"sky"}, thresholds=cfg, capture_ev=12.0, sky_warmth=-.14, clouds="deck").finer == "mist"
    # A waterfall in forest shade: no sky to judge, the land 4.6 stops under mid-grey, a daylight setting.
    shade = _stats(sky_cov=0, sky_lum=0, sky_sat=0, lum_p10=.03, lum_p50=.08, lum_p90=.21, fg_p50=.08)
    assert scene.classify(shade, {"water"}, thresholds=cfg, capture_ev=12.3).finer == "daylight"


def test_capture_ev_falls_back_to_libraw_only_for_its_own_key():
    from autoedit import imageio
    assert imageio._ev100(1/200, 14, 200) == pytest.approx(np.log2(19600))
    assert imageio._ev100(.125, 0, 100) is None and imageio._ev100(.125, 8, 0) is None
    assert imageio._raw_capture_ev("no-such-file.raf") is None


def test_an_afterglow_over_unlit_land_is_sunset_and_a_band_under_a_blue_sky_stays_twilight():
    cfg = _thresholds()
    def detect(g, ev, warmth, gold, side, **kw):
        return scene.classify(g, {"sky"}, thresholds=cfg, capture_ev=ev, sky_warmth=warmth, warm_share=gold,
                              warm_side=side, **kw).finer
    # F62 ticket 27. A peach sky over land in shadow: the land 4.6 stops under mid-grey (2.0), the sky at 8.5.
    glow = _stats(sky_cov=.48, sky_lum=.85, sky_sat=.13, lum_p10=.04, lum_p50=.33, lum_p90=.91, fg_p50=.08)
    assert detect(glow, 6.6, .12, .37, .96) == "sunset"
    assert detect(glow, 6.6, .12, .37, 0.) == "twilight"                # as before, for a caller that gives no share
    assert detect(glow, 6.6, .12, .37, .96, night=True) == "sunset"     # a high ISO does not make a lit sky night
    assert detect(glow, 6.6, .12, .37, cfg["afterglow_min_share"]) == "twilight"    # half is not the main colour
    assert detect(glow, 6.6, .01, 0., 1.) == "twilight"                 # a white sky with a warm cast: no gold in it
    # An orange band under a dark blue sky: the band lifts the mean warmth, most of the sky is blue.
    band = _stats(sky_cov=.29, sky_lum=.44, sky_sat=.33, lum_p10=.03, lum_p50=.11, lum_p90=.51, fg_p50=.08)
    assert detect(band, 9.1, .055, .29, .42) == "twilight"
    # A town at night under cloud that its lamps light: warm all over, and 9 stops under the afterglow.
    town = _stats(sky_cov=.68, sky_lum=.38, sky_sat=.20, lum_p50=.37, lum_p90=.47, fg_p50=.30)
    assert detect(town, .3, .07, .14, .94) == "twilight"
    # The sky's own light decides, at the level in the config: the same sky a stop above it and a stop below.
    level = cfg["afterglow_sky_min_ev"] - float(np.log2(scene.srgb_to_linear(.85) / .18))
    assert detect(glow, level + 1, .12, .37, .96) == "sunset" and detect(glow, level - 1, .12, .37, .96) == "twilight"


def test_the_afterglow_rule_only_ever_turns_a_night_answer_into_sunset():
    import random
    cfg = _thresholds()
    rng = random.Random(27)
    moved = 0
    for _ in range(2000):
        low, mid, high = sorted(rng.uniform(0, 1) for _ in range(3))
        g = _stats(sky_cov=rng.choice((0., rng.uniform(.04, .9), rng.uniform(.04, .9))), sky_lum=rng.uniform(0, 1),
                   sky_sat=rng.uniform(0, .5), lum_p10=low, lum_p50=mid, lum_p90=high, fg_p50=rng.uniform(0, .6),
                   veil=rng.choice((0., rng.uniform(0, .3))), blown_sky=rng.choice((0., rng.uniform(0, 1))))
        kw = dict(thresholds=cfg, capture_ev=rng.choice((None, rng.uniform(-6, 16), rng.uniform(2, 12))),
                  sky_warmth=rng.uniform(-.3, .3), warm_share=rng.uniform(0, 1),
                  rose_share=rng.choice((0., rng.uniform(0, .5))), night=rng.random() < .2,
                  clouds=rng.choice((None, "open", "deck", "ceiling")), fog=rng.choice((0., rng.uniform(0, 1))))
        before = scene.classify(g, {"sky"}, **kw)
        after = scene.classify(g, {"sky"}, warm_side=rng.uniform(0, 1), **kw)
        if (after.finer, after.reason) != (before.finer, before.reason):
            moved += 1
            assert (before.finer, before.reason) == ("twilight", "night exposure and low ambient light")
            assert after.finer == "sunset" and after.direct
    assert moved > 20     # the draws do reach the rule


def test_sky_colour_counts_the_share_of_the_sky_on_the_warm_side():
    cfg = _thresholds()
    rgb = np.zeros((10, 10, 3), np.float32)
    sky = np.zeros((10, 10), bool)
    sky[:6] = True
    rgb[:4] = [.05, .08, .16]       # dark blue above
    rgb[4:6] = [.95, .55, .10]      # a bright orange band on the horizon
    warmth, gold, rose, side = scene.sky_colour(rgb, sky, cfg)
    assert warmth > 0 and gold == pytest.approx(1 / 3)      # the mean says warm
    assert side == pytest.approx(1 / 3)                     # the share says blue
    rgb[:4] = [.80, .76, .74]       # a pale peach sky above the band: warm, and too pale to count as gold
    assert scene.sky_colour(rgb, sky, cfg)[1::2] == (pytest.approx(1 / 3), 1.0)
    rgb[:6] = .5                    # grey is on neither side
    assert scene.sky_colour(rgb, sky, cfg)[3] == 0.0
    assert scene.sky_colour(rgb, np.zeros_like(sky), cfg) == (0.0, 0.0, 0.0, 0.0)
    assert scene.sky_colour(rgb, None, cfg) == (0.0, 0.0, 0.0, 0.0)
