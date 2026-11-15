"""A small model on this computer reads the instruction clauses the word list did not (F20).

It talks to Ollama over HTTP on the loopback address. Only the clause texts leave this module:
no image, no path, no statistics. Standard library only. Every failure prints one line to stderr
and gives an empty answer; the caller (instructions.translate_problems) validates and clamps
everything that comes back. Ollama not running is not a failure: nothing is asked, nothing is
printed. AUTOEDIT_NO_LOCAL_MODEL=1 switches the model off for a process (the test runners set it
so no test can reach a real model).
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_UP: set[tuple] = set()     # (url, model) pairs seen installed in this process; a miss is asked again


def _base(cfg) -> str | None:
    """The Ollama address, or None when it is not a plain http loopback address: the clause text
    must stay on this machine whatever a settings file says."""
    u = urlparse(cfg.local_url)
    return cfg.local_url.rstrip("/") if u.scheme == "http" and u.hostname in _LOOPBACK else None


def available(cfg) -> bool:
    """True when Ollama answers at cfg.local_url and cfg.local_model is installed (GET /api/tags,
    timeout 1 s). A yes is remembered for the process; never raises."""
    base = _base(cfg)
    if base is None or os.environ.get("AUTOEDIT_NO_LOCAL_MODEL"):
        return False
    key = (base, cfg.local_model)
    if key in _UP:
        return True
    try:
        with urlopen(base + "/api/tags", timeout=1) as resp:
            names = {m.get("name") for m in json.load(resp).get("models", []) if isinstance(m, dict)}
    except Exception:
        return False
    if cfg.local_model not in names:
        return False
    _UP.add(key)
    return True


def _hints(path: str) -> dict:
    """The verbs, amount words, limits and worked examples of instructions.toml: the prompt takes
    the engine's units from the same file the word list reads."""
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _num(v: float) -> str:
    return f"{v:+g}"


def _schema(ops: list[str]) -> dict:
    edit = {"type": "object", "required": ["op", "value"],
            "properties": {"op": {"type": "string", "enum": sorted(ops)}, "value": {"type": "number"}}}
    item = {"type": "object", "required": ["clause", "target", "edits", "size"],
            "properties": {"clause": {"type": "integer"}, "target": {"type": "string"},
                           "edits": {"type": "array", "items": edit}, "size": {"type": "number"}}}
    return {"type": "object", "required": ["adjustments"],
            "properties": {"adjustments": {"type": "array", "items": item}}}


def _system(ops: list[str], places: list[str], cfg) -> str:
    data = _hints(cfg.instructions_path)
    limits = data.get("limits", {})
    units = "; ".join(f"{op} max {limits[op]:g}" if op in limits else op for op in sorted(ops))
    verbs = "\n".join(
        f"- {' / '.join(body.get('words', [name])[:3])}: "
        + ", ".join(f"{op} {_num(v)}" for op, v in body.items() if op in ops)
        for name, body in data.get("verbs", {}).items() if any(op in ops for op in body))
    amounts = ", ".join(f"{w} x{v:g}" for w, v in data.get("amounts", {}).items())
    sizes = ", ".join(f"{w} {v:g}" for w, v in data.get("sizes", {}).items())
    shots = []
    for ex in data.get("translator", {}).get("examples", {}).values():
        answer = {"adjustments": [{"clause": 0, "target": ex["target"], "size": ex.get("size", 1.0),
                                   "edits": [{"op": k, "value": v} for k, v in ex["edits"].items()]}]}
        shots.append(f'Request 0: {ex["text"]}\nAnswer: {json.dumps(answer)}')
    return (
        "You turn numbered photo-editing requests into adjustments for a photo editor. Answer with "
        "JSON only.\n"
        "For each request give clause (its number), target, edits and size. Leave a request out "
        "when it asks for nothing, forbids or excludes something (not, never, don't, leave alone, "
        "without, except), or cannot be done with the ops below.\n"
        "target: one short lower-case noun for the thing in the photo (sky, grass, water, snow, "
        "face, ...), or global for the whole photo. When the request is about a part of the frame, "
        f"a gradient, a vignette or a glow, use one of these places instead: {', '.join(places)}. "
        "edges is a vignette; light is a glow on the sun. A thing such as a sunset, a sky or a "
        "face is a thing, not a place, unless the request asks for a gradient: a gradient over the "
        "sky is the place top, over the ground the place bottom.\n"
        f"edits: op and value pairs, only these ops, each with the largest total allowed: {units}. "
        "exposure is in stops, the others about -100 to 100. Signs: temp positive is warmer "
        "(orange), negative is cooler (blue). tint positive is greener, negative is pinker "
        "(magenta, purple). exposure positive is brighter. A complaint such as \"too X\" or "
        "\"looks X, fix it\" asks for the opposite of X, so the sign is the reverse.\n"
        f"A normal step, which a plain request uses:\n{verbs}\n"
        f"Wording scales a step: {amounts}. Give values of the size of a step, not tiny ones. For "
        "a colour, move only the ops of the nearest colour above.\n"
        f"size: 1, except a gradient, vignette or glow made bigger or smaller: {sizes}. For a "
        "thing in the photo always 1.\n"
        "Examples:\n" + "\n".join(shots))


def translate(clauses: list[str], ops: list[str], places: list[str], cfg) -> list[dict]:
    """Ask the local model for [{clause, target, edits: [{op, value}], size}]. Returns [] when
    the model is not available, on a timeout, on invalid output or any error; the caller
    validates and clamps everything."""
    if not clauses or not available(cfg):
        return []
    base = _base(cfg)
    numbered = "\n".join(f"{i}: {c}" for i, c in enumerate(clauses))
    body = {
        "model": cfg.local_model, "stream": False, "think": False, "keep_alive": "10m",
        "format": _schema(ops),
        "options": {"temperature": 0, "seed": 7, "num_predict": 800},
        "messages": [{"role": "system", "content": _system(ops, places, cfg)},
                     {"role": "user", "content": f"Requests:\n{numbered}"}],
    }
    try:
        req = Request(base + "/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=cfg.local_timeout_s) as resp:
            reply = json.load(resp)
        items = json.loads(reply["message"]["content"])["adjustments"]
        if not isinstance(items, list):
            raise ValueError("the answer has no list of adjustments")
        return [i for i in items if isinstance(i, dict)]
    except Exception as e:
        _UP.discard((base, cfg.local_model))    # Ollama may have stopped: look again next time
        print(f"local instruction translation skipped: {e}", file=sys.stderr)
        return []
