"""
ta_grid_btcusdt.py — TA-Enhanced SOL/USDT Grid Bot
Hummingbot v2 StrategyV2Base implementation.

Start: start --script ta_grid_btcusdt.py
"""

import os
import asyncio
import logging
import threading
import json
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
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from typing import Dict, Optional
from dataclasses import dataclass as dataclass_fills
from typing import Optional as Optional_fills
import time as time_mod

import pandas as pd
import yaml

from src.indicators.bollinger import BollingerBands
from src.indicators.rsi import RSI
from src.indicators.ema import EMA
from src.indicators.atr import ATR
from src.grid.grid_manager import GridManager
from src.grid.grid_state import GridStateMachine, GridState
from src.grid.order_tracker import OrderTracker, GridOrder, OrderSide
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
    fee: float = 0.0


class TAGridConfig(StrategyV2ConfigBase):
    """
    Configuration class for TA-Enhanced Grid Bot strategy.
    Extends StrategyV2ConfigBase with strategy-specific parameters.
    """

    # Required by StrategyV2ConfigBase
    script_file_name: str = Field(default="ta_grid_btcusdt.py")

    # Exchange configuration
    exchange: str = Field(default="binance_paper_trade")
    trading_pair: str = Field(default="SOL-USDT")

    # Grid parameters
    levels: int = Field(default=8)
    capital_usdt: float = Field(default=200.0)
    min_reserve: float = Field(default=50.0)
    order_refresh_time: int = Field(default=60)
    step_size: float = Field(default=0.01)

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
    max_base_exposure_pct: float = Field(default=80.0)

    # Environment
    env: str = Field(default="paper")

    def update_markets(self, markets: MarketDict) -> MarketDict:
        """
        Register the trading pairs for this strategy.
        Called by Hummingbot v2 to configure which markets to connect to.
        """
        markets[self.exchange] = {self.trading_pair: {}}
        return markets


class TAGridSOLUSDT(StrategyV2Base):
    """
    TA-Enhanced Grid Bot strategy for Hummingbot v2.
    Uses Bollinger Bands, RSI, EMA 200, and ATR to dynamically
    manage a grid of buy/sell orders on the configured pair.
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
        self.step_size = float(grid_cfg.get("step_size", config.step_size))

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
        self.max_base_exposure_pct = float(os.environ.get("MAX_BASE_EXPOSURE_PCT", risk_cfg.get("max_base_exposure_pct", config.max_base_exposure_pct)))

        # Environment
        self.env = os.environ.get("ENV", config.env)
        self.is_testnet = self.env == "paper"

        # Exchange and trading pair from config
        self.exchange = config.exchange
        self.trading_pair = config.trading_pair
        self.base_asset = self.trading_pair.split("-")[0]  # e.g. "SOL"
        self.binance_symbol = self.trading_pair.replace("-", "")  # e.g. "SOLUSDT"
        self.display_pair = self.trading_pair.replace("-", "/")  # e.g. "SOL/USDT"

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
            step_size=self.step_size,
            spacing_multiplier=self.atr_multiplier,
        )
        self._base_capital = self.capital_usdt  # floor for auto-compound
        self._initial_equity = None  # captured on first grid placement
        self.state_machine = GridStateMachine()
        self._grid_order_tracker = OrderTracker()
        self.circuit_breaker = CircuitBreaker(self.max_drawdown_pct, self.daily_loss_limit_pct)
        self.circuit_breaker.set_peak_equity(self.capital_usdt)
        self.circuit_breaker.set_start_of_day_equity(self.capital_usdt)
        self.position_guard = PositionGuard(
            self.max_base_exposure_pct, self.min_reserve, self.capital_usdt
        )
        self.candle_feed = CandleFeed(
            symbol=self.binance_symbol,
            interval="1h",
            testnet=self.is_testnet,
        )
        self.telegram = TelegramBot()
        self.journal = TradeJournal()
        self.event_log = EventLogger(log_dir="logs")

        # Internal state
        self._peak_equity = self.capital_usdt
        self._open_buys: dict[str, FillRecord] = {}
        self._unmatched_sells: dict[str, FillRecord] = {}  # sells waiting for a buy to match
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
        self._active_buy_spacing = 0.0
        self._active_sell_spacing = 0.0

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
            f"max_exposure={self.max_base_exposure_pct}% fee={self._fee_rate*100:.4f}%"
        )
        logger.info(
            f"Components: telegram_token={'SET' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'NOT SET'} "
            f"telegram_chat={'SET' if os.environ.get('TELEGRAM_CHAT_ID') else 'NOT SET'} "
            f"journal={type(self.journal).__name__} event_log={type(self.event_log).__name__}"
        )

        # Fee tier auto-detection
        try:
            fee_info = self.candle_feed.client.get_trade_fee(symbol=self.binance_symbol)
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

        # Load persisted state (open BUYs)
        self._state_file = Path("data/grid_state.json")
        self._load_state()

        # Start force-ready watchdog (bypasses connector freeze after 30s)
        threading.Thread(target=self._force_connector_ready, daemon=True).start()

    def _save_state(self):
        """Persist open positions to a JSON file (atomic write)."""
        try:
            if not self._state_file.parent.exists():
                self._state_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "last_sod_reset": self._last_sod_reset,
                "open_buys": {
                    order_id: {
                        "order_id": fill.order_id,
                        "side": fill.side,
                        "price": fill.price,
                        "quantity": fill.quantity,
                        "grid_level": fill.grid_level,
                        "timestamp": fill.timestamp,
                        "rsi": fill.rsi,
                        "bb_upper": fill.bb_upper,
                        "bb_lower": fill.bb_lower,
                        "ema_200": fill.ema_200,
                        "atr": fill.atr,
                        "grid_state": fill.grid_state,
                        "fee": fill.fee,
                    } for order_id, fill in self._open_buys.items()
                },
                "unmatched_sells": {
                    order_id: {
                        "order_id": fill.order_id,
                        "side": fill.side,
                        "price": fill.price,
                        "quantity": fill.quantity,
                        "grid_level": fill.grid_level,
                        "timestamp": fill.timestamp,
                        "rsi": fill.rsi,
                        "bb_upper": fill.bb_upper,
                        "bb_lower": fill.bb_lower,
                        "ema_200": fill.ema_200,
                        "atr": fill.atr,
                        "grid_state": fill.grid_state,
                        "fee": fill.fee,
                    } for order_id, fill in self._unmatched_sells.items()
                }
            }
            tmp = self._state_file.with_suffix('.tmp')
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._state_file)
            logger.debug(f"State saved: {len(self._open_buys)} open buys, {len(self._unmatched_sells)} unmatched sells")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """Restore open positions from JSON file."""
        try:
            if self._state_file.exists():
                with open(self._state_file, "r") as f:
                    data = json.load(f)
                    self._last_sod_reset = data.get("last_sod_reset", "")
                    raw_buys = data.get("open_buys", {})
                    for order_id, d in raw_buys.items():
                        self._open_buys[order_id] = FillRecord(
                            order_id=d["order_id"],
                            side=d["side"],
                            price=d["price"],
                            quantity=d["quantity"],
                            grid_level=d["grid_level"],
                            timestamp=d["timestamp"],
                            rsi=d["rsi"],
                            bb_upper=d["bb_upper"],
                            bb_lower=d["bb_lower"],
                            ema_200=d["ema_200"],
                            atr=d["atr"],
                            grid_state=d["grid_state"],
                            fee=d.get("fee", 0.0),
                        )
                    raw_unmatched = data.get("unmatched_sells", {})
                    for order_id, d in raw_unmatched.items():
                        self._unmatched_sells[order_id] = FillRecord(
                            order_id=d["order_id"],
                            side=d["side"],
                            price=d["price"],
                            quantity=d["quantity"],
                            grid_level=d["grid_level"],
                            timestamp=d["timestamp"],
                            rsi=d["rsi"],
                            bb_upper=d["bb_upper"],
                            bb_lower=d["bb_lower"],
                            ema_200=d["ema_200"],
                            atr=d["atr"],
                            grid_state=d["grid_state"],
                            fee=d.get("fee", 0.0),
                        )
                logger.info(f"Restored {len(self._open_buys)} open buys, {len(self._unmatched_sells)} unmatched sells from {self._state_file}")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _cleanup_orphans(self):
        """Evict orphaned entries older than 7 days."""
        now = time_mod.time()
        ttl = 86400 * 7
        changed = False

        for oid in [k for k, f in self._unmatched_sells.items() if (now - f.timestamp) > ttl]:
            fill = self._unmatched_sells.pop(oid)
            logger.warning(f"Orphaned unmatched sell evicted: {fill.order_id} price=${fill.price:,.2f} qty={fill.quantity} age={int((now - fill.timestamp) / 3600)}h")
            self.event_log.log("orphan_evicted", side="SELL", order_id=fill.order_id, price=fill.price, quantity=fill.quantity, age_hours=int((now - fill.timestamp) / 3600))
            trade = Trade(
                timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                pair=self.display_pair, side="SELL", entry_price=fill.price, exit_price=fill.price,
                quantity=fill.quantity, gross_pnl=0.0, fee=fill.fee, net_pnl=-fill.fee,
                grid_level=fill.grid_level, duration_min=int((now - fill.timestamp) / 60),
                rsi=fill.rsi, bb_upper=fill.bb_upper, bb_lower=fill.bb_lower,
                ema_200=fill.ema_200, atr=fill.atr, grid_state=fill.grid_state,
            )
            self.journal.log_trade(trade)
            changed = True

        for oid in [k for k, f in self._open_buys.items() if (now - f.timestamp) > ttl]:
            fill = self._open_buys.pop(oid)
            logger.warning(f"Orphaned open buy evicted: {fill.order_id} price=${fill.price:,.2f} qty={fill.quantity} age={int((now - fill.timestamp) / 3600)}h")
            self.event_log.log("orphan_evicted", side="BUY", order_id=fill.order_id, price=fill.price, quantity=fill.quantity, age_hours=int((now - fill.timestamp) / 3600))
            changed = True

        if changed:
            self._save_state()

    # ── Main Tick Loop ───────────────────────────────────────────────
    # NOTE: Do NOT override tick() — Cython dispatch (StrategyPyBase.c_tick)
    # bypasses Python-level overrides and calls StrategyV2Base.tick() directly.
    # All logic goes in on_tick() which IS properly dispatched via self.on_tick().

    def _force_connector_ready(self):
        """Force ready_to_trade after timeout to bypass Hummingbot connector freeze."""
        try:
            time_mod.sleep(30)
            if self._tick_count == 0:
                self.ready_to_trade = True
        except Exception:
            pass

    def on_tick(self):
        try:
            self._tick_count += 1
            if self._tick_count <= 3:
                for name, conn in self.connectors.items():
                    logger.info(f"on_tick #{self._tick_count}: connector={name} ready={getattr(conn, 'ready', 'N/A')}")
            if self._tick_count <= 5 or self._tick_count % 300 == 0:
                connectors_ready = {name: c.ready for name, c in self.connectors.items()}
                logger.info(f"on_tick #{self._tick_count}: connectors_ready={connectors_ready}")
                if self._tick_count % 300 == 0:
                    self._cleanup_orphans()
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

        # Start-of-day equity reset + daily report
        today_str = now.strftime("%Y-%m-%d")
        if self._last_sod_reset != today_str:
            equity = self._estimate_equity(
                self._cached_indicators[4] if self._cached_indicators else 0
            )
            self.circuit_breaker.set_start_of_day_equity(equity)
            self._last_sod_reset = today_str
            self.event_log.log("daily_reset", equity=round(equity, 2))
            logger.info(f"Start-of-day equity reset: ${equity:.2f}")
            self._send_daily_report(equity)

        # Overtrading check (once per day)
        if self._overtrading_alerted_today != today_str:
            try:
                ot = self.journal.is_overtrading(threshold=0.30)
                if ot["is_overtrading"]:
                    self._overtrading_alerted_today = today_str
                    self.event_log.log("overtrading_detected", **ot)
                    ot_msg = (
                        f"⚠️ <b>OVERTRADING DETECTED</b>\n"
                        f"•••\n"
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

            if df.empty or len(df) < self.bb_period:
                logger.warning("Insufficient candle data, skipping tick")
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

            # Use dummy spacing for state transition if grid not yet calculated
            self._notify_state_change(new_state, prev_state, trigger_reason, current_price, rsi_value, bb_result, ema_value, atr_value, self._active_buy_spacing)
            self._grid_dirty = True

        if self.state_machine.is_paused:
            if self._grid_dirty:
                self._cancel_all_orders("state_paused")
                self._grid_dirty = False
            return

        equity = self._estimate_equity(current_price)
        self.circuit_breaker.update_peak(equity)
        if self.circuit_breaker.check(equity) or self.circuit_breaker.check_daily(equity):
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
            # Auto-compound: scale base capital by equity growth ratio
            # This works in both paper ($180K starting) and live ($1K starting)
            live_equity = self._estimate_equity(current_price)
            if self._initial_equity is None:
                self._initial_equity = live_equity
            growth_ratio = live_equity / self._initial_equity if self._initial_equity > 0 else 1.0
            compound_capital = self._base_capital * growth_ratio
            compound_capital = max(compound_capital, self._base_capital)
            self.grid_manager.capital_usdt = compound_capital
            grid = self.grid_manager.calculate_grid(bb_result, atr_value)
            self._active_buy_spacing = grid.buy_spacing
            self._active_sell_spacing = grid.sell_spacing
            self._place_grid_orders(grid, current_price)
            self._last_grid_place_time = now_ts
            
            # Log exact grid info for transparency
            logger.info(
                f"Grid updated: buy_spacing=${grid.buy_spacing:.2f}, sell_spacing=${grid.sell_spacing:.2f} "
                f"| compound=${compound_capital:.2f} (base=${self._base_capital} growth={growth_ratio:.4f})"
            )

            deployed = sum(l["price"] * l["quantity"] for l in grid.buy_levels)
            self.event_log.log("grid_recalculated",
                bb_upper=round(bb_result.upper, 2),
                bb_lower=round(bb_result.lower, 2),
                buy_spacing=round(grid.buy_spacing, 2),
                sell_spacing=round(grid.sell_spacing, 2),
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
        if hasattr(connector, 'ready') and not connector.ready:
            logger.warning(f"Connector '{self.exchange}' not ready — skipping grid placement")
            return

        usdt_bal = self._get_usdt_balance()
        base_bal = self._get_base_balance()
        equity = self._estimate_equity(current_price)
        exposure_pct = self.position_guard.base_exposure_pct(base_bal, current_price, equity)

        logger.info(
            f"Placing grid: {len(grid.buy_levels)} buy / {len(grid.sell_levels)} sell levels | "
            f"price=${current_price:,.2f} usdt=${usdt_bal:.2f} {self.base_asset.lower()}={base_bal:.4f} "
            f"equity=${equity:.2f} exposure={exposure_pct:.0f}%"
        )

        buys_placed = 0
        sells_placed = 0
        buys_skipped_price = 0
        buys_blocked = 0
        buys_skipped_rsi = 0

        # Get current RSI for level filtering
        indicators = self._cached_indicators
        current_rsi = indicators[1] if indicators else None

        for level in grid.buy_levels:
            # Skip buys when RSI is high — market already overbought
            if current_rsi and current_rsi > 60:
                buys_skipped_rsi += 1
                continue
            if level["price"] >= current_price:
                buys_skipped_price += 1
                continue
            order_usdt = level["price"] * level["quantity"]
            if not self.position_guard.can_place_order(
                current_base=base_bal,
                base_price=current_price,
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
            client_order_id = self.buy(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                amount=Decimal(str(level["quantity"])),
                order_type=OrderType.LIMIT,
                price=Decimal(str(level["price"])),
            )
            if client_order_id:
                self._grid_order_tracker.add(GridOrder(
                    order_id=client_order_id,
                    level=level["level"],
                    side=OrderSide.BUY,
                    price=level["price"],
                    quantity=level["quantity"],
                ))

        sells_skipped_price = 0
        sells_blocked = 0
        sells_skipped_rsi = 0
        sells_skipped_no_position = 0

        # Only place as many sells as we have open buy positions to close.
        # Selling without a buy to match creates unmatched fills that accumulate forever.
        sells_remaining = len(self._open_buys)

        for level in grid.sell_levels:
            # Skip sells when RSI is low — market oversold, wait for bounce
            if current_rsi and current_rsi < 40:
                sells_skipped_rsi += 1
                continue
            if level["price"] <= current_price:
                sells_skipped_price += 1
                continue
            if sells_remaining <= 0:
                sells_skipped_no_position += 1
                continue
            base_balance = self._get_base_balance()
            if level["quantity"] > base_balance:
                sells_blocked += 1
                continue
            sells_remaining -= 1
            sells_placed += 1
            client_order_id = self.sell(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                amount=Decimal(str(level["quantity"])),
                order_type=OrderType.LIMIT,
                price=Decimal(str(level["price"])),
            )
            if client_order_id:
                self._grid_order_tracker.add(GridOrder(
                    order_id=client_order_id,
                    level=level["level"],
                    side=OrderSide.SELL,
                    price=level["price"],
                    quantity=level["quantity"],
                ))

        logger.info(
            f"Grid placement: buys={buys_placed} placed / {buys_skipped_price} above price / {buys_blocked} blocked / {buys_skipped_rsi} rsi_skip | "
            f"sells={sells_placed} placed / {sells_skipped_price} below price / {sells_blocked} blocked / {sells_skipped_rsi} rsi_skip / {sells_skipped_no_position} no_position | "
            f"open_buys={len(self._open_buys)} unmatched_sells={len(self._unmatched_sells)} | "
            + (f"rsi={current_rsi:.1f}" if current_rsi else f"rsi=N/A")
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
        self._grid_order_tracker.cancel_all()
        self._grid_order_tracker.clear_history()

    # ── Balance Helpers ──────────────────────────────────────────────

    def _get_usdt_balance(self) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)("USDT")
        if balance is None:
            return 0.0
        return float(balance.available if hasattr(balance, 'available') else balance)

    def _get_base_balance(self) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)(self.base_asset)
        if balance is None:
            return 0.0
        return float(balance.available if hasattr(balance, 'available') else balance)

    def _estimate_equity(self, base_price: float) -> float:
        return self._get_usdt_balance() + (self._get_base_balance() * base_price)

    def _send_daily_report(self, equity: float):
        """Send daily P&L summary to Telegram at midnight UTC."""
        try:
            s = self.journal.summary_today()
            sw = self.journal.summary_this_week()
            sm = self.journal.summary_this_month()

            def fmt(val):
                sign = "+" if (val or 0) >= 0 else ""
                return f"{sign}${val:.2f}" if val else "$0.00"

            base = getattr(self, '_base_capital', self.capital_usdt)
            growth_pct = ((equity - base) / base * 100) if base > 0 else 0

            msg = (
                f"📅 <b>Daily Report — {pd.Timestamp.now(tz='UTC').strftime('%b %d, %Y')}</b>\n"
                f"•••\n"
                f"📊 Trades: {s['total_trades']}  "
                f"(✅{s['winning']} / ❌{s['losing']})  "
                f"Win: {s['win_rate']}%\n"
                f"•••\n"
                f"💰 Gross: {fmt(s['gross_pnl'])}\n"
                f"💸 Fees:  -${abs(s['total_fees']):.2f}\n"
                f"📈 <b>Net Today: {fmt(s['net_pnl'])}</b>\n"
                f"•••\n"
                f"📆 Week:  {fmt(sw['net_pnl'])}\n"
                f"🗓 Month: {fmt(sm['net_pnl'])}\n"
                f"🏦 <b>Eq:</b> ${equity:,.2f} ({growth_pct:+.1f}% vs base)\n"
                f"⚙️ <b>Env:</b> {self.env.upper()}"
            )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(msg))
            except RuntimeError:
                pass
            logger.info(f"Daily report sent: pnl={fmt(s['net_pnl'])} trades={s['total_trades']}")
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}")

    # ── Notifications ────────────────────────────────────────────────

    def _notify_state_change(self, new_state, prev_state, trigger_reason, price, rsi, bb, ema, atr, actual_spacing=0):
        # Cooldown: skip Telegram if we alerted this same state within 15 min
        state_key = new_state.value
        now = time_mod.time()
        last_alert = self._last_state_alert_time.get(state_key, 0)
        if now - last_alert < self._state_alert_cooldown:
            logger.info(f"State alert suppressed (cooldown): {state_key}")
            return
        self._last_state_alert_time[state_key] = now

        spacing = actual_spacing if actual_spacing > 0 else (atr * self.atr_multiplier if atr else 0)

        if new_state == GridState.ACTIVE:
            msg = (
                f"🟢 <b>Grid ACTIVATED — {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📐 <b>Range:</b> ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
                f"📏 <b>Space:</b> ${spacing:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"⚠️ <b>Why:</b> {trigger_reason}"
            )
        elif new_state == GridState.PAUSED:
            msg = (
                f"⏸️ <b>Grid PAUSED — {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"⚠️ <b>Why:</b> {trigger_reason}\n"
                f"💤 Holding USDT until re-entry signal."
            )
        elif new_state == GridState.REACTIVATING:
            msg = (
                f"🔄 <b>Grid REACTIVATING — {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📐 <b>Range:</b> ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
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
        trade_type = getattr(event, 'trade_type', None)
        price = float(getattr(event, 'price', 0))
        quantity = float(getattr(event, 'amount', 0))
        order_id = str(getattr(event, 'order_id', getattr(event, 'client_order_id', '')))

        # Robust side detection: handle TradeType enum, string, or None
        if trade_type is not None:
            tt_name = getattr(trade_type, 'name', str(trade_type))
            is_buy = tt_name == "BUY"
        else:
            tt_name = "UNKNOWN"
            # Fallback: check if order_id starts with "buy_"
            is_buy = order_id.startswith("buy_")
        side = "BUY" if is_buy else "SELL"
        logger.info(f"Fill event: type={type(event).__name__} trade_type={tt_name} side={side} "
                     f"price=${price:,.2f} qty={quantity} order_id={order_id}")

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
        # Try to find the order in the tracker to get the actual level
        grid_order = self._grid_order_tracker.mark_filled(order_id)
        if grid_order:
            grid_level = grid_order.level
        elif self._cached_indicators:
            # Fallback estimation if tracker missing (e.g. restart)
            bb_r = self._cached_indicators[0]
            mid = bb_r.mid
            # Use actual spacing for estimation if available
            spacing = self._active_buy_spacing if is_buy else self._active_sell_spacing
            if spacing <= 0:
                spacing = atr_val * self.atr_multiplier if atr_val > 0 else 1
            
            grid_level = int(round(abs(price - mid) / spacing)) if spacing > 0 else 0

        fee_est = quantity * price * self._fee_rate
        usdt_bal = self._get_usdt_balance()
        base_bal = self._get_base_balance()
        equity = self._estimate_equity(price)
        exposure_pct = self.position_guard.base_exposure_pct(base_bal, price, equity)

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
            base_balance=round(base_bal, 4),
            equity=round(equity, 2),
        )

        if side == "BUY":
            buy_fill = FillRecord(
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
                fee=fee_est,
            )

            # Check for a waiting unmatched sell to pair with
            matching_sell = self._unmatched_sells.pop(order_id, None)
            if not matching_sell and self._unmatched_sells:
                oldest_sell_id = min(self._unmatched_sells, key=lambda k: self._unmatched_sells[k].timestamp)
                matching_sell = self._unmatched_sells.pop(oldest_sell_id)

            if matching_sell:
                # Reverse match: sell filled before buy — compute round-trip PnL
                entry_price = matching_sell.price
                exit_price = price
                gross_pnl = (entry_price - exit_price) * quantity
                duration_min = int((time_mod.time() - matching_sell.timestamp) / 60)
                total_fee = matching_sell.fee + fee_est
                net_pnl = gross_pnl - total_fee

                self.event_log.log("round_trip_closed",
                    entry_price=round(entry_price, 2),
                    exit_price=round(exit_price, 2),
                    quantity=quantity,
                    gross_pnl=round(gross_pnl, 4),
                    fee=round(total_fee, 4),
                    net_pnl=round(net_pnl, 4),
                    duration_min=duration_min,
                    grid_level=grid_level,
                    entry_rsi=round(matching_sell.rsi, 2),
                    exit_rsi=round(rsi_val, 2),
                    entry_bb_lower=round(matching_sell.bb_lower, 2),
                    entry_bb_upper=round(matching_sell.bb_upper, 2),
                    exit_bb_lower=round(bb_lower, 2),
                    exit_bb_upper=round(bb_upper, 2),
                    entry_ema=round(matching_sell.ema_200, 2),
                    exit_ema=round(ema_val, 2),
                    entry_atr=round(matching_sell.atr, 2),
                    exit_atr=round(atr_val, 2),
                    equity=round(equity, 2),
                    exposure_pct=round(exposure_pct, 1),
                    match_direction="sell_first",
                )

                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=self.display_pair,
                    side="SELL",
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    gross_pnl=round(gross_pnl, 4),
                    fee=round(total_fee, 4),
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

                pending = self._grid_order_tracker.total_pending
                pnl_sign = "+" if net_pnl >= 0 else ""
                telegram_msg = (
                    f"{'💚' if net_pnl >= 0 else '🔴'} <b>Trade Closed (SELL-first) — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"📈 BUY closed SELL position  |  Grid Level {grid_level}\n"
                    f"⏱ <b>Dur:</b> {duration_min}m\n"
                    f"🔵 <b>In:</b>  ${entry_price:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${exit_price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"•••\n"
                    f"💰 <b>Gross:</b> {pnl_sign}${gross_pnl:.2f}\n"
                    f"💸 <b>Fee:</b> -${total_fee:.2f}\n"
                    f"<b>📊 NET: {pnl_sign}${net_pnl:.2f}</b>\n"
                    f"•••\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}  |  <b>Exp:</b> {exposure_pct:.0f}%\n"
                    f"Grid: {grid_state_val}  |  Pending: {pending}\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(telegram_msg))
                except RuntimeError:
                    pass

                logger.info(f"REVERSE MATCH: SELL@${entry_price:,.2f} -> BUY@${exit_price:,.2f} | PnL=${net_pnl:.2f} | Level {grid_level}")
            else:
                # Normal path: no unmatched sell waiting — buffer this buy
                self._open_buys[order_id] = buy_fill

                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=self.display_pair,
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

                buy_msg = (
                    f"📈 <b>BUY Filled — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"💵 <b>Price:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"📊 Level {grid_level}  |  RSI: {rsi_val:.1f}\n"
                    f"📏 <b>Space:</b> ${self._active_buy_spacing:,.2f}\n"
                    f"💸 <b>Fee:</b> -${fee_est:.2f}\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}  |  <b>Exp:</b> {exposure_pct:.0f}%\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(buy_msg))
                except RuntimeError:
                    pass

                logger.info(f"BUY filled: {quantity} {self.base_asset} @ ${price:,.2f} | Level {grid_level}")

            self._save_state()
            self._grid_dirty = True

        elif side == "SELL":
            fee = fee_est

            # Try exact order_id match first (for paired fills)
            matching_buy = self._open_buys.pop(order_id, None)
            if not matching_buy and self._open_buys:
                # FIFO: pop oldest buy (earliest timestamp)
                oldest_id = min(self._open_buys, key=lambda k: self._open_buys[k].timestamp)
                matching_buy = self._open_buys.pop(oldest_id)

            self._save_state()

            if matching_buy:
                entry_price = matching_buy.price
                entry_rsi = matching_buy.rsi
                entry_bb_upper = matching_buy.bb_upper
                entry_bb_lower = matching_buy.bb_lower
                entry_ema = matching_buy.ema_200
                entry_atr = matching_buy.atr
                gross_pnl = (price - entry_price) * quantity
                duration_min = int((time_mod.time() - matching_buy.timestamp) / 60)
                total_fee = matching_buy.fee + fee
                net_pnl = gross_pnl - total_fee

                self.event_log.log("round_trip_closed",
                    entry_price=round(entry_price, 2),
                    exit_price=round(price, 2),
                    quantity=quantity,
                    gross_pnl=round(gross_pnl, 4),
                    fee=round(total_fee, 4),
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
                    pair=self.display_pair,
                    side="SELL",
                    entry_price=entry_price,
                    exit_price=price,
                    quantity=quantity,
                    gross_pnl=round(gross_pnl, 4),
                    fee=round(total_fee, 4),
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
                pending = self._grid_order_tracker.total_pending
                pnl_sign = "+" if net_pnl >= 0 else ""

                telegram_msg = (
                    f"{'💚' if net_pnl >= 0 else '🔴'} <b>Trade Closed — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"📉 SELL  |  Grid Level {grid_level}\n"
                    f"⏱ <b>Dur:</b> {duration_min}m\n"
                    f"🔵 <b>In:</b> ${entry_price:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"•••\n"
                    f"💰 <b>Gross:</b> {pnl_sign}${gross_pnl:.2f}\n"
                    f"💸 <b>Fee:</b> -${total_fee:.2f}\n"
                    f"<b>📊 NET: {pnl_sign}${net_pnl:.2f}</b>\n"
                    f"•••\n"
                    f"📉 RSI: {entry_rsi:.1f} → {rsi_val:.1f}\n"
                    f"📐 BB: ${entry_bb_lower:,.2f} → ${bb_upper:,.2f}\n"
                    f"📏 ATR: ${atr_val:,.2f}  |  <b>Space:</b> ${self._active_sell_spacing:,.2f}\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}  |  <b>Exp:</b> {exposure_pct:.0f}%\n"
                    f"Grid: {grid_state_val}  |  Pending: {pending}\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
            else:
                # No open buy to match — buffer as unmatched sell
                self._unmatched_sells[order_id] = FillRecord(
                    order_id=order_id,
                    side="SELL",
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
                    fee=fee,
                )
                self._save_state()

                self.event_log.log("sell_buffered",
                    side="SELL", price=price, quantity=quantity,
                    grid_level=grid_level, fee_estimate=round(fee, 4),
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower,
                    ema_200=ema_val, atr=atr_val,
                    usdt_balance=round(usdt_bal, 2),
                    base_balance=round(base_bal, 4),
                    equity=round(equity, 2),
                    unmatched_sell_count=len(self._unmatched_sells),
                )

                telegram_msg = (
                    f"🟡 <b>SELL Filled (buffered, awaiting BUY match) — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"📉 SELL  |  Grid Level {grid_level}\n"
                    f"💵 <b>Price:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"💸 <b>Fee:</b> -${fee:.2f}\n"
                    f"🔄 Buffered sells awaiting match: {len(self._unmatched_sells)}\n"
                    f"•••\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}  |  <b>Exp:</b> {exposure_pct:.0f}%\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(telegram_msg))
            except RuntimeError:
                pass

            logger.info(f"SELL filled: {quantity} {self.base_asset} @ ${price:,.2f} | Level {grid_level}")
            self._grid_dirty = True

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
        self._save_state()
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
