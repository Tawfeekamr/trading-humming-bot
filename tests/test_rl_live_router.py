# tests/test_rl_live_router.py
"""Unit tests for the pure action decoder in src/rl/live_router.py.

These tests intentionally run with numpy + pytest only (no gymnasium) — the
decoder's ACTION_TO_ENGINE_SIZE import must be lazy so importing
``src.rl.live_router`` does not pull gymnasium at module load time.
"""
from src.rl.live_router import decode_action


def test_decode_action_grid_1x():
    assert decode_action(1) == {"active_engine": "grid", "size_mult": 1.0, "flat": False}


def test_decode_action_flat():
    assert decode_action(9) == {"active_engine": "flat", "size_mult": 0.0, "flat": True}


def test_decode_action_swing_1_5x():
    assert decode_action(8) == {"active_engine": "swing", "size_mult": 1.5, "flat": False}
