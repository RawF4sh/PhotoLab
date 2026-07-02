"""A per-concept segmentation cache — a decorator over any `Segmenter`.

SAM inference is the slow stage; everything downstream (resolve → stats → scene → decide →
apply) is cheap numpy. So when the UI re-previews the SAME image under a different style or
intensity, only the *new* concepts need segmenting — the overlap is served from cache. Styles
share concepts (e.g. `sky` is in all four built-ins; natural↔dramatic share five), so a style
switch usually segments only one or two fresh concepts instead of the whole set.

Keyed by (image identity, concept). A `None` sentinel remembers "this concept was already tried
on this image and found nothing", so a legitimately-absent concept isn't re-run every time.
In-memory images (no path) bypass the cache to avoid content collisions. LRU-bounded by image.
"""

from __future__ import annotations

import os
import threading

from ..imageio import Image
from .base import RegionMask, Segmenter


class CachingSegmenter:
    """Wrap a Segmenter, memoizing per-concept masks per image (LRU over images)."""

    def __init__(self, inner: Segmenter, max_images: int = 8):
        self.inner = inner
        self.max_images = max_images
        self._cache: dict = {}   # image_key -> {concept: RegionMask | None}
        self._order: list = []   # LRU of image_keys, most-recently-used last
        self._lock = threading.Lock()

    def _key(self, img: Image):
        """Identity for the cache: path + proxy shape + mtime. None → don't cache (no path)."""
        if not img.path:
            return None
        try:
            mtime = os.path.getmtime(img.path)
        except OSError:
            mtime = None
        return (img.path, img.rgb.shape, mtime)

    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        key = self._key(img)
        if key is None:
            return self.inner.segment(img, concepts)

        with self._lock:
            bucket = self._cache.get(key)
            missing = [c for c in concepts if not bucket or c not in bucket]

        # The slow call runs OUTSIDE the lock (inference is already serialized upstream).
        fresh = {rm.name: rm for rm in self.inner.segment(img, missing)} if missing else {}

        with self._lock:
            bucket = self._cache.setdefault(key, {})
            for c in missing:
                bucket[c] = fresh.get(c)         # None when the concept found nothing
            self._touch_evict(key)
            # Reassemble in the requested order, dropping concepts that found nothing.
            # NOTE: these are the CACHED RegionMask objects, returned by reference — downstream
            # (resolve/stats/apply) treats masks as read-only; mutating one would poison the cache.
            return [bucket[c] for c in concepts if bucket.get(c) is not None]

    def _touch_evict(self, key) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)
        while len(self._order) > self.max_images:
            self._cache.pop(self._order.pop(0), None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._order.clear()
