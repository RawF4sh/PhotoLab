"""F40: `autoedit run --strict` ends with 1 when a photo failed."""
import os

import numpy as np
from PIL import Image

from autoedit import cli


def _folder(tmp_path, bad):
    src = tmp_path / "in"
    src.mkdir()
    Image.fromarray((np.random.RandomState(2).rand(48, 64, 3) * 255).astype("uint8")).save(src / "good.jpg")
    if bad:
        (src / "bad.jpg").write_bytes(b"not a picture")
    return str(src)


def _run(tmp_path, bad, *extra):
    out = str(tmp_path / "out")
    code = cli.main(["run", _folder(tmp_path, bad), out, "--backend", "stub", *extra])
    return code, out


def test_failed_photo_strict_is_1_else_0(tmp_path):
    code, out = _run(tmp_path / "a", True, "--strict") if (tmp_path / "a").mkdir() is None else None
    assert code == 1 and os.path.exists(os.path.join(out, "good.jpg"))
    (tmp_path / "b").mkdir()
    code, out = _run(tmp_path / "b", True)
    assert code == 0 and os.path.exists(os.path.join(out, "good.jpg"))


def test_good_folder_strict_is_0(tmp_path):
    assert _run(tmp_path, False, "--strict")[0] == 0
