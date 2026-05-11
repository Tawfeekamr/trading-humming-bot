"""
TA Grid + Trend Dual-Engine Strategy

Runs the existing grid bot unchanged alongside a new trend-following engine.
Both engines share one Hummingbot instance but have isolated capital and state.
"""
import os
import json
import logging
import threading
import traceback as traceback_mod
from datetime import datetime, timezone
from decimal import Decimal
from dotenv import load_dotenv
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yaml

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

from src.trend.trend_manager import TrendManager
from src.trend.position_manager import PositionManager
from src.trend.trend_journal import TrendJournal
from src.indicators.atr import ATR
from src.data.candle_feed import CandleFeed
from src.risk.circuit_breaker import CircuitBreaker
from src.health import update_health, set_halted, start_health_server
from src.notifications.telegram_bot import TelegramBot
from src.notifications.telegram_commands import TelegramCommandHandler
from src.logging.event_logger import EventLogger

try:
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
    from hummingbot.connector.connector_base import ConnectorBase
    from pydantic import Field
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False
    StrategyV2Base = object
    StrategyV2ConfigBase = object
    ConnectorBase = object
    Field = lambda **kwargs: kwargs

logger = logging.getLogger(__name__)


class TAGridTrendConfig(StrategyV2ConfigBase):
    """
    Configuration class for TA Grid + Trend Dual-Engine strategy.
    Extends StrategyV2ConfigBase with strategy-specific parameters.
    """

    # Required by StrategyV2ConfigBase
    script_file_name: str = Field(default="ta_grid_trend.py")

    # Exchange configuration
    exchange: str = Field(default="binance_paper_trade")
    trading_pair: str = Field(default="SOL-USDT")

    def update_markets(self, markets: Dict) -> Dict:
        """
        Register the trading pairs for this strategy.
        Called by Hummingbot v2 to configure which markets to connect to.
        """
        markets[self.exchange] = {self.trading_pair: {}}
        return markets


class TAGridTrendStrategy(StrategyV2Base):
    """Dual-engine strategy: grid bot + trend following."""

    @staticmethod
    def _load_config() -> dict:
        """Load configuration from strategy.yaml with fallback paths."""
        config_paths = [
            Path("config/strategy.yaml"),
            Path(__file__).parent.parent.parent / "config" / "strategy.yaml",
        ]
        for path in config_paths:
            if path.exists():
                with open(path) as f:
                    return yaml.safe_load(f)
        return {}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: TAGridTrendConfig):
        """
        Initialize the dual-engine strategy with connectors and configuration.

        Args:
            connectors: Dictionary of exchange connector names to connector instances
            config: Strategy configuration instance
        """
        setup_logging()

        # Store config
        self.config = config
        self.exchange = config.exchange
        self.trading_pair = config.trading_pair

        # Call parent constructor (required for v2)
        super().__init__(connectors, config)

        # Start health server
        start_health_server(port=8081)  # Different port to avoid conflict with grid bot

        # Load configuration from YAML
        cfg = self._load_config()
        trend_cfg = cfg.get("trend", {})

        # Environment
        self.env = os.environ.get("ENV", "paper")
        self.is_testnet = self.env == "paper"

        # Candle feed — uses Binance REST API directly (public, no keys needed)
        self.binance_symbol = self.trading_pair.replace("-", "")  # e.g. "SOLUSDT"
        self.display_pair = self.trading_pair.replace("-", "/")   # e.g. "SOL/USDT"
        self.candle_feed = CandleFeed(
            symbol=self.binance_symbol,
            interval=trend_cfg.get("timeframe", "1h"),
            testnet=self.is_testnet,
        )
        self._last_candle_time = None

        # Trend engine components
        self._trend_manager = TrendManager(
            ema_fast=trend_cfg.get("ema_fast", 20),
            ema_slow=trend_cfg.get("ema_slow", 50),
            ema_trend=trend_cfg.get("ema_trend", 200),
            rsi_period=trend_cfg.get("rsi_period", 14),
            rsi_min=trend_cfg.get("rsi_min", 40),
            rsi_max=trend_cfg.get("rsi_max", 70),
            min_signal_score=trend_cfg.get("min_signal_score", 3),
            confirmation_ticks=trend_cfg.get("confirmation_ticks", 2),
            sl_buffer_pct=trend_cfg.get("sl_buffer_pct", 0.2),
            rr_ratio=trend_cfg.get("rr_ratio", 2.0),
        )

        capital = float(os.environ.get("TREND_CAPITAL_USDT", trend_cfg.get("capital", 0)))
        self._position_manager = PositionManager(
            capital=capital,
            max_positions=trend_cfg.get("max_positions", 2),
            risk_per_trade_pct=trend_cfg.get("risk_per_trade_pct", 2.0),
            max_position_pct=trend_cfg.get("max_position_pct", 25.0),
            trailing_stop_pct=trend_cfg.get("trailing_stop_pct", 1.5),
            trailing_activation_pct=trend_cfg.get("trailing_activation_pct", 1.5),
        )

        self._trend_journal = TrendJournal()
        self._trend_enabled = trend_cfg.get("enabled", True)

        # Trend circuit breaker (separate from grid)
        self._trend_breaker = CircuitBreaker(
            max_drawdown_pct=trend_cfg.get("max_drawdown_pct", 10.0),
            daily_loss_limit_pct=trend_cfg.get("daily_loss_limit_pct", 5.0),
        )

        # State
        self._last_price: float = 0.0
        self._last_trend_score = None
        self._trend_force_close: bool = False
        self._trend_tick_count: int = 0
        self._state_lock = threading.Lock()

        # Load trend state
        trend_state_path = Path("data/trend_state.json")
        if trend_state_path.exists():
            self._position_manager.load_state(trend_state_path)

        # Telegram — shared bot instance, command handler with this strategy
        self.telegram = TelegramBot()
        self._event_log = EventLogger(log_dir="logs")
        self._telegram_commands = TelegramCommandHandler(
            journal=None,
            state_machine=None,
            circuit_breaker=self._trend_breaker,
            position_guard=None,
            event_logger=self._event_log,
            strategy=self,
        )
        self._telegram_commands.start()

        logger.info(f"Trend engine initialized: capital=${self._position_manager._capital:.2f}, enabled={self._trend_enabled}")
        _logger.info(f"Dual-engine strategy started on {self.exchange} {self.trading_pair}")

        # Start force-ready watchdog (bypasses connector freeze after 30s)
        threading.Thread(target=self._force_connector_ready, daemon=True).start()

    def _force_connector_ready(self):
        """Force ready_to_trade after timeout to bypass connector freeze."""
        try:
            import time
            time.sleep(30)
            if self._trend_tick_count == 0:
                logger.warning("Connector never became ready after 30s — forcing ready_to_trade=True")
                self.ready_to_trade = True
        except Exception as e:
            logger.error(f"Force-ready thread failed: {e}")

    def on_tick(self):
        """
        Main strategy loop called by Hummingbot on each tick.
        This is the trend engine only - the grid bot runs as a separate process.
        """
        self._trend_tick_count += 1

        # Update current price
        connector = self.connectors.get(self.exchange)
        if connector:
            try:
                mid_price = connector.get_mid_price(self.trading_pair)
                if mid_price:
                    self._last_price = float(mid_price)
            except Exception as e:
                logger.debug(f"Failed to get mid price: {e}")

        # Update trailing stops
        if self._last_price > 0:
            for pos in self._position_manager.get_all_positions():
                self._position_manager.update_trailing(pos, self._last_price)

        # Check exits every tick
        if self._trend_enabled and self._position_manager.open_count > 0:
            self._check_trend_exits()

        # Force close
        if self._trend_force_close:
            self._close_all_trend_positions()
            self._trend_force_close = False

        # Refresh candle data every ~55 minutes (must happen before signal eval)
        now = pd.Timestamp.now(tz="UTC")
        if (self._last_candle_time is None
                or now - self._last_candle_time >= pd.Timedelta(minutes=55)):
            try:
                df = self.candle_feed.fetch_candles(limit=250)
                if not df.empty and len(df) >= 200:
                    self._cached_candles = df
                    self._last_candle_time = now
                    logger.info(f"Candle data refreshed: {len(df)} candles")
                else:
                    logger.warning(f"Insufficient candle data: {len(df) if not df.empty else 0}")
            except Exception as e:
                logger.error(f"Candle fetch failed: {e}")

        # Evaluate signals every 55 ticks (~55 seconds = ~1 min apart)
        if (self._trend_enabled
                and self._last_price > 0
                and self._position_manager._capital > 0
                and self._trend_tick_count % 55 == 0):
            self._evaluate_trend_signals()

        # Update health status
        update_health(
            trend_healthy=not self._trend_breaker.halted,
            trend_positions=self._position_manager.open_count,
            last_signal_score=self._last_trend_score.total if self._last_trend_score else 0,
        )

    def _check_trend_exits(self):
        """Check if any open positions should be exited."""
        if not self._last_price:
            return

        exits = self._position_manager.check_exits(self._last_price)
        for exit_info in exits:
            pos = self._position_manager.get_position(exit_info["order_id"])
            if pos:
                self._execute_trend_exit(pos, exit_info)

    def _execute_trend_exit(self, pos, exit_info: dict):
        """Execute a trend position exit."""
        exit_price = exit_info["exit_price"]
        reason = exit_info["reason"]
        amount = Decimal(str(pos.amount)).quantize(Decimal("0.01"))

        try:
            order_id = self.sell(self.exchange, self.trading_pair, amount)
            logger.info(f"Trend SELL order placed: {amount} SOL @ {exit_price}")
        except Exception as e:
            logger.error(f"Trend sell failed: {e}")
            return

        closed = self._position_manager.close_position(pos.entry_order_id, exit_price, reason)
        if closed:
            # Calculate fee (0.075% standard tier)
            fee = exit_price * float(amount) * 0.00075

            self._trend_journal.log_trade(
                side="SELL",
                entry_price=closed["entry_price"],
                exit_price=exit_price,
                amount=closed["amount"],
                fee=round(fee, 2),
                pnl=closed["pnl"],
                pnl_pct=closed["pnl_pct"],
                stop_loss=closed["stop_loss"],
                take_profit=closed["take_profit"],
                exit_reason=reason,
                signal_score=0,
                duration_minutes=closed["duration_minutes"],
            )
            self._save_trend_state()
            logger.info(f"TREND EXIT ({reason}): {closed['amount']:.1f} SOL @ ${exit_price:.2f} | PnL ${closed['pnl']:+.2f}")

    def _evaluate_trend_signals(self):
        """Evaluate trend signals and potentially open new positions."""
        if not self._position_manager.can_open():
            return
        if self._trend_breaker.halted:
            logger.warning("Trend circuit breaker halted - skipping signal evaluation")
            return

        candles = getattr(self, '_cached_candles', None)
        if candles is None or len(candles) < 200:
            logger.debug(f"Insufficient cached candles: {len(candles) if candles is not None else 0}")
            return

        score = self._trend_manager.evaluate(candles, self._last_price)
        self._last_trend_score = score

        logger.info(f"Trend signal score: {score.total}/7 | details: {[d['signal'] for d in score.details]}")

        if self._trend_manager.should_enter(score):
            if self._trend_manager.confirm_entry(score):
                self._open_trend_position(candles, score)
            else:
                logger.info(f"Trend signal pending confirmation (score={score.total}/7, need {self._trend_manager._confirmation_ticks} ticks)")

    # NOTE: _fetch_candles removed — candle data is now fetched in on_tick()
    # via self.candle_feed.fetch_candles() and cached in self._cached_candles.

    def _open_trend_position(self, candles: pd.DataFrame, score):
        """Open a new trend position."""
        # Detect support/resistance levels
        sr_levels = self._trend_manager._sr.detect(candles)

        # Calculate ATR for stop loss
        atr = ATR(14)
        closes = candles["close"]
        atr_val = None
        if "high" in candles.columns and "low" in candles.columns:
            atr_val = atr.calculate(candles["high"], candles["low"], closes)

        # Calculate stop loss and take profit
        sl = self._trend_manager.calculate_stop_loss(self._last_price, sr_levels, atr_val)
        tp = self._trend_manager.calculate_take_profit(self._last_price, sl)

        # Calculate position size
        amount = self._position_manager.calculate_position_size(self._last_price, sl)
        if amount <= 0:
            logger.warning(f"Position size too small: {amount}")
            return

        amount_dec = Decimal(str(amount)).quantize(Decimal("0.01"))
        try:
            order_id = self.buy(self.exchange, self.trading_pair, amount_dec)
            logger.info(f"Trend BUY order placed: {amount_dec} SOL @ {self._last_price}")
        except Exception as e:
            logger.error(f"Trend buy failed: {e}")
            return

        entry_time = datetime.now(timezone.utc).isoformat()
        pos = self._position_manager.open_position(
            entry_order_id=str(order_id),
            entry_price=self._last_price,
            amount=amount,
            stop_loss=sl,
            take_profit=tp,
            entry_time=entry_time,
        )

        if pos:
            self._save_trend_state()
            logger.info(f"TREND ENTRY: {amount:.1f} SOL @ ${self._last_price:.2f} | "
                       f"SL ${sl:.2f} TP ${tp:.2f} | Score {score.total}/7")

            # Update circuit breaker peak equity
            self._trend_breaker.set_peak_equity(self._position_manager._capital + pos.amount * self._last_price)

    def _close_all_trend_positions(self):
        """Close all open trend positions (emergency/manual)."""
        logger.warning("Closing all trend positions...")
        for pos in self._position_manager.get_all_positions():
            self._execute_trend_exit(pos, {
                "order_id": pos.entry_order_id,
                "exit_price": self._last_price or pos.entry_price,
                "reason": "manual_close",
            })

    def _save_trend_state(self):
        """Save trend position state to disk."""
        path = Path("data/trend_state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._position_manager.save_state(path)

    def did_fill_order(self, event):
        """
        Called when an order is filled.
        Currently no-op as fills are tracked via order_id in position manager.
        """
        pass
