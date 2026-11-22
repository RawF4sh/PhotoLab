"""A static page of a run's exports (F48): thumbnails that link to the full photos, the file names as captions.

Built from the sidecars (search.index). No script, no outside resource, no absolute path. The full
photos are linked where they are, never copied or embedded. A photo with reveal findings (F21) keeps
its tile and gets a visible warning that names the kinds."""

from __future__ import annotations

import hashlib
import html
import os
import re
from urllib.parse import quote

from . import reveal, search

PAGE, THUMBS = "gallery.html", "gallery"


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def write(folder: str, out: str | None = None, min_coverage: float = 0.02, skip=None) -> dict:
    """Write gallery.html and gallery/<thumbnails> into `out` (default: the folder). Returns {"page",
    "photos", "warned", "skipped"}; skipped counts sidecars without an export and unreadable ones."""
    out = os.path.abspath(out or folder)
    entries, broken = search.index(folder)
    shown = [e for e in entries if e["image"] and os.path.isfile(e["image"])]
    skip = search.skip_words() if skip is None else skip
    os.makedirs(os.path.join(out, THUMBS), exist_ok=True)
    tiles, warned = [], 0
    for e in shown:
        image = os.path.abspath(e["image"])
        rel = os.path.relpath(image, out).replace(os.sep, "/")
        name = os.path.basename(image)
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(name)[0])[:60]
        # The same export keeps the same thumbnail name, so a second run overwrites it in place.
        thumb = f"{THUMBS}/{stem}-{hashlib.blake2b(rel.encode('utf-8'), digest_size=4).hexdigest()}.jpg"
        alt = ", ".join(search.keywords(e, min_coverage, skip)) or name
        pic = (f'<img src="{quote(thumb)}" alt="{_e(alt)}" loading="lazy">'
               if reveal._thumb(image, os.path.join(out, thumb)) else "<span>no preview</span>")
        warn = ""
        if e["reveal_labels"]:
            warned += 1
            warn = f'<p class="warn">Check before sharing: {_e(", ".join(e["reveal_labels"]))}</p>'
        tiles.append(f'<figure><a href="{quote(rel)}">{pic}</a><figcaption>{_e(name)}</figcaption>{warn}</figure>')
    title = _e(os.path.basename(os.path.abspath(folder)) or "Photos")
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title><style>"
            "body{margin:0;padding:1rem;font:15px/1.4 system-ui,sans-serif;background:#111;color:#eee}"
            "h1{font-size:1.2rem;font-weight:600}"
            "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,16rem),1fr));gap:1rem}"
            "figure{margin:0}img{display:block;width:100%;height:auto}a{color:inherit}"
            "figcaption{margin-top:.3rem;font-size:.85rem;color:#bbb;overflow-wrap:anywhere}"
            ".warn{margin:.3rem 0 0;padding:.2rem .4rem;background:#fc3;color:#111;font-size:.85rem}"
            f"</style></head><body><h1>{title}</h1><main>{''.join(tiles)}</main></body></html>\n")
    path = os.path.join(out, PAGE)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return {"page": path, "photos": len(shown), "warned": warned, "skipped": len(entries) - len(shown) + broken}
