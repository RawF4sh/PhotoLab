"""Free-text look intent → bounded global nudges (local, offline). Guards the parser and that
the shipped vocabulary maps the obvious words the UI advertises (sunset = warm, b&w = desaturate)."""

import os

from autoedit import intent
from autoedit.paths import configs_dir

_VOCAB = {
    "warm": {"temp": 10.0},
    "sunset": {"temp": 12.0, "grade_highlights_temp": 9.0, "exposure": -0.03},
    "vibrant": {"vibrance": 10.0, "saturation": 6.0},
    "black and white": {"saturation": -100.0},
}


def test_parse_accumulates_matched_terms():
    nudge, terms = intent.parse("a warm sunset over the bay", _VOCAB)
    assert set(terms) == {"warm", "sunset"}
    assert nudge["temp"] == 22.0                     # 10 (warm) + 12 (sunset)
    assert nudge["grade_highlights_temp"] == 9.0
    assert round(nudge["exposure"], 3) == -0.03


def test_parse_clamps_per_op():
    # a single term whose delta exceeds the per-op bound is clamped (terms accumulate per-term,
    # not per-occurrence, so stacking distinct warm terms is what reaches the ceiling in practice)
    nudge, _ = intent.parse("scorching", {"scorching": {"temp": 999.0}})
    assert nudge["temp"] == intent._BOUNDS["temp"]


def test_black_and_white_fully_desaturates():
    nudge, terms = intent.parse("make it black and white", _VOCAB)
    assert nudge["saturation"] == -100.0             # saturation is uncapped downward


def test_empty_and_unknown():
    assert intent.parse("", _VOCAB) == ({}, [])
    assert intent.parse("something with no known words", _VOCAB) == ({}, [])
    assert intent.parse("warm", {}) == ({}, [])


def test_missing_vocab_file():
    assert intent.load_intents("/no/such/intents.toml") == {}


def test_shipped_vocabulary_maps_obvious_words():
    vocab = intent.load_intents(os.path.join(configs_dir(), "intents.toml"))
    assert vocab, "intents.toml should load"
    sunset, _ = intent.parse("sunset", vocab)
    assert sunset.get("temp", 0) > 0                 # sunset reads warm
    bw, _ = intent.parse("black and white", vocab)
    assert bw.get("saturation", 0) <= -100           # monochrome
