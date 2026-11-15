"""F4: row-strip threads must not change a single value."""

import json
import os
import threading
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

import numpy as np
from PIL import Image as PILImage

from autoedit import batch, emit
from autoedit.contract import RunConfig
from autoedit.edit import apply_image, ops, par
from autoedit.edit.gates import _chroma, chroma_guard
from autoedit.imageio import luma
from autoedit.pipeline import Pipeline
from autoedit.segment import MASK_CACHE_DIR, DiskMaskCache


class _Pinned:
    """Pin the strip threshold and thread count; put both back on exit."""

    def __init__(self, threads, min_pixels=0):
        self.threads, self.min_pixels = threads, min_pixels

    def __enter__(self):
        self.old = (par._threads, par.MIN_PIXELS)
        par.MIN_PIXELS = self.min_pixels
        par.set_threads(self.threads)

    def __exit__(self, *exc):
        par.MIN_PIXELS = self.old[1]
        par.set_threads(self.old[0])


def _img(h, w, seed=0):
    return np.random.default_rng(seed).random((h, w, 3), dtype=np.float32)


def _render(x, bundle, threads):
    with _Pinned(threads):
        return ops.apply_ops(x, bundle)


def test_rows_is_bit_identical_for_every_per_pixel_op():
    rng = np.random.default_rng(1)
    names = [n for n in ops.OP_NAMES if n not in ops.SPATIAL_OPS]
    bundles = [{} for _ in range(5)]
    for i, name in enumerate(names):
        lo, hi = ops.OP_BOUNDS[name]
        for b in (bundles[i % 5], bundles[(i + 2) % 5]):
            b[name] = float(rng.uniform(0.2, 0.8) * hi * (rng.choice([-1, 1]) if lo < 0 else 1))
    bundles[0]["clarity"] = 30.0  # a full-frame op between the two strip stretches
    assert set().union(*bundles) >= set(names)
    for shape in ((700, 900), (701, 333)):
        x = _img(*shape)
        for b in bundles:
            one, four = _render(x, b, 1), _render(x, b, 4)
            assert np.array_equal(one, four), (shape, sorted(b))


def test_steps_list_every_op_once():
    flat = [k for step in ops.STEPS for k in step]
    assert len(flat) == len(set(flat))
    assert set(flat) == set(ops.OP_NAMES)


def test_a_bundle_can_be_cut_between_any_two_steps():
    bundle = {"temp": 12.0, "tint": -8.0, "exposure": 0.4, "highlight_rolloff": 30.0, "shadows_ev": 0.5,
              "highlights": -20.0, "shadows": 25.0, "whites": 10.0, "blacks": -10.0, "contrast": 15.0,
              "curve": 20.0, "dehaze": 15.0, "clarity": 25.0, "black_point": 60.0, "white_point": 40.0, "vibrance": 20.0, "saturation": 10.0,
              "hsl_skin_sat": 10.0, "hsl_green_sat": -15.0, "hsl_green_lum": 10.0, "hsl_blue_sat": 20.0,
              "grade_shadows_temp": 10.0, "grade_shadows_tint": -5.0, "grade_highlights_temp": 12.0,
              "grade_highlights_tint": 6.0, "denoise": 40.0, "sharpen": 30.0}
    assert set(bundle) == set(ops.OP_NAMES)
    x = _img(96, 96, seed=3)
    want = ops.apply_ops(x, bundle)
    for cut in range(len(ops.STEPS) + 1):
        first = {k for step in ops.STEPS[:cut] for k in step}
        pre = {k: v for k, v in bundle.items() if k in first}
        post = {k: v for k, v in bundle.items() if k not in first}
        got = ops.apply_ops(ops.apply_ops(x, pre), post)
        assert np.array_equal(got, want), cut


def _old_guarded(x, g, guard_mask, guard_ops):
    full = ops.apply_ops(x, g)
    kept = {k: v for k, v in g.items() if k not in guard_ops}
    guarded = ops.apply_ops(x, kept) if kept else x
    m = guard_mask[..., None]
    return np.clip(full * (1.0 - m) + guarded * m, 0.0, 1.0)


def test_guarded_global_pass_is_unchanged():
    x = _img(600, 400, seed=5)
    rng = np.random.default_rng(6)
    mask = rng.random((600, 400), dtype=np.float32)
    g = {"exposure": 0.3, "contrast": 12.0, "clarity": 20.0, "saturation": 15.0, "vibrance": 10.0,
         "grade_shadows_temp": 8.0, "sharpen": 20.0}
    g["hsl_green_sat"] = 0.0   # a guard op whose setting is 0
    for guard_ops in ({"clarity", "saturation", "vibrance", "grade_shadows_temp"}, {"exposure", "saturation"},
                      {"hsl_green_sat", "hsl_blue_sat", "clarity"}):   # hsl_blue_sat is not in the settings
        with _Pinned(1):
            want = _old_guarded(x, g, mask, guard_ops)
        for threads in (1, 4):
            with _Pinned(threads):
                got = apply_image(x, [], g, guard_mask=mask, guard_ops=guard_ops)
            assert np.array_equal(got, want), (sorted(guard_ops), threads)


def test_identity_returns_the_input_object():
    x = _img(32, 32)
    assert ops.apply_ops(x, {}) is x
    assert ops.apply_ops(x, {"exposure": 0.0}) is x


def test_thread_setting():
    old_threads, old_env = par._threads, os.environ.get("AUTOEDIT_THREADS")
    try:
        par.set_threads(3)
        assert par.threads() == 3
        par.set_threads(None)
        os.environ["AUTOEDIT_THREADS"] = "2"
        assert par.threads() == 2
        Pipeline.build(RunConfig(threads=5))
        assert par.threads() == 5
        par.set_threads(None)
        os.environ["AUTOEDIT_THREADS"] = "999999"
        assert par.threads() == 256
    finally:
        par.set_threads(old_threads)
        if old_env is None:
            os.environ.pop("AUTOEDIT_THREADS", None)
        else:
            os.environ["AUTOEDIT_THREADS"] = old_env


def _corpus(folder):
    os.makedirs(folder)
    for k in range(5):
        a = _img(96, 96, seed=k) * 255
        a[:48] = [100 + 20 * k, 140, 200]
        PILImage.fromarray(a.astype(np.uint8), "RGB").save(os.path.join(folder, f"p{k}.jpg"), quality=95)
    with open(os.path.join(folder, "broken.jpg"), "wb") as fh:
        fh.write(b"not a jpeg")


def _snapshot(out):
    files = {}
    for name in sorted(os.listdir(out)):
        path = os.path.join(out, name)
        if not os.path.isfile(path):
            continue
        if name == "run.json":
            m = json.load(open(path))
            for k in ("duration_s", "out_dir", "workers"):
                m.pop(k)
            files[name] = m
        elif name.endswith(".json"):
            d = json.load(open(path))
            for k in ("timing_ms", "stage_timings_ms"):
                d.pop(k, None)
            files[name] = d
        else:
            files[name] = open(path, "rb").read()
    return files


def test_workers_write_the_same_files(tmp_path):
    src = str(tmp_path / "in")
    _corpus(src)
    one, two = str(tmp_path / "one"), str(tmp_path / "two")
    m1 = batch.run(RunConfig(backend="stub", workers=1), src, one)   # the setting, not the argument
    m2 = batch.run(RunConfig(backend="stub", workers=2), src, two)
    assert (m1["workers"], m2["workers"]) == (1, 2)
    assert m1["processed"] == 5 and m1["failed"] == 1
    assert m1["failures"] == m2["failures"] and m1["failures"][0]["image"] == "broken.jpg"
    assert _snapshot(one) == _snapshot(two)


def test_effective_workers():
    n, why = batch._effective_workers(RunConfig(decider="llm"), 3)
    assert n == 1 and why
    n, why = batch._effective_workers(RunConfig(backend="sam3", mask_cache=False), 3)
    assert n == 1 and why
    n, why = batch._effective_workers(RunConfig(backend="stub"), 0)
    assert 1 <= n <= 4
    assert batch._effective_workers(RunConfig(backend="stub"), 3) == (3, "")


def test_worker_never_loads_a_model(tmp_path):
    old, old_threads = dict(batch._WORKER), par._threads
    real_build, built = Pipeline.build, []
    try:
        import cv2
        old_cv = cv2.getNumThreads()
    except Exception:
        cv2, old_cv = None, 0

    def build(cfg):
        built.append(cfg.backend)
        return real_build(cfg)

    Pipeline.build = staticmethod(build)
    try:
        cfg = RunConfig(backend="sam3")
        batch._worker_init(cfg, str(tmp_path), 1)
    finally:
        Pipeline.build = real_build
        par.set_threads(old_threads)
        if cv2 is not None:
            cv2.setNumThreads(old_cv)
        pipe = batch._WORKER.get("pipe")
        batch._WORKER.clear()
        batch._WORKER.update(old)
    assert built == ["stub"]                  # nothing but the stub was ever built
    assert pipe.cfg is cfg and pipe.cfg.backend == "sam3"
    seg = pipe.segmenter
    assert isinstance(seg, DiskMaskCache) and isinstance(seg.inner, batch._NoModel)
    assert seg.root == os.path.join(str(tmp_path), MASK_CACHE_DIR, "sam3")
    img = type("I", (), {"path": "x.jpg", "rgb": np.zeros((8, 8, 3), np.float32)})()
    try:
        seg.segment(img, ["sky"])
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "sky" in str(e)


def _hazy(h, w, seed=3):
    return (0.7 + 0.3 * _img(h, w, seed) * _img(h, w, seed + 1)).astype(np.float32)


def _old_dehaze(rgb, amt):
    """The positive branch of dehaze before F4 (git show HEAD:src/autoedit/edit/ops.py)."""
    a = amt / 100.0
    h, w = rgb.shape[0], rgb.shape[1]
    long_edge = max(h, w)
    if long_edge > ops._DEHAZE_PROXY_EDGE:
        scale = ops._DEHAZE_PROXY_EDGE / long_edge
        work_w = max(1, round(w * scale))
        work_h = max(1, round(h * scale))
        work = ops._resize_rgb(rgb, (work_w, work_h))
    else:
        work = rgb
        work_h, work_w = h, w
    patch = max(1, int(round(0.006 * max(work_h, work_w))))
    omega, t0 = 0.92, 0.12
    dark = ops.box_min(work.min(axis=2), patch)
    flat = dark.reshape(-1)
    k = max(50, int(flat.size * 0.001))
    idx = np.argpartition(flat, -k)[-k:]
    pix = work.reshape(-1, 3)[idx]
    A = np.clip(pix.mean(axis=0), 0.5, 1.0).astype(np.float32)
    t = 1.0 - omega * ops.box_min((work / A).min(axis=2), patch)
    t = ops.guided_filter(luma(work), t.astype(np.float32), max(patch * 4, 8), eps=1e-3)
    t = np.clip(t, t0, 1.0)
    lum = luma(work)
    sigma = 0.006 * max(work_h, work_w) + 1.0
    detail = np.abs(lum - ops.gaussian_blur(lum, sigma))
    smooth = np.clip((0.018 - detail) / 0.018, 0.0, 1.0)
    bright = np.clip((lum - 0.50) / 0.30, 0.0, 1.0)
    neutral = np.clip((0.22 - ops._pixsat(work)) / 0.22, 0.0, 1.0)
    fragile = smooth * bright * neutral
    blend = a * (1.0 - 0.94 * fragile)
    if work is not rgb:
        t = ops._resize_float(t, (w, h))
        blend = ops._resize_float(blend, (w, h))
    t = np.clip(t, t0, 1.0)[..., None]
    recovered = (rgb - A) / t + A
    out = rgb + blend[..., None] * (np.clip(recovered, 0.0, 1.0) - rgb)
    return out.astype(np.float32)


def test_dehaze_strips_are_bit_identical():
    for shape in ((700, 900), (1500, 1700)):  # the second one goes through dehaze's proxy
        x = _hazy(*shape)
        with _Pinned(1):
            one = ops.dehaze(x, 30.0)
        with _Pinned(4):
            four = ops.dehaze(x, 30.0)
        assert np.array_equal(one, four), shape
    x = _hazy(700, 900)
    with _Pinned(4):
        assert np.array_equal(ops.dehaze(x, 30.0), _old_dehaze(x, 30.0))


def _old_chroma_guard(source, edited, max_increase=0.35, neutral_allowance=0.02, protect=None,
                      protect_increase=0.05, highlight_lum=0.90):
    """chroma_guard as it was before F4 T3 (git show HEAD~:src/autoedit/edit/gates.py)."""
    src_c = _chroma(source)
    out_c = _chroma(edited)
    allowance = max(0.0, float(neutral_allowance))
    ceiling = src_c * (1.0 + max_increase) + allowance
    if protect is not None:
        p = np.clip(protect.astype(np.float32), 0.0, 1.0)
        protected = np.minimum(ceiling, src_c * (1.0 + protect_increase) + 0.008)
        ceiling = ceiling * (1.0 - p) + protected * p
    hi = luma(edited) > highlight_lum
    ceiling = np.where(hi, np.minimum(ceiling, src_c * 1.02 + 0.004), ceiling)
    over = out_c > ceiling
    if not over.any():
        return edited.astype(np.float32), 0.0
    l = luma(edited)[..., None]
    scale = np.ones_like(out_c)
    np.divide(ceiling, out_c, out=scale, where=over)
    scale = np.clip(scale, 0.0, 1.0)[..., None]
    guarded = l + (edited - l) * scale
    return np.clip(guarded, 0.0, 1.0).astype(np.float32), float(over.mean())


def _saturated(src):
    mean = src.mean(axis=-1, keepdims=True)
    return np.clip(mean + (src - mean) * 2.5, 0.0, 1.0).astype(np.float32)


def test_chroma_guard_strips_are_bit_identical():
    src = _img(700, 900, 5) * 0.6
    edit = _saturated(src)
    mask = _img(700, 900, 6)[..., 0]
    for prot in (None, mask):
        with _Pinned(1):
            one = chroma_guard(src, edit, protect=prot)
        with _Pinned(4):
            four = chroma_guard(src, edit, protect=prot)
        assert one[1] > 0.0
        assert np.array_equal(one[0], four[0]) and one[1] == four[1]
    for n in (1, 4):
        with _Pinned(n):
            out, frac = chroma_guard(src, src.copy())
        assert frac == 0.0 and out.dtype == np.float32 and np.array_equal(out, src)


def test_chroma_guard_matches_the_previous_formula():
    src = _img(96, 96, 7) * 0.8
    edit = _saturated(src)
    mask = _img(96, 96, 8)[..., 0]
    for prot in (None, mask):
        for n in (1, 4):
            with _Pinned(n):
                new = chroma_guard(src, edit, protect=prot)
            old = _old_chroma_guard(src, edit, protect=prot)
            assert np.array_equal(new[0], old[0]) and new[1] == old[1] and new[1] > 0.0
    src = _img(700, 900, 9) * 0.6   # large enough to run on strips
    edit = _saturated(src)
    mask = _img(700, 900, 10)[..., 0]
    for prot in (None, mask):
        with _Pinned(4):
            new = chroma_guard(src, edit, protect=prot)
        old = _old_chroma_guard(src, edit, protect=prot)
        assert np.array_equal(new[0], old[0]) and new[1] == old[1] and new[1] > 0.0


def test_each_returns_one_value_per_strip():
    x = _img(700, 900, 9)
    for n in (1, 4):
        with _Pinned(n):
            parts = par.each(lambda a: a.shape[0], x)
            assert sum(parts) == 700
            assert np.array_equal(par.rows(lambda a: a * 2.0, x), x * 2.0)
    with _Pinned(4):
        assert len(par.each(lambda a: a.shape[0], x)) > 1


def _stub_pipe(cfg, out_dir, inner=None):
    """A pipeline that segments with the stub but is named for a model backend, behind the mask
    cache, as a render worker (inner=_NoModel) or the parent of a batch sees it."""
    pipe = Pipeline.build(cfg.model_copy(update={"backend": "stub"}))
    pipe.cfg = cfg
    pipe.use_mask_cache(out_dir)
    if inner is not None:
        pipe.segmenter.inner = inner
    return pipe


def test_workers_keep_the_reference_match(tmp_path):
    photo, ref, out = str(tmp_path / "a.jpg"), str(tmp_path / "ref.jpg"), str(tmp_path / "out")
    for path, gain in ((photo, 1.0), (ref, 1.25)):
        a = np.zeros((128, 128, 3), np.float32)
        a[:70] = [100, 140, 200]
        a[70:] = [60, 110, 70]
        yy, xx = np.mgrid[0:128, 0:128]
        a[(((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0] = [200, 150, 120]
        PILImage.fromarray(np.clip(a * gain, 0, 255).astype(np.uint8), "RGB").save(path, quality=95)
    cfg = RunConfig(backend="sam3", reference=ref)
    parent = _stub_pipe(cfg, out)
    parent.presegment(photo)                       # what batch.run does before it submits a photo
    worker = _stub_pipe(cfg, out, inner=batch._NoModel())
    got, got_result = worker.process_one(photo)
    want, want_result = parent.process_one(photo)  # the parent's own render
    assert any("reference match (region)" in n for n in want_result.notes)
    assert any("reference match (region)" in n for n in got_result.notes)
    assert np.array_equal(got, want)


def test_set_threads_while_rendering():
    x = _img(600, 800, 11)
    bundle = {"exposure": 0.3, "contrast": 12.0, "saturation": 15.0, "vibrance": 10.0, "curve": 20.0}
    with _Pinned(1):
        want = ops.apply_ops(x, bundle)
    errors, stop = [], threading.Event()

    def render():
        try:
            while not stop.is_set():
                if not np.array_equal(ops.apply_ops(x, bundle), want):
                    errors.append("a result differs")
                    return
        except Exception as e:   # the shared pool was shut down under a running call
            errors.append(repr(e))

    def flip():
        k = 0
        while not stop.is_set():
            par.set_threads((2, 3, 4)[k % 3])
            k += 1

    with _Pinned(2):
        workers = [threading.Thread(target=f) for f in (render, render, flip)]
        for t in workers:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in workers:
            t.join()
    assert not errors, errors


def test_set_threads_keeps_the_pool_when_nothing_changes():
    x = _img(700, 900, 12)
    with _Pinned(3):
        par.each(lambda a: a.shape[0], x)
        pool = par._pool
        assert pool is not None
        par.set_threads(3)
        assert par._pool is pool
        par.set_threads(2)
        assert par._pool is None


def _jobs(folder, out):
    cfg = RunConfig(backend="stub")
    taken, jobs = set(), []
    for i, name in enumerate(sorted(os.listdir(folder)), start=1):
        path = os.path.join(folder, name)
        image_out, sidecar = emit.output_paths(out, path, cfg.out_format, taken)
        jobs.append((i, path, image_out, sidecar, 0.0, ""))
    return cfg, jobs


def test_broken_pool_falls_back_to_the_parent(tmp_path):
    src, out = str(tmp_path / "in"), str(tmp_path / "out")
    _corpus(src)
    os.makedirs(out)
    cfg, jobs = _jobs(src, out)
    done, failed = [], []
    batch._render_here(Pipeline.build(cfg), jobs, True,
                       lambda i, path, r: done.append(os.path.basename(path)),
                       lambda i, path, e: failed.append(os.path.basename(path)))
    assert failed == ["broken.jpg"] and sorted(done) == [f"p{k}.jpg" for k in range(5)]
    assert sorted(os.listdir(out)) == sorted(
        [f"p{k}.jpg" for k in range(5)] + [f"p{k}.autoedit.json" for k in range(5)])


class _DyingPool:
    """Stands in for ProcessPoolExecutor: the first photos come back as BrokenProcessPool."""
    submit_raises = False

    def __init__(self, *a, **kw):
        self.calls = 0

    def submit(self, fn, *args):
        self.calls += 1
        if self.submit_raises:
            raise BrokenProcessPool("a worker died")
        fut = Future()
        fut.set_exception(BrokenProcessPool("a worker died"))
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_a_dead_pool_still_renders_every_photo_once(tmp_path):
    src = str(tmp_path / "in")
    _corpus(src)
    one = str(tmp_path / "one")
    m1 = batch.run(RunConfig(backend="stub"), src, one, workers=1)
    real = batch.ProcessPoolExecutor
    try:
        for raises in (False, True):
            _DyingPool.submit_raises = raises
            batch.ProcessPoolExecutor = _DyingPool
            two = str(tmp_path / f"two{raises}")
            m2 = batch.run(RunConfig(backend="stub"), src, two, workers=2)
            assert (m2["processed"], m2["failed"]) == (m1["processed"], m1["failed"]) == (5, 1)
            assert m2["failures"] == m1["failures"]
            assert _snapshot(one) == _snapshot(two)
    finally:
        batch.ProcessPoolExecutor = real
        _DyingPool.submit_raises = False
