"""Bounded recipe predicates over measurements of the source photo."""
import math
import os

from .. import paths
from ..contract import GlobalStatsModel

SCENES = ("people", "landscape", "other")


def measures(configs_dir=None, profile=None):
    path = os.path.join(configs_dir or paths.configs_dir(), "recipes.toml")
    if not os.path.isfile(path):
        path = paths.config_path("recipes.toml", packaged_fallback=True)
    table = paths.read_config(path, profile).get("measures")
    if not isinstance(table, dict) or not table or len(table) > 32:
        raise ValueError("recipes.toml needs a table of measures")
    for key, limits in table.items():
        if (key not in GlobalStatsModel.model_fields or not isinstance(limits, dict)
                or set(limits) != {"label", "min", "max"} or not isinstance(limits["label"], str)
                or not limits["label"].strip() or not all(type(limits[k]) in (int, float)
                and math.isfinite(limits[k]) for k in ("min", "max")) or limits["max"] <= limits["min"]):
            raise ValueError(f"Invalid recipe measure: {key}")
    return table


def check(value, table):
    if not isinstance(value, dict) or set(value) - {"scene", "tests"}:
        raise ValueError("A condition takes a scene and up to three tests")
    result = {}
    if "scene" in value:
        if value["scene"] not in SCENES:
            raise ValueError("Unknown recipe scene")
        result["scene"] = value["scene"]
    tests = value.get("tests", [])
    if not isinstance(tests, list) or len(tests) > 3:
        raise ValueError("A condition takes up to three tests")
    checked = []
    for test in tests:
        if not isinstance(test, dict) or set(test) != {"measure", "op", "value"}:
            raise ValueError("Each condition test needs a measure, comparison and number")
        key, op, n = test["measure"], test["op"], test["value"]
        if not isinstance(key, str) or key not in table or op not in ("above", "below"):
            raise ValueError("Choose a listed measure and an above/below comparison")
        if type(n) not in (int, float) or not table[key]["min"] <= n <= table[key]["max"]:
            raise ValueError(f"Threshold outside the range of {key}")
        checked.append(dict(test))
    if checked:
        result["tests"] = checked
    return result


def matches(condition, scene, values):
    if "scene" in condition and scene != condition["scene"]:
        return False
    for test in condition.get("tests", []):
        observed = values.get(test["measure"])
        if observed is None:
            return False
        threshold = test["value"]
        if not (observed > threshold if test["op"] == "above" else observed < threshold):
            return False
    return True
