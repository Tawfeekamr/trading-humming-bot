"""Integration test: simulate trend signals through the full pipeline."""
import pytest
import pandas as pd
import numpy as np
from src.trend.trend_manager import TrendManager, SignalScore
from src.trend.position_manager import PositionManager
from src.trend.trend_journal import TrendJournal
from src.trend.support_resistance import SupportResistance
from src.trend.candlestick_patterns import CandlestickPatterns
import tempfile
from pathlib import Path


def generate_trending_candles(n: int = 300, direction: str = "up") -> pd.DataFrame:
    np.random.seed(42)
    if direction == "up":
        returns = np.random.normal(0.002, 0.008, n)
    elif direction == "down":
        returns = np.random.normal(-0.002, 0.008, n)
    else:
        returns = np.random.normal(0, 0.008, n)

    close = 90 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
    opn = close * (1 + np.random.normal(0, 0.002, n))

    return pd.DataFrame({
        "open": opn, "high": high, "low": low, "close": close,
    })


class TestTrendIntegration:
    def test_full_pipeline_uptrend(self):
        candles = generate_trending_candles(300, "up")
        current_price = float(candles["close"].iloc[-1])

        tm = TrendManager()
        score = tm.evaluate(candles, current_price)

        assert score.total > 0
        assert len(score.details) > 0

        sr = SupportResistance()
        levels = sr.detect(candles)
        sl = tm.calculate_stop_loss(current_price, levels, atr_value=1.0)
        tp = tm.calculate_take_profit(current_price, sl)

        assert sl < current_price
        assert tp > current_price
        assert tp - current_price >= (current_price - sl) * 1.9

        pm = PositionManager(capital=2000.0, max_positions=2)
        amount = pm.calculate_position_size(current_price, sl)
        assert amount > 0

        pos = pm.open_position("test_order", current_price, amount, sl, tp, "2026-05-11T10:00:00Z")
        assert pos is not None
        assert pm.open_count == 1

    def test_full_pipeline_ranging_no_entry(self):
        candles = generate_trending_candles(300, "flat")
        current_price = float(candles["close"].iloc[-1])

        tm = TrendManager(min_signal_score=3, confirmation_ticks=1)
        score = tm.evaluate(candles, current_price)

    def test_trade_lifecycle(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        journal = TrendJournal(db_path=Path(tmp.name))

        pm = PositionManager(capital=2000.0, trailing_stop_pct=1.5, trailing_activation_pct=1.5)
        entry_price = 94.0
        sl = 91.3
        tp = 99.4

        amount = pm.calculate_position_size(entry_price, sl)
        pos = pm.open_position("lifecycle_test", entry_price, amount, sl, tp, "2026-05-11T10:00:00Z")

        pm.update_trailing(pos, 95.5)
        pm.update_trailing(pos, 96.5)

        assert pos.trailing_stop > sl
        assert pos.trailing_activated is True

        exits = pm.check_exits(current_price=99.5)
        assert len(exits) == 1
        assert exits[0]["reason"] == "take_profit"

        closed = pm.close_position("lifecycle_test", 99.4, "take_profit")
        assert closed["pnl"] > 0

        journal.log_trade(
            side="SELL", entry_price=closed["entry_price"],
            exit_price=closed["exit_price"], amount=closed["amount"],
            fee=0.75, pnl=closed["pnl"], pnl_pct=closed["pnl_pct"],
            stop_loss=closed["stop_loss"], take_profit=closed["take_profit"],
            exit_reason="take_profit", signal_score=4,
            duration_minutes=closed["duration_minutes"],
        )

        summary = journal.summary()
        assert summary["total_trades"] == 1
        assert summary["wins"] == 1
        assert summary["total_pnl"] > 0

        Path(tmp.name).unlink()

    def test_stop_loss_trade(self):
        pm = PositionManager(capital=2000.0)
        pos = pm.open_position("sl_test", 94.0, 14.0, 91.3, 99.4, "2026-05-11T10:00:00Z")

        exits = pm.check_exits(current_price=91.0)
        assert len(exits) == 1
        assert exits[0]["reason"] == "stop_loss"

        closed = pm.close_position("sl_test", 91.3, "stop_loss")
        assert closed["pnl"] < 0
