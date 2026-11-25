"""Offline provenance, byte-bound, resume and split tests. All image bytes are synthetic."""

import hashlib
import importlib.util
import io
import json
import re
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


# ---- Round two: FiveK pairs by time of day, and the split by shoot -------------------------


def index_row(name, period, subject="nature"):
    return (
        f'<tr><td>Subject:<br>{subject}</td><td>Time:<br>{period}</td>'
        f'<td><a href="img/dng/{name}.dng">raw</a></td>'
        f'<td><a href="img/tiff16_c/{name}.tif">edit</a></td></tr>'
    )


def made_up_index(counts, listed_extra=()):
    """counts: {period: n}. Returns a transport whose index has that many eligible pairs each."""
    rows, names = [], []
    for period, n in counts.items():
        for i in range(n):
            name = f"a{len(names):04d}_{period[:3]}{i}"
            names.append(name)
            rows.append(index_row(name, period))
    rows.append(index_row("a9998_people", "day", subject="people"))
    names += ["a9998_people", *listed_extra]
    net = Transport()
    net.data = {
        corpus.FIVEK + "legal/filesAdobe.txt": "\n".join(names).encode(),
        corpus.FIVEK + "legal/filesAdobeMIT.txt": b"",
        corpus.FIVEK: "".join(rows).encode(),
    }
    return net


def run_discover(net, limit, known=()):
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rows = corpus.discover_fivek(net, limit, known)
    return rows, buf.getvalue()


def kinds_of(rows):
    out = {}
    for r in rows:
        if r["role"] == "before":
            out[r["kind"]] = out.get(r["kind"], 0) + 1
    return out


def test_fivek_pairs_are_chosen_by_time_of_day_with_counts():
    net = made_up_index({"night": 5, "dawn or dusk": 4, "day": 3, "twilight": 2})
    rows, text = run_discover(net, 1000)
    assert kinds_of(rows) == {"blue_hour": 5, "sunset": 4, "control": 3}
    assert "night=5" in text and "dawn or dusk=4" in text and "day=3" in text and "twilight=2" in text
    assert "blue_hour: took 5" in text and "control: took 3" in text
    wanted = corpus.FIVEK_PAIRS_PER_KIND["blue_hour"]
    assert f"only 5 pairs, fewer than the {wanted}" in text and "only 4 pairs" in text
    for r in rows:
        corpus.check_row(r, corpus.config(), Path("/nonexistent-root"))


def test_fivek_per_kind_number_and_cap_are_respected():
    net = made_up_index({"night": 50, "dawn or dusk": 95, "day": 20})
    rows, text = run_discover(net, 1000)
    assert kinds_of(rows) == corpus.FIVEK_PAIRS_PER_KIND      # 50, 95 and 20 are each more than wanted
    assert "fewer than" not in text
    rows, _ = run_discover(net, 30)
    assert len(rows) == 30


def test_fivek_known_pairs_are_kept_and_two_runs_agree():
    net = made_up_index({"night": 3, "dawn or dusk": 3, "day": 40})
    first, _ = run_discover(net, 1000)
    again, _ = run_discover(net, 1000)
    assert first == again
    chosen = {r["pair"] for r in first if r["kind"] == "control"}
    names = re.findall(r"img/dng/([^\"/]+)\.dng", net.data[corpus.FIVEK].decode())
    every = {"pair-" + hashlib.sha256(n.encode()).hexdigest()[:16] for n in names if "_day" in n}
    left_out = sorted(every - chosen)[0]
    kept, _ = run_discover(net, 1000, {left_out})
    kept_control = {r["pair"] for r in kept if r["kind"] == "control"}
    assert left_out in kept_control and len(kept_control) == 16


def tiff(path, model=None, taken=None, seed=0):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (4, 4), (seed % 255, 1, 2))
    exif = Image.Exif()
    if model:
        exif[272] = model
    if taken:
        exif[306] = taken
    im.save(path, format="TIFF", exif=exif)


def test_fivek_leaves_out_rows_the_index_marks_as_indoors():
    def row(name, place):
        cell = f"<td>Location:<br>{place}</td>" if place else ""
        return index_row(name, "night").replace("<tr>", "<tr>" + cell)

    names = ["a0001", "a0002", "a0003"]
    net = Transport()
    net.data = {
        corpus.FIVEK + "legal/filesAdobe.txt": "\n".join(names).encode(),
        corpus.FIVEK + "legal/filesAdobeMIT.txt": b"",
        corpus.FIVEK: (row("a0001", "outdoor") + row("a0002", "indoor") + row("a0003", "")).encode(),
    }
    rows, printed = run_discover(net, 300)
    kept = {r["url"].rsplit("/", 1)[1].split(".")[0] for r in rows}
    assert kept == {"a0001", "a0003"}          # indoors is left out; a row without the label is kept
    assert "fivek rows by place: indoor=1, not labelled=1, outdoor=1" in printed
    # A pair that is here already and is no longer eligible is counted out loud.
    gone = "pair-" + hashlib.sha256(b"a0002").hexdigest()[:16]
    assert "1 pairs already here are no longer eligible" in run_discover(net, 300, known={gone})[1]


def fivek_pair(root, n, period, model, day):
    rows = []
    for role, ext in (("before", "dng"), ("after", "tif")):
        url = f"https://data.csail.mit.edu/graphics/fivek/img/{role}/a{n:04d}.{ext}"
        r = corpus.candidate(
            "fivek", "L", "control", role, url, corpus.FIVEK + "#a", "FiveK Adobe research",
            "synthetic", pair="pair-" + hashlib.sha256(str(n).encode()).hexdigest()[:16],
            query=f"nature or architecture; time={period}", licence_url=corpus.FIVEK + "legal/LicenseAdobe.txt",
        )
        path = root / r["file"]
        tiff(path, model if role == "before" else None, day, seed=n)
        r.update(sha256=corpus.digest(path), bytes=path.stat().st_size)
        rows.append(r)
    return rows


def round_two_corpus(root, shoots_per_kind=24, per_shoot=3, kinds=("night", "dawn or dusk", "day")):
    """FiveK-like pairs for the three times, and owner originals with references for moody."""
    rows, n = [], 0
    for period in kinds:
        for s in range(shoots_per_kind):
            for _ in range(per_shoot):
                n += 1
                rows += fivek_pair(root, n, period, f"Cam{s}", f"2024:03:{s + 1:02d} 10:00:00")
    corpus.save_manifest(root, rows)
    folder = root / "owner" / "originals" / "moody"
    for s in range(24):
        for k in range(3):
            stem = f"own{s:02d}{k}"
            tiff(folder / (stem + ".dng"), "Owner", f"2024:05:{s + 1:02d} 08:00:00", seed=s * 3 + k)
            if k == 0:
                tiff(folder / (stem + ".jpg"), seed=99)  # written as TIFF bytes; the extension marks it a reference
    return rows


def test_round_two_split_has_no_shoot_on_both_sides_and_caps_a_panel(tmp_path):
    round_two_corpus(tmp_path)
    out = tmp_path / "out"
    groups = corpus.split_round2(tmp_path, out)
    assert set(groups) == {"blue_hour", "sunset", "moody", "control"}
    assert list(groups["control"]) == ["panel"] and len(groups["control"]["panel"]) == 16
    shoots = json.loads((out / "round-2" / "shoots.json").read_text())
    assert "Cam" not in json.dumps(shoots)
    for kind in ("blue_hour", "sunset", "moody"):
        panel, hold = groups[kind]["panel"], groups[kind]["holdout"]
        assert (len(panel), len(hold)) == (16, 24)
        assert not {shoots[i] for i in panel} & {shoots[i] for i in hold}
        for side in (panel, hold):
            per = {}
            for i in side:
                per[shoots[i]] = per.get(shoots[i], 0) + 1
            assert max(per.values()) <= 2
    assert json.loads((out / "round-2" / "split.json").read_text()) == groups


def test_round_two_kind_with_too_few_photos_waits_and_joins_later(tmp_path):
    import contextlib

    rows = round_two_corpus(tmp_path, shoots_per_kind=5, per_shoot=2, kinds=("night",))
    out = tmp_path / "out"
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        first = corpus.split_round2(tmp_path, out)
    # Moody has enough of the owner's originals; blue hour has ten photos and waits.
    assert set(first) == {"moody"} and "waits: blue_hour: 10 photos" in printed.getvalue()
    n = 1000
    for s in range(5, 24):
        for _ in range(2):
            n += 1
            rows += fivek_pair(tmp_path, n, "night", f"Cam{s}", f"2024:03:{s + 1:02d} 10:00:00")
    corpus.save_manifest(tmp_path, rows)
    second = corpus.split_round2(tmp_path, out)
    assert set(second) == {"moody", "blue_hour"} and second["moody"] == first["moody"]
    assert (len(second["blue_hour"]["panel"]), len(second["blue_hour"]["holdout"])) == (16, 24)
    # With nothing complete, nothing is written.
    empty = tmp_path / "empty"
    (empty / "x").mkdir(parents=True)
    assert corpus.split_round2(empty / "x", empty / "out") == {}
    assert not (empty / "out" / "round-2" / "split.json").exists()


def test_round_two_never_holds_back_a_pair_round_one_measured_on(tmp_path):
    round_two_corpus(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    found = corpus.round2_photos(tmp_path)
    seen = sorted(found["sunset"])[:5] + sorted(found["blue_hour"])[:1]
    (out / "pairs-evaluation.txt").write_text(
        "".join(f"{found[k][i]['path']}\t{found[k][i]['ref']}\n"
                for k in ("sunset", "blue_hour") for i in seen if i in found[k]))
    groups = corpus.split_round2(tmp_path, out)
    held = {i for sides in groups.values() for i in sides.get("holdout", [])}
    tuned = {i for sides in groups.values() for i in sides["panel"]}
    assert not held & set(seen)
    assert set(seen) & tuned                       # they are still used, as tuning photos


def test_round_two_kinds_file_moves_or_leaves_out_a_photo_until_it_is_frozen(tmp_path):
    round_two_corpus(tmp_path)
    out = tmp_path / "out"
    folder = out / "round-2"
    folder.mkdir(parents=True)
    found = corpus.round2_photos(tmp_path)
    left, moved = sorted(found["blue_hour"])[:2]
    # a photo an earlier round measured on is taken first, so the moved one is sure to be used
    (out / "pairs-evaluation.txt").write_text(f"{found['blue_hour'][moved]['path']}\tref\n")
    (folder / "kinds.txt").write_text(f"{left} none\n\n{moved}\tsunset\n")
    groups = corpus.split_round2(tmp_path, out)
    used = {k: {i for ids in sides.values() for i in ids} for k, sides in groups.items()}
    assert not any(left in ids for ids in used.values())
    assert moved in groups["sunset"]["panel"] and moved not in used["blue_hour"]
    assert corpus.split_round2(tmp_path, out) == groups     # the same lines agree with the freeze
    for bad, why in ((f"{groups['blue_hour']['panel'][0]} none", "frozen split as blue_hour"),
                     (f"{moved} blue_hour", "frozen split as sunset"),
                     ("nobody none", "unknown photo or kind"),
                     (f"{left} dusk", "unknown photo or kind"),
                     (left, "unknown photo or kind")):
        (folder / "kinds.txt").write_text(bad + "\n")
        with pytest.raises(ValueError, match=why):
            corpus.split_round2(tmp_path, out)


def test_round_two_takes_owner_originals_with_their_reference(tmp_path):
    round_two_corpus(tmp_path)
    out = tmp_path / "out"
    groups = corpus.split_round2(tmp_path, out)
    lines = (out / "round-2" / "panel-moody.txt").read_text().splitlines()
    assert len(lines) == 16
    with_ref = [x for x in lines if "\t" in x]
    assert with_ref
    for line in lines:
        original, *ref = line.split("\t")
        assert Path(original).suffix == ".dng" and Path(original).is_absolute()
        if ref:
            assert Path(ref[0]).stem == Path(original).stem and Path(ref[0]).suffix == ".jpg"
    assert (out / "round-2" / "pairs-moody.txt").read_text().splitlines() == with_ref
    assert (out / "round-2" / "crops-moody.txt").read_text().count("\n") == 16
    assert not (out / "round-2" / "holdout-control.txt").exists()
    fivek = (out / "round-2" / "panel-blue_hour.txt").read_text().splitlines()
    assert all("\t" in x for x in fivek)


def test_round_two_is_frozen_and_never_touches_round_one(tmp_path):
    round_two_corpus(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    one = {
        "split.json": b'{"x": 1}',
        "planned-partitions.json": b"{}",
        "target-control.txt": b"a\n",
        "panel-control.txt": b"b\n",
        "holdout-control.txt": b"c\n",
        "crops-control.txt": b"d\n",
    }
    for name, data in one.items():
        (out / name).write_bytes(data)
    first = corpus.split_round2(tmp_path, out)
    folder = out / "round-2"
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file() and p.name != ".corpus.lock"}
    (folder / "crops-moody.txt").write_text("kept by hand\n")
    before["crops-moody.txt"] = b"kept by hand\n"
    assert corpus.split_round2(tmp_path, out, seed=5) == first
    after = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file() and p.name != ".corpus.lock"}
    assert after == before
    assert {n: (out / n).read_bytes() for n in one} == one
    # a listed original that vanished stops the run
    gone = Path((folder / "panel-sunset.txt").read_text().split("\t")[0])
    gone.unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        corpus.split_round2(tmp_path, out)
