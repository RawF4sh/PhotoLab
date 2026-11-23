"""`autoedit app`: the local UI in a native window (pywebview), over a private loopback server."""

from __future__ import annotations

import socket
import threading
import time

# The interface is the page at "/".
PAGE = "/"

_NEEDS = ("autoedit app needs the [app] extra: pip install 'autoedit[app]' "
          "(on Linux a GTK or Qt backend is also needed)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bound_socket() -> socket.socket:
    """Bind once and hand the socket to uvicorn, so no other process can take the port in between."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


def start_server(configs_dir: str, profile: str | None = None):
    import uvicorn

    from .app import create_app

    sock = _bound_socket()
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

    def pick(kind: str):
        if not webview.windows:
            return None
        if kind == "folder":
            chosen = webview.windows[0].create_file_dialog(getattr(fd, "FOLDER", None) or webview.FOLDER_DIALOG)
        else:
            types = ("JSON (*.json)",) if kind == "json" else ("Images (*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.webp)",)
            chosen = webview.windows[0].create_file_dialog(getattr(fd, "OPEN", None) or webview.OPEN_DIALOG,
                                                           file_types=types)
        return chosen[0] if chosen else None
    return pick


def run(configs_dir: str, profile: str | None = None, debug: bool = False) -> None:
    try:
        import webview
    except ImportError:
        raise ImportError(_NEEDS) from None
    url, server, thread = start_server(configs_dir, profile)
    try:
        from . import service
        service.NATIVE_PICKER = _window_picker(webview)   # /api/v1/pick opens the window's own dialog
        webview.create_window("AutoEdit", url, width=1280, height=820, min_size=(900, 600),
                             confirm_close=True)   # a running batch would be lost
        webview.start(debug=debug)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
