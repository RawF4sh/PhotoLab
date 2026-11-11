"""Adversarial unattended workflow: failures, provenance, no-clobber, and fallback."""
import json
from unittest.mock import patch

import numpy as np
from PIL import Image as PILImage
import pytest

from autoedit.captures import discover_captures, sha256_file
from autoedit.contract import Result, RunConfig
from autoedit.imageio import Image
from autoedit.quality_runner import render_safe, run_quality


def _photo(path, color=(110, 120, 130)):
    PILImage.new("RGB", (48, 40), color).save(path)


def test_batch_isolates_corrupt_capture_and_prefers_raw_without_modifying_sources(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    (source / "paired.CR2").write_bytes(b"corrupt RAW")
    _photo(source / "paired.JPG")
    _photo(source / "good.jpeg", color=(120, 130, 140))
    (source / "broken.jpg").write_bytes(b"corrupt JPEG")
    (source / "paired.xmp").write_text("untouched")
    hashes = {p.name: sha256_file(p) for p in source.iterdir()}
    run = run_quality(str(source), str(out), RunConfig(backend="stub"))
    assert run["expected"] == 3 and run["processed"] == 1 and run["failed"] == 2
    assert not run["complete"] and not run["source_errors"]
    assert {p.name: sha256_file(p) for p in source.iterdir()} == hashes
    assert len(list(out.glob("*.autoedit.json"))) == 1
    raw = next(e for e in run["selected_inputs"] if e["source_format"] == "cr2")
    assert len(raw["companions"]) == 1 and raw["companions"][0]["path"].endswith("paired.JPG")


def test_resume_rejects_configuration_source_and_output_changes(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    cfg = RunConfig(backend="stub")
    run = run_quality(str(source), str(out), cfg)
    assert run["complete"]
    artifact = run["entries"][0]["output"]
    original_output = sha256_file(artifact)
    assert run_quality(str(source), str(out), cfg, resume=True)["complete"]
    assert sha256_file(artifact) == original_output
    with pytest.raises(ValueError):
        run_quality(str(source), str(out), cfg.model_copy(update={"intensity": .2}), resume=True)
    PILImage.new("RGB", (48, 40), (120, 120, 130)).save(source / "photo.jpg")
    with pytest.raises(ValueError):
        run_quality(str(source), str(out), cfg, resume=True)
    _photo(source / "photo.jpg")
    with open(artifact, "ab") as fh:
        fh.write(b"tampered")
    tampered = sha256_file(artifact)
    failed = run_quality(str(source), str(out), cfg, resume=True)
    assert failed["failed"] == 1 and not failed["complete"]
    assert sha256_file(artifact) == tampered


def test_resume_setup_failure_preserves_existing_manifest_and_artifacts(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    cfg = RunConfig(backend="stub")
    initial = run_quality(str(source), str(out), cfg)
    assert initial["complete"]
    source_before = sha256_file(source / "photo.jpg")
    before = {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()}
    for target in ("build_safe_engine", "load_quality_config"):
        with patch("autoedit.quality_runner." + target, side_effect=RuntimeError("setup unavailable")):
            with pytest.raises(RuntimeError):
                run_quality(str(source), str(out), cfg, resume=True)
        assert before == {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()}
        assert sha256_file(source / "photo.jpg") == source_before
    with patch("autoedit.quality_runner.render_safe", side_effect=AssertionError("must reuse accepted output")):
        assert run_quality(str(source), str(out), cfg, resume=True)["complete"]


def test_malformed_quality_config_during_resume_setup_preserves_manifest(tmp_path):
    from autoedit.quality_runner import build_safe_engine
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    quality_config = tmp_path / "quality.toml"
    valid_config = "clip_budget = 0.005\n"
    quality_config.write_text(valid_config)
    options = {"quality_config": str(quality_config)}
    cfg = RunConfig(backend="stub")
    initial = run_quality(str(source), str(out), cfg, options=options)
    assert initial["complete"]
    source_before = sha256_file(source / "photo.jpg")
    before = {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()}
    def config_changes_during_setup(*args, **kwargs):
        engine = build_safe_engine(*args, **kwargs)
        # Exercise actual TOML validation after resume identity has matched.
        quality_config.write_text("clip_budget = nan\n")
        return engine
    with patch("autoedit.quality_runner.build_safe_engine", side_effect=config_changes_during_setup):
        with pytest.raises(ValueError):
            run_quality(str(source), str(out), cfg, options=options, resume=True)
    assert before == {p.name: sha256_file(p) for p in out.iterdir() if p.is_file()}
    assert sha256_file(source / "photo.jpg") == source_before
    quality_config.write_text(valid_config)
    with patch("autoedit.quality_runner.render_safe", side_effect=AssertionError("must reuse accepted output")):
        assert run_quality(str(source), str(out), cfg, options=options, resume=True)["complete"]


def test_interrupted_resume_checkpoint_retains_unvisited_accepted_records(tmp_path):
    from pathlib import Path
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "first.jpg"); _photo(source / "second.jpg", (120, 130, 140))
    cfg = RunConfig(backend="stub")
    initial = run_quality(str(source), str(out), cfg)
    assert initial["complete"] and len(initial["entries"]) == 2
    last = initial["entries"][-1]
    before = {p.name: sha256_file(p) for p in out.iterdir() if p.name != "run.json" and p.is_file()}
    def interrupted_hash(path):
        if Path(path) == Path(last["output"]):
            raise KeyboardInterrupt("interrupted before verifying final accepted artifact")
        return sha256_file(path)
    with patch("autoedit.quality_runner.sha256_file", side_effect=interrupted_hash):
        with pytest.raises(KeyboardInterrupt):
            run_quality(str(source), str(out), cfg, resume=True)
    saved = json.loads((out / "run.json").read_text())
    assert not saved["complete"]
    assert saved["entries"] == initial["entries"]
    assert before == {p.name: sha256_file(p) for p in out.iterdir() if p.name != "run.json" and p.is_file()}
    with patch("autoedit.quality_runner.render_safe", side_effect=AssertionError("must reuse both outputs")):
        assert run_quality(str(source), str(out), cfg, resume=True)["complete"]


def test_resume_transient_verification_error_retains_original_accepted_evidence(tmp_path):
    from pathlib import Path
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    cfg = RunConfig(backend="stub")
    initial = run_quality(str(source), str(out), cfg)
    accepted = initial["entries"][0]
    before = {p.name: sha256_file(p) for p in out.iterdir() if p.suffix == ".jpg" or p.name.endswith(".autoedit.json") or p.suffix == ".npz"}
    def unavailable_hash(path):
        if Path(path) == Path(accepted["output"]):
            raise OSError("temporarily unreadable")
        return sha256_file(path)
    with patch("autoedit.quality_runner.sha256_file", side_effect=unavailable_hash):
        failed = run_quality(str(source), str(out), cfg, resume=True)
    assert not failed["complete"] and failed["failed"] == 1
    assert failed["entries"][0]["previous_accepted"] == accepted
    assert before == {p.name: sha256_file(p) for p in out.iterdir() if p.suffix == ".jpg" or p.name.endswith(".autoedit.json") or p.suffix == ".npz"}
    # Saved provenance permits verification, never bypasses it: tampering still fails.
    artifact = Path(accepted["output"])
    original = artifact.read_bytes()
    artifact.write_bytes(original + b"tampered")
    changed = run_quality(str(source), str(out), cfg, resume=True)
    assert not changed["complete"] and changed["failed"] == 1
    assert changed["entries"][0]["previous_accepted"] == accepted
    assert artifact.read_bytes() == original + b"tampered"
    artifact.write_bytes(original)
    with patch("autoedit.quality_runner.render_safe", side_effect=AssertionError("must verify and reuse")):
        recovered = run_quality(str(source), str(out), cfg, resume=True)
    assert recovered["complete"] and recovered["entries"] == initial["entries"]


def test_failed_resume_item_checkpoint_preserves_later_accepted_record(tmp_path):
    from pathlib import Path
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); (source / "first.jpg").write_bytes(b"corrupt JPEG"); _photo(source / "second.jpg")
    cfg = RunConfig(backend="stub")
    initial = run_quality(str(source), str(out), cfg)
    assert initial["failed"] == 1 and initial["processed"] == 1
    accepted = next(e for e in initial["entries"] if e["status"] == "accepted")
    def interrupted_hash(path):
        if Path(path) == Path(accepted["output"]):
            raise KeyboardInterrupt("interrupted after earlier failed input checkpoint")
        return sha256_file(path)
    with patch("autoedit.quality_runner.sha256_file", side_effect=interrupted_hash):
        with pytest.raises(KeyboardInterrupt):
            run_quality(str(source), str(out), cfg, resume=True)
    saved = json.loads((out / "run.json").read_text())
    assert next(e for e in saved["entries"] if e["capture_id"] == accepted["capture_id"]) == accepted
    with patch("autoedit.quality_runner.render_safe", side_effect=AssertionError("must reuse accepted output")):
        resumed = run_quality(str(source), str(out), cfg, resume=True)
    assert resumed["failed"] == 1 and resumed["processed"] == 1
    assert next(e for e in resumed["entries"] if e["status"] == "accepted") == accepted


def test_final_source_integrity_failure_preserves_accepted_resume_evidence(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    cfg = RunConfig(backend="stub")
    initial = run_quality(str(source), str(out), cfg)
    accepted = initial["entries"][0]
    before = {p.name: sha256_file(p) for p in out.iterdir() if p.suffix == ".jpg" or p.name.endswith(".autoedit.json") or p.suffix == ".npz"}
    with patch("autoedit.quality_runner.verify_sources", return_value=["companion integrity unavailable"]):
        failed = run_quality(str(source), str(out), cfg, resume=True)
    assert not failed["complete"] and failed["source_errors"]
    assert failed["entries"] == [accepted]
    with patch("autoedit.quality_runner.verify_sources", side_effect=OSError("source audit unavailable")):
        with pytest.raises(OSError):
            run_quality(str(source), str(out), cfg, resume=True)
    saved = json.loads((out / "run.json").read_text())
    assert not saved["complete"] and saved["entries"] == [accepted]
    assert before == {p.name: sha256_file(p) for p in out.iterdir() if p.suffix == ".jpg" or p.name.endswith(".autoedit.json") or p.suffix == ".npz"}
    with patch("autoedit.quality_runner.render_safe", side_effect=AssertionError("must reuse after source audit recovers")):
        assert run_quality(str(source), str(out), cfg, resume=True)["complete"]


def test_manifest_cannot_escape_output_or_reuse_capture_id(tmp_path):
    source = tmp_path / "source"; source.mkdir(); _photo(source / "photo.jpg")
    manifest = discover_captures(source)
    frozen = tmp_path / "inputs.json"
    manifest["entries"][0]["capture_id"] = "../source/photo"
    frozen.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        run_quality(str(source), str(tmp_path / "out"), source_manifest=str(frozen))
    manifest = discover_captures(source)
    manifest["entries"].append(dict(manifest["entries"][0]))
    frozen.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        run_quality(str(source), str(tmp_path / "out"), source_manifest=str(frozen))


def test_sam_constructor_failure_degrades_locally_and_is_audited(tmp_path):
    from autoedit.workflows import WorkflowEngine
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    def build(cfg, **kw):
        if cfg.backend == "sam3":
            raise ModuleNotFoundError("torch missing")
        return WorkflowEngine(cfg, **kw)
    with patch("autoedit.workflows.WorkflowEngine", side_effect=build):
        run = run_quality(str(source), str(out), RunConfig(backend="sam3"))
    assert run["complete"] and run["entries"][0]["actual_backend"] == "none"
    assert run["fallbacks"] == 1 and run["entries"][0]["fallback"]
    assert "torch missing" in run["entries"][0]["backend_fallback"]
    sidecar = json.loads(open(run["entries"][0]["sidecar"]).read())
    assert sidecar["audit"]["requested_backend"] == "sam3"


def test_pixel_guard_records_bounded_fallback_and_handles_nan_proposal():
    source = Image(rgb=np.full((40, 48, 3), .2, np.float32))
    class Engine:
        last_masks = {}
        def __init__(self, fill):
            self.fill = fill
        def process(self, path, **kwargs):
            return np.full_like(source.rgb, self.fill), Result(image=path, scene="other", confidence=1,
                                                               global_settings={"exposure": 2})
    for fill in (1.0, float("nan")):
        rgb, result = render_safe("fixture.jpg", RunConfig(), image=source, common_source=source,
                                  engine=Engine(fill))
        assert np.isfinite(rgb).all() and result.audit["assessment"]["passed"]
        assert not result.audit["proposal_assessment"]["passed"]
        assert result.audit["remediation_factor"] in (.75, .5, .25, .125, 0)
        assert len(result.audit["remediation_attempts"]) <= 6
    assert np.array_equal(rgb, source.rgb) and result.global_settings == {}


def test_encoded_failure_never_publishes_accepted_output(tmp_path):
    from autoedit import quality_runner as runner
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    original = sha256_file(source / "photo.jpg")
    real_load = runner.load_image
    def bad_export(path, *args, **kw):
        image = real_load(path, *args, **kw)
        if "checking" in str(path):
            image.rgb[:] = np.nan
        return image
    with patch.object(runner, "load_image", side_effect=bad_export):
        run = run_quality(str(source), str(out))
    assert run["failed"] == 1 and not run["complete"]
    assert not list(out.glob("*.autoedit.json"))
    assert sha256_file(source / "photo.jpg") == original


def test_inference_fallback_clears_original_engine_masks():
    source = Image(rgb=np.full((40, 48, 3), .4, np.float32))
    class BrokenSam:
        last_masks = {"sky": np.ones((40, 48), bool)}
        def process(self, *args, **kw):
            raise RuntimeError("SAM unavailable during inference")
    engine = BrokenSam()
    _, result = render_safe("fixture.jpg", RunConfig(backend="sam3"), image=source,
                             common_source=source, engine=engine)
    assert engine.last_masks == {}
    assert result.audit["actual_backend"] == "none"
    assert result.needs_review and result.review_reason


def test_sidecar_race_does_not_overwrite_unrelated_file(tmp_path):
    from autoedit import quality_runner as runner
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    cid = discover_captures(source)["entries"][0]["capture_id"]
    sidecar = out / (cid + ".autoedit.json")
    real_render = runner.render_safe
    def race(*args, **kwargs):
        result = real_render(*args, **kwargs)
        sidecar.write_text("unrelated content appeared during rendering")
        return result
    with patch.object(runner, "render_safe", side_effect=race):
        run = run_quality(str(source), str(out))
    assert not run["complete"] and run["failed"] == 1
    assert sidecar.read_text() == "unrelated content appeared during rendering"


def test_manifest_atomic_writer_never_follows_old_temp_symlink(tmp_path):
    from autoedit.quality_runner import write_json
    source = tmp_path / "original.jpg"; source.write_bytes(b"original photo bytes")
    target = tmp_path / "run.json"
    (tmp_path / "run.json.part").symlink_to(source)
    write_json(target, {"complete": True})
    assert source.read_bytes() == b"original photo bytes"
    assert json.loads(target.read_text())["complete"]


def test_custom_quality_config_content_change_invalidates_resume(tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    quality = tmp_path / "quality.toml"
    quality.write_text("clip_budget = 0.005\n")
    options = {"quality_config": str(quality)}
    assert run_quality(str(source), str(out), options=options)["complete"]
    quality.write_text("clip_budget = 0.001\n")
    with pytest.raises(ValueError):
        run_quality(str(source), str(out), options=options, resume=True)


def test_existing_curation_metadata_does_not_hide_new_supported_photographs(tmp_path):
    _photo(tmp_path / "curated.jpg")
    _photo(tmp_path / "added.jpeg", color=(120, 130, 140))
    (tmp_path / "candidates.json").write_text(json.dumps({"items": [
        {"source_path": "/historical/location/curated.jpg", "copied_path": str(tmp_path / "curated.jpg")}
    ]}))
    selected = discover_captures(tmp_path)
    assert selected["counts"]["captures"] == 2


def test_safe_render_does_not_claim_uninvoked_requested_sam():
    from autoedit.workflows import WorkflowEngine
    source = Image(rgb=np.full((40, 48, 3), .4, np.float32))
    cfg = RunConfig(backend="sam3")
    engine = WorkflowEngine(cfg, "global-first")
    _, result = render_safe("fixture.jpg", cfg, workflow="global-first", image=source,
                            common_source=source, engine=engine)
    assert result.audit["requested_backend"] == "sam3"
    assert result.audit["actual_backend"] == "none"
    assert result.audit["backend_fallback"] is None


def test_resume_dependency_identity_covers_tiff_precision_and_sam_runtime(tmp_path):
    from autoedit import quality_runner as runner
    evidence = runner.configuration_evidence(RunConfig(), "current")
    versions = evidence["runtime"]["versions"]
    dependencies = ("tifffile", "torchvision", "opencv-python", "opencv-python-headless",
                    "opencv-contrib-python", "opencv-contrib-python-headless")
    assert set(dependencies) <= set(versions)
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir(); _photo(source / "photo.jpg")
    assert run_quality(str(source), str(out))["complete"]
    real_version = runner.importlib.metadata.version
    for dependency in dependencies:
        def changed(name):
            return "audit-test-different-version" if name == dependency else real_version(name)
        with patch.object(runner.importlib.metadata, "version", side_effect=changed):
            with pytest.raises(ValueError):
                run_quality(str(source), str(out), resume=True)


def test_quality_cli_explicit_raw_mode_overrides_experimental_default(tmp_path):
    import importlib.util
    from pathlib import Path
    from autoedit.workflows import WorkflowEngine
    path = Path(__file__).resolve().parents[1] / "scripts" / "quality_workflow.py"
    spec = importlib.util.spec_from_file_location("quality_cli_explicit_mode_test", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    seen = []
    def observe(_source, _output, cfg, *, workflow, options, **kwargs):
        engine = WorkflowEngine(cfg, workflow, options)
        seen.append(engine.decode_options()["raw_mode"])
        return {"processed": 0, "expected": 0, "failed": 0, "fallbacks": 0,
                "source_errors": [], "complete": True}
    config = tmp_path / "options.json"
    config.write_text(json.dumps({"options": {"raw_mode": "tone_target"}}))
    with patch.object(cli, "run_quality", side_effect=observe):
        for workflow in ("global-first", "optimized"):
            for mode in ("none", "libraw_auto"):
                assert cli.main(["input", "output", "--workflow", workflow, "--raw-mode", mode,
                                 "--config", str(config)]) == 0
                assert seen[-1] == mode
        assert cli.main(["input", "output", "--workflow", "global-first"]) == 0
        assert seen[-1] == "linear_srgb"  # Implicit default remains unchanged.
        assert cli.main(["input", "output", "--workflow", "current", "--raw-mode", "none"]) == 0
        assert seen[-1] == "none"


def test_encoded_only_remediation_has_review_reason_and_preserves_existing_context(tmp_path):
    from autoedit import quality_runner as runner
    source = tmp_path / 'source'; source.mkdir(); _photo(source / 'photo.jpg')
    real_load, real_render = runner.load_image, runner.render_safe
    for index, prior_reason in enumerate(('', 'Existing review context')):
        calls = {'exports': 0}
        def damage_first_encoded_read(path, *args, **kwargs):
            image = real_load(path, *args, **kwargs)
            if '.checking-' in str(path):
                calls['exports'] += 1
                if calls['exports'] == 1:
                    image.rgb[:] = .99  # Simulate a lossy encoder clipping regression.
            return image
        def preserve_prior_context(*args, **kwargs):
            rgb, result = real_render(*args, **kwargs)
            assert result.audit['remediation_factor'] == 1
            assert result.audit['assessment']['passed']
            result.review_reason = prior_reason
            return rgb, result
        with patch.object(runner, 'load_image', side_effect=damage_first_encoded_read), \
                patch.object(runner, 'render_safe', side_effect=preserve_prior_context):
            run = run_quality(str(source), str(tmp_path / f'out-{index}'), RunConfig(backend='stub'))
        assert run['complete'] and run['fallbacks'] == 1 and not run['source_errors']
        sidecar = json.loads(open(run['entries'][0]['sidecar']).read())
        audit = sidecar['audit']
        assert audit['remediation_factor'] == 1  # The float proposal needed no remediation.
        assert audit['encoded_remediation_factor'] == .75 and calls['exports'] == 2
        assert not audit['encoded_attempts'][0]['passed'] and audit['export_assessment']['passed']
        assert sidecar['needs_review']
        assert 'additional source blend 0.75' in sidecar['review_reason']
        assert 'introduced_rgb_high_clipping' in sidecar['review_reason']
        if prior_reason:
            assert sidecar['review_reason'].startswith(prior_reason + '; ')
