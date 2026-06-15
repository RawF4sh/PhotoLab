"""Phase 3 compositional masks: radial/linear alpha shapes, planning, and apply integration."""

import numpy as np
from PIL import Image as PILImage

from autoedit.compose import Move, load_compose, plan, settings_for
from autoedit.contract import RunConfig
from autoedit.paths import config_path
from autoedit.edit import apply_image, linear_alpha, radial_alpha
from autoedit.pipeline import Pipeline


def test_radial_alpha_center_vs_edge():
    a = radial_alpha((100, 100), 0.5, 0.5, 0.2, 0.2, feather=0.2, inside=True)
    assert a[50, 50] > 0.9          # center inside
    assert a[0, 0] < 0.1            # far corner outside
    inv = radial_alpha((100, 100), 0.5, 0.5, 0.2, 0.2, feather=0.2, inside=False)
    assert inv[50, 50] < 0.1 and inv[0, 0] > 0.9   # inverse (vignette)


def test_linear_alpha_top_gradient():
    a = linear_alpha((100, 10), frm="top", coverage=0.5)
    assert a[0, 0] == 1.0           # top edge
    assert a[99, 0] == 0.0          # bottom
    assert a[:, 0].tolist() == sorted(a[:, 0].tolist(), reverse=True)  # monotonic down


def test_load_and_settings_for():
    moves = load_compose(config_path("compose.toml"))
    names = {m.name for m in moves}
    assert {"subject_vignette", "sky_grad"} <= names
    s = settings_for(moves, {"subject": np.ones((4, 4), bool)}, intensity=1.0)
    assert "subject_vignette" in s and "sky_grad" not in s   # only triggers on present concept


def test_plan_triggers_on_present_concept():
    moves = [Move(name="vig", when="subject", shape="radial", target="outside", ops={"exposure": -0.3})]
    union = {"subject": np.zeros((20, 20), bool)}
    union["subject"][6:14, 6:14] = True
    out = plan(moves, union, (40, 40), intensity=0.5)
    assert len(out) == 1
    name, alpha, settings = out[0]
    assert name == "vig" and alpha.shape == (40, 40)
    assert settings["exposure"] == -0.15          # scaled by intensity


def test_compositional_darkens_outside_subject():
    base = np.full((40, 40, 3), 0.6, np.float32)
    # vignette: darken everywhere except a center ellipse
    alpha = radial_alpha((40, 40), 0.5, 0.5, 0.2, 0.2, feather=0.2, inside=False)
    out = apply_image(base, [], {}, compositional=[(alpha, {"exposure": -0.5})])
    assert out[0, 0, 0] < base[0, 0, 0]           # corner darkened
    assert abs(out[20, 20, 0] - base[20, 20, 0]) < 0.02  # center ~untouched


def test_pipeline_records_composition(tmp_path):
    a = np.zeros((96, 96, 3), np.uint8)
    a[:48] = [100, 140, 200]                       # sky
    yy, xx = np.mgrid[0:96, 0:96]
    a[(((xx - 48) / 20) ** 2 + ((yy - 60) / 24) ** 2) <= 1.0] = [200, 150, 120]  # subject
    p = tmp_path / "p.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))
    _, result = Pipeline.build(RunConfig(backend="stub")).process_one(str(p), True)
    targets = {m.target for m in result.composition}
    assert "subject_vignette" in targets and "sky_grad" in targets
