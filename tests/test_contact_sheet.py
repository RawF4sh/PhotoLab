"""Panel-mode contact sheets: both arms render from their own configs dir, the per-row A/B
order lives only in key.json, and blind verdicts tally back to Better/Same/Worse."""

import importlib.util
import json
import pathlib
import re
import shutil

import numpy as np
from PIL import Image as PILImage

from autoedit import paths

_CS = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "contact_sheet.py"


def _module():
    spec = importlib.util.spec_from_file_location("contact_sheet", _CS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_panel_sheets_are_blind_and_the_key_scores_verdicts(tmp_path):
    cs = _module()
    rng = np.random.default_rng(0)
    lines = []
    for name in ("one", "two", "three"):
        src = tmp_path / f"{name}.jpg"
        PILImage.fromarray((rng.random((120, 180, 3)) * 255).astype(np.uint8)).save(src)
        lines.append(f"{src}\t{src}")
    (tmp_path / "panel.txt").write_text("# frozen\n" + "\n".join(lines) + "\n")
    (tmp_path / "crops.txt").write_text("one\t0.5\t0.5\tcentre\n")
    best, cand = tmp_path / "best", tmp_path / "cand"
    shutil.copytree(paths._packaged(), best)
    shutil.copytree(best, cand)
    looks = (cand / "looks.toml").read_text()
    stronger = re.sub(r"(\[natural\][^\[]*?curve_add\s*=\s*)[\d.]+", r"\g<1>40.0", looks)
    assert stronger != looks
    (cand / "looks.toml").write_text(stronger)

    out = tmp_path / "sheets"
    files = cs.build_panel(str(tmp_path / "panel.txt"), str(best), str(cand), str(out),
                           crops_path=str(tmp_path / "crops.txt"), seed=3)
    key = json.loads((out / "key.json").read_text())
    assert set(key["rows"]) == {"one", "two", "three"}
    assert len(files["blind"]) == len(files["captioned"]) == len(files["crops"]) == 1
    assert key["renders"]["best"] != key["renders"]["candidate"]  # each configs dir has its own cache
    facts = [line.split("\t") for line in (out / "facts.txt").read_text().splitlines()]
    assert {stem for stem, _ in facts} == set(key["rows"])
    assert sum(fact.startswith("diff ") for _, fact in facts) == 3           # one tone/colour line per row
    assert [fact.split(":")[0] for stem, fact in facts if stem == "one"][1:] == ["crop centre"]
    assert "candidate" not in (out / "facts.txt").read_text()            # facts never unblind

    replay = cs.build_panel(str(tmp_path / "panel.txt"), str(best), str(cand), str(tmp_path / "replay"),
                            crops_path=str(tmp_path / "crops.txt"), seed=9, cached_only=True)
    assert len(replay["blind"]) == 1
    assert json.loads((tmp_path / "replay/key.json").read_text())["seed"] == 9
    cs.build_panel(str(tmp_path / "panel.txt"), str(best), str(cand), str(tmp_path / "unseeded"),
                   crops_path=str(tmp_path / "crops.txt"), cached_only=True)
    drawn = json.loads((tmp_path / "unseeded/key.json").read_text())["seed"]
    assert isinstance(drawn, int) and 0 <= drawn < 2**32 and drawn != 1  # recorded, not the old default

    candidate_side = {}
    for stem, row in key["rows"].items():
        assert {row["A"], row["B"]} == {"best", "candidate"}
        side = candidate_side[stem] = "A" if row["A"] == "candidate" else "B"
        sidecar = json.loads((pathlib.Path(key["renders"]["candidate"]) / f"{stem}.autoedit.json").read_text())
        curve = f"curv {sidecar['global_settings']['curve']:+.1f}"
        assert curve in row["caption_" + side]                       # the key points at the right arm
        assert curve not in row["caption_" + "AB".replace(side, "")]

    other = "B" if candidate_side["two"] == "A" else "A"
    (out / "verdicts.txt").write_text(
        f"# blind\none {candidate_side['one']} deeper blacks\ntwo {other} flatter\nthree = no visible change\n")
    assert cs.tally(str(out / "verdicts.txt")) == {"Better": 1, "Worse": 1, "Same": 1}


def test_reference_distances_use_aligned_cells_and_the_same_region_masks():
    cs = _module()
    original = PILImage.new("RGB",(120,120),(120,120,120))
    shifted = original.copy()
    shifted.paste((40,65,120),(0,0,60,120))
    mask = np.zeros((240,240),dtype=bool)
    mask[:,:120]=True
    exact = cs.reference_distance(original,original,{"left":mask})
    worse = cs.reference_distance(shifted,original,{"left":mask,"right":~mask})
    assert exact["cells_ev"]==0 and exact["hue_degrees"] is None
    assert worse["cells_ev"]>0 and worse["chroma"]>0
    assert worse["regions_ev"]["left"] > 0 and worse["regions_ev"]["right"] == 0
    assert not cs.reference_distance(original.resize((200,60)),original)["comparable"]


def test_overlapping_render_workers_publish_one_complete_cache_entry(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import argparse
    import time
    from unittest.mock import patch
    import pytest
    from PIL import Image

    cs = _module()
    src = tmp_path / "one.png"
    Image.new("RGB", (60, 40), "teal").save(src)
    config = pathlib.Path(__file__).resolve().parents[1] / "src/autoedit/configs"
    pipe = cs.Pipeline.build(cs.load_run_config(argparse.Namespace(configs_dir=str(config), backend="stub")))
    real = pipe.process_one
    runs = []

    def slower(*args, **kw):
        runs.append(1)
        time.sleep(.04)
        return real(*args, **kw)

    cache = tmp_path / "cache"
    with patch.object(cs.emit, "write_sidecar", side_effect=OSError("interrupted")):
        with pytest.raises(OSError, match="interrupted"):
            cs._render(pipe, str(src), str(cache))
    assert not list(cache.glob("*.jpg")) and not list(cache.glob("*.npz"))
    with patch.object(pipe, "process_one", side_effect=slower), ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(cs._render, pipe, str(src), str(cache)) for _ in range(2)]
        assert all(pathlib.Path(result.result()[0]).is_file() for result in results)
    assert len(runs) == 1 and len(list(cache.glob("*.autoedit.json"))) == 1
