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
