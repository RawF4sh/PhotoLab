"""A durable workspace follows the folder when opened through another path spelling."""

import json

from autoedit.web import projects, service


def _workspace(tmp_path):
    real = tmp_path / "photos"
    real.mkdir()
    (real / "nested").mkdir()
    alias = tmp_path / "shortcut"
    alias.symlink_to(real, target_is_directory=True)
    profile = tmp_path / "profile"
    profile.mkdir()
    service.approve_root(str(real))
    return real, alias, profile


def _data(folder, output):
    path = str(folder / "nested" / "a.jpg")
    return {"version": 1, "folder": {"look": "natural"},
            "photos": {path: {"reference": str(output), "sliders": {"exposure": .2},
                              "history": [{"sliders": {}}], "future": []}},
            "files": {path: {"checked": False, "status": "finished", "dirty": True,
                             "out": str(output)}}, "last_index": 0}


def test_project_saved_via_alias_reopens_real_path(tmp_path):
    real, alias, profile = _workspace(tmp_path)
    output = alias / "exports" / "a.jpg"
    original = _data(alias, output)
    projects.save(str(alias), original, str(profile))
    restored = projects.load(str(real), str(profile))
    assert restored == _data(real, output)
    # Only map keys are canonicalized, not user choices, exports, or the caller's object.
    assert str(alias / "nested" / "a.jpg") in original["photos"]
    stored = json.loads(next((profile / "projects").glob("*.json")).read_text())
    assert str((real / "nested" / "a.jpg").resolve()) in stored["photos"]
    assert stored["files"][str((real / "nested" / "a.jpg").resolve())]["out"] == str(output)


def test_project_saved_real_reopens_alias_path(tmp_path):
    real, alias, profile = _workspace(tmp_path)
    output = real / "exports" / "a.jpg"
    projects.save(str(real), _data(real, output), str(profile))
    assert projects.load(str(alias), str(profile)) == _data(alias, output)
    assert projects.load(str(alias / "."), str(profile)) == _data(alias, output)


def test_project_old_alias_keys_still_restore(tmp_path):
    real, alias, profile = _workspace(tmp_path)
    output = alias / "exports" / "a.jpg"
    # Simulate a pre-canonicalization project file containing the original alias keys.
    projects.save(str(real), _data(real, output), str(profile))
    file = next((profile / "projects").glob("*.json"))
    file.write_text(json.dumps(_data(alias, output)))
    assert projects.load(str(real), str(profile)) == _data(real, output)
    assert projects.load(str(alias), str(profile)) == _data(alias, output)


def test_project_rejects_duplicate_aliases_without_replacing_saved_data(tmp_path):
    real, alias, profile = _workspace(tmp_path)
    output = real / "exports" / "a.jpg"
    original = _data(real, output)
    projects.save(str(real), original, str(profile))
    duplicate = _data(real, output)
    duplicate["photos"][str(alias / "nested" / "a.jpg")] = {"sliders": {"exposure": .4}}
    try:
        projects.save(str(real), duplicate, str(profile))
    except ValueError as error:
        assert "same photo" in str(error)
    else:
        raise AssertionError("ambiguous photo aliases were accepted")
    assert projects.load(str(real), str(profile)) == original
