"""Plain-language region instructions (F2): parser, cell planner, policies, pipeline, CLI/API."""

import os

import numpy as np
import pytest
from PIL import Image as PILImage

from autoedit import cli, instructions, policies
from autoedit.concepts import load_concepts
from autoedit.contract import Adjustment, Result, RunConfig
from autoedit.paths import config_path, configs_dir
from autoedit.pipeline import Pipeline
from autoedit.segment.base import RegionMask
from autoedit.segment.resolve import ResolvedRegions
from autoedit.web import service

KNOWN = {"sky", "mountain", "grass", "skin", "subject"}


def _vocab():
    return instructions.load_vocab(os.path.join(configs_dir(), "instructions.toml"))


def _p(text):
    return instructions.parse(text, _vocab(), KNOWN)


def _close(ops, want):
    assert set(ops) == set(want), (ops, want)
    for k, v in want.items():
        assert abs(ops[k] - v) < 1e-9, (k, ops[k], v)


def _flat(text):
    adjs, problems = _p(text)
    assert not problems, problems
    return [(a.target, a.ops) for a in adjs]


def test_parse_golden():
    (t, o), = _flat("brighten the jacket")
    assert t == "jacket"
    _close(o, {"exposure": 0.30})
    (t, o), = _flat("darken the sky slightly")
    assert t == "sky"
    _close(o, {"exposure": -0.15})
    a, b = _flat("slightly brighten the jacket and the sky")
    assert (a[0], b[0]) == ("jacket", "sky")
    _close(a[1], {"exposure": 0.15})
    _close(b[1], {"exposure": 0.15})
    a, b = _flat("brighten and warm the jacket")
    assert (a[0], b[0]) == ("jacket", "jacket")
    _close(a[1], {"exposure": 0.30})
    _close(b[1], {"temp": 10})
    a, b = _flat("make the mountains much darker, warm the grass a little")
    assert (a[0], b[0]) == ("mountain", "grass")
    _close(a[1], {"exposure": -0.48})
    _close(b[1], {"temp": 5})
    a, b = _flat("soften the skin; deepen the sky")
    assert (a[0], b[0]) == ("skin", "sky")
    _close(a[1], {"clarity": -12, "contrast": -6})
    _close(b[1], {"exposure": -0.12, "contrast": 8, "saturation": 6})
    (t, o), = _flat("brighten the sky more")
    assert t == "sky"
    _close(o, {"exposure": 0.39})
    assert len(_flat("brighten the sky and please")) == 1
    assert len(_flat("brighten and the jacket")) == 1
    assert instructions._toks("देवनागरी ภาษาไทย") == ("देवनागरी", "ภาษาไทย")
    assert instructions._toks("cafe\u0301") == instructions._toks("caf\u00e9") == ("caf\u00e9",)
    # "less saturated" is a verb phrase: its "less" must not reject the clause as a stray one does
    (t, o), = _flat("make the sky less saturated")
    assert t == "sky"
    _close(o, {"saturation": -14})
    assert _p("darken the sky less")[0] == []


def test_parse_global_target():
    for text in ("brighten", "make it darker", "darken the whole photo"):
        (t, _o), = _flat(text)
        assert t == "global", text


def test_parse_rejects():
    for text in ("don't brighten the sky", "don’t brighten the sky"):
        adjs, problems = _p(text)
        assert adjs == [] and len(problems) == 1 and "negation" in problems[0]
    adjs, problems = _p("do something nice")
    assert adjs == [] and len(problems) == 1 and problems[0].startswith("not understood")
    for text in (None, "", "   "):
        assert _p(text) == ([], [])
    adjs, problems = _p("brighten the one two three four five six")
    assert adjs == [] and any("target too long" in p for p in problems)
    # a target in another script is a target, not "the whole photo"
    (t, _o), = _flat("brighten the 天空")
    assert t == "天空"
    for text in ("brighten the sky instead of the grass", "brighten the sky rather than the grass",
                 "brighten the sky or the grass", "brighten the sky more than the grass",
                 "brighten the sky twenty percent", "slightly more brighten the sky"):
        adjs, problems = _p(text)
        assert adjs == [] and problems, text
    for text in ("darken the ;; sky", "brighten everything but the sky", "darken the sky less",
                 "hardly brighten the sky", "undo brighten the sky"):
        adjs, problems = _p(text)
        assert adjs == [] and problems, text
    for text in ("the cool sky brighter", "brighten the sky darken the grass",
                 "lighten the warm tones"):
        adjs, problems = _p(text)
        assert adjs == [] and any("more than one verb" in p for p in problems), text
    adjs, problems = _p("brighten the sky by 20%")
    assert adjs == [] and any("numbers" in p for p in problems)
    adjs, problems = _p("darken the")
    assert adjs == [] and any("no target" in p for p in problems)
    _adjs, problems = _p("do \x1b something")
    assert problems and not any("\x1b" in p for p in problems)


def test_vocab_validation(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[verbs.x]\nwords = ["x"]\nexposur = 0.1\n')
    try:  # no `match=`: the runner's pytest shim (tests/run_all.py) only knows raises(exc)
        instructions.load_vocab(str(bad))
    except ValueError as e:
        assert "exposur" in str(e)
    else:
        raise AssertionError("an unknown op name must be rejected")
    empty = instructions.load_vocab(str(tmp_path / "missing.toml"))
    adjs, problems = instructions.parse("brighten the sky", empty, KNOWN)
    assert adjs == [] and len(problems) == 1


def test_configs_folder_without_the_file_uses_the_packaged_vocabulary(tmp_path):
    # A local tuning copy made before F2 has no instructions.toml; --edit must still work there.
    import shutil

    from autoedit import paths

    packaged = paths._packaged()
    for name in os.listdir(packaged):
        if name not in ("instructions.toml", "palettes.toml"):
            shutil.copy(os.path.join(packaged, name), tmp_path / name)
    old = os.environ.get("AUTOEDIT_CONFIGS_DIR")
    os.environ["AUTOEDIT_CONFIGS_DIR"] = str(tmp_path)
    try:
        cfg = RunConfig(edit="brighten the sky")
        assert cfg.rules_path == os.path.join(str(tmp_path), "rules.toml")
        assert cfg.instructions_path == os.path.join(packaged, "instructions.toml")
        assert instructions.load_vocab(cfg.instructions_path).verbs
        # only instructions.toml falls back: a folder without palettes.toml keeps its own path
        assert cfg.palettes_path == os.path.join(str(tmp_path), "palettes.toml")
        found = paths.config_paths(str(tmp_path))
        assert "palettes_path" in found and "instructions_path" not in found
    finally:
        if old is None:
            os.environ.pop("AUTOEDIT_CONFIGS_DIR", None)
        else:
            os.environ["AUTOEDIT_CONFIGS_DIR"] = old


def _mask(r0, r1, c0, c1, n=100):
    m = np.zeros((n, n), bool)
    m[r0:r1, c0:c1] = True
    return m


def _rm(name, mask):
    return RegionMask(name=name, mask=mask, score=0.9, coverage=float(mask.mean()))


def _resolved(*regions, n=100):
    return ResolvedRegions(regions=list(regions), union={r.name: r.mask for r in regions},
                           detected=[], label_map=np.zeros((n, n), int), shape=(n, n))


def _plan(adjs, raw, resolved, settings, glob=None):
    glob = {} if glob is None else glob
    return instructions.plan(adjs, _vocab(), raw, [r.name for r in resolved.regions],
                             resolved, settings, glob, {})


def test_plan_splits_a_region():
    subject = _rm("subject", _mask(20, 80, 20, 80))
    jacket = _rm("jacket", _mask(40, 60, 40, 60))
    adjs = [Adjustment(target="jacket", ops={"exposure": 0.3}, source="x")]
    p = _plan(adjs, [subject, jacket], _resolved(subject), {"subject": {"exposure": 0.2}})
    cells = {c.name: c for c in p.cells}
    assert set(cells) == {"jacket@subject", "subject"}
    assert abs(p.settings["jacket@subject"]["exposure"] - 0.5) < 1e-9
    assert abs(p.settings["subject"]["exposure"] - 0.2) < 1e-9
    a, b = cells["jacket@subject"].mask, cells["subject"].mask
    assert not (a & b).any()
    assert np.array_equal(a | b, subject.mask)
    assert len(p.groups) == 1 and {c.name for c, _m in p.groups[0]} == set(cells)
    fm = {c.name: m for c, m in p.groups[0]}
    assert np.array_equal(fm["jacket@subject"], jacket.mask & subject.mask)
    assert p.owners["jacket@subject"] == ["subject", "jacket"]
    assert p.hit["jacket"] == ["jacket@subject"]


def test_plan_background_and_sliver():
    subject = _rm("subject", _mask(20, 80, 20, 80))
    other = _rm("other", _mask(85, 95, 10, 30))
    jm = _mask(40, 60, 10, 30)          # half on the subject, half on background
    jm[85:87, 10:20] = True             # 20 px on "other": below min_cell_coverage (20 px of 10000 is not < 20, so use 10)
    jm[85:87, 15:20] = False            # leaves 10 px
    jacket = _rm("jacket", jm)
    adjs = [Adjustment(target="jacket", ops={"exposure": 0.3}, source="x")]
    settings = {"subject": {"exposure": 0.2}, "other": {"exposure": 0.1}}
    p = _plan(adjs, [subject, other, jacket], _resolved(subject, other), settings)
    names = [c.name for c in p.cells]
    assert {"jacket@subject", "subject", "jacket", "other"} == set(names)
    assert names.count("other") == 1
    cs = p.cells
    for i, c in enumerate(cs):
        for d in cs[i + 1:]:
            assert not (c.mask & d.mask).any(), (c.name, d.name)
    assert [c.name for c, _m in p.groups[0]].count("jacket@subject") == 1
    assert {"subject", "jacket@subject"} <= {c.name for c, _m in p.groups[0]}
    bg = [g for g in p.groups if g[0][0].name == "jacket"]
    assert len(bg) == 1 and np.array_equal(bg[0][0][1], jm)    # the whole layer is feathered
    assert p.settings["other"] == {"exposure": 0.1}
    assert p.settings["jacket"] == {"exposure": 0.3}
    # a target with no mask is not found
    adjs = [Adjustment(target="unicorn", ops={"exposure": 0.3}, source="x")]
    p = _plan(adjs, [subject], _resolved(subject), {"subject": {}})
    assert p.hit["unicorn"] == []
    notes = instructions.report(adjs, p, p.settings)
    assert adjs[0].status == "not_found" and len(notes) == 1


def test_policy_applies_to_cells():
    _presets, meta = load_concepts(config_path("concepts.toml"))
    s = {"x@sky": {"temp": 14.0}}
    notes = policies.enforce(s, meta, owners={"x@sky": ["sky", "x"]})
    assert s["x@sky"]["temp"] == 4
    assert notes and notes[0].startswith("policy[x@sky]")
    s = {"x@sky": {"temp": 14.0}}
    policies.enforce(s, meta, owners={"x@sky": ["x", "sky"]})
    assert s["x@sky"]["temp"] == 4


def test_policy_inheritance():
    _presets, meta = load_concepts(config_path("concepts.toml"))
    v = _vocab()
    assert instructions._policy_owners("face", meta, v) == ["skin", "face"]
    assert instructions._policy_owners("blue sky", meta, v) == ["sky", "blue sky"]
    assert instructions._policy_owners("jacket", meta, v) == ["jacket"]
    assert instructions._policy_owners("sky", meta, v) == ["sky"]
    assert instructions._policy_owners("skin tones", meta, v) == ["skin", "skin tones"]
    assert instructions._policy_owners("face man", meta, v) == ["skin", "face man"]
    assert instructions._policy_owners("snow field", meta, v) == ["snow", "grass", "snow field"]
    face = _rm("face", _mask(30, 60, 30, 60))
    adjs = [Adjustment(target="face", ops={"saturation": 19}, source="x")]
    p = instructions.plan(adjs, v, [face], [], _resolved(n=100), {}, {}, meta)
    assert p.owners["face"] == ["skin", "face"]
    policies.enforce(p.settings, meta, owners=p.owners)
    assert p.settings["face"]["saturation"] == 4


def test_blocked_and_too_small():
    _presets, meta = load_concepts(config_path("concepts.toml"))
    snow = _rm("snow", _mask(30, 60, 30, 60))
    adjs = [Adjustment(target="snow", ops={"temp": 10}, source="warm the snow")]
    p = instructions.plan(adjs, _vocab(), [snow], [], _resolved(n=100), {}, {}, meta)
    policies.enforce(p.settings, meta, owners=p.owners)
    notes = instructions.report(adjs, p, p.settings)
    assert adjs[0].status == "blocked" and "forbids" in notes[0]
    tiny = _mask(10, 12, 10, 12)
    res = ResolvedRegions(regions=[], union={"tiny": tiny}, detected=[],
                          label_map=np.zeros((100, 100), int), shape=(100, 100))
    adjs = [Adjustment(target="tiny", ops={"exposure": 0.3}, source="x")]
    p = instructions.plan(adjs, _vocab(), [], ["tiny"], res, {}, {}, meta)
    notes = instructions.report(adjs, p, p.settings)
    assert adjs[0].status == "not_found" and "too small" in notes[0]


def test_limits_and_direct_adjustments():
    empty = _resolved(n=10)
    adjs = [Adjustment(target="global", ops={"exposure": 0.30}, source="x") for _ in range(3)]
    g = {}
    p = _plan(adjs, [], empty, {}, g)
    assert abs(g["exposure"] - 0.75) < 1e-9
    instructions.report(adjs, p, p.settings)
    assert all(a.status == "clamped" for a in adjs)
    g = {}
    _plan([Adjustment(target="global", ops={"exposure": 2.0})], [], empty, {}, g)
    assert g["exposure"] == 2.0
    g = {}
    _plan([Adjustment(target="global", ops={"exposure": 9.0})], [], empty, {}, g)
    assert g["exposure"] == 3.0


def _make(path):
    a = np.zeros((128, 128, 3), np.uint8)
    a[:70] = [100, 140, 200]
    a[70:] = [60, 110, 70]
    yy, xx = np.mgrid[0:128, 0:128]
    a[(((xx - 64) / 28) ** 2 + ((yy - 80) / 36) ** 2) <= 1.0] = [200, 150, 120]
    PILImage.fromarray(a, "RGB").save(path, quality=95)


def _run(tmp_path, edit, **kw):
    img = tmp_path / "p.jpg"
    if not img.exists():
        _make(str(img))
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=["sky", "subject"], edit=edit, **kw))
    return pipe.process_one(str(img), do_apply=True)


def test_pipeline_instruction_on_sky_and_absent(tmp_path):
    out, res = _run(tmp_path, "darken the sky, brighten the unicorn")
    base, _ = _run(tmp_path, None)
    assert [a.status for a in res.adjustments] == ["applied", "not_found"]
    assert sum(n.startswith("instruction ") for n in res.notes) == 2
    assert out[:40].mean() < base[:40].mean()
    assert np.abs(out[-20:] - base[-20:]).mean() < 0.01


def test_pipeline_without_instruction_is_identical(tmp_path):
    ref, r0 = _run(tmp_path, None)
    for edit in ("", "   "):
        out, res = _run(tmp_path, edit)
        assert np.array_equal(out, ref) and res.adjustments == []
    assert r0.adjustments == []
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("F2 code ran without --edit"))  # noqa: E731
    saved = instructions.plan, instructions.report
    instructions.plan = instructions.report = boom
    try:
        out, _res = _run(tmp_path, None)
    finally:
        instructions.plan, instructions.report = saved
    assert np.array_equal(out, ref)
    bad = tmp_path / "bad.toml"
    bad.write_text("this is [not toml")
    img = str(tmp_path / "p.jpg")
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=["sky", "subject"],
                                    instructions_path=str(bad)))
    out, _res = pipe.process_one(img, do_apply=True)
    assert np.array_equal(out, ref)
    with pytest.raises(Exception):
        Pipeline.build(RunConfig(backend="stub", edit="brighten the sky",
                                 instructions_path=str(bad)))


class _Fake:
    name = "fake"

    def __init__(self):
        self.asked = []

    def segment(self, img, concepts):
        self.asked.append(list(concepts))
        h, w = img.rgb.shape[:2]

        def rect(r0, r1, c0, c1):
            m = np.zeros((h, w), bool)
            m[int(r0 * h):int(r1 * h), int(c0 * w):int(c1 * w)] = True
            return m
        out = []
        if "subject" in concepts:
            out.append(RegionMask("subject", rect(.2, .9, .2, .8), 0.9, 0.4))
        if "jacket" in concepts:
            out.append(RegionMask("jacket", rect(.4, .6, .4, .6), 0.9, 0.04))
        return out


def _fake_run(tmp_path, edit):
    img = tmp_path / "n.jpg"
    if not img.exists():   # textured, so the global banding gate does not react to the edit
        rng = np.random.default_rng(3)
        a = rng.integers(60, 190, (128, 128, 3), dtype=np.uint8)
        PILImage.fromarray(a, "RGB").save(str(img), quality=95)
    pipe = Pipeline.build(RunConfig(backend="stub", concepts=["subject"], edit=edit))
    pipe.segmenter = _Fake()
    out, res = pipe.process_one(str(img), do_apply=True)
    return pipe.segmenter, out, res


def test_pipeline_overlay_on_a_custom_concept(tmp_path):
    seg0, base, _ = _fake_run(tmp_path, None)
    assert not any("jacket" in c for c in seg0.asked)
    seg, out, res = _fake_run(tmp_path, "brighten the jacket")
    assert any("jacket" in c for c in seg.asked)
    assert "jacket@subject" in {m.target for m in res.masks}
    assert out[64, 64].mean() > base[64, 64].mean()
    far = (int(.3 * 128), int(.3 * 128))   # inside subject, outside jacket
    # not exactly equal: the global stage (dehaze/clarity) reads image-wide statistics
    assert np.abs(out[far] - base[far]).max() < 0.01
    assert np.abs(out[64, 64] - base[64, 64]).max() > 0.05


def test_cli_and_api_carry_edit(tmp_path):
    from types import SimpleNamespace

    def _args(**kw):
        return SimpleNamespace(**{"configs_dir": None, "config": None, **kw})

    assert cli.load_run_config(_args(edit="brighten the sky")).edit == "brighten the sky"
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[run]\nedit = "warm the grass"\n')
    assert cli.load_run_config(_args(config=str(cfg_file))).edit == "warm the grass"
    assert service.make_cfg({"edit": "cool the sky"}).edit == "cool the sky"
    with pytest.raises(ValueError):
        RunConfig(edit="x" * 401)


def test_sidecar_carries_adjustments(tmp_path):
    _out, res = _run(tmp_path, "darken the sky, brighten the unicorn")
    js = res.model_dump_json()
    back = Result.model_validate_json(js)
    assert len(back.adjustments) == 2
    a = back.adjustments[0]
    assert (a.target, a.status) == ("sky", "applied") and a.source and a.ops


def test_split_region_leaves_no_ring(tmp_path):
    img = tmp_path / "flat.jpg"
    PILImage.fromarray(np.full((160, 160, 3), 128, np.uint8), "RGB").save(str(img), quality=95)

    def run(adjustments):
        pipe = Pipeline.build(RunConfig(backend="stub", concepts=["subject"]))
        pipe.segmenter = _Fake()
        return pipe.process_one(str(img), do_apply=True, adjustments=adjustments)[0]

    strong = Adjustment(target="subject", ops={"exposure": 0.7})   # the region carries a strong edit
    base = run([strong])
    out = run([strong, Adjustment(target="jacket", ops={"exposure": 1e-6})])
    assert np.abs(out - base).max() < 0.003


def test_empty_cell_still_claims():
    from autoedit.edit.apply import apply_image
    rng = np.random.default_rng(5)
    img = rng.uniform(0.1, 0.9, (64, 64, 3)).astype(np.float32)
    a1 = np.zeros((64, 64), np.float32)
    a1[10:30, 10:30] = 1.0
    a2 = np.zeros_like(a1)
    a2[35:55, 35:55] = 1.0
    a_bg = np.zeros_like(a1)
    a_bg[5:60, 5:60] = 1.0               # covers the area of a1 as well
    s = {"exposure": 0.5}
    out = apply_image(img, [[(a1, {}), (a2, s)], (a_bg, {"exposure": -0.5})], {})
    assert np.abs(out[10:30, 10:30] - img[10:30, 10:30]).max() < 1e-6
    assert np.abs(out[35:55, 35:55] - img[35:55, 35:55]).max() > 0.01


def test_blocked_instruction_does_not_spill(tmp_path):
    import re
    rules = re.sub(r"(?ms)^\[\w+\.snow\]\n.*?(?=^\[|\Z)", "",
                   open(config_path("rules.toml")).read())
    assert "snow]" not in rules
    rules_file = tmp_path / "rules.toml"
    rules_file.write_text(rules)
    rng = np.random.default_rng(3)
    img = tmp_path / "n.jpg"
    PILImage.fromarray(rng.integers(60, 190, (160, 160, 3), dtype=np.uint8), "RGB").save(str(img), quality=95)

    class Snowy:
        name = "snowy"

        def segment(self, img, concepts):
            h, w = img.rgb.shape[:2]
            snow = np.zeros((h, w), bool)
            snow[:h // 2] = True
            jacket = np.zeros((h, w), bool)
            jacket[int(.4 * h):int(.6 * h), int(.4 * w):int(.6 * w)] = True
            masks = {"snow": RegionMask("snow", snow, 0.9, 0.5),
                     "jacket": RegionMask("jacket", jacket, 0.9, 0.04)}
            return [masks[c] for c in concepts if c in masks]

    def run(edit):
        pipe = Pipeline.build(RunConfig(backend="stub", concepts=["snow"], edit=edit,
                                        rules_path=str(rules_file)))
        pipe.segmenter = Snowy()
        return pipe.process_one(str(img), do_apply=True)[0]

    base, out = run(None), run("warm the jacket")
    assert not np.array_equal(base, out)          # the jacket below the snow line did change
    inside = (slice(int(.41 * 160), int(.46 * 160)), slice(int(.45 * 160), int(.55 * 160)))
    assert np.abs(out[inside] - base[inside]).max() < 1e-4
