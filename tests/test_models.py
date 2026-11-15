"""F14: model manager. A stand-in replaces models._hub: nothing here touches the network or the
real Hugging Face cache."""

import contextlib
import io
import types

from autoedit import cli, models

GB = 3_400_000_000


class GatedRepoError(Exception):
    pass


class _Hub:
    def __init__(self, repos=None, fail=None):
        self.repos = repos if repos is not None else []
        self.fail = fail
        self.downloads = []
        self.deleted = []
        self.scans = 0

    def scan_cache_dir(self):
        self.scans += 1
        hub = self
        def delete_revisions(*hashes):
            return types.SimpleNamespace(execute=lambda: hub.deleted.append(hashes))
        return types.SimpleNamespace(repos=self.repos, delete_revisions=delete_revisions)

    def snapshot_download(self, repo_id):
        self.downloads.append(repo_id)
        if self.fail:
            raise self.fail
        return "/cache/sam3"


def _repo(repo_id="facebook/sam3", hashes=("a1", "b2")):
    return types.SimpleNamespace(
        repo_id=repo_id, repo_type="model", size_on_disk=GB, repo_path="/cache/x",
        revisions=[types.SimpleNamespace(commit_hash=h) for h in hashes])


def _with(hub, fn, find_spec=lambda name: object()):
    old_hub, old_spec = models._hub, models.importlib.util.find_spec
    models._hub, models.importlib.util.find_spec = (lambda: hub), find_spec
    try:
        return fn()
    finally:
        models._hub, models.importlib.util.find_spec = old_hub, old_spec


def _cli(hub, argv):
    buf, err = io.StringIO(), io.StringIO()
    def go():
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            return cli.main(argv)
    code = _with(hub, go)
    return code, buf.getvalue() + err.getvalue()


def test_status_cached_and_not():
    st = _with(_Hub([_repo()]), models.status)
    assert st["cached"] and st["size_bytes"] == GB and st["revisions"] == 2
    assert not _with(_Hub([]), models.status)["cached"]

    hub = _Hub([_repo()])
    st = _with(hub, models.status, find_spec=lambda name: None)
    assert not st["hub"] and not st["cached"] and hub.scans == 0


def test_download_calls_the_hub():
    hub = _Hub()
    assert _with(hub, models.download) == "/cache/sam3"
    assert hub.downloads == ["facebook/sam3"]


def test_download_explains_a_gated_repo():
    import pytest

    for exc in (GatedRepoError("nope"), Exception("HTTP 401 Unauthorized")):
        with pytest.raises(PermissionError) as e:
            _with(_Hub(fail=exc), models.download)
        assert models.LICENCE_URL in str(e.value) and "huggingface-cli login" in str(e.value)
    with pytest.raises(ValueError):
        _with(_Hub(fail=ValueError("other")), models.download)


def test_remove_deletes_every_revision():
    other = _repo("someone/else", ("z9",))
    hub = _Hub([other, _repo()])
    assert _with(hub, models.remove) == GB
    assert hub.deleted == [("a1", "b2")]
    hub = _Hub([other])
    assert _with(hub, models.remove) == 0 and hub.deleted == []


def test_cli_models():
    hub = _Hub([_repo()])
    code, out = _cli(hub, ["models", "status"])
    assert code == 0 and "weights:" in out
    code, out = _cli(hub, ["models", "remove"])
    assert code == 1 and hub.deleted == []
    code, out = _cli(hub, ["models", "remove", "--yes"])
    assert code == 0 and hub.deleted == [("a1", "b2")]
    code, out = _cli(_Hub(fail=GatedRepoError("x")), ["models", "download"])
    assert code == 2 and models.LICENCE_URL in out


def test_missing_packages():
    def missing():
        raise RuntimeError("the SAM 3 backend is not installed: pip install '.[sam]'")
    old, old_spec = models._hub, models.importlib.util.find_spec
    models._hub = missing
    try:
        for argv in (["models", "download"], ["models", "remove"], ["models", "remove", "--yes"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                assert cli.main(argv) == 2
            assert "pip install" in buf.getvalue()
        with contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(["models", "status"]) == 0
    finally:
        models._hub, models.importlib.util.find_spec = old, old_spec
