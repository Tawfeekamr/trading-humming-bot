"""Grid strategy engine — places buy/sell limit orders at ATR-spaced intervals.

Uses Rust indicators (via trading_engine_core wheel) for EMA, RSI,
Bollinger Bands, and ATR calculations. Strategy logic is Python.

Ported from src/grid/grid_manager.py + grid_state.py + order_tracker.py
"""
from enum import Enum
from typing import Optional

from trading_engine_core import Ema, Rsi, BollingerBands, Atr

from .base import Strategy
from ..adapter.base import OrderFill


class GridState(Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class GridStrategy(Strategy):
    """Grid strategy — places symmetric orders around mid price.

    Config keys (passed via self.config dict):
        levels: int (5) — orders per side
        capital: float (5000) — total capital allocated
        spacing_atr_multiplier: float (1.5) — grid spacing = ATR × this
        min_usdt_reserve: float (100) — keep this in reserve
        ema_period: int (200) — trend filter
        rsi_period: int (14) — RSI
        bollinger_period: int (20) — Bollinger Bands
        bollinger_std_dev: float (2.0)
        atr_period: int (14)
        order_refresh_seconds: int (60)
    """

    def __init__(self, instrument_id: str, config: dict):
        super().__init__(instrument_id, config)

        # Indicators (Rust via PyO3)
        period_ema = config.get("ema_period", 200)
        period_rsi = config.get("rsi_period", 14)
        period_atr = config.get("atr_period", 14)
        period_bb = config.get("bollinger_period", 20)
        std_bb = config.get("bollinger_std_dev", 2.0)

        self.ema = Ema(period_ema)
        self.rsi = Rsi(period_rsi)
        self.atr = Atr(period_atr)
        self.bollinger = BollingerBands(period_bb, std_bb)

        # State
        self.state = GridState.INACTIVE
        self.active_orders: dict[str, dict] = {}  # order_id → level info
        self.base_price: Optional[float] = None
        self.last_refresh_time: int = 0
        self.total_pnl: float = 0.0
        self.trade_count: int = 0

    def on_start(self):
        self.adapter  # Verify adapter is set
        # Nothing else needed — indicators self-initialize with data

    def on_bar(self, bar: dict):
        # Update indicators
        close = bar["close"]
        high = bar["high"]
        low = bar["low"]
        ts = bar.get("timestamp", 0)

        self.ema.update(close)
        self.rsi.update(close)
        self.atr.update_bar(close, high, low, close)
        self.bollinger.update(close)

        # Need initialized indicators
        if not (self.ema.is_initialized and self.atr.is_initialized):
            return

        # Evaluate state
        mid_price = close
        new_state = self._evaluate_state(mid_price)

        # State transitions
        if self.state in (GridState.INACTIVE, GridState.PAUSED) and new_state == GridState.ACTIVE:
            self._place_grid(mid_price, ts)
        elif self.state == GridState.ACTIVE and new_state != GridState.ACTIVE:
            self._cancel_grid()

        self.state = new_state

        # Refresh grid if timer expired
        if self.state == GridState.ACTIVE:
            refresh_interval = self.config.get("order_refresh_seconds", 60)
            if ts - self.last_refresh_time >= refresh_interval:
                self._cancel_grid()
                self._place_grid(mid_price, ts)

    def on_stop(self):
        self._cancel_grid()

    def on_order_filled(self, fill: OrderFill):
        """Handle grid order fills."""
        order_id = fill.client_order_id
        if order_id in self.active_orders:
            level = self.active_orders.pop(order_id)
            self.trade_count += 1

    def _evaluate_state(self, mid_price: float) -> GridState:
        """Evaluate grid state based on indicators."""
        # Circuit breaker check
        if hasattr(self, '_circuit_breaker') and self._circuit_breaker:
            allowed, reason = self._circuit_breaker.check()
            if not allowed:
                return GridState.STOPPED

        # Simple rules (matches existing Python logic):
        # Activate: price above EMA, RSI not overbought
        ema_val = self.ema.value
        rsi_val = self.rsi.value

        price_above_ema = mid_price > ema_val if ema_val > 0 else True
        rsi_ok = self.config.get("rsi_oversold", 35) < rsi_val < self.config.get("rsi_overbought", 70)

        if price_above_ema and rsi_ok:
            return GridState.ACTIVE
        elif not price_above_ema:
            return GridState.PAUSED
        return self.state  # Maintain current state

    def _place_grid(self, mid_price: float, timestamp: int):
        """Place symmetric grid orders."""
        levels = self.config.get("levels", 5)
        spacing_mult = self.config.get("spacing_atr_multiplier", 1.5)
        atr_val = self.atr.value

        if atr_val <= 0:
            return

        spacing = atr_val * spacing_mult
        instrument = self.get_instrument()

        for i in range(1, levels + 1):
            # Buy level (below mid)
            buy_price = instrument.round_price(mid_price - spacing * i)
            buy_qty = instrument.round_quantity(
                self.config.get("capital", 5000) / levels / buy_price
            )
            if buy_qty > 0:
                oid = self.buy_limit(buy_price, buy_qty)
                self.active_orders[oid] = {"side": "BUY", "level": i, "price": buy_price}

            # Sell level (above mid)
            sell_price = instrument.round_price(mid_price + spacing * i)
            sell_qty = instrument.round_quantity(
                self.config.get("capital", 5000) / levels / sell_price
            )
            if sell_qty > 0:
                oid = self.sell_limit(sell_price, sell_qty)
                self.active_orders[oid] = {"side": "SELL", "level": i, "price": sell_price}

        self.base_price = mid_price
        self.last_refresh_time = timestamp

    def _cancel_grid(self):
        """Cancel all active grid orders."""
        self.cancel_all()
        self.active_orders.clear()
        self.base_price = None

    def format_status(self) -> str:
        orders = len(self.active_orders)
        return (
            f"Grid({self.instrument_id}) state={self.state.value} "
            f"orders={orders} trades={self.trade_count} "
            f"EMA={self.ema.value:.2f} RSI={self.rsi.value:.1f} ATR={self.atr.value:.4f}"
        )
