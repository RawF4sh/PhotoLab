"""Edit declaration (F42): what a run changed in each photo, read from its sidecar, with hashes.

Read-only. Lists only what `Result` records. No model, no network, no signing (C2PA is not part of this).
"""

from __future__ import annotations

import html
import json
import os

from .captures import sha256_file
from .contract import Result

STATEMENT = ("AutoEdit adjusts tone, colour, sharpness and geometry. It has no generative tools: "
             "it does not add, remove or replace content.")
_REPAIRS = {"hot_pixels": "stuck-pixel repair (F31)", "denoise": "noise reduction (F7)",
            "denoise_stars": "noise reduction around stars (F7)"}
_OWN = ("lut", "grain", "bw") + tuple(_REPAIRS)      # named in their own fields, not in the plain lists
_EV = ("exposure", "shadows_ev")


def _fmt(settings: dict) -> list[str]:
    """Every non-zero setting as 'name +value'; zero is what edit/ops.py::apply_ops treats as no-op."""
    return [f"{k} {v:+.2f} EV" if k in _EV else f"{k} {v:+g}"
            for k, v in settings.items() if v and k not in _OWN]


def _repairs(*bundles: dict) -> list[str]:
    seen = {k: v for b in bundles for k, v in b.items() if k in _REPAIRS and v}
    return [f"{_REPAIRS[k]} {v:g}" for k, v in seen.items()]


# Notes the pipeline writes only when the step changed the picture (pipeline.process_one); the sidecar
# records these steps nowhere else.
_NOTE_STEPS = ("reference match \u2192", "reference match (region)", "post-reference", "highlight hold:",
               "oversaturation gate:", "skin-safe guard:", "banding gate:", "banding watch:")

WATERMARK_NOTE = ("Exports listed with a watermark carry a mark that the photographer set in an export preset: "
                  "text or a logo laid over one corner, after every other step.")


def _steps(r: Result) -> list[str]:
    """Pixel-changing steps beyond the plain settings, each read from a recorded field or note."""
    a, out = r.audit, []
    if r.decode_ev:
        out.append(f"RAW decode: {r.decode_ev:+.2f} EV at decode")
    if r.exposure_bias:
        out.append(f"shoot consistency: exposure {r.exposure_bias:+.2f} EV toward the group")
    if any(r.wb_bias):
        out.append(f"shoot consistency: white balance {r.wb_bias[0]:+.1f} temp, {r.wb_bias[1]:+.1f} tint")
    if a.get("neutral"):
        out.append(f"white balance from a picked spot: temp {a['neutral']['temp']:+.1f}, tint {a['neutral']['tint']:+.1f}")
    for n in r.notes:
        if n.startswith(_NOTE_STEPS) or (n.startswith("palette:") and not n.startswith("palette: none")):
            out.append(n)
    bd = a.get("backdrop")
    if bd and bd.get("applied"):
        out.append(f"backdrop evened out on {bd['coverage']:.0%} of the frame, taken to {bd['white'] * 255:.0f} of 255 (F34)")
    edge = (a.get("run_config") or {}).get("out_long_edge")
    if edge:
        out.append(f"export scaled down to at most {edge} px on the long edge")
    wm = a.get("watermark")
    if wm:
        what = " and ".join(p for p in ((f"the text {wm['text']!r}" if wm.get("text") else ""),
                                        (f"the logo {wm['logo']}" if wm.get("logo") else "")) if p)
        out.append(f"watermark: {what} laid over the {str(wm.get('corner', '')).replace('_', ' ')} corner of the export")
    return out


def _source_dir(sidecar_dir: str) -> str | None:
    """The run's input folder, from run.json in the sidecar's folder or the one above (export subfolder)."""
    for d in (sidecar_dir, os.path.dirname(sidecar_dir)):
        try:
            with open(os.path.join(d, "run.json"), encoding="utf-8") as fh:
                return json.load(fh)["input_dir"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _hash(path: str | None) -> str | None:
    try:
        return sha256_file(path) if path and os.path.isfile(path) else None
    except OSError:
        return None


def declaration(sidecar_path: str) -> dict:
    """For one photo: source file name (no folder), export file name, SHA-256 of the source and of the
    export (None when a file is missing), the edits, and notes."""
    sidecar_path = os.path.abspath(sidecar_path)
    here = os.path.dirname(sidecar_path)
    with open(sidecar_path, encoding="utf-8") as fh:
        r = Result.model_validate_json(fh.read())
    export = os.path.basename(r.audit.get("output") or "") or None
    src_dir = _source_dir(here)
    src = _hash(os.path.join(src_dir, r.image) if src_dir else None)
    exp = _hash(os.path.join(here, export) if export else None)
    notes = []
    if src is None:
        notes.append("source file not found next to the run record; its hash is not given")
    if exp is None:
        notes.append("export file not found; its hash is not given")
    g = r.global_settings
    lut = None
    if g.get("lut"):
        lut = {"name": (r.audit.get("lut") or {}).get("name"), "amount": g["lut"]}
    geo = None
    if r.geometry and (r.geometry.line or r.geometry.aspect or r.geometry.kept < 1.0):
        gm = r.geometry
        geo = {"straighten_degrees": gm.angle if gm.line else 0.0, "line": gm.line,
               "crop_aspect": gm.aspect, "kept_share": gm.kept}
    return {
        "source": r.image, "export": export, "source_sha256": src, "export_sha256": exp,
        "style": r.style,
        "look": (r.audit.get("run_config") or {}).get("look"),
        "whole_photo": _fmt(g),
        "regions": [{"name": m.target, "coverage": m.coverage, "settings": _fmt(m.ops)}
                    for m in r.masks if _fmt(m.ops)],
        "shapes": [{"name": m.target, "settings": _fmt(m.ops)} for m in r.composition if _fmt(m.ops)],
        "geometry": geo,
        "lut": lut,
        "grain": g.get("grain") or None,
        "black_and_white": g.get("bw") or None,
        "steps": _steps(r),
        "watermark": r.audit.get("watermark"),
        "repairs": _repairs(g, *(m.ops for m in r.masks), *(m.ops for m in r.composition)),
        "notes": notes,
    }


def _page(decls: list[dict]) -> str:
    e = html.escape

    def li(items):
        return "".join(f"<li>{e(str(i))}</li>" for i in items)

    out = []
    for d in decls:
        rows = [("Source", f"{d['source']} (SHA-256 {d['source_sha256'] or 'not available'})"),
                ("Export", f"{d['export'] or 'not recorded'} (SHA-256 {d['export_sha256'] or 'not available'})")]
        body = "".join(f"<p><b>{e(k)}:</b> {e(v)}</p>" for k, v in rows)
        body += f"<h3>Whole photo</h3><ul>{li(d['whole_photo']) or '<li>nothing changed</li>'}</ul>"
        for r in d["regions"]:
            body += f"<h3>Region {e(r['name'])} ({r['coverage']:.0%} of the frame)</h3><ul>{li(r['settings'])}</ul>"
        for s in d["shapes"]:
            body += f"<h3>Shape {e(s['name'])}</h3><ul>{li(s['settings'])}</ul>"
        extra = []
        if d["geometry"]:
            g = d["geometry"]
            extra.append(f"geometry: straightened {g['straighten_degrees']:+.2f} degrees ({g['line']}), "
                         f"crop {g['crop_aspect'] or 'own shape'}, {g['kept_share']:.0%} of the area kept")
        if d["look"] or d["style"]:
            extra.append(f"style {d['style']}, look {d['look']}")
        if d["lut"]:
            extra.append(f"LUT {d['lut']['name']}, {d['lut']['amount']:g} of 100")
        if d["grain"]:
            extra.append(f"film grain {d['grain']:g} of 100")
        if d["black_and_white"]:
            extra.append(f"black and white {d['black_and_white']:g} of 100")
        if extra:
            body += f"<h3>Look and geometry</h3><ul>{li(extra)}</ul>"
        if d["steps"]:
            body += f"<h3>Other steps</h3><ul>{li(d['steps'])}</ul>"
        if d["repairs"]:
            body += ("<h3>Repairs</h3><p>These change pixel values locally.</p>"
                     f"<ul>{li(d['repairs'])}</ul>")
        if d["notes"]:
            body += f"<ul>{li(d['notes'])}</ul>"
        out.append(f"<section><h2>{e(d['source'])}</h2>{body}</section>")
    mark = f"<p>{e(WATERMARK_NOTE)}</p>" if any(d.get("watermark") for d in decls) else ""
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Edit declaration</title>"
            "<style>body{font:15px sans-serif;max-width:46em;margin:2em auto;padding:0 1em}"
            "section{border-top:1px solid #999;margin-top:1.5em}code,p{overflow-wrap:anywhere}</style></head><body>"
            f"<h1>Edit declaration</h1><p>{e(STATEMENT)}</p>{mark}{''.join(out)}</body></html>")


def _build(folder: str, out: str | None) -> tuple[str, str, int, int]:
    decls, skipped = [], 0
    for root, _dirs, files in sorted(os.walk(folder)):
        for f in sorted(files):
            if f.endswith(".autoedit.json"):
                try:
                    decls.append(declaration(os.path.join(root, f)))
                except (OSError, ValueError):       # unreadable or not a sidecar: skip, count
                    skipped += 1
    out = out or folder
    os.makedirs(out, exist_ok=True)
    jp, hp = os.path.join(out, "declaration.json"), os.path.join(out, "declaration.html")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"statement": STATEMENT, "photos": decls}, fh, indent=2)
    with open(hp, "w", encoding="utf-8") as fh:
        fh.write(_page(decls))
    return hp, jp, len(decls), skipped


def write(folder: str, out: str | None) -> tuple[str, str]:
    """declaration.html and declaration.json for every sidecar under folder; returns both paths."""
    return _build(folder, out)[:2]


def cmd_declare(folder: str, out: str | None = None) -> str:
    """For the CLI: write both files, return the text to print."""
    hp, jp, n, k = _build(folder, out)
    return f"{hp}\n{jp}\n{n} photos declared, {k} sidecar{'' if k == 1 else 's'} skipped"
