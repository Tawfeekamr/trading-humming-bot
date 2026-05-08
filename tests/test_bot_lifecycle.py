import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone
from pathlib import Path
from src.indicators.bollinger import BollingerBands
from src.indicators.rsi import RSI
from src.indicators.ema import EMA
from src.indicators.atr import ATR
from src.grid.grid_manager import GridManager
from src.grid.grid_state import GridStateMachine, GridState
from src.grid.order_tracker import OrderTracker
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.position_guard import PositionGuard
from src.journal.trade_journal import TradeJournal, Trade
from src.logging.event_logger import EventLogger


@pytest.fixture
def components(tmp_path):
    return {
        "bb": BollingerBands(20, 2.0), "rsi": RSI(14),
        "ema": EMA(200), "atr": ATR(14, 0.8),
        "gm": GridManager(levels=5, capital_usdt=200, min_reserve=50, spacing_multiplier=0.8),
        "sm": GridStateMachine(), "tracker": OrderTracker(),
        "cb": CircuitBreaker(10.0, 5.0),
        "pg": PositionGuard(80, 50, 200),
        "journal": TradeJournal(db_path=tmp_path / "lifecycle.db"),
        "event_log": EventLogger(log_dir=str(tmp_path / "logs")),
    }


@pytest.fixture
def uptrend_candles():
    np.random.seed(42)
    n = 250
    trend = np.linspace(0, 5000, n)
    noise = np.random.normal(0, 300, n)
    close = pd.Series(100_000 + trend + noise)
    high = close + np.random.uniform(100, 500, n)
    low = close - np.random.uniform(100, 500, n)
    return high, low, close


class TestFullTickPipeline:
    def test_indicators_to_grid_to_state(self, components, uptrend_candles):
        high, low, close = uptrend_candles
        bb = components["bb"].calculate(close)
        rsi = components["rsi"].calculate(close)
        ema = components["ema"].calculate(close)
        atr = components["atr"].calculate(high, low, close)

        assert bb is not None
        assert rsi is not None
        assert ema is not None
        assert atr is not None

        price = float(close.iloc[-1])
        state = components["sm"].evaluate(
            price=price, rsi=rsi, ema_200=ema,
            bb_lower=bb.lower, bb_upper=bb.upper,
        )
        assert state in (GridState.ACTIVE, GridState.REACTIVATING)

        grid = components["gm"].calculate_grid(bb, atr)
        assert len(grid.buy_levels) > 0
        assert len(grid.sell_levels) > 0

    def test_circuit_breaker_halts(self, components):
        cb = components["cb"]
        cb.set_peak_equity(200)
        assert cb.check(179) is True
        assert cb.halted

    def test_trade_journal_round_trip(self, components):
        journal = components["journal"]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        trade = Trade(
            timestamp=ts, pair="BTC/USDT", side="SELL",
            entry_price=100_000, exit_price=100_050, quantity=0.001,
            gross_pnl=0.05, fee=0.015, net_pnl=0.035,
            grid_level=1, duration_min=60,
            rsi=45, bb_upper=105_000, bb_lower=95_000,
            ema_200=100_000, atr=800, grid_state="ACTIVE",
        )
        row_id = journal.log_trade(trade)
        assert row_id > 0
        assert journal.summary_today()["total_trades"] >= 1

    def test_fee_overtrading_detection(self, components):
        journal = components["journal"]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(20):
            journal.log_trade(Trade(
                timestamp=ts, pair="BTC/USDT", side="SELL",
                entry_price=100_000, exit_price=100_005, quantity=0.001,
                gross_pnl=0.005, fee=0.015, net_pnl=-0.010,
                grid_level=i % 5, duration_min=30,
                rsi=50, bb_upper=105_000, bb_lower=95_000,
                ema_200=100_000, atr=800, grid_state="ACTIVE",
            ))
        ot = journal.is_overtrading(threshold=0.30)
        assert ot["is_overtrading"] is True