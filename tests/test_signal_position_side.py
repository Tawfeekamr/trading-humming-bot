# tests/test_signal_position_side.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from src.signals.signal_position import SignalPositionManager

def _mgr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return SignalPositionManager({"max_positions": 2, "tp1_close_pct": 33, "tp2_close_pct": 50})

def test_short_close_pnl_inverted(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    m.open_position("ETHUSDT", 3000.0, 2.0, 3150.0, [2900], "high", "x", "c", side="short")
    assert abs(m.close_position("ETHUSDT", 2850.0, "tp") - 300.0) < 1e-6  # (3000-2850)*2

def test_long_close_pnl_unchanged(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    m.open_position("BTCUSDT", 100.0, 1.0, 90.0, [110], "high", "x", "c")
    assert m.close_position("BTCUSDT", 110.0, "tp") == 10.0

def test_short_partial_close(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    m.open_position("ETHUSDT", 3000.0, 2.0, 3150.0, [2900], "high", "x", "c", side="short")
    amt, pnl = m.partial_close("ETHUSDT", 0.5, 2940.0, "tp1")  # (3000-2940)*1.0 = 60
    assert abs(pnl - 60.0) < 1e-6 and abs(amt - 1.0) < 1e-6
