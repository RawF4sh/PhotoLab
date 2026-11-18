"""The /lab test UI (F38): the route, and static checks of the one page. No browser."""

import json
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "autoedit", "web", "static")
SNAPSHOT = os.path.join(HERE, "snapshots", "api_v1.json")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _lab():
    return _read(os.path.join(STATIC, "lab.html"))


def _script(html):
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1
    return scripts[0]


def _client():
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return None
    from autoedit import paths
    from autoedit.web import app as webapp

    return TestClient(webapp.create_app(paths.configs_dir(), local_only=True),
                      base_url="http://localhost")


def test_the_lab_route_serves_the_page():
    client = _client()
    if client is None:
        return
    r = client.get("/lab")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == _lab()
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'none'", "connect-src 'self'", "img-src 'self' data:"):
        assert part in csp
    assert client.get("/lab", headers={"Host": "evil.com"}).status_code == 403
    for route, name in (("/", "studio.html"), ("/classic", "index.html")):
        r = client.get(route)
        assert r.text == _read(os.path.join(STATIC, name))
        assert "content-security-policy" not in r.headers


def test_the_page_makes_no_outside_request():
    html = _lab()
    for bad in ("http://", "https://", "@import", "<iframe", "<script src", 'target="_blank"'):
        assert bad not in html, bad
    for form in re.findall(r"<form[^>]*>", html):
        assert "action" not in form
    assert not re.search(r"""(?:src|href)\s*=\s*["']//""", html)
    assert re.findall(r"<link[^>]*>", html) == ['<link rel="icon" href="data:,">']
    for style in re.findall(r"<style>(.*?)</style>", html, re.S):
        assert "url(" not in style


def test_the_page_builds_no_markup_from_text():
    js = _script(_lab())
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in js, bad
    assert not re.search(r"set(Timeout|Interval)\(\s*['\"`]", js)


def test_the_page_calls_only_v1_routes_that_exist():
    js = _script(_lab())
    found = set(re.findall(r"""['"`](/api/[^'"`?$]*)""", js))
    paths = [k.split(" ", 1)[1] for k in json.loads(_read(SNAPSHOT))]
    for f in found:
        assert f.startswith("/api/v1/"), f
        assert any(p.startswith(f) or f.startswith(re.sub(r"\{.*", "", p)) for p in paths), f
    for need in ("preview", "pick", "groups", "thumb", "run-one", "jobs", "variants",
                 "settings/schema", "genres", "style/suggestions", "version"):
        assert any(need in f for f in found), need


def test_the_script_parses(tmp_path):
    node = shutil.which("node")
    if node is None:
        return
    p = tmp_path / "lab.js"
    p.write_text(_script(_lab()), encoding="utf-8")
    subprocess.run([node, "--check", str(p)], check=True)


def test_the_page_keeps_the_basics():
    html = _lab()
    for need in ('<html lang="en">', 'name="viewport"', "<title>AutoEdit Lab</title>",
                 "color-scheme: dark", "prefers-reduced-motion: reduce", ":focus-visible",
                 'aria-live="polite"', "function loadFolderPath(", "function previewBody(",
                 "const LAB", "'autoedit.lab.v1'"):
        assert need in html, need
    assert len(re.findall(r"<main", html)) == 1
    assert "autoedit.followups" not in html
