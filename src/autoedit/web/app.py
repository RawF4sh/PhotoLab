"""FastAPI layer — a thin wrapper over service.py. FastAPI/uvicorn are imported lazily
(the `[ui]` extra) so `import autoedit` stays dependency-free."""

from __future__ import annotations

import json
import os
import time

from . import aesthetic_jobs, curate_jobs, jobs, service


def create_app(configs_dir: str = "configs", local_only: bool = True, profile: str | None = None):
    from urllib.parse import urlparse

    from fastapi import APIRouter, Body, FastAPI, Request, Response
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    app = FastAPI(title="AutoEdit")
    api = APIRouter()
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    _LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

    @app.middleware("http")
    async def _origin_guard(request: Request, call_next):
        """Local-only guard. Two independent defenses:

        1. DNS-rebinding: a hostile web page whose domain re-resolves to 127.0.0.1 would otherwise
           be *same-origin* to the browser and could READ our responses (the user's photo folders,
           thumbnails at any resolution). We reject any request whose Host header isn't a loopback
           name — a rebinding request carries the attacker's Host, not localhost. Applies to every
           method (GETs are the read vector). Disabled when the server is intentionally bound
           off-machine (`--unsafe-network`), where the Host is a LAN name we can't predict.
        2. CSRF: mutating requests must additionally carry a local (or absent) Origin."""
        if local_only:
            host = urlparse(f"//{request.headers.get('host', '')}").hostname
            if host is not None and host not in _LOCAL_HOSTS:
                return JSONResponse(status_code=403,
                                    content={"error": "non-local Host header blocked (DNS-rebinding defense)",
                                         "code": "forbidden"})
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname not in _LOCAL_HOSTS:
                return JSONResponse(status_code=403, content={"error": "cross-origin request blocked", "code": "forbidden"})
        return await call_next(request)

    def _err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": message, "code": code})

    def guard(fn):
        """The one place that maps exceptions to the API error shape {"error", "code"}."""
        try:
            return fn()
        except PermissionError as e:               # before OSError: it is a subclass
            return _err(403, "forbidden", str(e))
        except FileNotFoundError as e:
            return _err(404, "not_found", str(e))
        except (ValueError, KeyError, OSError) as e:
            return _err(400, "bad_request", str(e))
        except Exception as e:
            return _err(500, "internal", str(e))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
        return _err(422, "bad_request", msg)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        return _err(500, "internal", str(exc))

    def _unknown(what: str):
        raise FileNotFoundError(what)

    def _require(payload: dict, *keys: str) -> None:
        """Fail with a field-naming 400 instead of a bare KeyError like {"error": "'input_dir'"}."""
        missing = [k for k in keys if not payload.get(k)]
        if missing:
            raise ValueError("missing required field(s): " + ", ".join(missing))

    def _page(name: str) -> str:
        with open(os.path.join(static_dir, name), encoding="utf-8") as fh:
            return fh.read()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _page("studio.html")          # the premium Studio UI

    @app.get("/classic", response_class=HTMLResponse)
    def classic() -> str:
        return _page("index.html")           # the minimal functional UI

    @api.get("/styles")
    def styles():
        return guard(lambda: service.list_styles(configs_dir, profile))

    @api.get("/presets")
    def presets():
        return guard(lambda: service.list_presets(configs_dir, profile))

    @api.get("/settings/schema")
    def settings_schema():
        from .. import settings_schema as schema_mod
        return guard(lambda: schema_mod.schema(configs_dir, profile))

    @api.get("/translator/status")
    def translator_status():
        return guard(lambda: service.translator_status(configs_dir, profile))

    @api.get("/version")
    def version():
        from .. import __version__
        return {"api": "v1", "engine": __version__}

    @api.get("/list")
    def listing(dir: str = ""):
        if not dir:
            return []
        return guard(lambda: (service.ensure_allowed(dir), service.list_images(dir))[1])

    @api.get("/groups")
    def groups(dir: str = ""):
        if not dir:
            return []
        # RAW+JPEG pairs collapsed into one logical photo (the Studio UI consumes this)
        return guard(lambda: (service.ensure_allowed(dir), service.group_images(dir))[1])

    @api.get("/thumb")
    def thumb(path: str, px: int = 360):
        def go():
            service.ensure_allowed(path)
            if not os.path.exists(path):           # service.check_image reports this as a 400
                raise FileNotFoundError(f"file not found: {path}")
            return Response(content=service.thumb_bytes(path, px), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=3600"})
        return guard(go)

    @api.post("/pick")
    def pick(kind: str = "folder"):
        # POST (not GET): picking pops a native dialog and registers a new approved workspace
        # root — a side effect that must sit behind the mutating-method origin guard above.
        return guard(lambda: {"path": service.pick_path(kind)})

    @api.post("/preview")
    def preview(payload: dict = Body(...)):
        folder, file = payload.get("dir", ""), payload.get("file", "")
        path = os.path.join(folder, file) if folder and file else (file or folder)
        return guard(lambda: (service.ensure_allowed(path), service.preview(path, payload, configs_dir, profile=profile))[1])

    @api.post("/run")
    def run(payload: dict = Body(...)):
        def go():
            input_dir = payload.get("input_dir")
            if not input_dir:
                raise ValueError("input_dir is required")
            # Resolve the default BEFORE the trust-boundary check so output can't land in the
            # server's CWD (an unapproved path). Default is input-relative, matching /api/jobs.
            out_dir = payload.get("out_dir") or os.path.join(input_dir, "AutoEdit Exports")
            service.ensure_allowed(input_dir, out_dir)
            return service.run_batch(input_dir, out_dir, payload, configs_dir, profile)
        return guard(go)

    @api.post("/run-one")
    def run_one(payload: dict = Body(...)):
        def go():
            file = payload.get("file")
            if not file:
                raise ValueError("file is required")
            out_dir = payload.get("out_dir") or os.path.join(os.path.dirname(file), "AutoEdit Exports")
            service.ensure_allowed(file, out_dir)
            return service.run_one(file, out_dir, payload, configs_dir, profile)
        return guard(go)

    @api.post("/curate")
    def curate(payload: dict = Body(...)):
        def go():
            input_dir = payload.get("input_dir", "")
            out_dir = payload.get("out_dir") or os.path.join(input_dir, "autoedit-curated")
            service.ensure_allowed(input_dir, out_dir, payload.get("aesthetic_model"))
            return service.curate_folder(input_dir, out_dir, payload)
        return guard(go)

    # ── background batch jobs (live per-photo progress) ──
    @api.post("/jobs")
    def start_job(payload: dict = Body(...)):
        def go():
            service.ensure_allowed(payload.get("input_dir"), payload.get("out_dir"),
                                   *(payload.get("files") or []))
            return jobs.snapshot(jobs.start(
                payload.get("input_dir"), payload.get("files"), payload.get("out_dir"),
                payload, configs_dir, profile, photo_edits=payload.get("photo_edits")))
        return guard(go)

    @api.get("/jobs/{job_id}")
    def job_status(job_id: str):
        return guard(lambda: jobs.snapshot(jobs.get(job_id) or _unknown("unknown job")))

    @api.get("/jobs/{job_id}/events")
    def job_events(job_id: str):
        """Server-Sent Events: a snapshot whenever it changes, then `done` when the job ends."""
        j = jobs.get(job_id)
        if j is None:
            return _err(404, "not_found", "unknown job")

        def stream():
            last = None
            while True:
                snap = jobs.snapshot(j)
                if snap != last:
                    last = snap
                    yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
                if snap["status"] in ("done", "cancelled", "error", "failed"):
                    yield f"event: done\ndata: {json.dumps(snap)}\n\n"
                    return
                time.sleep(0.25)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @api.post("/jobs/{job_id}/control")
    def job_control(job_id: str, payload: dict = Body(...)):
        def go():
            j = jobs.get(job_id) or _unknown("unknown job")
            jobs.control(j, payload.get("action", ""))
            return jobs.snapshot(j)
        return guard(go)

    # ── culling jobs (M1): copy a curated subset for review/training ──
    @api.post("/curate/jobs")
    def start_curate(payload: dict = Body(...)):
        def go():
            input_dir = payload.get("input_dir") or ""
            out_dir = payload.get("out_dir") or curate_jobs.default_out_dir(input_dir)
            service.ensure_allowed(input_dir, out_dir)
            return curate_jobs.snapshot(curate_jobs.start(input_dir, out_dir, payload))
        return guard(go)

    @api.get("/curate/jobs/{job_id}")
    def curate_status(job_id: str):
        return guard(lambda: curate_jobs.snapshot(curate_jobs.get(job_id) or _unknown("unknown curate job")))

    # ── review labels (M2): persist human verdicts for training ──
    @api.post("/review/labels")
    def review_labels(payload: dict = Body(...)):
        def go():
            labels = payload.get("labels") or []
            out_dir = payload.get("out_dir") or (
                os.path.dirname(labels[0]["image"]) if labels and labels[0].get("image") else "")
            if not out_dir:
                raise ValueError("out_dir required (labels need an absolute image path or an explicit out_dir)")
            service.ensure_allowed(out_dir)
            return service.save_review_labels(out_dir, labels)
        return guard(go)

    @api.get("/review/labels")
    def review_label_state(out_dir: str = ""):
        if not out_dir:
            return guard(lambda: service.load_review_labels(""))
        return guard(lambda: (service.ensure_allowed(out_dir), service.load_review_labels(out_dir))[1])

    # ── dataset builder (M3): pair + preservation-validate + build a training workspace ──
    @api.post("/datasets/validate")
    def datasets_validate(payload: dict = Body(...)):
        def go():
            _require(payload, "originals_dir", "targets_dir")
            o, t = payload["originals_dir"], payload["targets_dir"]
            service.ensure_allowed(o, t)
            return service.dataset_validate(o, t, bool(payload.get("recursive")))
        return guard(go)

    @api.post("/datasets/build")
    def datasets_build(payload: dict = Body(...)):
        def go():
            _require(payload, "originals_dir", "targets_dir")
            o, t = payload["originals_dir"], payload["targets_dir"]
            out_dir = payload.get("out_dir") or os.path.join(o, "AutoEdit Dataset")
            service.ensure_allowed(o, t, out_dir)
            return service.dataset_build(o, t, out_dir, payload)
        return guard(go)

    # ── aesthetic critic (M4): train a finished-photo scorer + score/rank a folder ──
    @api.post("/aesthetic/train")
    def aesthetic_train(payload: dict = Body(...)):
        def go():
            _require(payload, "images_dir")
            out_model = payload.get("out_model") or os.path.join(payload["images_dir"], "aesthetic-model.json")
            payload["out_model"] = out_model
            service.ensure_allowed(payload["images_dir"], payload.get("labels_csv"), out_model)
            return aesthetic_jobs.snapshot(aesthetic_jobs.start(payload))
        return guard(go)

    @api.get("/aesthetic/jobs/{job_id}")
    def aesthetic_status(job_id: str):
        return guard(lambda: aesthetic_jobs.snapshot(aesthetic_jobs.get(job_id) or _unknown("unknown train job")))

    @api.post("/aesthetic/score")
    def aesthetic_score(payload: dict = Body(...)):
        def go():
            _require(payload, "folder", "model")
            service.ensure_allowed(payload["folder"], payload["model"])
            return service.aesthetic_score(payload)
        return guard(go)

    # ── variant engine (M7): make + score + rank several recipes for a photo ──
    @api.post("/variants")
    def variants(payload: dict = Body(...)):
        def go():
            _require(payload, "file")
            file = payload["file"]
            service.ensure_allowed(file, payload.get("aesthetic_model"))
            return service.make_variants(file, payload, configs_dir, profile=profile)
        return guard(go)

    app.include_router(api, prefix="/api/v1")
    app.include_router(api, prefix="/api", include_in_schema=False)   # pre-v1 paths for the Studio page
    return app


def serve(host: str = "127.0.0.1", port: int = 8765, configs_dir: str = "configs",
          open_browser: bool = True, unsafe_network: bool = False, profile: str | None = None) -> None:
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
    print(f"    configs: {os.path.abspath(configs_dir)}")
    if profile:
        print(f"    profile: {profile}")   # a stray ./configs silently overrides packaged tuning
    # Enforce the loopback Host-header check only for local binds; an intentional off-machine
    # bind (--unsafe-network) is reachable under a LAN name/IP we can't enumerate here.
    uvicorn.run(create_app(configs_dir, local_only=not unsafe_network, profile=profile), host=host, port=port)
