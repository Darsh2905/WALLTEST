"""Demo defaults. They are DERIVED, not tuned: scripts/choose_defaults.py reproduces the numbers (exact binomial power plus a
Monte-Carlo of the Holm rule over the full family) and writes docs/defaults_derivation.json.

LIVE demo: full 2^3 factorial (8 cells) x 3 LOW agents = a family of K = 24 tests.
  planted accuracy  0.5 + trust/2 = 0.95 (trust 0.9)
  criterion         smallest planned n whose probability of flagging EVERY truly-leaky cell (Holm, family-wise alpha 0.05) is >= 0.80
                    and stays >= 0.80 for every larger n  ->  19 slots per cell, 152 slots
FAST doc-scale preset: single ALL_ON cell, 1,500 slots, alpha = 0.001, planted accuracy 0.55 (trust 0.1): the proposal's own
scale claim, reported with its exact power (it is NOT guaranteed to flag; the UI shows the minimum detectable accuracy)."""
from __future__ import annotations

LIVE_DEFAULT = {"alpha": 0.05, "design": "FULL_FACTORIAL", "planned_slots": 152, "slot_ms": 1000, "clock_mode": "LIVE",
                "trust": {"trader-leaky": 0.9, "trader-partial": 0.9}, "partial_channel": "vector_memory"}
FAST_DEFAULT = {"alpha": 0.05, "design": "FULL_FACTORIAL", "planned_slots": 152, "clock_mode": "SIMULATED",
                "trust": {"trader-leaky": 0.9, "trader-partial": 0.9}, "partial_channel": "vector_memory"}
DOC_SCALE = {"alpha": 0.001, "design": "ALL_ON", "planned_slots": 1500, "clock_mode": "SIMULATED",
             "trust": {"trader-leaky": 0.1, "trader-partial": 0.1}, "partial_channel": "vector_memory"}
NULL_CONTROL = {"alpha": 0.05, "design": "ALL_ON", "planned_slots": 40, "slot_ms": 1000, "clock_mode": "LIVE", "null_control": True, "trust": {}}
DOC_SAMPLE_SIZES = {0.7: 94, 0.6: 384, 0.55: 1543}   # proposal 6.5 / README table, alpha = 0.001, ~80% power
