"""Integration layer — wires trading_engine into a running Hummingbot strategy.

The Hummingbot script calls init_trading_engine() once at startup,
then tick_trading_engine() each on_tick, and route_fill() on did_fill_order.
"""
import time

from ..host import StrategyHost
from ..strategy.grid import GridStrategy
from .hummingbot import HummingbotAdapter
from ..adapter.base import OrderFill, InstrumentInfo


def build_grid_config(pair: str, yaml_config: dict) -> dict:
    """Build GridStrategy config from the existing YAML config values."""
    return {
        "levels": yaml_config.get("grid_levels", 5),
        "capital": yaml_config.get("capital", 5000),
        "spacing_atr_multiplier": yaml_config.get("spacing_atr_multiplier", 1.5),
        "ema_period": yaml_config.get("ema_period", 200),
        "rsi_period": yaml_config.get("rsi_period", 14),
        "atr_period": yaml_config.get("atr_period", 14),
        "bollinger_period": yaml_config.get("bollinger_period", 20),
        "bollinger_std_dev": yaml_config.get("bollinger_std_dev", 2.0),
        "order_refresh_seconds": yaml_config.get("order_refresh_seconds", 60),
        "rsi_oversold": yaml_config.get("rsi_oversold", 35),
        "rsi_overbought": yaml_config.get("rsi_overbought", 70),
    }


def init_trading_engine(connector, strategy_ref, pairs: list[str], config: dict) -> StrategyHost:
    """Initialize the trading engine for use inside a Hummingbot script."""
    adapter = HummingbotAdapter(connector, strategy_ref)
    host = StrategyHost(adapter)
    for pair in pairs:
        grid_config = build_grid_config(pair, config)
        host.add_strategy(GridStrategy(pair, grid_config))
    host.start()
    return host


def tick_trading_engine(host: StrategyHost, pair: str, bar: dict):
    """Feed a bar to the trading engine on each on_tick."""
    bar["instrument_id"] = pair
    host.on_bar(bar)


def route_fill(host: StrategyHost, event):
    """Route a Hummingbot fill event to the trading engine."""
    side = "BUY"
    trade_type = getattr(event, 'trade_type', None)
    if trade_type is not None:
        side = "BUY" if "BUY" in str(trade_type).upper() else "SELL"
    fill = OrderFill(
        client_order_id=getattr(event, 'order_id', ''),
        instrument_id=getattr(event, 'trading_pair', ''),
        side=side,
        price=float(getattr(event, 'price', 0)),
        quantity=float(getattr(event, 'amount', 0)),
        timestamp=int(time.time()),
    )
    host.on_order_filled(fill)
