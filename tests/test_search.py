import json
import xml.etree.ElementTree as ET
from pathlib import Path

from autoedit import cli, search

NS = {"dc": "http://purl.org/dc/elements/1.1/", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}


def _side(d: Path, name, scene, concepts, kinds=(), image=True):
    img = d / f"{name}.jpg"
    if image:
        img.write_bytes(b"x")
    data = {"image": str(img), "scene": scene, "audit": {"output": str(img)} if image else {},
            "concepts": [{"name": n, "present": c > 0, "score": 1, "coverage": c} for n, c in concepts.items()],
            "reveal": {"picture": "checked", "findings": [
                {"kind": k, "label": "a " + k, "word": k, "score": 1, "box": [0, 0, 1, 1], "share": 0.1} for k in kinds]}}
    (d / f"{name}.autoedit.json").write_text(json.dumps(data))
    return img


def _folder(tmp_path):
    _side(tmp_path, "a", "landscape", {"mountain lake": 0.3, "flake": 0.5, "tree": 0.01})
    _side(tmp_path, "b", "portrait", {"lake": 0.1}, kinds=["face"])
    _side(tmp_path, "c", "landscape", {"lake": 0.4})
    (tmp_path / "bad.autoedit.json").write_text("{nope")
    return str(tmp_path)


def test_index(tmp_path):
    e, skipped = search.index(_folder(tmp_path))
    assert len(e) == 3 and skipped == 1


def test_find(tmp_path):
    e, _ = search.index(_folder(tmp_path))
    names = lambda r: [Path(x["image"]).stem for x in r]
    assert names(search.find(e, ["lake"], 0.02)) == ["c", "a", "b"] or names(search.find(e, ["lake"], 0.02)) == ["c", "a", "b"]
    assert names(search.find(e, ["lake", "landscape"], 0.02)) == ["c", "a"]  # AND + scene
    assert names(search.find(e, ["tree"], 0.02)) == []                       # under min_coverage
    assert names(search.find(e, ["face"], 0.02)) == ["b"]                    # reveal kind
    assert names(search.find(e, ["A FACE"], 0.02)) == ["b"]                  # label, case-insensitive
    assert names(search.find(e, ["lak"], 0.02)) == []                        # whole words only


def test_keywords_no_reveal(tmp_path):
    e, _ = search.index(_folder(tmp_path))
    b = [x for x in e if x["image"].endswith("b.jpg")][0]
    assert search.keywords(b, 0.02) == ["lake", "portrait"]
    a = [x for x in e if x["image"].endswith("a.jpg")][0]
    assert search.keywords(a, 0.02) == ["flake", "landscape", "mountain lake"]


def test_write_xmp(tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"x")
    p = search.write_xmp(str(img), ['a<&"', "lake"])
    li = [x.text for x in ET.parse(p).getroot().iterfind(".//dc:subject/rdf:Bag/rdf:li", NS)]
    assert li == ['a<&"', "lake"] and img.read_bytes() == b"x"


def test_cmd_find(tmp_path):
    f = _folder(tmp_path)
    out, code = search.cmd_find(f, ["face"], 0.02, False)
    assert code == 0 and out.splitlines() == [str(tmp_path / "b.jpg"), "1 of 3 photos"]
    out, code = search.cmd_find(f, ["zebra"], 0.02, False)
    assert code == 1 and out == "0 of 3 photos"
    out, code = search.cmd_find(f, ["face"], 0.02, True)
    assert code == 0 and json.loads(out)[0]["scene"] == "portrait"


def test_cmd_keywords(tmp_path):
    f = _folder(tmp_path)
    (tmp_path / "b.xmp").write_text("<foreign/>")
    out = search.cmd_keywords(f, 0.02, True)
    assert not (tmp_path / "a.xmp").exists() and "a.jpg: flake" in out
    out = search.cmd_keywords(f, 0.02, False)
    assert (tmp_path / "a.xmp").exists() and (tmp_path / "c.xmp").exists()
    assert (tmp_path / "b.xmp").read_text() == "<foreign/>" and "skipped" in out
    search.cmd_keywords(f, 0.02, False)  # own xmp is overwritten, no error


def test_cli_find_and_keywords(tmp_path):
    f = _folder(tmp_path)
    assert cli.main(["find", f, "face"]) == 0
    assert cli.main(["find", f, "zebra"]) == 1
    assert cli.main(["keywords", f, "--dry-run", "--min-coverage", "0.2"]) == 0
    assert not (tmp_path / "a.xmp").exists()


def test_keywords_skip_reveal_words_and_guard_concepts(tmp_path):
    _side(tmp_path, "p", "people", {"person": 0.3, "skin": 0.2, "subject": 0.3, "lake": 0.4})
    e, _ = search.index(str(tmp_path))
    assert search.keywords(e[0], 0.02) == ["lake"]
    search.cmd_keywords(str(tmp_path), 0.02, False)
    assert [x.text for x in ET.parse(tmp_path / "p.xmp").getroot().iterfind(".//dc:subject/rdf:Bag/rdf:li", NS)] == ["lake"]


def test_dry_run_sidecar_has_no_image_and_gets_no_xmp(tmp_path):
    _side(tmp_path, "d", "landscape", {"lake": 0.4}, image=False)
    (tmp_path / "d.jpg").write_bytes(b"source photo beside the sidecar")
    e, _ = search.index(str(tmp_path))
    assert e[0]["image"] is None
    out = search.cmd_keywords(str(tmp_path), 0.02, False)
    assert not (tmp_path / "d.xmp").exists() and "skipped" in out
    text, code = search.cmd_find(str(tmp_path), ["lake"], 0.02, False)
    assert code == 0 and text.splitlines()[0] == str(tmp_path / "d.autoedit.json")
