"""Multi-format support: camera RAW extensions are recognized and routed to the rawpy decoder."""

from autoedit.imageio import _decode_raw, is_supported


def test_raw_extensions_supported():
    for ext in (".dng", ".cr2", ".nef", ".arw", ".rw2"):
        assert is_supported(f"/x/photo{ext}")
        assert is_supported(f"/x/PHOTO{ext.upper()}")  # case-insensitive


def test_decode_raw_skips_non_raw():
    assert _decode_raw("/x/photo.jpg") is None      # not a RAW → falls through to Pillow path
    assert _decode_raw("/x/photo.tiff") is None
