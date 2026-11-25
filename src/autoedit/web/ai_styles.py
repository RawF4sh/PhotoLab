"""Photographic briefs for both Gemini editing workflows."""

import tomllib

from ..decide.gemini import MAX_PROMPT
from ..paths import config_path


def _styles():
    with open(config_path("ai_edits.toml", packaged_fallback=True), "rb") as source:
        return tomllib.load(source)


def catalog():
    return [{"id": name, "label": style["label"]} for name, style in _styles().items()]


def prompt_for(style, instruction=""):
    styles = _styles()
    if isinstance(style, str) and style not in styles:
        former_name = style.replace("_", " ").casefold()
        style = next((name for name, brief in styles.items()
                      if brief.get("formerly", "").casefold() == former_name and former_name), None)
    if not isinstance(style, str) or style not in styles:
        raise ValueError("Choose a supported AI edit style.")
    if not isinstance(instruction, str):
        raise ValueError("The optional editing instruction must be text.")
    prompt = styles[style]["prompt"]
    if instruction.strip():
        prompt += "\nPhotographer's direction: " + instruction.strip()
    if len(prompt) > MAX_PROMPT:
        raise ValueError("Shorten your editing instruction so it fits alongside the selected style.")
    return prompt
