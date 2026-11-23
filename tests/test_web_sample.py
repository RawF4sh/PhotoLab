from PIL import Image

from autoedit.web.sample import sample_folder


def test_samples_are_local_reusable_images(tmp_path):
    from pathlib import Path
    folder = Path(sample_folder(str(tmp_path)))
    pictures = sorted(folder.glob("*.jpg"))
    assert len(pictures) == 3
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in pictures]
    for p in pictures:
        with Image.open(p) as photo:
            assert photo.size == (1200, 800)
            assert "generated demo" in photo.getexif()[270]
    from autoedit.consistency import capture_time
    times = [capture_time(str(p)) for p in pictures]
    assert all(t is not None for t in times)
    assert times[1]-times[0] == times[2]-times[1] == 1
    assert sample_folder(str(tmp_path)) == str(folder)
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in pictures]
