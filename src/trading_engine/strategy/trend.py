"""Trend strategy engine — point-based entry with EMA cross, RSI, S/R scoring.

Uses Rust indicators (via trading_engine_core wheel) for fast bar-by-bar updates.
Delegates scoring to existing TrendManager and position management to
PositionManager. Accumulates bars in a rolling buffer for DataFrame-based
evaluate() calls.
"""
from enum import Enum
from typing import Optional
import logging

from trading_engine_core import Ema, Rsi, Atr

from .base import Strategy
from ..adapter.base import OrderFill

from src.trend.trend_manager import TrendManager, SignalScore
from src.trend.position_manager import PositionManager, TrendPosition

import pandas as pd

logger = logging.getLogger(__name__)


class TrendState(Enum):
    FLAT = "flat"
    SCORING = "scoring"
    PENDING_ENTRY = "pending_entry"
    IN_POSITION = "in_position"
    EXITING = "exiting"


class TrendStrategy(Strategy):
    """Trend-following strategy using point-based signal scoring."""

    def __init__(self, instrument_id: str, config: dict):
        super().__init__(instrument_id, config)

        # Rust indicators (bar-by-bar update)
        self.ema_fast = Ema(config.get("ema_fast", 20))
        self.ema_slow = Ema(config.get("ema_slow", 50))
        self.ema_trend = Ema(config.get("ema_trend", 200))
        self.rsi = Rsi(config.get("rsi_period", 14))
        self.atr = Atr(config.get("atr_period", 14))

        # Rolling bar buffer for TrendManager (needs DataFrame)
        self._bars: list[dict] = []
        self._max_bars: int = 250

        # Existing trend logic (unchanged)
        self._trend_mgr = TrendManager(
            ema_fast=config.get("ema_fast", 20),
            ema_slow=config.get("ema_slow", 50),
            ema_trend=config.get("ema_trend", 200),
            rsi_period=config.get("rsi_period", 14),
            rsi_min=config.get("rsi_min", 40),
            rsi_max=config.get("rsi_max", 70),
            min_signal_score=config.get("min_signal_score", 3),
            confirmation_ticks=config.get("confirmation_ticks", 2),
            sl_buffer_pct=config.get("sl_buffer_pct", 0.2),
            rr_ratio=config.get("rr_ratio", 2.0),
            exit_signal_threshold=config.get("exit_signal_threshold", 2),
        )
        self._pos_mgr = PositionManager(
            capital=config.get("capital", 2000),
            max_positions=config.get("max_positions", 1),
            risk_per_trade_pct=config.get("risk_pct", 2.0),
            max_position_pct=config.get("max_position_pct", 75.0),
            trailing_stop_pct=config.get("trail_distance_pct", 1.5),
            trailing_activation_pct=config.get("trail_activation_pct", 1.5),
        )

        self.state = TrendState.FLAT
        self._entry_order_id: str = ""
        self._atr_value: float = 0.0
        self._pending_sl: float = 0.0
        self._pending_tp: float = 0.0
        self._pending_size: float = 0.0
        self._pending_score: int = 0

    def on_start(self):
        self.adapter  # Verify adapter is set

    def on_bar(self, bar: dict):
        close = bar["close"]
        high = bar["high"]
        low = bar["low"]

        # Update Rust indicators
        self.ema_fast.update(close)
        self.ema_slow.update(close)
        self.ema_trend.update(close)
        self.rsi.update(close)
        self.atr.update_bar(close, high, low, close)
        self._atr_value = self.atr.value

        # Accumulate bar for TrendManager
        self._bars.append(bar)
        if len(self._bars) > self._max_bars:
            self._bars = self._bars[-self._max_bars:]

        # Need enough bars for TrendManager to work
        min_period = min(self.config.get("ema_trend", 200), 20)
        if len(self._bars) < min_period:
            return

        # Convert to DataFrame for TrendManager
        df = pd.DataFrame(self._bars)

        # Check if indicators are ready
        if not self.ema_trend.is_initialized:
            return

        if self.state == TrendState.FLAT:
            self.state = TrendState.SCORING

        # Evaluate signal
        score = self._trend_mgr.evaluate(df, close)

        # State dispatch
        if self.state == TrendState.SCORING:
            self._handle_scoring(score, close)

        elif self.state == TrendState.IN_POSITION:
            self._handle_in_position(score, close)

    def _handle_scoring(self, score: SignalScore, current_price: float):
        if not self._pos_mgr.can_open():
            return
        if self._trend_mgr.confirm_entry(score):
            self._submit_entry(current_price, score)

    def _submit_entry(self, current_price: float, score: SignalScore):
        df = pd.DataFrame(self._bars)
        sr_levels = self._trend_mgr._sr.detect(df)

        stop_loss = self._trend_mgr.calculate_stop_loss(
            current_price, sr_levels, self._atr_value if self._atr_value > 0 else None
        )
        take_profit = self._trend_mgr.calculate_take_profit(current_price, stop_loss)
        size = self._pos_mgr.calculate_position_size(current_price, stop_loss)

        if size <= 0:
            return

        instrument = self.get_instrument()
        size = instrument.round_quantity(size)

        if size > 0:
            oid = self.buy_limit(instrument.round_price(current_price), size)
            self._entry_order_id = oid
            self._pending_sl = stop_loss
            self._pending_tp = take_profit
            self._pending_size = size
            self._pending_score = score.total
            self.state = TrendState.PENDING_ENTRY

    def _handle_in_position(self, score: SignalScore, current_price: float):
        for pos in self._pos_mgr.get_all_positions():
            self._pos_mgr.update_trailing(pos, current_price)

        exits = self._pos_mgr.check_exits(current_price)
        for exit_info in exits:
            self._submit_exit(exit_info["order_id"], exit_info["reason"], exit_info["exit_price"])
            return

        if self._trend_mgr.should_exit(score):
            for pos in self._pos_mgr.get_all_positions():
                if not pos.exit_order_id:
                    self._submit_exit(pos.entry_order_id, "signal_weak", current_price)
                    return

    def _submit_exit(self, entry_order_id: str, reason: str, exit_price: float):
        pos = self._pos_mgr.get_position(entry_order_id)
        if pos is None:
            return
        instrument = self.get_instrument()
        oid = self.sell_limit(instrument.round_price(exit_price), pos.amount)
        self._pos_mgr.mark_exit_pending(entry_order_id, oid, reason)
        self.state = TrendState.EXITING

    def on_order_filled(self, fill: OrderFill):
        if self.state == TrendState.PENDING_ENTRY and fill.client_order_id == self._entry_order_id:
            pos = self._pos_mgr.open_position(
                entry_order_id=fill.client_order_id,
                entry_price=fill.price,
                amount=fill.quantity,
                stop_loss=self._pending_sl,
                take_profit=self._pending_tp,
                entry_time=str(fill.timestamp),
            )
            if pos:
                pos.signal_score = self._pending_score
            self.state = TrendState.IN_POSITION

        elif self.state == TrendState.EXITING:
            pos = self._pos_mgr.get_position_by_exit(fill.client_order_id)
            if pos:
                self._pos_mgr.finalize_exit(pos.entry_order_id, fill.price, fee=0.0)
                self.state = TrendState.SCORING

    def on_stop(self):
        self.cancel_all()

    def format_status(self) -> str:
        ema_f = self.ema_fast.value
        ema_s = self.ema_slow.value
        ema_t = self.ema_trend.value
        rsi_val = self.rsi.value
        pos_count = self._pos_mgr.open_count
        return (
            f"Trend({self.instrument_id}) state={self.state.value} "
            f"positions={pos_count} "
            f"EMA_f={ema_f:.2f} EMA_s={ema_s:.2f} EMA_t={ema_t:.2f} "
            f"RSI={rsi_val:.1f} ATR={self._atr_value:.4f}"
        )
