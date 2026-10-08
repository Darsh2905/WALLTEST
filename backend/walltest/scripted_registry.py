"""Maps registered agents (agent.model_name) to scripted behaviours and seeds their private PRNGs."""
from __future__ import annotations

import random

from .agents.scripted import TraderSpec

BEHAVIOUR = {"scripted-leaky-trader": "leaky", "scripted-partial-trader": "partial", "scripted-clean-trader": "clean"}
ALL_CHANNELS = ("vector_memory", "notes_table", "feature_cache")


def build_traders(low_agents: list[dict], cfg, seed: int) -> list[TraderSpec]:
    out = []
    for a in low_agents:
        b = BEHAVIOUR.get(a["model_name"])
        if b is None:
            raise ValueError(f"no scripted behaviour for model {a['model_name']!r}")
        trust = float(cfg.trust.get(a["agent_name"], 0.0)) if b != "clean" else 0.0
        channels = {"leaky": ALL_CHANNELS, "partial": (cfg.partial_channel,), "clean": ()}[b]
        out.append(TraderSpec(a["agent_name"], a["agent_id"], b, trust, channels, random.Random(f"{a['agent_name']}:{seed}")))
    return out
