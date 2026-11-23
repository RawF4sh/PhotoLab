"""Selective sync changes only the chosen photos and adjustment categories."""
import pytest

from autoedit import adjust, sync
from autoedit.contract import Adjustment, Result
from test_sync import _run, _sc, _bytes


def test_selected_recipient_and_categories_preserve_other_edits(tmp_path):
    out = _run(tmp_path)
    adjust.rerender(str(out / "f1.jpg"), sets=["global.exposure=0.2", "global.saturation=8", "global.temp=5"])
    mark = Adjustment(target="mark:area:0.1000,0.1000,0.4000,0.4000",
                      ops={"exposure": 0.1}, scope="photo")
    adjust.rerender(str(out / "f0.jpg"), sets=["global.exposure=0.1", "global.saturation=-6"],
                    adjustments=[mark], exposure_bias=0.12, wb=[2.0, -1.0])
    before = _bytes(out)
    report = sync.run(str(out / "f1.jpg"), targets=[_sc(out, "f0")], categories=["tone"], match_exposure=False)
    assert len(report["targets"]) == 1 and report["targets"][0]["status"] == "synced"
    after = _bytes(out)
    assert before["f2.jpg"] == after["f2.jpg"]
    assert before["f2.autoedit.json"] == after["f2.autoedit.json"]
    result = Result.model_validate_json((out / "f0.autoedit.json").read_text())
    own = [a for a in result.adjustments if a.scope == "photo"]
    global_ops = {k: v for a in own if a.target == "global" for k, v in a.ops.items()}
    assert global_ops == {"saturation": -6.0, "exposure": 0.2}
    assert any(a.target.startswith("mark:") for a in own)
    assert result.exposure_bias == 0.12
    assert result.audit["run_config"]["wb"] == [2.0, -1.0]
    assert "matching off" in "\n".join(sync.lines(report))


def test_sync_selection_validates_before_writing(tmp_path):
    out = _run(tmp_path)
    before = _bytes(out)
    for kw in ({"targets": [str(tmp_path / "outside.autoedit.json")]},
               {"targets": "all"}, {"categories": ["unknown"]},
               {"categories": "tone"}, {"match_exposure": "yes"}):
        with pytest.raises(ValueError):
            sync.run(str(out / "f1.jpg"), **kw)
    assert _bytes(out) == before
    assert sync.run(str(out / "f1.jpg"), targets=[])["targets"] == []
    assert _bytes(out) == before


def test_sync_dry_run_exposes_named_candidates_and_filter(tmp_path):
    out = _run(tmp_path)
    before = _bytes(out)
    report = sync.run(str(out / "f1.jpg"), dry_run=True, targets=[_sc(out, "f2")], categories=["color"])
    assert report["targets"] == [{"sidecar": _sc(out, "f2"), "image": "f2.jpg", "status": "would sync"}]
    assert report["categories"] == ["color"]
    assert _bytes(out) == before
