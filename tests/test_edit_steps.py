"""Saved changes compose once across ordinary editor, export and batch entry points."""
import copy
import json
import os
import time

import numpy as np
import pytest
from PIL import Image

from autoedit.web import edit_steps, jobs, projects, service


def change(**fields):
    return {"id": "sky-1", "label": "Recover sky", "origin": "suggested", "on": True,
            "amount": 1, "global": {"exposure": .4}, "regions": {"sky": {"highlights": -8}},
            **fields}


def test_check_rejects_invalid_and_duplicate_entries():
    table, _ = service.editor_sliders()
    good = change(note="Retain detail", ref="local-test")
    assert edit_steps.check([good], table) == [good]
    bad = [[good] * 25, [good, good], None, {},
           *([change(**{key: val})] for key, val in (
               ("id", "a/b"), ("label", " "), ("origin", ["recipe"]), ("on", 1),
               ("amount", True), ("amount", float("nan")), ("amount", float("inf")),
               ("global", {"exposure": 4}), ("global", {"unknown": 1}),
               ("regions", {"__proto__": {}}), ("regions", {"cat": {"temp": True}}),
               ("note", "x" * 241), ("ref", "x" * 101), ("url", "unexpected")))]
    for value in bad:
        with pytest.raises(ValueError):
            edit_steps.check(value, table)


def test_fold_adds_all_before_clamping_and_preserves_inputs():
    table, _ = service.editor_sliders()
    steps = [change(amount=2, regions={"sky": {"highlights": -60}}),
             change(id="sky-2", **{"global": {"exposure": -.4}}),
             change(id="off", on=False, regions={"train": {"temp": 4}})]
    opts = {"photo_settings": {"exposure": 1.5}, "region_settings": {"skin": {}}, "edit_steps": steps}
    original = copy.deepcopy(opts)
    totals, report = edit_steps.fold(opts, table)
    assert totals == {"photo_settings": {"exposure": 1.9}, "region_settings": {"sky": {"highlights": -100}, "skin": {}}}
    assert report == [{"target": "sky", "op": "highlights", "requested": -128, "value": -100}]
    assert opts == original
    cancellation = [change(), change(id="reverse", **{"global": {"exposure": -.4}, "regions": {"sky": {"highlights": 8}}})]
    assert edit_steps.fold({"edit_steps": cancellation}, table)[0] == {"photo_settings": {}, "region_settings": {}}


def test_steps_match_typed_totals_across_render_paths(tmp_path):
    path = tmp_path / "input.png"
    ramp = np.tile(np.linspace(24, 224, 96, dtype=np.uint8), (72, 1))
    Image.fromarray(np.stack([ramp, ramp, ramp], -1)).save(path)
    service.approve_root(str(tmp_path))
    base = {"backend": "stub", "concepts": ["sky"], "out_format": "png", "consistent": False, "compose": False}
    entries = [change(), change(id="disabled", label="Disabled", on=False)]
    raw = {**base, "photo_settings": {"exposure": -.1}, "edit_steps": entries}
    typed = {**base, "photo_settings": {"exposure": .3}, "region_settings": {"sky": {"highlights": -8}}}

    p = service.preview(str(path), raw)
    assert p["after"] == service.preview(str(path), typed)["after"]
    assert p["editor"]["totals"] == {"global": typed["photo_settings"], "regions": typed["region_settings"]}
    slider = next(v for v in p["editor"]["sliders"] if v["op"] == "exposure")
    assert slider["value"] == -.1 and not p["editor"]["clamped"]
    assert service.detail(str(path), raw) == service.detail(str(path), typed)
    spec = [{"name": "Natural", "look": "natural"}]
    assert (service.make_variants(str(path), {**raw, "variants": spec})["variants"]
            == service.make_variants(str(path), {**typed, "variants": spec})["variants"])
    saved = service.run_one(str(path), str(tmp_path / "steps"), raw)
    flat = service.run_one(str(path), str(tmp_path / "typed"), typed)
    assert (tmp_path / "steps/input.png").read_bytes() == (tmp_path / "typed/input.png").read_bytes()
    assert saved["result"]["adjustments"] == flat["result"]["adjustments"]
    notes = json.loads((tmp_path / "steps/input.autoedit.json").read_text())["notes"]
    assert any("Recover sky" in n and "global.exposure +0.4" in n and "sky.highlights -8" in n for n in notes)
    assert not any("Disabled" in n for n in notes)

    # A folder change is evaluated against each photo's own manual offset.
    other = tmp_path / "other.png"
    other.write_bytes(path.read_bytes())
    job = jobs.start(str(tmp_path), [str(path), str(other)], str(tmp_path / "batch"),
                     {**base, "edit_steps": entries, "photo_settings": {"exposure": 1}},
                     photo_options={str(path): {"photo_settings": {"exposure": -.1}}, str(other): {"edit_steps": []}})
    deadline = time.monotonic() + 30
    while job.status not in ("done", "error", "cancelled") and time.monotonic() < deadline:
        time.sleep(.02)
    assert job.status == "done" and all(item.status != "failed" for item in job.items)
    assert (tmp_path / "steps/input.png").read_bytes() == (tmp_path / "batch/input.png").read_bytes()
    batch_notes = json.loads((tmp_path / "batch/input.autoedit.json").read_text())["notes"]
    assert notes == batch_notes
    cleared = json.loads((tmp_path / "batch/other.autoedit.json").read_text())
    assert not any("Recover sky" in n for n in cleared["notes"])
    assert [(adj["target"], adj["ops"]) for adj in cleared["adjustments"]] == [("global", {"exposure": 1})]


def test_empty_and_disabled_preserve_existing_pixels_and_reopen(tmp_path):
    service.approve_root(str(tmp_path))
    photo = str(tmp_path / "source.png")
    Image.new("RGB", (64, 48), (32, 98, 164)).save(photo)
    opts = {"backend": "stub", "concepts": [], "compose": False, "photo_settings": {"temp": 5}}
    expected = service.preview(photo, opts)
    inactive = [change(on=False)]
    for steps in ([], inactive):
        got = service.preview(photo, {**opts, "edit_steps": steps})
        assert got["after"] == expected["after"]
        assert got["result"]["adjustments"] == expected["result"]["adjustments"]
    project = {"version": 1, "photos": {photo: {"edit_steps": inactive, "history": [{"edit_steps": [change()]}]}}}
    projects.save(str(tmp_path), project, str(tmp_path / "profile"))
    assert projects.load(str(tmp_path), str(tmp_path / "profile")) == project


def test_thousand_photo_changes_fit_project_budget(tmp_path):
    steps = [change(note="Open the shadows in the sky"), change(id="warm-2", label="Warm the foreground")]
    snapshot = {"sliders": {"exposure": -.1}, "edit_steps": steps}
    photo = {**snapshot, "history": [{}, {"edit_steps": steps[:1]}, snapshot]}
    project = {"version": 1, "photos": {os.path.join(str(tmp_path), f"{i}.jpg"): photo for i in range(1000)}}
    assert len(projects.check_project(str(tmp_path), project)) < 4 * 1024 * 1024


def test_one_planned_recipe_only_restores_an_unplanned_matching_entry():
    table, _ = service.editor_sliders()
    reference = "a" * 32
    manual = change(id="recipe-" + reference, origin="recipe", ref=reference)
    plan = {"prior": {"look": ""}, "applied": {"intensity": .7}, "previous": manual, "missing": ["car"]}
    entry = {**manual, "plan": plan}
    assert edit_steps.check([entry], table) == [entry]
    invalid = [
        [change(plan=plan)], [{**entry, "ref": "b" * 32}],
        [entry, {**entry, "ref": "b" * 32, "id": "recipe-" + "b" * 32, "plan": {"prior": {}, "applied": {}}}],
    ]
    for patch in ({"prior": None}, {"prior": {"path": "x"}}, {"applied": {"intensity": True}},
                  {"previous": entry}, {"previous": {**manual, "origin": "suggested"}},
                  {"previous": {**manual, "amount": -1}}, {"missing": ["sky", "sky"]},
                  {"missing": ["global"]}, {"missing": None}):
        invalid.append([{**entry, "plan": {**plan, **patch}}])
    for value in invalid:
        with pytest.raises(ValueError):
            edit_steps.check(value, table)
    # Neither target discovery nor portable recipe settings include the displaced edit.
    folded, _ = edit_steps.fold({"edit_steps": [entry]}, table)
    assert folded == {"photo_settings": {"exposure": .4}, "region_settings": {"sky": {"highlights": -8}}}


def test_combined_base_and_active_regions_fit_before_project_write(tmp_path):
    path = str(tmp_path / "pic.png")
    base = {"selected_regions": [f"object {i}" for i in range(32)]}
    photo = {"base": base, "edit_steps": [change(regions={"new object": {"saturation": 1}})]}
    with pytest.raises(ValueError, match="32"):
        projects.check_project(str(tmp_path), {"version": 1, "photos": {path: photo}})
    photo["edit_steps"][0]["on"] = False
    projects.check_project(str(tmp_path), {"version": 1, "photos": {path: photo}})


def test_assistant_receives_folded_numbers_without_prior_change_text(tmp_path):
    from unittest.mock import patch
    from autoedit.web import assist

    path = tmp_path / "private-filename.png"
    Image.new("RGB", (64, 48), (108, 128, 144)).save(path)
    service.approve_root(str(tmp_path))
    entries = [change(label="Private change title", note="Private reason", ref="private-reference")]
    opts = {"backend": "stub", "concepts": ["sky"], "allow_cloud": True, "gemini_api_key": "test-key",
            "assist_prompt": "Soften the sky", "photo_settings": {"exposure": -.1}, "edit_steps": entries}
    with patch.object(assist._CLIENT, "suggest", return_value={"status": "ok"}) as provider:
        assist.suggest(str(path), opts)
    payload = provider.call_args.kwargs
    assert payload["context"]["current_controls"] == {"exposure": .3}
    assert payload["context"]["regions"]["sky"]["current_controls"] == {"highlights": -8}
    assert b"private" not in payload["thumbnail"].lower()
    assert "private" not in json.dumps(payload["context"]).lower()
    assert not {"edit_steps", "file", "opts"} & payload.keys()


def test_batch_reports_missing_object_from_actual_render(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (64, 48), (120, 130, 150)).save(path)
    entries = [change(regions={"red kite": {"exposure": .2}}, origin="recipe")]
    job = jobs.start(str(tmp_path), [str(path)], str(tmp_path / "out"),
                     {"backend": "stub", "consistent": False}, photo_options={str(path): {"edit_steps": entries}})
    deadline = time.monotonic() + 30
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(.02)
    item = job.items[0]
    assert item.status == "review" and "red kite" in item.warn
    snapshot = jobs.snapshot(job)["items"][0]
    assert snapshot["object_checks"] == {"red kite": False}
    assert "red kite" not in snapshot["quality_warn"]
    assert (tmp_path / "out" / "photo.autoedit.json").is_file()
