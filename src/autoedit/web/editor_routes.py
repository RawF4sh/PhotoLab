"""Local editor import, working pictures and durable manual edits."""
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import tempfile

from . import editor_files, manual_stage, service


@asynccontextmanager
async def _upload(request, suffix, byte_limit):
    count = 0
    checksum = hashlib.sha256()
    with tempfile.TemporaryDirectory(dir=editor_files.root()) as directory:
        path = Path(directory) / ("upload" + suffix)
        with path.open("wb") as output:
            async for block in request.stream():
                count += len(block)
                if count > byte_limit:
                    raise ValueError("Photo exceeds the editor upload limit.")
                checksum.update(block)
                output.write(block)
        if not count:
            raise ValueError("No photo bytes received.")
        yield path, checksum.hexdigest()


def register(api, guard, configs_dir, profile):
    from fastapi import Body, Request
    from fastapi.responses import StreamingResponse
    from starlette.concurrency import run_in_threadpool

    def photo_request(payload, operation):
        opts = payload.get("options", {})
        full = payload.get("full_size", False)
        if not isinstance(opts, dict) or type(full) is not bool or not isinstance(payload.get("path"), str):
            raise ValueError("Supply a photo path, options object and optional full_size boolean.")
        return operation(payload["path"], opts, configs_dir, profile, full)

    @api.post("/editor/import")
    def import_photo(payload: dict = Body(...)):
        def open_source():
            if "expected_drop" in payload:
                path = payload.get("path")
                if not isinstance(path, str):
                    raise ValueError("Choose the matching original photo.")
                service.ensure_allowed(path)
                service.check_image(path)
                with open(path, "rb") as src:
                    if hashlib.file_digest(src, "sha256").hexdigest() != payload["expected_drop"]:
                        raise ValueError("This is a different photo. Select the original file that was dropped.")
            return photo_request(payload, editor_files.import_photo)
        return guard(open_source)

    @api.post("/editor/working")
    def working(payload: dict = Body(...)):
        return guard(lambda: photo_request(payload, editor_files.working_picture))

    @api.post("/editor/source-preview")
    def original_preview(payload: dict = Body(...)):
        def preview():
            path, opts = payload.get("path"), payload.get("options", {})
            if not isinstance(path, str) or not isinstance(opts, dict):
                raise ValueError("Supply a photo path and an options object.")
            return editor_files.source_preview(path, opts, configs_dir, profile)
        return guard(preview)

    @api.get("/editor/pictures/{key}")
    def picture(key: str):
        def read():
            stream, size = editor_files.open_picture(key)
            def chunks():
                with stream:
                    while block := stream.read(256 * 1024):
                        yield block
            return StreamingResponse(chunks(), media_type="image/tiff",
                                     headers={"Content-Length": str(size), "Cache-Control": "no-store"})
        return guard(read)

    @api.post("/editor/drop")
    async def drop(request: Request, name: str):
        try:
            async with _upload(request, ".photo", editor_files.UPLOAD_BYTES) as (temp, sha):
                path = editor_files.store_drop(temp, name, sha)
                answer = await run_in_threadpool(editor_files.import_photo, path, {}, configs_dir, profile)
                return {**answer, "dropped": True, "sha256": sha}
        except (ValueError, OSError, RuntimeError) as exc:
            return guard(lambda: _raise(exc))

    @api.get("/editor/manual")
    def manual(path: str):
        return guard(lambda: manual_stage.load(path, profile))

    @api.post("/editor/manual")
    def update_manual(payload: dict = Body(...)):
        def write():
            if "expected" not in payload or "settings" not in payload:
                raise ValueError("Supply settings and the previous checksum (null for a new edit).")
            return manual_stage.save(payload["path"], payload["settings"], payload.get("base", ""),
                                     payload["expected"], profile, baseline=payload.get("baseline"))
        return guard(write)

    @api.post("/editor/export")
    async def export(request: Request, path: str, manual: str, ticket: str, out_dir: str | None = None):
        try:
            dest = out_dir or editor_files.default_export_path(path)
            service.ensure_allowed(path, dest)
            async with _upload(request, ".tif", manual_stage.upload_limit(path, ticket)) as (temp, _):
                return await run_in_threadpool(guard, lambda: manual_stage.export(
                    path, ticket, manual, temp, dest, configs_dir, profile))
        except (ValueError, OSError) as exc:
            return guard(lambda: _raise(exc))


def _raise(error):
    raise error
