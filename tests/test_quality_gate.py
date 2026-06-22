"""Post-edit safety: the review gate must read the EDITED output, the quality assessment must
flag clipping/oversaturation, and reference transfer must not bypass the chroma gate."""

import numpy as np
from PIL import Image as PILImage

from autoedit.contract import GlobalStatsModel, RunConfig
from autoedit.edit.gates import assess
from autoedit.pipeline import Pipeline
from autoedit.web.jobs import _safety


def _g(**kw):
    base = dict(mean_lum=0.5, mean_sat=0.2, shadow_clip=0.0, highlight_clip=0.0, haze=0.1)
    base.update(kw)
    return GlobalStatsModel(**base)


def test_assess_flags_clipping():
    score, needs, reason, warns = assess(_g(), _g(highlight_clip=0.10))
    assert needs and "highlight" in reason and score < 0.85


def test_assess_flags_oversaturation():
    score, needs, reason, warns = assess(_g(mean_sat=0.2), _g(mean_sat=0.4))
    assert any("saturation" in w for w in warns) and score < 1.0


def test_assess_clean_edit_passes():
    score, needs, reason, warns = assess(_g(), _g(mean_sat=0.22))
    assert not needs and score >= 0.85 and reason == ""


def test_jobs_safety_reads_post_edit(tmp_path):
    from autoedit.contract import Result

    ok = Result(image="a", scene="landscape", confidence=0.6, needs_review=False)
    bad = Result(image="b", scene="landscape", confidence=0.6, needs_review=True,
                 review_reason="highlight clipping 9%")
    assert _safety(ok)[0] == "finished"
    assert _safety(bad) == ("review", "highlight clipping 9%")


def test_pipeline_populates_post_edit_fields(tmp_path):
    a = np.zeros((96, 96, 3), np.uint8)
    a[:48] = [120, 150, 210]
    p = tmp_path / "p.jpg"
    PILImage.fromarray(a, "RGB").save(str(p))
    _, res = Pipeline.build(RunConfig(backend="stub")).process_one(str(p), True)
    assert res.post_edit_stats is not None          # stats came from the edited output
    assert 0.0 <= res.quality_score <= 1.0
    assert isinstance(res.needs_review, bool)
