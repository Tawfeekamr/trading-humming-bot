# src/rl/live_router.py
"""Per-bar PPO routing service: compute the action each 1h bar and push it to
the Rust engine's RoutingCache via POST /api/v1/routing. Paper-gated only.

Task 7 scope is just the pure action decoder below; Task 8 will add the
observation builder + POST loop on top of it.
"""
from __future__ import annotations

from src.rl.action_map import ACTION_TO_ENGINE_SIZE


def decode_action(action: int) -> dict:
    """Map a PPO action int (0-9) to the routing payload.

    Mirrors ``src.rl.env.ACTION_TO_ENGINE_SIZE`` exactly — the policy is
    trained against that mapping, so the decoder must agree or actions get
    silently misinterpreted. The mapping lives in ``src.rl.action_map`` (a
    pure module with no numpy / gymnasium / sb3 / torch deps) so importing
    this module — and running its unit tests — works with only numpy present.
    """
    engine, size_mult = ACTION_TO_ENGINE_SIZE[int(action)]
    return {
        "active_engine": engine,
        "size_mult": float(size_mult),
        "flat": engine == "flat",
    }
