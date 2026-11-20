"""F43: timelapse flicker."""
import random

import numpy as np
from PIL import Image as PILImage

from autoedit import batch
from autoedit.consistency import timelapse_biases
from autoedit.contract import RunConfig


def _ramp(n=60, flicker=0.3):
    rnd = random.Random(1)
    base = [-2.0 * i / (n - 1) for i in range(n)]                 # EV, falling 2 EV
    return base, [b + rnd.uniform(-flicker, flicker) for b in base]


def _lums(ev):
    return [2.0 ** e for e in ev]


def test_flicker_removed_ramp_kept():
    base, ev = _ramp()
    after = [e + b for e, b in zip(ev, timelapse_biases(_lums(ev), 9, 0.5))]
    d0, d1 = np.diff(ev).std(), np.diff(after).std()
    assert d1 <= 0.3 * d0
    assert abs(after[0] - base[0]) < 0.15 and abs(after[-1] - base[-1]) < 0.15


def test_clean_ramp_barely_moves():
    base, _ = _ramp()
    assert max(abs(b) for b in timelapse_biases(_lums(base), 9, 0.5)) < 0.02


def test_clamp_window_and_single():
    assert timelapse_biases([1.0, 1.0, 8.0, 1.0, 1.0], 5, 0.5)[2] == -0.5
    assert all(abs(b) < 1e-9 for b in timelapse_biases([1.0, 2.0], 9, 0.5))   # window shrunk to the two frames, a ramp
    assert timelapse_biases([3.0], 9, 0.5) == [0.0]


def _frames(folder, n=12):
    rnd = random.Random(2)
    folder.mkdir()
    for i in range(n):
        v = 200 - 8 * i + rnd.choice([-30, 30])                     # slow fall, big flicker
        PILImage.fromarray(np.full((64, 64, 3), v, np.uint8), "RGB").save(folder / f"f{i:02d}.jpg", quality=95)


def _steps(out, n):
    m = [float(np.asarray(PILImage.open(out / f"f{i:02d}.jpg").convert("L")).mean()) for i in range(n)]
    return np.abs(np.diff(m)).mean(), [(out / f"f{i:02d}.jpg").read_bytes() for i in range(n)]


def test_stub_run_evens_steps_and_default_unchanged(tmp_path):
    _frames(tmp_path / "in")
    batch.run(RunConfig(backend="stub"), str(tmp_path / "in"), str(tmp_path / "off"))
    batch.run(RunConfig(backend="stub", consistency="timelapse"), str(tmp_path / "in"), str(tmp_path / "on"))
    batch.run(RunConfig(backend="stub"), str(tmp_path / "in"), str(tmp_path / "off2"))
    s_off, b_off = _steps(tmp_path / "off", 12)
    s_on, _ = _steps(tmp_path / "on", 12)
    assert s_on < s_off
    assert b_off == _steps(tmp_path / "off2", 12)[1]


def test_clean_step_is_kept():
    ev = [0.0] * 15 + [-2.0] * 15                     # one sudden change of light in 30 frames
    assert max(abs(b) for b in timelapse_biases(_lums(ev), 9, 0.5, 0.75)) < 0.05


def test_flicker_still_cut_with_step_guard():
    _, ev = _ramp()
    after = [e + b for e, b in zip(ev, timelapse_biases(_lums(ev), 9, 0.5, 0.75))]
    assert np.diff(after).std() <= 0.3 * np.diff(ev).std()


def test_raw_and_jpeg_frames_are_evened_apart():
    from autoedit import consistency
    names = [f"f{i}.{'dng' if i % 2 else 'jpg'}" for i in range(12)]       # alternating kinds, RAW 1.5 stops darker
    lv = {n: (0.0 if n.endswith("jpg") else -1.5) for n in names}
    mk = lambda n: {"fg_p50": 2.0 ** (lv[n] / 2.2), "exposure": 0.0, "shadows_ev": 0.0, "headroom_ev": 5.0}
    was = consistency._decided, consistency.capture_time
    consistency._decided, consistency.capture_time = (lambda _p, path: mk(path)), (lambda _p: None)
    try:
        out = consistency._timelapse_plan(names, None, {**consistency.BURST_DEFAULTS})
    finally:
        consistency._decided, consistency.capture_time = was
    assert max(abs(b.exposure) for b in out.values()) < 0.05
