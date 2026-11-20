"""F39: masks that reach the frame edge (opt-in snap_edges)."""
import numpy as np

from autoedit.edit.mask import snap_to_edges
from autoedit.segment.base import RegionMask


def test_snap_fills_gap_in_its_columns_only_and_leaves_input():
    m = np.zeros((100, 100), bool)
    m[3:10, 40:50] = True
    before = m.copy()
    out = snap_to_edges(m, 12)
    assert (m == before).all() and out is not m
    assert out[0:10, 40:50].all() and out.sum() == 10 * 10
    far = np.zeros((100, 100), bool)
    far[20:30, 40:50] = True
    assert (snap_to_edges(far, 12) == far).all()
    assert (snap_to_edges(m, 0) == m).all()


def test_all_edges_and_corner():
    for sl in ((slice(5, 8), slice(10, 14)), (slice(32, 35), slice(10, 14)),
               (slice(10, 14), slice(4, 7)), (slice(10, 14), slice(33, 36))):
        m = np.zeros((40, 40), bool)
        m[sl] = True
        out = snap_to_edges(m, 12)
        assert out.sum() > m.sum()
    m = np.zeros((40, 40), bool)
    m[3:6, 3:6] = True
    out = snap_to_edges(m, 12)
    assert out[0:6, 3:6].all() and out[3:6, 0:6].all()


def _rect(r0, r1, c0, c1, n=128):
    m = np.zeros((n, n), bool)
    m[r0:r1, c0:c1] = True
    return m


def _snap_run(tmp_path, snap):
    from PIL import Image
    from autoedit.contract import Adjustment, RunConfig
    from autoedit.pipeline import Pipeline

    masks = {"sky": _rect(3, 60, 0, 128), "lake": _rect(70, 125, 0, 128)}   # stop 3 px from the top and 3 px from the bottom
    class Seg:
        name, open_vocabulary = "fake", True
        def segment(self, img, words):
            return [RegionMask(name=w, mask=masks[w], score=0.9, coverage=float(masks[w].mean())) for w in words if w in masks]
    path = tmp_path / "n.png"
    Image.fromarray(np.random.default_rng(3).integers(60, 190, (128, 128, 3), dtype=np.uint8)).save(path)
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=["sky"], snap_edges=snap))
    pipe.segmenter = Seg()
    seen = {}
    adj = Adjustment(target="lake", ops={"exposure": 0.4}, source="x", scope="photo")
    pipe.process_one(str(path), adjustments=[adj], on_resolved=lambda r: seen.update(res=r),
                     on_plan=lambda _a, plan, _b, _s: seen.update(plan=plan))
    return seen, masks


def test_pipeline_snaps_style_and_instruction_masks_once(tmp_path):
    seen, masks = _snap_run(tmp_path, True)
    res, plan = seen["res"], seen["plan"]
    assert res.union["sky"][0].all() and res.regions[0].mask[0].all() and (res.label_map[0] > 0).all()
    lake = [c for c in plan.cells if "lake" in c.name]
    assert lake and np.logical_or.reduce([c.mask for c in lake])[127].all()
    for i, c in enumerate(plan.cells):
        for d in plan.cells[i + 1:]:
            assert not (c.mask & d.mask).any()
    assert not masks["sky"][0].any() and not masks["lake"][127].any()   # the segmenter's arrays were never written into
    seen_off, _ = _snap_run(tmp_path, False)
    assert not seen_off["res"].union["sky"][0].any()


def test_stub_run_default_unchanged_and_flag_runs(tmp_path):
    from PIL import Image
    from autoedit import cli
    src = tmp_path / "in"
    src.mkdir()
    Image.fromarray((np.random.RandomState(1).rand(64, 96, 3) * 255).astype("uint8")).save(src / "a.jpg")
    outs = []
    for name, extra in (("o1", []), ("o2", []), ("o3", ["--snap-edges"])):
        assert cli.main(["run", str(src), str(tmp_path / name), "--backend", "stub", *extra]) == 0
        outs.append((tmp_path / name / "a.jpg").read_bytes())
    assert outs[0] == outs[1]


def test_corners_fill_when_mask_stops_short_of_all_four_edges():
    m = np.zeros((60, 60), bool)
    m[3:57, 3:57] = True
    out = snap_to_edges(m, 12)
    assert out.all()
    assert not m[0, 0]   # input untouched
