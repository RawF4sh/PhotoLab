from autoedit import instructions
from autoedit.contract import Adjustment
from test_instructions import _vocab, _resolved


def test_global_control_limits_include_automatic_baseline():
    edits = [Adjustment(target="global", ops={"highlights": -100, "shadows": 100, "exposure": 2}, scope="photo")]
    baseline = {"highlights": -8.0, "shadows": 6.4, "exposure": 1.4}
    plan = instructions.plan(edits, _vocab(), [], [], _resolved(), {}, baseline, {})
    assert baseline == {"highlights": -100, "shadows": 100, "exposure": 3}
    assert plan.limited == {("global", "highlights"), ("global", "shadows"), ("global", "exposure")}
    instructions.report(edits, plan, {})
    assert edits[0].status == "clamped"


def test_small_offsets_keep_their_requested_values():
    edits = [Adjustment(target="global", ops={"exposure": .2, "temp": 5}, scope="photo")]
    baseline = {"exposure": .3, "temp": -2}
    plan = instructions.plan(edits, _vocab(), [], [], _resolved(), {}, baseline, {})
    assert baseline == {"exposure": .5, "temp": 3}
    assert not plan.limited
