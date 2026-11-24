"""A style has to clear complete, frozen control, tone, and expert reference panels."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    "web_round", Path(__file__).resolve().parents[1] / "scripts/web_round.py"
)
web_round = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_round)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def round_fixture(root):
    directory = root / "iter-01"
    panel = [f"scene-{i}" for i in range(16)]
    controls = [f"day-{i}" for i in range(16)]
    pairs = ["expert-1", "expert-2"]
    save(
        root / "split.json",
        {"blue_hour": {"panel": panel}, "control": {"panel": controls}},
    )
    (root / "pairs-evaluation.txt").write_text(
        "expert-1.dng\tedited-1.tif\nexpert-2.dng\tedited-2.tif\n"
    )
    tone = {
        "rows": [
            dict(
                name=f"{name}.jpg",
                safety_fallback=1.0,
                audit_passed=True,
                requested_backend="sam3",
                actual_backend="sam3",
                backend_fallback=None,
                **dict.fromkeys(web_round.NATIVE_FLAGS, False),
            )
            for name in panel
        ]
    }
    for role in web_round.ROLES:
        save(directory / f"tone-{role}.json", tone)
    for name, ids in (("sheets", panel), ("controls", controls), ("pairs", pairs)):
        key = {   # as contact_sheet.py writes it: the two config folders, then where each arm was rendered
            "best": str(directory / "baseline"),
            "candidate": str(directory / "configs"),
            "rows": {i: {"A": "best", "B": "candidate"} for i in ids},
            "renders": {role: str(directory / role) for role in web_round.ROLES},
        }
        save(directory / name / "key.json", key)
        for role in web_round.ROLES:
            for ident in ids:
                save(
                    directory / role / f"{ident}.autoedit.json",
                    {
                        "audit": {
                            "panel_segmentation": {
                                "requested": "sam3",
                                "actual": "sam3",
                            }
                        }
                    },
                )
                (directory / role / f"{ident}.jpg").write_bytes(role.encode())
    save(
        directory / "pairs/distances.json",
        {
            s: {
                "A": {"comparable": True, "cells_ev": 0.2},
                "B": {"comparable": True, "cells_ev": 0.3},
            }
            for s in pairs
        },
    )
    (directory / "verdicts.txt").write_text(
        "".join(f"{s} B More readable shadows\n" for s in panel)
    )
    return directory


def test_visual_votes_do_not_override_bad_control_or_reference(tmp_path):
    directory = round_fixture(tmp_path)
    with patch.object(
        web_round, "native_flags", return_value={"best": set(), "candidate": set()}
    ):
        result = web_round.score(directory)
    assert result["counts"] == {"Better": 16}
    assert result["gates"]["blind_preference"]
    assert (
        not result["gates"]["expert_distance"]
        and not result["gates"]["standard"]
        and not result["keep"]
    )


def test_refuse_missing_controls_pairs_or_backend_fallback(tmp_path):
    directory = round_fixture(tmp_path)
    with patch.object(
        web_round, "native_flags", return_value={"best": set(), "candidate": set()}
    ):
        for group, label in [
            ("controls", "Standard controls"),
            ("pairs", "expert pairs"),
        ]:
            key = directory / group / "key.json"
            original = key.read_text()
            partial = json.loads(original)
            partial["rows"].pop(next(iter(partial["rows"])))
            save(key, partial)
            with pytest.raises(ValueError, match=label):
                web_round.score(directory)
            key.write_text(original)
        file = directory / "tone-candidate.json"
        doc = json.loads(file.read_text())
        doc["rows"][0]["backend_fallback"] = "compiled backend unavailable"
        save(file, doc)
        with pytest.raises(ValueError, match="lacked real SAM"):
            web_round.score(directory)
        save(
            directory / "candidate/scene-0.autoedit.json",
            {"audit": {"panel_segmentation": {"requested": "sam3", "actual": "stub"}}},
        )
        with pytest.raises(ValueError, match="requires actual SAM"):
            web_round.score(directory)


def test_duplicate_and_missing_votes_never_unmask_a_partial_round(tmp_path):
    (tmp_path / "verdicts.txt").write_text(
        "same A lighter shadow\nsame B reduced glare\n"
    )
    with pytest.raises(ValueError, match="unique"):
        web_round.score(tmp_path)
    (tmp_path / "verdicts.txt").write_text("")
    with pytest.raises(ValueError, match="required before"):
        web_round.score(tmp_path)


def no_flags():
    return patch.object(
        web_round, "native_flags", return_value={"best": set(), "candidate": set()}
    )


def test_inputs_of_the_keep_rule_must_belong_together(tmp_path):
    directory = round_fixture(tmp_path)
    with no_flags():
        web_round.score(directory)      # the three keys of one round name the same two folders
        key = directory / "controls/key.json"
        original = json.loads(key.read_text())
        save(key, {**original, "candidate": str(tmp_path / "another-round" / "configs")})
        with pytest.raises(ValueError, match="controls key was built from other config folders"):
            web_round.score(directory)
        # One folder against itself: the two arms are trivially identical, which proves nothing.
        save(key, {**original, "candidate": original["best"]})
        with pytest.raises(ValueError, match="controls key compares a config folder with itself"):
            web_round.score(directory)
        save(key, original)
        pairs = directory / "pairs/key.json"
        save(pairs, {**json.loads(pairs.read_text()), "best": str(tmp_path / "elsewhere")})
        with pytest.raises(ValueError, match="pairs key was built from other config folders"):
            web_round.score(directory)


def test_uncomparable_candidate_side_blocks_keep(tmp_path):
    directory = round_fixture(tmp_path)
    dist = json.loads((directory / "pairs/distances.json").read_text())
    for v in dist.values():
        v["A"]["cells_ev"], v["B"]["cells_ev"] = 0.3, 0.2
    dist["expert-2"]["B"]["comparable"] = False
    save(directory / "pairs/distances.json", dist)
    with no_flags():
        result = web_round.score(directory)
    assert not result["gates"]["candidate_comparable"]
    assert "expert-2" in result["reasons"][0]
    assert not result["keep"]


def test_clearly_worse_in_any_case_counts(tmp_path):
    directory = round_fixture(tmp_path)
    lines = (directory / "verdicts.txt").read_text().splitlines()
    ident = lines[0].split()[0]
    (directory / "verdicts.txt").write_text(
        f"{ident} A Clearly worse: halo\n" + "\n".join(lines[1:]) + "\n"
    )
    with no_flags():
        assert web_round.score(directory)["clearly_worse"] == [ident]
        (directory / "verdicts.txt").write_text(
            f"{ident} A CLEAR halo\n" + "\n".join(lines[1:]) + "\n"
        )
        assert web_round.score(directory)["clearly_worse"] == [ident]


def test_a_reason_that_denies_a_clear_loss_is_not_one(tmp_path):
    directory = round_fixture(tmp_path)
    verdicts = directory / "verdicts.txt"
    lines = verdicts.read_text().splitlines()
    # Side A is "best" in the fixture, so a vote for A calls the candidate worse.
    lines[0] = lines[0].split()[0] + " A Slightly flatter, not clearly worse"
    lines[1] = lines[1].split()[0] + " A Worse, clearly: a halo on the ridge"
    verdicts.write_text("\n".join(lines) + "\n")
    with no_flags():
        result = web_round.score(directory)
    assert result["clearly_worse"] == [lines[1].split()[0]]
