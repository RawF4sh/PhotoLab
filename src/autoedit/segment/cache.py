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
import hashlib
import json
import tempfile
import threading

import numpy as np

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
        """Path + proxy pixels + mtime. RAW development can change pixels at the same path/size."""
        if not img.path:
            return None
        try:
            mtime = os.path.getmtime(img.path)
        except OSError:
            mtime = None
        pixels = np.ascontiguousarray(img.rgb)
        digest = hashlib.blake2b(memoryview(pixels).cast("B"), digest_size=16).digest()
        return (img.path, img.rgb.shape, str(img.rgb.dtype), mtime, digest)

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


MASK_CACHE_DIR = ".autoedit-masks"


def pixel_digest(rgb: np.ndarray) -> str:
    """A name for a photo's proxy pixels. The mask cache's files are named by it, and the SAM 3
    backend uses it to recognise the photo it has just encoded."""
    pixels = np.ascontiguousarray(rgb)
    h = hashlib.blake2b(repr((pixels.shape, str(pixels.dtype))).encode(), digest_size=16)
    h.update(memoryview(pixels).cast("B"))
    return h.hexdigest()


class DiskMaskCache:
    """Keep each photo's raw masks on disk, keyed by the proxy pixels, so a re-render
    (`autoedit adjust`), a resumed batch or a later run with another style does not segment
    again. One .npz per photo: every concept tried so far, plus a JSON index. Never pickled, so
    a file planted in an output folder cannot run code."""

    def __init__(self, inner: Segmenter, root: str):
        self.inner, self.root = inner, root

    def __getattr__(self, name):            # `.name` and the like of the wrapped segmenter
        return getattr(self.inner, name)

    def _path(self, img: Image) -> str:
        return os.path.join(self.root, pixel_digest(img.rgb) + ".npz")

    @staticmethod
    def _read(path: str, shape: tuple | None = None) -> dict:
        try:
            with np.load(path, allow_pickle=False) as z:
                index = json.loads(bytes(z["index"]).decode())
                out = {}
                for e in index:
                    if not e.get("key"):
                        out[e["name"]] = None
                        continue
                    mask = z[e["key"]].astype(bool)
                    if shape is not None and mask.shape != tuple(shape):
                        continue            # another proxy shape: segment that concept again
                    out[e["name"]] = RegionMask(name=e["name"], mask=mask, score=float(e["score"]),
                                                coverage=float(e["coverage"]),
                                                instance_count=int(e["instances"]))
                return out
        except Exception:                   # missing, truncated or foreign file: segment again
            return {}

    def _write(self, path: str, bucket: dict) -> None:
        os.makedirs(self.root, exist_ok=True)
        index, arrays = [], {}
        for i, (name, rm) in enumerate(bucket.items()):
            if rm is None:                  # tried, found nothing
                index.append({"name": name})
                continue
            arrays[f"m{i}"] = np.asarray(rm.mask, dtype=bool)
            index.append({"name": name, "key": f"m{i}", "score": float(rm.score),
                          "coverage": float(rm.coverage), "instances": int(rm.instance_count)})
        arrays["index"] = np.frombuffer(json.dumps(index).encode(), dtype=np.uint8)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                np.savez_compressed(fh, **arrays)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def segment(self, img: Image, concepts: list[str]) -> list[RegionMask]:
        path = self._path(img)
        bucket = self._read(path, img.rgb.shape[:2])
        missing = [c for c in concepts if c not in bucket]
        if missing:
            fresh = {rm.name: rm for rm in self.inner.segment(img, missing)}
            bucket.update({c: fresh.get(c) for c in missing})
            try:
                self._write(path, bucket)
            except OSError:                 # a read-only output folder must not fail the photo
                pass
        return [bucket[c] for c in concepts if bucket.get(c) is not None]
