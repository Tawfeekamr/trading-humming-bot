"""Tests for live execution-path safety in SignalEngine.

Covers three real-money bugs:
1. Buy order must NOT be placed for a duplicate symbol or when max positions reached.
2. Entries and exits must use MARKET orders (LIMIT can fail to fill → phantom /
   stuck positions). Entries must record the live fill price as cost basis.
3. Partial TP closes (TP1/TP2) must sell only the partial slice, not the whole
   remaining position.
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.signals.signal_engine import SignalEngine
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.signal_position import SignalPosition, SignalPositionManager


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    """Keep tests off the real filesystem and network."""
    monkeypatch.setattr(SignalPositionManager, "_save_state", lambda self: None)
    monkeypatch.setattr(SignalPositionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    # SignalJournal would otherwise create/write ./data/signal_journal.db
    import src.signals.signal_engine as se_mod
    monkeypatch.setattr(se_mod, "SignalJournal", lambda *a, **k: MagicMock())


def _make_engine(price=60.0, audit=False, max_positions=3):
    cfg = {
        "enabled": True,
        "audit_mode": audit,
        "max_positions": max_positions,
        "max_capital_usdt": 1000,
        "capital_pct": 100.0,
        "per_trade_risk_pct": 3.0,
        "session_name": "test",
    }
    buy = MagicMock(return_value="order-buy")
    sell = MagicMock(return_value="order-sell")
    engine = SignalEngine(
        config=cfg,
        btc_regime_fn=lambda: ("RANGING", 0.5, 0.0),
        telegram_send_fn=lambda msg: None,
        buy_fn=buy,
        sell_fn=sell,
        get_price_fn=lambda symbol: price,
    )
    return engine


def _signal(pair="HYPE-USDT"):
    return ParsedSignal(
        action=SignalAction.OPEN_LONG,
        pair=pair,
        entry_low=60.0,
        entry_high=60.0,
        stop_loss=55.0,
        take_profits=[65.0, 68.0],
        confidence=SignalConfidence.HIGH,
        quality_score=8,
        raw_message="test",
    )


def _inject(engine, pair="HYPE-USDT", amount=5.0):
    """Add an open position straight into the manager (no disk IO)."""
    engine._position_mgr._positions[pair] = SignalPosition(
        symbol=pair, entry_price=60.0, amount=amount, stop_loss=55.0,
        take_profits=[65.0, 68.0], signal_confidence="high",
        raw_message="x", channel_name="c",
    )


# ── Bug 1: order must not be placed when it would be rejected ────────────────

class TestEntryGuards:
    def test_duplicate_symbol_does_not_place_order(self):
        engine = _make_engine()
        _inject(engine, "HYPE-USDT")  # already open

        engine._execute_entry(_signal("HYPE-USDT"), "chan", connector=None)

        assert engine._buy_fn.call_count == 0, "must not buy for an already-open symbol"
        # and no phantom tracking
        assert len(engine._position_mgr.get_open_positions()) == 1

    def test_max_positions_blocks_new_order(self):
        engine = _make_engine(max_positions=2)
        _inject(engine, "HYPE-USDT")
        _inject(engine, "ETH-USDT", amount=1.0)
        _inject(engine, "BNB-USDT", amount=1.0)  # 3rd injected but max=2

        engine._execute_entry(_signal("DOGE-USDT"), "chan", connector=None)

        assert engine._buy_fn.call_count == 0, "must not buy past max positions"


# ── Bug 2: MARKET orders + fill-price cost basis ─────────────────────────────

class TestOrderTypes:
    def test_entry_is_market_order_at_live_price(self):
        engine = _make_engine(price=61.5)  # market above signal entry zone

        engine._execute_entry(_signal(), "chan", connector=None)

        assert engine._buy_fn.call_count == 1
        kwargs = engine._buy_fn.call_args.kwargs
        assert kwargs.get("order_type") == "MARKET"
        # cost basis recorded at the live fill price, not the stale entry zone
        pos = engine._position_mgr.get_position("HYPE-USDT")
        assert pos is not None
        assert pos.entry_price == pytest.approx(61.5)

    def test_stop_loss_exit_is_market_full_remaining(self):
        engine = _make_engine(price=54.0)  # below SL=55
        _inject(engine, amount=10.0)

        engine._manage_positions(connector=None)

        assert engine._sell_fn.call_count == 1
        kwargs = engine._sell_fn.call_args.kwargs
        assert kwargs.get("order_type") == "MARKET"
        assert float(kwargs["amount"]) == pytest.approx(10.0, rel=1e-6)


# ── Bug 3: partial TP closes sell only the partial slice ─────────────────────

class TestPartialExits:
    def test_tp1_sells_partial_slice_not_whole_position(self):
        engine = _make_engine(price=66.0)  # >= TP1 (65), < TP2 (68)
        _inject(engine, amount=10.0)       # tp1_close_pct default 0.33

        engine._manage_positions(connector=None)

        assert engine._sell_fn.call_count == 1
        kwargs = engine._sell_fn.call_args.kwargs
        # only 33% of the position should hit the exchange, not all 10 units
        assert float(kwargs["amount"]) == pytest.approx(3.3, rel=1e-3)
        assert kwargs.get("order_type") == "MARKET"


class TestAdapterMarketPayload:
    """Real-money safety: MARKET orders must not carry price/time_in_force
    (Binance/Gate reject a MARKET order with price set)."""

    def test_market_order_omits_price_and_tif(self):
        from src.trading_engine.adapter.base import Order
        from src.trading_engine.adapter.rust_engine import RustEngineAdapter

        adapter = RustEngineAdapter()
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"orderId": "ord-1"}

        def fake_post(url, json=None, timeout=None):
            captured["payload"] = json
            return FakeResp()

        adapter._session.post = fake_post  # bypass real HTTP

        adapter.submit_order(Order("BTC-USDT", "BUY", "MARKET", 50000.0, 0.001))

        payload = captured["payload"]
        assert payload["order_type"] == "Market"
        assert payload["price"] is None, "MARKET must not send a price"
        assert payload["time_in_force"] is None, "MARKET must not send time_in_force"

    def test_limit_order_keeps_price_and_tif(self):
        from src.trading_engine.adapter.base import Order
        from src.trading_engine.adapter.rust_engine import RustEngineAdapter

        adapter = RustEngineAdapter()
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"orderId": "ord-2"}

        adapter._session.post = lambda url, json=None, timeout=None: (
            captured.__setitem__("payload", json), FakeResp())[1]

        adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))

        payload = captured["payload"]
        assert payload["order_type"] == "Limit"
        assert payload["price"] == 50000.0
        assert payload["time_in_force"] == "Gtc"
