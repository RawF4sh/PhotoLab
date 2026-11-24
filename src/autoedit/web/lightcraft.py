"""Serve the official LightCraft web editor from a verified, local release."""
from __future__ import annotations

import hashlib
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

VERSION = "0.21.1"
BUNDLE = f"lightcraft-web-{VERSION}"
URL = f"https://github.com/storytold/lightcraft/releases/download/v{VERSION}/{BUNDLE}.zip"
DIGEST = "57147383a46eaab3b0cc036acf9443b221450c6a19f5dc303ae165bb9678b8cb"
BYTES = 74_569_049   # the size of that release file: what the page tells the user before it asks to download
_LIMIT = 90 * 1024 * 1024
_LOCK = threading.Lock()
_ENGINE_FILES = {"index.html", "lightcraft_web.js", "lightcraft_web_bg.wasm", "worker.js", "lightcraft_zh_hans.otf",
                 "snippets/eframe-d31a8450ea12dfc1/inline0.js"}
_HEADERS = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp",
            "Cross-Origin-Resource-Policy": "same-origin"}
# Only the upstream release page needs the inline boot script and WebAssembly.
_ENGINE_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval' 'sha256-4HgEuelxRDuqDvdKlQvpBManpPXKzOs1Wui7fv3wL/w='; "
               "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; "
               "worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")


def release_dir():
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "autoedit" / BUNDLE


def available():
    return (release_dir() / ".verified").is_file()


def install(archive=None):
    """Publish a complete verified release atomically. No network on subsequent opens."""
    with _LOCK:
        if available():
            return
        dest = release_dir()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
            root = Path(tmp)
            file = Path(archive) if archive else root / "release.zip"
            if archive is None:
                with urllib.request.urlopen(URL, timeout=90) as response, file.open("wb") as out:
                    while block := response.read(1024 * 1024):
                        if out.tell() + len(block) > _LIMIT:
                            raise ValueError("Editor download exceeds its expected size")
                        out.write(block)
            with file.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != DIGEST:
                    raise ValueError("The editor download did not match the published release checksum")
            unpacked = root / "editor"
            unpacked.mkdir()
            with zipfile.ZipFile(file) as bundle:
                for entry in bundle.infolist():
                    prefix = BUNDLE + "/"
                    name = entry.filename.removeprefix(prefix)
                    if not entry.filename.startswith(prefix) or entry.is_dir():
                        continue
                    notice = "/" not in name and name.startswith(("NOTICE", "LICENSE", "OFL-", "ATTRIBUTION"))
                    if name not in _ENGINE_FILES and not notice:
                        continue
                    target = unpacked / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, target.open("wb") as out:
                        shutil.copyfileobj(source, out)
            if any(not (unpacked / name).is_file() for name in _ENGINE_FILES):
                raise ValueError("The editor download is missing required files")
            (unpacked / ".verified").write_text(DIGEST)
            try:
                unpacked.rename(dest)
            except OSError:
                if not available():
                    raise


def native_app():
    if sys.platform == "darwin":
        return next((str(p) for p in (Path("/Applications/LightCraft.app"), Path.home() / "Applications/LightCraft.app") if p.is_dir()), None)
    return shutil.which("lightcraft")


def suggest_adjustments(payload):
    """Ask about a rendered manual edit using the same consent, thumbnail and spending limits as Guided."""
    from ..decide import gemini
    from . import ai_styles, assist, service

    try:
        if payload.get("allow_cloud") is not True:
            raise gemini.Unavailable("consent", "Allow a small preview to be sent before asking Gemini.")
        encoded = payload.get("thumbnail")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((gemini.MAX_THUMB_BYTES + 2) // 3):
            raise ValueError("Supply a JPEG preview of at most 512 pixels.")
        slider_bounds, lessons = service.editor_sliders()
        native = ("light.exposure", "light.contrast", "light.highlights", "light.shadows", "color.saturation")
        allowed = {name: slider_bounds[name.split(".")[1]] for name in native}
        limits = lessons.get("try", {})
        allowed.update(limits.get("lightcraft_deltas", {}))
        bands = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
        mixer = {f"mixer.{band}.{axis}": delta for band in bands
                 for axis, delta in limits.get("lightcraft_mixer_deltas", {}).items()}
        controls = payload.get("controls")
        if not isinstance(controls, dict):
            raise ValueError("Current slider values are required.")
        current = assist._numbers(controls, allowed)
        colors = assist._numbers(controls, mixer)
        if not current and not colors:
            raise ValueError("The photo has no supported adjustments.")
        bounds = {**{key: allowed[key] for key in current}, **{key: mixer[key] for key in colors}}
        context = {"current_controls": current}
        if colors:
            context["current_color_mixer"] = colors
        # Older clients can still supply a complete request. In the editor, the look is
        # sufficient on its own and the typed direction is optional.
        prompt = (ai_styles.prompt_for(payload["style"], payload.get("prompt", ""))
                  if "style" in payload else payload.get("prompt"))
        return assist._CLIENT.suggest(
            thumbnail=base64.b64decode(encoded, validate=True), prompt=prompt,
            context=context, names=[], bounds=bounds,
            api_key=payload.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", ""), allow_cloud=True)
    except gemini.Unavailable as exc:
        return {"status": "unavailable", "reason": exc.reason, "message": str(exc)}


def register(app, api, guard):
    from fastapi import Body
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.background import BackgroundTask
    from . import service

    static = Path(__file__).parent / "static" / "app"

    @app.get("/editor/fine-tune/", include_in_schema=False)
    @app.get("/editor/", include_in_schema=False)
    def editor():
        csp = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
               "frame-src 'self'; worker-src 'self'; "
               "connect-src 'self'; img-src 'self' blob: data:; frame-ancestors 'none'; base-uri 'none'")
        return FileResponse(static / "editor.html", headers={**_HEADERS, "Content-Security-Policy": csp})

    @app.get("/editor/release/{asset:path}", include_in_schema=False)
    def asset_file(asset: str):
        root = release_dir()
        if asset not in _ENGINE_FILES or not available():
            return JSONResponse({"error": "Editor file unavailable"}, status_code=404)
        return FileResponse(root / asset, headers={**_HEADERS, "Content-Security-Policy": _ENGINE_CSP},
                            media_type="application/wasm" if asset.endswith(".wasm") else None)

    @api.get("/editor/status")
    def status():
        return {"ready": available(), "version": VERSION, "native": bool(native_app()),
                "download_mb": round(BYTES / 1e6)}

    @api.post("/editor/install")
    def install_editor():
        return guard(lambda: (install(), status())[1])

    @api.post("/editor/assist")
    def editor_assist(payload: dict = Body(...)):
        return guard(lambda: suggest_adjustments(payload))

    @api.get("/editor/original")
    def original(path: str, as_tiff: bool = False):
        def read():
            service.ensure_allowed(path)
            service.check_image(path)
            if as_tiff:
                from .. import imageio
                if not imageio.is_raw(path):
                    raise ValueError("Choose a camera RAW file to open as TIFF.")
                try:
                    import tifffile  # noqa: F401 — a compatibility TIFF must retain 16-bit precision
                except ImportError:
                    raise ValueError("Install the AutoEdit accel extra to open RAW files as 16-bit TIFF.") from None
                temp = tempfile.TemporaryDirectory(prefix="autoedit-lightcraft-")
                output = os.path.join(temp.name, "photo.tif")
                try:
                    photo = imageio.load_image(path, {"raw_mode": "none", "raw_recover": False})
                    imageio.save_image(photo.rgb, output, fmt="tiff", icc_profile=imageio.srgb_profile(),
                                       exif=imageio.export_exif(path, "keep", (photo.width, photo.height)))
                    return FileResponse(output, media_type="image/tiff", headers={"Cache-Control": "no-store"},
                                        background=BackgroundTask(temp.cleanup))
                except Exception:
                    temp.cleanup()
                    raise
            return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
        return guard(read)

    @api.post("/editor/native")
    def open_native(payload: dict = Body(...)):
        def launch():
            executable = native_app()
            if not executable:
                raise FileNotFoundError("Install LightCraft desktop to use native-only tools")
            path = payload.get("path")
            args = ["/usr/bin/open", "-a", executable] if sys.platform == "darwin" else [executable]
            if path:
                service.ensure_allowed(path)
                service.check_image(path)
                args.append(os.path.abspath(path))
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": True}
        return guard(launch)
