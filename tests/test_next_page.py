"""The interface at / (UI-minimal.md, U1, U2, U4): its routes and static checks of its files. No browser."""

import glob
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "src", "autoedit", "web", "static", "app")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _files(*exts):
    return sorted(p for p in glob.glob(os.path.join(APP, "*")) if p.endswith(exts))


def _client():
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return None
    from autoedit import paths
    from autoedit.web import app as webapp

    return TestClient(webapp.create_app(paths.configs_dir(), local_only=True), base_url="http://localhost")


def test_the_root_route_serves_the_page_with_a_strict_policy():
    client = _client()
    if client is None:
        return
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == _read(os.path.join(APP, "index.html"))
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'self'", "img-src 'self' data:"):
        assert part in csp, part
    assert "frame-ancestors 'none'" in csp and r.headers["x-frame-options"] == "DENY"
    for old in ("/classic", "/lab", "/next"):
        assert client.get(old).status_code == 404, old
    assert client.get("/", headers={"Host": "evil.com"}).status_code == 403


def test_the_app_files_are_served_and_nothing_else():
    client = _client()
    if client is None:
        return
    r = client.get("/app/main.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/app/main.js", headers={"Host": "evil.com"}).status_code == 403
    assert client.get("/app/app.css").headers["content-type"].startswith("text/css")
    for bad in ("/app/../app.py", "/app/x.py", "/app/index.html", "/app/nope.js"):
        assert client.get(bad).status_code == 404, bad


def test_no_markup_from_text_and_no_inline_code():
    for p in _files(".js"):
        js = _read(p)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert bad not in js, (p, bad)
    html = _read(os.path.join(APP, "index.html"))
    for bad in ("innerHTML", "eval("):
        assert bad not in html
    for tag in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        assert tag.strip() == "", "inline script"
    assert "<style" not in html
    for tag in re.findall(r"<[a-z][^>]*>", html):
        assert not re.search(r"\son[a-z]+\s*=", tag), tag
        assert not re.search(r"\sstyle\s*=", tag), tag
    assert "http://" not in html and "https://" not in html


def test_relative_imports_point_to_files_that_exist():
    for p in _files(".js"):
        for target in re.findall(r"""from\s+['"](\./[^'"]+)['"]""", _read(p)):
            assert os.path.isfile(os.path.join(APP, target)), (p, target)


def test_the_stylesheet_defines_the_stage_and_both_themes():
    css = _read(os.path.join(APP, "app.css"))
    assert re.search(r":root\s*\{[^}]*--stage:\s*#777777", css)
    assert "prefers-color-scheme: dark" in css and "prefers-reduced-motion: reduce" in css
    assert ":focus-visible" in css and "dark-surround" in css
    assert "url(" not in css and "gradient" not in css


def test_the_scripts_parse():
    node = shutil.which("node")
    if node is None:
        return
    for p in _files(".js"):
        subprocess.run([node, "--check", p], check=True)
