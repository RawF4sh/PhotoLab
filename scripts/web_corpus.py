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


def discover_fivek(net, limit):
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
    result = []
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
        family, licence = names[src[2]]
        pair = "pair-" + hashlib.sha256(src[2].encode()).hexdigest()[:16]
        period = re.search(r"Time:\s*<br>([^<]+)", row)
        period = period[1].strip() if period else "unknown"
        kind = "blue_hour" if period == "night" else "control"
        page = FIVEK + "#" + src[2][1:5]
        for role, m in (("before", src), ("after", dst)):
            credit = (
                "FiveK dataset contributor"
                if role == "before"
                else "MIT–Adobe FiveK, expert C"
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
        if len(result) + 2 > limit:
            break
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


def verify(root, rows=None):
    cfg = config()
    entire = rows is None
    rows = manifest(root) if entire else rows
    errors = []
    for row in rows:
        try:
            path = check_row(row, cfg, root)
            if (
                path.stat().st_size != int(row["bytes"])
                or digest(path) != row["sha256"]
            ):
                raise ValueError("size or checksum differs")
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


def fetch(root, entries, approved, net_for, cfg=None):
    cfg = cfg or config()
    with lock(root):
        rows = manifest(root)
        known = {r["id"]: r for r in rows}
        counts = {s: sum(r["source"] == s for r in rows) for s in SOURCES}
        sizes = {
            s: sum(int(r["bytes"]) for r in rows if r["source"] == s) for s in SOURCES
        }
        pairs = {r["pair"] for r in rows if r["source"] == "fivek"}
        dests = [(entry, check_row(entry, cfg, root)) for entry in entries]
        for entry, dest in dests:
            source = entry["source"]
            if source not in approved:
                raise ValueError("source needs explicit approval")
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
                if verify(root, [known[entry["id"]]]):
                    raise ValueError("previous download failed checksum verification")
                continue
            available = min(
                cfg["total_bytes"] - sum(sizes.values()),
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
                counts[source] >= cfg[source]["max_files"]
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
                    if length is not None and int(length) > available:
                        print(
                            f"source budget would be exceeded for {entry['id']}",
                            flush=True,
                        )
                        continue
                    while chunk := response.read(
                        min(1_048_576, 1 + available - downloaded)
                    ):
                        downloaded += len(chunk)
                        if downloaded > available:
                            raise ValueError(
                                "download would exceed approved byte ceiling"
                            )
                        out.write(chunk)
                        checksum.update(chunk)
                if not downloaded or (length is not None and downloaded != int(length)):
                    raise ValueError("incomplete download")
                if (
                    source == "commons"
                    and int(entry["bytes"])
                    and downloaded != int(entry["bytes"])
                ):
                    raise ValueError("file changed since discovery")
                got = checksum.hexdigest()
                if entry["sha256"] and got != entry["sha256"]:
                    raise ValueError("download differs from source checksum")
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
                if source == "fivek":
                    pairs.add(row["pair"])
                sizes[source] += downloaded
                counts[source] += 1
                print(
                    f"{source} {counts[source]} · {row['id']} · {downloaded} bytes",
                    flush=True,
                )
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
        by_author.setdefault(r["author"].casefold(), []).append(r)
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
                    author, sha = row["author"].casefold(), row["sha256"]
                    if (
                        roles_by_author.setdefault(author, role) != role
                        or sha in used_hashes
                        or not sha
                    ):
                        raise ValueError(
                            "duplicate photo or author crossing roles in saved split"
                        )
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
        # Validate every group before publishing its paths. Preserve annotated crops on resume.
        for path, value in files.items():
            write_atomic(path, value.encode())
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
        rows = fn(Transport(source, cfg), cap)
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
            fetch(args.root, rows, sources, {s: net}, cfg)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, error.URLError) as exc:
        raise SystemExit(str(exc))
