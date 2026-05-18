"""
TA Grid + Trend Dual-Engine Strategy

Runs grid bot and trend-following engine in one Hummingbot strategy.
Both engines share one connector but have isolated capital and state.
"""
import os
import asyncio
import logging
import threading
import json
import traceback as traceback_mod
from datetime import datetime, timezone
from decimal import Decimal
from dotenv import load_dotenv
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass as dataclass_fills
from typing import Optional as Optional_fills
import time as time_mod

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
from src.trend.trend_manager import TrendManager
from src.trend.position_manager import PositionManager
from src.trend.trend_journal import TrendJournal
from src.notifications.telegram_bot import TelegramBot
from src.notifications.telegram_commands import TelegramCommandHandler
from src.journal.trade_journal import TradeJournal, Trade
from src.health import update_health, set_halted, start_health_server
from src.logging.event_logger import EventLogger
from src.monitoring.system_monitor import SystemAlertMonitor

try:
    from src.ml.regime_classifier import RegimeClassifier
    from src.data.feature_engineering import calculate_technical_features
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False


try:
    from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
    from hummingbot.connector.connector_base import ConnectorBase
    from hummingbot.core.data_type.common import MarketDict, OrderType, PriceType, TradeType
    from pydantic import Field
    V2_AVAILABLE = True
except ImportError:
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


class TAGridTrendConfig(StrategyV2ConfigBase):
    """Configuration class for dual-engine strategy."""

    script_file_name: str = Field(default="ta_grid_trend.py")

    # Exchange
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
        markets[self.exchange] = {self.trading_pair: {}}
        return markets


class TAGridTrendStrategy(StrategyV2Base):
    """Dual-engine strategy: grid bot + trend following."""

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

    def __init__(self, connectors: Dict[str, ConnectorBase], config: TAGridTrendConfig):
        setup_logging()

        self.config = config
        self.exchange = config.exchange
        self.trading_pair = config.trading_pair

        # Call parent constructor (required for v2)
        super().__init__(connectors, config)

        # Load configuration from YAML
        cfg = self._load_config()
        grid_cfg = cfg.get("grid", {})
        ind_cfg = cfg.get("indicators", {})
        risk_cfg = cfg.get("risk", {})
        trend_cfg = cfg.get("trend", {})

        # Environment
        self.env = os.environ.get("ENV", config.env)
        self.is_testnet = self.env == "paper"

        # Pair helpers
        self.base_asset = self.trading_pair.split("-")[0]
        self.binance_symbol = self.trading_pair.replace("-", "")
        self.display_pair = self.trading_pair.replace("-", "/")

        # ── Health server ──
        start_health_server(port=8080)

        # ── Grid engine ──
        self.levels = int(os.environ.get("GRID_LEVELS", grid_cfg.get("levels", config.levels)))
        self.capital_usdt = float(os.environ.get("GRID_CAPITAL_USDT", grid_cfg.get("capital_usdt", config.capital_usdt)))
        self.min_reserve = float(os.environ.get("MIN_USDT_RESERVE", grid_cfg.get("min_usdt_reserve", config.min_reserve)))
        self.order_refresh_time = grid_cfg.get("order_refresh_time", config.order_refresh_time)
        self.step_size = float(grid_cfg.get("step_size", config.step_size))

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

        self.bb = BollingerBands(self.bb_period, self.bb_std)
        self.rsi = RSI(self.rsi_period)
        self.ema = EMA(self.ema_period)
        self.atr = ATR(self.atr_period, self.atr_multiplier)

        self.grid_manager = GridManager(
            levels=self.levels,
            capital_usdt=self.capital_usdt,
            min_reserve=self.min_reserve,
            step_size=self.step_size,
            spacing_multiplier=self.atr_multiplier,
        )
        self._base_capital = self.capital_usdt
        self._initial_equity = None
        self.state_machine = GridStateMachine()
        self._grid_order_tracker = OrderTracker()
        self.grid_circuit_breaker = CircuitBreaker(
            float(os.environ.get("MAX_DRAWDOWN_PCT", risk_cfg.get("max_drawdown_pct", config.max_drawdown_pct))),
            risk_cfg.get("daily_loss_limit_pct", config.daily_loss_limit_pct),
        )
        self.grid_circuit_breaker.set_peak_equity(self.capital_usdt)
        self.grid_circuit_breaker.set_start_of_day_equity(self.capital_usdt)
        self.position_guard = PositionGuard(
            float(os.environ.get("MAX_BASE_EXPOSURE_PCT", risk_cfg.get("max_base_exposure_pct", config.max_base_exposure_pct))),
            self.min_reserve, self.capital_usdt,
        )
        self._peak_equity = self.capital_usdt
        self._open_buys: dict[str, FillRecord] = {}
        self._unmatched_sells: dict[str, FillRecord] = {}
        self._grid_dirty = True
        self._last_state_alert_time: dict[str, float] = {}
        self._state_alert_cooldown = 900
        self._manual_pause = False
        self._last_sod_reset: Optional[str] = None
        self._fee_rate: float = 0.00075
        self._overtrading_alerted_today: str = ""
        self._active_buy_spacing = 0.0
        self._active_sell_spacing = 0.0
        self._last_grid_place_time = 0
        self._min_grid_refresh_sec = 300

        # ── Shared candle feed ──
        self.candle_feed = CandleFeed(
            symbol=self.binance_symbol,
            interval=trend_cfg.get("timeframe", "1h"),
            testnet=self.is_testnet,
        )
        self._last_candle_time = None
        self._cached_indicators = None

        # ── Trend engine ──
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

        trend_capital = float(os.environ.get("TREND_CAPITAL_USDT", trend_cfg.get("capital", 0)))
        self._position_manager = PositionManager(
            capital=trend_capital,
            max_positions=trend_cfg.get("max_positions", 2),
            risk_per_trade_pct=trend_cfg.get("risk_per_trade_pct", 2.0),
            max_position_pct=trend_cfg.get("max_position_pct", 25.0),
            trailing_stop_pct=trend_cfg.get("trailing_stop_pct", 1.5),
            trailing_activation_pct=trend_cfg.get("trailing_activation_pct", 1.5),
        )

        self._trend_journal = TrendJournal()
        self._trend_enabled = trend_cfg.get("enabled", True)

        self._trend_breaker = CircuitBreaker(
            max_drawdown_pct=trend_cfg.get("max_drawdown_pct", 10.0),
            daily_loss_limit_pct=trend_cfg.get("daily_loss_limit_pct", 5.0),
        )

        # ── ML Regime Classifier ──
        self._ml_classifier = None
        self._ml_confidence = 0.0
        self._ml_regime = 0
        if ML_AVAILABLE:
            try:
                model_path = Path("models/regime_rf_v3.pkl")
                if model_path.exists():
                    self._ml_classifier = RegimeClassifier(model_path=str(model_path))
                    self._ml_classifier.load_model()
                    logger.info(f"Loaded ML Regime Classifier from {model_path}")
                else:
                    logger.info("ML Regime Classifier model file not found.")
            except Exception as e:
                logger.warning(f"Could not initialize ML Regime Classifier: {e}")

        # ── Shared state ──
        self._last_price: float = 0.0
        self._last_trend_score = None
        self._trend_force_close: bool = False
        self._tick_count = 0
        self._trend_tick_count: int = 0
        self._state_lock = threading.Lock()

        # ── Shared services ──
        self.telegram = TelegramBot()
        self.journal = TradeJournal()
        self.event_log = EventLogger(log_dir="logs")

        self.event_log.log("bot_started", mode=self.env, capital=self.capital_usdt,
                           levels=self.levels, testnet=self.is_testnet, trend_capital=trend_capital)

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
            f"Trend engine: capital=${trend_capital:.2f} enabled={self._trend_enabled} "
            f"ema={trend_cfg.get('ema_fast', 20)}/{trend_cfg.get('ema_slow', 50)}/{trend_cfg.get('ema_trend', 200)}"
        )

        # Fee tier auto-detection
        try:
            fee_info = self.candle_feed.client.get_trade_fee(symbol=self.binance_symbol)
            if fee_info and len(fee_info) > 0:
                maker_fee = float(fee_info[0].get("makerCommission", 0.00075))
                if maker_fee > 0:
                    self._fee_rate = maker_fee
            logger.info(f"Fee rate: {self._fee_rate * 100:.4f}%")
        except Exception as e:
            logger.info(f"Fee detection skipped, using default 0.075%: {e}")

        # Telegram commands
        self._telegram_commands = TelegramCommandHandler(
            journal=self.journal,
            state_machine=self.state_machine,
            circuit_breaker=self.grid_circuit_breaker,
            position_guard=self.position_guard,
            event_logger=self.event_log,
            strategy=self,
        )
        self._telegram_commands.start()

        # System monitor
        self._sys_monitor = SystemAlertMonitor(self.telegram, interval_sec=300)
        self._sys_monitor.start()

        # Startup Telegram alert
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_startup(self.env, self.capital_usdt))
        except RuntimeError:
            pass

        # ── Load state ──
        self._state_file = Path("data/grid_state.json")
        self._load_grid_state()

        trend_state_path = Path("data/trend_state.json")
        if trend_state_path.exists():
            self._position_manager.load_state(trend_state_path)

        _logger.info(f"Dual-engine strategy started on {self.exchange} {self.trading_pair}")

        # Force-ready watchdog
        threading.Thread(target=self._force_connector_ready, daemon=True).start()

    # ── Properties for config fields used by grid logic ──
    @property
    def max_drawdown_pct(self):
        return float(os.environ.get("MAX_DRAWDOWN_PCT", self.grid_circuit_breaker.max_drawdown_pct))

    @property
    def daily_loss_limit_pct(self):
        return self.grid_circuit_breaker.daily_loss_limit_pct

    @property
    def max_base_exposure_pct(self):
        return float(os.environ.get("MAX_BASE_EXPOSURE_PCT", self.position_guard.max_base_exposure_pct))

    # ── Force-Ready Watchdog ──

    def _force_connector_ready(self):
        try:
            time_mod.sleep(30)
            if self._tick_count == 0:
                self.ready_to_trade = True
        except Exception:
            pass

    # ── Main Tick Loop ──

    def on_tick(self):
        try:
            self._tick_count += 1
            self._trend_tick_count += 1

            # Poll for Telegram commands (non-blocking)
            if self._telegram_commands:
                self._telegram_commands.poll_once()

            # Update current price
            connector = self.connectors.get(self.exchange)
            if connector:
                try:
                    mid_price = connector.get_mid_price(self.trading_pair)
                    if mid_price:
                        self._last_price = float(mid_price)
                except Exception:
                    pass

            # ── Grid Engine ──
            self._grid_tick()

            # ── Trend Engine ──
            self._trend_tick()

            # Update health
            update_health(
                grid_state=self.state_machine.state.value,
                trend_healthy=not self._trend_breaker.halted,
                trend_positions=self._position_manager.open_count,
                last_signal_score=self._last_trend_score.total if self._last_trend_score else 0,
            )
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"CRASH in on_tick: {e}\n{tb}")
            self._safe_telegram_crash("on_tick", str(e), tb)

    # ── Grid Engine Tick ──

    def _grid_tick(self):
        if self.grid_circuit_breaker.halted:
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
            self.grid_circuit_breaker.set_start_of_day_equity(equity)
            self._last_sod_reset = today_str
            self.event_log.log("daily_reset", equity=round(equity, 2))
            logger.info(f"Start-of-day equity reset: ${equity:.2f}")
            self._send_daily_report(equity)

        # Overtrading check
        if self._overtrading_alerted_today != today_str:
            try:
                ot = self.journal.is_overtrading(threshold=0.30)
                if ot["is_overtrading"]:
                    self._overtrading_alerted_today = today_str
                    self.event_log.log("overtrading_detected", **ot)
                    ot_msg = (
                        f"⚠️ <b>OVERTRADING DETECTED</b>\n"
                        f"💸 Fees today: ${ot['total_fees']:.2f}\n"
                        f"📊 Gross activity: ${ot['abs_gross_pnl']:.2f}\n"
                        f"📈 Fee ratio: {ot['fee_to_gross_ratio']:.1%} (threshold: {ot['threshold']:.0%})"
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
                return

            if df.empty or len(df) < self.bb_period:
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
                return

            self._cached_indicators = (bb_result, rsi_value, ema_value, atr_value, current_price)
            self._last_candle_time = now
            self._cached_candles = df
            self._grid_dirty = True
            
            # ML Prediction
            if self._ml_classifier:
                try:
                    df_features = calculate_technical_features(df)
                    if not df_features.empty:
                        last_features = df_features.iloc[[-1]][[
                            'returns', 'volatility_14', 'volatility_30', 'normalized_atr',
                            'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
                            'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14'
                        ]]
                        prob = self._ml_classifier.predict_proba(last_features)[0]
                        regime_probs = self._ml_classifier.predict_proba_full(last_features)
                        self._ml_regime = self._ml_classifier.predict_class(last_features)
                        self._ml_confidence = regime_probs[self._ml_regime]
                        REGIME_NAMES = {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}
                        regime_name = REGIME_NAMES.get(self._ml_regime, 'UNKNOWN')
                        logger.info(f"ML Regime: {regime_name} ({self._ml_confidence*100:.1f}%) | probs={regime_probs}")
                except Exception as e:
                    logger.error(f"ML classification failed: {e}")

            self.event_log.log("indicators_updated",
                rsi=round(rsi_value, 2), bb_upper=round(bb_result.upper, 2),
                bb_mid=round(bb_result.mid, 2), bb_lower=round(bb_result.lower, 2),
                ema_200=round(ema_value, 2), atr=round(atr_value, 2),
                price=round(current_price, 2), grid_state=self.state_machine.state.value,
                ml_confidence=round(self._ml_confidence, 3), ml_regime=self._ml_regime
            )
        else:
            if self._cached_indicators is None:
                return
            bb_result, rsi_value, ema_value, atr_value, current_price = self._cached_indicators

        # Evaluate state
        prev_state = self.state_machine.state
        new_state = self.state_machine.evaluate(
            price=current_price, rsi=rsi_value, ema_200=ema_value,
            bb_lower=bb_result.lower, bb_upper=bb_result.upper,
            rsi_overbought=self.rsi_overbought, rsi_oversold=self.rsi_oversold,
            ml_regime=self._ml_regime, ml_confidence=self._ml_confidence,
        )

        if new_state != prev_state:
            trigger_reason = self._determine_trigger_reason(
                prev_state, new_state, current_price, rsi_value, ema_value, bb_result
            )
            logger.info(f"Grid state: {prev_state.value} -> {new_state.value} ({trigger_reason})")
            self.event_log.log("state_changed",
                previous_state=prev_state.value, new_state=new_state.value,
                trigger_reason=trigger_reason, price=round(current_price, 2),
                rsi=round(rsi_value, 2), ema_200=round(ema_value, 2),
            )
            self._notify_state_change(new_state, prev_state, trigger_reason, current_price, rsi_value, bb_result, ema_value, atr_value, self._active_buy_spacing)
            self._grid_dirty = True

        if self.state_machine.is_paused:
            if self._grid_dirty:
                self._cancel_all_orders("state_paused")
                self._grid_dirty = False
            return

        equity = self._estimate_equity(current_price)
        self.grid_circuit_breaker.update_peak(equity)
        if self.grid_circuit_breaker.check(equity) or self.grid_circuit_breaker.check_daily(equity):
            self._cancel_all_orders("circuit_breaker")
            self.event_log.log("circuit_breaker",
                drawdown_pct=round(((self._peak_equity - equity) / self._peak_equity) * 100, 2) if self._peak_equity > 0 else 0,
                peak_equity=round(self._peak_equity, 2), current_equity=round(equity, 2),
            )
            logger.critical("Grid circuit breaker triggered!")
            set_halted("circuit_breaker")
            return

        if self._grid_dirty:
            now_ts = time_mod.time()
            elapsed = now_ts - self._last_grid_place_time
            if elapsed < self._min_grid_refresh_sec:
                return
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
            logger.info(f"Grid updated: buy_spacing=${grid.buy_spacing:.2f}, sell_spacing=${grid.sell_spacing:.2f} | compound=${compound_capital:.2f}")
            deployed = sum(l["price"] * l["quantity"] for l in grid.buy_levels)
            self.event_log.log("grid_recalculated",
                bb_upper=round(bb_result.upper, 2), bb_lower=round(bb_result.lower, 2),
                buy_spacing=round(grid.buy_spacing, 2), sell_spacing=round(grid.sell_spacing, 2),
                num_buy_levels=len(grid.buy_levels), num_sell_levels=len(grid.sell_levels),
                total_capital_deployed=round(deployed, 2),
            )
            self._grid_dirty = False

    # ── Trend Engine Tick ──

    def _trend_tick(self):
        if not self._trend_enabled:
            return

        # Update trailing stops
        if self._last_price > 0:
            for pos in self._position_manager.get_all_positions():
                self._position_manager.update_trailing(pos, self._last_price)

        # Check exits every tick
        if self._position_manager.open_count > 0:
            self._check_trend_exits()

        # Force close
        if self._trend_force_close:
            self._close_all_trend_positions()
            self._trend_force_close = False

        # Evaluate signals every 55 ticks (~1 min apart)
        if (self._last_price > 0
                and self._position_manager._capital > 0
                and self._trend_tick_count % 55 == 0):
            self._evaluate_trend_signals()

    # ── Fill Handler ──

    def did_fill_order(self, event):
        try:
            order_id = str(getattr(event, 'order_id', getattr(event, 'client_order_id', '')))
            # Route to trend if order_id matches a trend position's entry OR exit order ID
            if self._position_manager.get_position(order_id) or self._position_manager.get_position_by_exit(order_id):
                self._trend_fill(event)
            else:
                self._grid_fill(event)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"CRASH in did_fill_order: {e}\n{tb}")
            self._safe_telegram_crash("did_fill_order", str(e), tb)

    def _trend_fill(self, event):
        """Handle fill for a trend position (async tracking)."""
        order_id = str(getattr(event, 'order_id', getattr(event, 'client_order_id', '')))
        price = float(getattr(event, 'price', 0))
        quantity = float(getattr(event, 'amount', 0))
        fee = quantity * price * self._fee_rate

        pos = self._position_manager.get_position(order_id)
        if pos:
            # It's an entry fill
            pos.entry_price = price
            pos.amount = quantity  # update to actual filled amount
            logger.info(f"TREND ENTRY FILLED: {quantity} SOL @ ${price:,.2f}")
            msg = (
                f"🚀 <b>TREND IN: {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                f"🛑 <b>SL:</b> ${pos.stop_loss:,.2f}\n"
                f"🎯 <b>TP:</b> ${pos.take_profit:,.2f}\n"
                f"⚙️ <b>Env:</b> {self.env.upper()}"
            )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(msg))
            except RuntimeError:
                pass
            return

        pos = self._position_manager.get_position_by_exit(order_id)
        if pos:
            # It's an exit fill
            closed = self._position_manager.finalize_exit(pos.entry_order_id, price, fee)
            if closed:
                self._trend_journal.log_trade(
                    side="SELL", entry_price=closed["entry_price"], exit_price=closed["exit_price"],
                    amount=closed["amount"], fee=round(fee, 2), pnl=closed["pnl"],
                    pnl_pct=closed["pnl_pct"], stop_loss=closed["stop_loss"],
                    take_profit=closed["take_profit"], exit_reason=closed["exit_reason"],
                    signal_score=0, duration_minutes=closed["duration_minutes"],
                )
                self._save_trend_state()
                logger.info(f"TREND EXIT FILLED ({closed['exit_reason']}): {closed['amount']:.1f} SOL @ ${price:.2f} | PnL ${closed['pnl']:+.2f}")
                
                pnl_sign = "+" if closed["pnl"] >= 0 else ""
                emoji = "💚" if closed["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>TREND OUT: {self.display_pair}</b>\n"
                    f"•••\n"
                    f"🔔 <b>{closed['exit_reason'].upper()}</b> ({closed['duration_minutes']}m)\n"
                    f"🔵 <b>In:</b>  ${closed['entry_price']:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {closed['amount']} {self.base_asset}\n"
                    f"•••\n"
                    f"<b>📊 NET: {pnl_sign}${closed['pnl'] - fee:.2f}</b>\n"
                    f"💸 <i>Fee: -${fee:.2f}</i>\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(msg))
                except RuntimeError:
                    pass

    def _grid_fill(self, event):
        """Handle fill for a grid order."""
        trade_type = getattr(event, 'trade_type', None)
        price = float(getattr(event, 'price', 0))
        quantity = float(getattr(event, 'amount', 0))
        order_id = str(getattr(event, 'order_id', getattr(event, 'client_order_id', '')))

        if trade_type is not None:
            tt_name = getattr(trade_type, 'name', str(trade_type))
            is_buy = tt_name == "BUY"
        else:
            tt_name = "UNKNOWN"
            is_buy = order_id.startswith("buy_")
        side = "BUY" if is_buy else "SELL"
        logger.info(f"Grid fill: side={side} price=${price:,.2f} qty={quantity} order_id={order_id}")

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
        grid_order = self._grid_order_tracker.mark_filled(order_id)
        if grid_order:
            grid_level = grid_order.level
        elif self._cached_indicators:
            bb_r = self._cached_indicators[0]
            mid = bb_r.mid
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
            side=side, price=round(price, 2), quantity=quantity,
            grid_level=grid_level, fee_estimate=round(fee_est, 4),
            rsi=round(rsi_val, 2), bb_upper=round(bb_upper, 2),
            bb_lower=round(bb_lower, 2), ema_200=round(ema_val, 2),
            atr=round(atr_val, 2), usdt_balance=round(usdt_bal, 2),
            base_balance=round(base_bal, 4), equity=round(equity, 2),
            engine="grid",
        )

        if side == "BUY":
            buy_fill = FillRecord(
                order_id=order_id, side=side, price=price, quantity=quantity,
                grid_level=grid_level, timestamp=time_mod.time(),
                rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower,
                ema_200=ema_val, atr=atr_val, grid_state=grid_state_val, fee=fee_est,
            )

            matching_sell = self._unmatched_sells.pop(order_id, None)
            if not matching_sell and self._unmatched_sells:
                oldest_sell_id = min(self._unmatched_sells, key=lambda k: self._unmatched_sells[k].timestamp)
                matching_sell = self._unmatched_sells.pop(oldest_sell_id)

            if matching_sell:
                entry_price = matching_sell.price
                gross_pnl = (entry_price - price) * quantity
                duration_min = int((time_mod.time() - matching_sell.timestamp) / 60)
                total_fee = matching_sell.fee + fee_est
                net_pnl = gross_pnl - total_fee

                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=self.display_pair, side="SELL", entry_price=entry_price, exit_price=price,
                    quantity=quantity, gross_pnl=round(gross_pnl, 4), fee=round(total_fee, 4),
                    net_pnl=round(net_pnl, 4), grid_level=grid_level, duration_min=duration_min,
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower, ema_200=ema_val, atr=atr_val,
                    grid_state=grid_state_val,
                )
                self.journal.log_trade(trade)

                pnl_sign = "+" if net_pnl >= 0 else ""
                telegram_msg = (
                    f"{'💚' if net_pnl >= 0 else '🔴'} <b>Trade Closed — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"📈 BUY closed SELL position  |  Grid Level {grid_level}\n"
                    f"⏱ <b>Dur:</b> {duration_min}m\n"
                    f"🔵 <b>In:</b>  ${entry_price:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${exit_price:,.2f}\n"  # noqa: F821 - price from scope
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"•••\n"
                    f"💰 <b>Gross:</b> {pnl_sign}${gross_pnl:.2f}\n"
                    f"💸 <b>Fee:</b> -${total_fee:.2f}\n"
                    f"<b>📊 NET: {pnl_sign}${net_pnl:.2f}</b>\n"
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
                logger.info(f"REVERSE MATCH: SELL@${entry_price:,.2f} -> BUY@${price:,.2f} | PnL=${net_pnl:.2f}")
            else:
                self._open_buys[order_id] = buy_fill
                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=self.display_pair, side="BUY", entry_price=price, exit_price=price,
                    quantity=quantity, gross_pnl=0.0, fee=fee_est, net_pnl=-fee_est,
                    grid_level=grid_level, duration_min=0, rsi=rsi_val, bb_upper=bb_upper,
                    bb_lower=bb_lower, ema_200=ema_val, atr=atr_val, grid_state=grid_state_val,
                )
                self.journal.log_trade(trade)
                buy_msg = (
                    f"📈 <b>BUY Filled — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"💵 <b>Price:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"📊 Level {grid_level}  |  RSI: {rsi_val:.1f}\n"
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

            self._save_grid_state()
            self._grid_dirty = True

        elif side == "SELL":
            fee = fee_est
            matching_buy = self._open_buys.pop(order_id, None)
            if not matching_buy and self._open_buys:
                oldest_id = min(self._open_buys, key=lambda k: self._open_buys[k].timestamp)
                matching_buy = self._open_buys.pop(oldest_id)

            self._save_grid_state()

            if matching_buy:
                entry_price = matching_buy.price
                gross_pnl = (price - entry_price) * quantity
                duration_min = int((time_mod.time() - matching_buy.timestamp) / 60)
                total_fee = matching_buy.fee + fee
                net_pnl = gross_pnl - total_fee

                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=self.display_pair, side="SELL", entry_price=entry_price, exit_price=price,
                    quantity=quantity, gross_pnl=round(gross_pnl, 4), fee=round(total_fee, 4),
                    net_pnl=round(net_pnl, 4), grid_level=grid_level, duration_min=duration_min,
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower, ema_200=ema_val, atr=atr_val,
                    grid_state=grid_state_val,
                )
                self.journal.log_trade(trade)

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
                    f"🏦 <b>Eq:</b> ${equity:,.2f}  |  <b>Exp:</b> {exposure_pct:.0f}%\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
            else:
                self._unmatched_sells[order_id] = FillRecord(
                    order_id=order_id, side="SELL", price=price, quantity=quantity,
                    grid_level=grid_level, timestamp=time_mod.time(),
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower,
                    ema_200=ema_val, atr=atr_val, grid_state=grid_state_val, fee=fee,
                )
                self._save_grid_state()
                self.event_log.log("sell_buffered",
                    side="SELL", price=price, quantity=quantity, grid_level=grid_level,
                    fee_estimate=round(fee, 4), unmatched_sell_count=len(self._unmatched_sells),
                )
                telegram_msg = (
                    f"🟡 <b>SELL Filled (buffered) — {self.display_pair}</b>\n"
                    f"•••\n"
                    f"📉 SELL  |  Grid Level {grid_level}\n"
                    f"💵 <b>Price:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {self.base_asset}\n"
                    f"🔄 Buffered sells: {len(self._unmatched_sells)}\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}\n"
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

    # ── Trend Engine Methods ──

    def _check_trend_exits(self):
        if not self._last_price:
            return
        exits = self._position_manager.check_exits(self._last_price)
        for exit_info in exits:
            pos = self._position_manager.get_position(exit_info["order_id"])
            if pos:
                self._execute_trend_exit(pos, exit_info)

    def _execute_trend_exit(self, pos, exit_info: dict):
        exit_price = exit_info["exit_price"]
        reason = exit_info["reason"]
        amount = Decimal(str(pos.amount)).quantize(Decimal("0.01"))

        try:
            order_id = self.sell(self.exchange, self.trading_pair, amount, OrderType.LIMIT)
            logger.info(f"Trend SELL order placed: {amount} SOL @ {exit_price}")
        except Exception as e:
            logger.error(f"Trend sell failed: {e}")
            return

        self._position_manager.mark_exit_pending(pos.entry_order_id, str(order_id), reason)
        self._save_trend_state()

    def _evaluate_trend_signals(self):
        if not self._position_manager.can_open():
            return
        if self._trend_breaker.halted:
            return

        # ML gate: skip trend entry if classifier signals ranging regime (<0.5 confidence of trending)
        if self._ml_classifier is not None:
            if self._ml_regime == 2:  # Danger regime — no trend entries
                return
            if self._ml_confidence < 0.5:
                return

        candles = getattr(self, '_cached_candles', None)
        if candles is None or len(candles) < 200:
            return

        score = self._trend_manager.evaluate(candles, self._last_price)
        self._last_trend_score = score

        self.event_log.log("trend_score", total=score.total, max=7, details=score.details)

        if self._trend_manager.should_enter(score):
            confirmed = self._trend_manager.confirm_entry(score)
            self.event_log.log("trend_confirm", score=score.total, confirmed=confirmed,
                               pending=self._trend_manager._pending_ticks,
                               required=self._trend_manager._confirmation_ticks)
            if confirmed:
                self.event_log.log("trend_open_called", score=score.total)
                self._open_trend_position(candles, score)

    def _open_trend_position(self, candles: pd.DataFrame, score):
        try:
            sr_levels = self._trend_manager._sr.detect(candles)
            atr = ATR(14)
            closes = candles["close"]
            atr_val = None
            if "high" in candles.columns and "low" in candles.columns:
                atr_val = atr.calculate(candles["high"], candles["low"], closes)

            sl = self._trend_manager.calculate_stop_loss(self._last_price, sr_levels, atr_val)
            tp = self._trend_manager.calculate_take_profit(self._last_price, sl)
            capital = self._position_manager._capital

            amount = self._position_manager.calculate_position_size(self._last_price, sl)
            self.event_log.log("trend_open_debug", price=round(self._last_price, 2), sl=round(sl, 2),
                               tp=round(tp, 2), capital=capital, amount=amount, score=score.total)
            if amount <= 0:
                self.event_log.log("trend_open_blocked", reason="amount_le_0", amount=amount, capital=capital,
                                   sl_dist=round(abs(self._last_price - sl), 4))
                return
        except Exception as e:
            self.event_log.log("trend_open_error", error=str(e))
            return

        amount_dec = Decimal(str(amount)).quantize(Decimal("0.01"))
        try:
            self.event_log.log("trend_buy_attempt", amount=str(amount_dec), price=self._last_price)
            order_id = self.buy(self.exchange, self.trading_pair, amount_dec, OrderType.LIMIT)
            self.event_log.log("trend_buy_result", order_id=str(order_id), amount=str(amount_dec))
        except Exception as e:
            self.event_log.log("trend_buy_error", error=str(e))
            return

        entry_time = datetime.now(timezone.utc).isoformat()
        pos = self._position_manager.open_position(
            entry_order_id=str(order_id), entry_price=self._last_price,
            amount=amount, stop_loss=sl, take_profit=tp, entry_time=entry_time,
        )

        if pos:
            self._save_trend_state()
            self.event_log.log("trend_entry", amount=round(amount, 2), price=self._last_price,
                               sl=sl, tp=tp, score=score.total)
            self._trend_breaker.set_peak_equity(self._position_manager._capital + pos.amount * self._last_price)
        else:
            self.event_log.log("trend_entry_failed", reason="position_not_created")

    def _close_all_trend_positions(self):
        logger.warning("Closing all trend positions...")
        for pos in self._position_manager.get_all_positions():
            self._execute_trend_exit(pos, {
                "order_id": pos.entry_order_id,
                "exit_price": self._last_price or pos.entry_price,
                "reason": "manual_close",
            })

    def _save_trend_state(self):
        path = Path("data/trend_state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._position_manager.save_state(path)

    # ── Grid Helper Methods ──

    def _place_grid_orders(self, grid, current_price: float):
        self._cancel_all_orders("grid_refresh")
        connector = self.connectors.get(self.exchange)
        if not connector:
            return
        if hasattr(connector, 'ready') and not connector.ready:
            return

        usdt_bal = self._get_usdt_balance()
        base_bal = self._get_base_balance()
        equity = self._estimate_equity(current_price)
        exposure_pct = self.position_guard.base_exposure_pct(base_bal, current_price, equity)

        buys_placed = 0
        sells_placed = 0
        indicators = self._cached_indicators
        current_rsi = indicators[1] if indicators else None
        filled_buy_levels = set(fill.grid_level for fill in self._open_buys.values())
        filled_buy_prices = [fill.price for fill in self._open_buys.values()]
        min_spacing = grid.buy_spacing * 0.5 if grid.buy_spacing > 0 else 0.5

        for level in grid.buy_levels:
            if current_rsi and current_rsi > 60:
                continue
            if level["price"] >= current_price:
                continue
            if level["level"] in filled_buy_levels:
                continue
            if any(abs(level["price"] - fp) < min_spacing for fp in filled_buy_prices):
                continue
            order_usdt = level["price"] * level["quantity"]
            if not self.position_guard.can_place_order(
                current_base=base_bal, base_price=current_price,
                current_usdt=usdt_bal, order_usdt=order_usdt, equity=equity,
            ):
                continue
            buys_placed += 1
            client_order_id = self.buy(
                connector_name=self.exchange, trading_pair=self.trading_pair,
                amount=Decimal(str(level["quantity"])), order_type=OrderType.LIMIT,
                price=Decimal(str(level["price"])),
            )
            if client_order_id:
                self._grid_order_tracker.add(GridOrder(
                    order_id=client_order_id, level=level["level"],
                    side=OrderSide.BUY, price=level["price"], quantity=level["quantity"],
                ))

        # Place sells for each open buy at a price that guarantees profit.
        # Uses entry_price + sell_spacing, NOT bb.mid + sell_spacing.
        min_sell_spacing = grid.sell_spacing if grid.sell_spacing > 0 else grid.mid_price * 0.002
        for buy in list(self._open_buys.values()):
            if current_rsi and current_rsi < 40:
                continue
            profit_price = buy.price + min_sell_spacing
            sell_price = max(profit_price, current_price + min_sell_spacing * 0.5)
            sell_price = round(sell_price, 2)
            if sell_price <= current_price:
                continue
            if sell_price <= buy.price:
                continue
            base_balance = self._get_base_balance()
            if buy.quantity > base_balance:
                continue
            sells_placed += 1
            client_order_id = self.sell(
                connector_name=self.exchange, trading_pair=self.trading_pair,
                amount=Decimal(str(buy.quantity)), order_type=OrderType.LIMIT,
                price=Decimal(str(sell_price)),
            )
            if client_order_id:
                self._grid_order_tracker.add(GridOrder(
                    order_id=client_order_id, level=buy.grid_level,
                    side=OrderSide.SELL, price=sell_price, quantity=buy.quantity,
                ))

        logger.info(f"Grid: buys={buys_placed} sells={sells_placed} | open_buys={len(self._open_buys)} unmatched={len(self._unmatched_sells)}")

    def _cancel_all_orders(self, reason: str = "grid_refresh"):
        try:
            active = self.get_active_orders(self.exchange)
        except Exception:
            active = []
        for order in active:
            self.event_log.log("order_cancelled",
                order_id=str(order.client_order_id),
                side="BUY" if order.is_buy else "SELL",
                price=float(order.price), reason=reason,
            )
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)
        self._grid_order_tracker.cancel_all()
        self._grid_order_tracker.clear_history()

    def _regime_name(self) -> str:
        return {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}.get(self._ml_regime, 'UNKNOWN')

    def _determine_trigger_reason(self, prev_state, new_state, price, rsi, ema_200, bb) -> str:
        if new_state == GridState.PAUSED:
            if rsi > self.rsi_overbought:
                return f"rsi_overbought ({rsi:.1f} > {self.rsi_overbought})"
            if price < ema_200:
                return f"price_below_ema ({price:,.0f} < {ema_200:,.0f})"
            return "combined_pause_signal"
        if new_state == GridState.REACTIVATING:
            return f"rsi_oversold_bounce ({rsi:.1f} < {self.rsi_oversold}, near BB lower)"
        if new_state == GridState.DANGER:
            return f"ml_danger_regime (confidence={self._ml_confidence:.0%})"
        if new_state == GridState.ACTIVE:
            if prev_state == GridState.PAUSED:
                return f"conditions_cleared (rsi={rsi:.1f}, price>ema)"
            if prev_state == GridState.REACTIVATING:
                return f"bounce_confirmed (rsi={rsi:.1f})"
            return "initial_activation"
        return "unknown"

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
        try:
            s = self.journal.summary_today()
            sw = self.journal.summary_this_week()
            sm = self.journal.summary_this_month()
            
            ts = self._trend_journal.summary_today()
            tsw = self._trend_journal.summary_this_week()
            tsm = self._trend_journal.summary_this_month()

            def fmt(val):
                sign = "+" if (val or 0) >= 0 else ""
                return f"{sign}${val:.2f}" if val else "$0.00"

            base = getattr(self, '_base_capital', self.capital_usdt)
            growth_pct = ((equity - base) / base * 100) if base > 0 else 0
            
            total_net_today = s['net_pnl'] + ts['net_pnl']
            total_net_week = sw['net_pnl'] + tsw['net_pnl']
            total_net_month = sm['net_pnl'] + tsm['net_pnl']

            msg = (
                f"📅 <b>Daily Report — {pd.Timestamp.now(tz='UTC').strftime('%b %d, %Y')}</b>\n"
                f"•••\n"
                f"🤖 <b>GRID BOT</b>\n"
                f"📊 Trades: {s['total_trades']} (✅{s['winning']} / ❌{s['losing']}) Win: {s['win_rate']}%\n"
                f"💰 Gross: {fmt(s['gross_pnl'])}  |  💸 Fees: -${abs(s['total_fees']):.2f}\n"
                f"📈 Net Today: {fmt(s['net_pnl'])}\n"
                f"•••\n"
                f"📈 <b>TREND BOT</b>\n"
                f"📊 Trades: {ts['total_trades']} (✅{ts['winning']} / ❌{ts['losing']}) Win: {ts['win_rate']}%\n"
                f"💰 Gross: {fmt(ts['gross_pnl'])}  |  💸 Fees: -${abs(ts['total_fees']):.2f}\n"
                f"📈 Net Today: {fmt(ts['net_pnl'])}\n"
                f"•••\n"
                f"🏆 <b>COMBINED PNL</b>\n"
                f"📈 Net Today: <b>{fmt(total_net_today)}</b>\n"
                f"📆 Net Week:  {fmt(total_net_week)}\n"
                f"🗓 Net Month: {fmt(total_net_month)}\n"
                f"•••\n"
                f"🏦 <b>Eq:</b> ${equity:,.2f} ({growth_pct:+.1f}% vs base)\n"
                f"{'🤖 ML: ' + self._regime_name() + f' ({self._ml_confidence*100:.0f}%)' + chr(10) if self._ml_classifier else ''}"
                f"⚙️ <b>Env:</b> {self.env.upper()}"
            )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}")

    def _notify_state_change(self, new_state, prev_state, trigger_reason, price, rsi, bb, ema, atr, actual_spacing=0):
        state_key = new_state.value
        now = time_mod.time()
        last_alert = self._last_state_alert_time.get(state_key, 0)
        if now - last_alert < self._state_alert_cooldown:
            return
        self._last_state_alert_time[state_key] = now

        spacing = actual_spacing if actual_spacing > 0 else (atr * self.atr_multiplier if atr else 0)
        ml_line = f"🤖 ML: {self._regime_name()} ({self._ml_confidence*100:.0f}%)" if self._ml_classifier else ""

        if new_state == GridState.ACTIVE:
            msg = (
                f"🟢 <b>Grid ACTIVATED — {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📐 <b>Range:</b> ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
                f"📏 <b>Space:</b> ${spacing:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"{'🤖 ' + ml_line + chr(10) if ml_line else ''}"
                f"⚠️ <b>Why:</b> {trigger_reason}"
            )
        elif new_state == GridState.PAUSED:
            msg = (
                f"⏸️ <b>Grid PAUSED — {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.0f}\n"
                f"{'🤖 ' + ml_line + chr(10) if ml_line else ''}"
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
                f"{'🤖 ' + ml_line + chr(10) if ml_line else ''}"
                f"⚠️ <b>Why:</b> {trigger_reason}"
            )
        elif new_state == GridState.DANGER:
            msg = (
                f"🔴 <b>Grid DANGER MODE — {self.display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"🤖 ML: DANGER ({self._ml_confidence*100:.0f}%)\n"
                f"⚠️ Both engines paused — market whipsaw detected.\n"
                f"💤 Holding all positions until regime clears."
            )
        else:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.send(msg))
        except RuntimeError:
            pass

    # ── Grid State Persistence ──

    def _save_grid_state(self):
        try:
            if not self._state_file.parent.exists():
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "last_sod_reset": self._last_sod_reset,
                "open_buys": {
                    oid: {"order_id": f.order_id, "side": f.side, "price": f.price,
                          "quantity": f.quantity, "grid_level": f.grid_level, "timestamp": f.timestamp,
                          "rsi": f.rsi, "bb_upper": f.bb_upper, "bb_lower": f.bb_lower,
                          "ema_200": f.ema_200, "atr": f.atr, "grid_state": f.grid_state, "fee": f.fee}
                    for oid, f in self._open_buys.items()
                },
                "unmatched_sells": {
                    oid: {"order_id": f.order_id, "side": f.side, "price": f.price,
                          "quantity": f.quantity, "grid_level": f.grid_level, "timestamp": f.timestamp,
                          "rsi": f.rsi, "bb_upper": f.bb_upper, "bb_lower": f.bb_lower,
                          "ema_200": f.ema_200, "atr": f.atr, "grid_state": f.grid_state, "fee": f.fee}
                    for oid, f in self._unmatched_sells.items()
                }
            }
            tmp = self._state_file.with_suffix('.tmp')
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._state_file)
        except Exception as e:
            logger.error(f"Failed to save grid state: {e}")

    def _load_grid_state(self):
        try:
            if self._state_file.exists():
                with open(self._state_file, "r") as f:
                    data = json.load(f)
                    self._last_sod_reset = data.get("last_sod_reset", "")
                    for oid, d in data.get("open_buys", {}).items():
                        self._open_buys[oid] = FillRecord(**d)
                    for oid, d in data.get("unmatched_sells", {}).items():
                        self._unmatched_sells[oid] = FillRecord(**d)
                logger.info(f"Restored {len(self._open_buys)} open buys, {len(self._unmatched_sells)} unmatched sells")
        except Exception as e:
            logger.error(f"Failed to load grid state: {e}")

    def _cleanup_orphans(self):
        now = time_mod.time()
        ttl = 86400 * 7
        changed = False
        for oid in [k for k, f in self._unmatched_sells.items() if (now - f.timestamp) > ttl]:
            self._unmatched_sells.pop(oid)
            changed = True
        for oid in [k for k, f in self._open_buys.items() if (now - f.timestamp) > ttl]:
            self._open_buys.pop(oid)
            changed = True
        if changed:
            self._save_grid_state()

    # ── Thread-safe accessors for Telegram commands ──

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

    # ── Safe Telegram Error Helpers ──

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

    # ── Graceful Shutdown ──

    def on_stop(self):
        self._save_grid_state()
        self._save_trend_state()
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
        logger.info("Dual-engine strategy stopped — all orders cancelled")
