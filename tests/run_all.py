"""Zero-dependency test runner. Runs the pytest-style tests without needing pytest.

    PYTHONPATH=src python tests/run_all.py

When pytest IS installed, `python -m pytest` collects the same files normally.
"""

import importlib.util
import inspect
import os
import pathlib
import sys
import tempfile
import traceback


def _temp_root() -> str:
    """Where per-test tmp dirs live. Honors AUTOEDIT_TMPDIR, else a repo-local .tmp — so the
    suite runs in sandboxes where the system temp dir is unavailable (Codex review F8)."""
    env = os.environ.get("AUTOEDIT_TMPDIR")
    base = env or str(pathlib.Path(__file__).resolve().parent.parent / ".tmp")
    os.makedirs(base, exist_ok=True)
    return base


def _install_pytest_shim():
    if "pytest" in sys.modules:
        return
    try:
        import pytest  # noqa: F401
        return
    except ImportError:
        pass
    import types

    shim = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, exc):
            self.exc = exc

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"did not raise {self.exc}")
            return issubclass(et, self.exc)

    shim.raises = lambda exc: _Raises(exc)
    sys.modules["pytest"] = shim


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "src"))
    _install_pytest_shim()

    temp_root = _temp_root()
    test_files = sorted((root / "tests").glob("test_*.py"))
    passed = failed = 0
    for tf in test_files:
        spec = importlib.util.spec_from_file_location(tf.stem, tf)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            kwargs = {}
            if "tmp_path" in inspect.signature(fn).parameters:
                kwargs["tmp_path"] = pathlib.Path(tempfile.mkdtemp(dir=temp_root))
            try:
                fn(**kwargs)
                passed += 1
                print(f"PASS {tf.name}::{name}")
            except Exception:
                failed += 1
                print(f"FAIL {tf.name}::{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
