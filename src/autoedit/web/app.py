"""FastAPI layer — a thin wrapper over service.py. FastAPI/uvicorn are imported lazily
(the `[ui]` extra) so `import autoedit` stays dependency-free."""

from __future__ import annotations

import os

from . import service


def create_app(configs_dir: str = "configs"):
    from fastapi import Body, FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="AutoEdit")
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as fh:
            return fh.read()

    @app.get("/api/styles")
    def styles():
        return service.list_styles(configs_dir)

    @app.get("/api/presets")
    def presets():
        return service.list_presets(configs_dir)

    @app.get("/api/list")
    def listing(dir: str = ""):
        return service.list_images(dir)

    @app.get("/api/pick")
    def pick(kind: str = "folder"):
        return {"path": service.pick_path(kind)}

    @app.post("/api/preview")
    def preview(payload: dict = Body(...)):
        folder, file = payload.get("dir", ""), payload.get("file", "")
        path = os.path.join(folder, file) if folder and file else (file or folder)
        return service.preview(path, payload, configs_dir)

    @app.post("/api/run")
    def run(payload: dict = Body(...)):
        return service.run_batch(payload["input_dir"], payload.get("out_dir", "out"), payload, configs_dir)

    @app.post("/api/run-one")
    def run_one(payload: dict = Body(...)):
        return service.run_one(payload["file"], payload.get("out_dir", "out"), payload, configs_dir)

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, configs_dir: str = "configs",
          open_browser: bool = True) -> None:
    import uvicorn

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"AutoEdit UI → http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(configs_dir), host=host, port=port)
