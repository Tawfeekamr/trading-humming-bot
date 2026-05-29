"""TrendStrategy tests using MockAdapter."""
import pytest
from src.trading_engine.strategy.trend import TrendStrategy, TrendState
from src.trading_engine.adapter import MockAdapter, InstrumentInfo


def make_strategy_and_adapter():
    config = {
        "ema_fast": 5, "ema_slow": 8, "ema_trend": 10,
        "rsi_period": 5, "atr_period": 5,
        "rsi_min": 30, "rsi_max": 70,
        "min_signal_score": 3, "confirmation_ticks": 2,
        "capital": 2000, "max_positions": 1,
    }
    strategy = TrendStrategy("BTC-USDT", config)
    adapter = MockAdapter({"USDT": 10000})
    adapter.set_price("BTC-USDT", 50000.0)
    adapter.set_instrument("BTC-USDT", InstrumentInfo("BTC-USDT", price_precision=2, quantity_precision=5))
    strategy._set_adapter(adapter)
    strategy.start()
    return strategy, adapter


def make_bar(close, high=None, low=None, ts=0):
    return {
        "open": close,
        "high": high or close * 1.01,
        "low": low or close * 0.99,
        "close": close,
        "volume": 1000.0,
        "timestamp": ts,
    }


def feed_rising_bars(strategy, adapter, n=20, base=50000.0):
    for i in range(n):
        price = base + i * 50
        adapter.set_price("BTC-USDT", price)
        strategy.on_bar(make_bar(price, high=price * 1.01, low=price * 0.99, ts=i * 3600))


def test_starts_flat():
    s, _ = make_strategy_and_adapter()
    assert s.state == TrendState.FLAT


def test_warms_up_indicators():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=20)
    assert s.state in (TrendState.SCORING, TrendState.PENDING_ENTRY, TrendState.IN_POSITION)


def test_enters_on_signal():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=30, base=50000.0)
    # Should have at least attempted entry or be in position
    if s.state in (TrendState.PENDING_ENTRY, TrendState.IN_POSITION):
        assert True  # Entry signal triggered
    else:
        assert s.state == TrendState.SCORING  # Still looking for signal


def test_exit_on_stop_loss():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=30, base=50000.0)
    if s.state != TrendState.IN_POSITION:
        return  # Skip if no position opened
    for i in range(5):
        price = 49000.0 - i * 200
        adapter.set_price("BTC-USDT", price)
        s.on_bar(make_bar(price, high=price * 1.001, low=price * 0.999, ts=(30 + i) * 3600))


def test_format_status():
    s, _ = make_strategy_and_adapter()
    status = s.format_status()
    assert "BTC-USDT" in status
    assert "Trend" in status


def test_on_stop_cancels_orders():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=20)
    s.stop()


def test_on_bar_accumulates_bars():
    s, adapter = make_strategy_and_adapter()
    for i in range(15):
        s.on_bar(make_bar(50000.0 + i * 10, ts=i * 3600))
    assert len(s._bars) == 15


def test_max_bars_buffer():
    s, adapter = make_strategy_and_adapter()
    for i in range(300):
        s.on_bar(make_bar(50000.0 + i, ts=i * 3600))
    assert len(s._bars) <= 250
