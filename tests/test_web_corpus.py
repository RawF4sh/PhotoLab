"""Offline provenance, byte-bound, resume and split tests. All image bytes are synthetic."""

import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "web_corpus", Path(__file__).resolve().parents[1] / "scripts/web_corpus.py"
)
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


class Reply(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


class Transport:
    def __init__(self):
        self.data = {}
        self.calls = []

    def open(self, url):
        self.calls.append(url)
        payload = self.data[url]
        if isinstance(payload, Exception):
            raise payload
        return Reply(payload)


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
    net.data = {rows[0]["url"]: b"abcd", rows[1]["url"]: OSError("interrupted")}
    with pytest.raises(OSError):
        corpus.fetch(tmp_path, rows, ["commons"], {"commons": net})
    assert len(corpus.manifest(tmp_path)) == 1 and not list(tmp_path.rglob("*.part"))
    net.data[rows[1]["url"]] = b"1234"
    saved = corpus.fetch(tmp_path, rows, ["commons"], {"commons": net})
    assert len(saved) == 2 and net.calls.count(rows[0]["url"]) == 1
    assert corpus.verify(tmp_path) == []
    persisted = corpus.manifest(tmp_path)[1]
    assert (
        persisted["author"] == rows[1]["author"]
        and persisted["licence_url"] == rows[1]["licence_url"]
    )
    assert persisted["sha256"] == hashlib.sha256(b"1234").hexdigest()
    (tmp_path / persisted["file"]).write_bytes(b"bad!")
    assert len(corpus.verify(tmp_path)) == 1
    with pytest.raises(ValueError):
        corpus.fetch(tmp_path, rows, ["commons"], {"commons": net})


def test_total_and_source_budget_apply_to_repeated_fetches(tmp_path):
    rows = [image(1), image(2)]
    net = Transport()
    net.data = {r["url"]: b"abcd" for r in rows}
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
            response = Reply(b"123456")
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
