"""Run the browser module's regression cases for reassigning recipes across folder plans."""
import shutil
import subprocess
from pathlib import Path


def test_folder_recipe_rerouting_preserves_manual_changes():
    node = shutil.which("node")
    if node:
        script = Path(__file__).parent / "js" / "recipe_rerouting.cjs"
        subprocess.run([node, "--experimental-vm-modules", str(script)], check=True, timeout=30)
