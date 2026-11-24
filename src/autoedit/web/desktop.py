"""`autoedit app`: the local UI in a native window (pywebview), over a private loopback server."""

from __future__ import annotations

import errno
import hashlib
import os
import socket
import threading
import time
from pathlib import Path

# The interface is the page at "/".
PAGE = "/"
_DESKTOP_PORT = 18765

_NEEDS = ("autoedit app needs the [app] extra: pip install 'autoedit[app]' "
          "(on Linux a GTK or Qt backend is also needed)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bound_socket(port: int = 0) -> socket.socket:
    """Bind once and hand the socket to uvicorn, so no other process can take the port in between."""
    sock = socket.socket()
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        raise
    return sock


def start_server(configs_dir: str, profile: str | None = None, port: int = 0):
    import uvicorn

    from .app import create_app

    sock = _bound_socket(port)
    port = sock.getsockname()[1]
    app = create_app(configs_dir, local_only=True, profile=profile)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            server.should_exit = True
            sock.close()
            raise RuntimeError("the local server did not start")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}{PAGE}", server, thread


def _window_picker(webview):
    """The server's picker in the desktop app: the window's own dialog, so it opens on the window.
    Same process as the server, so no page bridge is needed (the page's CSP forbids pywebview's)."""
    fd = getattr(webview, "FileDialog", None)
    from ..imageio import picker_extensions

    def pick(kind: str):
        if not webview.windows:
            return None
        if kind == "folder":
            chosen = webview.windows[0].create_file_dialog(getattr(fd, "FOLDER", None) or webview.FOLDER_DIALOG)
        else:
            types = ("JSON (*.json)",) if kind == "json" else (f"Images ({';'.join(picker_extensions())})",)
            chosen = webview.windows[0].create_file_dialog(getattr(fd, "OPEN", None) or webview.OPEN_DIALOG,
                                                           file_types=types)
        return chosen[0] if chosen else None
    return pick


def _profile_port(profile: str | None) -> int:
    """Web storage belongs to an origin. Reopen the same library for each desktop profile."""
    location = profile or os.environ.get("AUTOEDIT_PROFILE")
    if location is None:
        from ..paths import default_profile_dir
        location = default_profile_dir()
    if not location:
        return _DESKTOP_PORT
    digest = hashlib.sha256(os.fsencode(Path(location).resolve())).digest()
    return 20000 + int.from_bytes(digest[:2], "big") % 20000


def run(configs_dir: str, profile: str | None = None, debug: bool = False) -> None:
    try:
        import webview
    except ImportError:
        raise ImportError(_NEEDS) from None
    port = _profile_port(profile)
    try:
        url, server, thread = start_server(configs_dir, profile, port=port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise RuntimeError(f"AutoEdit's local port {port} is busy. Close the other window or service and try again.") from exc
        raise
    from . import service
    previous_picker = service.NATIVE_PICKER
    try:
        service.NATIVE_PICKER = _window_picker(webview)   # /api/v1/pick opens the window's own dialog
        settings = getattr(webview, "settings", None)
        if settings is not None:
            settings["ALLOW_DOWNLOADS"] = True   # "Export" of a recipe saves a file the page made; off by default
        webview.create_window("AutoEdit", url, width=1280, height=820, min_size=(900, 600),
                             confirm_close=True)   # a running batch would be lost
        webview.start(debug=debug, private_mode=False)
    finally:
        service.NATIVE_PICKER = previous_picker
        server.should_exit = True
        thread.join(timeout=5)
