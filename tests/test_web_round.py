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


def vote_file(directory, better, worse, same=0):
    """Panel photo i gets a vote: A calls the candidate worse, B better (A is best in the fixture)."""
    ids = [f"scene-{i}" for i in range(16)]
    picks = ["B"] * better + ["A"] * worse + ["="] * same
    (directory / "verdicts.txt").write_text(
        "".join(f"{s} {p} a reason\n" for s, p in zip(ids, picks))
    )
    return ids


def bar_fixture(root, better, worse, same=0):
    directory = round_fixture(root)
    vote_file(directory, better, worse, same)
    key = directory / "sheets/key.json"
    save(key, {**json.loads(key.read_text()), "rows": {
        f"scene-{i}": {"A": "best", "B": "candidate"} for i in range(better + worse + same)}})
    return directory


def test_bar_passes_at_ten_decided_and_seven_tenths(tmp_path):
    directory = bar_fixture(tmp_path, 7, 3)
    with no_flags():
        answer = web_round.bar(directory)
    assert (answer["better"], answer["worse"], answer["same"], answer["decided"]) == (7, 3, 0, 10)
    assert answer["share"] == 0.7 and answer["clear_losses"] == [] and answer["new_review_marks"] == []
    assert answer["changed"] == 10 and answer["by_shoot"] == {} and answer["passes"]


def test_bar_fails_below_the_edges(tmp_path):
    with no_flags():
        assert not web_round.bar(bar_fixture(tmp_path / "a", 6, 3))["passes"]            # decided 9
        short = web_round.bar(bar_fixture(tmp_path / "b", 11, 5))                         # 11/16 < 0.7
        assert short["decided"] == 16 and short["share"] < 0.7 and not short["passes"]
        assert web_round.bar(bar_fixture(tmp_path / "c", 12, 3, 1))["passes"]            # same does not count
        assert web_round.bar(bar_fixture(tmp_path / "d", 0, 0, 3))["share"] is None
        clear = bar_fixture(tmp_path / "e", 9, 1)
        lines = (clear / "verdicts.txt").read_text().splitlines()
        lines[9] = lines[9].split()[0] + " A CLEAR halo"
        (clear / "verdicts.txt").write_text("\n".join(lines) + "\n")
        answer = web_round.bar(clear)
        assert answer["clear_losses"] == ["scene-9"] and not answer["passes"]


def test_bar_fails_on_a_new_review_mark_only_when_standard_lacks_it(tmp_path):
    directory = bar_fixture(tmp_path, 8, 2)
    new = {"best": set(), "candidate": {("scene-1", "needs_review"), ("scene-2", "dark_made_darker")}}
    with patch.object(web_round, "native_flags", return_value=new):
        answer = web_round.bar(directory)
    assert answer["new_review_marks"] == ["scene-1"] and not answer["passes"]
    both = {"best": {("scene-1", "needs_review")}, "candidate": {("scene-1", "needs_review")}}
    with patch.object(web_round, "native_flags", return_value=both):
        assert web_round.bar(directory)["passes"]


def test_bar_counts_changed_photos_and_groups_by_shoot(tmp_path):
    directory = bar_fixture(tmp_path, 7, 3)
    (directory / "candidate/scene-0.jpg").write_bytes(b"best")      # now the same bytes as Standard
    save(tmp_path / "shoots.json", {f"scene-{i}": f"shoot-{i % 2}" for i in range(16)})
    with no_flags():
        answer = web_round.bar(directory)
    assert answer["changed"] == 9
    assert answer["by_shoot"] == {
        "shoot-0": dict(better=4, worse=1, same=0),   # scene-0,2,4,6 better; 8 worse
        "shoot-1": dict(better=3, worse=2, same=0),
    }


def test_bar_wants_the_style_closer_to_the_reference_when_one_was_measured(tmp_path):
    directory = bar_fixture(tmp_path, 8, 2)
    rows = json.loads((directory / "sheets/key.json").read_text())["rows"]

    def measured(best, candidate, lost=()):
        save(directory / "sheets/distances.json", {
            stem: {"A": {"comparable": True, "cells_ev": best},
                   "B": {"comparable": stem not in lost, "cells_ev": candidate}} for stem in rows})
        with no_flags():
            return web_round.bar(directory)

    with no_flags():
        assert web_round.bar(directory)["median_36_cell_ev"] is None        # no references: not part of the bar
    closer = measured(.5, .4)
    assert closer["median_36_cell_ev"] == {"best": .5, "candidate": .4} and closer["passes"]
    assert not measured(.5, .5)["passes"]                                    # as far as Standard is not closer
    assert not measured(.4, .5)["passes"]
    lost = measured(.5, .4, lost=("scene-0",))
    assert lost["candidate_not_comparable"] == ["scene-0"] and not lost["passes"]
    # Only four photos came with a reference: the distance is reported, not part of the bar.
    few = {stem: {"A": {"comparable": True, "cells_ev": .4}, "B": {"comparable": True, "cells_ev": .5}}
           for stem in list(rows)[:4]}
    save(directory / "sheets/distances.json", few)
    with no_flags():
        thin = web_round.bar(directory)
    assert thin["reference_pairs"] == 4 and thin["passes"]
    # Every photo came with a reference and none can be compared: nothing was measured, so no pass.
    save(directory / "sheets/distances.json", {
        stem: {"A": {"comparable": False}, "B": {"comparable": False}} for stem in rows})
    with no_flags():
        blank = web_round.bar(directory)
    assert blank["median_36_cell_ev"] is None and blank["reference_pairs"] == 0 and not blank["passes"]
    save(directory / "sheets/distances.json", {"somebody-else": few["scene-0"]})
    with no_flags(), pytest.raises(ValueError, match="not judged"):
        web_round.bar(directory)


def test_bar_and_score_tell_a_waiting_control_and_a_fallback_backend(tmp_path):
    directory = bar_fixture(tmp_path, 8, 2)
    sidecar = directory / "candidate" / "scene-0.autoedit.json"
    save(sidecar, {"audit": {"panel_segmentation": {"requested": "sam3", "actual": "stub"}}})
    with no_flags(), pytest.raises(ValueError, match="actual SAM segmentation"):
        web_round.bar(directory)
    other = round_fixture(tmp_path / "waiting")
    split = json.loads((tmp_path / "waiting" / "split.json").read_text())
    split.pop("control")              # round two's control has not joined the split yet
    save(tmp_path / "waiting" / "split.json", split)
    with no_flags(), pytest.raises(ValueError, match="sixteen distinct"):
        web_round.score(other)


def test_bar_refuses_votes_that_do_not_cover_the_key(tmp_path):
    directory = bar_fixture(tmp_path, 7, 3)
    (directory / "verdicts.txt").write_text("scene-0 B ok\n")
    with no_flags(), pytest.raises(ValueError, match="every photo"):
        web_round.bar(directory)


def test_score_on_the_fixture_is_unchanged(tmp_path):
    directory = round_fixture(tmp_path)
    with no_flags():
        result = web_round.score(directory)
    assert result["counts"] == {"Better": 16} and result["clearly_worse"] == []
    assert len(result["changed_controls"]) == 16      # the fixture's two arms differ on every control
    assert result["reference_pairs"] == 2 and result["median_36_cell_ev"] == {"best": 0.2, "candidate": 0.3}
    assert not result["keep"]


def test_a_per_kind_pairs_file_replaces_the_evaluation_list(tmp_path):
    directory = round_fixture(tmp_path)
    panel = {f"scene-{i}" for i in range(16)}
    assert web_round.frozen_inputs(directory, panel)[1] == {"expert-1", "expert-2"}
    (tmp_path / "pairs-blue_hour.txt").write_text("own-1.jpg\tref-1.jpg\n")
    assert web_round.frozen_inputs(directory, panel)[1] == {"own-1"}


def gallery_fixture(root, lines, rows):
    """A held-back list of round two: the list, split.json, a manifest and a key beside the sheets."""
    ids = [Path(line.split("\t")[0]).stem for line in lines]
    (root / "list.txt").write_text("\n".join(lines) + "\n")
    save(root / "split.json", {"blue_hour": {"panel": ["p"], "holdout": ids}})
    manifest = root / "manifest.csv"
    manifest.write_text("id,source,role,track,kind,file,author,sha256\n" + "".join(
        ",".join(r) + "\n" for r in rows))
    sheets = root / "sheets"
    save(sheets / "key.json", {"rows": {i: {"A": "best", "B": "candidate"} for i in ids},
                               "renders": {r: str(root / r) for r in web_round.ROLES}})
    return sheets, manifest


def test_gallery_accepts_a_round_two_list(tmp_path):
    fivek = tmp_path / "originals/fivek-1.jpg"
    mine = tmp_path / "owner/originals/blue_hour/mine.jpg"
    for path in (fivek, mine):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pixels")
    sheets, manifest = gallery_fixture(
        tmp_path,
        [f"{fivek}\t{tmp_path}/reference-1.jpg", str(mine)],
        [("fivek-1", "fivek", "before", "L", "blue_hour", "originals/fivek-1.jpg", "someone", "abc")],
    )
    save(tmp_path / "shoots.json", {"fivek-1": "shoot-a", "mine": "shoot-b"})
    with patch.object(web_round, "render_comparison", side_effect=lambda *a, **k: "ok"):
        assert web_round.gallery(sheets, tmp_path / "list.txt", manifest, tmp_path / "out", 1) == "ok"
    entries = json.loads((sheets / "gallery-sources.json").read_text())["entries"]
    by_id = {e["capture_id"]: e for e in entries}
    assert by_id["fivek-1"]["group"] == "shoot-a" and by_id["fivek-1"]["source_sha256"] == "abc"
    assert by_id["mine"]["group"] == "shoot-b" and by_id["mine"]["scene"] == "blue_hour"
    assert by_id["mine"]["source_sha256"] == web_round.checksum(mine)
    # Anything else without a manifest row is refused.
    stray = tmp_path / "elsewhere/stray.jpg"
    stray.parent.mkdir()
    stray.write_bytes(b"x")
    sheets, manifest = gallery_fixture(tmp_path, [str(stray)], [])
    with pytest.raises(ValueError, match="licensed held-back"):
        web_round.gallery(sheets, tmp_path / "list.txt", manifest, tmp_path / "out", 1)


def standard_fixture(root, rows=None, candidate=None, best=None, mine="", theirs="", style="natural", local=None):
    """A round where every gate passes, plus a vs-standard key whose best arm is Standard.
    Photo scene-3 carries the review reasons: `mine` on the candidate's sidecar, `theirs` on Standard's."""
    directory = round_fixture(root)
    panel = [f"scene-{i}" for i in range(16)]
    dist = json.loads((directory / "pairs/distances.json").read_text())
    for v in dist.values():
        v["A"]["cells_ev"], v["B"]["cells_ev"] = 0.3, 0.2
    save(directory / "pairs/distances.json", dist)
    for i in range(16):
        (directory / "candidate" / f"day-{i}.jpg").write_bytes(b"best")
    key = json.loads((directory / "sheets/key.json").read_text())
    ids = panel if rows is None else rows
    save(directory / "vs-standard/key.json", {
        "best": best or key["best"],
        "candidate": candidate or key["candidate"],
        "rows": {i: {"A": "best", "B": "candidate"} for i in ids},
        "renders": {"best": str(directory / "standard"), "candidate": str(directory / "candidate")},
    })
    seg = {"panel_segmentation": {"requested": "sam3", "actual": "sam3"}}
    for i in panel:
        save(directory / "standard" / f"{i}.autoedit.json", {
            "style": style, "review_reason": theirs if i == "scene-3" else "",
            "audit": {**seg, **({"local_style": local} if local else {})}})
        save(directory / "candidate" / f"{i}.autoedit.json", {
            "review_reason": mine if i == "scene-3" else "", "audit": seg})
    return directory


def judged(new, standard_marks=()):
    """native_flags stand-in: the style panel's own arms, or Standard's arm of the vs-standard key."""
    def flags(key):
        if key["renders"]["best"].endswith("standard"):
            return {"best": {(s, "needs_review") for s in standard_marks}, "candidate": set()}
        return {"best": set(), "candidate": set(new)}
    return patch.object(web_round, "native_flags", side_effect=flags)


NEW = {("scene-3", "needs_review")}


def test_a_review_mark_standard_gives_too_is_not_new(tmp_path):
    directory = standard_fixture(tmp_path / "none", mine="shadow clipping 24.7%")
    (directory / "vs-standard/key.json").unlink()
    with judged(NEW):
        result = web_round.score(directory)
    assert not result["gates"]["native"] and not result["keep"]
    assert "review_marks_standard_gives_too" not in result
    # The same kinds with other numbers: excused.
    directory = standard_fixture(
        tmp_path / "same", mine="shadow clipping 24.7%",
        theirs="shadow clipping 18.0%; oversaturation gate worked hard (42% of frame)")
    with judged(NEW, ["scene-3"]):
        result = web_round.score(directory)
    assert result["gates"]["native"] and result["keep"] and result["new_native_flags"] == []
    assert result["review_marks_standard_gives_too"] == ["scene-3"]
    # Standard does not mark that photo.
    directory = standard_fixture(tmp_path / "lacks", mine="shadow clipping 24.7%")
    with judged(NEW, ["scene-4"]):
        result = web_round.score(directory)
    assert not result["gates"]["native"] and result["review_marks_standard_gives_too"] == []
    # A kind Standard lacks, or no reason at all: not excused.
    for name, mine in (("extra", "shadow clipping 24.7%; highlight clipping 6.0%"), ("empty", "")):
        directory = standard_fixture(tmp_path / name, mine=mine, theirs="shadow clipping 24.7%")
        with judged(NEW, ["scene-3"]):
            result = web_round.score(directory)
        assert not result["gates"]["native"] and result["review_marks_standard_gives_too"] == []


def test_standard_never_excuses_the_other_native_flags(tmp_path):
    new = {("scene-3", "needs_review"), ("scene-3", "shadow_pushed_to_clip")}
    directory = standard_fixture(tmp_path, mine="shadow clipping 24.7%", theirs="shadow clipping 24.7%")
    with judged(new, ["scene-3"]):
        result = web_round.score(directory)
    assert result["new_native_flags"] == [("scene-3", "shadow_pushed_to_clip")]
    assert not result["gates"]["native"]


def test_the_standard_comparison_must_match_the_judged_panel(tmp_path):
    with judged(set()), pytest.raises(ValueError, match="did not cover"):
        web_round.score(standard_fixture(tmp_path / "a", rows=["scene-0"]))
    with judged(set()), pytest.raises(ValueError, match="another candidate"):
        web_round.score(standard_fixture(tmp_path / "b", candidate=str(tmp_path / "other")))
    with judged(set()), pytest.raises(ValueError, match="another config folder"):
        web_round.score(standard_fixture(tmp_path / "c", best=str(tmp_path / "elsewhere")))
    for name, extra in (("d", dict(style="dramatic")), ("e", dict(local={"name": "blue_hour"}))):
        with judged(set()), pytest.raises(ValueError, match="not rendered under Standard"):
            web_round.score(standard_fixture(tmp_path / name, **extra))


def test_review_kinds_drop_numbers_and_ignore_order():
    kinds = web_round.review_kinds("shadow clipping 24.7%; oversaturation gate worked hard (37% of frame)")
    assert kinds == {"shadow clipping #%", "oversaturation gate worked hard (#% of frame)"}
    assert web_round.review_kinds("b -3;  a +1.5 ") == web_round.review_kinds("a 2;b 9")
    assert web_round.review_kinds("") == web_round.review_kinds(None) == set()
