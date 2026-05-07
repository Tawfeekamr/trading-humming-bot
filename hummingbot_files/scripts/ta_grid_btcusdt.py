"""
ta_grid_btcusdt.py — TA-Enhanced BTC/USDT Grid Bot
Hummingbot v2 ScriptStrategyBase implementation.

Start: start --script ta_grid_btcusdt.py
"""

import os
import asyncio
import logging
from decimal import Decimal
from typing import Dict

import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.indicators.bollinger import BollingerBands
from src.indicators.rsi import RSI
from src.indicators.ema import EMA
from src.indicators.atr import ATR
from src.grid.grid_manager import GridManager
from src.grid.grid_state import GridStateMachine, GridState
from src.grid.order_tracker import OrderTracker
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.position_guard import PositionGuard
from src.data.candle_feed import CandleFeed
from src.notifications.telegram_bot import TelegramBot
from src.journal.trade_journal import TradeJournal, Trade

try:
    from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
    from hummingbot.core.event.events import OrderType, TradeType
except ImportError:
    # Allow testing without Hummingbot installed
    ScriptStrategyBase = object
    OrderType = type("OrderType", (), {"LIMIT": "LIMIT"})
    TradeType = type("TradeType", (), {"BUY": "BUY", "SELL": "SELL"})

logger = logging.getLogger(__name__)


class TAGridBTCUSDT(ScriptStrategyBase):
    """
    TA-Enhanced Grid Bot strategy for Hummingbot v2.
    Uses Bollinger Bands, RSI, EMA 200, and ATR to dynamically
    manage a grid of buy/sell orders on BTC/USDT.
    """

    exchange = "binance"
    trading_pair = "BTC-USDT"
    order_refresh_time = 60

    levels = 8
    capital_usdt = float(os.environ.get("GRID_CAPITAL_USDT", "200"))
    min_reserve = float(os.environ.get("MIN_USDT_RESERVE", "50"))

    bb_period = 20
    bb_std = 2.0
    rsi_period = 14
    rsi_overbought = 70.0
    rsi_oversold = 35.0
    ema_period = 200
    atr_period = 14
    atr_multiplier = 0.8

    max_drawdown_pct = float(os.environ.get("MAX_DRAWDOWN_PCT", "10"))
    daily_loss_limit_pct = 5.0
    max_btc_exposure_pct = float(os.environ.get("MAX_BTC_EXPOSURE_PCT", "80"))

    env = os.environ.get("ENV", "paper")
    is_testnet = env == "paper"

    markets = {
        "binance": {"BTC-USDT": {}}
    }

    def __init__(self, connectors: Dict):
        super().__init__(connectors)

        self.bb = BollingerBands(self.bb_period, self.bb_std)
        self.rsi = RSI(self.rsi_period)
        self.ema = EMA(self.ema_period)
        self.atr = ATR(self.atr_period, self.atr_multiplier)

        self.grid_manager = GridManager(
            levels=self.levels,
            capital_usdt=self.capital_usdt,
            min_reserve=self.min_reserve,
            spacing_multiplier=self.atr_multiplier,
        )
        self.state_machine = GridStateMachine()
        self.order_tracker = OrderTracker()
        self.circuit_breaker = CircuitBreaker(self.max_drawdown_pct, self.daily_loss_limit_pct)
        self.position_guard = PositionGuard(
            self.max_btc_exposure_pct, self.min_reserve, self.capital_usdt
        )
        self.candle_feed = CandleFeed(
            symbol="BTCUSDT",
            interval="1h",
            testnet=self.is_testnet,
        )
        self.telegram = TelegramBot()
        self.journal = TradeJournal()

        self._peak_equity = self.capital_usdt

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_startup(self.env, self.capital_usdt))
        except RuntimeError:
            pass

    def on_tick(self):
        if self.circuit_breaker.halted:
            return

        try:
            df = self.candle_feed.fetch_candles(limit=250)
        except Exception as e:
            logger.error(f"Candle fetch failed: {e}")
            return

        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        current_price = float(closes.iloc[-1])

        bb_result = self.bb.calculate(closes)
        rsi_value = self.rsi.calculate(closes)
        ema_value = self.ema.calculate(closes)
        atr_value = self.atr.calculate(highs, lows, closes)

        if any(v is None for v in [bb_result, rsi_value, ema_value, atr_value]):
            logger.warning("Insufficient data for indicator calculation")
            return

        prev_state = self.state_machine.state
        new_state = self.state_machine.evaluate(
            price=current_price,
            rsi=rsi_value,
            ema_200=ema_value,
            bb_lower=bb_result.lower,
            bb_upper=bb_result.upper,
            rsi_overbought=self.rsi_overbought,
            rsi_oversold=self.rsi_oversold,
        )

        if new_state != prev_state:
            logger.info(f"Grid state: {prev_state.value} -> {new_state.value}")
            self._notify_state_change(new_state, current_price, rsi_value, bb_result)

        if self.state_machine.is_paused:
            self._cancel_all_orders()
            return

        equity = self._estimate_equity(current_price)
        self.circuit_breaker.update_peak(equity)
        if self.circuit_breaker.check(equity):
            self._cancel_all_orders()
            logger.critical("Circuit breaker triggered!")
            return

        grid = self.grid_manager.calculate_grid(bb_result, atr_value)
        self._place_grid_orders(grid, current_price)

    def _place_grid_orders(self, grid, current_price: float):
        self._cancel_all_orders()
        connector = self.connectors.get(self.exchange)
        if not connector:
            return

        for level in grid.buy_levels:
            if level["price"] >= current_price:
                continue
            if not self.position_guard.can_place_order(
                current_btc=self._get_btc_balance(),
                btc_price=current_price,
                current_usdt=self._get_usdt_balance(),
                order_usdt=level["price"] * level["quantity"],
            ):
                continue
            self.place_order(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.BUY,
                amount=Decimal(str(level["quantity"])),
                price=Decimal(str(level["price"])),
            )

        for level in grid.sell_levels:
            if level["price"] <= current_price:
                continue
            self.place_order(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.SELL,
                amount=Decimal(str(level["quantity"])),
                price=Decimal(str(level["price"])),
            )

    def _cancel_all_orders(self):
        connector = self.connectors.get(self.exchange)
        if not connector:
            return
        for order in list(getattr(connector, "in_flight_orders", {}).values()):
            self.cancel_order(self.exchange, order.trading_pair, order.client_order_id)

    def _get_usdt_balance(self) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)("USDT")
        return float(balance.available) if balance else 0.0

    def _get_btc_balance(self) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)("BTC")
        return float(balance.available) if balance else 0.0

    def _estimate_equity(self, btc_price: float) -> float:
        return self._get_usdt_balance() + (self._get_btc_balance() * btc_price)

    def _notify_state_change(self, new_state, price, rsi, bb):
        if new_state == GridState.ACTIVE:
            msg = (
                f"🟢 <b>Grid ACTIVATED — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📐 Range: ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
                f"📊 RSI: {rsi:.1f}"
            )
        elif new_state == GridState.PAUSED:
            msg = (
                f"⏸️ <b>Grid PAUSED — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f}\n"
                f"💤 Holding USDT until re-entry signal."
            )
        elif new_state == GridState.REACTIVATING:
            msg = (
                f"🔄 <b>Grid REACTIVATING — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f} (oversold bounce)\n"
                f"📐 New range: ${bb.lower:,.0f} → ${bb.upper:,.0f}"
            )
        else:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.send(msg))
        except RuntimeError:
            pass

    def did_fill_order(self, event):
        order = event.order
        trade = Trade(
            timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
            pair="BTC/USDT",
            side="BUY" if str(order.trade_type) == "BUY" else "SELL",
            entry_price=float(order.price),
            exit_price=float(order.price),
            quantity=float(order.amount),
            gross_pnl=0.0,
            fee=0.0,
            net_pnl=0.0,
            grid_level=0,
            duration_min=0,
            rsi=0.0,
            bb_upper=0.0,
            bb_lower=0.0,
            ema_200=0.0,
            atr=0.0,
            grid_state=self.state_machine.state.value,
        )
        trade_id = self.journal.log_trade(trade)
        logger.info(f"Trade filled: {trade.side} {trade.quantity} @ {trade.entry_price}")
