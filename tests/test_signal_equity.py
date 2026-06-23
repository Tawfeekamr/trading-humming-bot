"""Tests that SignalEngine._get_equity uses a real injected equity source
(get_equity_fn, backed by the Rust /api/v1/capital endpoint in production)
instead of falling back to the hardcoded max_capital_usdt when no connector
is present.

Background: run_signal_listener.py ticks the engine with connector=None, so
the connector-balance branch in _get_equity was always skipped and sizing
fell back to max_capital_usdt ($10k) — every signal logged
"equity fallback: using $10000" and position sizing was decoupled from the
true portfolio equity. The fix injects get_equity_fn, mirroring get_price_fn.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.signals.signal_engine import SignalEngine  # noqa: E402


@pytest.fixture
def make_engine(monkeypatch):
    """Build a SignalEngine without network/Telethon/DeepSeek side effects."""
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)

    def _make(**overrides):
        kwargs = dict(
            config={"enabled": True, "audit_mode": False},
            btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        )
        kwargs.update(overrides)
        return SignalEngine(**kwargs)

    return _make


def test_get_equity_uses_injected_fn_when_no_connector(make_engine):
    """With get_equity_fn set, sizing uses the real equity, not the fallback."""
    eng = make_engine(get_equity_fn=lambda: 7342.5)
    # connector is None in production (run_signal_listener ticks with no connector)
    assert eng._get_equity(None) == 7342.5


def test_get_equity_falls_back_when_fn_returns_nonpositive(make_engine):
    """A non-positive equity from the fn (API down / zero balance) falls back
    to max_capital_usdt so sizing stays sane rather than going to zero."""
    eng = make_engine(
        get_equity_fn=lambda: 0.0,
        config={"enabled": True, "audit_mode": False, "max_capital_usdt": 2000},
    )
    assert eng._get_equity(None) == 2000.0


def test_get_equity_falls_back_when_no_fn(make_engine):
    """No get_equity_fn and no connector → config fallback (legacy behavior)."""
    eng = make_engine(
        config={"enabled": True, "audit_mode": False, "max_capital_usdt": 1500},
    )
    assert eng._get_equity(None) == 1500.0


def test_get_equity_helper_reads_total_equity(monkeypatch):
    """run_signal_listener._get_equity reads total_equity from the Rust
    /api/v1/capital CapitalSnapshot (pins the Python↔Rust field contract)."""
    import urllib.request
    from src.run_signal_listener import _get_equity

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

    # Exact CapitalSnapshot shape emitted by the Rust get_capital handler.
    payload = (
        b'{"total_equity": 10466.42, "usdt_balance": 4200.0, '
        b'"locked_in_positions": 6266.42, "reserve_limit_pct": 20.0, '
        b'"reserve": 2093.28, "free_capital": 2106.72, "deployed_capital": {}}'
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=5: _Resp(payload))
    assert _get_equity() == 10466.42


def test_get_equity_helper_returns_zero_on_failure(monkeypatch):
    """API unreachable → 0.0, so SignalEngine falls back to max_capital_usdt."""
    import urllib.request
    from src.run_signal_listener import _get_equity

    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert _get_equity() == 0.0

