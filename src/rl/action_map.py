# src/rl/action_map.py
"""Single source of truth for the PPO action -> (engine, size_multiplier) map.

Lives in its own pure-Python module (no numpy / gymnasium / sb3 / torch) so
that lightweight consumers — notably the live router's action decoder — can
import it without pulling in the heavy training stack. ``src.rl.env`` re-exports
it for backwards compatibility.

The policy is trained against this exact mapping; the live router must decode
with the same mapping or actions get silently misinterpreted at inference time.
"""
from __future__ import annotations

# Maps action int -> (engine_name, size_multiplier). GO_FLAT carries no size.
ACTION_TO_ENGINE_SIZE: list[tuple[str, float]] = [
    ("grid", 0.5),
    ("grid", 1.0),
    ("grid", 1.5),
    ("trend", 0.5),
    ("trend", 1.0),
    ("trend", 1.5),
    ("swing", 0.5),
    ("swing", 1.0),
    ("swing", 1.5),
    ("flat", 0.0),  # action 9 = GO_FLAT
]
