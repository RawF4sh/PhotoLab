"""Zero-dependency test runner. Runs the pytest-style tests without needing pytest.

    PYTHONPATH=src python tests/run_all.py                      # every test file
    PYTHONPATH=src python tests/run_all.py test_ops.py ...      # only the named files

When pytest IS installed, `python -m pytest` collects the same files normally.

A test that needs an optional library says so with `pytest.importorskip("name")` (or
`pytest.skip("why")`). It is then printed as SKIP and counted apart, so a run on an interpreter
without that library does not report it as passed.
"""

import importlib.util
import inspect
import os
import pathlib
import re
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


class _Skip(Exception):
    """Raised by the stand-in's `skip` and `importorskip`."""


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
        def __init__(self, exc, match=None):
            self.exc, self.match = exc, match

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"did not raise {self.exc}")
            self.value = ev   # like pytest: `with pytest.raises(E) as e: ...; str(e.value)`
            if self.match and issubclass(et, self.exc) and not re.search(self.match, str(ev)):
                raise AssertionError(f"{ev!r} does not match {self.match!r}")
            return issubclass(et, self.exc)

    builtin_abs = abs

    class _Approx:   # pytest.approx for plain numbers, with pytest's default tolerances
        def __init__(self, want, rel=None, abs=None):
            self.want = want
            self.tol = max((1e-6 if rel is None else rel) * builtin_abs(want), 1e-12 if abs is None else abs)

        def __eq__(self, got):
            return builtin_abs(got - self.want) <= self.tol

        def __repr__(self):
            return f"approx({self.want!r})"

    def _skip(reason=""):
        raise _Skip(reason)

    def _importorskip(modname, reason=None):
        try:
            return importlib.import_module(modname)
        except ImportError:
            raise _Skip(reason or f"could not import {modname!r}") from None

    _skip.Exception = _Skip
    shim.raises = lambda exc, match=None: _Raises(exc, match)
    shim.approx = _Approx
    shim.skip, shim.importorskip = _skip, _importorskip
    sys.modules["pytest"] = shim


def main(argv=None) -> int:
    os.environ["AUTOEDIT_PROFILE"] = ""   # a developer's own profile must not change results
    os.environ["AUTOEDIT_NO_LOCAL_MODEL"] = "1"   # no test may reach a real model on the developer's machine
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "src"))
    _install_pytest_shim()
    # Real pytest signals a skip with a BaseException of its own; the stand-in uses _Skip.
    skips = tuple({_Skip, getattr(getattr(sys.modules["pytest"], "skip", None), "Exception", _Skip)})
    # Real pytest also reports "did not raise" and pytest.fail() with a BaseException: a failure here.
    fails = (Exception, getattr(getattr(sys.modules["pytest"], "fail", None), "Exception", Exception))

    temp_root = _temp_root()
    test_files = sorted((root / "tests").glob("test_*.py"))
    wanted = list(sys.argv[1:] if argv is None else argv)
    if wanted:
        names = {pathlib.Path(w).name if w.endswith(".py") else pathlib.Path(w).name + ".py" for w in wanted}
        unknown = sorted(names - {tf.name for tf in test_files})
        if unknown:
            print("no such test file: " + ", ".join(unknown))
            return 2
        test_files = [tf for tf in test_files if tf.name in names]
    passed = failed = skipped = 0
    for tf in test_files:
        spec = importlib.util.spec_from_file_location(tf.stem, tf)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except skips as why:     # the whole file needs something this interpreter lacks
            skipped += 1
            print(f"SKIP {tf.name}: {why}")
            continue
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
            except skips as why:
                skipped += 1
                print(f"SKIP {tf.name}::{name}: {why}")
            except fails:
                failed += 1
                print(f"FAIL {tf.name}::{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
