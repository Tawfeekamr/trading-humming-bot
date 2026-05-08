"""
ta_grid_btcusdt.py — TA-Enhanced BTC/USDT Grid Bot
Hummingbot v2 StrategyV2Base implementation.

Start: start --script ta_grid_btcusdt.py
"""

import os
import asyncio
import logging
import threading
import traceback as traceback_mod
from decimal import Decimal
from dotenv import load_dotenv
from pathlib import Path

# Setup logging FIRST so all startup messages go to files
from src.logging_config import setup_logging
setup_logging()
_logger = logging.getLogger("startup")

# Load .env from known locations (mounted at /home/hummingbot/.env in Docker)
_dotenv_loaded = False
for _env_path in [Path("/home/hummingbot/.env"), Path(__file__).parent.parent / ".env", Path(".env")]:
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
        _dotenv_loaded = True
        _logger.info(f"Loaded .env from {_env_path}")
        break
if not _dotenv_loaded:
    _logger.warning("No .env file found — Telegram and secrets unavailable")

# Log env status
_tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
_logger.info(f"ENV={os.environ.get('ENV', 'NOT SET')} "
             f"TELEGRAM_TOKEN={'SET' if _tg_token else 'NOT SET'} "
             f"TELEGRAM_CHAT_ID={'SET' if _tg_chat else 'NOT SET'}")

# Global uncaught exception handler — logs + Telegram alert
def _global_exception_handler(exc_type, exc_value, exc_tb):
    tb_str = "".join(traceback_mod.format_exception(exc_type, exc_value, exc_tb))
    logging.critical(f"UNCAUGHT EXCEPTION: {exc_value}\n{tb_str}")
    # Attempt Telegram alert
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            import urllib.request
            msg = f"🚨 <b>UNCAUGHT EXCEPTION</b>\n{exc_type.__name__}: {exc_value}\n\n<pre>{tb_str[:600]}</pre>"
            url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&parse_mode=HTML&text="
            urllib.request.urlopen(url + urllib.request.quote(msg), timeout=5)
    except Exception:
        pass

import sys
sys.excepthook = _global_exception_handler
from typing import Dict, Optional
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
from src.notifications.telegram_commands import TelegramCommandHandler
from src.journal.trade_journal import TradeJournal, Trade
from src.health import update_health, set_halted, start_health_server
from src.logging_config import setup_logging
from src.logging.event_logger import EventLogger
from src.monitoring.system_monitor import SystemAlertMonitor

try:
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
    from hummingbot.connector.connector_base import ConnectorBase
    from hummingbot.core.data_type.common import MarketDict, OrderType, PriceType, TradeType
    from hummingbot.core.data_type.order_candidate import OrderCandidate
    from pydantic import Field
    V2_AVAILABLE = True
except ImportError:
    # Fallback to v1 for local testing
    V2_AVAILABLE = False
    StrategyV2Base = object
    StrategyV2ConfigBase = object
    ConnectorBase = object
    OrderType = type("OrderType", (), {"LIMIT": "LIMIT"})
    TradeType = type("TradeType", (), {"BUY": "BUY", "SELL": "SELL"})
    MarketDict = dict
    Field = lambda **kwargs: kwargs

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


class TAGridConfig(StrategyV2ConfigBase):
    """
    Configuration class for TA-Enhanced Grid Bot strategy.
    Extends StrategyV2ConfigBase with strategy-specific parameters.
    """

    # Required by StrategyV2ConfigBase
    script_file_name: str = Field(default="ta_grid_btcusdt.py")

    # Exchange configuration
    exchange: str = Field(default="binance_paper_trade")
    trading_pair: str = Field(default="BTC-USDT")

    # Grid parameters
    levels: int = Field(default=8)
    capital_usdt: float = Field(default=200.0)
    min_reserve: float = Field(default=50.0)
    order_refresh_time: int = Field(default=60)

    # Bollinger Bands
    bb_period: int = Field(default=20)
    bb_std: float = Field(default=2.0)

    # RSI
    rsi_period: int = Field(default=14)
    rsi_overbought: float = Field(default=70.0)
    rsi_oversold: float = Field(default=35.0)

    # EMA
    ema_period: int = Field(default=200)

    # ATR
    atr_period: int = Field(default=14)
    atr_multiplier: float = Field(default=0.8)

    # Risk management
    max_drawdown_pct: float = Field(default=10.0)
    daily_loss_limit_pct: float = Field(default=5.0)
    max_btc_exposure_pct: float = Field(default=80.0)

    # Environment
    env: str = Field(default="paper")

    def update_markets(self, markets: MarketDict) -> MarketDict:
        """
        Register the trading pairs for this strategy.
        Called by Hummingbot v2 to configure which markets to connect to.
        """
        markets[self.exchange] = {self.trading_pair: {}}
        return markets


class TAGridBTCUSDT(StrategyV2Base):
    """
    TA-Enhanced Grid Bot strategy for Hummingbot v2.
    Uses Bollinger Bands, RSI, EMA 200, and ATR to dynamically
    manage a grid of buy/sell orders on BTC/USDT.
    """

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

    def __init__(self, connectors: Dict[str, ConnectorBase], config: TAGridConfig):
        """
        Initialize the strategy with connectors and configuration.

        Args:
            connectors: Dictionary of exchange connector names to connector instances
            config: Strategy configuration instance
        """
        setup_logging()

        # Store config
        self.config = config

        # Load additional config from YAML if available
        cfg = self._load_config()
        grid_cfg = cfg.get("grid", {})
        ind_cfg = cfg.get("indicators", {})
        risk_cfg = cfg.get("risk", {})

        # Environment variables override config
        self.levels = int(os.environ.get("GRID_LEVELS", grid_cfg.get("levels", config.levels)))
        self.capital_usdt = float(os.environ.get("GRID_CAPITAL_USDT", grid_cfg.get("capital_usdt", config.capital_usdt)))
        self.min_reserve = float(os.environ.get("MIN_USDT_RESERVE", grid_cfg.get("min_usdt_reserve", config.min_reserve)))
        self.order_refresh_time = grid_cfg.get("order_refresh_time", config.order_refresh_time)

        # Indicator config
        bb_cfg = ind_cfg.get("bollinger", {})
        self.bb_period = bb_cfg.get("period", config.bb_period)
        self.bb_std = bb_cfg.get("std_dev", config.bb_std)
        rsi_cfg = ind_cfg.get("rsi", {})
        self.rsi_period = rsi_cfg.get("period", config.rsi_period)
        self.rsi_overbought = rsi_cfg.get("overbought", config.rsi_overbought)
        self.rsi_oversold = rsi_cfg.get("oversold", config.rsi_oversold)
        ema_cfg = ind_cfg.get("ema", {})
        self.ema_period = ema_cfg.get("period", config.ema_period)
        atr_cfg = ind_cfg.get("atr", {})
        self.atr_period = atr_cfg.get("period", config.atr_period)
        self.atr_multiplier = atr_cfg.get("spacing_multiplier", config.atr_multiplier)

        # Risk config
        self.max_drawdown_pct = float(os.environ.get("MAX_DRAWDOWN_PCT", risk_cfg.get("max_drawdown_pct", config.max_drawdown_pct)))
        self.daily_loss_limit_pct = risk_cfg.get("daily_loss_limit_pct", config.daily_loss_limit_pct)
        self.max_btc_exposure_pct = float(os.environ.get("MAX_BTC_EXPOSURE_PCT", risk_cfg.get("max_btc_exposure_pct", config.max_btc_exposure_pct)))

        # Environment
        self.env = os.environ.get("ENV", config.env)
        self.is_testnet = self.env == "paper"

        # Exchange and trading pair from config
        self.exchange = config.exchange
        self.trading_pair = config.trading_pair

        # Call parent constructor (required for v2)
        super().__init__(connectors, config)

        start_health_server(port=8080)

        # Initialize indicators
        self.bb = BollingerBands(self.bb_period, self.bb_std)
        self.rsi = RSI(self.rsi_period)
        self.ema = EMA(self.ema_period)
        self.atr = ATR(self.atr_period, self.atr_multiplier)

        # Initialize grid and risk management components
        self.grid_manager = GridManager(
            levels=self.levels,
            capital_usdt=self.capital_usdt,
            min_reserve=self.min_reserve,
            spacing_multiplier=self.atr_multiplier,
        )
        self.state_machine = GridStateMachine()
        self._grid_order_tracker = OrderTracker()
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

        # Internal state
        self._peak_equity = self.capital_usdt
        self._open_buys: dict[int, FillRecord] = {}
        self._last_candle_time = None
        self._cached_indicators = None
        self._grid_dirty = True
        self._last_state_alert_time: dict[str, float] = {}
        self._state_alert_cooldown = 900  # 15 minutes between repeated state alerts
        self._manual_pause = False
        self._last_sod_reset: Optional[str] = None
        self._fee_rate: float = 0.00075  # default 0.075% standard tier
        self._overtrading_alerted_today: str = ""
        self._state_lock = threading.Lock()  # protects _manual_pause, _cached_indicators, _open_buys
        self._tick_count = 0
        self._last_grid_place_time = 0  # timestamp of last grid placement
        self._min_grid_refresh_sec = 300  # 5 min minimum between grid refreshes

        self.event_log.log("bot_started", mode=self.env, capital=self.capital_usdt,
                           levels=self.levels, testnet=self.is_testnet)

        # Log full configuration for remote debugging
        logger.info(
            f"Bot config: env={self.env} exchange={self.exchange} pair={self.trading_pair} "
            f"levels={self.levels} capital=${self.capital_usdt} reserve=${self.min_reserve} "
            f"bb={self.bb_period}/{self.bb_std} rsi={self.rsi_period} "
            f"rsi_ob={self.rsi_overbought} rsi_os={self.rsi_oversold} "
            f"ema={self.ema_period} atr={self.atr_period}*{self.atr_multiplier} "
            f"max_dd={self.max_drawdown_pct}% daily_loss={self.daily_loss_limit_pct}% "
            f"max_exposure={self.max_btc_exposure_pct}% fee={self._fee_rate*100:.4f}%"
        )
        logger.info(
            f"Components: telegram_token={'SET' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'NOT SET'} "
            f"telegram_chat={'SET' if os.environ.get('TELEGRAM_CHAT_ID') else 'NOT SET'} "
            f"journal={type(self.journal).__name__} event_log={type(self.event_log).__name__}"
        )

        # Fee tier auto-detection
        try:
            fee_info = self.candle_feed.client.get_trade_fee(symbol="BTCUSDT")
            if fee_info and len(fee_info) > 0:
                maker_fee = float(fee_info[0].get("makerCommission", 0.00075))
                if maker_fee > 0:
                    self._fee_rate = maker_fee
            logger.info(f"Fee rate: {self._fee_rate * 100:.4f}%")
            self.event_log.log("fee_detected", rate=self._fee_rate)
        except Exception as e:
            logger.info(f"Fee detection skipped, using default 0.075%: {e}")

        # Start Telegram command handler
        self._telegram_commands = TelegramCommandHandler(
            journal=self.journal,
            state_machine=self.state_machine,
            circuit_breaker=self.circuit_breaker,
            position_guard=self.position_guard,
            event_logger=self.event_log,
            strategy=self,
        )
        self._telegram_commands.start()

        # Start system resource monitor (alerts at 75% CPU/RAM/Disk)
        self._sys_monitor = SystemAlertMonitor(self.telegram, interval_sec=300)
        self._sys_monitor.start()
        logger.info("System resource monitor started")

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_startup(self.env, self.capital_usdt))
        except RuntimeError:
            pass

    # ── Main Tick Loop ───────────────────────────────────────────────
    # NOTE: Do NOT override tick() — Cython dispatch (StrategyPyBase.c_tick)
    # bypasses Python-level overrides and calls StrategyV2Base.tick() directly.
    # All logic goes in on_tick() which IS properly dispatched via self.on_tick().

    def on_tick(self):
        try:
            self._tick_count += 1
            if self._tick_count <= 5 or self._tick_count % 300 == 0:
                connectors_ready = {name: c.ready for name, c in self.connectors.items()}
                logger.info(f"on_tick #{self._tick_count}: connectors_ready={connectors_ready}")
            self._on_tick_inner()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"CRASH in on_tick: {e}\n{tb}")
            self._safe_telegram_crash("on_tick", str(e), tb)

    def _on_tick_inner(self):
        if self.circuit_breaker.halted:
            return

        if self._manual_pause:
            if self._grid_dirty:
                self._cancel_all_orders("manual_pause")
                self._grid_dirty = False
            return

        now = pd.Timestamp.now(tz="UTC")

        # Start-of-day equity reset
        today_str = now.strftime("%Y-%m-%d")
        if self._last_sod_reset != today_str:
            equity = self._estimate_equity(
                self._cached_indicators[4] if self._cached_indicators else 0
            )
            self.circuit_breaker.set_start_of_day_equity(equity)
            self._last_sod_reset = today_str
            self.event_log.log("daily_reset", equity=round(equity, 2))
            logger.info(f"Start-of-day equity reset: ${equity:.2f}")

        # Overtrading check (once per day)
        if self._overtrading_alerted_today != today_str:
            try:
                ot = self.journal.is_overtrading(threshold=0.30)
                if ot["is_overtrading"]:
                    self._overtrading_alerted_today = today_str
                    self.event_log.log("overtrading_detected", **ot)
                    ot_msg = (
                        f"⚠️ <b>OVERTRADING DETECTED</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💸 Fees today: ${ot['total_fees']:.2f}\n"
                        f"📊 Gross activity: ${ot['abs_gross_pnl']:.2f}\n"
                        f"📈 Fee ratio: {ot['fee_to_gross_ratio']:.1%} (threshold: {ot['threshold']:.0%})\n"
                        f"⚠️ Fees are eating a large portion of your P&L.\n"
                        f"Consider widening grid spacing or reducing levels."
                    )
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(self.telegram.send(ot_msg))
                    except RuntimeError:
                        pass
            except Exception as e:
                logger.error(f"Overtrading check failed: {e}")

        should_fetch = (
            self._last_candle_time is None or
            now - self._last_candle_time >= pd.Timedelta(minutes=55)
        )

        if should_fetch:
            try:
                df = self.candle_feed.fetch_candles(limit=250)
            except Exception as e:
                logger.error(f"Candle fetch failed: {e}")
                self._safe_telegram_error("candle_fetch", str(e))
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
            now_ts = time_mod.time()
            elapsed = now_ts - self._last_grid_place_time
            if elapsed < self._min_grid_refresh_sec:
                return  # Too soon — let existing orders work
            grid = self.grid_manager.calculate_grid(bb_result, atr_value)
            self._place_grid_orders(grid, current_price)
            self._last_grid_place_time = now_ts

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
            logger.warning(
                f"No connector for exchange '{self.exchange}' — "
                f"available: {list(self.connectors.keys())}"
            )
            return

        usdt_bal = self._get_usdt_balance()
        btc_bal = self._get_btc_balance()
        equity = self._estimate_equity(current_price)
        exposure_pct = self.position_guard.btc_exposure_pct(btc_bal, current_price, equity)

        logger.info(
            f"Placing grid: {len(grid.buy_levels)} buy / {len(grid.sell_levels)} sell levels | "
            f"price=${current_price:,.0f} usdt=${usdt_bal:.2f} btc={btc_bal:.8f} "
            f"equity=${equity:.2f} exposure={exposure_pct:.0f}%"
        )

        buys_placed = 0
        sells_placed = 0
        buys_skipped_price = 0
        buys_blocked = 0

        for level in grid.buy_levels:
            if level["price"] >= current_price:
                buys_skipped_price += 1
                continue
            order_usdt = level["price"] * level["quantity"]
            if not self.position_guard.can_place_order(
                current_btc=btc_bal,
                btc_price=current_price,
                current_usdt=usdt_bal,
                order_usdt=order_usdt,
                equity=equity,
            ):
                buys_blocked += 1
                logger.debug(
                    f"BUY blocked: level={level['level']} price=${level['price']:,.0f} "
                    f"usdt=${usdt_bal:.2f} need=${order_usdt:.2f} reserve=${self.min_reserve}"
                )
                continue
            buys_placed += 1
            self.buy(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                amount=Decimal(str(level["quantity"])),
                order_type=OrderType.LIMIT,
                price=Decimal(str(level["price"])),
            )

        sells_skipped_price = 0
        sells_blocked = 0
        for level in grid.sell_levels:
            if level["price"] <= current_price:
                sells_skipped_price += 1
                continue
            btc_balance = self._get_btc_balance()
            if level["quantity"] > btc_balance:
                sells_blocked += 1
                continue
            sells_placed += 1
            self.sell(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                amount=Decimal(str(level["quantity"])),
                order_type=OrderType.LIMIT,
                price=Decimal(str(level["price"])),
            )

        logger.info(
            f"Grid placement: buys={buys_placed} placed / {buys_skipped_price} above price / {buys_blocked} blocked | "
            f"sells={sells_placed} placed / {sells_skipped_price} below price / {sells_blocked} blocked"
        )

    def _cancel_all_orders(self, reason: str = "grid_refresh"):
        try:
            active = self.get_active_orders(self.exchange)
        except Exception:
            active = []
        for order in active:
            self.event_log.log("order_cancelled",
                order_id=str(order.client_order_id),
                side="BUY" if order.is_buy else "SELL",
                price=float(order.price),
                reason=reason,
            )
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)

    # ── Balance Helpers ──────────────────────────────────────────────

    def _get_usdt_balance(self) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)("USDT")
        if balance is None:
            return 0.0
        return float(balance.available if hasattr(balance, 'available') else balance)

    def _get_btc_balance(self) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)("BTC")
        if balance is None:
            return 0.0
        return float(balance.available if hasattr(balance, 'available') else balance)

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
        try:
            self._did_fill_order_inner(event)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"CRASH in did_fill_order: {e}\n{tb}")
            self._safe_telegram_crash("did_fill_order", str(e), tb)

    def _did_fill_order_inner(self, event):
        # Hummingbot v2 OrderFilledEvent: attributes are directly on event, not event.order
        trade_type = getattr(event, 'trade_type', None)
        side = "BUY" if trade_type is not None and trade_type.name == "BUY" else "SELL"
        price = float(getattr(event, 'price', 0))
        quantity = float(getattr(event, 'amount', 0))
        order_id = str(getattr(event, 'order_id', getattr(event, 'client_order_id', '')))

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

        fee_est = quantity * price * self._fee_rate
        usdt_bal = self._get_usdt_balance()
        btc_bal = self._get_btc_balance()
        equity = self._estimate_equity(price)
        exposure_pct = self.position_guard.btc_exposure_pct(btc_bal, price, equity)

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
                order_id=order_id,
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
            self._grid_dirty = True  # Refresh grid after fill

            # Telegram notification for BUY fills
            spacing = atr_val * self.atr_multiplier if atr_val else 0
            buy_msg = (
                f"📈 <b>BUY Filled — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📦 Qty: {quantity} BTC\n"
                f"📊 Level {grid_level}  |  RSI: {rsi_val:.1f}\n"
                f"💸 Fee: -${fee_est:.2f}\n"
                f"🏦 Equity: ${equity:,.2f}  |  Exposure: {exposure_pct:.0f}%\n"
                f"🌐 Mode: {self.env.upper()}"
            )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(buy_msg))
            except RuntimeError:
                pass

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
            pending = self._grid_order_tracker.total_pending
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
            self._grid_dirty = True  # Refresh grid after fill

    # ── Safe Telegram Error Helpers ────────────────────────────────────

    # Thread-safe accessors for Telegram commands
    @property
    def manual_pause(self) -> bool:
        with self._state_lock:
            return self._manual_pause

    @manual_pause.setter
    def manual_pause(self, value: bool) -> None:
        with self._state_lock:
            self._manual_pause = value

    def get_indicators_snapshot(self):
        with self._state_lock:
            return self._cached_indicators

    @property
    def order_tracker(self):
        return self._grid_order_tracker

    def _safe_telegram_error(self, source: str, error: str, details: str = ""):
        self.event_log.log("error", source=source, error=error, details=details[:300])
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_error(source, error, details))
        except Exception:
            pass

    def _safe_telegram_crash(self, source: str, error: str, traceback_str: str = ""):
        self.event_log.log("crash", source=source, error=error, traceback=traceback_str[:500])
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_crash(source, error, traceback_str))
        except Exception:
            pass

    # ── Graceful Shutdown ────────────────────────────────────────────

    def on_stop(self):
        super().on_stop()
        if hasattr(self, "_sys_monitor"):
            self._sys_monitor.stop()
        if hasattr(self, "_telegram_commands"):
            self._telegram_commands.stop()
        try:
            self._cancel_all_orders("graceful_shutdown")
        except Exception as e:
            logger.error(f"Error cancelling orders on stop: {e}")
        self.event_log.log("bot_stopped", reason="graceful stop")
        self.event_log.close()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_shutdown("graceful stop"))
        except RuntimeError:
            pass
        logger.info("Grid bot stopped — all orders cancelled")
