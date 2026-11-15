"""F20 part 1: colour verbs and size words of the instruction word list."""

import os

import numpy as np

from autoedit import instructions
from autoedit.contract import Adjustment
from autoedit.edit.ops import apply_ops
from autoedit.light import Light
from autoedit.paths import configs_dir
from autoedit.segment.resolve import ResolvedRegions

KNOWN = {"sky", "mountain", "grass"}
COLOURS = {
    "pink": {"tint": -100, "temp": 8}, "purple": {"tint": -90, "temp": -10},
    "golden": {"temp": 50, "tint": 6}, "teal": {"temp": -20, "tint": 100},
    "green": {"tint": 100}, "blue": {"temp": -50},
}
SIZES = {"bigger": 1.5, "larger": 1.5, "wider": 1.5, "longer": 1.5, "taller": 1.5, "huge": 2.0,
         "smaller": 0.67, "narrower": 0.67, "shorter": 0.67, "tighter": 0.67, "tiny": 0.5}


def _vocab():
    return instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))


def _p(text):
    return instructions.parse(text, _vocab(), KNOWN)


def _one(text):
    adjs, problems = _p(text)
    assert not problems and len(adjs) == 1, (text, problems)
    return adjs[0]


def test_colour_parses():
    words = {"pink": ["pink", "pinker", "rosy", "rosier", "magenta", "more pink"],
             "purple": ["purple", "purpler", "violet", "more purple"],
             "golden": ["golden", "gold", "more golden"], "teal": ["teal", "cyan", "more teal"],
             "green": ["greener", "more green"], "blue": ["bluer", "more blue"]}
    for verb, forms in words.items():
        for form in forms:
            a = _one(f"make the sky {form}")
            assert a.target == "sky" and a.ops == {k: float(v) for k, v in COLOURS[verb].items()}, (form, a)
            top = _one(f"make the top {form}")
            assert top.target == "place:top" and top.ops == a.ops, form
    assert _one("pinker the sky").ops == _one("more pink the sky").ops


def test_colour_moves_the_right_channels():
    grey = np.full((8, 8, 3), 0.5, np.float32)

    def mean(verb):
        return apply_ops(grey, _vocab().verbs[verb]).reshape(-1, 3).mean(axis=0)

    r, g, b = mean("pink")
    assert r > g and b > g
    r, g, b = mean("golden")
    assert r > b
    r, g, b = mean("teal")
    assert g > r and b > r


def test_size_parses():
    v = _vocab()
    assert {" ".join(t): s for t, s in v.size_words} == SIZES
    for word, factor in SIZES.items():
        assert _one(f"make the top pink with a {word} gradient").size == factor, word
    a = _one("make the top pink with a bigger gradient")
    assert a.target == "place:top" and a.ops == {"tint": -100.0, "temp": 8.0}
    a = _one("a smaller vignette")
    assert a.target == "place:edges" and a.ops == {"exposure": -0.25} and a.size == 0.67
    a = _one("add a wider glow")
    assert a.target == "place:light" and a.size == 1.5 and a.ops == {"exposure": 0.15, "temp": 8.0}
    a = _one("golden light on the left")
    assert a.target == "place:left" and a.ops == {"temp": 50.0, "tint": 6.0} and a.size == 1.0
    assert _one("brighten the top").size == 1.0
    adjs, problems = _p("make the top pink with a bigger and wider gradient")
    assert not adjs and "more than one size" in problems[0]
    adjs, problems = _p("make the sky pink with a bigger gradient")
    assert not adjs and "size only applies to a gradient, an oval or a glow" in problems[0]
    adjs, problems = _p("make the sky have a bigger gradient for pink")
    assert not adjs and problems and instructions.translatable(problems[0])


def _alpha(place, size, light=None):
    return instructions.place_alpha(_vocab().places[place], (200, 200), light, size)


def test_size_scales_the_shape():
    col = lambda a: a[:, 100]  # noqa: E731
    half = lambda a: int(np.argmin(np.abs(col(a) - 0.5)))  # noqa: E731
    base, big = half(_alpha("top", 1.0)), half(_alpha("top", 1.5))
    assert abs(big / base - 1.5) < 0.05, (base, big)
    assert half(_alpha("top", 4.0)) <= 200                   # capped at the frame
    assert _alpha("edges", 1.5).mean() > _alpha("edges", 1.0).mean() > _alpha("edges", 0.67).mean()
    assert _alpha("center", 1.5).mean() > _alpha("center", 1.0).mean()
    light = Light(cx=0.5, cy=0.5, radius=0.05, source="concept", score=1.0)
    assert _alpha("light", 1.5, light).sum() > _alpha("light", 1.0, light).sum() * 1.5


def test_from_translation_size():
    v, clauses = _vocab(), ["x"]

    def size_of(value):
        out = instructions.from_translation(
            [{"clause": 0, "target": "top", "edits": [{"op": "tint", "value": -40}], "size": value}],
            clauses, v, KNOWN)
        return out[0].size if out else None

    assert size_of(1.5) == 1.5 and size_of(99) == 4.0 and size_of(0.01) == 0.25
    assert size_of("big") == 1.0 and size_of(float("nan")) == 1.0
    out = instructions.from_translation(
        [{"clause": 0, "target": "top", "edits": [{"op": "tint", "value": -40}]}], clauses, v, KNOWN)
    assert out[0].target == "place:top" and out[0].size == 1.0


def test_tint_limit():
    n = 100
    region = ResolvedRegions(regions=[], union={}, detected=[], label_map=np.zeros((n, n), int), shape=(n, n))
    adjs = [Adjustment(target="global", ops={"tint": -100.0, "temp": 8.0}, source="pink") for _ in range(4)]
    glob = {}
    p = instructions.plan(adjs, _vocab(), [], [], region, {}, glob, {})
    limit = _vocab().limits["tint"]
    assert limit > 0 and glob["tint"] == -limit
    instructions.report(adjs, p, p.settings)
    assert all(a.status == "clamped" for a in adjs)


def _rows_changed(edit, tmp_path):
    """How many of the 200 rows a darkening of the top changes on a flat frame, through the pipeline."""
    from PIL import Image as PILImage

    from autoedit.contract import RunConfig
    from autoedit.pipeline import Pipeline

    path = str(tmp_path / "flat.png")
    PILImage.fromarray(np.full((200, 100, 3), 128, np.uint8), "RGB").save(path)
    base, _ = Pipeline.build(RunConfig(backend="stub")).process_one(path)
    out, result = Pipeline.build(RunConfig(backend="stub", edit=edit)).process_one(path)
    rows = np.abs(out - base).max(axis=(1, 2)) > 0.004
    return int(rows.sum()), result


def test_size_reaches_the_pipeline(tmp_path):
    plain, _ = _rows_changed("darken the top", tmp_path)
    bigger, _ = _rows_changed("darken the top with a bigger gradient", tmp_path)
    smaller, _ = _rows_changed("darken the top with a smaller gradient", tmp_path)
    assert smaller < plain < bigger, (smaller, plain, bigger)


def test_the_last_size_on_a_place_wins(tmp_path):
    last_big, r = _rows_changed("darken the top with a smaller gradient, darken the top with a bigger gradient", tmp_path)
    last_small, _ = _rows_changed("darken the top with a bigger gradient, darken the top with a smaller gradient", tmp_path)
    assert last_big > last_small
    first, second = [n for n in r.notes if n.startswith("instruction ")]
    assert "its size 0.67 is not used, the last instruction on this place sets 1.5" in first
    assert "is not used" not in second
