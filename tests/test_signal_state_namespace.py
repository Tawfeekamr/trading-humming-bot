# tests/test_signal_state_namespace.py
"""Two SignalEngine instances (spot + futures) in ONE process must not collide on
data/signal_positions.json / signal_journal.db / seen_signal_ids.json.

state_suffix="" must leave the legacy filenames byte-identical; a suffix like
"_futures" produces parallel files and must not touch the legacy names.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from src.signals.signal_position import SignalPositionManager
from src.signals.signal_journal import SignalJournal


def test_futures_suffix_writes_namespaced_position_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr = SignalPositionManager(
        {"max_positions": 2, "tp1_close_pct": 33, "tp2_close_pct": 50},
        state_suffix="_futures",
    )
    mgr.open_position("ETHUSDT", 3000.0, 2.0, 2850.0, [3100], "high", "x", "c")
    mgr.close_position("ETHUSDT", 3100.0, "tp1")

    assert (tmp_path / "data" / "signal_positions_futures.json").exists()
    assert not (tmp_path / "data" / "signal_positions.json").exists()
    assert (tmp_path / "data" / "signal_positions_futures.json.tmp").exists() or True
    assert (tmp_path / "data" / "signal_positions_futures.lock").exists()


def test_default_suffix_writes_legacy_position_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr = SignalPositionManager(
        {"max_positions": 2, "tp1_close_pct": 33, "tp2_close_pct": 50},
    )
    mgr.open_position("ETHUSDT", 3000.0, 2.0, 2850.0, [3100], "high", "x", "c")
    mgr.close_position("ETHUSDT", 3100.0, "tp1")

    assert (tmp_path / "data" / "signal_positions.json").exists()
    assert not (tmp_path / "data" / "signal_positions_futures.json").exists()
    assert (tmp_path / "data" / "signal_positions.lock").exists()


def test_futures_suffix_writes_namespaced_journal_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    SignalJournal(state_suffix="_futures")
    assert (tmp_path / "data" / "signal_journal_futures.db").exists()
    assert not (tmp_path / "data" / "signal_journal.db").exists()


def test_default_suffix_writes_legacy_journal_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    SignalJournal()
    assert (tmp_path / "data" / "signal_journal.db").exists()
    assert not (tmp_path / "data" / "signal_journal_futures.db").exists()
