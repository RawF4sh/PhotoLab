"""CLI config loading for run-time style/palette knobs."""

from types import SimpleNamespace

from autoedit.cli import load_run_config


def _args(**overrides):
    base = dict(
        configs_dir=None,
        config=None,
        style=None,
        prompt=None,
        preset=None,
        concepts=None,
        look=None,
        palette=None,
        palette_strength=None,
        reference=None,
        reference_strength=None,
        intensity=None,
        decider=None,
        allow_cloud=None,
        api_key=None,
        llm_mode=None,
        backend=None,
        device=None,
        format=None,
        quality=None,
        analysis_long_edge=None,
        emit=None,
        compose=None,
        vignette=None,
        sharpen=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cli_palette_flags_flow_to_run_config():
    cfg = load_run_config(_args(palette="cool_blue", palette_strength=0.65))
    assert cfg.palette == "cool_blue"
    assert cfg.palette_strength == 0.65
    assert cfg.palettes_path.endswith("palettes.toml")
    assert cfg.intents_path.endswith("intents.toml")
