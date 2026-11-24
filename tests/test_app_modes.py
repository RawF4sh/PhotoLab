"""Automatic follows the Settings sheet, and the Photo editor asks before it downloads its editor."""
from pathlib import Path
import shutil
import subprocess


def _run(name):
    node = shutil.which("node")
    if node is None:
        return
    script = Path(__file__).parent / "js" / name
    result = subprocess.run([node, "--experimental-vm-modules", str(script)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_automatic_sends_the_settings_and_no_guided_folder_choice():
    _run("automatic_settings.cjs")


def test_editor_download_waits_for_a_click_and_names_its_size():
    _run("editor_download.cjs")
