"""Publication guard regressions; all fixture data is synthetic."""
import importlib.util
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_public_repo.py"
SPEC = importlib.util.spec_from_file_location("public_repo_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def test_publication_guard_blocks_private_inputs_and_preserves_code_paths():
    for path in [".env.production", "nested/portrait.JPG", "out-test/run.json",
                 ".claude/settings.json", "eval_3way/scores.csv", "credentials-prod.json",
                 "models/model.safetensors", "nested/edit.autoedit.json", "nested/a0_brackets.merge.json"]:
        assert guard.private_path(path), path
    for path in ["src/autoedit/imageio.py", "src/autoedit/configs/rules.toml",
                 ".github/workflows/privacy.yml", "test-corpus/.gitkeep", ".env.example"]:
        assert not guard.private_path(path), path


def test_publication_guard_detects_deleted_photo_in_history(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c",
             "user.email=fixture@users.noreply.github.com", *args],
            cwd=tmp_path, check=True, capture_output=True,
        )
    git("init", "-q")
    photo = tmp_path / "private.JPG"
    photo.write_text("synthetic fixture, no actual photo")
    git("add", "private.JPG")
    git("commit", "-qm", "Fixture")
    git("rm", "private.JPG")
    git("commit", "-qm", "Remove fixture")
    current = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True)
    history = subprocess.run([sys.executable, str(SCRIPT), "--history"], cwd=tmp_path, capture_output=True)
    assert current.returncode == 0
    assert history.returncode == 1
    assert b"private.JPG" not in history.stdout  # logs disclose counts, not private paths


def test_publication_guard_finds_a_home_folder_path_in_a_tracked_file(tmp_path):
    home = "/" + "Users/"          # built here, so this file holds no such path itself
    assert guard.home_path_lines(f"first\ncd {home}alex/Projects/AutoEdit\n".encode()) == [2]
    assert guard.home_path_lines(("PYTHONPATH=/" + "home/alex/src python3 x.py").encode()) == [1]
    assert guard.home_path_lines(("C:" + chr(92) + "Users" + chr(92) + "alex" + chr(92) + "Pictures").encode()) == [1]
    for harmless in (f"{home}me/Pictures", f"{home}.../x", "https://example.org/home/page", "a/" + "home/x is relative",
                     f"binary\0{home}alex/"):
        assert guard.home_path_lines(harmless.encode()) == [], harmless

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c",
             "user.email=fixture@users.noreply.github.com", *args],
            cwd=tmp_path, check=True, capture_output=True,
        )
    git("init", "-q")
    (tmp_path / "notes.md").write_text(f"Run it from the checkout.\n\ncd {home}alex/Projects/AutoEdit\n")
    git("add", "notes.md")
    run = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True)
    assert run.returncode == 1 and b"notes.md:3" in run.stdout
    assert b"alex" not in run.stdout          # the file and the line, never the path
    (tmp_path / "notes.md").write_text("Run it from the repository root.\n")
    assert subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True).returncode == 0
