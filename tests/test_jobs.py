"""Background batch jobs — threaded per-photo runner with live status (stub backend)."""

import os
import time

import numpy as np
from PIL import Image as PILImage

from autoedit.web import jobs


def _img(p):
    a = np.zeros((48, 48, 3), np.uint8)
    a[:24] = [100, 140, 200]   # sky-ish top → stub detects sky → landscape scene
    a[24:] = [90, 110, 80]
    PILImage.fromarray(a, "RGB").save(str(p))


def _wait(job, timeout=20):
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job


def test_job_runs_to_done_and_writes_outputs(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    for n in ("a.jpg", "b.jpg"):
        _img(d / n)
    out = tmp_path / "out"
    job = jobs.start(str(d), None, str(out), {"backend": "stub", "style": "natural"})
    _wait(job)
    snap = jobs.snapshot(job)
    assert snap["status"] == "done"
    assert snap["total"] == 2 and snap["processed"] == 2 and snap["failed"] == 0
    assert snap["done"] + snap["review"] == 2
    for it in snap["items"]:                         # outputs written + recorded
        assert it["out"] and os.path.exists(it["out"])
        assert PILImage.open(it["out"]).info.get("icc_profile")      # F1: sRGB profile embedded
    assert os.path.isdir(str(out))


def test_job_respects_output_format(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    out = tmp_path / "out"
    job = jobs.start(str(d), None, str(out), {"backend": "stub", "out_format": "tiff"})
    _wait(job)
    snap = jobs.snapshot(job)

    assert snap["status"] == "done"
    assert snap["items"][0]["out"].endswith(".tif")
    assert os.path.exists(snap["items"][0]["out"])


def test_job_isolates_a_bad_image(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "good.jpg")
    (d / "bad.jpg").write_bytes(b"not an image")    # supported extension, undecodable
    job = jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"})
    _wait(job)
    snap = jobs.snapshot(job)
    assert snap["status"] == "done" and snap["total"] == 2
    assert snap["failed"] == 1                       # bad.jpg fails, good.jpg succeeds
    assert any(i["status"] == "failed" and i["error"] for i in snap["items"])


def test_start_on_empty_dir_raises(tmp_path):
    import pytest

    d = tmp_path / "empty"; d.mkdir()
    with pytest.raises(ValueError):                  # was a silent 0-item "done"
        jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"})


def test_explicit_empty_selection_does_not_process_the_folder(tmp_path):
    import pytest

    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        jobs.start(str(d), [], str(out), {"backend": "stub"})
    assert not out.exists()


def test_control_is_noop_on_finished_job(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    job = jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"})
    _wait(job)
    assert job.status == "done"
    jobs.control(job, "pause")                       # must not resurrect a finished batch
    assert job.status == "done"
    jobs.control(job, "resume")
    assert job.status == "done"


def test_registry_prunes_terminal_jobs(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    _img(d / "a.jpg")
    saved = dict(jobs._JOBS)
    try:
        jobs._JOBS.clear()
        for _ in range(jobs._JOB_CAP + 5):
            _wait(jobs.start(str(d), None, str(tmp_path / "o"), {"backend": "stub"}))
        assert len(jobs._JOBS) <= jobs._JOB_CAP      # oldest terminal jobs evicted
    finally:
        jobs._JOBS.clear()
        jobs._JOBS.update(saved)


def test_saved_auto_scene_reaches_batch_and_legacy_simple_is_preserved(tmp_path):
    import json
    from pathlib import Path
    from autoedit.web import service

    service.approve_root(str(tmp_path))
    files = []
    for index in range(3):
        path = tmp_path / f"frame{index}.jpg"
        _img(path)
        files.append(str(path))
    job = jobs.start(str(tmp_path), files, str(tmp_path / "result"), {"backend": "stub", "style": "standard"},
                     photo_options={files[0]: {"style": "auto", "scene": "mist"},
                                    files[1]: {"style": "auto", "scene": "mist"}})
    _wait(job)
    assert all(i.status in ("finished", "review") for i in job.items), jobs.snapshot(job)
    output = [json.loads(Path(i.out).with_suffix(".autoedit.json").read_text()) for i in job.items]
    assert all(r["scene"] == "mist" and r["audit"]["auto"]["fallback"] for r in output[:2])
    assert "auto" not in output[2]["audit"]
    assert service.make_cfg({"style": "auto", "auto_unreleased": True}).auto_unreleased is False


def test_grouped_auto_receipt_replays_on_preview_single_export_and_mixed_jobs(tmp_path):
    import json
    import shutil
    from pathlib import Path
    from autoedit.paths import config_path
    from autoedit.web import service

    cfgdir = tmp_path / "configs"
    shutil.copytree(Path(config_path("auto.toml")).parent, cfgdir)
    auto = cfgdir / "auto.toml"
    auto.write_text(auto.read_text().replace("[scene.daylight]\nreleased = false", "[scene.daylight]\nreleased = true"))
    files = []
    for i in range(3):
        src = tmp_path / f"{i}.png"
        lum = np.linspace(.11,.42,120,dtype=np.float32)[None,:,None]
        pixels = np.zeros((80,120,3),dtype=np.float32) + lum + i*.015
        PILImage.fromarray((pixels * 255).astype(np.uint8)).save(src)
        files.append(str(src))
    service.approve_root(str(tmp_path))
    opts = {"style":"standard", "backend":"stub", "out_format":"png"}
    chosen = {"style":"auto", "scene":"daylight"}
    result = _wait(jobs.start(str(tmp_path), files, str(tmp_path/"out"), opts, str(cfgdir),
                              photo_options={p:chosen for p in files[:2]}))
    rows = jobs.snapshot(result)["items"]
    assert all(row["out"] for row in rows) and "auto_shoot" not in rows[-1]
    assert rows[0]["auto_shoot"]["median"] == rows[1]["auto_shoot"]["median"]
    saved = {**opts, **chosen, "photo_base":{"auto_shoot":rows[0]["auto_shoot"]}}
    preview = service.preview(files[0], saved, str(cfgdir))["result"]
    original = json.loads(Path(rows[0]["out"]).with_suffix(".autoedit.json").read_text())
    assert preview["audit"]["auto"]["shoot"] == original["audit"]["auto"]["shoot"]
    assert preview["global_settings"] == original["global_settings"]
    native = service._edit(files[0], saved, str(cfgdir), None, native=True)
    assert native["result"].global_settings == original["global_settings"]
    variant = service.make_variants(files[0], {**saved, "variants":[{"name":"Current"}]}, str(cfgdir),
                                    with_images=True)["variants"][0]
    assert variant["after"] == service._img_b64(native["out"], 1100, fmt="jpeg", quality=88)
    single = service.run_one(files[0], str(tmp_path/"one"), saved, str(cfgdir))
    assert Path(single["image_out"]).read_bytes() == Path(rows[0]["out"]).read_bytes()
    fallback = service.preview(files[0], {**saved,"scene":"mist"}, str(cfgdir))["result"]["audit"]["auto"]
    assert not fallback.get("shoot") and fallback["target"] is None


def test_group_receipts_validate_and_match_only_the_effective_settings():
    import pytest
    from autoedit.contract import RunConfig
    from autoedit.web import photo_base
    receipt = {"scene":"mist","median":{"global":{"temp":2}}, "scene_override":None,
               "intensity":1.0,"exposure_bias":.2,"batch_group":"g2"}
    base = photo_base.check({"auto_shoot":receipt})
    assert photo_base.render_args(base, RunConfig(style="auto"))["exposure_bias"] == .2
    for cfg in (RunConfig(style="standard"), RunConfig(style="auto",scene="daylight"), RunConfig(style="auto",intensity=.3)):
        args = photo_base.render_args(base,cfg)
        assert "auto_shoot" not in args and args["exposure_bias"] == 0
    for invalid in ({**receipt,"median":{"sky":{"exposure":float("nan")}}}, {**receipt,"scene":[]},
                    {**receipt,"median":{"sky":{"unknown":0}}}):
        with pytest.raises(ValueError):
            photo_base.check({"auto_shoot":invalid})
