"""Offline provenance, byte-bound, resume and split tests. All image bytes are synthetic."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "web_corpus", Path(__file__).resolve().parents[1] / "scripts/web_corpus.py"
)
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


JPEG = b"\xff\xd8\xff"


class Reply(io.BytesIO):
    def __init__(self, data, length=None, headers=None):
        super().__init__(data)
        self.headers = headers if headers is not None else {
            "Content-Length": str(len(data) if length is None else length)
        }


class Transport:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.headers = {}

    def open(self, url):
        self.calls.append(url)
        payload = self.data[url]
        if isinstance(payload, Exception):
            raise payload
        return Reply(payload, headers=self.headers.get(url))


def image(number, kind="blue_hour", author=None):
    url = f"https://upload.wikimedia.org/wikipedia/commons/{number}.jpg"
    return corpus.candidate(
        "commons",
        "L",
        kind,
        "finished",
        url,
        "https://commons.wikimedia.org/wiki/File:synthetic",
        "CC BY-SA 4.0",
        author or f"synthetic {number}",
        day="2001-09-09",
        size=4,
        licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
    )


def test_reject_unapproved_licence_or_filename_before_fetch(tmp_path):
    good = image(1)
    net = Transport()
    for patch in (
        {"licence": "CC BY-NC 4.0"},
        {"file": "../../outside.jpg"},
        {"id": "../../outside"},
        {"url": "file:///tmp/test.jpg"},
        {"page": "https://example.org/file"},
        {"licence_url": "https://creativecommons.org/licenses/by/3.0/"},
    ):
        with pytest.raises(ValueError):
            corpus.fetch(tmp_path, [dict(good, **patch)], ["commons"], {"commons": net})
    with pytest.raises(ValueError):
        corpus.fetch(tmp_path, [good], [], {"commons": net})
    assert net.calls == []


def test_fetch_finishes_atomically_and_can_resume_after_interrupt(tmp_path):
    rows = [image(1), image(2)]
    net = Transport()
    net.data = {rows[0]["url"]: JPEG + b"a", rows[1]["url"]: OSError("interrupted")}
    with pytest.raises(OSError):
        corpus.fetch(tmp_path, rows, ["commons"], {"commons": net})
    assert len(corpus.manifest(tmp_path)) == 1 and not list(tmp_path.rglob("*.part"))
    net.data[rows[1]["url"]] = JPEG + b"b"
    saved = corpus.fetch(tmp_path, rows, ["commons"], {"commons": net})
    assert len(saved) == 2 and net.calls.count(rows[0]["url"]) == 1
    assert corpus.verify(tmp_path) == []
    persisted = corpus.manifest(tmp_path)[1]
    assert (
        persisted["author"] == rows[1]["author"]
        and persisted["licence_url"] == rows[1]["licence_url"]
    )
    assert persisted["sha256"] == hashlib.sha256(JPEG + b"b").hexdigest()
    (tmp_path / persisted["file"]).write_bytes(b"bad!")
    assert len(corpus.verify(tmp_path)) == 1
    with pytest.raises(ValueError):
        corpus.fetch(tmp_path, rows, ["commons"], {"commons": net})


def test_total_and_source_budget_apply_to_repeated_fetches(tmp_path):
    rows = [image(1), image(2)]
    net = Transport()
    net.data = {r["url"]: JPEG + b"a" for r in rows}
    cfg = corpus.config()
    cfg["total_bytes"] = 5
    corpus.fetch(tmp_path, rows, ["commons"], {"commons": net}, cfg)
    corpus.fetch(tmp_path, rows, ["commons"], {"commons": net}, cfg)
    assert len(corpus.manifest(tmp_path)) == 1 and len(net.calls) == 1
    cfg["total_bytes"] = 100
    cfg["commons"]["max_files"] = 1
    assert len(corpus.fetch(tmp_path, rows, ["commons"], {"commons": net}, cfg)) == 1


def test_split_is_frozen_and_excludes_research_and_author_overlap(tmp_path):
    rows = []
    for k, kind in enumerate(corpus.KINDS):
        for n in range(8):
            r = image(k * 100 + n, kind, author=f"author-{n}")
            rows.append({**r, "sha256": hashlib.sha256(r["url"].encode()).hexdigest()})
            path = tmp_path / r["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r["url"].encode())
    corpus.save_manifest(tmp_path, rows)
    out = tmp_path / "frozen"
    split = corpus.split(tmp_path, out, targets=1, panel=1, holdout=1)
    assert corpus.split(tmp_path, out, seed=99) == split
    by_id = {r["id"]: r["author"] for r in rows}
    groups = {
        role: {by_id[i] for x in split.values() for i in x[role]}
        for role in ("holdout", "panel", "target")
    }
    assert not groups["holdout"] & (groups["panel"] | groups["target"])
    assert not groups["panel"] & groups["target"]

    (
        tmp_path
        / next(r["file"] for r in rows if r["id"] in split["control"]["holdout"])
    ).unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        corpus.split(tmp_path, out)


def test_unadvertised_payload_size_and_changed_credit(tmp_path):
    row = image(5)
    row["bytes"] = 0

    class Stream:
        def open(self, url):
            response = Reply(JPEG + b"123")
            response.headers = {}
            return response

    cfg = corpus.config()
    cfg["total_bytes"] = 5
    with pytest.raises(ValueError, match="exceed"):
        corpus.fetch(tmp_path, [row], ["commons"], {"commons": Stream()}, cfg)
    assert not corpus.manifest(tmp_path)
    assert not list(tmp_path.rglob("*.part"))
    cfg["total_bytes"] = 100
    corpus.fetch(tmp_path, [row], ["commons"], {"commons": Stream()}, cfg)
    with pytest.raises(ValueError, match="metadata differs"):
        corpus.fetch(
            tmp_path,
            [dict(row, author="changed")],
            ["commons"],
            {"commons": Stream()},
            cfg,
        )
    assert not corpus.commons_license(
        "CC0", "https://creativecommons.org/licenses/by-nc/4.0/"
    )


def test_discovery_checks_research_subject_and_file_specific_licence(tmp_path):
    rows = []
    for name, subject in (
        ("a0001_sample", "nature"),
        ("a0002_sample", "people"),
        ("a0003_unlisted", "nature"),
    ):
        rows.append(
            f'<tr><td>Subject:<br>{subject}</td><td>Time:<br>night</td><td><a href="img/dng/{name}.dng">raw</a></td><td><a href="img/tiff16_c/{name}.tif">edit</a></td></tr>'
        )
    net = Transport()
    net.data = {
        corpus.FIVEK + "legal/filesAdobe.txt": b"a0001_sample\na0002_sample",
        corpus.FIVEK + "legal/filesAdobeMIT.txt": b"",
        corpus.FIVEK: "".join(rows).encode(),
    }
    pair = corpus.discover_fivek(net, 8)
    assert len(pair) == 2 and {r["role"] for r in pair} == {"before", "after"}
    assert {r["kind"] for r in pair} == {"blue_hour"}
    assert len({r["pair"] for r in pair}) == 1
    c = corpus.config()
    for r in pair:
        assert r["licence_url"].endswith("LicenseAdobe.txt")
        corpus.check_row(r, c, tmp_path)
    corpus.save_manifest(tmp_path, pair[:1])
    assert any("incomplete research pair" in err for err in corpus.verify(tmp_path))


def test_bad_files_fail_alone_and_budget_errors_still_stop(tmp_path):
    rows = [image(n) for n in (1, 2, 3, 4, 5)]
    net = Transport()
    net.data = {
        rows[0]["url"]: JPEG + b"a",
        rows[1]["url"]: JPEG + b"a",  # odd Content-Length
        rows[2]["url"]: JPEG + b"abcdefgh" * 100_000,  # far larger than listed
        rows[3]["url"]: b"<htm",  # a page of the listed size
        rows[4]["url"]: JPEG + b"e",
    }
    net.headers[rows[1]["url"]] = {"Content-Length": "4, 4"}
    net.headers[rows[2]["url"]] = {}
    net.headers[rows[3]["url"]] = {"Content-Type": "application/octet-stream"}
    failures = []
    saved = corpus.fetch(tmp_path, rows, ["commons"], {"commons": net}, None, failures)
    assert [r["id"] for r in saved] == [rows[0]["id"], rows[4]["id"]]
    assert [i for i, _ in failures] == [rows[n]["id"] for n in (1, 2, 3)]
    assert not list(tmp_path.rglob("*.part")) and corpus.verify(tmp_path) == []


def test_unbounded_stream_is_read_only_one_byte_past_listed_size(tmp_path):
    row = image(7)
    seen = []

    class Endless(io.BytesIO):
        headers = {}

        def read(self, n=-1):
            seen.append(n)
            return b"x" * n

    class Net:
        def open(self, url):
            return Endless()

    failures = []
    corpus.fetch(tmp_path, [row], ["commons"], {"commons": Net()}, None, failures)
    assert len(failures) == 1 and sum(seen) <= row["bytes"] + 1


def test_an_approximate_listed_size_is_tolerated_but_not_far_more(tmp_path):
    """The RAW index lists sizes that are a few kB off in either direction; its checksum is exact."""
    def sample(number, payload, listed):
        url = f"https://raw.pixls.us/data/Maker/Model/{number}.cr2"
        return corpus.candidate("rawpixls", "O", "opens", "source", url, "https://raw.pixls.us/", "CC0 1.0",
                                "synthetic", size=listed, sha256=hashlib.sha256(payload).hexdigest())

    near = b"II*\x00" + b"n" * 2000
    far = b"II*\x00" + b"f" * (1_048_576 + 4000)
    rows = [sample(1, near, len(near) - 300), sample(2, far, 1000)]
    net = Transport()
    net.data = {rows[0]["url"]: near, rows[1]["url"]: far}
    failures = []
    corpus.fetch(tmp_path, rows, ["rawpixls"], {"rawpixls": net}, None, failures)
    assert [r["id"] for r in corpus.manifest(tmp_path)] == [rows[0]["id"]]
    assert failures == [(rows[1]["id"], "file is larger than its listed size")]


def test_a_source_that_lists_no_size_still_has_a_ceiling(tmp_path):
    old = corpus.UNLISTED_FILE_BYTES
    corpus.UNLISTED_FILE_BYTES = 10
    try:
        payload = JPEG + b"x" * 20
        row = dict(image(9), bytes=0)
        net = Transport()
        net.data = {row["url"]: payload}
        failures = []
        corpus.fetch(tmp_path, [row], ["commons"], {"commons": net}, None, failures)
    finally:
        corpus.UNLISTED_FILE_BYTES = old
    assert corpus.manifest(tmp_path) == []
    assert failures == [(row["id"], "file is larger than the limit for a source that lists no size")]


def test_unapproved_source_is_refused_before_any_download(tmp_path):
    net = Transport()
    other = dict(image(2), source="fivek")
    with pytest.raises(ValueError):
        corpus.fetch(tmp_path, [image(1), other], ["commons"], {"commons": net})
    assert net.calls == [] and corpus.manifest(tmp_path) == []


def test_not_a_picture_is_refused_by_content():
    assert corpus.not_a_picture(b"\xef\xbb\xbf \n<html>", ".dng")
    assert corpus.not_a_picture(b"  {}", ".jpg") and corpus.not_a_picture(b"[1]", ".dng")
    assert corpus.not_a_picture(b"GIF89a", ".jpg") and corpus.not_a_picture(b"x", ".webp")
    assert corpus.not_a_picture(JPEG + b"1", ".jpeg") is None
    assert corpus.not_a_picture(b"\x89PNG\r\n\x1a\n", ".png") is None
    assert corpus.not_a_picture(b"II*\x00", ".tif") is None
    assert corpus.not_a_picture(b"RIFF\x00\x00\x00\x00WEBP", ".webp") is None
    assert corpus.not_a_picture(b"\x00\x01raw", ".cr2") is None


def test_verify_refuses_a_stored_page(tmp_path):
    row = image(8)
    net = Transport()
    net.data = {row["url"]: JPEG + b"x"}
    saved = corpus.fetch(tmp_path, [row], ["commons"], {"commons": net})
    page = b"<htm"
    (tmp_path / saved[0]["file"]).write_bytes(page)
    saved[0].update(bytes=4, sha256=hashlib.sha256(page).hexdigest())
    corpus.save_manifest(tmp_path, saved)
    assert "document" in corpus.verify(tmp_path)[0]


def test_authors_differing_in_punctuation_share_a_role(tmp_path):
    rows = []
    for k, kind in enumerate(corpus.KINDS):
        for n in range(8):
            spelling = f"Jo. Smith-{n}" if k % 2 else f"jo smith {n}"
            r = image(k * 100 + n, kind, author=spelling)
            rows.append({**r, "sha256": hashlib.sha256(r["url"].encode()).hexdigest()})
    groups = corpus.partition(rows, targets=1, panel=1, holdout=1)
    role_of = {}
    for roles in groups.values():
        for role, chosen in roles.items():
            for r in chosen:
                assert role_of.setdefault(corpus.same_author(r["author"]), role) == role


def test_frozen_split_with_crossing_author_warns_and_goes_on(tmp_path):
    rows = []
    for k, kind in enumerate(corpus.KINDS):
        for n in range(3):
            r = image(k * 100 + n, kind, author="same credit" if n < 2 else f"a{n}")
            rows.append({**r, "sha256": hashlib.sha256(r["url"].encode()).hexdigest()})
            path = tmp_path / r["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r["url"].encode())
    corpus.save_manifest(tmp_path, rows)
    out = tmp_path / "frozen"
    out.mkdir()
    ids = {k: [r["id"] for r in rows if r["kind"] == k] for k in corpus.KINDS}
    frozen = {
        k: {"target": [v[0]], "panel": [v[1]], "holdout": [v[2]]} for k, v in ids.items()
    }
    (out / "split.json").write_text(json.dumps(frozen))
    assert corpus.split(tmp_path, out) == frozen
