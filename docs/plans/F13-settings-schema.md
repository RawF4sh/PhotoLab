# F13. Settings schema

Status: spec (2026-11-11). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F13.
Built after every feature that adds a run setting, so that its snapshot is written once.

## Goal

One description of every user-facing setting: key, label, group, type, default, range or choices,
help text. A future interface renders its controls from it instead of hardcoding them (Studio
hardcodes four styles today). Available as `autoedit settings` and `GET /api/v1/settings/schema`.

## Design

Two sources, joined in `src/autoedit/settings_schema.py`:

1. **What the code knows**, read from `RunConfig`: the type (bool, int, float, string, a choice
   from a `Literal`, a list), the default, and the limits (`ge`, `le`, `max_length`).
2. **What only a person can write**, in a new data file `configs/settings.toml`: the label, the
   group, a one-line help text, and whether the setting is `advanced`. The same file lists the
   fields that are not user-facing (`hidden`): the config file paths, the API key and internal
   fields.

Choices that live in other config files are filled in when the schema is built: `style` from
`styles.toml`, `look` from `looks.toml`, `palette` from `palettes.toml` (plus `auto` and `off`),
`preset` from `concepts.toml`, `export` from `exports.toml`. With a profile (F11) these include
the user's own looks and styles.

**A field cannot be forgotten.** A test fails when a `RunConfig` field is neither described in
`settings.toml` nor listed under `hidden`, and when `settings.toml` describes a field that no
longer exists.

## Output shape

```json
{
  "version": 1,
  "groups": [{"key": "look", "label": "Look"}, ...],
  "settings": [
    {"key": "style", "label": "Style", "group": "look", "type": "choice", "default": "natural",
     "choices": ["natural", "editorial", ...], "help": "...", "advanced": false},
    {"key": "intensity", "label": "Strength", "group": "look", "type": "float", "default": 0.8,
     "min": 0.0, "max": 2.0, "help": "...", "advanced": false},
    ...
  ]
}
```

Types: `bool`, `int`, `float`, `string`, `choice`, `list`. `min` and `max` appear when the field
has them; `choices` for `choice`; `max_length` for a limited string. Settings come in the order of
the groups, and inside a group in the order of `settings.toml`.

## `configs/settings.toml`

```toml
# AutoEdit settings: what a person sees for each run setting. The type, default and limits come
# from the code (contract.py::RunConfig); this file adds the words. A test fails when a setting is
# missing here.

hidden = ["rules_path", "looks_path", "...every *_path field...", "api_key", "emit"]

[groups]          # order of the groups, and their titles
look = "Look"
direct = "Directed edits"
quality = "Image quality"
batch = "Batch"
output = "Output"
raw = "RAW development"
segmentation = "Regions"
llm = "AI assistant (optional, your own key)"
performance = "Performance"

[setting.style]
label = "Style"
group = "look"
help = "The overall look: which regions are edited and how strongly."
```

The Builder writes one `[setting.KEY]` block for every `RunConfig` field that is not hidden, with
a short label, the group and a help line in plain words (one sentence, no jargon; take the meaning
from the field's comment in `contract.py` and from `APP_GUIDE.md`). `advanced = true` for the
settings a first-time user should not see: the RAW fine-tuning values, the LLM prices and caps,
`threads`, `workers`, `analysis_long_edge`, `device`, `mask_cache`, `recipe_model`.

## Module

```python
# src/autoedit/settings_schema.py
def schema(configs_dir: str | None = None, profile: str | None = None) -> dict:
    """The settings schema as a plain dict (see docs/plans/F13-settings-schema.md)."""
```

- Type mapping from the field's annotation: `bool` -> `bool`; `int` -> `int`; `float` -> `float`;
  `str` -> `string`; `Literal[...]` -> `choice` with its values; `list[...]` -> `list`;
  `X | None` -> the type of `X` (the default is then `null`).
- Limits from the field's metadata (`annotated_types.Ge`, `Le`, `MaxLen`); read them by attribute
  name (`ge`, `le`, `max_length`) so the code does not depend on the classes.
- An unknown annotation is a `TypeError` naming the field: a new kind of field must be mapped on
  purpose.
- The schema never contains the API key's value or any path.

CLI: `autoedit settings` prints a table (group, key, type, default, range or choices);
`autoedit settings --json` prints the dict. API: `GET /api/v1/settings/schema` (F15's router;
the legacy alias `/api/settings/schema` exists too) returns the dict.

## Files

`src/autoedit/settings_schema.py` (new), `src/autoedit/configs/settings.toml` (new),
`src/autoedit/cli.py`, `src/autoedit/web/app.py`, `src/autoedit/paths.py` (`"settings"` is not a
run config file: it is read with `paths.config_path("settings.toml", packaged_fallback=True)`),
`tests/test_settings_schema.py` (new), `tests/snapshots/settings_schema.json` (new),
`tests/snapshots/api_v1.json` (the new route).

## Task

One Builder (Sonnet, medium). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_settings_schema.py tests/test_api_contract.py tests/test_cli.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

## Tests: `tests/test_settings_schema.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_every_field_is_described_or_hidden` | The set of `RunConfig` fields equals the described settings plus `hidden`, with no overlap; every described setting has a non-empty label, a group that exists, and a help text of at most 140 characters |
| 2 | `test_types_defaults_and_limits` | `intensity` is `float` with `min` 0.0, `max` 2.0 and default 0.8; `backend` is `choice` with the values of the `Literal`; `levels` is `bool` with default false; `edit` is `string` with `max_length` 400 and default null; `concepts` is `list` |
| 3 | `test_choices_from_config_files` | `style` lists the styles of `styles.toml`; `look` the looks; `palette` includes `auto` and `off`; with a configs folder copy that has one more look, the schema for that folder lists it |
| 4 | `test_no_secret_and_no_path` | The JSON text of the schema contains no key named `api_key` and no key ending in `_path`, and no absolute path |
| 5 | `test_snapshot` | The schema for the packaged configs equals `tests/snapshots/settings_schema.json`; with `AUTOEDIT_UPDATE_SNAPSHOTS=1` the test writes the file |
| 6 | `test_cli_and_api` | `autoedit settings --json` prints the same dict; `GET /api/v1/settings/schema` returns it |
| 7 | `test_unknown_annotation_is_an_error` | A model subclass with a field of an unmapped type makes the type mapper raise `TypeError` naming the field |

## Result

Built on 2026-11-11, in the unattended run, as the last feature so that its snapshot is written
once. Status: done.

- **Built** by one Builder (Sonnet, medium) from this spec: `settings_schema.schema()`,
  `configs/settings.toml` (49 settings described in nine groups; 11 fields hidden: the nine
  config paths, the API key and `emit`), `autoedit settings` with `--json`, and
  `GET /api/v1/settings/schema` (also under the old `/api` prefix).
- **Deviations accepted:** `settings.toml` is taken from the given configs folder first and from
  the packaged files otherwise; `schema()` itself raises when `settings.toml` and `RunConfig`
  disagree, so a missing field cannot drop out silently; a limit that excludes its end value
  (`gt`, `lt`) is reported as `min` or `max` without a mark.
- **The route snapshot** `tests/snapshots/api_v1.json` was regenerated on purpose for the new
  route; `tests/snapshots/settings_schema.json` pins the schema.
- **A field cannot be forgotten:** the test fails when a `RunConfig` field is neither described
  nor hidden, and the Builder confirmed that on a changed copy of the tree.
- **Both interpreters** run the tests.
- **Known limits of this first version** (from the final check): a limit that excludes its end
  value is reported as `min` or `max`; the rules for `out_subdir` are not in the schema; `look`
  and `palette` have a default of null that is not among their choices.
