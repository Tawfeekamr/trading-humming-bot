# tests/signals/test_futures_math.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.futures_math import estimate_liquidation, pnl, sl_triggers_before_liquidation

def test_liquidation_long_below_entry():
    liq = estimate_liquidation(100.0, 3, "long")
    assert 66.0 < liq < 68.0

def test_liquidation_short_above_entry():
    liq = estimate_liquidation(100.0, 3, "short")
    assert 132.0 < liq < 134.0

def test_pnl_long_and_short_inverted():
    assert pnl("long", 100.0, 110.0, 1.0) == 10.0
    assert pnl("short", 100.0, 110.0, 1.0) == -10.0
    assert pnl("short", 100.0, 90.0, 2.0) == 20.0

def test_sl_must_trigger_before_liquidation():
    assert sl_triggers_before_liquidation("long", 100.0, 80.0, 3) is True
    assert sl_triggers_before_liquidation("long", 100.0, 60.0, 3) is False
    assert sl_triggers_before_liquidation("short", 100.0, 120.0, 3) is True
