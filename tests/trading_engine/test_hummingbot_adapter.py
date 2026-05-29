"""HummingbotAdapter tests — uses mocks, no real Hummingbot dependency."""
from decimal import Decimal
from src.trading_engine.adapter import Order, InstrumentInfo
from src.trading_engine.adapter.hummingbot_integration import (
    build_grid_config,
    init_trading_engine,
    tick_trading_engine,
    route_fill,
)


class MockBalance:
    """Mock Hummingbot balance object."""
    def __init__(self, available: float):
        self.available = available


class MockConnector:
    """Mock Hummingbot connector."""
    def __init__(self, balances=None, prices=None):
        self._balances = balances or {"USDT": 10000.0}
        self._prices = prices or {"BTC-USDT": 50000.0}
        self.ready = True

    def get_mid_price(self, trading_pair):
        return self._prices.get(trading_pair, 0.0)

    def get_balance(self, currency):
        return MockBalance(self._balances.get(currency, 0.0))


class MockStrategy:
    """Mock Hummingbot StrategyV2Base."""
    def __init__(self, connector_name="binance_paper_trade"):
        self.exchange = connector_name
        self._orders = {}
        self._next_id = 1
        self.active_orders = {}  # Hummingbot's live orders tracker

    def buy(self, connector_name, trading_pair, amount, order_type, price):
        oid = f"hb-buy-{self._next_id}"
        self._next_id += 1
        self._orders[oid] = {
            "connector": connector_name,
            "pair": trading_pair,
            "side": "BUY",
            "price": float(price),
            "quantity": float(amount),
            "order_type": order_type,
        }
        return oid

    def sell(self, connector_name, trading_pair, amount, order_type, price):
        oid = f"hb-sell-{self._next_id}"
        self._next_id += 1
        self._orders[oid] = {
            "connector": connector_name,
            "pair": trading_pair,
            "side": "SELL",
            "price": float(price),
            "quantity": float(amount),
            "order_type": order_type,
        }
        return oid

    def cancel(self, connector_name, trading_pair, order_id):
        self._orders.pop(order_id, None)


def test_submit_buy_order():
    """Submit a buy order — verify buy() called and order tracked."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector()
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)

    assert oid == "hb-buy-1"
    assert oid in adapter._submitted_orders
    assert adapter._submitted_orders[oid].instrument_id == "BTC-USDT"
    assert adapter._submitted_orders[oid].side == "BUY"
    assert len(strategy._orders) == 1


def test_submit_sell_order():
    """Submit a sell order — verify sell() called."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector()
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    order = Order("ETH-USDT", "SELL", "LIMIT", 3000.0, 0.01)
    oid = adapter.submit_order(order)

    assert oid == "hb-sell-1"
    assert oid in adapter._submitted_orders
    assert adapter._submitted_orders[oid].side == "SELL"
    assert len(strategy._orders) == 1


def test_cancel_order():
    """Cancel an order by ID — removed from strategy."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector()
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)

    adapter.cancel_order(oid)

    assert oid not in adapter._submitted_orders
    assert oid not in strategy._orders


def test_cancel_all_orders():
    """Cancel all orders for an instrument — keep other instruments."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector()
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    # Submit orders for two instruments
    adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.submit_order(Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001))
    adapter.submit_order(Order("ETH-USDT", "BUY", "LIMIT", 3000.0, 0.01))

    adapter.cancel_all_orders("BTC-USDT")

    # BTC-USDT orders cancelled
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    # ETH-USDT order still present
    assert len(adapter.get_open_orders("ETH-USDT")) == 1


def test_get_balance():
    """Get available balance for a currency."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector(balances={"USDT": 5000.0, "BTC": 0.5})
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    assert adapter.get_balance("USDT") == 5000.0
    assert adapter.get_balance("BTC") == 0.5
    assert adapter.get_balance("ETH") == 0.0


def test_get_mid_price():
    """Get mid price for an instrument."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector(prices={"BTC-USDT": 50000.0, "ETH-USDT": 3000.0})
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    assert adapter.get_mid_price("BTC-USDT") == 50000.0
    assert adapter.get_mid_price("ETH-USDT") == 3000.0
    assert adapter.get_mid_price("SOL-USDT") == 0.0


def test_get_open_orders():
    """Get open orders filtered by instrument_id."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector()
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    # Submit orders for two instruments
    adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.submit_order(Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001))
    adapter.submit_order(Order("ETH-USDT", "BUY", "LIMIT", 3000.0, 0.01))

    btc_orders = adapter.get_open_orders("BTC-USDT")
    assert len(btc_orders) == 2
    assert all(o.instrument_id == "BTC-USDT" for o in btc_orders)

    eth_orders = adapter.get_open_orders("ETH-USDT")
    assert len(eth_orders) == 1
    assert eth_orders[0].instrument_id == "ETH-USDT"


def test_get_instrument():
    """Get instrument metadata."""
    from src.trading_engine.adapter import HummingbotAdapter

    connector = MockConnector()
    strategy = MockStrategy()
    adapter = HummingbotAdapter(connector, strategy)

    info = adapter.get_instrument("BTC-USDT")
    assert isinstance(info, InstrumentInfo)
    assert info.symbol == "BTC-USDT"
    assert info.price_precision == 4
    assert info.quantity_precision == 4


def test_build_grid_config():
    config = build_grid_config("BTC-USDT", {"grid_levels": 3, "capital": 2000})
    assert config["levels"] == 3
    assert config["capital"] == 2000
    assert config["ema_period"] == 200  # default


def test_init_trading_engine():
    connector = MockConnector()
    strategy = MockStrategy()
    host = init_trading_engine(connector, strategy, ["BTC-USDT"], {"grid_levels": 3})
    assert len(host.strategies) == 1
    assert host.strategies[0].instrument_id == "BTC-USDT"


def test_tick_trading_engine():
    connector = MockConnector()
    strategy = MockStrategy()
    host = init_trading_engine(connector, strategy, ["BTC-USDT"], {
        "grid_levels": 3, "ema_period": 5, "rsi_period": 5,
        "atr_period": 5, "bollinger_period": 5,
    })
    # Feed bars — need enough to warm up indicators
    prices = [50000, 50100, 49900, 50200, 49800, 50100, 49900, 50150, 49850, 50050]
    for i, price in enumerate(prices):
        tick_trading_engine(host, "BTC-USDT", {
            "open": price, "high": price * 1.001, "low": price * 0.999,
            "close": price, "volume": 1000.0, "timestamp": i * 3600,
        })
    # Should have processed all bars without error
    assert host.strategies[0].state.value != "inactive"


def test_route_fill():
    connector = MockConnector()
    strategy = MockStrategy()
    host = init_trading_engine(connector, strategy, ["BTC-USDT"], {"grid_levels": 3})

    class MockEvent:
        order_id = "hb-buy-1"
        trading_pair = "BTC-USDT"
        trade_type = type('TT', (), {'__str__': lambda s: 'BUY'})()
        price = 50000.0
        amount = 0.001

    route_fill(host, MockEvent())
    # Fill was routed — no crash = success
