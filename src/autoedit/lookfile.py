"""Look files (F28): one look, and optionally its palette and habit, as a small TOML file.

A look file can come from someone else, so this module is a trust boundary. It is read with `tomllib`
and nothing else (TOML has no include; no value in the file is a path or is run). The reader works on
bytes: size, parse, version, then every key and value. An unknown key refuses the whole file. Every
limit is a constant of this module, not a config file a profile could loosen.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

from . import looks as looks_mod
from . import paths
from .palette import Palette

VERSION, EXT = 1, ".aelook"
MAX_BYTES = 16 * 1024      # a file with every field, a palette and twelve habits is 2 kB
MAX_TEXT, MAX_PROTECTED = 200, 8         # author and licence, one line each; names in a palette's `protected`
NAME = re.compile(r"[a-z0-9_]{1,40}")
LOOK_WORDS = ("surprise",)               # the word of --look that picks a look (F26): no file's look takes it
PALETTE_WORDS = ("auto", "none", "off")  # words of --palette: no file's palette takes them
NOT_CARRIED = ("preserve_exposure",)     # a file cannot switch off a check of the review gate
_MUL = {"contrast_mul": 3.0, "clarity_mul": 3.0, "dehaze_mul": 3.0, "curve_mul": 3.0, "exposure_mul": 1.4}
_ADD_OP = {"vibrance_add": "vibrance", "saturation_add": "saturation", "curve_add": "curve", "dehaze_add": "dehaze"}
PALETTE_LIMITS = {"shadow_hue": (0.0, 360.0), "shadow_chroma": (0.0, 0.15), "mid_hue": (0.0, 360.0), "mid_chroma": (0.0, 0.15),
                  "highlight_hue": (0.0, 360.0), "highlight_chroma": (0.0, 0.15), "neutral_protection": (0.0, 1.0)}
_KEYS = ("aelook", "name", "author", "licence", "look", "palette", "habit")
_WORD = re.compile(r"[a-z][a-z0-9 _-]{0,39}")
_HEAD = ("# AutoEdit look file. Data only: numbers and short text; nothing in it is run.\n"
         "# Read it with: autoedit looks import FILE\n")


@dataclass
class LookFile:
    name: str
    look: looks_mod.Look
    palette: Palette | None = None
    habit: dict = field(default_factory=dict)
    author: str = ""
    licence: str = ""


def _show(v) -> str:
    return repr(str(v)[:40])


def limits() -> dict[str, tuple[float, float]]:
    """Field of a look -> (lowest, highest) a file may hold. Every field of a look but those of NOT_CARRIED."""
    from .edit.ops import OP_BOUNDS    # inside: the same reason as in looks.check_habit

    return {f: (0.0, _MUL[f]) if f in _MUL else OP_BOUNDS[_ADD_OP.get(f, f)]
            for f in looks_mod.FIELDS if f not in NOT_CARRIED}


def check_name(name) -> str:
    """`name` when it is 1 to 40 lowercase letters, digits and underscores (it may become a file name)."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(f"look name {_show(name)}: use 1 to 40 lowercase letters, digits and underscores")
    return name


def parse(data: bytes, where: str = "look file") -> LookFile:
    """The look in the bytes of a look file, or a ValueError that says which rule broke."""
    def bad(why: str) -> ValueError:
        return ValueError(f"{where}: {why}")
    def table(value, what: str, known) -> dict:
        if not isinstance(value, dict):
            raise bad(f"{what} must be a table")
        for key in value:
            if key not in known:
                raise bad(f"unknown key {_show(key)} in {what}; valid keys: {', '.join(known)}")
        return value
    def number(value, lo: float, hi: float, what: str) -> float:
        # nan fails the comparison, as inf and a whole number of any length do: no float() before it
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise bad(f"{what} must be a number from {lo:g} to {hi:g}")
        return float(value)
    def text(key: str) -> str:
        value = doc.get(key, "")
        if key in doc and not (isinstance(value, str) and 0 < len(value) <= MAX_TEXT and value.isprintable()):
            raise bad(f"{key} must be one line of text, at most {MAX_TEXT} characters")
        return value
    if len(data) > MAX_BYTES:
        raise bad(f"larger than {MAX_BYTES // 1024} kB, not read")
    try:
        doc = tomllib.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise bad("not a text file") from None
    except tomllib.TOMLDecodeError as e:
        raise bad(f"not valid TOML ({e})") from None
    except (ValueError, RecursionError):   # a number of thousands of digits; brackets a thousand deep
        raise bad("not valid TOML") from None
    if type(doc.get("aelook")) is not int:
        raise bad("no `aelook = 1` line: this is not an AutoEdit look file")
    if doc["aelook"] != VERSION:
        raise bad(f"this AutoEdit reads look file format {VERSION}, not the format of this file")
    table(doc, "the file", _KEYS)
    name = doc.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise bad("name must be 1 to 40 lowercase letters, digits or underscores")
    lim = limits()
    body = doc.get("look")
    for key in NOT_CARRIED:
        if isinstance(body, dict) and key in body:
            raise bad(f"{key} cannot be in a look file: a file does not switch off a check")
    body = table(body, "[look]", tuple(lim))
    look = looks_mod.Look(**{k: number(v, *lim[k], f"{k} in [look]") for k, v in body.items()})
    palette = None
    if "palette" in doc:
        body = dict(table(doc["palette"], "[palette]", (*PALETTE_LIMITS, "protected")))
        words = body.pop("protected", [])
        if not (isinstance(words, list) and len(words) <= MAX_PROTECTED
                and all(isinstance(w, str) and _WORD.fullmatch(w) for w in words)):
            raise bad(f"protected in [palette] must be a list of at most {MAX_PROTECTED} concept names")
        palette = Palette(name=name, protected=tuple(words),
                          **{k: number(v, *PALETTE_LIMITS[k], f"{k} in [palette]") for k, v in body.items()})
    habit = looks_mod.check_habit(doc["habit"], where) if "habit" in doc else {}
    return LookFile(name, look, palette, habit, text("author"), text("licence"))


def _file_name(path: str) -> str:
    """The file's name without its folder; a ValueError when it does not end in .aelook (any case)."""
    name = os.path.basename(path)
    if not name.lower().endswith(EXT):
        raise ValueError(f"{name}: a look file must end in {EXT}")
    return name


def read(path: str) -> LookFile:
    """The look in the file at `path`. At most MAX_BYTES + 1 bytes are read, whatever the file's size."""
    name = _file_name(path)
    if not os.path.isfile(path):
        raise ValueError(f"{name}: look file not found")
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise ValueError(f"{name}: cannot be read ({e.strerror})") from None
    return parse(data, name)


def _palette_table(pal: Palette) -> dict:
    return {**{k: getattr(pal, k) for k in PALETTE_LIMITS}, "protected": list(pal.protected)}


def dumps(name: str, look: looks_mod.Look, palette: Palette | None = None, habit: dict | None = None,
          author: str = "", licence: str = "") -> str:
    """The text of a look file. It is read back before it is returned, so a file that import would
    refuse is never written."""
    doc: dict = {"aelook": VERSION, "name": name}
    if author:
        doc["author"] = author
    if licence:
        doc["licence"] = licence
    doc["look"] = {f: getattr(look, f) for f in limits()}
    if palette is not None:
        doc["palette"] = _palette_table(palette)
    if habit:
        doc["habit"] = habit
    text = _HEAD + paths.dump_toml(doc)
    parse(text.encode("utf-8"), f"look {name}")
    return text


def write(path: str, text: str) -> None:
    """Write `text` to `path` (it must end in .aelook), over an existing file, through a `.part` file."""
    _file_name(path)
    part = path + ".part"
    try:
        with open(part, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(part, path)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise


def check_habit_limits(habit: dict, config: dict, where: str) -> None:
    """A habit repeats only what a typed instruction may do: every op has a limit in instructions.toml
    and every value is inside it. A ValueError starting with `where`."""
    from . import instructions

    vocab = instructions.load_vocab(config.get("instructions_path") or
                                    paths.config_path("instructions.toml", packaged_fallback=True))
    try:
        instructions.habit_adjustments(habit, "x", vocab)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None
    for scene, targets in habit.items():
        for target, ops in targets.items():
            for op, value in ops.items():
                lim = vocab.limits[op]
                if abs(value) > lim:
                    raise ValueError(f"{where}: habit {scene}/{target}: {op} must be from {-lim:g} to {lim:g}")


def export(name: str, configs_dir: str | None = None, profile: str | None = None, palette: str | None = None,
           author: str = "", licence: str = "") -> tuple[str, list[str]]:
    """(text of the look file for look `name`, notes about what the file leaves out)."""
    from .palette import load_palettes

    config = paths.config_paths(configs_dir, profile)
    library = looks_mod.load(config["looks_path"])
    if name not in library:
        raise ValueError(f"look {name!r} not found (known: {', '.join(library)})")
    check_name(name)
    pal = None
    if palette:
        known = load_palettes(config["palettes_path"])
        if palette not in known:
            raise ValueError(f"palette {palette!r} not found (known: {', '.join(known)})")
        pal = known[palette]
    habit = looks_mod.load_habits(config["looks_path"]).get(name) or {}
    if habit:
        check_habit_limits(habit, config, f"look {name}")
    notes = [f"not in the file: {k} (a look file does not switch off a check)"
             for k in NOT_CARRIED if getattr(library[name], k)]
    return dumps(name, library[name], pal, habit, author, licence), notes


def install(lf: LookFile, profile: str, name: str | None = None, force: bool = False,
            configs_dir: str | None = None) -> dict:
    """Put the look (and palette) of `lf` into the profile folder. Every check runs before the first write."""
    from dataclasses import asdict

    from .cli import write_look

    name = check_name(name or lf.name)
    if name in LOOK_WORDS:
        raise ValueError(f"{name!r} is a word of --look; import the file under another name with --name")
    if lf.palette and name in PALETTE_WORDS:
        raise ValueError(f"{name!r} is a word of --palette; import the file under another name with --name")
    base = configs_dir or paths.configs_dir()
    replaced = []
    for kind, file, have in (("look", "looks.toml", True), ("palette", "palettes.toml", lf.palette is not None)):
        if not have:
            continue
        if any(name in paths.read_config(os.path.join(d, file)) for d in {base, paths._packaged()}):
            raise ValueError(f"{name!r} is a packaged {kind}; import the file under another name with --name")
        if name in paths.read_config(os.path.join(profile, file)):
            if not force:
                raise ValueError(f"the profile already has a {kind} {name!r}: --force replaces it, --name NEW keeps both")
            replaced.append(kind)
    if lf.habit:
        check_habit_limits(lf.habit, paths.config_paths(configs_dir, profile), f"look {name}")
    palette_path = None
    if lf.palette:
        os.makedirs(profile, exist_ok=True)
        palette_path = os.path.join(profile, "palettes.toml")
        data = paths.read_config(palette_path)
        data[name] = _palette_table(lf.palette)
        with open(palette_path, "w", encoding="utf-8") as fh:
            fh.write(paths.dump_toml(data))
    look_path, rewritten = write_look(profile, name, asdict(lf.look), habit=lf.habit or None)
    return {"name": name, "look_path": look_path, "palette_path": palette_path,
            "replaced": replaced, "rewritten": rewritten}
