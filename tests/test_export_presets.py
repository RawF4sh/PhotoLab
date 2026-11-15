"""F10: resize, WebP/PNG, name templates, subfolders and export presets."""

import json
import os
from types import SimpleNamespace

import numpy as np
from PIL import Image as PILImage

from autoedit import adjust, batch, emit, exports, paths
from autoedit.cli import load_run_config
from autoedit.contract import RunConfig
from autoedit.imageio import save_image, srgb_profile
from autoedit.web import service

from test_export_metadata import _exif_of, _fixture

_PACKAGED = os.path.join(os.path.dirname(paths.__file__), "configs", "exports.toml")


def _photos(d, n=2, size=(400, 300)):
    d.mkdir()
    for i in range(n):
        a = np.random.default_rng(i).integers(60, 200, (size[1], size[0], 3), dtype=np.uint8)
        PILImage.fromarray(a, "RGB").save(str(d / f"p{i}.jpg"), quality=95)
    return str(d)


def _cli(**kw):
    return load_run_config(SimpleNamespace(configs_dir=None, config=None, **kw))


def test_default_paths_and_bytes_are_unchanged(tmp_path):
    out = str(tmp_path / "out")
    img, side = emit.output_paths(out, "a/IMG_1.CR2", "jpeg")
    assert img == os.path.join(out, "IMG_1.jpg")
    assert side == os.path.join(out, "IMG_1.autoedit.json")
    rgb = np.random.default_rng(1).random((40, 60, 3)).astype(np.float32)
    a, b = str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")
    emit.write_image(rgb, a, "jpeg", 90)
    save_image(rgb, b, fmt="jpeg", quality=90)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_long_edge(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    rgb = np.full((200, 300, 3), 0.5, np.float32)
    p = str(tmp_path / "o.jpg")
    emit.write_image(rgb, p, "jpeg", 95, source=src, metadata="keep", long_edge=120)
    pil = PILImage.open(p)
    assert pil.size == (120, 80)
    assert _exif_of(p)[1][0xA002] == 120
    assert abs(float(np.asarray(pil).mean()) - 127.5) <= 1
    q = str(tmp_path / "q.jpg")
    emit.write_image(rgb, q, "jpeg", 95, source=src, metadata="keep", long_edge=1000)
    assert PILImage.open(q).size == (300, 200)


def test_webp_and_png(tmp_path):
    src = _fixture(tmp_path / "s.jpg")
    rgb = np.full((20, 30, 3), 0.5, np.float32)
    for fmt in ("webp", "png"):
        img, _ = emit.output_paths(str(tmp_path), "x.jpg", fmt)
        assert img.endswith("." + fmt)
        emit.write_image(rgb, img, fmt, 80, source=src, metadata="keep")
        pil = PILImage.open(img)
        assert pil.format == fmt.upper() and pil.size == (30, 20)
        assert pil.info.get("icc_profile") == srgb_profile()
        assert _exif_of(img)[1][0x9003] == "2020:01:01 10:00:00"


def test_name_template(tmp_path):
    out = str(tmp_path)
    f = {"style": "natural", "n": 7}
    assert emit.output_paths(out, "a/IMG.CR2", "jpeg", name="{stem}_web", fields=f)[0].endswith("IMG_web.jpg")
    assert emit.output_paths(out, "a/IMG.CR2", "jpeg", name="{style}-{stem}", fields=f)[0].endswith("natural-IMG.jpg")
    assert emit.output_paths(out, "a/IMG.CR2", "jpeg", name="{n:04d}_{stem}", fields=f)[0].endswith("0007_IMG.jpg")
    taken = set()
    one = emit.output_paths(out, "x/IMG.CR2", "jpeg", taken, name="{stem}_web", fields=f)
    two = emit.output_paths(out, "y/IMG.jpg", "jpeg", taken, name="{stem}_web", fields=f)
    assert two[0].endswith("IMG_web-2.jpg") and two[1].endswith("IMG_web-2.autoedit.json")
    assert one[1].endswith("IMG_web.autoedit.json")


def test_subdir(tmp_path):
    src = _photos(tmp_path / "in")
    out = str(tmp_path / "out")
    cfg = RunConfig(backend="stub", out_subdir="web")
    m = batch.run(cfg, src, out)
    assert m["processed"] == 2
    assert sorted(os.listdir(os.path.join(out, "web"))) == [
        "p0.autoedit.json", "p0.jpg", "p1.autoedit.json", "p1.jpg"]
    assert os.path.isfile(os.path.join(out, "run.json")) and not os.path.exists(os.path.join(out, "p0.jpg"))
    assert batch.run(cfg, src, out, resume=True)["skipped"] == 2
    target = os.path.join(out, "web", "p0.jpg")
    os.remove(target)
    r = adjust.rerender(target)
    assert r["image"] == target and os.path.isfile(target)


def test_validation():
    import pytest
    for bad in ("", "a/b", "../x", "{nope}", "a\\b"):
        with pytest.raises(ValueError):
            RunConfig(out_name=bad)
    for bad in ("../x", "/abs", "a/../b", "C:x", "x" * 101, "out/{style}"):
        with pytest.raises(ValueError):
            RunConfig(out_subdir=bad)
    RunConfig(out_name="{n:04d}_{stem}", out_subdir="a/b")


def test_the_name_template_takes_plain_fields_only():
    import pytest
    for bad in ("{n[0]}", "{stem.x}", "{stem!r}", "{stem:{style}}", "{stem:>8}", "{style:x}",
                "{n:{stem}}", "{n!s}", "{0}", "{}", "{stem", "{n:}}", "{stem.__class__}"):
        with pytest.raises(ValueError):
            RunConfig(out_name=bad)
    for good in ("{n:04d}_{stem}", "{style}-{stem}", "{stem}_{{web}}", "x{n}"):
        RunConfig(out_name=good)
    assert emit.output_paths("o", "a/IMG.jpg", "jpeg", name="{n:04d}_{stem}",
                             fields={"style": "s", "n": 3})[0].endswith("0003_IMG.jpg")


def test_a_style_cannot_steer_the_file_name_out_of_the_folder(tmp_path):
    import pytest
    src = _photos(tmp_path / "in", n=1)
    out = tmp_path / "deep" / "out"
    cfg = service.make_cfg({"style": "../../escaped", "out_name": "{style}-{stem}", "backend": "stub"})
    m = batch.run(cfg, src, str(out))          # the photo fails, the run goes on and says so
    assert m["failed"] == 1 and "escaped" in m["failures"][0]["error"]
    inside = (tmp_path / "in", out)
    stray = [p for p in tmp_path.rglob("*") if p.is_file() and not any(i in p.parents for i in inside)]
    assert stray == []
    # the same through the filled-name check
    for bad in ("..", ".", "", "a/b", "a\\b", ".hidden", "a\0b"):
        with pytest.raises(ValueError):
            emit.output_paths(str(out), "x.jpg", "jpeg", name="{style}", fields={"style": bad, "n": 1})
    assert not (tmp_path / "deep" / "x.jpg").exists()
    # a hidden source keeps the name it always had
    assert emit.output_paths(str(out), "d/.hid.jpg", "jpeg")[0].endswith(".hid.jpg")


def test_an_export_never_replaces_its_source(tmp_path):
    import pytest
    src = _photos(tmp_path / "photos", n=2)
    before = {n: open(os.path.join(src, n), "rb").read() for n in os.listdir(src)}
    cfg = RunConfig(backend="stub", out_subdir="photos")
    m = batch.run(cfg, src, str(tmp_path))                  # tmp/photos/p0.jpg is the source
    assert m["processed"] == 0 and m["failed"] == 2
    assert all("overwrite a source photo" in f["error"] for f in m["failures"])
    m = batch.run(RunConfig(backend="stub"), src, src)      # same folder, same format
    assert m["processed"] == 0 and m["failed"] == 2
    with pytest.raises(ValueError):
        service.run_one(os.path.join(src, "p0.jpg"), src, {"backend": "stub"})
    assert {n: open(os.path.join(src, n), "rb").read() for n in before if n.endswith(".jpg")} == before
    # a different format next to the sources is fine
    m = batch.run(RunConfig(backend="stub", out_format="tiff"), src, src)
    assert m["processed"] == 2 and os.path.isfile(os.path.join(src, "p0.tif"))


def test_adjust_finds_the_run_folder_from_the_recorded_subfolder(tmp_path):
    import pytest
    src = _photos(tmp_path / "in", n=1)
    out = str(tmp_path / "out")
    batch.run(RunConfig(backend="stub", out_subdir="web/2026"), src, out)
    target = os.path.join(out, "web", "2026", "p0.jpg")
    before = open(target, "rb").read()
    assert adjust.rerender(target)["image"] == target
    assert open(target, "rb").read() == before
    # two runs, the inner one without its manifest: never the outer run's photo over it
    other = _photos(tmp_path / "in2", n=1, size=(320, 240))
    batch.run(RunConfig(backend="stub"), src, out)
    inner = os.path.join(out, "b")
    batch.run(RunConfig(backend="stub"), other, inner)
    os.remove(os.path.join(inner, "run.json"))
    inner_img = os.path.join(inner, "p0.jpg")
    inner_before = open(inner_img, "rb").read()
    with pytest.raises(FileNotFoundError) as e:
        adjust.rerender(inner_img)
    assert "--source" in str(e.value)
    assert open(inner_img, "rb").read() == inner_before
    adjust.rerender(inner_img, source=os.path.join(other, "p0.jpg"))
    assert open(inner_img, "rb").read() == inner_before


def test_presets():
    import pytest
    kw = exports.preset_kwargs("web", _PACKAGED)
    assert kw == {"out_format": "jpeg", "quality": 85, "out_long_edge": 2048,
                  "out_name": "{stem}_web", "out_subdir": "web"}
    with pytest.raises(ValueError) as e:
        exports.preset_kwargs("nope", _PACKAGED)
    assert "web" in str(e.value) and "archive" in str(e.value)
    bad = os.path.join(os.path.dirname(_PACKAGED), "..", "..", "..", "x-missing.toml")
    with pytest.raises(ValueError):
        exports.preset_kwargs("web", bad)
    for name in exports.load(_PACKAGED):
        RunConfig(**exports.preset_kwargs(name, _PACKAGED))


def test_bad_preset_key(tmp_path):
    import pytest
    p = tmp_path / "e.toml"
    p.write_text('[x]\nfoo = 1\n')
    with pytest.raises(ValueError):
        exports.preset_kwargs("x", str(p))


def test_cli_preset_and_overrides(tmp_path):
    cfg = _cli(export="web")
    assert (cfg.out_format, cfg.quality, cfg.out_long_edge, cfg.out_name, cfg.out_subdir) == (
        "jpeg", 85, 2048, "{stem}_web", "web")
    cfg = _cli(export="web", quality=70, subdir="x")
    assert (cfg.quality, cfg.out_subdir, cfg.out_long_edge, cfg.out_name) == (70, "x", 2048, "{stem}_web")
    run = tmp_path / "run.toml"
    run.write_text('[output]\nexport = "social"\n')
    cfg = load_run_config(SimpleNamespace(configs_dir=None, config=str(run)))
    assert cfg.out_long_edge == 1350 and cfg.metadata == "none"
    assert service.make_cfg({"export": "webp"}).out_format == "webp"
    assert service.make_cfg({"export": "webp", "quality": 50}).quality == 50


def test_preset_order_files_over_preset_and_flags_over_all(tmp_path):
    run = tmp_path / "run.toml"
    run.write_text('[output]\nexport = "web"\nquality = 70\nsubdir = "mine"\n')
    cfg = load_run_config(SimpleNamespace(configs_dir=None, config=str(run)))
    assert (cfg.quality, cfg.out_subdir, cfg.out_long_edge, cfg.out_name) == (70, "mine", 2048, "{stem}_web")
    # `--export ""` switches a preset set in a file off: the plain defaults
    cfg = load_run_config(SimpleNamespace(configs_dir=None, config=str(run), export=""))
    assert (cfg.export, cfg.out_long_edge, cfg.out_name, cfg.quality) == (None, None, "{stem}", 70)
    run.write_text('[output]\nexport = "web"\n')
    cfg = load_run_config(SimpleNamespace(configs_dir=None, config=str(run), export=""))
    plain = _cli()
    assert cfg == plain
    assert load_run_config(SimpleNamespace(configs_dir=None, config=str(run), export="social")).out_long_edge == 1350


def test_batch_with_a_preset(tmp_path):
    src = _photos(tmp_path / "in")
    out = str(tmp_path / "out")
    batch.run(_cli(export="web", backend="stub"), src, out)
    web = os.path.join(out, "web")
    names = sorted(n for n in os.listdir(web) if n.endswith(".jpg"))
    assert names == ["p0_web.jpg", "p1_web.jpg"]
    assert PILImage.open(os.path.join(web, names[0])).size == (400, 300)
    side = json.load(open(os.path.join(web, "p0_web.autoedit.json")))
    assert side["audit"]["output"] == "p0_web.jpg"


# ---- review round 2: no export lands on any source of the run -----------------------------------

def _bytes(folder):
    return {n: open(os.path.join(folder, n), "rb").read() for n in os.listdir(folder)
            if n.lower().endswith((".jpg", ".png"))}


def _case_insensitive(folder) -> bool:
    probe = os.path.join(folder, "Probe.TXT")
    open(probe, "w").close()
    try:
        return os.path.exists(os.path.join(folder, "probe.txt"))
    finally:
        os.remove(probe)


def test_an_upper_case_source_is_not_replaced_on_a_case_insensitive_volume(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    if not _case_insensitive(str(folder)):
        return                                              # IMG_1.JPG and IMG_1.jpg are two files here
    PILImage.fromarray(np.full((60, 80, 3), 120, np.uint8), "RGB").save(str(folder / "IMG_1.JPG"), quality=95)
    before = _bytes(str(folder))
    m = batch.run(RunConfig(backend="stub"), str(folder), str(folder))      # the export is IMG_1.jpg
    assert m["processed"] == 0 and m["failed"] == 1
    assert "overwrite a source photo" in m["failures"][0]["error"]
    assert _bytes(str(folder)) == before


def test_an_export_never_lands_on_another_source_of_the_run(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    PILImage.fromarray(np.full((60, 80, 3), 90, np.uint8), "RGB").save(str(folder / "b.jpg"), quality=95)
    PILImage.fromarray(np.full((60, 80, 3), 160, np.uint8), "RGB").save(str(folder / "b.png"))
    before = _bytes(str(folder))
    m = batch.run(RunConfig(backend="stub", out_format="png"), str(folder), str(folder))
    # b.jpg would become b.png, over a source: it fails. b.png finds the name taken and goes to b-2.png.
    assert m["processed"] == 1 and m["failed"] == 1 and m["failures"][0]["image"] == "b.jpg"
    assert os.path.isfile(folder / "b-2.png")
    assert {n: _bytes(str(folder))[n] for n in before} == before
    # the same through the building block: with the run's sources the other source is protected
    jpg, png = str(folder / "b.jpg"), str(folder / "b.png")
    import pytest
    with pytest.raises(ValueError) as e:
        emit.output_paths(str(folder), jpg, "png", sources=emit.source_ids([jpg, png]))
    assert "overwrite a source photo" in str(e.value)
    emit.output_paths(str(folder), jpg, "png")              # without the set only its own source counts
    # TIFF exports next to JPEG and PNG sources are fine
    m = batch.run(RunConfig(backend="stub", out_format="tiff"), str(folder), str(folder))
    assert m["failed"] == 0 and m["processed"] == 3         # b.jpg, b.png and the b-2.png of the first run
    assert {n: _bytes(str(folder))[n] for n in before} == before


def test_a_second_run_replaces_its_own_earlier_exports(tmp_path):
    src = _photos(tmp_path / "in", n=2)
    out = str(tmp_path / "out")
    for _ in range(2):
        m = batch.run(RunConfig(backend="stub"), src, out)
        assert m["processed"] == 2 and m["failed"] == 0
    assert os.path.isfile(os.path.join(out, "p0.jpg"))


def test_a_job_does_not_replace_another_source(tmp_path):
    import time

    from autoedit.web import jobs
    folder = tmp_path / "shoot"
    folder.mkdir()
    PILImage.fromarray(np.full((60, 80, 3), 90, np.uint8), "RGB").save(str(folder / "b.jpg"), quality=95)
    PILImage.fromarray(np.full((60, 80, 3), 160, np.uint8), "RGB").save(str(folder / "b.png"))
    before = _bytes(str(folder))
    job = jobs.start(str(folder), None, str(folder), {"backend": "stub", "out_format": "png"})
    t0 = time.time()
    while job.status not in ("done", "cancelled", "error") and time.time() - t0 < 30:
        time.sleep(0.05)
    snap = jobs.snapshot(job)
    assert snap["failed"] == 1 and snap["items"][0]["status"] == "failed"
    assert "overwrite a source photo" in snap["items"][0]["error"]
    assert {n: _bytes(str(folder))[n] for n in before} == before


def test_a_name_problem_fails_one_photo_and_the_run_goes_on(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    for name, fmt in (("a.jpg", "JPEG"), ("b.jpg", "JPEG"), ("c.png", "PNG")):
        PILImage.fromarray(np.full((60, 80, 3), 100, np.uint8), "RGB").save(str(folder / name), format=fmt)
    png = open(folder / "c.png", "rb").read()
    for workers in (1, 2):                                   # the plain loop and the worker pool
        for leftover in ("a.png", "b.png", "run.json"):
            if (folder / leftover).exists():
                os.remove(folder / leftover)
        m = batch.run(RunConfig(backend="stub", out_format="png"), str(folder), str(folder), workers=workers)
        assert m["processed"] == 2 and m["failed"] == 1
        assert m["failures"][0]["image"] == "c.png" and "overwrite" in m["failures"][0]["error"]
        assert (folder / "run.json").exists()
        assert open(folder / "c.png", "rb").read() == png


def test_the_run_command_reports_a_value_error_and_returns_2(tmp_path):
    from autoedit import cli
    src = _photos(tmp_path / "in", n=1)
    real = batch.run

    def refuse(*a, **k):
        raise ValueError("no such thing")
    batch.run = refuse
    try:
        assert cli.main(["run", src, str(tmp_path / "out"), "--backend", "stub"]) == 2
    finally:
        batch.run = real


def test_a_subfolder_cannot_lead_out_of_the_output_folder(tmp_path):
    import pytest
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    try:
        os.symlink(str(outside), str(out / "web"))
    except (OSError, NotImplementedError):
        return                                               # no symlinks on this platform
    with pytest.raises(ValueError) as e:
        emit.output_paths(str(out), "x/IMG.jpg", "jpeg", subdir="web")
    assert "outside the output folder" in str(e.value)
    src = _photos(tmp_path / "in", n=1)
    m = batch.run(RunConfig(backend="stub", out_subdir="web"), src, str(out))
    assert m["failed"] == 1 and m["processed"] == 0
    assert os.listdir(outside) == []
    # a nested part that is the link leads out as well, a plain sub-folder does not
    with pytest.raises(ValueError):
        emit.output_paths(str(out), "x/IMG.jpg", "jpeg", subdir="web/2026")
    assert emit.output_paths(str(out), "x/IMG.jpg", "jpeg", subdir="fine/2026")[0].endswith("IMG.jpg")
    assert os.listdir(outside) == []


def test_the_number_field_takes_a_width_and_a_d_only():
    import pytest
    for bad in ("{n:c}", "{n:x}", "{n:>8}", "{n:.2f}", "{n:100}", "{n:b}", "{n:0}"):
        with pytest.raises(ValueError):
            RunConfig(out_name=bad)
    for good in ("{n}", "{n:4}", "{n:04d}", "{n:d}", "{n:99}"):
        RunConfig(out_name=good)
