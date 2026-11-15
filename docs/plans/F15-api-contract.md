# F15. API contract

Status: spec (2026-11-11). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F15.

## Goal

A future interface can build on the local web API without reading its source: the routes carry
a version, errors have one shape with a machine-readable code, a job reports progress as events,
and a test fails when the contract changes by accident. The current Studio page keeps working
unchanged.

## Where the API stands (checked 2026-11-11)

- 23 JSON routes under `/api/...`, all defined on the app inside `web/app.py::create_app`. No
  version prefix.
- Errors are `{"error": "<message>"}`. `guard()` turns `PermissionError` into 403 and every other
  exception into 400. Six routes are not wrapped (the three job status routes, job control,
  thumb, and GET review labels): an exception there becomes FastAPI's default 500 page.
- Progress is by polling `GET /api/jobs/{id}`.
- FastAPI's `/docs` and `/openapi.json` are on by default. Nothing tests the route list.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Do the unversioned routes stay? | Yes, as aliases of `/api/v1/...`, so the current Studio page works. They are left out of the OpenAPI document | Remove the second `include_router` call |
| Error body | `{"error": "<message>", "code": "<slug>"}`: the message stays where the Studio page reads it, the code is new | Nothing to reverse |
| What does the snapshot test compare? | A projection of the OpenAPI document (method, path, parameter names, whether there is a body), because the two supported FastAPI versions print different full documents | `tests/test_api_contract.py` |

## Design

### 1. Version prefix

All JSON routes move onto an `APIRouter` with paths relative to the prefix (`/styles`, `/jobs`,
...). The app includes it twice:

```python
    app.include_router(api, prefix="/api/v1")
    app.include_router(api, prefix="/api", include_in_schema=False)   # the pre-v1 paths, same handlers
```

A new route `GET /version` returns `{"api": "v1", "engine": "<package version>"}`.

### 2. One error shape

| Exception | Status | `code` |
|---|---|---|
| `PermissionError` | 403 | `forbidden` |
| `FileNotFoundError`, an unknown job id | 404 | `not_found` |
| `ValueError`, `KeyError`, other `OSError` (an unreadable or unsupported file) | 400 | `bad_request` |
| anything else | 500 | `internal` |
| the Host and Origin checks of the middleware | 403 | `forbidden` |
| FastAPI's own validation error (a malformed query or body) | 422 | `bad_request` |

- `guard()` stays the single place that maps exceptions; every route body runs inside it,
  including the six that are not wrapped today.
- An app-level exception handler for `Exception` returns the `internal` shape for anything that
  still escapes; one for `RequestValidationError` returns the `bad_request` shape with status 422.
- Today a missing file gives 400. It becomes 404. Nothing else changes status for a case that has
  a test today (`missing required field` stays 400; the Host and Origin checks stay 403).

### 3. Progress events

`GET /jobs/{job_id}/events` streams Server-Sent Events (`text/event-stream`):

- `event: snapshot`, `data: <the JSON of jobs.snapshot(job)>` once at the start and then whenever
  the snapshot differs from the last one sent (checked every 0.25 s);
- `event: done`, `data: <the final snapshot>` when the job's status is `done`, `cancelled` or
  `failed`, then the stream ends;
- an unknown id gives the 404 error body, not a stream.

A sync generator with `time.sleep` behind `StreamingResponse` is enough; FastAPI runs it in its
thread pool. Polling stays available.

### 4. The contract test

`tests/test_api_contract.py` builds the app, reads `app.openapi()`, and reduces it to

```json
{"GET /api/v1/styles": {"path": [], "query": [], "body": false}, ...}
```

(parameter names sorted). It compares that with `tests/snapshots/api_v1.json`. With the
environment variable `AUTOEDIT_UPDATE_SNAPSHOTS=1` the test rewrites the file instead of failing,
and says so in its output. A second assertion: every `/api/v1` path also answers under `/api`
(same handler), and no `/api/` path without `v1` is in the document.

## Files

`src/autoedit/web/app.py`, `tests/test_api_contract.py` (new), `tests/snapshots/api_v1.json` (new).
`web/jobs.py` and `web/service.py` do not change.

## Task

One Builder (Sonnet, medium). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_api_contract.py tests/test_security.py tests/test_web_service.py tests/test_jobs.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`. The suite also runs on a second Python with
another FastAPI version: the snapshot projection must not depend on the FastAPI version (no
schema names, no response codes, no descriptions).

## Tests: `tests/test_api_contract.py`

Use `fastapi.testclient.TestClient` as `tests/test_security.py` does (headers for a local Host).

| # | Test | Asserts |
|---|---|---|
| 1 | `test_contract_snapshot` | The projection equals `tests/snapshots/api_v1.json`; with `AUTOEDIT_UPDATE_SNAPSHOTS=1` it writes the file |
| 2 | `test_versioned_and_legacy_paths_share_handlers` | `GET /api/v1/styles` and `GET /api/styles` return the same JSON; `GET /api/v1/version` returns `api == "v1"` and the package version; the OpenAPI document has no `/api/` path without `v1` |
| 3 | `test_error_shape_and_codes` | `POST /api/v1/run` with `{}` gives 400, `code == "bad_request"`, and `error` contains `input_dir` (the old assertion); `GET /api/v1/jobs/nope` gives 404 with `code == "not_found"`; a request with `Host: evil.com` gives 403 with `code == "forbidden"`; `GET /api/v1/thumb?path=<a missing file under an approved root>` gives 404 `not_found`; every error body has exactly the keys `error` (a string) and `code` |
| 4 | `test_unexpected_exception_is_internal` | With `service.list_styles` replaced by a function that raises `RuntimeError("boom")` (restored in `finally`), `GET /api/v1/styles` gives 500, `code == "internal"`, and the message contains `boom`. Build the client with `raise_server_exceptions=False` |
| 5 | `test_job_events_stream` | Start a job on two small synthetic photos (stub backend) through `POST /api/v1/jobs`, read `GET /api/v1/jobs/{id}/events` as a stream until it ends: there is at least one `snapshot` event, the last event is `done`, its data has `status == "done"` and `processed == 2`; `GET /api/v1/jobs/nope/events` gives 404 `not_found` |

## Result

Built on 2026-11-11, in the unattended run. Status: done.

- **Built** by one Builder (Sonnet, medium) from this spec: every JSON route sits on one router
  that is mounted at `/api/v1` (documented) and at `/api` (the old paths, same handlers, not in
  the document); `GET /api/v1/version`; `GET /api/v1/jobs/{id}/events` as a server-sent event
  stream; one error shape `{"error", "code"}` for every failure; the route snapshot
  `tests/snapshots/api_v1.json` (25 routes) with its test.
- **Deviations accepted:** a job in the state `error` also ends the event stream (the job runner
  uses that state for a crashed job); a thumbnail of a missing file answers 404 through a check
  that runs after the path trust check; the two HTML pages stay on the app and appear in the
  snapshot.
- **Checked by the session:** the Studio pages read `.error` from every failed response and never
  `.detail`, so the uniform error body is what they expect, including the validation errors that
  used to come in FastAPI's own shape.
- **To change the API on purpose:** run the contract test with `AUTOEDIT_UPDATE_SNAPSHOTS=1` and
  commit the new snapshot.
- **Both interpreters** run the contract tests (FastAPI 0.137 and 0.162); the snapshot is the
  same on both.
- **Final check** (Checker, Opus, high): the Host and Origin checks and the path trust boundary
  are unchanged against the last commit, and the old `/api` paths share the handlers. Known
  limits: the event stream of a paused job stays open and uses a server thread per poll; a 500
  response carries the exception text, which can contain a local path (sent to the local client
  only).
