"""Original-first guided opening and the image-aligned comparison control."""
from pathlib import Path
import shutil
import subprocess


def test_guided_browsing_explicit_preview_and_comparison():
    node = shutil.which("node")
    if node is None:
        return
    script = Path(__file__).parent / "js" / "guided_photo.cjs"
    result = subprocess.run([node, "--experimental-vm-modules", str(script)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
