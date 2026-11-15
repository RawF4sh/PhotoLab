"""F17: the LLM path against the current API shape, instruction translation, real-usage budget.
No test calls the API: every client is a stand-in built here. An ANTHROPIC_API_KEY in the
environment is removed for the duration of a test."""

import math
import os
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

from autoedit import decide as decide_mod
from autoedit import pipeline as pipeline_mod
from autoedit import instructions
from autoedit.contract import GlobalStatsModel, RegionStatsModel, RunConfig
from autoedit.decide.base import DecisionContext
from autoedit.decide.llm_decider import LlmDecider
from autoedit.edit.ops import OP_BOUNDS
from autoedit.looks import load as load_looks
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.rules import load as load_rules

_KEYS = ("ANTHROPIC_API_KEY", "AUTOEDIT_ALLOW_CLOUD")
_RULES = load_rules(config_path("rules.toml"))
_LOOKS = load_looks(config_path("looks.toml"))
_VOCAB = instructions.load_vocab(config_path("instructions.toml"))


class _Env:
    """Keeps a developer's key and consent out of the tests; restores them on exit."""
    def __enter__(self):
        self.saved = {k: os.environ.pop(k, None) for k in _KEYS}
        pipeline_mod._TRANSLATIONS.clear()      # one process asks once: a test must not inherit answers

    def __exit__(self, *exc):
        pipeline_mod._TRANSLATIONS.clear()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _usage(i=2000, o=1000):
    return NS(input_tokens=i, output_tokens=o)


def _tool_reply(payload, usage=None, stop="tool_use", name="set_edits"):
    return NS(content=[NS(type="thinking", thinking="..."),
                       NS(type="tool_use", name=name, input=payload)],
              usage=usage or _usage(), stop_reason=stop)


class _Client:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


def _decider(client, **kw):
    d = LlmDecider(**kw)
    d._client = client
    return d


def _ctx(with_region=True):
    g = GlobalStatsModel(mean_lum=0.4, mean_sat=0.3, shadow_clip=0.0, highlight_clip=0.0, haze=0.1)
    stats = {"sky": RegionStatsModel(name="sky", lum=0.7, sat=0.3, clip_low=0.0, clip_high=0.0,
                                     coverage=0.3)} if with_region else {}
    return DecisionContext(scene="landscape", gstats=g, region_stats=stats,
                           region_rule={"sky": "sky"} if with_region else {}, intensity=1.0,
                           rules=_RULES, looks=_LOOKS, thumb=np.full((16, 16, 3), 0.5, np.float32))


def _objects(schema):
    """Every object-typed node of a JSON schema."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for v in schema.values():
            yield from _objects(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from _objects(v)


def test_request_shape():
    with _Env():
        ctx = _ctx()
        base = LlmDecider()._base.decide(ctx)[1]
        client = _Client(_tool_reply({"global": [], "regions": []}))
        _decider(client).decide(ctx)
        req = client.requests[0]
        assert req["model"] == "claude-haiku-5-5"
        assert not {"temperature", "top_p", "top_k"} & set(req)
        assert req["tool_choice"] == {"type": "auto"}
        assert req["max_tokens"] >= 4096
        (tool,) = req["tools"]
        assert tool["strict"] is True
        assert all(o["additionalProperties"] is False for o in _objects(tool["input_schema"]))
        schema = tool["input_schema"]["properties"]
        assert set(schema["global"]["items"]["properties"]["op"]["enum"]) == set(OP_BOUNDS)
        region = schema["regions"]["items"]["properties"]
        assert set(region["name"]["enum"]) == set(base)
        assert "set_edits" in req["messages"][0]["content"][0]["text"]

        client = _Client(_tool_reply({"global": []}))
        _decider(client).decide(_ctx(with_region=False))
        assert "regions" not in client.requests[0]["tools"][0]["input_schema"]["properties"]


def test_reply_is_merged_and_bounded():
    with _Env():
        ctx = _ctx()
        g0, r0 = LlmDecider()._base.decide(ctx)
        reply = {"global": [{"op": "exposure", "value": 9}],
                 "regions": [{"name": "sky", "edits": [{"op": "contrast", "value": 500}]}]}
        g, r = _decider(_Client(_tool_reply(reply))).decide(ctx)
        assert g["exposure"] == min(g0.get("exposure", 0.0) + 0.5, OP_BOUNDS["exposure"][1])
        assert r["sky"]["contrast"] == min(r0["sky"].get("contrast", 0.0) + 25.0,
                                           OP_BOUNDS["contrast"][1])


def test_thinking_blocks_are_skipped():
    with _Env():
        ctx = _ctx()
        g0, _ = LlmDecider()._base.decide(ctx)
        reply = _tool_reply({"global": [{"op": "contrast", "value": 3}], "regions": []})
        assert reply.content[0].type == "thinking"
        g, _ = _decider(_Client(reply)).decide(ctx)
        assert g["contrast"] == g0.get("contrast", 0.0) + 3


def test_refusal_and_missing_tool_fall_back_to_rules():
    with _Env():
        ctx = _ctx()
        want = LlmDecider()._base.decide(ctx)
        payload = {"global": [{"op": "exposure", "value": 0.4}], "regions": []}
        refusal = _tool_reply(payload, stop="refusal")
        text_only = NS(content=[NS(type="text", text="no")], usage=_usage(), stop_reason="end_turn")
        for client in (_Client(refusal), _Client(text_only), _Client(error=RuntimeError("boom"))):
            assert _decider(client).decide(ctx) == want


def test_budget_counts_real_usage():
    with _Env():
        ctx = _ctx()
        d = _decider(_Client(_tool_reply({"global": [], "regions": []})))
        d.decide(ctx)
        d.decide(ctx)
        assert abs(d.spent - 0.0014) < 1e-9
        # One call costs 0.0007 USD: a 0.0005 cap stops the second photo.
        client = _Client(_tool_reply({"global": [], "regions": []}))
        d = _decider(client, budget_usd=0.0005)
        d.decide(ctx)
        d.decide(ctx)
        assert len(client.requests) == 1


def test_defaults():
    import tomllib
    cfg = RunConfig()
    assert cfg.llm_model == "claude-haiku-5-5" and cfg.llm_budget_usd == 1.0
    assert cfg.llm_translate_instructions is True
    llm = tomllib.loads(Path(config_path("defaults.toml")).read_text())["llm"]
    assert llm["model"] == cfg.llm_model and llm["budget_usd"] == cfg.llm_budget_usd
    assert llm["input_usd_per_mtok"] == cfg.llm_input_usd_per_mtok
    assert llm["output_usd_per_mtok"] == cfg.llm_output_usd_per_mtok
    from autoedit import cli
    kw = cli._flatten({"llm": {"temperature": 0.3, "translate_instructions": False}})
    assert RunConfig(**kw).llm_temperature == 0.3 and kw["llm_translate_instructions"] is False


def test_translate_request_and_result():
    with _Env():
        items = [{"clause": 0, "target": "lake", "edits": [{"op": "saturation", "value": 12}]}]
        client = _Client(_tool_reply({"adjustments": items}, name="set_adjustments"))
        d = _decider(client)
        ops = sorted(_VOCAB.limits)
        assert d.translate(["make the lake pop"], ops) == items
        req = client.requests[0]
        content = req["messages"][0]["content"]
        assert isinstance(content, str) and "make the lake pop" in content   # text only, no image
        (tool,) = req["tools"]
        assert tool["strict"] is True
        edit = tool["input_schema"]["properties"]["adjustments"]["items"]["properties"]["edits"]["items"]
        assert edit["properties"]["op"]["enum"] == ops
        assert all(o["additionalProperties"] is False for o in _objects(tool["input_schema"]))
        # capped: no call
        capped = _Client(_tool_reply({"adjustments": items}))
        c = _decider(capped, max_calls=1)
        c.calls = 1
        assert c.translate(["x"], ops) == [] and capped.requests == []
        # client error
        assert _decider(_Client(error=RuntimeError("boom"))).translate(["x"], ops) == []


def test_from_translation_validates():
    ops = [{"op": "exposure", "value": 0.3}]
    clauses = ["make the lake pop"]
    known = {"lake", "sky"}

    def conv(item):
        return instructions.from_translation([item], clauses, _VOCAB, known)

    assert conv({"clause": 5, "target": "lake", "edits": ops}) == []
    assert conv({"clause": 0, "target": "the big old blue mountain lake", "edits": ops}) == []
    assert conv({"clause": 0, "target": "lake 2", "edits": ops}) == []
    assert conv({"clause": 0, "target": "lake", "edits": [{"op": "dehaze", "value": 5}]}) == []
    assert conv({"clause": 0, "target": "lake", "edits": [{"op": "exposure", "value": math.nan}]}) == []
    (good,) = conv({"clause": 0, "target": "Lake", "edits": ops})
    assert good.target == "lake" and good.source == "make the lake pop" and good.ops == {"exposure": 0.3}
    (whole,) = conv({"clause": 0, "target": "Whole Photo", "edits": ops})
    assert whole.target == "global"


class _FakeLlm:
    name = "llm"

    def __init__(self):
        self.asked = []

    def decide(self, ctx):
        return decide_mod.RulesDecider().decide(ctx)

    def translate(self, clauses, ops):
        self.asked.append(list(clauses))
        return [{"clause": 0, "target": "lake", "edits": [{"op": "exposure", "value": 0.3}]}]


def _build(fake, **cfg):
    import autoedit.pipeline as pl
    real, pl.make_decider = pl.make_decider, lambda c: fake
    try:
        return Pipeline.build(RunConfig(backend="stub", edit="make the lake pop, brighten the sky", **cfg))
    finally:
        pl.make_decider = real


def test_pipeline_uses_the_translation():
    with _Env():
        fake = _FakeLlm()
        pipe = _build(fake)
        assert [(a.target, a.source) for a in pipe.adjustments] == [
            ("sky", "brighten the sky"), ("lake", "make the lake pop")]
        assert pipe.adjust_problems == [] and fake.asked == [["make the lake pop"]]
        # translation switched off, or a decider that cannot translate: the clause stays rejected
        fake = _FakeLlm()
        pipe = _build(fake, llm_translate_instructions=False)
        assert fake.asked == [] and len(pipe.adjust_problems) == 1
        pipe = _build(decide_mod.RulesDecider())
        assert [a.target for a in pipe.adjustments] == ["sky"] and len(pipe.adjust_problems) == 1


def test_no_default_run_touches_the_llm():
    with _Env():
        pipe = Pipeline.build(RunConfig(backend="stub", edit="make the lake pop, brighten the sky"))
        assert pipe.decider.name == "rules" and len(pipe.adjust_problems) == 1


# ---- review round 2: what may go to the model, and what comes back -------------------------------

class _Answers(_Client):
    """Answers every numbered clause it is shown with a sky adjustment, and adds one item for a
    clause number that was never shown (a model can say anything)."""
    def __init__(self):
        super().__init__()
        self.shown = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        text = kwargs["messages"][0]["content"]
        lines = text.split("Instructions, numbered:\n")[1].split("\nCall the")[0].splitlines()
        self.shown.append([ln.split(": ", 1)[1] for ln in lines])
        items = [{"clause": int(ln.split(":")[0]), "target": "sky",
                  "edits": [{"op": "saturation", "value": -10}]} for ln in lines]
        items.append({"clause": len(lines), "target": "face", "edits": [{"op": "exposure", "value": 0.3}]})
        items.append({"clause": -1, "target": "face", "edits": [{"op": "exposure", "value": 0.3}]})
        return _tool_reply({"adjustments": items}, name="set_adjustments")


def _build_with(decider, text):
    import autoedit.pipeline as pl
    real, pl.make_decider = pl.make_decider, lambda c: decider
    try:
        return Pipeline.build(RunConfig(backend="stub", edit=text))
    finally:
        pl.make_decider = real


def test_a_clause_rejected_for_safety_is_never_sent_to_the_model():
    with _Env():
        client = _Answers()
        pipe = _build_with(_decider(client), "don't brighten the face, make the sky moodier")
        assert client.shown == [["make the sky moodier"]]          # only the plainly not understood clause
        assert [(a.target, a.source) for a in pipe.adjustments] == [("sky", "make the sky moodier")]
        assert not any(a.target == "face" for a in pipe.adjustments)
        (problem,) = pipe.adjust_problems                           # the negation stays a reported problem
        assert "negation or exclusion" in problem and "face" in problem
        # nothing left to translate: no request at all
        client = _Answers()
        pipe = _build_with(_decider(client), "everything but the sky darker, without any warming")
        assert client.requests == [] and len(pipe.adjust_problems) == 2


def test_a_translation_needs_a_real_clause_number_and_finite_values():
    ops = [{"op": "exposure", "value": 0.3}]
    clauses = ["first", "second"]
    for bad in (-1, 2, True, 1.0, "1", None):
        assert instructions.from_translation(
            [{"clause": bad, "target": "lake", "edits": ops}], clauses, _VOCAB, {"lake"}) == [], bad
    (ok,) = instructions.from_translation([{"clause": 1, "target": "lake", "edits": ops}], clauses, _VOCAB, {"lake"})
    assert ok.source == "second"
    from autoedit.decide.llm_decider import _from_pairs
    got = _from_pairs({"global": [{"op": "exposure", "value": math.nan}, {"op": "contrast", "value": 5}],
                       "regions": [{"name": "sky", "edits": [{"op": "saturation", "value": math.inf},
                                                             {"op": "clarity", "value": -math.inf},
                                                             {"op": "temp", "value": 4}]}]})
    assert got == {"global": {"contrast": 5}, "regions": {"sky": {"temp": 4}}}


def test_one_process_asks_the_model_once_for_the_same_text():
    with _Env():
        client = _Answers()
        d = _decider(client)
        first = _build_with(d, "make the sky moodier")
        second = _build_with(d, "make the sky moodier")
        assert len(client.requests) == 1
        assert [a.target for a in second.adjustments] == [a.target for a in first.adjustments] == ["sky"]
        _build_with(d, "make the lake moodier")                     # other text is another question
        assert len(client.requests) == 2
