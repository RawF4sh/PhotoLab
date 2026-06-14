"""BYO-key cloud consent gating: the LLM decider only activates with consent + a key."""

import os

from autoedit.contract import RunConfig
from autoedit.decide import DISCLOSURE, consented, make_decider


def _clear():
    return {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "AUTOEDIT_ALLOW_CLOUD")}


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_default_is_rules():
    saved = _clear()
    try:
        assert make_decider(RunConfig()).name == "rules"
    finally:
        _restore(saved)


def test_llm_without_consent_falls_back():
    saved = _clear()
    try:
        assert make_decider(RunConfig(decider="llm")).name == "rules"
    finally:
        _restore(saved)


def test_llm_consent_but_no_key_falls_back():
    saved = _clear()
    try:
        assert make_decider(RunConfig(decider="llm", allow_cloud=True)).name == "rules"
    finally:
        _restore(saved)


def test_llm_consent_and_key_activates():
    saved = _clear()
    try:
        d = make_decider(RunConfig(decider="llm", allow_cloud=True, api_key="sk-test"))
        assert d.name == "llm" and d.api_key == "sk-test"
    finally:
        _restore(saved)


def test_env_consent_works():
    saved = _clear()
    try:
        os.environ["AUTOEDIT_ALLOW_CLOUD"] = "1"
        assert consented(RunConfig()) is True
    finally:
        _restore(saved)


def test_disclosure_is_honest():
    d = DISCLOSURE.lower()
    assert "thumbnail" in d and "api key" in d and "local" in d
