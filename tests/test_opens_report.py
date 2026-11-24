import importlib.util
from pathlib import Path
from PIL import Image

spec = importlib.util.spec_from_file_location(
    "opens_report", Path(__file__).resolve().parents[1] / "scripts/opens_report.py"
)
opens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opens)


def test_each_input_gets_a_measured_result_or_a_reason(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (36, 24), "#476182").save(src / "one.png")
    (src / "broken.jpg").write_bytes(b"broken")
    report = opens.report(src, tmp_path / "out")
    assert len(report) == 2
    bad, good = report
    assert bad["status"] == "failed" and bad["reason"]
    assert good["status"] == "ok" and (good["width"], good["height"]) == (36, 24)
    assert (tmp_path / "out/run" / good["export"]).is_file()
    assert len(opens.report(src, tmp_path / "out", resume=True)) == 2


def test_file_orientation_uses_the_expected_upright_axes(tmp_path):
    import numpy as np

    src = tmp_path / "rotated.jpg"
    values = np.zeros((12, 20, 3), dtype=np.uint8)
    values[2:8, 3:15] = [224, 160, 72]
    image = Image.fromarray(values)
    exif = Image.Exif()
    exif[274] = 6
    image.save(src, exif=exif)
    reference, kind, encoding = opens.reference_for(src)
    assert reference.shape[:2] == (20, 12) and "EXIF 6" in encoding
    assert kind == "display-oriented input"
    assert (
        opens.camera_check(np.rot90(reference, 2), reference)[0]
        == "possible rotation or reflection"
    )


def header_only_png(width, height):
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_an_export_above_the_pixel_limit_never_stops_a_run(tmp_path):
    import csv

    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (36, 24), "#476182").save(src / "a.png")
    Image.new("RGB", (36, 24), "#826147").save(src / "b.png")
    out = tmp_path / "out"
    first = opens.report(src, out)
    rows = list(csv.DictReader((out / "coverage.csv").open()))
    rows[0]["reference"] = ""
    with (out / "coverage.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=opens.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    (out / "run" / rows[0]["export"]).write_bytes(header_only_png(400, 400))
    original = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1000  # 160000 pixels is far past the error threshold
    try:
        assert opens.header_size(out / "run" / rows[0]["export"]) == (400, 400)
        assert Image.MAX_IMAGE_PIXELS == 1000
        resumed = opens.report(src, out, resume=True)
    finally:
        Image.MAX_IMAGE_PIXELS = original
    assert [r["file"] for r in resumed] == [r["file"] for r in first]
    assert all(r["status"] in ("ok", "review") for r in resumed)
