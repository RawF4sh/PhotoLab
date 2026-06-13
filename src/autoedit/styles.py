"""Style profiles — the user-facing "pick a look" layer (configs/styles.toml).

A style = a looks.toml profile + a default concept set + a baked natural-language brief.
The brief is combined with the user's `--prompt` and handed to the LLM decider; it replaces
the "needs 3,000 of your edits" training model with "pick a style + say what you want."
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field


@dataclass
class Style:
    name: str
    look: str = "natural"
    concepts: list[str] = field(default_factory=list)
    brief: str = ""


def load_styles(path: str) -> dict[str, Style]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out: dict[str, Style] = {}
    for name, body in data.items():
        out[name] = Style(
            name=name,
            look=str(body.get("look", "natural")),
            concepts=list(body.get("concepts", [])),
            brief=str(body.get("brief", "")),
        )
    return out


def style_for(name: str | None, styles: dict[str, Style]) -> Style:
    if name and name in styles:
        return styles[name]
    return Style(name=name or "natural")


def combine_brief(style_brief: str, prompt: str | None) -> str | None:
    """Merge the style's baked brief with the user's free-text prompt."""
    parts = [p.strip() for p in (style_brief, prompt) if p and p.strip()]
    return ". ".join(parts) if parts else None
