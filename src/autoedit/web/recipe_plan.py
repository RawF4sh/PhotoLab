"""Plan a folder against ordered recipes using local, pre-edit measurements."""
import math
import os

from .. import batch
from . import edit_presets, projects, recipe_conditions, service

# The plan needs only settings that affect segmentation, source development or scene analysis.
ANALYSIS_OPTIONS = frozenset(("backend", "device", "genre", "style", "preset", "concepts", "look",
                              "raw_mode", "raw_recover", "fast_analysis", "night_sky",
                              "lens_ca", "lens_vignette", "snap_edges"))


def _inputs(payload):
    if set(payload) - {"folder", "out", "photos", "recipes", "options"}:
        raise ValueError("Unsupported folder plan field")
    folder = payload["folder"]
    service.ensure_allowed(folder)
    if not os.path.isdir(folder):
        raise ValueError("Choose an existing photo folder")
    options = payload.get("options", {})
    if not isinstance(options, dict) or set(options) - ANALYSIS_OPTIONS:
        raise ValueError("Unsupported analysis option for a recipe plan")
    photos = payload.get("photos", None)
    if "photos" not in payload:
        photos = batch.list_images(folder)
    if not isinstance(photos, list) or not 1 <= len(photos) <= projects.MAX_PHOTOS:
        raise ValueError(f"Select 1 to {projects.MAX_PHOTOS} photos to plan")
    root = os.path.realpath(folder)
    real_paths = set()
    for path in photos:
        if not isinstance(path, str) or not os.path.isabs(path):
            raise ValueError("Select photos from this folder")
        real = os.path.realpath(path)
        if os.path.commonpath((root, real)) != root:
            raise PermissionError("A photo is outside the selected folder")
        if real in real_paths:
            raise ValueError("A photo appears more than once in the selection")
        real_paths.add(real)
    cache_dir = payload.get("out") or os.path.join(folder, "AutoEdit Exports")
    service.ensure_allowed(cache_dir)
    return photos, options, cache_dir


def _analyze(pipe, path):
    # Region statistics use raw unions. Only regions with an arbitrated claim can take an edit.
    objects = []
    def observe(resolved):
        objects.extend(region.name for region in resolved.regions if region.coverage > 0)

    with service.INFERENCE_LOCK:
        _, result = pipe.process_one(path, do_apply=False, on_resolved=observe)
    return result, sorted(set(objects))


def run(payload, configs_dir, profile, log):
    photos, options, cache_dir = _inputs(payload)
    records = edit_presets.ordered(payload["recipes"], profile, configs_dir)
    measures = recipe_conditions.measures(configs_dir, profile)

    def pipeline(settings):
        cfg = service.make_cfg({**settings, "decider": "rules", "local_only": True,
                                "translator": "off", "history": False}, configs_dir, profile)
        pipe = service.build_pipeline(cfg)
        pipe.use_mask_cache(cache_dir)
        return pipe

    base = pipeline(options)
    by_recipe = {}
    counts = {record["id"]: 0 for record in records}
    items = []
    for path in photos:
        item = {"path": path, "recipe_id": None, "scene": None, "values": {}, "objects": [], "missing": []}
        try:
            service.check_image(path)
            result, objects = _analyze(base, path)
            stats = result.global_stats
            if stats is None:
                raise ValueError("No source measurements available")
            values = {}
            for key in measures:
                v = getattr(stats, key)
                # ISO is zero when EXIF is absent. A photo with unknown ISO matches neither comparison.
                values[key] = v if math.isfinite(v) and (key != "iso" or v > 0) else None
            record = next((r for r in records if recipe_conditions.matches(
                r["settings"].get("condition", {}), result.scene, values)), None)
            item.update(scene=result.scene, values=values, objects=objects)
            if record:
                key, settings = record["id"], record["settings"]
                targets = settings.get("region_settings", {})
                if targets:
                    # Only ask for the winning recipe's additional objects. Unrelated recipes must
                    # not steal pixels or influence the scene used for matching.
                    if key not in by_recipe:
                        effective = {**options, **{k: settings[k] for k in ("look", "intensity") if k in settings},
                                     "region_settings": targets}
                        by_recipe[key] = pipeline(effective)
                    _, objects = _analyze(by_recipe[key], path)
                item.update(recipe_id=key, objects=objects, missing=sorted(set(targets) - set(objects)))
                counts[key] += 1
        except Exception as exc:
            item["error"] = str(exc) or type(exc).__name__
        items.append(item)
    return {"items": items, "recipes": records, "counts": counts,
            "unmatched": sum(row["recipe_id"] is None and "error" not in row for row in items),
            "missing_objects": sum(bool(row["missing"]) for row in items),
            "failed": sum("error" in row for row in items)}
