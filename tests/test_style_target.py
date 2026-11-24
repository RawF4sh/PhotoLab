"""Owner target lists and the isolated start of scripts/style_target.py. No network, no corpus."""

import importlib.util
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
