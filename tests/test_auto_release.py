"""F62 release checks use held-out shoots and count ties in the owner's denominator."""
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import auto_release as release


def test_scene_release_requires_complete_held_out_votes():
    assert release.vote_passes({"total": 10, "expected": 10, "wins": {"autoedit": 7}})
    assert not release.vote_passes({"total": 10, "expected": 10, "wins": {"autoedit": 6}})
    assert not release.vote_passes({"total": 10, "expected": 11, "wins": {"autoedit": 10}})
    assert not release.vote_passes({"total": 10, "expected": 10, "wins": {"autoedit": 11}})
    assert not release.vote_passes({"total": 9, "expected": 9, "wins": {"autoedit": 9}})


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_holdout_rejects_reused_captures_and_shoots():
    dev = {"source_sha256": digest("dev"), "group": "shoot", "split": "development"}
    rows = [dict(scene="mist", group="new", source_sha256=digest(str(i)), split="holdout") for i in range(10)]
    assert release.held_out([dev, *rows], "mist") == rows
    for change in ({"source_sha256": digest("1")}, {"source_sha256": digest("dev")}, {"group": "shoot"}):
        with pytest.raises(ValueError, match="duplicate capture or development shoot"):
            release.held_out([dev, {**rows[0], **change}, *rows[1:]], "mist")


def test_private_round_binds_checker_findings_to_candidate_and_opens_blind_gallery(tmp_path):
    rows = [{"capture_id": "previous", "source_sha256": digest("dev"), "group": "used", "split": "development"}]
    for i in range(10):
        grey = np.full((18, 24, 3), 65+i, dtype=np.uint8)
        source = tmp_path / f"photo{i}.png"
        Image.fromarray(grey).save(source)
        methods = {}
        for method, style in (("baseline","standard"), ("autoedit","auto")):
            output = tmp_path / f"{method}-{i}.png"
            Image.fromarray(grey if method == "baseline" else grey+32).save(output)
            audit = {"run_config": {"style": style, "intensity": 1 if style == "auto" else .8}, "output": output.name, "config_digest": "same"}
            if method == "autoedit":
                audit["auto"] = {"scene": "sunset", "target": "auto_sunset", "photos": 45,
                                 "fallback": "", "fault_check": {"failures": []}}
            sidecar = output.with_suffix(".json")
            sidecar.write_text(json.dumps(dict(scene="sunset", style=style, confidence=1, image=source.name, audit=audit)))
            methods[method] = {"sidecar": sidecar.name, "path": output.name}
        rows.append(dict(capture_id=f"img{i}", source=source.name, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                         scene="sunset", group=f"shoot{i}", split="holdout", methods=methods))
    manifest = tmp_path / "gallery-comparisons.json"
    manifest.write_text(json.dumps({"entries": rows}))
    report = release.measure(manifest, "sunset")
    assert report["visible"] and report["automatic_checks"]
    panel = {p["render_sha256"]: [] for p in report["photos"]}
    with pytest.raises(ValueError, match="checker"):
        release.gallery(manifest, "sunset", {}, tmp_path / "gallery")
    frozen = manifest.read_bytes()
    sc = tmp_path / rows[1]["methods"]["autoedit"]["sidecar"]
    sidecar = sc.read_text()
    bad = json.loads(sidecar)
    for field, value in (("photos", "45"), ("photos", -2), ("photos", 46), ("target", "auto_mist")):
        bad["audit"]["auto"][field] = value
        sc.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            release.measure(manifest, "sunset")
        bad = json.loads(sidecar)
    bad["audit"]["manual"] = {"engine":"LightCraft"}
    sc.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        release.measure(manifest, "sunset")
    sc.write_text(sidecar)
    release.gallery(manifest, "sunset", panel, tmp_path / "gallery")
    assert manifest.read_bytes() == frozen
    with (tmp_path / "gallery/manifest.csv").open() as f:
        cards = list(csv.DictReader(f))
    assert len(cards) == 20 and len({c["gallery_id"] for c in cards}) == 1
    results = tmp_path / "fixture-votes.csv"
    with results.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["gallery_id", "photo", "best_slot"])
        writer.writeheader()
        for card in (c for c in cards if c["method"] == "autoedit"):
            writer.writerow(dict(gallery_id=card["gallery_id"], photo=card["photo"], best_slot=card["slot"]))
    gallery_manifest = tmp_path / "gallery/manifest.csv"
    assert release.score_round(gallery_manifest, results)
    ballot = results.read_text()
    rows_cast = ballot.splitlines()
    assert len(rows_cast) == 11
    results.write_text("\n".join(rows_cast[:-1]) + "\n")
    assert not release.score_round(gallery_manifest, results)
    results.write_text(ballot + rows_cast[-1] + "\n")
    with pytest.raises(ValueError, match="duplicate vote"):
        release.score_round(gallery_manifest, results)
    results.write_text(ballot)
    card_file = gallery_manifest.parent / cards[0]["photo"] / f"slot{cards[0]['slot']}.jpg"
    saved_card = card_file.read_bytes()
    card_file.write_bytes(b"changed")
    with pytest.raises(ValueError, match="blind card"):
        release.score_round(gallery_manifest, results)
    card_file.write_bytes(saved_card)
    gallery_manifest.write_text(gallery_manifest.read_text() + "\n")
    with pytest.raises(ValueError, match="checked scene round"):
        release.score_round(gallery_manifest, results)


def test_release_arms_only_differ_in_style_preview_and_normal_strength():
    simple = {"style": "standard", "intensity": .8}
    auto = {"style": "auto", "intensity": 1, "auto_unreleased": True}
    assert release.matching_settings(simple, auto)
    assert release.matching_settings({**simple, "out_name": "simple-{stem}"}, auto)
    assert not release.matching_settings({**simple, "intensity": 0}, auto)
    assert not release.matching_settings(simple, {**auto, "palette": "cool_blue"})
    assert not release.matching_settings(simple, {**auto, "scene": "sunset"})


def test_high_bit_distance_uses_the_same_decoded_samples_as_the_gallery(tmp_path):
    from autoedit.imageio import load_image
    files = [tmp_path/"left.tif", tmp_path/"right.tif"]
    for f, level in zip(files, (0x3000, 0x7000)):
        Image.fromarray(np.full((20, 20), level, dtype=np.uint16)).save(f)
    views = release.image_pair(files)
    for view, name in zip(views, files):
        assert np.array_equal(view, load_image(str(name)).rgb)
    assert release.visible_distance(*views) > 5
