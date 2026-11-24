"""The local web API contract: versioned routes, one error shape, progress events, and a
snapshot of the route list so a change to it is deliberate. Skipped without the [ui] extra."""

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, "snapshots", "api_v1.json")


def _client(**kw):
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return None
    from autoedit import paths
    from autoedit.web import app as webapp

    return TestClient(webapp.create_app(paths.configs_dir(), local_only=True),
                      base_url="http://localhost", **kw)


def _projection(spec: dict) -> dict:
    """Method, path, parameter names, body or not. No schema names, codes or descriptions: those
    differ between FastAPI versions."""
    out = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            params = op.get("parameters", [])
            out[f"{method.upper()} {path}"] = {
                "path": sorted(p["name"] for p in params if p["in"] == "path"),
                "query": sorted(p["name"] for p in params if p["in"] == "query"),
                "body": "requestBody" in op,
            }
    return dict(sorted(out.items()))


def test_contract_snapshot():
    client = _client()
    if client is None:
        return
    got = _projection(client.app.openapi())
    if os.environ.get("AUTOEDIT_UPDATE_SNAPSHOTS") == "1":
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "w", encoding="utf-8") as fh:
            json.dump(got, fh, indent=2)
            fh.write("\n")
        print(f"rewrote {SNAPSHOT}")
        return
    with open(SNAPSHOT, encoding="utf-8") as fh:
        assert got == json.load(fh), "API surface changed; if intended, rerun with AUTOEDIT_UPDATE_SNAPSHOTS=1"


def test_only_the_versioned_api_exists():
    client = _client()
    if client is None:
        return
    from autoedit import __version__

    assert client.get("/api/v1/styles").status_code == 200 and client.get("/api/styles").status_code == 404
    v = client.get("/api/v1/version").json()
    assert v["api"] == "v1" and v["engine"] == __version__
    assert not [p for p in client.app.openapi()["paths"] if p.startswith("/api/") and not p.startswith("/api/v1/")]


def test_ai_edit_catalog_has_a_prompt_free_standard_and_distinct_briefs():
    from autoedit.decide.gemini import MAX_PROMPT
    from autoedit.web import ai_styles

    styles = ai_styles.catalog()
    assert styles[0] == {"id": "standard", "label": "Standard"}
    prompts = {ai_styles.prompt_for(style["id"]) for style in styles}
    assert len(prompts) == len(styles) >= 2
    for style in styles:
        direction = ai_styles.prompt_for(style["id"], "Keep the foreground cool.")
        assert direction.endswith("Keep the foreground cool.") and len(direction) <= MAX_PROMPT
    client = _client()
    if client:
        catalog = client.get("/api/v1/ai-edits").json()
        assert catalog["styles"] == styles and catalog["remaining_calls"] >= 0


def test_error_shape_and_codes(tmp_path):
    client = _client()
    if client is None:
        return
    from autoedit.web import service

    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service.approve_root(str(tmp_path))
    bodies = []

    r = client.post("/api/v1/run", json={})
    assert r.status_code == 400 and r.json()["code"] == "bad_request" and "input_dir" in r.json()["error"]
    bodies.append(r.json())
    r = client.get("/api/v1/jobs/nope")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    bodies.append(r.json())
    r = client.get("/api/v1/styles", headers={"Host": "evil.com"})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    bodies.append(r.json())
    r = client.get("/api/v1/thumb", params={"path": str(tmp_path / "missing.jpg")})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    bodies.append(r.json())
    r = client.get("/api/v1/thumb")                     # missing query parameter
    assert r.status_code == 422 and r.json()["code"] == "bad_request"
    bodies.append(r.json())
    for b in bodies:
        assert set(b) == {"error", "code"} and isinstance(b["error"], str)


def test_unexpected_exception_is_internal():
    client = _client(raise_server_exceptions=False)
    if client is None:
        return
    from autoedit.web import service

    def boom(*a, **k):
        raise RuntimeError("boom")

    orig = service.list_styles
    service.list_styles = boom
    try:
        r = client.get("/api/v1/styles")
    finally:
        service.list_styles = orig
    assert r.status_code == 500 and r.json()["code"] == "internal" and "boom" in r.json()["error"]


def test_job_events_stream(tmp_path):
    client = _client()
    if client is None:
        return
    import numpy as np
    from PIL import Image

    from autoedit.web import service

    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    src = tmp_path / "in"
    src.mkdir()
    for n in ("a", "b"):
        Image.fromarray(np.full((48, 64, 3), 120, np.uint8)).save(src / f"{n}.png")
    service.approve_root(str(tmp_path))

    r = client.post("/api/v1/jobs", json={"input_dir": str(src), "out_dir": str(tmp_path / "out"),
                                          "backend": "stub", "style": "natural"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    events, deadline = [], time.time() + 60
    with client.stream("GET", f"/api/v1/jobs/{job_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        name = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
            assert time.time() < deadline, "stream did not end"
    assert any(n == "snapshot" for n, _ in events)
    last_name, last = events[-1]
    assert last_name == "done" and last["status"] == "done" and last["processed"] == 2

    r = client.get("/api/v1/jobs/nope/events")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
