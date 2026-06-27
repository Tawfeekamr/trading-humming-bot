# tests/test_futures_wiring.py
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import src.run_signal_listener as rsl
from src.signals.signal_engine import SignalEngine
from src.signals.paper_futures_connector import PaperFuturesConnector


def test_build_futures_engine_uses_paper_connector_without_keys(monkeypatch):
    """The futures engine builds from the config flag alone — no Binance keys.
    Before the fix this path required BINANCE_FUTURES_KEY/SECRET and constructed
    BinanceFuturesConnector."""
    monkeypatch.delenv("SIGNAL_MODE", raising=False)
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = rsl._build_futures_engine(
        signal_cfg={"enabled": True}, fc={"enabled": True, "leverage": 3}
    )
    assert eng is not None
    assert isinstance(eng._futures_connector, PaperFuturesConnector)
    assert eng._futures_mode is True
    assert eng._leverage == 3


def test_build_futures_engine_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("SIGNAL_MODE", raising=False)
    assert rsl._build_futures_engine(
        signal_cfg={"enabled": True}, fc={"enabled": False}
    ) is None
