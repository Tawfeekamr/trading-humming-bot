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
from dataclasses import dataclass as dataclass_fills
from typing import Optional as Optional_fills
import time as time_mod

import pandas as pd
import yaml

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
from src.health import update_health, set_halted, start_health_server
from src.logging_config import setup_logging
from src.logging.event_logger import EventLogger

try:
    from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
    from hummingbot.core.event.events import OrderType, TradeType
except ImportError:
    ScriptStrategyBase = object
    OrderType = type("OrderType", (), {"LIMIT": "LIMIT"})
    TradeType = type("TradeType", (), {"BUY": "BUY", "SELL": "SELL"})

logger = logging.getLogger(__name__)


@dataclass_fills
class FillRecord:
    order_id: str
    side: str
    price: float
    quantity: float
    grid_level: int
    timestamp: float
    rsi: float
    bb_upper: float
    bb_lower: float
    ema_200: float
    atr: float
    grid_state: str


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

    @staticmethod
    def _load_config() -> dict:
        config_paths = [
            Path("config/strategy.yaml"),
            Path(__file__).parent.parent.parent / "config" / "strategy.yaml",
        ]
        for path in config_paths:
            if path.exists():
                with open(path) as f:
                    return yaml.safe_load(f)
        return {}

    def __init__(self, connectors: Dict):
        setup_logging()

        cfg = self._load_config()
        grid_cfg = cfg.get("grid", {})
        ind_cfg = cfg.get("indicators", {})
        risk_cfg = cfg.get("risk", {})

        self.levels = int(os.environ.get("GRID_LEVELS", grid_cfg.get("levels", self.levels)))
        self.capital_usdt = float(os.environ.get("GRID_CAPITAL_USDT", grid_cfg.get("capital_usdt", self.capital_usdt)))
        self.min_reserve = float(os.environ.get("MIN_USDT_RESERVE", grid_cfg.get("min_usdt_reserve", self.min_reserve)))
        self.order_refresh_time = grid_cfg.get("order_refresh_time", self.order_refresh_time)

        bb_cfg = ind_cfg.get("bollinger", {})
        self.bb_period = bb_cfg.get("period", self.bb_period)
        self.bb_std = bb_cfg.get("std_dev", self.bb_std)
        rsi_cfg = ind_cfg.get("rsi", {})
        self.rsi_period = rsi_cfg.get("period", self.rsi_period)
        self.rsi_overbought = rsi_cfg.get("overbought", self.rsi_overbought)
        self.rsi_oversold = rsi_cfg.get("oversold", self.rsi_oversold)
        ema_cfg = ind_cfg.get("ema", {})
        self.ema_period = ema_cfg.get("period", self.ema_period)
        atr_cfg = ind_cfg.get("atr", {})
        self.atr_period = atr_cfg.get("period", self.atr_period)
        self.atr_multiplier = atr_cfg.get("spacing_multiplier", self.atr_multiplier)

        self.max_drawdown_pct = float(os.environ.get("MAX_DRAWDOWN_PCT", risk_cfg.get("max_drawdown_pct", self.max_drawdown_pct)))
        self.daily_loss_limit_pct = risk_cfg.get("daily_loss_limit_pct", self.daily_loss_limit_pct)
        self.max_btc_exposure_pct = float(os.environ.get("MAX_BTC_EXPOSURE_PCT", risk_cfg.get("max_btc_exposure_pct", self.max_btc_exposure_pct)))

        super().__init__(connectors)

        start_health_server(port=8080)

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
        self.event_log = EventLogger(log_dir="logs")

        self._peak_equity = self.capital_usdt
        self._open_buys: dict[int, FillRecord] = {}
        self._last_candle_time = None
        self._cached_indicators = None
        self._grid_dirty = True
        self._last_state_alert_time: dict[str, float] = {}  # state -> last Telegram alert timestamp
        self._state_alert_cooldown = 900  # 15 minutes between repeated state alerts

        self.event_log.log("bot_started", mode=self.env, capital=self.capital_usdt,
                           levels=self.levels, testnet=self.is_testnet)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_startup(self.env, self.capital_usdt))
        except RuntimeError:
            pass

    # ── Main Tick Loop ───────────────────────────────────────────────

    def on_tick(self):
        if self.circuit_breaker.halted:
            return

        now = pd.Timestamp.now(tz="UTC")

        should_fetch = (
            self._last_candle_time is None or
            now - self._last_candle_time >= pd.Timedelta(minutes=55)
        )

        if should_fetch:
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

            self._cached_indicators = (bb_result, rsi_value, ema_value, atr_value, current_price)
            self._last_candle_time = now
            self._grid_dirty = True

            self.event_log.log("indicators_updated",
                rsi=round(rsi_value, 2),
                bb_upper=round(bb_result.upper, 2),
                bb_mid=round(bb_result.mid, 2),
                bb_lower=round(bb_result.lower, 2),
                ema_200=round(ema_value, 2),
                atr=round(atr_value, 2),
                price=round(current_price, 2),
                grid_state=self.state_machine.state.value,
            )

            update_health(self.state_machine.state.value)
        else:
            if self._cached_indicators is None:
                return
            bb_result, rsi_value, ema_value, atr_value, current_price = self._cached_indicators

        # Evaluate state
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
            trigger_reason = self._determine_trigger_reason(
                prev_state, new_state, current_price, rsi_value, ema_value, bb_result
            )
            logger.info(f"Grid state: {prev_state.value} -> {new_state.value} ({trigger_reason})")

            self.event_log.log("state_changed",
                previous_state=prev_state.value,
                new_state=new_state.value,
                trigger_reason=trigger_reason,
                price=round(current_price, 2),
                rsi=round(rsi_value, 2),
                ema_200=round(ema_value, 2),
            )

            self._notify_state_change(new_state, prev_state, trigger_reason, current_price, rsi_value, bb_result, ema_value, atr_value)
            self._grid_dirty = True

        if self.state_machine.is_paused:
            if self._grid_dirty:
                self._cancel_all_orders("state_paused")
                self._grid_dirty = False
            return

        equity = self._estimate_equity(current_price)
        self.circuit_breaker.update_peak(equity)
        if self.circuit_breaker.check(equity):
            self._cancel_all_orders("circuit_breaker")
            drawdown_pct = ((self._peak_equity - equity) / self._peak_equity) * 100 if self._peak_equity > 0 else 0
            self.event_log.log("circuit_breaker",
                drawdown_pct=round(drawdown_pct, 2),
                peak_equity=round(self._peak_equity, 2),
                current_equity=round(equity, 2),
                daily_loss_pct=round(self.circuit_breaker.daily_loss_limit_pct, 2),
            )
            logger.critical("Circuit breaker triggered!")
            set_halted("circuit_breaker")
            return

        if self._grid_dirty:
            grid = self.grid_manager.calculate_grid(bb_result, atr_value)
            self._place_grid_orders(grid, current_price)

            deployed = sum(l["price"] * l["quantity"] for l in grid.buy_levels)
            self.event_log.log("grid_recalculated",
                bb_upper=round(bb_result.upper, 2),
                bb_lower=round(bb_result.lower, 2),
                spacing=round(grid.spacing, 2),
                num_buy_levels=len(grid.buy_levels),
                num_sell_levels=len(grid.sell_levels),
                total_capital_deployed=round(deployed, 2),
                buy_levels=[{"level": l["level"], "price": l["price"], "qty": l["quantity"]} for l in grid.buy_levels],
                sell_levels=[{"level": l["level"], "price": l["price"], "qty": l["quantity"]} for l in grid.sell_levels],
            )
            self._grid_dirty = False

    # ── State Change Helpers ─────────────────────────────────────────

    def _determine_trigger_reason(self, prev_state, new_state, price, rsi, ema_200, bb) -> str:
        if new_state == GridState.PAUSED:
            if rsi > self.rsi_overbought:
                return f"rsi_overbought ({rsi:.1f} > {self.rsi_overbought})"
            if price < ema_200:
                return f"price_below_ema ({price:,.0f} < {ema_200:,.0f})"
            return "combined_pause_signal"
        if new_state == GridState.REACTIVATING:
            return f"rsi_oversold_bounce ({rsi:.1f} < {self.rsi_oversold}, near BB lower)"
        if new_state == GridState.ACTIVE:
            if prev_state == GridState.PAUSED:
                return f"conditions_cleared (rsi={rsi:.1f}, price>ema)"
            if prev_state == GridState.REACTIVATING:
                return f"bounce_confirmed (rsi={rsi:.1f})"
            return "initial_activation"
        return "unknown"

    # ── Order Management ─────────────────────────────────────────────

    def _place_grid_orders(self, grid, current_price: float):
        self._cancel_all_orders("grid_refresh")
        connector = self.connectors.get(self.exchange)
        if not connector:
            return

        usdt_bal = self._get_usdt_balance()
        btc_bal = self._get_btc_balance()
        equity = self._estimate_equity(current_price)
        exposure_pct = self.position_guard.btc_exposure_pct(btc_bal, current_price)

        for level in grid.buy_levels:
            if level["price"] >= current_price:
                continue
            order_usdt = level["price"] * level["quantity"]
            if not self.position_guard.can_place_order(
                current_btc=btc_bal,
                btc_price=current_price,
                current_usdt=usdt_bal,
                order_usdt=order_usdt,
            ):
                self.event_log.log("order_blocked",
                    side="BUY",
                    price=level["price"],
                    quantity=level["quantity"],
                    reason="below_usdt_reserve" if (usdt_bal - order_usdt) < self.min_reserve else "exceeds_btc_exposure",
                    current_usdt=round(usdt_bal, 2),
                    current_btc=round(btc_bal, 8),
                    exposure_pct=round(exposure_pct, 1),
                )
                continue
            self.event_log.log("order_placed",
                side="BUY",
                price=level["price"],
                quantity=level["quantity"],
                grid_level=level["level"],
                current_price=round(current_price, 2),
                usdt_balance=round(usdt_bal, 2),
                btc_balance=round(btc_bal, 8),
                equity=round(equity, 2),
            )
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
            btc_balance = self._get_btc_balance()
            if level["quantity"] > btc_balance:
                self.event_log.log("order_blocked",
                    side="SELL",
                    price=level["price"],
                    quantity=level["quantity"],
                    reason="insufficient_btc",
                    current_btc=round(btc_balance, 8),
                )
                continue
            self.event_log.log("order_placed",
                side="SELL",
                price=level["price"],
                quantity=level["quantity"],
                grid_level=level["level"],
                current_price=round(current_price, 2),
                usdt_balance=round(usdt_bal, 2),
                btc_balance=round(btc_balance, 8),
                equity=round(equity, 2),
            )
            self.place_order(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.SELL,
                amount=Decimal(str(level["quantity"])),
                price=Decimal(str(level["price"])),
            )

    def _cancel_all_orders(self, reason: str = "grid_refresh"):
        connector = self.connectors.get(self.exchange)
        if not connector:
            return
        for order in list(getattr(connector, "in_flight_orders", {}).values()):
            self.event_log.log("order_cancelled",
                order_id=str(order.client_order_id),
                side=str(order.trade_type),
                price=float(order.price),
                reason=reason,
            )
            self.cancel_order(self.exchange, order.trading_pair, order.client_order_id)

    # ── Balance Helpers ──────────────────────────────────────────────

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

    # ── Notifications ────────────────────────────────────────────────

    def _notify_state_change(self, new_state, prev_state, trigger_reason, price, rsi, bb, ema, atr):
        # Cooldown: skip Telegram if we alerted this same state within 15 min
        state_key = new_state.value
        now = time_mod.time()
        last_alert = self._last_state_alert_time.get(state_key, 0)
        if now - last_alert < self._state_alert_cooldown:
            logger.info(f"State alert suppressed (cooldown): {state_key}")
            return
        self._last_state_alert_time[state_key] = now

        spacing = atr * self.atr_multiplier if atr else 0

        if new_state == GridState.ACTIVE:
            msg = (
                f"🟢 <b>Grid ACTIVATED — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📐 Range: ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
                f"📏 Spacing: ${spacing:,.0f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"⚠️ Trigger: {trigger_reason}"
            )
        elif new_state == GridState.PAUSED:
            msg = (
                f"⏸️ <b>Grid PAUSED — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"⚠️ Trigger: {trigger_reason}\n"
                f"💤 Holding USDT until re-entry signal."
            )
        elif new_state == GridState.REACTIVATING:
            msg = (
                f"🔄 <b>Grid REACTIVATING — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📐 New range: ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"⚠️ Trigger: {trigger_reason}"
            )
        else:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.send(msg))
        except RuntimeError:
            pass

    # ── Trade Filled Hook ────────────────────────────────────────────

    def did_fill_order(self, event):
        order = event.order
        side = "BUY" if str(order.trade_type) == "BUY" else "SELL"
        price = float(order.price)
        quantity = float(order.amount)

        rsi_val = 0.0
        bb_upper = 0.0
        bb_lower = 0.0
        ema_val = 0.0
        atr_val = 0.0
        grid_state_val = self.state_machine.state.value

        if self._cached_indicators:
            bb_r, rsi_r, ema_r, atr_r, _ = self._cached_indicators
            rsi_val = rsi_r
            bb_upper = bb_r.upper
            bb_lower = bb_r.lower
            ema_val = ema_r
            atr_val = atr_r

        grid_level = 0
        if self._cached_indicators:
            bb_r = self._cached_indicators[0]
            mid = bb_r.mid
            grid_level = int(abs(price - mid) / (self.atr_multiplier * atr_val)) if atr_val > 0 else 0

        fee_est = quantity * price * 0.00075
        usdt_bal = self._get_usdt_balance()
        btc_bal = self._get_btc_balance()
        equity = self._estimate_equity(price)
        exposure_pct = self.position_guard.btc_exposure_pct(btc_bal, price)

        self.event_log.log("trade_filled",
            side=side,
            price=round(price, 2),
            quantity=quantity,
            grid_level=grid_level,
            fee_estimate=round(fee_est, 4),
            rsi=round(rsi_val, 2),
            bb_upper=round(bb_upper, 2),
            bb_lower=round(bb_lower, 2),
            ema_200=round(ema_val, 2),
            atr=round(atr_val, 2),
            usdt_balance=round(usdt_bal, 2),
            btc_balance=round(btc_bal, 8),
            equity=round(equity, 2),
        )

        if side == "BUY":
            self._open_buys[grid_level] = FillRecord(
                order_id=str(order.client_order_id),
                side=side,
                price=price,
                quantity=quantity,
                grid_level=grid_level,
                timestamp=time_mod.time(),
                rsi=rsi_val,
                bb_upper=bb_upper,
                bb_lower=bb_lower,
                ema_200=ema_val,
                atr=atr_val,
                grid_state=grid_state_val,
            )
            trade = Trade(
                timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                pair="BTC/USDT",
                side="BUY",
                entry_price=price,
                exit_price=price,
                quantity=quantity,
                gross_pnl=0.0,
                fee=fee_est,
                net_pnl=-fee_est,
                grid_level=grid_level,
                duration_min=0,
                rsi=rsi_val,
                bb_upper=bb_upper,
                bb_lower=bb_lower,
                ema_200=ema_val,
                atr=atr_val,
                grid_state=grid_state_val,
            )
            self.journal.log_trade(trade)
            logger.info(f"BUY filled: {quantity} BTC @ ${price:,.2f} | Level {grid_level}")

        elif side == "SELL":
            gross_pnl = 0.0
            fee = fee_est
            duration_min = 0
            entry_price = price
            entry_rsi = rsi_val
            entry_bb_upper = bb_upper
            entry_bb_lower = bb_lower
            entry_ema = ema_val
            entry_atr = atr_val

            matching_buy = self._open_buys.pop(grid_level, None)
            if matching_buy:
                entry_price = matching_buy.price
                entry_rsi = matching_buy.rsi
                entry_bb_upper = matching_buy.bb_upper
                entry_bb_lower = matching_buy.bb_lower
                entry_ema = matching_buy.ema_200
                entry_atr = matching_buy.atr
                gross_pnl = (price - entry_price) * quantity
                duration_min = int((time_mod.time() - matching_buy.timestamp) / 60)
            else:
                if self._open_buys:
                    level, buy = next(iter(self._open_buys.items()))
                    self._open_buys.pop(level)
                    entry_price = buy.price
                    entry_rsi = buy.rsi
                    entry_bb_upper = buy.bb_upper
                    entry_bb_lower = buy.bb_lower
                    entry_ema = buy.ema_200
                    entry_atr = buy.atr
                    gross_pnl = (price - entry_price) * quantity
                    duration_min = int((time_mod.time() - buy.timestamp) / 60)

            net_pnl = gross_pnl - fee

            self.event_log.log("round_trip_closed",
                entry_price=round(entry_price, 2),
                exit_price=round(price, 2),
                quantity=quantity,
                gross_pnl=round(gross_pnl, 4),
                fee=round(fee, 4),
                net_pnl=round(net_pnl, 4),
                duration_min=duration_min,
                grid_level=grid_level,
                entry_rsi=round(entry_rsi, 2),
                exit_rsi=round(rsi_val, 2),
                entry_bb_lower=round(entry_bb_lower, 2),
                entry_bb_upper=round(entry_bb_upper, 2),
                exit_bb_lower=round(bb_lower, 2),
                exit_bb_upper=round(bb_upper, 2),
                entry_ema=round(entry_ema, 2),
                exit_ema=round(ema_val, 2),
                entry_atr=round(entry_atr, 2),
                exit_atr=round(atr_val, 2),
                equity=round(equity, 2),
                exposure_pct=round(exposure_pct, 1),
            )

            trade = Trade(
                timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                pair="BTC/USDT",
                side="SELL",
                entry_price=entry_price,
                exit_price=price,
                quantity=quantity,
                gross_pnl=round(gross_pnl, 4),
                fee=round(fee, 4),
                net_pnl=round(net_pnl, 4),
                grid_level=grid_level,
                duration_min=duration_min,
                rsi=rsi_val,
                bb_upper=bb_upper,
                bb_lower=bb_lower,
                ema_200=ema_val,
                atr=atr_val,
                grid_state=grid_state_val,
            )
            self.journal.log_trade(trade)

            # Send rich Telegram alert on round-trip close
            spacing = atr_val * self.atr_multiplier if atr_val else 0
            pending = self.order_tracker.total_pending
            pnl_sign = "+" if net_pnl >= 0 else ""
            side_em = "📈 BUY" if trade.side == "BUY" else "📉 SELL"

            telegram_msg = (
                f"{'💚' if net_pnl >= 0 else '🔴'} <b>Trade Closed — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{side_em}  |  Grid Level {grid_level}\n"
                f"⏱ Duration:    {duration_min} min\n"
                f"🔵 Entry:      ${entry_price:,.2f}\n"
                f"🔵 Exit:       ${price:,.2f}\n"
                f"📦 Qty:        {quantity} BTC\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Gross PnL:  {pnl_sign}${gross_pnl:.2f}\n"
                f"💸 Fee:        -${fee:.2f}\n"
                f"<b>📊 Net PnL:   {pnl_sign}${net_pnl:.2f}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📉 RSI: {entry_rsi:.1f} → {rsi_val:.1f}\n"
                f"📐 BB: ${entry_bb_lower:,.0f} → ${bb_upper:,.0f}\n"
                f"📏 ATR: ${atr_val:,.0f}  |  Spacing: ${spacing:,.0f}\n"
                f"🏦 Equity: ${equity:,.2f}  |  Exposure: {exposure_pct:.0f}%\n"
                f"Grid: {grid_state_val}  |  Pending: {pending} orders\n"
                f"🌐 Mode: {self.env.upper()}"
            )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(telegram_msg))
            except RuntimeError:
                pass

            logger.info(
                f"SELL filled: {quantity} BTC @ ${price:,.2f} | "
                f"PnL: ${net_pnl:+.2f} | Level {grid_level}"
            )

    # ── Graceful Shutdown ────────────────────────────────────────────

    def on_stop(self):
        self._cancel_all_orders("graceful_shutdown")
        self.event_log.log("bot_stopped", reason="graceful stop")
        self.event_log.close()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_shutdown("graceful stop"))
        except RuntimeError:
            pass
        logger.info("Grid bot stopped — all orders cancelled")
