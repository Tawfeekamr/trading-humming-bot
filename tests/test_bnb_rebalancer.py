# tests/test_bnb_rebalancer.py
"""Tests for BNB rebalancer — maintains BNB balance for fee payments."""
import pytest
from src.risk.bnb_rebalancer import BNBRebalancer


class TestBNBRebalancer:
    def setup_method(self):
        self.rebalancer = BNBRebalancer(
            bnb_min_usdt=10.0,
            bnb_target_usdt=20.0,
            bnb_max_usdt=50.0,
        )

    def test_no_action_when_balance_in_range(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=25.0)
        assert result.action == "none"

    def test_buy_triggered_below_min(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=5.0)
        assert result.action == "buy"
        assert result.amount_usdt == 15.0  # target (20) - current (5)

    def test_sell_triggered_above_max(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=60.0)
        assert result.action == "sell"
        assert result.amount_usdt == 40.0  # current (60) - target (20)

    def test_no_action_at_min_boundary(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=10.0)
        assert result.action == "none"

    def test_no_action_at_max_boundary(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=50.0)
        assert result.action == "none"

    def test_cooldown_prevents_rapid_rebalance(self):
        r1 = self.rebalancer.evaluate(bnb_balance_usdt=5.0)
        assert r1.action == "buy"
        r2 = self.rebalancer.evaluate(bnb_balance_usdt=5.0)
        assert r2.action == "none"

    def test_buy_amount_capped_to_available(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=5.0, available_usdt=10.0)
        assert result.action == "buy"
        assert result.amount_usdt <= 10.0
