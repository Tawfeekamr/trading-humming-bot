"""Tests for the Forex grid strategy."""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from src.nautilus.strategies.forex_grid import ForexGridStrategy, ForexGridConfig


def _make_config(
    symbol="EUR/USD.IDEALPRO",
    bar_type="EUR/USD.IDEALPRO-1-HOUR-BID-INTERNAL",
    levels=5,
    spacing_pips=10,
    trade_size="10000",
):
    return ForexGridConfig(
        instrument_id=symbol,
        bar_type=bar_type,
        levels=levels,
        spacing_pips=spacing_pips,
        take_profit_pips=15,
        stop_loss_pips=30,
        trade_size=Decimal(trade_size),
        ema_fast=20,
        ema_slow=50,
        rsi_period=14,
        rsi_oversold=35,
        rsi_overbought=70,
    )


def test_config_defaults():
    config = _make_config()
    assert config.levels == 5
    assert config.spacing_pips == 10
    assert config.trade_size == Decimal("10000")


def test_strategy_creation():
    config = _make_config()
    strategy = ForexGridStrategy(config)
    assert strategy.config.levels == 5
    assert strategy.config.spacing_pips == 10


def test_strategy_pip_value_eurusd():
    """EUR/USD pip = 0.0001 (4-decimal pairs)."""
    config = _make_config(symbol="EUR/USD.IDEALPRO")
    strategy = ForexGridStrategy(config)
    pip = strategy._pip_size("EUR/USD")
    assert pip == 0.0001


def test_strategy_pip_value_usdjpy():
    """USD/JPY pip = 0.01 (2-decimal JPY pairs)."""
    config = _make_config(symbol="USD/JPY.IDEALPRO")
    strategy = ForexGridStrategy(config)
    pip = strategy._pip_size("USD/JPY")
    assert pip == 0.01


def test_strategy_grid_level_prices():
    """Grid levels should be symmetric around mid price."""
    config = _make_config(spacing_pips=10)
    strategy = ForexGridStrategy(config)

    pip = strategy._pip_size("EUR/USD")  # 0.0001
    mid_price = 1.1000

    levels = strategy._calculate_grid_levels(mid_price)
    # 5 levels each side = 10 total
    assert len(levels) == 10

    # First buy level should be mid - 1 * spacing_pips * pip
    assert abs(levels[0]["price"] - (mid_price - 10 * pip)) < 1e-10
    # First sell level should be mid + 1 * spacing_pips * pip
    assert abs(levels[5]["price"] - (mid_price + 10 * pip)) < 1e-10


def test_strategy_handles_jpy_pairs():
    """Grid calculation should work for JPY pairs with pip=0.01."""
    config = _make_config(symbol="USD/JPY.IDEALPRO", spacing_pips=5)
    strategy = ForexGridStrategy(config)

    mid_price = 150.00
    levels = strategy._calculate_grid_levels(mid_price)

    pip = 0.01
    assert abs(levels[0]["price"] - (mid_price - 5 * pip)) < 1e-10
