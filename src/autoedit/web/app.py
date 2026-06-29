"""FastAPI layer — a thin wrapper over service.py. FastAPI/uvicorn are imported lazily
(the `[ui]` extra) so `import autoedit` stays dependency-free."""

from __future__ import annotations

import os

from . import curate_jobs, jobs, service


def create_app(configs_dir: str = "configs"):
    from urllib.parse import urlparse

    from fastapi import Body, FastAPI, Request, Response
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="AutoEdit")
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    _LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

    @app.middleware("http")
    async def _origin_guard(request: Request, call_next):
        """Basic CSRF/cross-origin defense: mutating requests must come from a local origin."""
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname not in _LOCAL_HOSTS:
                return JSONResponse(status_code=403, content={"error": "cross-origin request blocked"})
        return await call_next(request)

    def guard(fn):
        """Turn engine errors (bad file, etc.) into a friendly 400 instead of a 500 traceback."""
        try:
            return fn()
        except PermissionError as e:
            return JSONResponse(status_code=403, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": str(e)})

    def _page(name: str) -> str:
        with open(os.path.join(static_dir, name), encoding="utf-8") as fh:
            return fh.read()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _page("studio.html")          # the premium Studio UI

    @app.get("/classic", response_class=HTMLResponse)
    def classic() -> str:
        return _page("index.html")           # the minimal functional UI

    @app.get("/api/styles")
    def styles():
        return service.list_styles(configs_dir)

    @app.get("/api/presets")
    def presets():
        return service.list_presets(configs_dir)

    @app.get("/api/list")
    def listing(dir: str = ""):
        return guard(lambda: (service.ensure_allowed(dir), service.list_images(dir))[1])

    @app.get("/api/groups")
    def groups(dir: str = ""):
        # RAW+JPEG pairs collapsed into one logical photo (the Studio UI consumes this)
        return guard(lambda: (service.ensure_allowed(dir), service.group_images(dir))[1])

    @app.get("/api/thumb")
    def thumb(path: str, px: int = 360):
        try:
            service.ensure_allowed(path)
            return Response(content=service.thumb_bytes(path, px), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=3600"})
        except PermissionError as e:
            return JSONResponse(status_code=403, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": str(e)})

    @app.get("/api/pick")
    def pick(kind: str = "folder"):
        return {"path": service.pick_path(kind)}

    @app.post("/api/preview")
    def preview(payload: dict = Body(...)):
        folder, file = payload.get("dir", ""), payload.get("file", "")
        path = os.path.join(folder, file) if folder and file else (file or folder)
        return guard(lambda: (service.ensure_allowed(path), service.preview(path, payload, configs_dir))[1])

    @app.post("/api/run")
    def run(payload: dict = Body(...)):
        def go():
            service.ensure_allowed(payload["input_dir"], payload.get("out_dir"))
            return service.run_batch(payload["input_dir"], payload.get("out_dir", "out"), payload, configs_dir)
        return guard(go)

    @app.post("/api/run-one")
    def run_one(payload: dict = Body(...)):
        def go():
            service.ensure_allowed(payload["file"], payload.get("out_dir"))
            return service.run_one(payload["file"], payload.get("out_dir", "out"), payload, configs_dir)
        return guard(go)

    # ── background batch jobs (live per-photo progress) ──
    @app.post("/api/jobs")
    def start_job(payload: dict = Body(...)):
        def go():
            service.ensure_allowed(payload.get("input_dir"), payload.get("out_dir"),
                                   *(payload.get("files") or []))
            return jobs.snapshot(jobs.start(
                payload.get("input_dir"), payload.get("files"), payload.get("out_dir"),
                payload, configs_dir))
        return guard(go)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        j = jobs.get(job_id)
        if j is None:
            return JSONResponse(status_code=404, content={"error": "unknown job"})
        return jobs.snapshot(j)

    @app.post("/api/jobs/{job_id}/control")
    def job_control(job_id: str, payload: dict = Body(...)):
        j = jobs.get(job_id)
        if j is None:
            return JSONResponse(status_code=404, content={"error": "unknown job"})
        jobs.control(j, payload.get("action", ""))
        return jobs.snapshot(j)

    # ── culling jobs (M1): copy a curated subset for review/training ──
    @app.post("/api/curate/jobs")
    def start_curate(payload: dict = Body(...)):
        def go():
            input_dir = payload.get("input_dir") or ""
            out_dir = payload.get("out_dir") or curate_jobs.default_out_dir(input_dir)
            service.ensure_allowed(input_dir, out_dir)
            return curate_jobs.snapshot(curate_jobs.start(input_dir, out_dir, payload))
        return guard(go)

    @app.get("/api/curate/jobs/{job_id}")
    def curate_status(job_id: str):
        j = curate_jobs.get(job_id)
        if j is None:
            return JSONResponse(status_code=404, content={"error": "unknown curate job"})
        return curate_jobs.snapshot(j)

    # ── review labels (M2): persist human verdicts for training ──
    @app.post("/api/review/labels")
    def review_labels(payload: dict = Body(...)):
        def go():
            labels = payload.get("labels") or []
            out_dir = payload.get("out_dir") or (
                os.path.dirname(labels[0]["image"]) if labels and labels[0].get("image") else "")
            service.ensure_allowed(out_dir)
            return service.save_review_labels(out_dir, labels)
        return guard(go)

    # ── dataset builder (M3): pair + preservation-validate + build a training workspace ──
    @app.post("/api/datasets/validate")
    def datasets_validate(payload: dict = Body(...)):
        def go():
            o, t = payload["originals_dir"], payload["targets_dir"]
            service.ensure_allowed(o, t)
            return service.dataset_validate(o, t, bool(payload.get("recursive")))
        return guard(go)

    @app.post("/api/datasets/build")
    def datasets_build(payload: dict = Body(...)):
        def go():
            o, t = payload["originals_dir"], payload["targets_dir"]
            out_dir = payload.get("out_dir") or os.path.join(o, "AutoEdit Dataset")
            service.ensure_allowed(o, t, out_dir)
            return service.dataset_build(o, t, out_dir, payload)
        return guard(go)

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, configs_dir: str = "configs",
          open_browser: bool = True, unsafe_network: bool = False) -> None:
    import uvicorn

    loopback = {"127.0.0.1", "localhost", "::1"}
    if host not in loopback and not unsafe_network:
        raise SystemExit(
            f"refusing to bind a non-loopback host ({host}): the Studio API can read local "
            "images and write exports. Pass --unsafe-network to override (only on a trusted LAN).")
    if host not in loopback:
        print(f"⚠  AutoEdit UI bound to {host} — the local filesystem API is reachable off-machine.")
    if open_browser:
        import threading
        import webbrowser

        opn = host if host not in ("0.0.0.0", "::") else "127.0.0.1"
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{opn}:{port}")).start()
    print(f"AutoEdit UI → http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(configs_dir), host=host, port=port)
