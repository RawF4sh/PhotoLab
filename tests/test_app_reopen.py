"""Saved-workspace reopening in the real JavaScript modules, with a fake local transport."""
from pathlib import Path
import shutil
import subprocess


def test_folder_reopen_recovers_access_without_losing_saved_edits():
    node = shutil.which("node")
    if node is None:  # Node is an optional development tool, as in test_next_page.py.
        return
    script = Path(__file__).parent / "js" / "folder_reopen.cjs"
    result = subprocess.run([node, "--experimental-vm-modules", str(script)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
