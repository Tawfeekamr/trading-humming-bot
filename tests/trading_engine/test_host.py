"""StrategyHost tests."""
from src.trading_engine.host import StrategyHost
from src.trading_engine.strategy.base import Strategy
from src.trading_engine.adapter import MockAdapter


class CountingStrategy(Strategy):
    """Test strategy that counts bars and fills."""
    def __init__(self, instrument_id: str, config: dict = None):
        super().__init__(instrument_id, config or {})
        self.bar_count = 0
        self.fill_count = 0
        self.started = False
        self.stopped = False

    def on_start(self):
        self.started = True

    def on_bar(self, bar: dict):
        self.bar_count += 1

    def on_stop(self):
        self.stopped = True

    def on_order_filled(self, fill):
        self.fill_count += 1


def test_host_starts_all_strategies():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    s1 = CountingStrategy("BTC-USDT")
    s2 = CountingStrategy("ETH-USDT")
    host.add_strategy(s1)
    host.add_strategy(s2)
    host.start()
    assert s1.started
    assert s2.started


def test_host_routes_bars_by_instrument():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    btc = CountingStrategy("BTC-USDT")
    eth = CountingStrategy("ETH-USDT")
    host.add_strategy(btc)
    host.add_strategy(eth)
    host.start()

    host.on_bar({"instrument_id": "BTC-USDT", "close": 50000.0})
    host.on_bar({"instrument_id": "BTC-USDT", "close": 50100.0})
    host.on_bar({"instrument_id": "ETH-USDT", "close": 3000.0})

    assert btc.bar_count == 2
    assert eth.bar_count == 1


def test_host_stops_all_strategies():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    s = CountingStrategy("BTC-USDT")
    host.add_strategy(s)
    host.start()
    host.stop()
    assert s.stopped


def test_host_routes_fills():
    from src.trading_engine.adapter.base import OrderFill
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    s = CountingStrategy("BTC-USDT")
    host.add_strategy(s)
    host.start()

    fill = OrderFill("mock-1", "BTC-USDT", "BUY", 50000.0, 0.001, 0)
    host.on_order_filled(fill)
    assert s.fill_count == 1


def test_host_format_status():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    host.add_strategy(CountingStrategy("BTC-USDT"))
    host.start()
    status = host.format_status()
    assert "BTC-USDT" in status
