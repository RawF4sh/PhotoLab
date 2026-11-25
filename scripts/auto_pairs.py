"""Acquire and screen public forum pairs for Auto, separately from the F60 research set.

Run with python -I from the project, outside curated-web/. All ledgers and thumbnails are private.
fetch reads an owner approval JSON and a list of candidate pairs. An approval has max_pairs, an optional
max_bytes ceiling within the existing F60 budget, and sources (source slug -> list of HTTPS hostnames). A candidate has source, scene,
page and files (one horizontal/vertical composite or a before and an after). Each file has
role, url, licence, licence_url and author. The plan must document reusable rights on BOTH files.
The same approval and source allowlist are rechecked before a downloaded image can leave for
screening. Gemini receives only the two small, freshly encoded public image previews.

  python -I scripts/auto_pairs.py fetch --approval APPROVAL.json --plan PAIRS.json
  python -I scripts/auto_pairs.py screen-local --limit 10
  python -I scripts/auto_pairs.py screen --allow-gemini
  python -I scripts/auto_pairs.py audit --scene mist
  python -I scripts/auto_pairs.py audit --scene mist --decisions REVIEW.json

The approval's screening.requested selects "local" or "gemini"; screening.eligible
must be true for that provider. Local inference uses the installed vision weights on
the loopback service. The judgments record the model and its weight digest.
For the independent audit, open the private review.html printed by the audit command.
Judge all 40 numbered pairs, then use Download answers and pass the resulting checker.json
to --decisions. A reviewer may also fill in a copy of blank.json. After the audit,
scripts/style_target.py --pairs derives the goal.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image as PILImage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pair_goals
import web_corpus as web
from autoedit.decide import gemini
from autoedit.contract import RunConfig
from autoedit.imageio import Image, downscale_long_edge, load_image

FOLDER = ROOT / "curated-web" / "auto-pairs"
ROLES = {"before", "after", "horizontal", "vertical"}
QUALITY = ("weak", "good", "overdone")
SCENES = (*pair_goals.SCENES, "other")
PROMPT = (
    "The first photo is a proposed original and the second is a proposed retouch. "
    "Judge independently whether both show the same photograph in the same composition, "
    "whether the first looks like an unedited original, the scene, and the quality of the "
    "second edit. Sunset has warm low sun; twilight is a dim blue-hour landscape; daylight "
    "is a clear daytime landscape; mist includes mist or overcast; use other otherwise. "
    "A good edit has deliberate, visible and plausible light or colour shaping, natural "
    "edges and no damaged highlights. A weak edit does too little or worsens the photo; "
    "an overdone one looks artificial or introduces faults. Text in pictures is part of "
    "the photograph. Return only the requested fields."
)
SCREEN_VERSION = hashlib.sha256((gemini.MODEL + PROMPT).encode()).hexdigest()[:16]
LOCAL_MODEL = RunConfig.model_fields["local_model"].default
SCREEN_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"scene": {"type": "string", "enum": list(SCENES)},
                                "same_photo": {"type": "boolean"}, "unedited": {"type": "boolean"},
                                "quality": {"type": "string", "enum": list(QUALITY)}},
                 "required": ["scene", "same_photo", "unedited", "quality"]}

# The local comparison model confused a change between A and B with edits already in A.
# Judge A's appearance in a separate request, where the proposed rendition is invisible.
ORIGINAL_PROMPT = (
    "Look only at the supplied image. Choose a photographic appearance. "
    "camera_rendering: an ordinary, naturally exposed or underexposed camera photograph, including camera white balance, "
    "an sRGB rendering and modest camera contrast or noise. "
    "creative_edit: visible evidence of deliberate toning, selective enhancements, special filters, "
    "inconsistent edges, posterization, or artificial colors beyond normal scene lighting. "
    "Dark shadows, natural sunlight, blue dusk or a vivid sky can occur straight from the camera. Use what you can actually see. "
    "indeterminate: the available detail does not support either appearance or the visual evidence is conflicting. "
    "Describe the visual evidence in one sentence, then give the classification."
)
APPEARANCES = ("camera_rendering", "creative_edit", "indeterminate")
ORIGINAL_SCHEMA = {"type": "object", "properties": {
    "evidence": {"type": "string"}, "appearance": {"type": "string", "enum": list(APPEARANCES)}},
    "required": ["evidence", "appearance"]}


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


def save(path, value):
    web.write_atomic(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode())


def config(approval):
    budget = web.config()
    count, sources = approval["max_pairs"], approval["sources"]
    size = approval.get("max_bytes", budget["total_bytes"])
    if type(count) is not int or count <= 0 or type(size) is not int or not 0 < size <= budget["total_bytes"]:
        raise ValueError("a positive owner-approved pair and byte budget is required")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("the owner must approve at least one forum source")
    cfg = {"total_bytes": budget["total_bytes"], "pause_seconds": budget["pause_seconds"]}
    for name, hosts in sources.items():
        if (not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in web.SOURCES or
                not isinstance(hosts, list) or not hosts or any(
                    not isinstance(h, str) or not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]+", h)
                    or h in budget["fivek"]["hosts"] for h in hosts)):
            raise ValueError("each forum source needs its explicitly approved public hostnames")
        cfg[name] = {"hosts": hosts, "max_bytes": size, "max_files": 2 * count}
    return cfg


def row_check(row, cfg, root):
    src, pair = row["source"], row["pair"]
    if src not in cfg or not isinstance(cfg[src], dict) or src in web.SOURCES:
        raise ValueError("source is not an approved editing forum")
    web.safe_url(row["page"], cfg[src]["hosts"])
    web.safe_url(row["url"], cfg[src]["hosts"])
    if not row["licence_url"] or not web.commons_license(row["licence"], row["licence_url"]):
        raise ValueError("each image requires an eligible reuse licence and its URL")
    if (not re.fullmatch(r"pair-[0-9a-f]{20}", pair) or row["kind"] not in pair_goals.SCENES or
            row["role"] not in ROLES or row["track"] != "L" or int(row["bytes"]) < 0):
        raise ValueError("invalid forum pair, expected scene, or image role")
    canonical = web.candidate(src, "L", row["kind"], row["role"], row["url"], row["page"],
                              row["licence"], row["author"], pair=pair)
    path = (root / row["file"]).resolve()
    if canonical["id"] != row["id"] or canonical["file"] != row["file"] or root.resolve() not in path.parents:
        raise ValueError("invalid forum pair location")
    return path


def candidates(plan, cfg, root):
    rows = []
    for pair in plan:
        files = pair["files"]
        roles = sorted(f["role"] for f in files)
        if roles not in (["after", "before"], ["horizontal"], ["vertical"]):
            raise ValueError("provide one composite or both the before and the after")
        urls = sorted(f["url"] for f in files)
        key = "pair-" + hashlib.sha256(json.dumps(urls).encode()).hexdigest()[:20]
        for f in files:
            row = web.candidate(pair["source"], "L", pair["scene"], f["role"], f["url"], pair["page"],
                                f["licence"], f["author"], pair=key, licence_url=f["licence_url"],
                                size=f.get("bytes", 0), sha256=f.get("sha256", ""))
            row_check(row, cfg, root)
            rows.append(row)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("the same source image occurs in multiple proposed pairs")
    return rows


def collect(root, plan, approval, net=None):
    cfg = config(approval)
    ceiling = approval.get("max_bytes", cfg["total_bytes"])
    entries = candidates(plan, cfg, root)
    net = net or {s: web.Transport(s, cfg) for s in approval["sources"]}
    with web.lock(root.parent):
        prior = web.manifest(root)
        if any(r["source"] not in approval["sources"] for r in prior):
            raise ValueError("the approved source list must cover the existing forum-pair ledger")
        # One global spending limit across sources, and the F60 byte allowance shared by
        # the previous raw/research/finished files and these forum pairs.
        other = sum(int(r["bytes"]) for r in web.manifest(root.parent))
        cfg["total_bytes"] = min(cfg["total_bytes"], other + ceiling)
        failures = []
        try:
            web.fetch(root, entries, set(approval["sources"]), net, cfg, failures,
                      row_check=row_check, pair_budget=approval["max_pairs"], other_bytes=other)
        finally:
            save(root / "approval.json", approval)
            save(root / "fetch-report.json", {"max_pairs": approval["max_pairs"],
                 "max_bytes": ceiling, "pairs_started": len({r["pair"] for r in web.manifest(root)}),
                 "bytes_downloaded": sum(int(r["bytes"]) for r in web.manifest(root)), "failures": failures})


def public_pairs(root):
    cfg = config(read(root / "approval.json"))
    groups = defaultdict(dict)
    for row in web.manifest(root):
        path = row_check(row, cfg, root)
        if int(row["bytes"]) != path.stat().st_size or web.digest(path) != row["sha256"]:
            raise ValueError("a public pair differs from its fetch ledger; verify or fetch it again")
        sides = groups[row["pair"]]
        if row["role"] in sides:
            raise ValueError("duplicate role in pair ledger")
        sides[row["role"]] = row
    pairs = {}
    for key, sides in groups.items():
        if sorted(sides) not in (["after", "before"], ["horizontal"], ["vertical"]):
            continue  # partial acquisition has no usable pair
        if len({(r["kind"], r["source"], r["page"]) for r in sides.values()}) != 1:
            raise ValueError("inconsistent pair provenance")
        pairs[key] = sides
    return pairs


def views(root, sides, edge=512):
    # A composite is allowed twice the proxy size until its halves have been separated.
    def image(role, limit):
        img = load_image(str(root / sides[role]["file"]))
        return downscale_long_edge(img, limit).rgb
    if "before" in sides:
        return image("before", edge), image("after", edge)
    role = next(iter(sides))
    return pair_goals.split_pair(image(role, edge * 2), role)


def signature(sides):
    return hashlib.sha256(json.dumps(sorted((role, r["sha256"], r["kind"]) for role, r in sides.items())).encode()).hexdigest()


def judgment(value):
    if (not isinstance(value, dict) or set(value) != {"scene", "same_photo", "unedited", "quality"}
            or value["scene"] not in SCENES or value["quality"] not in QUALITY
            or type(value["unedited"]) is not bool or type(value["same_photo"]) is not bool):
        raise ValueError("invalid screening answer")
    return value


def accepted(scene, report):
    return report["scene"] == scene and report["same_photo"] and report["unedited"] and report["quality"] == "good"


def thumbnail(rgb):
    image = PILImage.fromarray(np.uint8(np.clip(rgb * 255 + .5, 0, 255)))
    image.thumbnail((gemini.MAX_THUMB_PX, gemini.MAX_THUMB_PX), PILImage.Resampling.LANCZOS)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=82)
    return stream.getvalue()


def gemini_screen(images, api_key):
    if not api_key:
        raise ValueError("set GEMINI_FREE_API_KEY to a free-tier project key before screening")
    parts = [{"text": PROMPT}, *[{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(
        gemini._thumb(thumbnail(rgb))).decode("ascii")}} for rgb in images]]
    request = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"candidateCount": 1,
               "maxOutputTokens": 512, "thinkingConfig": {"thinkingLevel": "MINIMAL", "includeThoughts": False},
               "responseMimeType": "application/json", "responseJsonSchema": SCREEN_SCHEMA}}
    answer = gemini._strict_json(gemini._transport(json.dumps(request).encode(), api_key))
    cs = answer.get("candidates", [])
    if len(cs) != 1 or cs[0].get("finishReason") != "STOP" or answer.get("promptFeedback", {}).get("blockReason"):
        raise ValueError("screening model did not return a complete judgment")
    parts = cs[0].get("content", {}).get("parts", [])
    if len(parts) != 1 or not isinstance(parts[0].get("text"), str):
        raise ValueError("invalid screening model response")
    return judgment(gemini._strict_json(parts[0]["text"]))


def local_request(endpoint, payload=None):
    """Direct loopback connection. HTTP redirects and ambient proxy settings do not apply."""
    if os.environ.get("AUTOEDIT_NO_LOCAL_MODEL"):
        raise ValueError("local models are disabled for this process")
    conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=120)
    try:
        conn.request("GET" if payload is None else "POST", endpoint,
                     body=json.dumps(payload).encode() if payload is not None else None,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"local vision service returned HTTP {response.status}")
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("local vision service response is too large")
        return gemini._strict_json(data)
    finally:
        conn.close()


def local_weights():
    tags = local_request("/api/tags")
    digest = next((m.get("digest", "") for m in tags.get("models", []) if m.get("name") == LOCAL_MODEL), "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError(f"local vision model {LOCAL_MODEL} is not installed")
    info = local_request("/api/show", {"model": LOCAL_MODEL})
    if (info.get("remote_model") or info.get("remote_host") or "vision" not in info.get("capabilities", [])
            or not info.get("model_info", {}).get("general.parameter_count")):
        raise ValueError("screening requires installed vision weights on this computer")
    return digest


def local_answer(parts, prompt, schema, max_tokens, expected_weights):
    if local_weights() != expected_weights:
        raise ValueError("local vision weights changed during the screening run")
    answer = local_request("/api/chat", {
        "model": LOCAL_MODEL, "stream": False, "think": False, "format": schema,
        "messages": [{"role": "user", "content": prompt, "images": parts}],
        "options": {"temperature": 0, "seed": 0, "num_predict": max_tokens}})
    if not answer.get("done") or answer.get("done_reason") != "stop" or answer.get("model") != LOCAL_MODEL:
        raise ValueError("local vision model did not return a complete judgment")
    content = answer.get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("local vision model returned an invalid judgment")
    return gemini._strict_json(content)


def valid_appearance(original):
    return (isinstance(original, dict) and set(original) == {"appearance", "evidence"}
            and original["appearance"] in APPEARANCES
            and isinstance(original["evidence"], str) and bool(original["evidence"].strip()))


def local_screen(images, expected_weights):
    before, after = (base64.b64encode(thumbnail(rgb)).decode("ascii") for rgb in images)
    original = local_answer([before], ORIGINAL_PROMPT, ORIGINAL_SCHEMA, 180, expected_weights)
    if not valid_appearance(original):
        raise ValueError("invalid original-appearance assessment")
    pair = judgment(local_answer([before, after], PROMPT, SCREEN_SCHEMA, 160, expected_weights))
    pair["unedited"] = original["appearance"] == "camera_rendering"
    return {**pair, "original_appearance": original}


def approved_screen(root, expected=None):
    policy = read(root / "approval.json").get("screening", {})
    provider = policy.get("requested")
    if policy.get("eligible") is not True or provider not in ("local", "gemini") or (expected and expected != provider):
        raise ValueError("record an eligible, owner-approved screening provider before screening or using its judgments")
    return provider


def screen_file(root, provider):
    return root / ("screen-local.json" if provider == "local" else "screen.json")


def audit_folder(root, wanted):
    provider = approved_screen(root)
    return root / ("audit-local" if provider == "local" else "audit") / wanted


def screen_version(provider, weights):
    if provider == "gemini":
        return SCREEN_VERSION
    description = json.dumps([LOCAL_MODEL, weights, ORIGINAL_PROMPT, ORIGINAL_SCHEMA, PROMPT, SCREEN_SCHEMA,
                              "local-v2"], sort_keys=True)
    return hashlib.sha256(description.encode()).hexdigest()[:16]


def screenings(root, pairs, *, weights=None):
    provider = approved_screen(root)
    path = screen_file(root, provider)
    data = read(path) if path.exists() else {}
    current = {}
    for key, row in data.items():
        recorded = row.get("weights", "")
        if provider == "local" and (not re.fullmatch("[0-9a-f]{64}", recorded) or (weights and weights != recorded)):
            continue
        if (key in pairs and row.get("provider", "gemini") == provider and row["signature"] == signature(pairs[key])
                and row["version"] == screen_version(provider, recorded) and judgment(row["answer"])):
            if provider == "local":
                appearance = row.get("original_appearance")
                if (not valid_appearance(appearance)
                        or row["answer"]["unedited"] != (appearance["appearance"] == "camera_rendering")):
                    continue
            current[key] = row
    if len({r["version"] for r in current.values()}) > 1:
        raise ValueError("screening ledger contains mixed model versions; rerun the approved screen")
    return current


def scene(sides):
    return next(iter(sides.values()))["kind"]


def screen(root, judge, *, provider="gemini", weights=None, limit=None):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("screening limit must be a positive number of pairs")
    with web.lock(root):
        approved_screen(root, provider)
        if provider == "local" and not re.fullmatch(r"[a-f0-9]{64}", weights or ""):
            raise ValueError("local screening needs the installed model's weight digest")
        pairs = public_pairs(root)
        results = screenings(root, pairs, weights=weights)
        completed = 0
        for k, sides in pairs.items():
            if k in results:
                continue
            # Acquisition may checkpoint a revised approval while a long batch is running.
            approved_screen(root, provider)
            reply = judge(views(root, sides))
            appearance = None
            if provider == "local":
                if not isinstance(reply, dict) or not valid_appearance(reply.get("original_appearance")):
                    raise ValueError("invalid original-appearance assessment")
                appearance = reply["original_appearance"]
                reply = {key: value for key, value in reply.items() if key != "original_appearance"}
            result = judgment(reply)
            if appearance and result["unedited"] != (appearance["appearance"] == "camera_rendering"):
                raise ValueError("original-appearance assessment and screening decision disagree")
            results[k] = {"signature": signature(sides), "version": screen_version(provider, weights), "answer": result,
                          "provider": provider, "model": LOCAL_MODEL if provider == "local" else gemini.MODEL}
            if appearance is not None:
                results[k]["original_appearance"] = appearance
            if weights:
                results[k]["weights"] = weights
            save(screen_file(root, provider), results)
            completed += 1
            if completed == limit:
                break
        return dict(Counter(scene(pairs[k]) for k, r in results.items() if accepted(scene(pairs[k]), r["answer"])))


def audit_sample(pairs, results, wanted):
    """Reproducible and blind: a reviewer sees both pictures but not the model result."""
    if any(scene(sides) == wanted and key not in results for key, sides in pairs.items()):
        raise ValueError(f"{wanted} still has unscreened pairs")
    selected = []
    for keep in (True, False):
        keys = [k for k, s in pairs.items() if k in results and scene(s) == wanted
                and accepted(wanted, results[k]["answer"]) == keep]
        if len(keys) < 20:
            raise ValueError(f"{wanted} needs 20 kept and 20 refused judgments for the audit")
        selected.extend(sorted(keys, key=lambda k: hashlib.sha256(("sample:" + k).encode()).digest())[:20])
    return sorted(selected, key=lambda k: hashlib.sha256(("audit:" + k).encode()).digest())


def write_review_form(directory, blind):
    """Embed only the frozen sample identifiers; the browser can open this form offline."""
    template = Path(__file__).with_name("data").joinpath("pair_review.html").read_text(encoding="utf8")
    embedded = json.dumps({field: blind[field] for field in ("screen_digest", "sample")}).replace("<", "\\u003c")
    web.write_atomic(directory / "review.html", template.replace("__BLIND_MANIFEST__", embedded).encode("utf8"))


def audit(root, wanted, decisions=None):
    pairs = public_pairs(root)
    results = screenings(root, pairs)
    sample = audit_sample(pairs, results, wanted)
    directory = audit_folder(root, wanted)
    directory.mkdir(parents=True, exist_ok=True)
    if decisions is None:
        entries = {}
        for i, key in enumerate(sample, 1):
            photos = [PILImage.open(io.BytesIO(thumbnail(v))) for v in views(root, pairs[key])]
            sheet = PILImage.new("RGB", (sum(im.width for im in photos), max(im.height for im in photos)))
            sheet.paste(photos[0], (0, 0))
            sheet.paste(photos[1], (photos[0].width, 0))
            sheet.save(directory / f"{i:02d}.jpg", quality=90)
            entries[str(i)] = {"scene": None, "same_photo": None, "unedited": None, "quality": None}
        blind = {"screen_digest": screen_digest(pairs, results, wanted), "sample": sample, "answers": entries}
        save(directory / "blank.json", blind)
        write_review_form(directory, blind)
        return
    recorded = read(decisions)
    if recorded.get("screen_digest") != screen_digest(pairs, results, wanted) or recorded.get("sample") != sample:
        raise ValueError("these reviewer labels refer to a different screening run")
    answers = recorded["answers"]
    if set(answers) != {str(i) for i in range(1, 41)}:
        raise ValueError("the reviewer must label all 40 pairs")
    errors = Counter()
    for i, key in enumerate(sample, 1):
        model = results[key]["answer"]
        human = judgment(answers[str(i)])
        if human != model:
            errors["kept" if accepted(wanted, model) else "refused"] += 1
    report = {"sample": sample, "version": results[sample[0]]["version"], "provider": approved_screen(root),
              "screen_digest": screen_digest(pairs, results, wanted),
              "kept_errors": errors["kept"], "refused_errors": errors["refused"],
              "passed": errors["kept"] <= 3}
    save(directory / "result.json", report)
    return report


def screen_digest(pairs, results, wanted):
    active = {k: v for k, v in results.items() if scene(pairs[k]) == wanted}
    return hashlib.sha256(json.dumps(active, sort_keys=True).encode()).hexdigest()


def eligible(root, wanted):
    pairs = public_pairs(root)
    results = screenings(root, pairs)
    scenes = pair_goals.SCENES if wanted == "landscape" else (wanted,)
    used_before, chosen = set(), {}
    for fine in scenes:
        if any(scene(s) == fine and key not in results for key, s in pairs.items()):
            if wanted == "landscape":
                continue
            raise ValueError(f"{fine} still has unscreened pairs")
        available = sorted(k for k, s in pairs.items() if scene(s) == fine
                           and accepted(fine, results[k]["answer"]))
        if not available:
            continue
        path = audit_folder(root, fine) / "result.json"
        report = read(path) if path.exists() else {}
        if not report.get("passed") or report.get("screen_digest") != screen_digest(pairs, results, fine):
            if wanted == "landscape":
                continue
            raise ValueError(f"{fine} needs a passing independent audit of its current screen")
        for k in available:
            sides = pairs[k]
            # An identical before may appear in several composites with different
            # afters. Hash a standard-sized decoded before, not the composite file.
            before, _ = views(root, sides, edge=96)
            before = downscale_long_edge(Image(rgb=before), 96).rgb
            origin = (before.shape, hashlib.sha256(np.uint8(np.clip(before * 255 + .5, 0, 255)).tobytes()).digest())
            if origin not in used_before:
                chosen[k] = sides
                used_before.add(origin)
    if len(chosen) < pair_goals.MIN_PAIRS:
        raise ValueError(f"{wanted} needs {pair_goals.MIN_PAIRS} distinct screened pairs, got {len(chosen)}")
    return chosen


def isolated():
    if not sys.flags.isolated or ROOT / "curated-web" in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        raise ValueError("launch with python -I from outside curated-web")


def main():
    isolated()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = cli.add_subparsers(required=True, dest="command")
    fetch = subs.add_parser("fetch", help="collect eligible files under the approved shared byte and pair budgets")
    fetch.add_argument("--approval", required=True, type=Path)
    fetch.add_argument("--plan", required=True, type=Path)
    screening = subs.add_parser("screen", help="send only verified public previews using a free-tier Gemini key")
    screening.add_argument("--allow-gemini", action="store_true", required=True)
    screening.add_argument("--limit", type=int, help="maximum new pairs in this invocation")
    local = subs.add_parser("screen-local", help="judge verified public pairs with the installed local vision model")
    local.add_argument("--limit", type=int, help="maximum new pairs in this invocation")
    review = subs.add_parser("audit", help="create numbered local preview sheets, then verify reviewer labels")
    review.add_argument("--scene", choices=pair_goals.SCENES, required=True)
    review.add_argument("--decisions", type=Path)
    args = cli.parse_args()
    if args.command == "fetch":
        collect(FOLDER, read(args.plan), read(args.approval))
        print(json.dumps(read(FOLDER / "fetch-report.json"), indent=2))
    elif args.command == "screen":
        print(screen(FOLDER, lambda images: gemini_screen(images, os.environ.get("GEMINI_FREE_API_KEY")), limit=args.limit))
    elif args.command == "screen-local":
        approved_screen(FOLDER, "local")
        weights = local_weights()
        print(screen(FOLDER, lambda images: local_screen(images, weights), provider="local", weights=weights, limit=args.limit))
    else:
        print(audit(FOLDER, args.scene, args.decisions) or f"Open {audit_folder(FOLDER, args.scene) / 'review.html'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, gemini.Unavailable) as exc:
        raise SystemExit(str(exc)) from None
