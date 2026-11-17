"""SAM 3 backend glue: the part of Sam3Segmenter that turns the transformers
post-processed output (per-instance masks + scores) into one unioned RegionMask.

Uses fakes for the model/processor so it needs neither torch, the gated weights, nor a
network — it guards the conversion contract, not SAM itself. (The live torch-tensor path
was verified separately on an MPS box; here we exercise the numpy branch + union/gating.)
"""

from contextlib import nullcontext
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from autoedit.imageio import Image
from autoedit.segment.cache import pixel_digest
from autoedit.segment.sam3 import Sam3Segmenter


class _Inputs(dict):
    def to(self, device):  # processor(...).to(device) is a no-op for the fake
        return self


class _Proc:
    """Mimics Sam3Processor's surface used by _encode and _run_concept."""

    def __call__(self, images=None, text=None, return_tensors=None):
        if images is not None:
            return _Inputs(pixel_values=np.zeros((1, 3, 8, 8), np.float32),
                           original_sizes=np.array([[6, 6]]))
        return _Inputs(input_ids=np.zeros((1, 4), np.int64),
                       attention_mask=np.ones((1, 4), np.int64))

    def post_process_instance_segmentation(self, outputs, threshold, mask_threshold, target_sizes):
        h, w = target_sizes[0]
        a = np.zeros((h, w), bool); a[:3, :3] = True   # instance A: 9 px
        b = np.zeros((h, w), bool); b[3:, 3:] = True   # instance B: 9 px (disjoint)
        return [{"masks": np.stack([a, b]),
                 "scores": np.array([0.81, 0.55]),
                 "boxes": np.zeros((2, 4))}]


class _Model:
    def __init__(self):
        self.encoder_calls = 0
        self.calls = []

    def get_vision_features(self, pixel_values=None):
        self.encoder_calls += 1
        return "vision-marker"

    def __call__(self, **kw):
        self.calls.append(kw)
        return object()


def _seg() -> Sam3Segmenter:
    s = Sam3Segmenter.__new__(Sam3Segmenter)   # bypass __init__/_ensure_model (no torch needed)
    s.device, s.score_threshold, s.mask_threshold = "cpu", 0.4, 0.5
    s._model, s._processor = _Model(), _Proc()
    s._kept = None
    return s


def _img() -> Image:
    return Image(rgb=np.zeros((6, 6, 3), np.float32), source_bits=8, path="x")


def _segment(seg, concepts, img=None):
    # The inference wrapper imports torch even with a fake model. Supply only the
    # surface used by these numpy fixtures so core-only CI never needs torch.
    fake_torch = SimpleNamespace(no_grad=nullcontext, Tensor=type("FakeTensor", (), {}))
    with patch.dict(sys.modules, {"torch": fake_torch}):
        return seg.segment(img or _img(), concepts)


def test_sam3_segment_unions_instances_and_keeps_best_score():
    regions = _segment(_seg(), ["snow"])
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
    assert _segment(s, ["unicorn"]) == []   # open-vocab: absent concept just drops out


def test_the_image_is_encoded_once_for_all_concepts():
    s = _seg()
    regions = _segment(s, ["snow", "sky", "tree"])
    assert len(regions) == 3
    assert s._model.encoder_calls == 1
    assert len(s._model.calls) == 3
    for kw in s._model.calls:
        assert kw["vision_embeds"] == "vision-marker"
        assert "pixel_values" not in kw
        # the prompt is padded: without its mask the model attends to the padding and masks change
        assert kw["input_ids"].shape == (1, 4)
        assert np.array_equal(kw["attention_mask"], np.ones((1, 4), np.int64))


def test_no_concepts_does_not_touch_the_model():
    s = _seg()
    seen = []

    def boom():
        seen.append(1)
        raise AssertionError("_ensure_model must not be called")

    s._ensure_model = boom
    assert _segment(s, []) == []
    assert seen == []


def test_a_second_call_on_the_same_photo_reuses_the_features():
    s = _seg()
    _segment(s, ["snow"])
    _segment(s, ["sky"])
    assert s._model.encoder_calls == 1
    assert len(s._model.calls) == 2


def test_another_photo_encodes_again_and_drops_the_old_features():
    s = _seg()
    _segment(s, ["snow"])
    other = Image(rgb=np.ones((6, 6, 3), np.float32) * 0.5, source_bits=8, path="y")
    _segment(s, ["snow"], other)
    assert s._model.encoder_calls == 2
    assert s._kept[0] == pixel_digest(other.rgb)
    assert s._kept[0] != pixel_digest(_img().rgb)


def test_the_last_photo_is_let_go_before_the_next_is_encoded():
    s = _seg()
    _segment(s, ["snow"])
    assert s._kept is not None
    held = []

    def failing_encode(rgb):
        held.append(s._kept)    # what is still held while the next photo is being encoded
        raise RuntimeError("the encoder failed")

    s._encode = failing_encode
    other = Image(rgb=np.ones((6, 6, 3), np.float32) * 0.5, source_bits=8, path="y")
    with pytest.raises(RuntimeError):
        _segment(s, ["snow"], other)
    assert held == [None]       # the old features were dropped first: the peak does not double
    assert s._kept is None      # and a photo that failed leaves nothing behind


def test_on_mps_unused_gpu_memory_is_handed_back_after_each_photo():
    s = _seg()
    s.device = "mps"
    calls = []
    fake_torch = SimpleNamespace(no_grad=nullcontext, Tensor=type("FakeTensor", (), {}),
                                 mps=SimpleNamespace(empty_cache=lambda: calls.append(1)))
    with patch.dict(sys.modules, {"torch": fake_torch}):
        regions = s.segment(_img(), ["snow", "sky"])
    assert len(regions) == 2
    assert calls == [1]   # once per photo, not once per concept
