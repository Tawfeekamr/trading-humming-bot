from src.trading_engine.risk.position_guard import PositionGuard


def test_allows_initial_position():
    pg = PositionGuard(max_total_positions=3)
    allowed, _ = pg.can_open("BTC-USDT", "grid", 1000.0, 10000.0)
    assert allowed


def test_blocks_max_total():
    pg = PositionGuard(max_total_positions=2)
    pg.register("k1", "BTC-USDT", "grid", 1000)
    pg.register("k2", "ETH-USDT", "grid", 1000)
    allowed, reason = pg.can_open("XRP-USDT", "grid", 1000.0, 10000.0)
    assert not allowed
    assert "total" in reason.lower()


def test_blocks_per_pair_limit():
    pg = PositionGuard(max_positions_per_pair=1, max_total_positions=10)
    pg.register("k1", "BTC-USDT", "grid", 1000)
    allowed, reason = pg.can_open("BTC-USDT", "trend", 1000.0, 10000.0)
    assert not allowed
    assert "per-pair" in reason.lower()


def test_blocks_duplicate():
    pg = PositionGuard()
    pg.register("k1", "BTC-USDT", "grid", 1000)
    allowed, reason = pg.can_open("BTC-USDT", "grid", 1000.0, 10000.0)
    assert not allowed
    assert "duplicate" in reason.lower()


def test_close_removes_position():
    pg = PositionGuard(max_total_positions=1)
    pg.register("k1", "BTC-USDT", "grid", 1000)
    assert pg.open_count == 1
    pg.close("k1")
    assert pg.open_count == 0
    allowed, _ = pg.can_open("ETH-USDT", "grid", 1000.0, 10000.0)
    assert allowed


def test_blocks_exposure():
    pg = PositionGuard(max_exposure_pct=50.0, max_total_positions=10)
    pg.register("k1", "BTC-USDT", "grid", 4000)
    # 4000 already deployed, trying to add 2000 more = 60% of 10000
    allowed, reason = pg.can_open("ETH-USDT", "grid", 2000.0, 10000.0)
    assert not allowed
    assert "exposure" in reason.lower()
