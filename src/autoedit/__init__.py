"""AutoEdit — standalone open-vocabulary batch photo auto-editor.

Pipeline: decode → segment (SAM 3) → resolve masks → per-region stats → scene →
decide (rules baseline + optional LLM refine) → looks → apply pixel edits → emit.

The Lua/Rust scaffold of the original Lightroom-plugin product was set aside; the
*concepts* (rules-as-data, per-region stats, looks/intensity, "why" notes) live on here.
"""

__version__ = "0.2.0"
