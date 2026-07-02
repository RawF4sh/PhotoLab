"""Studio API filesystem trust boundary (Codex review F4): paths must descend from a folder
the user picked this session; everything else is rejected."""

import os

import pytest

from autoedit.web import service


def test_path_outside_approved_roots_rejected(tmp_path):
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    inside = tmp_path / "a.jpg"
    inside.write_bytes(b"x")

    with pytest.raises(PermissionError):           # nothing approved yet
        service.ensure_allowed(str(inside))

    service.approve_root(str(tmp_path))            # picker approved the folder
    service.ensure_allowed(str(inside))            # now a file under it is allowed
    assert service.path_allowed(str(tmp_path / "sub" / "b.jpg"))  # nested too

    with pytest.raises(PermissionError):           # a sibling outside the root stays blocked
        service.ensure_allowed(os.path.join(os.path.dirname(str(tmp_path)), "outside.jpg"))


def test_approve_file_approves_its_folder(tmp_path):
    service._ALLOWED_ROOTS.clear()
    f = tmp_path / "pic.jpg"
    f.write_bytes(b"x")
    service.approve_root(str(f))                   # picked a single file
    assert service.path_allowed(str(tmp_path / "other.jpg"))  # its folder is the root


def test_allow_any_path_env_opt_out(tmp_path):
    service._ALLOWED_ROOTS.clear()
    os.environ["AUTOEDIT_ALLOW_ANY_PATH"] = "1"
    try:
        assert service.path_allowed("/etc/passwd")  # explicit opt-out disables the boundary
    finally:
        os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)


def test_empty_string_path_is_rejected_but_none_is_skipped(tmp_path):
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    service.approve_root(str(tmp_path))
    with pytest.raises(PermissionError):
        service.ensure_allowed("")        # "" resolves to CWD — must NOT fail open (regression)
    service.ensure_allowed(None)          # None = optional/unset → skipped, no raise


def test_dotdot_traversal_is_rejected(tmp_path):
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    root = tmp_path / "approved"
    root.mkdir()
    service.approve_root(str(root))
    with pytest.raises(PermissionError):  # realpath collapses .. before containment check
        service.ensure_allowed(str(root / ".." / "outside.jpg"))


def test_symlink_escaping_root_is_rejected(tmp_path):
    service._ALLOWED_ROOTS.clear()
    os.environ.pop("AUTOEDIT_ALLOW_ANY_PATH", None)
    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.jpg").write_bytes(b"x")
    service.approve_root(str(root))
    link = root / "escape"
    os.symlink(str(outside), str(link))   # approved/escape -> outside (a real symlink escape)
    with pytest.raises(PermissionError):
        service.ensure_allowed(str(link / "secret.jpg"))


def test_app_rejects_nonlocal_host_and_cross_origin():
    """DNS-rebinding (Host) + CSRF (Origin) middleware defenses. Skipped if the [ui] extra
    (fastapi/httpx) isn't installed, so the zero-dep runner stays green everywhere."""
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return
    from autoedit import paths
    from autoedit.web import app as webapp

    client = TestClient(webapp.create_app(paths.configs_dir(), local_only=True),
                        base_url="http://localhost")
    assert client.get("/api/styles").status_code == 200                     # loopback Host ok
    assert client.get("/api/styles", headers={"Host": "evil.com"}).status_code == 403  # rebinding blocked (GET too)
    r = client.post("/api/preview", json={}, headers={"Origin": "http://evil.com"})
    assert r.status_code == 403                                             # cross-origin mutation blocked
