import subprocess
import sys
import types
import urllib.request
from pathlib import Path

from autoedit.web import desktop

ROOT = Path(__file__).resolve().parent.parent


def _have_server():
    try:
        import fastapi, uvicorn  # noqa: F401
        return True
    except ImportError:
        return False


def test_free_port():
    assert isinstance(desktop.free_port(), int) and desktop.free_port() > 0


def test_start_server_answers():
    if not _have_server():
        return
    url, server, thread = desktop.start_server(str(ROOT / "configs"))
    try:
        assert url.endswith(desktop.PAGE)
        assert urllib.request.urlopen(url, timeout=10).status == 200
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_run_without_webview_names_the_extra():
    saved = sys.modules.get("webview")
    sys.modules["webview"] = None
    try:
        try:
            desktop.run(str(ROOT / "configs"))
        except ImportError as e:
            assert "[app] extra" in str(e)
        else:
            raise AssertionError("expected ImportError")
    finally:
        if saved is None:
            sys.modules.pop("webview", None)
        else:
            sys.modules["webview"] = saved


def test_run_opens_page_and_stops_server():
    if not _have_server():
        return
    calls = []
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: calls.append(("window", a, k))
    fake.start = lambda **k: calls.append(("start", k))
    saved = sys.modules.get("webview")
    sys.modules["webview"] = fake
    try:
        desktop.run(str(ROOT / "configs"), debug=True)
    finally:
        if saved is None:
            sys.modules.pop("webview", None)
        else:
            sys.modules["webview"] = saved
    assert calls[0][1][1].endswith(desktop.PAGE)
    assert calls[1] == ("start", {"debug": True})


def test_server_thread_stops_after_run_also_when_webview_raises():
    if not _have_server():
        return
    import threading

    def go(start):
        fake = types.ModuleType("webview")
        fake.create_window = lambda *a, **k: None
        fake.start = start
        saved = sys.modules.get("webview")
        sys.modules["webview"] = fake
        try:
            desktop.run(str(ROOT / "configs"))
        finally:
            sys.modules.pop("webview", None)
            if saved is not None:
                sys.modules["webview"] = saved

    def boom(**k):
        raise RuntimeError("no window")

    for start in (lambda **k: None, boom):
        before = {t for t in threading.enumerate()}
        try:
            go(start)
        except RuntimeError:
            pass
        assert [t for t in threading.enumerate() if t not in before and t.is_alive()] == []


def test_app_help_parses():
    r = subprocess.run([sys.executable, "-m", "autoedit.cli", "app", "--help"], capture_output=True,
                       text=True, env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""})
    assert r.returncode == 0 and "--debug" in r.stdout, r.stderr
