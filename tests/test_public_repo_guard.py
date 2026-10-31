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
                 "models/model.safetensors", "nested/edit.autoedit.json"]:
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
