"""Plan, fetch, verify and freeze licence-documented evaluation photographs.

  python -I scripts/web_corpus.py plan --sources rawpixls,commons,fivek
  python -I scripts/web_corpus.py select --holdout 24
  python -I scripts/web_corpus.py fetch --approved commons --selected --limit 64
  python -I scripts/web_corpus.py verify
  python -I scripts/web_corpus.py split

A plan contains discovery metadata only. Sources must be approved separately for fetch.
The manifest and the fetched bytes live in the ignored corpus folder. A crash cannot
publish a partial photo. A resume verifies a previous download before reusing it.
"""

from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html import unescape
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib
from urllib import error, parse, request

CONFIG = Path(__file__).parent / "data/web_sources.toml"
SOURCES = ("rawpixls", "commons", "fivek")
KINDS = ("blue_hour", "sunset", "moody", "control")
FIELDS = (
    "id",
    "track",
    "kind",
    "role",
    "pair",
    "file",
    "source",
    "page",
    "licence",
    "author",
    "sha256",
    "bytes",
    "fetched",
    "query",
    "capture_day",
    "url",
    "licence_url",
)
FIVEK = "https://data.csail.mit.edu/graphics/fivek/"
AGENT = "AutoEdit-WebPhotoLoop/1.0 (https://github.com/RawF4sh/AutoEdit)"
EXTENSIONS = set(
    "jpg jpeg tif tiff png webp avif heic heif dng cr2 cr3 crw nef nrw arw raf orf rw2 pef srw 3fr fff iiq dcr kdc mrw erf x3f raw mos mef rwl srf sr2 ari bay".split()
)


# The most one file may be when its source lists no size (FiveK). The largest file of the first
# run was 402 MB.
UNLISTED_FILE_BYTES = 1024**3


class BudgetError(ValueError):
    """The approved byte ceiling would be exceeded; this stops the run."""


def same_author(credit):
    """Two credits that differ only in case, punctuation or spacing are one author."""
    text = credit.casefold()
    return "".join(c for c in text if c.isalnum()) or text


SIGNATURES = {
    ("jpg", "jpeg"): (b"\xff\xd8\xff",),
    ("png",): (b"\x89PNG\r\n\x1a\n",),
    ("tif", "tiff"): (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
}


def not_a_picture(head, suffix):
    """Reason the first bytes cannot be a picture of this extension, else None."""
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if head.startswith(bom):
            head = head[len(bom) :]
            break
    if head.lstrip()[:1] in (b"<", b"{", b"["):
        return "a document, not a picture"
    suffix = suffix.lstrip(".").lower()
    for names, signatures in SIGNATURES.items():
        if suffix in names and not head.startswith(signatures):
            return f"not a {suffix} picture"
    if suffix == "webp" and not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "not a webp picture"
    return None


def config():
    return tomllib.loads(CONFIG.read_text())


def digest(path):
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        tmp = Path(out.name)
        try:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def text_html(s):
    return " ".join(unescape(re.sub("<[^>]*>", " ", str(s))).split())


def safe_url(url, hosts):
    try:
        u = parse.urlsplit(url)
        if (
            u.scheme == "https"
            and u.hostname in hosts
            and u.username is None
            and u.password is None
            and u.port in (None, 443)
        ):
            return url
    except (ValueError, TypeError):
        pass
    raise ValueError("URL is outside this source's HTTPS hosts")


class Transport:
    def __init__(self, source, cfg):
        self.hosts = cfg[source]["hosts"]
        self.pause = cfg["pause_seconds"]
        self.last = 0
        hosts = self.hosts

        class CheckedRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                safe_url(newurl, hosts)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        self.opener = request.build_opener(CheckedRedirect())

    def open(self, url):
        safe_url(url, self.hosts)
        time.sleep(max(0, self.pause - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = request.Request(
            url, headers={"User-Agent": AGENT, "Accept-Encoding": "identity"}
        )
        return self.opener.open(req, timeout=45)


def metadata(net, url, cap=12_000_000):
    with net.open(url) as response:
        data = response.read(cap + 1)
    if len(data) > cap:
        raise ValueError("metadata response exceeds its byte limit")
    return data


def candidate(
    source,
    track,
    kind,
    role,
    url,
    page,
    licence,
    author,
    *,
    day="",
    pair="",
    query="",
    size=0,
    sha256="",
    licence_url="",
):
    suffix = Path(parse.unquote(parse.urlsplit(url).path)).suffix.lower().lstrip(".")
    if suffix not in EXTENSIONS or not author:
        raise ValueError("missing supported file type or author credit")
    identifier = source + "-" + hashlib.sha256(url.encode()).hexdigest()[:20]
    folder = (
        Path("opens")
        if track == "O"
        else (Path("pairs") / pair if pair else Path("looks") / kind)
    )
    return dict(
        id=identifier,
        track=track,
        kind=kind,
        role=role,
        pair=pair,
        file=str(folder / (identifier + "." + suffix)),
        source=source,
        page=page,
        licence=licence,
        author=author,
        sha256=sha256,
        bytes=size,
        fetched="",
        query=query,
        capture_day=day,
        url=url,
        licence_url=licence_url,
    )


def discover_raw(net, limit):
    url = "https://raw.pixls.us/json/getrepository.php?set=all"
    rows = json.loads(metadata(net, url))["data"]
    seen = {}
    output = []
    for brand, model, mode, _, remark, licence, day, markup, _exif in sorted(
        rows, key=lambda r: (r[0] != "Canon", r[1] != "EOS 5D Mark III", r[0], r[1])
    ):
        if "creativecommons.org/publicdomain/zero/1.0/" not in licence:
            continue
        match = re.search(
            r"""href=['"](https://raw\.pixls\.us/getfile\.php/(\d+)/nice/[^'"]+)['"]""",
            markup,
        )
        checksum = re.search(r"\b[0-9a-f]{64}\b", markup)
        if not match or not checksum:
            continue
        url = parse.quote(unescape(match[1]), safe=":/%")
        suffix = Path(parse.urlsplit(url).path).suffix.lower()
        group = brand, suffix
        if seen.get(group, 0) >= 3 or suffix[1:] not in EXTENSIONS:
            continue
        seen[group] = seen.get(group, 0) + 1
        estimate = re.search(r"\(([\d.]+)MB\)", markup)
        output.append(
            candidate(
                "rawpixls",
                "O",
                "opens",
                "source",
                url,
                "https://raw.pixls.us/#repo",
                "CC0 1.0",
                "Uncredited raw.pixls.us contributor",
                query=f"{brand} {model} {mode}",
                day=day,
                size=int(float(estimate[1]) * 1048576) if estimate else 0,
                sha256=checksum[0],
                licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
        )
        if len(output) == limit:
            break
    return output


def commons_license(short, url):
    if short in ("CC0", "CC0 1.0", "Public domain"):
        # Commons occasionally supplies only a public-domain template and no URL.
        if url and not re.fullmatch(
            r"https?://creativecommons\.org/publicdomain/(?:zero|mark)/1\.0(?:/deed\.[a-z_-]+)?/?",
            url,
        ):
            return ""
        if short.startswith("CC0") and url and "/zero/" not in url:
            return ""
        return "CC0" if short.startswith("CC0") else short
    match = re.fullmatch(r"(CC BY(?:-SA)?) (\d\.\d)(?: [A-Z]+)?", short)
    if match and re.fullmatch(
        r"https?://creativecommons\.org/licenses/(by(?:-sa)?)/(\d\.\d)(?:/[a-z-]+)?/?",
        url,
    ):
        slug = match[1].lower().replace("cc ", "").replace(" ", "-")
        if f"/licenses/{slug}/{match[2]}" in url:
            return short
    return ""


def discover_commons(net, limit):
    cfg = config()["commons"]
    output, seen = [], set()
    # Reserve space for format coverage; distribute the rest across four styles.
    cap = max(1, (limit + len(cfg["queries"]) - 1) // len(cfg["queries"]))
    for kind, queries in cfg["queries"].items():
        n = 0
        for q in queries:
            if n >= cap or len(output) >= limit:
                break
            params = dict(
                action="query",
                format="json",
                generator="search",
                gsrnamespace=6,
                gsrlimit=50,
                gsrsearch=q,
                prop="imageinfo",
                iiprop="url|size|mime|extmetadata",
                iiextmetadatafilter="LicenseShortName|LicenseUrl|Artist|DateTimeOriginal|Categories|Restrictions",
            )
            for _page in range(4):
                data = json.loads(
                    metadata(
                        net,
                        "https://commons.wikimedia.org/w/api.php?"
                        + parse.urlencode(params),
                    )
                )
                if "error" in data:
                    raise ValueError(
                        f"Commons discovery error: {data['error'].get('code')}"
                    )
                for item in sorted(
                    data.get("query", {}).get("pages", {}).values(),
                    key=lambda p: p.get("index", 0),
                ):
                    if not item.get("imageinfo") or item["pageid"] in seen:
                        continue
                    info = item["imageinfo"][0]
                    if (
                        not info.get("mime", "").startswith("image/")
                        or info.get("size", 0) < 15000
                    ):
                        continue
                    fields = {
                        k: text_html(v["value"])
                        for k, v in info.get("extmetadata", {}).items()
                    }
                    title = item["title"] + " " + fields.get("Categories", "")
                    if re.search(
                        r"\b(child(?:ren)?|boy|girl|kid|teens?|portrait|people|faces?|non-free)\b",
                        title,
                        re.I,
                    ) or fields.get("Restrictions"):
                        continue
                    licence_url = fields.get("LicenseUrl", "")
                    licence = commons_license(
                        fields.get("LicenseShortName", ""), licence_url
                    )
                    day = re.search(
                        r"\b\d{4}-\d{2}-\d{2}\b", fields.get("DateTimeOriginal", "")
                    )
                    if not licence or not fields.get("Artist") or not day:
                        continue
                    url = info["url"].split("?")[0]
                    if (
                        Path(parse.urlsplit(url).path).suffix.lower()[1:]
                        not in EXTENSIONS
                    ):
                        continue
                    output.append(
                        candidate(
                            "commons",
                            "O" if kind == "opens" else "L",
                            kind,
                            "source" if kind == "opens" else "finished",
                            url,
                            info["descriptionurl"],
                            licence,
                            fields["Artist"],
                            query=q,
                            day=day[0],
                            size=info["size"],
                            licence_url=licence_url,
                        )
                    )
                    seen.add(item["pageid"])
                    n += 1
                    if n >= cap or len(output) >= limit:
                        break
                if not data.get("continue") or n >= cap or len(output) >= limit:
                    break
                params.update(data["continue"])
    return output


# Round two: the pairs of the index are taken by time of day. A time not listed is left out.
FIVEK_KIND_OF_TIME = {"night": "blue_hour", "dawn or dusk": "sunset", "day": "control"}
# Blue hour takes four more than the 40 the split needs: a shoot gives at most two photos to a
# side, and the 40 night pairs first fetched came to 39 usable ones.
# Dawn or dusk takes 89: the last 49 pairs of the owner's 150 (2026-11-24), so that twilight
# frames with buildings can be sorted out of them for Blue hour (F61, O8).
FIVEK_PAIRS_PER_KIND = {"blue_hour": 44, "sunset": 89, "control": 16}
FIVEK_SEED = 61


def discover_fivek(net, limit, known=()):
    """Read every eligible pair of the index; keep `known` pairs, fill each kind up to its number."""
    names = {}
    for family, licence in (
        ("Adobe", "FiveK Adobe research"),
        ("AdobeMIT", "FiveK Adobe-MIT research"),
    ):
        for name in (
            metadata(net, FIVEK + "legal/files" + family + ".txt").decode().splitlines()
        ):
            names[name.strip()] = (family, licence)
    html = metadata(net, FIVEK, 25_000_000).decode()
    eligible, places = {}, {}
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.I | re.S)
    for row in rows:
        if not re.search(
            r"Subject:\s*<br>\s*(nature|man-made object)\s*</td>", row, re.I
        ):
            continue
        src = re.search(r'href="(img/dng/([^"/]+)\.dng)"', row)
        dst = re.search(r'href="(img/tiff16_c/([^"/]+)\.tif)"', row)
        if not src or not dst or src[2] != dst[2] or src[2] not in names:
            continue
        pair = "pair-" + hashlib.sha256(src[2].encode()).hexdigest()[:16]
        period = re.search(r"Time:\s*<br>([^<]+)", row)
        period = period[1].strip() if period else "unknown"
        # Where the index says a photo was taken indoors, it is left out. A row without that
        # label is kept: the label's wording could not be checked before the first real plan.
        place = re.search(r"(?:Location|Place):\s*<br>([^<]+)", row, re.I)
        place = place[1].strip().lower() if place else "not labelled"
        places[place] = places.get(place, 0) + 1
        if place != "not labelled" and not place.startswith("outdoor"):
            continue
        eligible[pair] = (src, dst, names[src[2]], period)
    times = {}
    for *_, period in eligible.values():
        times[period] = times.get(period, 0) + 1
    print(
        "fivek eligible pairs by time: "
        + ", ".join(f"{t}={n}" for t, n in sorted(times.items())),
        flush=True,
    )
    print(
        "fivek rows by place: " + ", ".join(f"{t}={n}" for t, n in sorted(places.items())),
        flush=True,
    )
    gone = sum(p not in eligible for p in known)
    if gone:   # 5. say so: such a pair stays in the manifest and leaves the plan
        print(f"fivek: {gone} pairs already here are no longer eligible in the index", flush=True)
    ranked = sorted(
        eligible,
        key=lambda p: hashlib.sha256(f"{FIVEK_SEED}:{p}".encode()).digest(),
    )
    chosen = [p for p in ranked if p in known]
    taken = dict.fromkeys(FIVEK_PAIRS_PER_KIND, 0)
    for p in chosen:
        kind = FIVEK_KIND_OF_TIME.get(eligible[p][3])
        if kind:
            taken[kind] += 1
    for p in ranked:
        kind = FIVEK_KIND_OF_TIME.get(eligible[p][3])
        if (
            p in chosen
            or not kind
            or taken[kind] >= FIVEK_PAIRS_PER_KIND[kind]
            or len(chosen) * 2 + 2 > limit
        ):
            continue
        chosen.append(p)
        taken[kind] += 1
    for kind, want in FIVEK_PAIRS_PER_KIND.items():
        print(f"fivek {kind}: took {taken[kind]} pairs (up to {want})", flush=True)
        if kind != "control" and taken[kind] < want:
            print(f"fivek {kind}: only {taken[kind]} pairs, fewer than the {want} wanted", flush=True)
    result = []
    for pair in chosen:
        src, dst, (family, licence), period = eligible[pair]
        kind = FIVEK_KIND_OF_TIME.get(period, "control")
        page = FIVEK + "#" + src[2][1:5]
        for role, m in (("before", src), ("after", dst)):
            credit = (
                "FiveK dataset contributor"
                if role == "before"
                else "MIT\u2013Adobe FiveK, expert C"
            )
            result.append(
                candidate(
                    "fivek",
                    "L",
                    kind,
                    role,
                    parse.urljoin(FIVEK, parse.quote(m[1])),
                    page,
                    licence,
                    credit,
                    pair=pair,
                    query=f"nature or architecture; time={period}",
                    licence_url=FIVEK + "legal/License" + family + ".txt",
                )
            )
    return result


def check_row(row, cfg, root):
    source = row["source"]
    if source not in SOURCES:
        raise ValueError("unknown source")
    safe_url(row["url"], cfg[source]["hosts"])
    safe_url(row["page"], cfg[source]["hosts"])
    if row["kind"] not in (*KINDS, "opens") or row["track"] not in ("O", "L"):
        raise ValueError("unsupported kind or track")
    if int(row["bytes"]) < 0 or not row["author"].strip():
        raise ValueError("invalid size or missing creator credit")
    if source == "rawpixls" and (row["track"], row["kind"], row["role"]) != (
        "O",
        "opens",
        "source",
    ):
        raise ValueError("RAW repository photographs are decoder samples")
    if source == "commons" and (row["track"], row["role"]) != (
        ("O", "source") if row["kind"] == "opens" else ("L", "finished")
    ):
        raise ValueError("invalid Commons track or role")
    if source == "rawpixls" and not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
        raise ValueError("a RAW sample needs the timestamped repository's SHA-256")
    if source == "commons":
        valid = commons_license(row["licence"], row.get("licence_url", ""))
    else:
        valid = row["licence"] in cfg[source]["licences"]
    if not valid:
        raise ValueError("unapproved licence")
    if source == "fivek" and (
        row["track"] != "L"
        or row["kind"] == "opens"
        or row["role"] not in ("before", "after")
        or not re.fullmatch(r"pair-[0-9a-f]{16}", row["pair"])
    ):
        raise ValueError("FiveK is limited to research evaluation pairs")
    if source != "fivek" and row["pair"]:
        raise ValueError("unexpected pair tag")
    actual = candidate(
        source,
        row["track"],
        row["kind"],
        row["role"],
        row["url"],
        row["page"],
        row["licence"],
        row["author"],
        pair=row["pair"],
    )
    if actual["id"] != row["id"] or actual["file"] != row["file"]:
        raise ValueError("unsafe or inconsistent corpus filename")
    dest = (root / row["file"]).resolve()
    if root.resolve() not in dest.parents:
        raise ValueError("corpus entry escapes root")
    return dest


def manifest(root):
    path = root / "manifest.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("invalid manifest columns")
        rows = list(reader)
    if len(set(r["id"] for r in rows)) != len(rows):
        raise ValueError("duplicate manifest id")
    return rows


def save_manifest(root, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    write_atomic(root / "manifest.csv", stream.getvalue().encode())


def verify(root, rows=None, *, cfg=None, row_check=check_row):
    cfg = cfg or config()
    entire = rows is None
    rows = manifest(root) if entire else rows
    errors = []
    for row in rows:
        try:
            path = row_check(row, cfg, root)
            if (
                path.stat().st_size != int(row["bytes"])
                or digest(path) != row["sha256"]
            ):
                raise ValueError("size or checksum differs")
            with path.open("rb") as fh:
                reason = not_a_picture(fh.read(64), path.suffix)
            if reason:
                raise ValueError(reason)
        except (OSError, ValueError) as exc:
            errors.append(f"{row['id']}: {exc}")
    if entire:
        pairs = {}
        for row in rows:
            if row["pair"]:
                pairs.setdefault(row["pair"], []).append(row["role"])
        errors.extend(
            f"{pair}: incomplete research pair"
            for pair, roles in pairs.items()
            if sorted(roles) != ["after", "before"]
        )
    return errors


@contextmanager
def lock(root):
    # Same file protects the source budgets, manifest and freeze across processes.
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".corpus.lock").open("a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            if fh.read(1) == b"":
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def fetch(root, entries, approved, net_for, cfg=None, failures=None, *, row_check=check_row, pair_budget=None, other_bytes=0):
    """Download entries. A bad file is recorded in `failures` (id, reason) and skipped."""
    cfg = cfg or config()
    failures = [] if failures is None else failures
    if any(entry["source"] not in approved for entry in entries):
        raise ValueError("source needs explicit approval")
    with lock(root):
        rows = manifest(root)
        known = {r["id"]: r for r in rows}
        # The forum-pair ledger shares this corpus’s total byte allowance.
        other_bytes += sum(int(r["bytes"]) for r in manifest(root / "auto-pairs"))
        sources = {r["source"] for r in (*rows, *entries)}
        counts = {s: sum(r["source"] == s for r in rows) for s in sources}
        sizes = {
            s: sum(int(r["bytes"]) for r in rows if r["source"] == s) for s in sources
        }
        pairs = {r["pair"] for r in rows if r["source"] == "fivek"}
        collected_pairs = {r["pair"] for r in rows if r["pair"]}
        dests = [(entry, row_check(entry, cfg, root)) for entry in entries]
        for entry, dest in dests:
            source = entry["source"]
            if entry["id"] in known:
                previous = known[entry["id"]]
                if any(
                    previous[field] != entry[field]
                    for field in (
                        "file",
                        "source",
                        "url",
                        "licence",
                        "licence_url",
                        "role",
                        "pair",
                        "author",
                    )
                ):
                    raise ValueError("metadata differs from the original acquisition")
                if verify(root, [known[entry["id"]]], cfg=cfg, row_check=row_check):
                    raise ValueError("previous download failed checksum verification")
                continue
            available = min(
                cfg["total_bytes"] - other_bytes - sum(sizes.values()),
                cfg[source]["max_bytes"] - sizes[source],
            )
            if (
                source == "fivek"
                and entry["pair"] not in pairs
                and len(pairs) >= cfg[source]["max_files"] // 2
            ):
                print("FiveK pair budget reached", flush=True)
                continue
            if (
                (pair_budget is not None and entry["pair"] not in collected_pairs and len(collected_pairs) >= pair_budget)
                or counts[source] >= cfg[source]["max_files"]
                or available <= 0
                or int(entry["bytes"]) > available
            ):
                print(f"budget reached for {source}", flush=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_suffix(dest.suffix + ".part")
            # The index is published after the verified bytes. After an interruption
            # either there is a manifest entry or the unindexed file can be fetched again.
            try:
                expected = int(entry.get("bytes") or 0)
                downloaded = 0
                checksum = hashlib.sha256()
                with (
                    net_for[source].open(entry["url"]) as response,
                    part.open("wb") as out,
                ):
                    if (
                        response.headers.get("Content-Type", "")
                        .lower()
                        .startswith(("text/", "application/json"))
                    ):
                        raise ValueError("image download returned a document")
                    length = response.headers.get("Content-Length")
                    if length is not None:
                        try:
                            length = int(length)
                        except ValueError:
                            raise ValueError("unusable Content-Length header")
                        if length > available:
                            print(
                                f"source budget would be exceeded for {entry['id']}",
                                flush=True,
                            )
                            continue
                    # Never read far past the size the source listed. A Commons size is exact.
                    # The RAW index lists approximate sizes (148 of 149 fetched files differed
                    # by a few kB, half of them larger), so those get 5 % and 1 MB of room.
                    ceiling = UNLISTED_FILE_BYTES
                    if expected:
                        ceiling = expected if source == "commons" else expected + expected // 20 + 1_048_576
                    limit = min(ceiling, available) + 1
                    while chunk := response.read(min(1_048_576, limit - downloaded)):
                        downloaded += len(chunk)
                        if downloaded > ceiling:
                            raise ValueError("file is larger than its listed size" if expected
                                             else "file is larger than the limit for a source that lists no size")
                        if downloaded > available:
                            raise BudgetError(
                                "download would exceed approved byte ceiling"
                            )
                        out.write(chunk)
                        checksum.update(chunk)
                if not downloaded or (length is not None and downloaded != length):
                    raise ValueError("incomplete download")
                if source == "commons" and expected and downloaded != expected:
                    raise ValueError("file changed since discovery")
                got = checksum.hexdigest()
                if entry["sha256"] and got != entry["sha256"]:
                    raise ValueError("download differs from source checksum")
                with part.open("rb") as fh:
                    reason = not_a_picture(fh.read(64), dest.suffix)
                if reason:
                    raise ValueError(reason)
                os.replace(part, dest)
                row = dict(
                    entry,
                    sha256=got,
                    bytes=downloaded,
                    fetched=datetime.now(timezone.utc).isoformat(),
                )
                rows.append(row)
                save_manifest(root, rows)
                known[row["id"]] = row
                if row["pair"]:
                    collected_pairs.add(row["pair"])
                if source == "fivek":
                    pairs.add(row["pair"])
                sizes[source] += downloaded
                counts[source] += 1
                print(
                    f"{source} {counts[source]} · {row['id']} · {downloaded} bytes",
                    flush=True,
                )
            except BudgetError:
                raise
            except error.HTTPError as exc:
                if exc.code not in {404, 410}:
                    raise
                failures.append((entry["id"], f"source file unavailable (HTTP {exc.code})"))
                exc.close()
            except ValueError as exc:
                failures.append((entry["id"], str(exc)))
                print(f"failed {entry['id']}: {exc}", flush=True)
            finally:
                part.unlink(missing_ok=True)
        return rows


def partition(rows, seed=60, targets=30, panel=16, holdout=12):
    """Allocate by author across all styles; duplicate checksums cannot cross roles."""
    if min(holdout, panel, targets) < 1:
        raise ValueError("positive panel, target and holdout sizes are required")
    need = {"holdout": holdout, "panel": panel, "target": targets}
    groups = {k: {role: [] for role in need} for k in KINDS}

    def order(string):
        return hashlib.sha256(f"{seed}:{string}".encode()).digest()

    # One author's work goes to one role across all kinds. Fill the largest
    # groups first so a prolific photographer cannot consume the other roles.
    by_author = {}
    seen = set()
    for r in sorted(rows, key=lambda r: order(r["id"])):
        if (
            r["source"] != "commons"
            or r["role"] != "finished"
            or r["kind"] not in groups
        ):
            continue
        identity = r["sha256"] or r["id"]
        if identity in seen:
            continue
        seen.add(identity)
        by_author.setdefault(same_author(r["author"]), []).append(r)
    for author, photos in sorted(
        by_author.items(), key=lambda kv: (-len(kv[1]), order(kv[0]))
    ):
        counts = {k: sum(r["kind"] == k for r in photos) for k in KINDS}

        def gain(role):
            total = 0
            for kind, n in counts.items():
                missing = need[role] - len(groups[kind][role])
                total += (missing**2 - max(0, missing - n) ** 2) / need[role]
            return total

        role = max(need, key=gain)
        for r in photos:
            chosen = groups[r["kind"]][role]
            if len(chosen) < need[role]:
                chosen.append(r)
    short = [
        (kind, role, len(v), n)
        for kind, o in groups.items()
        for role, n in (("target", targets), ("panel", panel), ("holdout", holdout))
        if len(v := o[role]) < n
    ]
    if short:
        raise ValueError(
            f"need more independent photos before freezing this round: {short}"
        )
    return groups


def ids_by_group(groups):
    return {
        kind: {role: [r["id"] for r in rows] for role, rows in roles.items()}
        for kind, roles in groups.items()
    }


def select(root, output, *, targets=30, panel=16, holdout=24, seed=60):
    """Reserve specific source IDs before downloads begin. Never redraw an existing reservation."""
    path = output / "planned-partitions.json"
    with lock(output):
        if path.exists():
            return json.loads(path.read_text())
        source_plan = json.loads((root / "metadata" / "plan-commons.json").read_text())
        selection = ids_by_group(partition(source_plan, seed, targets, panel, holdout))
        write_atomic(path, json.dumps(selection, indent=2).encode())
        return selection


def selected_ids(selection):
    return {
        identifier
        for roles in selection.values()
        for ids in roles.values()
        for identifier in ids
    }


def split(root, output, seed=60, targets=30, panel=16, holdout=12):
    """Publish paths for the reserved IDs, rechecking actual hashes and credits for leakage."""
    freeze = output / "split.json"
    reserved = output / "planned-partitions.json"
    with lock(output):
        rows = manifest(root)
        prior = freeze if freeze.exists() else reserved
        groups = (
            json.loads(prior.read_text())
            if prior.exists()
            else ids_by_group(partition(rows, seed, targets, panel, holdout))
        )
        by_id = {r["id"]: r for r in rows}
        roles_by_author = {}
        crossing = set()
        used_hashes = set()
        files = {}
        if set(groups) != set(KINDS):
            raise ValueError("unexpected kinds in saved split")
        for kind, roles in groups.items():
            if set(roles) != {"target", "panel", "holdout"}:
                raise ValueError("invalid roles in saved split")
            for role, ids in roles.items():
                if not ids:
                    raise ValueError("empty group in saved split")
                items = []
                for ident in ids:
                    if ident not in by_id:
                        raise ValueError(
                            f"reserved file has not been acquired: {ident}"
                        )
                    row = by_id[ident]
                    if (row["kind"], row["role"], row["source"]) != (
                        kind,
                        "finished",
                        "commons",
                    ):
                        raise ValueError(
                            "saved split includes a different kind or ineligible source"
                        )
                    author, sha = same_author(row["author"]), row["sha256"]
                    if roles_by_author.setdefault(author, role) != role:
                        crossing.add(author)
                    if sha in used_hashes or not sha:
                        raise ValueError("duplicate photo in saved split")
                    used_hashes.add(sha)
                    path = (root / row["file"]).resolve()
                    if not path.is_file() or digest(path) != sha:
                        raise ValueError(f"reserved image missing or changed: {ident}")
                    items.append(str(path))
                files[output / f"{role}-{kind}.txt"] = "\n".join(items) + "\n"
            crop_path = output / f"crops-{kind}.txt"
            if not crop_path.exists():
                files[crop_path] = "".join(
                    f"{i}\t0.5\t0.5\tcentre\n" for i in roles["panel"]
                )
        if crossing:
            print(f"warning: {len(crossing)} authors appear in more than one role")
        # Validate every group before publishing its paths. Preserve annotated crops on resume.
        for path, value in files.items():
            write_atomic(path, value.encode())
        write_atomic(freeze, json.dumps(groups, indent=2).encode())
        return groups


REFERENCE_EXTENSIONS = ("jpg", "jpeg", "tif", "tiff", "png")
ROUND2_SEED = 61
ROUND2_SIZES = {
    "blue_hour": (16, 24),
    "sunset": (16, 24),
    "moody": (16, 24),
    "control": (16, 0),
}


def shoot_key(path, ident):
    """A short hash of camera model and capture day; a file with no readable EXIF is its own shoot."""
    from PIL import Image

    try:
        with Image.open(path) as im:
            exif = im.getexif()
            model = str(exif.get(272) or "").strip()
            taken = str(exif.get_ifd(34665).get(36867) or exif.get(306) or "").strip()
        day = taken[:10]
        if model and day:
            return hashlib.sha256(f"{model}|{day}".encode()).hexdigest()[:12]
    except Exception:  # unreadable or foreign format
        pass
    return hashlib.sha256(f"own|{ident}".encode()).hexdigest()[:12]


def round2_photos(root):
    """Every usable photo by kind: {kind: {id: dict(path, ref, shoot)}}. Duplicate ids raise."""
    found, seen = {k: {} for k in KINDS}, set()

    def add(kind, ident, path, ref):
        if ident in seen:
            raise ValueError(f"duplicate photo id in the round-two split: {ident}")
        seen.add(ident)
        found[kind][ident] = dict(
            path=str(path), ref=str(ref) if ref else "", shoot=shoot_key(path, ident)
        )

    pairs = {}
    for r in manifest(root):
        if r["source"] == "fivek" and r["pair"]:
            pairs.setdefault(r["pair"], {})[r["role"]] = r
    for roles in pairs.values():
        if set(roles) != {"before", "after"} or any(verify(root, [r]) for r in roles.values()):
            continue
        when = re.search(r"time=(.+)$", roles["before"]["query"])
        kind = FIVEK_KIND_OF_TIME.get(when[1].strip()) if when else None
        if kind:
            before, after = (root / roles[x]["file"] for x in ("before", "after"))
            add(kind, before.stem, before.resolve(), after.resolve())
    for kind in KINDS:
        folder = root / "owner" / "originals" / kind
        if not folder.is_dir():
            continue
        stems = {}
        for f in sorted(folder.iterdir()):
            if f.is_file() and not f.name.startswith(".") and f.suffix[1:].lower() in EXTENSIONS:
                stems.setdefault(f.stem, []).append(f)
        for stem, files in stems.items():
            raws = [f for f in files if f.suffix[1:].lower() not in REFERENCE_EXTENSIONS]
            if raws:
                refs = [f for f in files if f not in raws]
                add(kind, stem, raws[0].resolve(), refs[0].resolve() if refs else None)
            else:
                for f in files:  # no RAW beside it: each is an original without a reference
                    add(kind, stem, f.resolve(), None)
    return found


def round2_choose(kind, photos, seed, panel, holdout, tuning_only=()):
    """Shoots in a seeded order; a shoot goes to one side and gives at most two photos to it.
    A shoot with a photo in `tuning_only` (seen in an earlier round) is never held back."""

    def order(s):
        return hashlib.sha256(f"{seed}:{kind}:{s}".encode()).digest()

    shoots = {}
    for ident, p in photos.items():
        shoots.setdefault(p["shoot"], []).append(ident)
    sides = {"panel": [], "holdout": []}
    need = {"panel": panel, "holdout": holdout}
    seen = [k for k in sorted(shoots, key=order) if set(shoots[k]) & set(tuning_only)]
    for key in seen + [k for k in sorted(shoots, key=order) if k not in seen]:
        side = "panel" if len(sides["panel"]) < panel else "holdout"
        room = need[side] - len(sides[side])
        if room > 0 and not (side == "holdout" and key in seen):
            sides[side] += sorted(shoots[key], key=order)[: min(2, room)]
    if any(len(sides[s]) < need[s] for s in sides):
        raise ValueError(
            f"{kind}: {len(photos)} photos in {len(shoots)} shoots, "
            f"{panel + holdout} needed with at most two per shoot and side"
        )
    return {s: ids for s, ids in sides.items() if need[s]}


def split_round2(root, output, seed=ROUND2_SEED):
    """Round two's split, written only into <output>/round-2/. A kind joins the split once it has
    enough photos, and from then on its assignment never changes. A kind with too few waits and
    is named; it does not hold up the others."""
    out = output / "round-2"
    freeze = out / "split.json"
    with lock(out):
        found = round2_photos(root)
        groups = json.loads(freeze.read_text()) if freeze.exists() else {}
        if not set(groups) <= set(ROUND2_SIZES):
            raise ValueError("unexpected kinds in saved round-two split")
        # kinds.txt, one "ID KIND" per line: a photo's kind where it is not its label, or "none"
        # to leave the photo out. A photo that is in the frozen split keeps its kind.
        frozen = {i: k for k, sides in groups.items() for ids in sides.values() for i in ids}
        moves = out / "kinds.txt"
        for line in moves.read_text().splitlines() if moves.is_file() else ():
            if not line.strip():
                continue
            parts = line.split()
            ident, kind = parts if len(parts) == 2 else ("", "")
            old = next((k for k in found if ident in found[k]), None)
            if old is None or kind not in (*ROUND2_SIZES, "none"):
                raise ValueError(f"kinds.txt: unknown photo or kind: {line.strip()}")
            if frozen.get(ident, kind) != kind:
                raise ValueError(f"kinds.txt: {ident} is in the frozen split as {frozen[ident]}")
            photo = found[old].pop(ident)
            if kind != "none":
                found[kind][ident] = photo
        for kind, sides in groups.items():
            for ident in (i for ids in sides.values() for i in ids):
                if ident not in found[kind] or not Path(found[kind][ident]["path"]).is_file():
                    raise ValueError(f"reserved image missing or changed: {ident}")
        # The pairs every iteration of round one was measured on are no longer unseen.
        earlier = output / "pairs-evaluation.txt"
        seen = (
            {Path(line.split("\t")[0]).stem for line in earlier.read_text().splitlines() if line.strip()}
            if earlier.is_file()
            else set()
        )
        for kind, (panel, holdout) in ROUND2_SIZES.items():
            if kind in groups or not found[kind]:
                continue
            try:
                groups[kind] = round2_choose(kind, found[kind], seed, panel, holdout, seen)
            except ValueError as short:
                print(f"waits: {short}", flush=True)
        if not groups:
            return groups
        files, shoots = {}, {}
        for kind, sides in groups.items():
            lines = {}
            for side, ids in sides.items():
                lines[side] = [
                    found[kind][i]["path"] + ("\t" + found[kind][i]["ref"] if found[kind][i]["ref"] else "")
                    for i in ids
                ]
                shoots.update({i: found[kind][i]["shoot"] for i in ids})
                files[out / f"{side}-{kind}.txt"] = "".join(x + "\n" for x in lines[side])
            files[out / f"pairs-{kind}.txt"] = "".join(
                x + "\n" for x in lines["panel"] if "\t" in x
            )
            crops = out / f"crops-{kind}.txt"
            if not crops.exists():
                files[crops] = "".join(f"{i}\t0.5\t0.5\tcentre\n" for i in sides["panel"])
        for path, text in files.items():
            write_atomic(path, text.encode())
        write_atomic(out / "shoots.json", json.dumps(shoots, indent=2).encode())
        write_atomic(freeze, json.dumps(groups, indent=2).encode())
        return groups


def discover(root, sources, limit):
    cfg = config()
    entries = []
    for source in sources:
        cap = min(limit or cfg[source]["max_files"], cfg[source]["max_files"])
        if source == "fivek" and cap < 2:
            continue
        fn = {
            "commons": discover_commons,
            "rawpixls": discover_raw,
            "fivek": discover_fivek,
        }[source]
        extra = (
            ({r["pair"] for r in manifest(root) if r["source"] == "fivek"},)
            if source == "fivek"
            else ()
        )
        rows = fn(Transport(source, cfg), cap, *extra)
        for row in rows:
            check_row(row, cfg, root)
        write_atomic(
            root / "metadata" / f"plan-{source}.json",
            json.dumps(rows, indent=2).encode(),
        )
        entries.extend(rows)
        known = sum(int(r["bytes"]) for r in rows)
        unknown = sum(not int(r["bytes"]) for r in rows)
        print(
            f"{source}: {len(rows)} files; estimated known {known:,} bytes, {unknown} sizes pending; ceiling {cfg[source]['max_bytes']:,} bytes."
        )
    return entries


def terms(root, source, net, cfg):
    name = root / "metadata" / f"terms-{source}.json"
    if not name.exists():
        texts = {
            url: metadata(net, url).decode("utf-8", "replace")
            for url in cfg[source]["terms"]
        }
        write_atomic(
            name,
            json.dumps(
                dict(fetched=datetime.now(timezone.utc).date().isoformat(), pages=texts)
            ).encode(),
        )
    parts = ["# Source terms at first fetch\n"]
    for s in SOURCES:
        f = root / "metadata" / f"terms-{s}.json"
        if f.exists():
            doc = json.loads(f.read_text())
            parts.append(
                f"## {s}\n\nRetrieved {doc['fetched']}. Snapshot: metadata/{f.name}\n"
            )
            parts.extend("- " + u for u in doc["pages"])
    write_atomic(root / "SOURCES.md", "\n".join(parts).encode())


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("action", choices=["plan", "select", "fetch", "verify", "split"])
    ap.add_argument("--root", type=Path, default=Path("curated-web"))
    ap.add_argument("--out", type=Path, default=Path("out-webloop"))
    ap.add_argument("--sources", default=",".join(SOURCES))
    ap.add_argument(
        "--approved", default="", help="comma-separated source ids the owner approved"
    )
    ap.add_argument(
        "--limit",
        type=int,
        help="maximum planned files per source or number fetched per source this run",
    )
    ap.add_argument(
        "--selected",
        action="store_true",
        help="fetch only reserved Commons style groups and format samples",
    )
    ap.add_argument(
        "--holdout",
        type=int,
        default=24,
        help="held-back photos per kind when first reserving the split",
    )
    ap.add_argument(
        "--round", type=int, choices=(1, 2), default=1, help="split: which round's split to write"
    )
    args = ap.parse_args()
    if args.action == "select":
        selected = select(args.root, args.out, holdout=args.holdout)
        print(f"Reserved {len(selected_ids(selected))} style photographs.")
        return 0
    cfg = config()
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be positive")
    if args.action == "verify":
        errors = verify(args.root)
        print(f"Verified {len(manifest(args.root))} entries; {len(errors)} errors.")
        for msg in errors:
            print(msg)
        return int(bool(errors) or not manifest(args.root))
    if args.action == "split" and args.round == 2:
        for kind, sides in split_round2(args.root, args.out).items():
            print(kind, {side: len(ids) for side, ids in sides.items()})
        return 0
    if args.action == "split":
        split(args.root, args.out, holdout=args.holdout)
        return 0
    sources = (
        args.sources.split(",")
        if args.action == "plan"
        else list(dict.fromkeys(s for s in args.approved.split(",") if s))
    )
    if not sources or not set(sources) <= set(SOURCES):
        ap.error("choose one or more accepted source ids")
    if args.action == "plan":
        discover(args.root, sources, args.limit)
    else:
        failures = []
        for s in sources:
            net = Transport(s, cfg)
            rows = json.loads((args.root / "metadata" / f"plan-{s}.json").read_text())
            if args.selected and s == "commons":
                reserved = selected_ids(
                    json.loads((args.out / "planned-partitions.json").read_text())
                )
                rows = [r for r in rows if r["track"] == "O" or r["id"] in reserved]
            terms(args.root, s, net, cfg)
            if args.limit:
                known = {r["id"] for r in manifest(args.root)}
                rows = [r for r in rows if r["id"] not in known][: args.limit]
            fetch(args.root, rows, sources, {s: net}, cfg, failures)
        if failures:
            print(f"{len(failures)} files failed:")
            for ident, reason in failures:
                print(f"  {ident}: {reason}")
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, error.URLError) as exc:
        raise SystemExit(str(exc))
