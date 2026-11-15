"""F13: the settings schema (what a future interface renders its controls from)."""

import contextlib
import io
import json
import os
import shutil

import pytest
from pydantic import BaseModel

from autoedit import cli, paths, settings_schema
from autoedit.contract import RunConfig

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "snapshots", "settings_schema.json")


def _packaged_schema() -> dict:
    return settings_schema.schema(paths._packaged())


def _by_key(data: dict) -> dict:
    return {s["key"]: s for s in data["settings"]}


def test_every_field_is_described_or_hidden():
    doc = settings_schema.described(paths._packaged())
    hidden, words, groups = set(doc["hidden"]), set(doc["setting"]), set(doc["groups"])
    assert set(RunConfig.model_fields) == hidden | words
    assert not hidden & words
    for key, w in doc["setting"].items():
        assert w.get("label", "").strip(), key
        assert w.get("group") in groups, key
        assert 0 < len(w.get("help", "")) <= 140, key


def test_types_defaults_and_limits():
    s = _by_key(_packaged_schema())
    assert s["intensity"]["type"] == "float"
    assert (s["intensity"]["min"], s["intensity"]["max"], s["intensity"]["default"]) == (0.0, 2.0, 0.8)
    assert s["backend"]["type"] == "choice" and s["backend"]["choices"] == ["stub", "sam3", "auto"]
    assert s["levels"]["type"] == "bool" and s["levels"]["default"] is False
    assert s["edit"]["type"] == "string" and s["edit"]["max_length"] == 400 and s["edit"]["default"] is None
    assert s["concepts"]["type"] == "list"


def test_choices_from_config_files(tmp_path):
    base = paths._packaged()
    s = _by_key(settings_schema.schema(base))
    from autoedit.looks import load as load_looks
    from autoedit.styles import load_styles

    assert s["style"]["choices"] == list(load_styles(os.path.join(base, "styles.toml")))
    assert s["look"]["choices"] == list(load_looks(os.path.join(base, "looks.toml")))
    assert {"auto", "off"} <= set(s["palette"]["choices"])

    copy = str(tmp_path / "configs")
    shutil.copytree(base, copy)
    with open(os.path.join(copy, "looks.toml"), "a", encoding="utf-8") as fh:
        fh.write("\n[my_extra_look]\ncontrast_mul = 1.1\n")
    assert "my_extra_look" not in s["look"]["choices"]
    assert "my_extra_look" in _by_key(settings_schema.schema(copy))["look"]["choices"]


def test_no_secret_and_no_path():
    text = json.dumps(_packaged_schema())
    keys = set(_by_key(_packaged_schema()))
    assert "api_key" not in keys and not [k for k in keys if k.endswith("_path")]
    assert os.path.abspath(paths._packaged()) not in text and os.path.expanduser("~") not in text


def test_snapshot():
    got = _packaged_schema()
    if os.environ.get("AUTOEDIT_UPDATE_SNAPSHOTS") == "1":
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "w", encoding="utf-8") as fh:
            json.dump(got, fh, indent=2)
            fh.write("\n")
        return
    with open(SNAPSHOT, encoding="utf-8") as fh:
        assert got == json.load(fh), "settings changed; if intended, rerun with AUTOEDIT_UPDATE_SNAPSHOTS=1"


def test_cli_and_api():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(["settings", "--json", "--configs-dir", paths._packaged()]) == 0
    assert json.loads(buf.getvalue()) == _packaged_schema()

    try:
        from fastapi.testclient import TestClient
    except Exception:
        return
    from autoedit.web import app as webapp

    client = TestClient(webapp.create_app(paths._packaged(), local_only=True), base_url="http://localhost")
    assert client.get("/api/v1/settings/schema").json() == _packaged_schema()
    assert client.get("/api/settings/schema").json() == _packaged_schema()


def test_unknown_annotation_is_an_error():
    class M(BaseModel):
        odd_field: dict = {}

    with pytest.raises(TypeError) as e:
        settings_schema._kind("odd_field", M.model_fields["odd_field"].annotation)
    assert "odd_field" in str(e.value)
