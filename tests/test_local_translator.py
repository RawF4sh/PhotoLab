"""F20 part 2: the local model that reads instruction clauses the word list missed.

No test reaches a real model or the network: each replaces `urlopen` inside `translate_local` with
a stand-in, by hand, and restores it in `finally`."""

import io
import json
import os
import sys
import tomllib
from urllib.error import URLError

import numpy as np
from PIL import Image as PILImage

from autoedit import instructions, translate_local
from autoedit.contract import RunConfig
from autoedit.paths import config_path
from autoedit.pipeline import Pipeline
from autoedit.web import service

OWNER = "make the sky have a bigger gradient for pink"
TOP = {"adjustments": [{"clause": 0, "target": "top", "size": 1.5,
                        "edits": [{"op": "tint", "value": -40}, {"op": "temp", "value": 6}]}]}


class _Resp:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def read(self, *_):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Ollama:
    """Stands in for urlopen: GET /api/tags lists the models, POST /api/chat answers `answer`
    (a dict, or text for an answer that is not JSON). `down` is Ollama not running."""

    def __init__(self, answer=None, models=("qwen3.5:9b",), down=False):
        self.answer, self.models, self.down, self.calls = answer, models, down, []

    def __call__(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        body = None if isinstance(req, str) or req.data is None else json.loads(req.data)
        self.calls.append((url, timeout, body))
        if self.down:
            raise URLError("connection refused")
        if url.endswith("/api/tags"):
            return _Resp({"models": [{"name": m} for m in self.models]})
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return _Resp({"message": {"role": "assistant", "content": text}, "done": True})

    @property
    def chats(self):
        return [c for c in self.calls if c[0].endswith("/api/chat")]


class _Local:
    """Puts a stand-in in place of urlopen, lifts the runner's switch that keeps tests off a real
    model, and restores everything on exit."""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        self.saved = translate_local.urlopen, os.environ.pop("AUTOEDIT_NO_LOCAL_MODEL", None)
        translate_local.urlopen = self.fake
        translate_local._UP.clear()
        instructions._TRANSLATIONS.clear()
        return self.fake

    def __exit__(self, *exc):
        translate_local.urlopen = self.saved[0]
        if self.saved[1] is not None:
            os.environ["AUTOEDIT_NO_LOCAL_MODEL"] = self.saved[1]
        translate_local._UP.clear()
        instructions._TRANSLATIONS.clear()


def _photo(tmp_path):
    a = np.zeros((48, 64, 3), np.uint8)
    a[:] = (110, 130, 170)
    path = str(tmp_path / "p.png")
    PILImage.fromarray(a, "RGB").save(path)
    return path


def _vocab():
    return instructions.load_vocab(config_path("instructions.toml"))


def test_unavailable_does_nothing(tmp_path):
    path = _photo(tmp_path)
    cfg = RunConfig(backend="stub")
    err, sys.stderr = sys.stderr, io.StringIO()
    try:
        with _Local(_Ollama(down=True)) as fake:
            assert translate_local.available(cfg) is False
            assert translate_local.translate(["make the sky pop"], ["tint"], ["top"], cfg) == []
            on, r_on = Pipeline.build(RunConfig(backend="stub", edit=OWNER)).process_one(path)
            assert [c[0] for c in fake.calls] and not fake.chats
        printed = sys.stderr.getvalue()
        with _Local(_Ollama(TOP)) as fake:
            off, r_off = Pipeline.build(RunConfig(backend="stub", edit=OWNER, translator="off")).process_one(path)
            assert fake.calls == []                                   # off: not even a look
            assert translate_local.available(cfg) is True
            os.environ["AUTOEDIT_NO_LOCAL_MODEL"] = "1"               # the runner's switch beats a running Ollama
            try:
                assert translate_local.available(cfg) is False
            finally:
                del os.environ["AUTOEDIT_NO_LOCAL_MODEL"]
            # a model installed under another name is no model
            assert translate_local.available(RunConfig(local_model="other:1b")) is False
            # the clause text only goes to this computer, whatever the address says
            assert translate_local.available(RunConfig(local_url="http://example.com:11434")) is False
    finally:
        sys.stderr = err
    assert printed == ""
    assert np.array_equal(on, off) and r_on.notes == r_off.notes and r_on.adjustments == r_off.adjustments


def test_request_shape(tmp_path):
    with _Local(_Ollama(TOP)) as fake:
        pipe = Pipeline.build(RunConfig(backend="stub", edit=f"{OWNER}, brighten the sky"))
        (url, timeout, body), = fake.chats
        cfg = pipe.cfg
        assert url == cfg.local_url + "/api/chat" and timeout == cfg.local_timeout_s
        assert body["stream"] is False and body["think"] is False and body["model"] == cfg.local_model
        assert body["options"]["temperature"] == 0 and "seed" in body["options"]
        ops = sorted(_vocab().limits)
        item = body["format"]["properties"]["adjustments"]["items"]
        assert item["properties"]["edits"]["items"]["properties"]["op"]["enum"] == ops
        assert set(item["required"]) == {"clause", "target", "edits", "size"}
        user = [m for m in body["messages"] if m["role"] == "user"]
        assert [m["content"] for m in user] == [f"Requests:\n0: {OWNER}"]   # the understood clause stays home
        sent = json.dumps(body)
        assert str(tmp_path) not in sent and "brighten the sky" not in sent
        assert "mean_lum" not in sent and "coverage" not in sent
        system = [m["content"] for m in body["messages"] if m["role"] == "system"][0]
        for place in sorted(_vocab().places):
            assert place in system
        assert "tint -100" in system                  # the engine's units, from the colour verbs
    # the worked examples are not the sentences the measurement uses
    with open(os.path.join(os.path.dirname(__file__), "..", "scripts", "data", "phrasings.toml"), "rb") as fh:
        phrasings = {p["text"].lower() for p in tomllib.load(fh)["phrase"]}
    with open(config_path("instructions.toml"), "rb") as fh:
        shots = list(tomllib.load(fh)["translator"]["examples"].values())
    assert shots and not phrasings & {s["text"].lower() for s in shots}


def test_answer_becomes_adjustments(tmp_path):
    path = _photo(tmp_path)
    with _Local(_Ollama(TOP)):
        pipe = Pipeline.build(RunConfig(backend="stub", edit=OWNER))
        (a,) = pipe.adjustments
        assert a.target == "place:top" and a.via == "local" and a.size == 1.5 and a.source == OWNER
        assert a.ops == {"tint": -40.0, "temp": 6.0} and pipe.adjust_problems == []
        out, result = pipe.process_one(path)
        (done,) = result.adjustments
        assert done.status == "applied" and done.via == "local"
        note = [n for n in result.notes if OWNER in n]
        assert note and "read by qwen3.5:9b on this computer" in note[0], note
        base, _ = Pipeline.build(RunConfig(backend="stub")).process_one(path)
        assert not np.array_equal(out, base)
    sky = {"adjustments": [{"clause": 0, "target": "sky", "size": 1.0, "edits": [{"op": "tint", "value": -40}]}]}
    with _Local(_Ollama(sky)):
        (a,) = Pipeline.build(RunConfig(backend="stub", edit=OWNER)).adjustments
        assert a.target == "sky" and a.via == "local" and a.size == 1.0


def test_bad_answers_are_dropped(tmp_path):
    def edit(op="tint", value=-40, **item):
        return {"adjustments": [{"clause": 0, "target": "sky", "size": 1.0,
                                 "edits": [{"op": op, "value": value}], **item}]}

    answers = [
        "sorry, I cannot do that", "", "[1, 2]", {"adjustments": "none"}, {"adjustments": [3, "x", None]},
        {"adjustments": [{"clause": 0}]}, {"nothing": 1},
        edit(op="dehaze"), edit(value=float("nan")), edit(value=float("inf")), edit(clause=5), edit(clause=-1),
        edit(target="the big old blue mountain lake"),
    ]
    for answer in answers:
        err, sys.stderr = sys.stderr, io.StringIO()
        try:
            with _Local(_Ollama(answer)):
                pipe = Pipeline.build(RunConfig(backend="stub", edit="make the sky pop like a movie"))
        finally:
            sys.stderr = err
        assert pipe.adjustments == [] and len(pipe.adjust_problems) == 1, answer
    with _Local(_Ollama(edit(size=99))):
        (a,) = Pipeline.build(RunConfig(backend="stub", edit="make the top pop like a movie")).adjustments
        assert a.size == 1.0                    # an object keeps no size
    with _Local(_Ollama(edit(target="top", size=99))):
        (a,) = Pipeline.build(RunConfig(backend="stub", edit="make the top pop like a movie")).adjustments
        assert a.target == "place:top" and a.size == 4.0


def test_rejected_clauses_are_never_sent():
    with _Local(_Ollama(TOP)) as fake:
        pipe = Pipeline.build(RunConfig(backend="stub", edit=f"don't brighten the face, {OWNER}"))
        (url, timeout, body), = fake.chats
        assert "brighten the face" not in json.dumps(body)
        (problem,) = pipe.adjust_problems
        assert "negation or exclusion" in problem and "face" in problem
    with _Local(_Ollama(TOP)) as fake:
        Pipeline.build(RunConfig(backend="stub", edit="don't brighten the face, nothing without the sky"))
        assert fake.chats == []                 # nothing translatable: no request


class _Cloud:
    """A cloud decider that must never be asked about a follow-up."""
    name = "llm"
    model = "cloud-model"

    def __init__(self):
        self.asked = []

    def translate(self, clauses, ops):
        self.asked.append(clauses)
        return [{"clause": 0, "target": "sky", "edits": [{"op": "exposure", "value": 0.3}]}]


def test_follow_ups_are_translated_locally_never_in_the_cloud():
    with _Local(_Ollama(TOP)) as fake:
        pipe = Pipeline.build(RunConfig(backend="stub"))
        pipe.decider = cloud = _Cloud()
        adjs, problems = service.photo_adjustments(pipe, OWNER)
        (a,) = adjs
        assert a.scope == "photo" and a.via == "local" and a.target == "place:top" and problems == []
        assert cloud.asked == [] and len(fake.chats) == 1
        # the run-wide text of the same pipeline may use the cloud: that is the run's own choice
        translated, rest = instructions.translate_problems(
            [f'not understood: "{OWNER}"'], pipe.vocab(), pipe.known_concepts(), decider=cloud, cfg=pipe.cfg)
        assert cloud.asked == [[OWNER]] and [t.via for t in translated] == ["cloud"] and rest == []
        # translator "local" keeps even the run-wide text off the cloud
        cloud.asked.clear()
        instructions._TRANSLATIONS.clear()
        local_cfg = pipe.cfg.model_copy(update={"translator": "local"})
        translated, _ = instructions.translate_problems(
            [f'not understood: "{OWNER}"'], pipe.vocab(), pipe.known_concepts(), decider=cloud, cfg=local_cfg)
        assert cloud.asked == [] and [t.via for t in translated] == ["local"]
    with _Local(_Ollama(down=True)):
        pipe = Pipeline.build(RunConfig(backend="stub"))
        adjs, problems = service.photo_adjustments(pipe, OWNER)
        assert adjs == [] and len(problems) == 1


def test_cache():
    with _Local(_Ollama(TOP)) as fake:
        for _ in range(2):
            (a,) = Pipeline.build(RunConfig(backend="stub", edit=OWNER)).adjustments
            assert a.target == "place:top"
        assert len(fake.chats) == 1
        Pipeline.build(RunConfig(backend="stub", edit="make the sky have a smaller gradient for pink"))
        assert len(fake.chats) == 2


def _client():
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return None
    from autoedit import paths
    from autoedit.web import app as webapp

    return TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")


def test_status_route():
    client = _client()
    if client is None:
        return
    with _Local(_Ollama()):
        got = client.get("/api/v1/translator/status")
        assert got.status_code == 200 and got.json() == {"available": True, "model": "qwen3.5:9b"}
    with _Local(_Ollama(down=True)):
        assert client.get("/api/v1/translator/status").json() == {"available": False, "model": "qwen3.5:9b"}
    with _Local(_Ollama(models=("other:1b",))):
        assert client.get("/api/v1/translator/status").json()["available"] is False
    # a foreign Host header is refused like on every other route
    assert client.get("/api/v1/translator/status", headers={"host": "evil.example"}).status_code == 403


def test_a_made_up_target_goes_to_the_model():
    # The word list keeps leftover words as the target: "lift the shadows in the trees" became the
    # target "shadows trees". With a model at hand, such a clause is read by the model instead; with
    # none, the word list's reading stays (F2's open vocabulary).
    text = "lift the shadows in the trees"
    trees = {"adjustments": [{"clause": 0, "target": "trees", "size": 1.0, "edits": [{"op": "exposure", "value": 0.3}]}]}
    alone = Pipeline.build(RunConfig(backend="stub", edit=text, translator="off")).adjustments
    assert [a.target for a in alone] == ["shadows trees"]
    with _Local(_Ollama(trees)):
        (a,) = Pipeline.build(RunConfig(backend="stub", edit=text)).adjustments
    assert a.target in ("tree", "trees") and a.via == "local" and a.source == text
    # a known single-word target is never second-guessed
    with _Local(_Ollama(trees)):
        (b,) = Pipeline.build(RunConfig(backend="stub", edit="brighten the sky")).adjustments
    assert b.target == "sky" and b.via == ""
