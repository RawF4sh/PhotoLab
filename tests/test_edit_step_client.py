"""Check the client-side selection and history contract across an app restart."""
from pathlib import Path
import shutil
import subprocess


def test_selected_suggestions_survive_restart_and_undo():
    node = shutil.which("node")
    if node is None:
        return
    script = Path(__file__).parent / "js" / "edit_step_selection.cjs"
    result = subprocess.run([node, "--experimental-vm-modules", str(script)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
