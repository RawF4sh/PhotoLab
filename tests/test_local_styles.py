"""Local styles use measurable, bounded edits and need an explicit release."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import compose, geometry
from autoedit.contract import RunConfig
from autoedit.edit import gates, ops, style_target
from autoedit.edit.reference import reference_features
from autoedit.imageio import Image, luma
from autoedit.pipeline import Pipeline
from autoedit.quality import assess_pixels, load_quality_config
from autoedit.quality_runner import style_thresholds
from autoedit.segment.base import RegionMask
from autoedit.styles import load_styles, local_catalog, offered_styles


def frame(level=.18, hw=(64, 64)):
    return np.full((*hw, 3), level, dtype=np.float32)


def test_standard_and_specialist_release():
    cfg = RunConfig(backend="stub")
    styles = load_styles(cfg.styles_path)
    assert [s["id"] for s in local_catalog(styles)] == ["standard"]
    assert {s["id"] for s in local_catalog(styles, True)} == {"standard", "blue_hour", "sunset_glow", "moody_landscape"}
    assert "natural" in offered_styles(styles) and "blue_hour" not in offered_styles(styles)
    assert Pipeline.build(RunConfig(style="sunset_glow")).cfg.denoise
    assert not Pipeline.build(RunConfig(style="sunset_glow", denoise=False)).cfg.denoise


def test_burn_keeps_black_hue_and_tone_order():
    ramp = np.linspace(0, 1, 1000, dtype=np.float32)[None, :, None] * np.array([1., .8, .6], np.float32)
    assert np.array_equal(ops.apply_ops(ramp, {"burn_ev": 0}), ramp)
    edited = ops.apply_ops(ramp, {"burn_ev": 2})
    pre, post = luma(ramp), luma(edited)
    assert np.array_equal(ramp[pre <= .03], edited[pre <= .03])
    assert (np.diff(post) > 0).all()
    assert np.array_equal(pre < .02, post < .02)
    assert np.allclose(edited[:, 1:, 1] / edited[:, 1:, 0], .8)
    assert np.isclose(post[0,-1] / pre[0,-1], .25)


def test_target_moves_to_nearest_edge_without_overshoot():
    src, quality = frame(), load_quality_config()
    bounds = {"p50": [.27, .42]}
    settings, why = style_target.fit(src, {}, bounds, .75, {"exposure": 1.5}, quality)
    median = reference_features(ops.apply_ops(src, settings))["p50"]
    assert .244 < median <= .18 + .75*(.27-.18) + 1e-6
    assert any("p50" in line for line in why)
    assert style_target.fit(frame(.30), {}, bounds, 1, {}, quality) == ({}, [])
    assert style_target.fit(src, {}, {"hold": True}, 1, {}, quality) == ({}, [])
    assert style_target.fit(src, {}, bounds, 0, {}, quality) == ({}, [])


def test_target_checks_isolated_rgb_clip_on_whole_proxy():
    src = frame(hw=(321, 321))
    # This bright red pin-light falls between sampled rows and columns.
    src[1, 1] = [.976, .5, .3]
    quality = {**load_quality_config(), "clip_budget": 0.0}
    settings, _ = style_target.fit(src, {}, {"p50": [.42, .6]}, 1, {"exposure": 1.5}, quality)
    edited = ops.apply_ops(src, settings)
    assert style_target.clipping_ok(src, edited, quality)
    assert edited[1, 1, 0] <= .98


def test_confined_gradient_and_trusted_horizon():
    cfg = RunConfig(style="moody_landscape")
    pipe = Pipeline.build(cfg)
    moves = {m.name: m for m in pipe.compose_moves}
    assert moves["sky_grad"].ops == {"burn_ev": 0.25}
    assert moves["far_shore"].ops == {"exposure": 0.2}
    sky = np.zeros((80, 96), bool)
    sky[:24] = True
    water = ~sky
    trusted = geometry.horizon_position({"water": water, "sky": sky}, geometry.config(pipe.rules))
    assert trusted and np.allclose(trusted, [.3,.3], atol=.025)
    assert geometry.horizon_position({}, geometry.config(pipe.rules)) is None
    named = [moves["sky_grad"], moves["far_shore"]]
    masks = {"sky": sky}
    assert compose.plan(named, {}, sky.shape, 1) == []
    assert [n for n, _, _ in compose.plan(named, masks, sky.shape, 1)] == ["sky_grad"]
    planned = {n:a for n,a,_ in compose.plan(named, masks, sky.shape, 1, horizon=trusted)}
    assert not planned["sky_grad"][31:].any()
    assert planned["sky_grad"][:10].mean() > .5
    assert np.argmax(planned["far_shore"].mean(axis=1)) in range(22,26)
    assert not planned["far_shore"][:15].any() and not planned["far_shore"][32:].any()


def test_lamp_aliases_and_hold_have_named_settings(tmp_path):
    sky = frame(.19)
    mask = np.zeros(sky.shape[:2], bool)
    mask[28:38, 28:38] = True
    sky[mask] = [.89, .69, .43]
    path = tmp_path / "lamps.png"
    PILImage.fromarray(np.uint8(255*sky)).save(path)
    pipe = Pipeline.build(RunConfig(style="blue_hour", intensity=1))
    other = mask[::-1].copy()
    segmenter = Mock()
    segmenter.segment.return_value = [RegionMask("lamp", mask, .9), RegionMask("lit window", other, .85)]
    pipe.segmenter = segmenter
    _, report = pipe.process_one(str(path), do_apply=False)
    prompts = segmenter.segment.call_args.args[1]
    assert set(("lamp", "street light", "lit window")) <= set(prompts)
    lamps = next(m for m in report.masks if m.target == "lights")
    assert lamps.ops["highlights"] < 0 and lamps.ops["exposure"] < 0
    assert lamps.ops["exposure"] + report.global_settings.get("exposure", 0) <= .7 + 1e-6
    assert np.isclose(lamps.coverage, (mask|other).mean())
    assert any("Blue hour" in note and "frame" in note for note in report.notes)
    assert not any(note.startswith("Blue hour, lights:") for note in report.notes)   # a held region is never fitted


def test_declared_intent_only_relaxes_relative_checks():
    src = frame(.4)
    richer = src * np.array([1.6, .9, .65],np.float32)
    audit = {"local_style": {"name": "sunset_glow", "target": "sunset_glow", "intent": ["richer"]}}
    rich = style_thresholds(audit, {}, "sunset_glow")
    assert style_thresholds(audit, {}, "standard") == {}
    assert "excessive_color_increase" in assess_pixels(src,richer)["failures"]
    assert "excessive_color_increase" not in assess_pixels(src,richer,thresholds=rich)["failures"]
    clipped = np.ones_like(src)
    assert set(assess_pixels(src,clipped,thresholds=rich)["failures"]) >= {"introduced_rgb_high_clipping"}
    assert "dark_made_darker" in assess_pixels(frame(.2), frame(.1), thresholds=rich)["failures"]
    audit = {"local_style": {"name": "moody_landscape", "target": "moody_landscape", "intent": ["darker", "richer"]}}
    darker = style_thresholds(audit, {}, "moody_landscape")
    assert "dark_made_darker" not in assess_pixels(frame(.2),frame(.1),thresholds=darker)["failures"]
    protected, _ = gates.chroma_guard(src, richer, max_increase=.9, protect=np.ones(src.shape[:2],np.float32))
    assert np.max(np.ptp(protected,axis=-1)) <= .00801


def test_target_extraction_quartiles_and_no_paths(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/style_target.py"
    spec = importlib.util.spec_from_file_location("local_target_tool", script)
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    photos = []
    for i, level in enumerate((51, 102, 153, 204)):
        p = tmp_path / f"owner-file-{i}.png"
        PILImage.fromarray(np.full((32, 32, 3), level, np.uint8)).save(p)
        photos.append(p)
    fields = {"global": {"p50": []}, "lights": {"hold": True}}
    got = target.extract(photos, Pipeline.build(RunConfig(concepts=[], intensity=0)), fields)
    assert got["meta"] == {"photos": 4, "region_counts": {"lights":0}}
    assert got["lights"] == {"hold": True}
    assert np.allclose(got["global"]["p50"], [.35,.65])
    assert "owner-file" not in repr(got)


def test_reference_manifest_rejects_holdout_and_research(tmp_path):
    import csv
    import json

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location("target_role_tool", scripts / "style_target.py")
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)     # the script finds its neighbour web_corpus.py by itself
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="") as f:
        writer = csv.DictWriter(f,fieldnames=["id","source","role","kind"])
        writer.writeheader()
        writer.writerow(dict(id="research",source="fivek",role="after",kind="blue_hour"))
    listed = tmp_path / "target-blue_hour.txt"
    listed.write_text(str(tmp_path / "photo.jpg"))
    freeze = {"blue_hour": {"target": ["research"], "panel": [], "holdout": []}}
    (tmp_path / "split.json").write_text(json.dumps(freeze))
    with pytest.raises(ValueError, match="eligible finished"):
        target.finished_files(listed, manifest, "blue_hour")
    freeze["blue_hour"]["holdout"] = ["research"]
    (tmp_path / "split.json").write_text(json.dumps(freeze))
    with pytest.raises(ValueError, match="held-back"):
        target.finished_files(listed, manifest, "blue_hour")


def test_blue_hour_lifts_the_frame_without_clipping_lamp_pixels():
    from autoedit.imageio import srgb_to_linear

    rgb = frame(.19, (128, 160))
    y, x = np.indices(rgb.shape[:2])
    lamp = (x - 90)**2 + (y - 74)**2 < 8**2
    rgb[lamp] = [.92, .69, .43]
    pipe = Pipeline.build(RunConfig(backend="stub", style="blue_hour", intensity=1))
    pipe.segmenter = Mock()
    pipe.segmenter.segment.return_value = [RegionMask("lamp", lamp, .9)]
    edited, result = pipe.process_one("scene.jpg", image=Image(rgb))
    ev = np.log2(luma(srgb_to_linear(edited)) / luma(srgb_to_linear(rgb)))
    assert np.median(ev[~lamp]) > .9 and np.median(ev[lamp]) < .7
    assert assess_pixels(rgb, edited)["passed"] and not result.needs_review


def test_native_files_keep_absolute_artifact_gates_for_each_intent(tmp_path):
    from autoedit.imageio import load_image

    # Round-trip every independent artifact through lossless RGB8 to exercise exported pixels.
    sky = np.repeat(np.linspace(.78,.94,256, dtype=np.float32)[:,None,None], 512, axis=1).repeat(3,axis=2)
    mask = np.zeros(sky.shape[:2],bool)
    mask[:,256:] = True
    halo = sky.copy()
    halo[:,256] = .52
    fixtures = {
        "high": (np.full_like(sky,.99), {"introduced_luma_high_clipping","introduced_rgb_high_clipping"}),
        "low": (np.full_like(sky,.01), {"introduced_luma_low_clipping","introduced_rgb_low_clipping"}),
        "band": (np.round(sky*13)/13, {"severe_new_banding"}),
        "halo": (halo, {"severe_region_boundary_artifact"}),
    }
    src = tmp_path/"source.png"
    PILImage.fromarray(np.uint8(np.rint(sky*255))).save(src)
    native = load_image(str(src)).rgb
    for name,(pixels,required) in fixtures.items():
        dst = tmp_path/f"{name}.png"
        PILImage.fromarray(np.uint8(np.rint(pixels*255))).save(dst)
        after = load_image(str(dst)).rgb
        for selected, intent in (("sunset_glow",["richer"]),("moody_landscape",["darker","richer"])):
            audit = {"local_style": {"name":selected,"target":selected,"intent":intent}}
            thresholds = style_thresholds(audit, {}, selected)
            failures = set(assess_pixels(native, after, {"sky":mask}, thresholds=thresholds)["failures"])
            assert required <= failures, (name,failures)


def test_sunset_noise_reduction_survives_native_export(tmp_path):
    from autoedit.imageio import load_image

    rng = np.random.default_rng(25)
    pixels = np.clip(.16 + rng.normal(0,.021,(128,128,3)), 0,1).astype(np.float32)
    source = Image(pixels, exif={"iso":12800})
    grain = []
    for label, options in (("disabled", {"denoise":False}), ("style", {})):
        pipe = Pipeline.build(RunConfig(style="sunset_glow", concepts=[], **options))
        edited, result = pipe.process_one("sample.cr2", image=source)
        path = tmp_path/f"{label}.png"
        PILImage.fromarray(np.uint8(np.rint(255*edited))).save(path)
        native = load_image(str(path)).rgb
        grain.append(float(np.std(np.diff(native[...,0]-native[...,1],axis=1))))
        assert bool(result.global_settings.get("denoise")) == (label == "style")
    assert grain[1] < .5*grain[0]


def test_richer_specialists_keep_a_bounded_guard_with_profile_overrides(tmp_path):
    original = Path(RunConfig().rules_path).read_text()
    rules = tmp_path / "rules.toml"
    for value, expected in (('"off"', .60), ('1.3', .90)):
        rules.write_text(original.replace("max_chroma_increase = 0.30", f"max_chroma_increase = {value}"))
        assert Pipeline.build(RunConfig(style="sunset_glow", rules_path=str(rules))).chroma_max == expected


def test_frame_and_region_share_the_style_lift_budget():
    src = frame(.15)
    mask = np.ones(src.shape[:2], bool)
    settings, _ = style_target.fit(src, {}, {"p50": [.60, .75]}, 1.0, {"exposure": 2.0},
                                   load_quality_config(), mask=mask, global_after={"exposure":1.25})
    assert .70 <= settings["exposure"] <= .75


def test_a_regions_own_lift_shares_the_budget_with_the_frame():
    # The rules gave the water +0.6 EV before the style raised the frame from 0.54 to 1.99 on a JPEG.
    regions = {"water": {"exposure": .6, "contrast": 4}, "lights": {"exposure": .9}, "sky": {"exposure": -.2}}
    found = style_target.over_budget(regions, 1.99, .54, 2.0, held=["lights"])
    assert set(found) == {"water"} and found["water"][0] == .6 and np.isclose(found["water"][1], .01)
    # Never more than the frame gained: what the rules alone gave stays the floor.
    assert np.isclose(style_target.over_budget({"water": {"exposure": .7}}, 1.6, 1.5, 2.0)["water"][1], .6)
    assert style_target.over_budget(regions, 1.99, .54, None) == {}      # no limit for this source
    assert style_target.over_budget(regions, .40, .54, 2.0) == {}        # the style lowered the frame
    # Through the pipeline: no region of a JPEG ends above the style's limit together with the frame.
    # The rules lift a mountain that is darker than the frame, which the budget has to take back.
    rgb = frame(.14, (96, 128))
    mountain = np.zeros(rgb.shape[:2], bool)
    mountain[48:] = True
    rgb[mountain] = .04
    pipe = Pipeline.build(RunConfig(backend="stub", style="sunset_glow", intensity=1))
    pipe.segmenter = Mock()
    pipe.segmenter.segment.return_value = [RegionMask("mountain", mountain, .9)]
    _, result = pipe.process_one("scene.jpg", image=Image(rgb), do_apply=False)
    frame_ev = result.global_settings.get("exposure", 0.0)
    assert frame_ev > 1.5
    assert any("mountain lift reduced by" in note and "within 2 EV" in note for note in result.notes)
    for region in result.masks:
        assert frame_ev + max(0.0, region.ops.get("exposure", 0.0)) <= 2.0 + 1e-6, region.target
    # The shoot-consistency nudge is bound by the same budget, and its note says what it did.
    _, nudged = pipe.process_one("scene.jpg", image=Image(rgb), do_apply=False, exposure_bias=.6)
    assert nudged.global_settings["exposure"] <= 2.0 + 1e-6
    assert any(note.startswith("batch normalize:") and "limited by the style's lift budget" in note
               for note in nudged.notes)
    # Without a style target the nudge and its note are what they always were.
    plain = Pipeline.build(RunConfig(backend="stub"))
    _, default = plain.process_one("scene.jpg", image=Image(rgb), do_apply=False, exposure_bias=.6)
    assert "batch normalize: +0.60 EV toward group ?" in default.notes


def test_region_search_stays_inside_the_regions_own_rails():
    # A cool patch, and targets that ask for more warmth and colour than a sky may take.
    rng = np.random.default_rng(3)
    src = np.clip(np.array([.30, .38, .52], np.float32) + rng.normal(0, .03, (64, 64, 3)).astype(np.float32), 0, 1)
    mask = np.ones(src.shape[:2], bool)
    quality = load_quality_config()
    warmer, richer = {"warmth": [.10, .20]}, {"sat": [.60, .80]}
    free, _ = style_target.fit(src, {}, warmer, 1.0, {}, quality, mask=mask)
    assert free["temp"] > 4
    railed, why = style_target.fit(src, {}, warmer, 1.0, {}, quality, mask=mask, caps={"temp": 4})
    assert 0 < railed["temp"] <= 4 + 1e-6
    assert any(line.startswith("temp +") and float(line.split("+")[1]) <= 4.05 for line in why)   # the note is what is applied
    assert style_target.fit(src, {}, richer, 1.0, {}, quality, mask=mask)[0]["saturation"] > 0
    assert style_target.fit(src, {}, richer, 1.0, {}, quality, mask=mask, forbidden=("saturation",)) == ({}, [])


def test_candidates_stay_hidden_and_standard_takes_no_target(tmp_path):
    packaged = Path(RunConfig().styles_path).read_text()
    styles = tmp_path / "styles.toml"
    # A specialist without a label is still a candidate until it is tested.
    styles.write_text(packaged + '\n[quiet_dusk]\ntarget = "blue_hour"\npull = 0.5\n')
    loaded = load_styles(str(styles))
    assert "quiet_dusk" in loaded and "quiet_dusk" not in offered_styles(loaded)
    styles.write_text(packaged + '\n[quiet_dusk]\ntarget = "blue_hour"\npull = 0.5\ntested = true\n')
    assert "quiet_dusk" in offered_styles(load_styles(str(styles)))
    for extra in ('target = "blue_hour"', "run = { denoise = true }", 'moves = { sky_grad = { ops = { burn_ev = 0.2 } } }'):
        styles.write_text('[natural]\nlook = "natural"\n' + extra + "\n")
        with pytest.raises(ValueError, match="standard style takes no"):
            load_styles(str(styles))


def test_richer_is_declared_by_a_style_never_by_a_config_file(tmp_path):
    custom = tmp_path / "quality.toml"
    custom.write_text("richer = true\n")
    with pytest.raises(ValueError, match="declared by a style"):
        load_quality_config(str(custom))
    custom.write_text("preserve_exposure = true\n")
    assert load_quality_config(str(custom))["preserve_exposure"] is True
    # The post-edit gate: only the relative colour warning is skipped, clipping still counts.
    from autoedit.contract import GlobalStatsModel
    pre = GlobalStatsModel(mean_lum=.4, mean_sat=.20, shadow_clip=0, highlight_clip=0, haze=.2)
    vivid = pre.model_copy(update={"mean_sat": .45})
    assert any("saturation rose" in w for w in gates.assess(pre, vivid)[3])
    assert not any("saturation rose" in w for w in gates.assess(pre, vivid, richer=True)[3])
    clipped = vivid.model_copy(update={"highlight_clip": .30})
    score, review, _, warnings = gates.assess(pre, clipped, richer=True)
    assert review and warnings and score < gates.assess(pre, vivid, richer=True)[0]


def test_lights_rules_golden():
    """[opt_in.lights]: the lamps take back the frame's lift by how dark the whole frame is, and
    pull their highlights down by how much of them is clipped. The same numbers in every scene."""
    from autoedit.contract import GlobalStatsModel, RegionStatsModel
    from autoedit.paths import config_path
    from autoedit.rules import apply, load

    path = config_path("rules.toml")
    plain = load(path)
    assert all("lights" not in plain.get(scene, {}) for scene in ("people", "landscape", "other"))
    rules = load(path, require=["lights"])
    for scene in ("people", "landscape", "other"):
        for mean_lum, clip, exposure, highlights in ((.10, 0.0, -.3444, -12.0), (.30, .5, -.1804, -22.0),
                                                     (.52, 1.0, 0.0, -32.0), (.80, 2.0, 0.0, -32.0)):
            whole = GlobalStatsModel(mean_lum=mean_lum, mean_sat=.2, shadow_clip=0, highlight_clip=0, haze=.2)
            lamps = {"lights": RegionStatsModel(name="lights", lum=.8, sat=.3, clip_low=0, clip_high=clip, coverage=.01)}
            got = apply(rules, scene, whole, lamps, {}, intensity=1.0)[1]["lights"]
            assert abs(got["exposure"] - exposure) < 1e-9 and abs(got["highlights"] - highlights) < 1e-9, (scene, mean_lum)


def test_the_model_is_not_offered_the_burn():
    import json

    from autoedit.decide import llm_decider

    assert "burn_ev" in ops.OP_BOUNDS and "burn_ev" not in llm_decider._MODEL_OPS
    assert set(llm_decider._MODEL_OPS) == set(ops.OP_BOUNDS) - {"burn_ev"}
    assert "burn_ev" not in json.dumps(llm_decider._edits_tool(["sky"]))


def test_a_batch_stops_with_a_reason_when_the_saved_project_cannot_be_read(tmp_path):
    from autoedit import batch
    from autoedit.web import projects

    photos = tmp_path / "photos"
    photos.mkdir()
    PILImage.fromarray(np.uint8(255 * frame(.3))).save(photos / "one.png")
    profile = tmp_path / "profile"
    saved = Path(projects._project_file(str(photos), str(profile)))
    saved.parent.mkdir(parents=True)
    saved.write_text("{ this is not a project")
    cfg = RunConfig(backend="stub", profile_dir=str(profile))
    with pytest.raises(ValueError, match="saved project for this folder cannot be read"):
        batch.run(cfg, str(photos), str(tmp_path / "out"))
    assert not list((tmp_path / "out").glob("*.jpg"))
    # A plan without pixels never needed the project.
    assert batch.run(cfg, str(photos), str(tmp_path / "plan"), do_apply=False)
