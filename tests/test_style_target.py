"""Owner target lists and the isolated start of scripts/style_target.py. No network, no corpus."""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("style_target_tool", ROOT / "scripts/style_target.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def listed(tmp_path, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    lst = tmp_path / "list.txt"
    lst.write_text(str(path) + "\n")
    return lst


def test_owner_files_accepts_only_the_owners_own_photos(tmp_path):
    web = tmp_path / "curated-web"
    web.mkdir()
    (web / "manifest.csv").write_text("id,track,kind,role,pair,file,source,page,licence,author,sha256,bytes,fetched,query\n")
    mine = web / "owner/photos/IMG_0001.jpg"
    assert st.owner_files(listed(tmp_path, mine)) == [mine.resolve()]
    elsewhere = tmp_path / "pictures/IMG_0002.jpg"
    assert st.owner_files(listed(tmp_path, elsewhere)) == [elsewhere.resolve()]
    prints = tmp_path / "prints"
    prints.mkdir()
    (prints / "manifest.csv").write_text("photo,slot\n")      # another tool's manifest: not a corpus
    own = prints / "IMG_0003.jpg"
    assert st.owner_files(listed(tmp_path, own)) == [own.resolve()]
    refused = [
        web / "owner/brief-1.jpg",  # a brief placed straight into owner/
        web / "pairs/pair-0000000000000000/fivek-aa.tif",
        web / "looks/sunset/commons-aa.jpg",  # no SOURCES.md needed to be refused
        tmp_path / "out/cache/fivek-0123456789abcdef0123.jpg",  # a corpus id outside it
        tmp_path / "out/commons-0123456789abcdef0123.png",
        tmp_path / "out/commons-0123456789abcdef0123 (1).png",  # a renamed copy keeps the id in front
    ]
    for path in refused:
        with pytest.raises(ValueError):
            st.owner_files(listed(tmp_path, path))


def test_script_starts_isolated(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("id\n")
    run = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/style_target.py"), "--manifest", str(manifest),
         "--list", str(tmp_path / "missing.txt"), "--style", "blue_hour", "--out", str(tmp_path / "t.toml"),
         "--backend", "stub"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert "ModuleNotFoundError" not in run.stderr and "ImportError" not in run.stderr


def target_group(tmp_path, count=14):
    """A frozen group of `count` target photos with real small files, and its list folder."""
    web = importlib.import_module("web_corpus")
    root = tmp_path / "corpus"
    rows, paths = [], []
    for number in range(count):
        row = web.candidate(
            "commons", "L", "blue_hour", "finished",
            f"https://upload.wikimedia.org/wikipedia/commons/t{number}.jpg",
            "https://commons.wikimedia.org/wiki/File:synthetic", "CC BY-SA 4.0", f"synthetic {number}",
            size=8, licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
        )
        path = root / row["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"photo {number}".encode())
        row["sha256"] = web.digest(path)
        rows.append(row)
        paths.append(path)
    with (root / "manifest.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, web.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    folder = tmp_path / "lists"
    folder.mkdir()
    (folder / "split.json").write_text(json.dumps(
        {"blue_hour": {"target": [r["id"] for r in rows], "panel": ["p1"], "holdout": ["h1"]}}))
    return root / "manifest.csv", folder, paths


def pick(folder, paths):
    lst = folder / "pick.txt"
    lst.write_text("".join(f"{p}\n" for p in paths))
    return lst


def test_a_target_may_be_a_part_of_the_frozen_group(tmp_path):
    manifest, folder, paths = target_group(tmp_path)
    resolved = [p.resolve() for p in paths]
    assert st.finished_files(pick(folder, paths), manifest, "blue_hour") == resolved   # the whole group
    assert st.finished_files(pick(folder, paths[:12]), manifest, "blue_hour") == resolved[:12]
    with pytest.raises(ValueError, match="at least 12"):
        st.finished_files(pick(folder, paths[:11]), manifest, "blue_hour")


def test_a_target_photo_from_outside_the_group_is_refused(tmp_path):
    manifest, folder, paths = target_group(tmp_path)
    stranger = tmp_path / "stranger.jpg"
    stranger.write_bytes(b"not in the group")
    with pytest.raises(ValueError, match="frozen target group"):
        st.finished_files(pick(folder, [*paths[:12], stranger]), manifest, "blue_hour")
