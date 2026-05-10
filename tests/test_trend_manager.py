import pytest
import pandas as pd
import numpy as np
from src.trend.trend_manager import TrendManager, SignalScore


@pytest.fixture
def trend_manager():
    return TrendManager(
        ema_fast=20, ema_slow=50, ema_trend=200,
        rsi_period=14, rsi_min=40, rsi_max=70,
        min_signal_score=3, confirmation_ticks=2,
    )


def make_candles_with_trend(n: int = 250, trend: str = "up") -> pd.DataFrame:
    np.random.seed(42)
    if trend == "up":
        base = np.cumsum(np.random.uniform(-0.5, 1.0, n)) + 90
    elif trend == "down":
        base = np.cumsum(np.random.uniform(-1.0, 0.5, n)) + 100
    else:
        base = np.cumsum(np.random.uniform(-0.5, 0.5, n)) + 95
    return pd.DataFrame({
        "open": base - 0.2,
        "high": base + 0.5,
        "low": base - 0.5,
        "close": base,
    })


class TestTrendManager:
    def test_score_structure(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        assert isinstance(score, SignalScore)
        assert hasattr(score, "total")
        assert hasattr(score, "details")
        assert score.total >= 0
        assert score.total <= 7

    def test_score_details_list(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        assert isinstance(score.details, list)
        for d in score.details:
            assert "signal" in d
            assert "points" in d

    def test_ema_cross_adds_points(self, trend_manager):
        n = 250
        base = np.linspace(90, 100, n)
        candles = pd.DataFrame({
            "open": base - 0.1, "high": base + 0.3,
            "low": base - 0.3, "close": base,
        })
        score = trend_manager.evaluate(candles, float(candles["close"].iloc[-1]))
        assert score.total >= 1

    def test_should_enter_requires_minimum_score(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        if score.total >= 3:
            assert trend_manager.should_enter(score) is True
        else:
            assert trend_manager.should_enter(score) is False

    def test_should_exit_on_low_score(self, trend_manager):
        score = SignalScore(total=1, details=[])
        assert trend_manager.should_exit(score) is True

    def test_should_not_exit_on_high_score(self, trend_manager):
        score = SignalScore(total=4, details=[])
        assert trend_manager.should_exit(score) is False

    def test_confirmation_ticks(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        if score.total >= 3:
            assert trend_manager.confirm_entry(score) is False
            assert trend_manager.confirm_entry(score) is True

    def test_calculate_stop_loss_with_support(self, trend_manager):
        levels = [{"price": 92.0, "type": "support", "touches": 3, "strength": 0.6}]
        sl = trend_manager.calculate_stop_loss(94.0, levels, atr_value=0.5)
        assert sl < 92.0
        assert sl == pytest.approx(91.82, abs=0.01)

    def test_calculate_stop_loss_without_support(self, trend_manager):
        sl = trend_manager.calculate_stop_loss(94.0, [], atr_value=0.5)
        assert sl == pytest.approx(93.0, abs=0.01)

    def test_calculate_take_profit(self, trend_manager):
        tp = trend_manager.calculate_take_profit(94.0, 91.3)
        risk = 94.0 - 91.3
        expected_tp = 94.0 + risk * 2.0
        assert tp == pytest.approx(expected_tp, abs=0.01)
