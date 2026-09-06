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
