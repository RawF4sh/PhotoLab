"""F62 finer scenes use capture light plus the rendered view, independently of source identity."""
import tomllib

from autoedit.contract import GlobalStatsModel
from autoedit.paths import config_path
from autoedit.scene import classify


def label(*, ev, hues=(0., 0., 0.), **stats):
    with open(config_path("auto.toml"), "rb") as stream:
        config = tomllib.load(stream)["scenes"]
    values = {"mean_lum": .32, "mean_sat": .2, "shadow_clip": 0., "highlight_clip": 0., "haze": 0.,
              "sky_cov": .31, "sky_lum": .58, "sky_sat": .23, "lum_p10": .14, "lum_p50": .40, "lum_p90": .69}
    scene = classify(GlobalStatsModel(**{**values, **stats}), {"mountain", "sky"}, thresholds=config,
                     capture_ev=ev, sky_warmth=hues[0], warm_share=hues[1], rose_share=hues[2])
    return scene.finer


def test_metered_rose_twilight_and_sunlit_pink_clouds():
    # Exposing for a rosy afterglow brings the median up. Its sky can contain a few gold clouds.
    assert label(ev=6., hues=(.05, .14, .39)) == "twilight"
    assert label(ev=7., hues=(-.13, .01, .04), lum_p90=.8) == "twilight"
    assert label(ev=14., hues=(-.13, .01, .04), lum_p90=.8) == "daylight"


def test_deep_artificial_light_and_flat_invalid_captures():
    # A warmly lit urban sky and bright pin lights are compatible with a very dark ambient exposure.
    assert label(ev=2., hues=(.16, .42, .03), sky_lum=.22, lum_p50=.16, lum_p90=.70) == "twilight"
    assert label(ev=7., hues=(.16, .42, .03), sky_lum=.58, lum_p50=.40) == "sunset"
    assert label(ev=2., sky_cov=0., lum_p50=.99, lum_p90=.99, lum_p10=.99) == "landscape"
    assert label(ev=7., sky_cov=0., lum_p50=.30, lum_p90=.65) == "landscape"


def test_bright_sunlit_cloud_has_different_evidence_from_flat_fog():
    assert label(ev=14.3, sky_sat=.07, sky_lum=.91, lum_p90=.95, blown_sky=.09, veil=.02) == "daylight"
    assert label(ev=14., sky_lum=.80, sky_sat=.05, lum_p10=.42, lum_p90=.71, veil=.19) == "mist"
    assert label(ev=12., sky_lum=.75, sky_sat=.07, lum_p10=.17, lum_p90=.82) == "mist"


def test_continuous_ceiling_and_blue_holes_under_the_same_high_daylight_exposure():
    import numpy as np
    from autoedit.scene import cloud_cover

    with open(config_path("auto.toml"), "rb") as stream:
        cfg = tomllib.load(stream)["scenes"]
    y, x = np.indices((80, 100))
    sky = y < 30
    grey = np.where(sky, .76 + .14*np.sin(x/18.), .16 + .16*np.sin(x/10.))
    rgb = np.stack((grey, grey, grey + .025), axis=2).astype(np.float32)
    stats = GlobalStatsModel(mean_lum=.44, mean_sat=.15, shadow_clip=0, highlight_clip=0,
                             haze=0, veil=.14, sky_cov=.375, sky_lum=.78, sky_sat=.09,
                             lum_p10=.08, lum_p50=.40, lum_p90=.89)
    def measured():
        return classify(stats, {"sky","mountain"}, thresholds=cfg, capture_ev=14.,
                        clouds=cloud_cover(rgb, sky, cfg)).finer
    assert measured() == "mist"
    # A small part of the sky contains real blue; its mean colour can still look grey.
    rgb[sky & (x < 14)] = [.40,.61,.85]
    assert measured() == "daylight"
    assert cloud_cover(rgb, sky & (x < 10), cfg) is None  # too little sky to distinguish the two


def test_a_white_clipped_or_tiny_sky_alone_cannot_establish_a_cloud_deck():
    import numpy as np
    from autoedit.scene import cloud_cover

    with open(config_path("auto.toml"), "rb") as stream:
        cfg = tomllib.load(stream)["scenes"]
    pixels = np.ones((30, 60, 3), np.float32)
    mask = np.ones((30,60), bool)
    weather = cloud_cover(pixels, mask, cfg)
    stats = GlobalStatsModel(mean_lum=.6, mean_sat=.3, shadow_clip=0, highlight_clip=.4,
                             haze=0, sky_cov=.4, sky_lum=1., sky_sat=0., blown_sky=1.,
                             lum_p10=.1, lum_p50=.6, lum_p90=1.)
    result = classify(stats, {"sky","grass"}, thresholds=cfg, clouds=weather, capture_ev=14.)
    assert result.finer == "daylight"


def test_semantic_cloud_deck_distinguishes_blue_cast_from_blue_holes():
    import numpy as np
    from autoedit.scene import cloud_cover

    with open(config_path("auto.toml"), "rb") as stream:
        cfg = tomllib.load(stream)["scenes"]
    y, x = np.indices((64, 96))
    mask = y < 28
    rgb = np.zeros((*mask.shape, 3), np.float32)
    rgb[mask] = [.62, .69, .77]  # a consistent cool white balance on light grey
    assert cloud_cover(rgb, mask, cfg, mask) == "deck"
    assert cloud_cover(rgb, mask, cfg, ~mask) != "deck"  # cloud in front of land
    assert cloud_cover(rgb, mask, cfg, mask & (x > 20)) != "deck"
    rgb[mask & (x < 7)] = [.28, .50, .77]
    assert cloud_cover(rgb, mask, cfg, mask) != "deck"  # even if SAM bridges the break


def test_unreleased_scene_and_release_preview_see_the_same_cloud_evidence():
    import numpy as np
    from autoedit.contract import RunConfig
    from autoedit.imageio import Image
    from autoedit.pipeline import Pipeline
    from autoedit.segment.base import RegionMask

    class Masks:
        def __init__(self):
            self.words = []
        def segment(self, im, words):
            self.words = list(words)
            sky = np.indices(im.rgb.shape[:2])[0] < im.height // 2
            items = {"sky": sky, "cloud": sky, "mountain": ~sky}
            return [RegionMask(w, items[w], .99) for w in words if w in items]

    y, x = np.indices((40, 64))
    rgb = np.stack([.18 + .12*x/64]*3, axis=2).astype(np.float32)
    rgb[y<20] = [.62, .69, .77]
    im = Image(rgb=rgb, exif={"capture_ev100":14.})
    photos = {}
    for style, preview in (("standard", False), ("auto", False), ("auto", True)):
        pipe = Pipeline.build(RunConfig(backend="stub", style=style, auto_unreleased=preview))
        pipe.segmenter = Masks()
        pixels, info = pipe.process_one("sample.jpg", image=im)
        photos[(style, preview)] = pixels
        if style == "auto":
            assert info.scene == "mist" and "cloud" in pipe.segmenter.words
        else:
            assert "cloud" not in pipe.segmenter.words
    assert np.array_equal(photos["standard", False], photos["auto", False])
