"""MockAdapter tests."""
from src.trading_engine.adapter import MockAdapter, Order, InstrumentInfo


def test_submit_order_returns_id():
    adapter = MockAdapter()
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)
    assert oid.startswith("mock-")


def test_cancel_order():
    adapter = MockAdapter()
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)
    adapter.cancel_order(oid)
    assert len(adapter.get_open_orders("BTC-USDT")) == 0


def test_cancel_all_orders():
    adapter = MockAdapter()
    adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.submit_order(Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001))
    adapter.submit_order(Order("ETH-USDT", "BUY", "LIMIT", 3000.0, 0.01))
    adapter.cancel_all_orders("BTC-USDT")
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(adapter.get_open_orders("ETH-USDT")) == 1


def test_get_balance():
    adapter = MockAdapter({"USDT": 5000.0, "BTC": 0.5})
    assert adapter.get_balance("USDT") == 5000.0
    assert adapter.get_balance("ETH") == 0.0


def test_set_and_get_price():
    adapter = MockAdapter()
    adapter.set_price("BTC-USDT", 50000.0)
    assert adapter.get_mid_price("BTC-USDT") == 50000.0
    assert adapter.get_mid_price("ETH-USDT") == 0.0


def test_fill_order():
    adapter = MockAdapter()
    oid = adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.fill_order(oid, 50001.0)
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(adapter._filled) == 1
    assert adapter._filled[0]["price"] == 50001.0


def test_instrument_info():
    adapter = MockAdapter()
    info = InstrumentInfo("BTC-USDT", price_precision=2, quantity_precision=5)
    adapter.set_instrument("BTC-USDT", info)
    got = adapter.get_instrument("BTC-USDT")
    assert got.round_price(50000.126) == 50000.13
    assert got.round_quantity(0.123456789) == 0.12345  # truncates, not rounds
