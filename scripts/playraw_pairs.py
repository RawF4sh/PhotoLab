"""Find PIXLS.US Play Raw topics and build a private acquisition plan from reviewed posts.

Run with python -I. 'discover' stores only public topic metadata; it never requests a photo.
Review the authors, subject, original, finished rendition and licence scope in those posts.
A selection JSON lists topic, scene, raw_grant and edit_grant. Copy the author's exact words
immediately before the licence link into each grant field (for example, "These files are licensed").
Select the corresponding assets by zero-based raw_index and edit_index from 'inspect'. Both
indices default to zero. For an independently licensed reply, use edit_post for its post number.
Each selected author must explicitly license the associated file. A single
nonspecific "this file is licensed" below multiple files is insufficient. Do not select an
out-of-camera preview or an illustrative screenshot.

  python -I scripts/playraw_pairs.py discover --query '"these files are licensed" fog'
  python -I scripts/playraw_pairs.py inspect 12345
  python -I scripts/playraw_pairs.py plan --selection curated-web/auto-pairs/selection.json

Topic metadata, evidence, and planned image URLs stay under curated-web/auto-pairs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib import parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_pairs
import web_corpus as web

FORUM = "https://discuss.pixls.us"
CATEGORY = 30
SOURCE = "playraw"
RAW = web.EXTENSIONS - set("jpg jpeg tif tiff png webp avif heic heif".split())
MEDIA = {"jpg", "jpeg", "png", "tif", "tiff", "webp"}
CDN = "d2x313g9lpht1q.cloudfront.net"
UPLOAD_HOSTS = {CDN, "pixls-discuss.s3.dualstack.us-east-1.amazonaws.com", "pixls-discuss.s3.amazonaws.com"}


class Attachments(HTMLParser):
    """Read links in the post body. Quoted attachments and licence notices belong to others."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.quoted = 0
        self.current = None
        self.body = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("aside", "blockquote"):
            self.quoted += 1
        if self.quoted:
            return
        if tag == "img" and self.current is None and attrs.get("src"):
            try:
                width, height = int(attrs.get("width", 0)), int(attrs.get("height", 0))
            except ValueError:
                width = height = 0
            if min(width, height) >= 160:
                self.links.append({"href": attrs["src"], "title": attrs.get("alt", ""), "text": "",
                                   "position": len(self.body), "class": "post-image"})
        if tag == "a" and attrs.get("href"):
            self.current = dict(attrs, text="", position=len(self.body))
        if tag in ("p", "br", "div"):
            self.body += "\n"

    def handle_data(self, data):
        if not self.quoted:
            self.body += data
            if self.current is not None:
                self.current["text"] += data

    def handle_endtag(self, tag):
        if self.quoted:
            if tag in ("aside", "blockquote"):
                self.quoted -= 1
            return
        if tag == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None
        if tag in ("p", "div"):
            self.body += "\n"


def attachments(topic, post_number=1):
    if topic.get("category_id") != CATEGORY:
        raise ValueError("topic is outside the PIXLS.US Play Raw category")
    post = next((p for p in topic["post_stream"]["posts"] if p.get("post_number") == post_number), None)
    if post is None:
        raise ValueError(f"post {post_number} is missing from the reviewed topic")
    parsed = Attachments()
    parsed.feed(post["cooked"])
    items = {"raw": [], "edit": []}
    grants = []
    for link in parsed.links:
        url = parse.urljoin(FORUM, link["href"])
        u = parse.urlsplit(url)
        if u.hostname == "creativecommons.org":
            grants.append((url, link["position"]))
            continue
        ext = Path(u.path).suffix.lower().lstrip(".")
        if (u.hostname == "discuss.pixls.us" and u.path.startswith("/uploads/short-url/")) or (
                u.hostname in UPLOAD_HOSTS and u.path.startswith("/original/")):
            classes = set(link.get("class", "").split())
            if ext in RAW and ("attachment" in classes or "|attachment" in link["text"]):
                role = "raw"
            elif ext in MEDIA and classes.intersection({"lightbox", "attachment", "post-image"}):
                role = "edit"
            else:
                continue
            web.safe_url(url, ["discuss.pixls.us", *UPLOAD_HOSTS])
            items[role].append({"url": url, "name": link.get("title") or link["text"].strip(),
                                "position": link["position"]})
    if not grants:
        # CC0 has one version. An explicit written dedication can identify it without a link.
        # Capture the position after the dedication so the review records its exact wording.
        pattern = r"(?im)(?:^|[.!?]\s+)\s*((?:both|all (?:of )?(?:these |the )?(?:files|images|photos)) are CC0)\s*[.!]?(?=\s|$)"
        if sum(map(len, items.values())) == 1:
            pattern += r"|(?im:^\s*(licen[cs]e:\s*CC0)\s*[.!]?\s*$)"
        for match in re.finditer(pattern, parsed.body):
            grants.append(("https://creativecommons.org/publicdomain/zero/1.0/", match.end(match.lastindex)))
    return post, parsed.body, items, grants


def licence_of(url):
    u = parse.urlsplit(url)
    match = re.fullmatch(r"/licenses/(by(?:-sa)?)/(\d\.\d)/?", u.path)
    if match:
        name = "CC " + match[1].upper() + " " + match[2]
    elif u.path.rstrip("/") == "/publicdomain/zero/1.0":
        name = "CC0"
    else:
        return ""
    return web.commons_license(name, url)


def reviewed_asset(topic, number, role, selection):
    post, body, files, grants = attachments(topic, number)
    licence_urls = {url.rstrip("/") for url, _ in grants}
    if len(licence_urls) != 1 or any(not licence_of(url) for url in licence_urls):
        raise ValueError("missing, mixed or ineligible Creative Commons grants")
    index = selection.get(f"{role}_index", 0)
    if type(index) is not int or not 0 <= index < len(files[role]):
        raise ValueError(f"selected {role} attachment is missing")
    attachment = files[role][index]
    phrase = selection[f"{role}_grant"]
    if not isinstance(phrase, str) or len(phrase.split()) < 2:
        raise ValueError("a reviewed licence statement is required for each attachment")
    matches = []
    for _, position in grants:
        preceding = " ".join(body[max(0, position - 300):position].split())
        if preceding.rstrip(" ([:").casefold().endswith(phrase.casefold()):
            matches.append(position)
    if not matches:
        raise ValueError("licence statement does not precede the linked grant")
    # A singular caption must follow the selected attachment, before the next photograph.
    # Read plural grants in context when selecting: "these RAW files" can exclude a JPEG.
    if not re.search(r"\b(files|images|photos|pictures|works|both|all)\b", phrase, re.I):
        next_photo = min((f["position"] for group in files.values() for f in group
                          if f["position"] > attachment["position"]), default=len(body))
        if not any(attachment["position"] < position < next_photo for position in matches):
            raise ValueError("singular licence statement does not identify this file")
    licence_url = grants[0][0]
    side = "before" if role == "raw" else "after"
    file = {"role": side, "url": attachment["url"], "licence": licence_of(licence_url),
            "licence_url": licence_url, "author": post["username"]}
    evidence = {"role": side, "page": f"{FORUM}/t/{topic['id']}/{number}", "statement": phrase,
                "licence_url": licence_url, "author": post["username"]}
    return file, evidence


def reviewed_pair(topic, selection):
    if selection["scene"] not in auto_pairs.pair_goals.SCENES:
        raise ValueError("unknown landscape scene")
    original, original_grant = reviewed_asset(topic, 1, "raw", selection)
    edit, edit_grant = reviewed_asset(topic, selection.get("edit_post", 1), "edit", selection)
    if original["author"] != edit["author"]:
        if edit["licence"] == "CC0":
            # An editor can dedicate their contribution, but the original photographer's
            # conditions still govern the photograph. Keep both credits and source statements.
            edit["licence"], edit["licence_url"] = original["licence"], original["licence_url"]
        elif original["licence"] != "CC0" and edit["licence"] != original["licence"]:
            raise ValueError("mixed original and retouch terms need a separate rights review")
        edit["author"] = f"{original['author']} (photograph), {edit['author']} (edit)"
    return {"source": SOURCE, "scene": selection["scene"], "page": original_grant["page"],
            "topic_id": topic["id"], "evidence": [original_grant, edit_grant], "files": [original, edit]}


def topic_info(root, topic, net):
    number = int(topic)
    file = root / "discovery" / "topics" / f"{number}.json"
    if file.exists():
        data = auto_pairs.read(file)
    else:
        data = json.loads(web.metadata(net, f"{FORUM}/t/{number}.json"))
    if data.get("id") != number or data.get("category_id") != CATEGORY:
        raise ValueError("topic is outside the selected Play Raw collection")
    if not file.exists():
        auto_pairs.save(file, data)
    return data


def main():
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--root", type=Path, default=auto_pairs.FOLDER)
    sub = cli.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--query", required=True)
    discover.add_argument("--page", type=int, default=1)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("topics", nargs="+", type=int)
    plan = sub.add_parser("plan")
    plan.add_argument("--selection", type=Path, required=True)
    args = cli.parse_args()
    approval = auto_pairs.read(args.root / "approval.json")
    cfg = auto_pairs.config(approval)
    if "discuss.pixls.us" not in cfg.get(SOURCE, {}).get("hosts", []):
        cli.error("PIXLS.US Play Raw must be an approved source")
    net = web.Transport(SOURCE, cfg)
    if args.command == "discover":
        query = parse.urlencode({"q": args.query + " category:playraw", "page": args.page})
        topics = json.loads(web.metadata(net, f"{FORUM}/search.json?{query}")).get("topics", [])
        ids = [t["id"] for t in topics if t.get("category_id") == CATEGORY]
        for i in ids:
            print(i, topic_info(args.root, i, net)["title"])
    elif args.command == "inspect":
        for i in args.topics:
            t = topic_info(args.root, i, net)
            _, body, files, _ = attachments(t)
            print(i, t["title"], "\n", " ".join(body.split()), "\n",
                  {k: [f["name"] for f in group] for k, group in files.items()})
    else:
        selected = auto_pairs.read(args.selection)
        if len(selected) > approval["max_pairs"]:
            cli.error("selection exceeds the approved pair count")
        pairs = [reviewed_pair(topic_info(args.root, r["topic"], net), r) for r in selected]
        auto_pairs.candidates(pairs, cfg, args.root)
        auto_pairs.save(args.root / "plan.json", pairs)
        print(len(pairs), dict(Counter(p["scene"] for p in pairs)))


if __name__ == "__main__":
    main()
