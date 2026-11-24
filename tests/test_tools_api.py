"""The /api/v1/tools routes (U6): job pattern, error codes and a few real tools. Stub only, no network."""

import json
import os
import tempfile
import time


def _client(profile=None):
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return None
    from autoedit import paths
    from autoedit.web import app as webapp

    return TestClient(webapp.create_app(paths.configs_dir(), local_only=True, profile=profile), base_url="http://localhost")


def _wait(c, job):
    for _ in range(100):
        r = c.get(f"/api/v1/tools/jobs/{job['id']}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.05)
    raise AssertionError("tool job did not finish")


def _run(c, name, body):
    r = c.post(f"/api/v1/tools/{name}", json=body)
    assert r.status_code == 200, r.text
    return _wait(c, r.json())


def _approved(tmp):
    from autoedit.web import service

    service.approve_root(tmp)


def test_unknown_tool_404_bad_body_400_outside_403():
    c = _client()
    if c is None:
        return
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    assert c.post("/api/v1/tools/nope", json={}).status_code == 404
    assert c.post("/api/v1/tools/find", json={"folder": "x"}).status_code == 400
    assert c.get("/api/v1/tools/jobs/missing").status_code == 404
    with tempfile.TemporaryDirectory() as tmp:
        r = c.post("/api/v1/tools/find", json={"folder": tmp, "words": ["sky"]})
        assert r.status_code == 403 and r.json()["code"] == "forbidden"


def test_exports_find_history_and_job_states():
    c = _client()
    if c is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        _approved(tmp)
        r = _run(c, "exports", {})
        assert r["status"] == "done" and "web" in [p["name"] for p in r["result"]["presets"]]
        with open(os.path.join(tmp, "a.png"), "wb") as fh:
            fh.write(b"x")
        side = {"audit": {"output": "a.png"}, "scene": "landscape",
                "concepts": [{"name": "sky", "present": True, "coverage": 0.4}]}
        with open(os.path.join(tmp, "a.autoedit.json"), "w") as fh:
            json.dump(side, fh)
        r = _run(c, "find", {"folder": tmp, "words": ["sky"]})
        assert r["status"] == "done" and [h["image"] for h in r["result"]["hits"]] == [os.path.join(tmp, "a.png")]
        assert _run(c, "find", {"folder": tmp, "words": ["boat"]})["result"]["hits"] == []
        h = _run(_client(profile=tmp), "history_status", {})
        assert isinstance(h["result"], dict), h["error"]
        assert _run(c, "find", {"folder": os.path.join(tmp, "nope"), "words": ["a"]})["status"] == "error"


def test_job_runs_then_finishes():
    from autoedit.web import tools_jobs

    c = _client()
    if c is None:
        return
    gate = __import__("threading").Event()

    def slow(p, configs_dir, profile, log):
        log("working")
        gate.wait(5)
        return {"ok": True}

    tools_jobs.TOOLS["_slow"] = (slow, False)
    try:
        r = c.post("/api/v1/tools/_slow", json={}).json()
        assert r["status"] == "running"
        assert c.get(f"/api/v1/tools/jobs/{r['id']}").json()["status"] == "running"
        gate.set()
        done = _wait(c, r)
        assert done["status"] == "done" and done["result"] == {"ok": True} and done["log"] == ["working"]
    finally:
        del tools_jobs.TOOLS["_slow"]


def test_doctor_returns_text():
    c = _client()
    if c is None:
        return
    r = _run(c, "doctor", {})
    assert r["status"] == "done" and "AutoEdit doctor" in r["result"]["text"]


def test_ui_batch_notes_its_input_dir(tmp_path=None):
    """A UI batch writes a minimal run.json, so adjust, sync and declare find its sources; never replaces one."""
    import json
    import tempfile

    from autoedit.web import jobs
    d = str(tmp_path) if tmp_path else tempfile.mkdtemp()
    out = os.path.join(d, "AutoEdit Exports")
    os.makedirs(out)
    jobs._note_input_dir(out, [os.path.join(d, "a.jpg"), os.path.join(d, "b.jpg")])
    with open(os.path.join(out, "run.json")) as fh:
        assert json.load(fh)["input_dir"] == os.path.abspath(d)
    with open(os.path.join(out, "run.json"), "w") as fh:
        fh.write('{"input_dir": "kept"}')
    jobs._note_input_dir(out, [os.path.join(d, "a.jpg")])
    with open(os.path.join(out, "run.json")) as fh:
        assert json.load(fh)["input_dir"] == "kept"


def test_every_path_key_of_every_tool_is_403_outside_approved_folders():
    from autoedit.web import tools_jobs as tj

    c = _client()
    if c is None:
        return
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    outside = os.path.join(tempfile.gettempdir(), "autoedit-not-approved")
    for name in tj.TOOLS:
        # every required key gets a type-correct value; then one path key at a time is the outside path
        body = {k: ["sky"] if k == "words" else "x" for k in tj.REQUIRED.get(name, ())}
        for key in tj.PATH_KEYS:
            b = dict(body)
            b[key] = [outside] if key == "photos" and name == "looks_card" else outside
            r = c.post(f"/api/v1/tools/{name}", json=b)
            assert r.status_code == 403, (name, key, r.status_code, r.text)


def test_wrong_types_and_non_object_bodies_are_400():
    c = _client()
    if c is None:
        return
    for body in ({"folder": 3, "words": ["a"]}, {"folder": "x", "words": "sky"}, {"folder": "x", "words": [1]}):
        r = c.post("/api/v1/tools/find", json=body)
        assert r.status_code == 400 and r.json()["code"] == "bad_request", (body, r.text)
    assert c.post("/api/v1/tools/calibrate_start", json={"photos": "p", "out": "o", "count": "3"}).status_code == 400
    assert c.post("/api/v1/tools/exports", json={"dry_run": "yes"}).status_code == 400
    for body in ([1], "text", 3):
        r = c.post("/api/v1/tools/exports", json=body)
        assert r.status_code == 400 and r.json()["code"] == "bad_request", (body, r.text)


def test_a_slow_job_that_raises_releases_the_lock_and_systemexit_is_an_error():
    from autoedit.web import tools_jobs

    c = _client()
    if c is None:
        return

    def bad(p, configs_dir, profile, log):
        raise SystemExit("cli said no")

    def ok(p, configs_dir, profile, log):
        return {"ok": True}

    tools_jobs.TOOLS["_bad"], tools_jobs.TOOLS["_ok"] = (bad, True), (ok, True)
    try:
        r = _wait(c, c.post("/api/v1/tools/_bad", json={}).json())
        assert r["status"] == "error" and "cli said no" in r["error"]
        assert _wait(c, c.post("/api/v1/tools/_ok", json={}).json())["status"] == "done"
    finally:
        del tools_jobs.TOOLS["_bad"], tools_jobs.TOOLS["_ok"]


def test_doctor_and_models_remove_share_the_slow_lock():
    from autoedit.web import tools_jobs

    assert tools_jobs.TOOLS["doctor"][1] and tools_jobs.TOOLS["models_remove"][1]


def test_reveal_runs_the_helper_without_a_shell():
    from unittest import mock

    from autoedit.web import tools_jobs

    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "a b.png")
        open(f, "wb").close()
        for plat, want in (("darwin", ["open", "-R", f]),
                           ("linux", ["xdg-open", tmp]),
                           ("win32", f'explorer /select,"{f}"')):
            with mock.patch.object(tools_jobs.subprocess, "Popen") as popen, mock.patch.object(tools_jobs.sys, "platform", plat):
                tools_jobs.reveal({"path": f}, "", None, print)
            assert popen.call_args.args == (want,) and not popen.call_args.kwargs.get("shell"), plat


def test_rank_variants_lists_a_failing_image_instead_of_failing_the_job():
    from unittest import mock

    from autoedit.web import tools_jobs

    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "a.jpg"), os.path.join(tmp, "b.jpg")
        for f in (a, b):
            open(f, "wb").close()

        def fake(path, *x, **k):
            if path == b:
                raise ValueError("bad pixels")
            return {"best": "v"}

        with mock.patch.object(tools_jobs.service, "make_variants", fake):
            r = tools_jobs.rank_variants({"input": tmp}, "", None, print)
        assert list(r["photos"]) == ["a.jpg"] and r["errors"] == {"b.jpg": "bad pixels"}


def test_ridge_zero_is_kept():
    from unittest import mock

    from autoedit.web import tools_jobs

    with mock.patch("autoedit.recipe.train_from_dataset", return_value={}) as t:
        tools_jobs.train_recipe({"dataset": "d", "out_model": "m", "ridge": 0}, "", None, print)
    assert t.call_args.kwargs["ridge"] == 0.0


def test_the_guard_covers_the_tools_routes_and_app_files():
    c = _client()
    if c is None:
        return
    for method, url in (("post", "/api/v1/tools/exports"), ("get", "/api/v1/tools/jobs/x"), ("get", "/app/main.js")):
        r = getattr(c, method)(url, headers={"Host": "evil.com"})
        assert r.status_code == 403, (url, r.status_code)
    r = c.post("/api/v1/tools/exports", json={}, headers={"Origin": "http://evil.com"})
    assert r.status_code == 403


def test_recipe_plan_routes_images_and_reports_missing_objects_and_corrupt_file(tmp_path):
    from PIL import Image
    from autoedit.web import edit_presets
    c = _client(str(tmp_path))
    if c is None:
        return
    folder = str(tmp_path / 'photos')
    os.makedirs(folder)
    _approved(folder)
    for name, value in (('dark', 40), ('light', 220), ('other', 130)):
        Image.new('RGB', (32, 32), (value,) * 3).save(os.path.join(folder, name + '.png'))
    open(os.path.join(folder, 'broken.png'), 'wb').close()
    dark = edit_presets.save('Dark scenes', {
        'region_settings': {'red boat': {'temp': -3}}, 'condition': {
            'tests': [{'measure': 'mean_lum', 'op': 'below', 'value': .4}]}}, profile=str(tmp_path))
    light = edit_presets.save('Light scenes', {'condition': {
        'tests': [{'measure': 'mean_lum', 'op': 'above', 'value': .7}]}}, profile=str(tmp_path))
    ids = [record['id'] for record in (light, dark)]
    body = {'folder': folder, 'recipes': ids, 'options': {'backend': 'stub'}}
    job = _run(c, 'plan-recipes', body)
    assert job['status'] == 'done', job
    result = job['result']
    assert result['counts'] == {key: 1 for key in ids}, result
    assert result['failed'] == result['unmatched'] == result['missing_objects'] == 1
    items = {os.path.basename(row['path']): row for row in result['items']}
    assert 'error' in items['broken.png']
    assert items['dark.png']['missing'] == ['red boat']
    assert items['other.png']['recipe_id'] is None
    assert items['light.png']['recipe_id'] == light['id'] and items['light.png']['values']['iso'] is None
    all_photos = edit_presets.save('All', {}, profile=str(tmp_path))
    repeat = _run(c, 'plan-recipes', {**body, 'recipes': [all_photos['id'], *ids],
                                      'photos': [os.path.join(folder, 'light.png')]})
    assert repeat['result']['items'][0]['recipe_id'] == all_photos['id']
    payload = c.get(f"/api/v1/edit-presets/{dark['id']}/export").json()
    assert set(payload) == {'name', 'settings'}
    imported = c.post('/api/v1/edit-presets/import', json={**payload, 'name': 'Imported'}).json()['preset']
    assert imported['settings'] == dark['settings']
    assert c.post('/api/v1/edit-presets/import', json={**payload, 'path': 'private'}).status_code == 400
