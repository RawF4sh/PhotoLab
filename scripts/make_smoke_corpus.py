"""Create small deterministic smoke photographs without overwriting existing files."""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from PIL import Image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="test-corpus")
    args = parser.parse_args(argv)
    folder = Path(args.directory)
    folder.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:192, 0:256].astype(np.float32)
    ramp = x/255
    dark = np.stack([0.04+0.25*ramp, 0.03+0.23*ramp, 0.03+0.20*ramp], axis=-1)
    normal = np.stack([0.18+0.60*ramp, 0.20+0.55*ramp, 0.22+0.51*ramp], axis=-1)
    for name, rgb in (("quality-smoke-dark.jpg", dark), ("quality-smoke-neutral.jpg", normal)):
        dest = folder/name
        if dest.exists():
            raise FileExistsError(f"refusing to overwrite {dest}")
        Image.fromarray(np.rint(rgb*255).astype(np.uint8)).save(dest, quality=98, subsampling=0)
        print(dest.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
