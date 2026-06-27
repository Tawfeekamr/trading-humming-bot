# tests/signals/test_futures_price_guard.py
import sys
import pathlib
import json

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

import urllib.request
from src.signals.signal_engine import SignalEngine


def _engine(monkeypatch, futures_mode):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    return SignalEngine(
        config={"enabled": True, "audit_mode": False, "allow_shorts": True},
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=lambda m: None,
        get_price_fn=lambda s: 0.0,           # primary (perp) feed "fails"
        futures_mode=futures_mode,
        futures_connector=object() if futures_mode else None,
        leverage=3,
    )


def test_futures_mode_does_not_fall_through_to_spot(monkeypatch):
    """A failed perp price must NOT be replaced by the Gate SPOT ticker in futures
    mode — that would mis-price a leveraged sim. It must return 0.0."""
    eng = _engine(monkeypatch, futures_mode=True)
    spot_called = []

    def boom(*args, **kwargs):
        spot_called.append(1)
        raise AssertionError("spot fallback must not fire in futures mode")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert eng._get_current_price(None, "ICP-USDT") == 0
    assert spot_called == []


def test_spot_mode_still_uses_spot_fallback(monkeypatch):
    """Spot mode must keep falling back to Gate spot when the price fn returns 0
    (regression guard for the existing spot behavior)."""
    eng = _engine(monkeypatch, futures_mode=False)

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps([{"last": "2.50"}]).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
    assert eng._get_current_price(None, "ICP-USDT") == 2.50
