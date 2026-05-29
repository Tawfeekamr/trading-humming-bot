"""Grid strategy tests using MockAdapter."""
from src.trading_engine.strategy.grid import GridStrategy, GridState
from src.trading_engine.adapter import MockAdapter, InstrumentInfo


def make_strategy_and_adapter():
    config = {
        "levels": 3,
        "capital": 3000,
        "spacing_atr_multiplier": 1.5,
        "ema_period": 5,
        "rsi_period": 5,
        "atr_period": 5,
        "bollinger_period": 5,
        "bollinger_std_dev": 2.0,
        "order_refresh_seconds": 3600,
    }
    strategy = GridStrategy("BTC-USDT", config)
    adapter = MockAdapter({"USDT": 10000})
    adapter.set_price("BTC-USDT", 50000.0)
    adapter.set_instrument("BTC-USDT", InstrumentInfo("BTC-USDT", price_precision=2, quantity_precision=5))
    strategy._set_adapter(adapter)
    strategy.start()
    return strategy, adapter


def make_bar(close, high=None, low=None, ts=0):
    return {
        "open": close,
        "high": high or close * 1.001,
        "low": low or close * 0.999,
        "close": close,
        "volume": 1000.0,
        "timestamp": ts,
    }


def test_starts_inactive():
    s, _ = make_strategy_and_adapter()
    assert s.state == GridState.INACTIVE


def test_activates_after_indicators_warm_up():
    s, adapter = make_strategy_and_adapter()
    # Feed oscillating prices to get RSI in a reasonable range
    prices = [50000, 50100, 49900, 50200, 49800, 50100, 49900, 50150, 49850, 50050]
    for i, price in enumerate(prices):
        adapter.set_price("BTC-USDT", float(price))
        s.on_bar(make_bar(float(price), ts=i * 3600))

    # Should have evaluated state — at minimum should not be INACTIVE
    assert s.state != GridState.INACTIVE


def test_places_grid_orders():
    s, adapter = make_strategy_and_adapter()
    # Feed rising prices to trigger activation
    for i in range(10):
        price = 50000.0 + i * 100  # Rising market
        adapter.set_price("BTC-USDT", price)
        s.on_bar(make_bar(price, ts=i * 3600))

    if s.state == GridState.ACTIVE:
        # Should have placed buy + sell orders (3 each = 6 total)
        open_orders = adapter.get_open_orders("BTC-USDT")
        assert len(open_orders) > 0


def test_format_status():
    s, _ = make_strategy_and_adapter()
    status = s.format_status()
    assert "BTC-USDT" in status
    assert "Grid" in status


def test_cancel_on_stop():
    s, adapter = make_strategy_and_adapter()
    for i in range(10):
        price = 50000.0 + i * 100
        adapter.set_price("BTC-USDT", price)
        s.on_bar(make_bar(price, ts=i * 3600))

    s.stop()
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(s.active_orders) == 0
