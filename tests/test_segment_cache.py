"""CachingSegmenter: per-concept memoization so style switches don't re-run SAM.

Guards the behaviour the UI relies on — switching natural→dramatic must segment only the new
concepts, serving the shared ones (sky/mountain/snow/grass/water) from cache.
"""

import numpy as np

from autoedit.imageio import Image
from autoedit.segment import CachingSegmenter
from autoedit.segment.base import RegionMask


class SpySegmenter:
    """Records every concept it was actually asked to segment. Returns a mask for all but
    'ghost' (which it reports as not-found) so we can test the None sentinel."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def segment(self, img, concepts):
        self.calls.append(list(concepts))
        return [RegionMask(name=c, mask=np.ones(img.rgb.shape[:2], bool), coverage=1.0)
                for c in concepts if c != "ghost"]

    @property
    def segmented(self):  # flat list of every concept ever passed to the inner segmenter
        return [c for call in self.calls for c in call]


def _img(path="/x/a.jpg"):
    return Image(rgb=np.zeros((8, 8, 3), np.float32), path=path)


def test_overlapping_concepts_segment_only_the_new_ones():
    spy = SpySegmenter()
    cache = CachingSegmenter(spy)
    img = _img()

    r1 = cache.segment(img, ["sky", "skin"])
    assert {m.name for m in r1} == {"sky", "skin"}
    assert spy.calls == [["sky", "skin"]]

    # style switch: sky is shared (cached), mountain is new → inner sees only mountain
    r2 = cache.segment(img, ["sky", "mountain"])
    assert {m.name for m in r2} == {"sky", "mountain"}
    assert spy.calls == [["sky", "skin"], ["mountain"]]

    # fully-cached request hits no inner call at all
    cache.segment(img, ["sky", "skin", "mountain"])
    assert spy.segmented == ["sky", "skin", "mountain"]  # nothing re-run


def test_absent_concept_not_retried():
    spy = SpySegmenter()
    cache = CachingSegmenter(spy)
    img = _img()
    assert cache.segment(img, ["ghost"]) == []          # found nothing
    assert cache.segment(img, ["ghost"]) == []          # served from the None sentinel
    assert spy.calls == [["ghost"]]                      # inner asked exactly once


def test_distinct_images_do_not_share_cache():
    spy = SpySegmenter()
    cache = CachingSegmenter(spy)
    cache.segment(_img("/x/a.jpg"), ["sky"])
    cache.segment(_img("/x/b.jpg"), ["sky"])
    assert spy.calls == [["sky"], ["sky"]]               # different paths → both segmented


def test_changed_development_at_the_same_path_is_resegmented():
    spy = SpySegmenter()
    cache = CachingSegmenter(spy)
    original = _img()
    developed = _img()
    developed.rgb[:] = 0.5
    cache.segment(original, ["sky"])
    cache.segment(developed, ["sky"])
    cache.segment(developed, ["sky"])
    cache.segment(original, ["sky"])
    assert spy.calls == [["sky"], ["sky"]]


def test_pathless_image_bypasses_cache():
    spy = SpySegmenter()
    cache = CachingSegmenter(spy)
    anon = Image(rgb=np.zeros((8, 8, 3), np.float32), path=None)
    cache.segment(anon, ["sky"])
    cache.segment(anon, ["sky"])
    assert spy.calls == [["sky"], ["sky"]]               # no path → never cached


def test_lru_evicts_oldest_image():
    spy = SpySegmenter()
    cache = CachingSegmenter(spy, max_images=2)
    cache.segment(_img("/x/a.jpg"), ["sky"])
    cache.segment(_img("/x/b.jpg"), ["sky"])
    cache.segment(_img("/x/c.jpg"), ["sky"])            # evicts a
    cache.segment(_img("/x/a.jpg"), ["sky"])            # a must be re-segmented
    assert spy.calls.count(["sky"]) == 4
