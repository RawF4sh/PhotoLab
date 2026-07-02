"""Copy-only photo curation for building small style-training sets.

The curator never moves, renames, or deletes the source library. It scans supported images,
measures cheap visual features on a proxy, picks a diverse subset, copies those originals into a
new folder, and writes a manifest + contact sheet for human review.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import xml.sax.saxutils
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .imageio import downscale_long_edge, is_supported, load_image, luma, save_image
from . import aesthetic


@dataclass
class Candidate:
    path: str
    features: dict[str, float]
    bucket: str
    score: float = 0.0


_FEATURE_ORDER = (
    "lum_mean",
    "lum_std",
    "lum_p10",
    "lum_p90",
    "warmth",
    "green",
    "blue_top",
    "top_lum",
    "dark_frac",
    "bright_frac",
    "snow_frac",
    "aspect",
    "edge_mean",
    "edge_p90",
)


def list_images(input_dir: str, recursive: bool = True) -> list[str]:
    root = Path(input_dir)
    if not root.exists():
        return []
    if recursive:
        paths = (p for p in root.rglob("*") if p.is_file())
    else:
        paths = (p for p in root.iterdir() if p.is_file())
    return sorted(str(p) for p in paths if is_supported(str(p)))


def image_features(path: str, long_edge: int = 256) -> dict[str, float]:
    img = downscale_long_edge(load_image(path), long_edge)
    rgb = np.clip(img.rgb.astype(np.float32), 0.0, 1.0)
    yy = luma(rgb)
    h, w = yy.shape
    top = rgb[:max(1, h // 3), :, :]
    top_y = yy[:max(1, h // 3), :]
    rb = (rgb[..., 0] + rgb[..., 2]) * 0.5
    neutral = np.max(rgb, axis=2) - np.min(rgb, axis=2)
    snow = (yy > 0.72) & (neutral < 0.16)
    gy, gx = np.gradient(yy)
    edge = np.sqrt(gx * gx + gy * gy)
    return {
        "lum_mean": float(np.mean(yy)),
        "lum_std": float(np.std(yy)),
        "lum_p10": float(np.percentile(yy, 10)),
        "lum_p90": float(np.percentile(yy, 90)),
        "warmth": float(np.mean(rgb[..., 0] - rgb[..., 2])),
        "green": float(np.mean(rgb[..., 1] - rb)),
        "blue_top": float(np.mean(top[..., 2] - top[..., 0])),
        "top_lum": float(np.mean(top_y)),
        "dark_frac": float(np.mean(yy < 0.18)),
        "bright_frac": float(np.mean(yy > 0.78)),
        "snow_frac": float(np.mean(snow)),
        "aspect": float(w / max(h, 1)),
        "edge_mean": float(np.mean(edge)),
        "edge_p90": float(np.percentile(edge, 90)),
    }


def bucket_for(features: dict[str, float]) -> str:
    if features["snow_frac"] > 0.10:
        return "snow"
    if features["dark_frac"] > 0.45:
        return "dark"
    if features["bright_frac"] > 0.35:
        return "bright"
    if features["blue_top"] > 0.08 and features["top_lum"] > features["lum_mean"]:
        return "blue-sky"
    if features["green"] > 0.08:
        return "green"
    if features["warmth"] > 0.08:
        return "warm"
    if features["warmth"] < -0.08:
        return "cool"
    if features["lum_std"] < 0.08:
        return "flat"
    if features["lum_std"] > 0.24:
        return "contrast"
    return "neutral"


def scan(input_dir: str, recursive: bool = True, long_edge: int = 256,
         scan_limit: int | None = None, failures: list[str] | None = None) -> list[Candidate]:
    """Measure features for every supported image. Undecodable files are skipped but recorded in
    `failures` (when given) — "500 scanned" must be distinguishable from "500 of 520 readable"."""
    images = list_images(input_dir, recursive=recursive)
    if scan_limit is not None:
        images = images[:max(0, scan_limit)]
    out: list[Candidate] = []
    for path in images:
        try:
            feats = image_features(path, long_edge=long_edge)
        except Exception:
            if failures is not None:
                failures.append(path)
            continue
        out.append(Candidate(path=path, features=feats, bucket=bucket_for(feats)))
    return out


def select_diverse(candidates: list[Candidate], count: int) -> list[Candidate]:
    if count <= 0 or not candidates:
        return []
    if count >= len(candidates):
        selected = list(candidates)
        for c in selected:
            c.score = 1.0
        return selected

    mat = np.array([[c.features[k] for k in _FEATURE_ORDER] for c in candidates], dtype=np.float32)
    lo = np.percentile(mat, 5, axis=0)
    hi = np.percentile(mat, 95, axis=0)
    norm = np.clip((mat - lo) / np.maximum(hi - lo, 1e-6), 0.0, 1.0)

    center = np.median(norm, axis=0)
    first = int(np.argmax(np.linalg.norm(norm - center, axis=1)))
    chosen = [first]
    min_dist = np.linalg.norm(norm - norm[first], axis=1)
    min_dist[first] = -1.0

    # Seed with different visual buckets before pure farthest-point selection.
    for bucket in sorted({c.bucket for c in candidates}):
        if len(chosen) >= count:
            break
        idxs = [i for i, c in enumerate(candidates) if c.bucket == bucket and i not in chosen]
        if not idxs:
            continue
        idx = max(idxs, key=lambda i: min_dist[i])
        chosen.append(idx)
        min_dist = np.minimum(min_dist, np.linalg.norm(norm - norm[idx], axis=1))
        min_dist[chosen] = -1.0

    while len(chosen) < count:
        idx = int(np.argmax(min_dist))
        if min_dist[idx] < 0:
            break
        chosen.append(idx)
        min_dist = np.minimum(min_dist, np.linalg.norm(norm - norm[idx], axis=1))
        min_dist[chosen] = -1.0

    selected = [candidates[i] for i in chosen]
    for c, i in zip(selected, chosen):
        c.score = float(np.linalg.norm(norm[i] - center))
    return selected


def quality_score(features: dict[str, float]) -> float:
    """Cheap proxy score for culling: exposure health + contrast/detail - clipping.

    This is intentionally transparent, not magic. It finds likely keepers from large folders;
    the contact sheet is still the final human review.
    """
    lum_mean = features["lum_mean"]
    lum_std = features["lum_std"]
    exposure = 1.0 - min(1.0, abs(lum_mean - 0.46) / 0.40)
    contrast = min(1.0, max(0.0, (lum_std - 0.055) / 0.19))
    detail = min(1.0, max(0.0, features.get("edge_p90", 0.0) / 0.18))
    clipping = min(1.0, features["dark_frac"] * 0.9 + features["bright_frac"] * 1.2)
    flat_penalty = max(0.0, 0.10 - lum_std) * 2.0
    score = 0.38 * exposure + 0.26 * contrast + 0.26 * detail + 0.10 * (1.0 - clipping)
    score -= 0.22 * clipping + flat_penalty
    return float(max(0.0, min(1.0, score)))


def select_best(candidates: list[Candidate], count: int) -> list[Candidate]:
    if count <= 0 or not candidates:
        return []
    ranked = list(candidates)
    for c in ranked:
        c.score = quality_score(c.features)
    ranked.sort(key=lambda c: (c.score, c.features.get("edge_p90", 0.0), c.features["lum_std"]), reverse=True)
    return ranked[:count]


def select_aesthetic(candidates: list[Candidate], count: int, model_path: str) -> list[Candidate]:
    if count <= 0 or not candidates:
        return []
    model = aesthetic.load_model(model_path)
    ranked = list(candidates)
    for c in ranked:
        c.score = aesthetic.predict(c.features, model)
    ranked.sort(key=lambda c: (c.score, c.features.get("edge_p90", 0.0), c.features["lum_std"]), reverse=True)
    return ranked[:count]


def _safe_name(path: str, rank: int, bucket: str) -> str:
    p = Path(path)
    stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in p.stem)[:80]
    ext = p.suffix.lower() or ".jpg"
    return f"{rank:04d}_{bucket}__{stem}{ext}"


def _write_thumb(src: str, dst: str, long_edge: int = 360) -> bool:
    try:
        img = downscale_long_edge(load_image(src), long_edge)
        save_image(img.rgb, dst, fmt="jpeg", quality=90)
        return True
    except Exception:
        return False


def _write_selection_csv(rows: list[dict], path: str) -> None:
    import csv

    fields = ["rank", "bucket", "score", "source_path", "copied_path", "thumb_path"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _xmp_rating(score: float) -> int:
    if score >= 0.78:
        return 5
    if score >= 0.64:
        return 4
    if score >= 0.50:
        return 3
    return 2


def _write_xmp_sidecar(row: dict, copied_path: str) -> str:
    xmp_path = str(Path(copied_path).with_suffix(".xmp"))
    rating = _xmp_rating(float(row["score"]))
    label = "AutoEdit Pick"
    source = xml.sax.saxutils.escape(row["source_path"])
    bucket = xml.sax.saxutils.escape(row["bucket"])
    doc = f"""<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmp:Rating="{rating}"
      xmp:Label="{label}">
      <dc:description>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">AutoEdit curation rank {row['rank']} · {bucket} · source: {source}</rdf:li>
        </rdf:Alt>
      </dc:description>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""
    with open(xmp_path, "w") as fh:
        fh.write(doc)
    return os.path.abspath(xmp_path)


def curate(input_dir: str, out_dir: str, count: int = 200, recursive: bool = True,
           proxy_long_edge: int = 256, scan_limit: int | None = None,
           copy_originals: bool = True, mode: str = "diverse",
           emit_xmp: bool = False, aesthetic_model_path: str | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    selected_dir = os.path.join(out_dir, "selected")
    thumbs_dir = os.path.join(out_dir, "thumbs")
    os.makedirs(selected_dir, exist_ok=True)
    os.makedirs(thumbs_dir, exist_ok=True)

    decode_failures: list[str] = []
    candidates = scan(input_dir, recursive=recursive, long_edge=proxy_long_edge,
                      scan_limit=scan_limit, failures=decode_failures)
    if mode == "best":
        selected = select_best(candidates, count)
    elif mode == "aesthetic":
        if not aesthetic_model_path:
            raise ValueError("mode='aesthetic' requires aesthetic_model_path")
        selected = select_aesthetic(candidates, count, aesthetic_model_path)
    elif mode == "diverse":
        selected = select_diverse(candidates, count)
    else:
        raise ValueError(f"unknown curate mode: {mode}")
    rows = []
    skipped = []
    for cand in selected:
        rank = len(rows) + 1
        name = _safe_name(cand.path, rank, cand.bucket)
        copied = os.path.join(selected_dir, name)
        if copy_originals:
            try:
                shutil.copy2(cand.path, copied)
            except OSError as exc:
                skipped.append({
                    "source_path": os.path.abspath(cand.path),
                    "bucket": cand.bucket,
                    "error": str(exc),
                })
                continue
        thumb = os.path.join(thumbs_dir, f"{Path(name).stem}.jpg")
        has_thumb = _write_thumb(cand.path, thumb)
        rows.append({
            "rank": rank,
            "source_path": os.path.abspath(cand.path),
            "copied_path": os.path.abspath(copied) if copy_originals else "",
            "thumb_path": os.path.abspath(thumb) if has_thumb else "",
            "bucket": cand.bucket,
            "score": round(cand.score, 4),
            "features": {k: round(v, 4) for k, v in cand.features.items()},
        })
        if emit_xmp and copy_originals:
            rows[-1]["xmp_path"] = _write_xmp_sidecar(rows[-1], copied)

    manifest = {
        "input_dir": os.path.abspath(input_dir),
        "out_dir": os.path.abspath(out_dir),
        "total_scanned": len(candidates),
        "decode_failures": len(decode_failures),
        "decode_failure_paths": decode_failures[:50],
        "selected": len(rows),
        "skipped": len(skipped),
        "skipped_items": skipped,
        "copy_originals": copy_originals,
        "recursive": recursive,
        "mode": mode,
        "emit_xmp": emit_xmp,
        "aesthetic_model_path": os.path.abspath(aesthetic_model_path) if aesthetic_model_path else "",
        "items": rows,
    }
    manifest_path = os.path.join(out_dir, "candidates.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    _write_selection_csv(rows, os.path.join(out_dir, "selected.csv"))
    _write_contact_sheet(manifest, os.path.join(out_dir, "contact-sheet.html"))
    return manifest


def _write_contact_sheet(manifest: dict, path: str) -> None:
    cards = []
    for item in manifest["items"]:
        thumb = os.path.relpath(item["thumb_path"], os.path.dirname(path)) if item["thumb_path"] else ""
        title = html.escape(Path(item["source_path"]).name)
        src = html.escape(item["source_path"])
        bucket = html.escape(item["bucket"])
        img = f'<img src="{html.escape(thumb)}" alt="{title}">' if thumb else "<div class='missing'>No preview</div>"
        cards.append(
            f"<article>{img}<h2>{item['rank']:04d} · {bucket}</h2>"
            f"<p>{title}</p><code>{src}</code></article>"
        )
    body = "\n".join(cards)
    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AutoEdit Curation</title>
  <style>
    body {{ margin: 24px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #101114; color: #f3f4f6; }}
    header {{ margin-bottom: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }}
    article {{ background: #181a20; border: 1px solid #30333d; border-radius: 8px; overflow: hidden; }}
    img, .missing {{ width: 100%; aspect-ratio: 4 / 3; object-fit: cover; background: #252832; display: block; }}
    .missing {{ display: grid; place-items: center; color: #9ca3af; }}
    h1 {{ font-size: 22px; margin: 0 0 6px; }}
    h2 {{ font-size: 13px; letter-spacing: .08em; text-transform: uppercase; margin: 10px 12px 4px; color: #cbd5e1; }}
    p {{ margin: 0 12px 8px; font-size: 13px; color: #e5e7eb; overflow-wrap: anywhere; }}
    code {{ display: block; margin: 0 12px 12px; font-size: 11px; color: #9ca3af; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <header>
    <h1>AutoEdit curation</h1>
    <p>{manifest['selected']} selected from {manifest['total_scanned']} scanned. Mode: {html.escape(manifest.get('mode', 'diverse'))}. Originals were copied, not moved.</p>
  </header>
  <main class="grid">
    {body}
  </main>
</body>
</html>
"""
    with open(path, "w") as fh:
        fh.write(doc)
