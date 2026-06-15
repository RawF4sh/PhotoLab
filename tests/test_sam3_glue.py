"""SAM 3 backend glue: the part of Sam3Segmenter that turns the transformers
post-processed output (per-instance masks + scores) into one unioned RegionMask.

Uses fakes for the model/processor so it needs neither torch, the gated weights, nor a
network — it guards the conversion contract, not SAM itself. (The live torch-tensor path
was verified separately on an MPS box; here we exercise the numpy branch + union/gating.)
"""

import numpy as np

from autoedit.imageio import Image
from autoedit.segment.sam3 import Sam3Segmenter


class _Inputs(dict):
    def to(self, device):  # processor(...).to(device) is a no-op for the fake
        return self


class _Proc:
    """Mimics Sam3Processor's surface used by _run_concept."""

    def __call__(self, images=None, text=None, return_tensors=None):
        return _Inputs(pixel_values=np.zeros((1, 3, 8, 8), np.float32),
                       original_sizes=np.array([[6, 6]]))

    def post_process_instance_segmentation(self, outputs, threshold, mask_threshold, target_sizes):
        h, w = target_sizes[0]
        a = np.zeros((h, w), bool); a[:3, :3] = True   # instance A: 9 px
        b = np.zeros((h, w), bool); b[3:, 3:] = True   # instance B: 9 px (disjoint)
        return [{"masks": np.stack([a, b]),
                 "scores": np.array([0.81, 0.55]),
                 "boxes": np.zeros((2, 4))}]


class _Model:
    def __call__(self, **kw):
        return object()


def _seg() -> Sam3Segmenter:
    s = Sam3Segmenter.__new__(Sam3Segmenter)   # bypass __init__/_ensure_model (no torch needed)
    s.device, s.score_threshold, s.mask_threshold = "cpu", 0.4, 0.5
    s._model, s._processor = _Model(), _Proc()
    return s


def _img() -> Image:
    return Image(rgb=np.zeros((6, 6, 3), np.float32), source_bits=8, path="x")


def test_sam3_segment_unions_instances_and_keeps_best_score():
    regions = _seg().segment(_img(), ["snow"])
    assert len(regions) == 1
    r = regions[0]
    assert r.name == "snow"
    assert r.mask.dtype == bool and r.mask.shape == (6, 6)
    assert int(r.mask.sum()) == 18              # 9 + 9 unioned, disjoint
    assert abs(r.score - 0.81) < 1e-6           # best instance score wins
    assert r.instance_count == 2
    assert abs(r.coverage - 18 / 36) < 1e-6


def test_sam3_concept_with_no_matches_yields_no_region():
    s = _seg()
    s._processor.post_process_instance_segmentation = lambda *a, **k: [
        {"masks": np.zeros((0, 6, 6), bool), "scores": np.zeros((0,)), "boxes": np.zeros((0, 4))}]
    assert s.segment(_img(), ["unicorn"]) == []   # open-vocab: absent concept just drops out
