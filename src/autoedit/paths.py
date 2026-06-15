"""Locate config assets so the tool works both from the repo and pip-installed.

Resolution order for the configs directory:
  1. $AUTOEDIT_CONFIGS_DIR (explicit override)
  2. ./configs in the current working directory (a user's local tuning copy)
  3. the configs bundled inside the installed package (the default)
"""

from __future__ import annotations

import os


def _packaged() -> str:
    return os.path.join(os.path.dirname(__file__), "configs")


def configs_dir() -> str:
    env = os.environ.get("AUTOEDIT_CONFIGS_DIR")
    if env:
        return env
    if os.path.isdir("configs"):
        return "configs"
    return _packaged()


def config_path(name: str) -> str:
    return os.path.join(configs_dir(), name)
