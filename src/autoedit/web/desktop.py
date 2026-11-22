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


def run(configs_dir: str, profile: str | None = None, debug: bool = False) -> None:
    try:
        import webview
    except ImportError:
        raise ImportError(_NEEDS) from None
    url, server, thread = start_server(configs_dir, profile)
    try:
        # Folder picker: /api/v1/pick opens a native dialog server-side; if tkinter is missing
        # on Linux, pywebview's create_file_dialog is the upgrade.
        webview.create_window("AutoEdit", url, width=1280, height=820, min_size=(900, 600),
                             confirm_close=True)   # a running batch would be lost
        webview.start(debug=debug)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
