"""
TA Grid + Trend Dual-Engine Strategy

Runs grid bot and trend-following engine in one Hummingbot strategy.
Both engines share one connector but have isolated capital and state.
"""
import os
import asyncio
import gc as gc_mod
import logging
import math
import threading
import json
import traceback as traceback_mod
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from dotenv import load_dotenv
from pathlib import Path
from typing import Dict, Optional, List
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
from src.risk.bnb_rebalancer import BNBRebalancer
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

# Multi-pair support
try:
    from hummingbot_files.scripts.pair_engine import PairEngine, PairConfig
    from hummingbot_files.scripts.capital_manager import CapitalManager
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from pair_engine import PairEngine, PairConfig
    from capital_manager import CapitalManager

try:
    from src.ml.regime_classifier import RegimeClassifier
    from src.data.feature_engineering import calculate_technical_features
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

# Trading engine (Rust indicators + StrategyHost)
_trading_engine_enabled = os.environ.get("USE_TRADING_ENGINE", "").lower() == "true"


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
    OrderType = type("OrderType", (), {"LIMIT": "LIMIT", "LIMIT_MAKER": "LIMIT_MAKER"})
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
    trading_pair: str = Field(default="DOGE-USDT")

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
        # Load YAML config directly (self._load_config is on strategy, not config)
        cfg = {}
        for p in [Path("config/strategy.yaml"), Path(__file__).parent.parent.parent / "config" / "strategy.yaml"]:
            if p.exists():
                with open(p) as f:
                    cfg = yaml.safe_load(f)
                break
        pairs_cfg = cfg.get("pairs", [])
        if pairs_cfg:
            # Multi-pair mode
            for p in pairs_cfg:
                if p.get("enabled", True):
                    markets.setdefault(self.exchange, {})[p["symbol"]] = {}
        else:
            # Legacy single-pair mode
            pair = cfg.get("pair", self.trading_pair)
            markets[self.exchange] = {pair: {}}

        # Register signal exchange connector (Gate.io for wider altcoin support)
        signal_cfg = cfg.get("signal_copy", {})
        signal_ex = signal_cfg.get("exchange", "")
        if signal_ex and signal_cfg.get("enabled", False):
            signal_pairs = markets.setdefault(signal_ex, {})
            # Core pairs for price data + most common signal channel altcoins
            # Unlisted pairs use Gate.io REST API price fallback in signal_engine
            for pair in [
                "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
                "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "LINK-USDT",
                "HYPE-USDT", "SUI-USDT", "APT-USDT", "ARB-USDT", "OP-USDT",
            ]:
                signal_pairs[pair] = {}

        return markets


class TAGridTrendStrategy(StrategyV2Base):
    """Dual-engine strategy: grid bot + trend following."""

    FEATURE_COLS = [
        'returns', 'volatility_ratio', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
        'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
        'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
    ]

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

        # Call parent constructor (required for v2)
        super().__init__(connectors, config)

        # Load configuration from YAML
        cfg = self._load_config()
        grid_cfg = cfg.get("grid", {})
        ind_cfg = cfg.get("indicators", {})
        risk_cfg = cfg.get("risk", {})
        trend_cfg = cfg.get("trend", {})
        fee_cfg = cfg.get("fee_optimization", {})
        self._use_limit_maker = fee_cfg.get("use_limit_maker", True)
        self._bnb_rebalancer = BNBRebalancer(
            bnb_min_usdt=fee_cfg.get("bnb_min_usdt", 10.0),
            bnb_target_usdt=fee_cfg.get("bnb_target_usdt", 20.0),
            bnb_max_usdt=fee_cfg.get("bnb_max_usdt", 50.0),
        )

        # Multi-pair support: parse pairs from config or fall back to legacy single pair
        pairs_cfg = cfg.get("pairs", [])
        if not pairs_cfg:
            # Legacy single-pair fallback
            pair = cfg.get("pair", config.trading_pair)
            step = grid_cfg.get("step_size", config.step_size)
            pairs_cfg = [{"symbol": pair, "step_size": step, "enabled": True}]

        self._open_buys: Dict[str, dict[str, FillRecord]] = {}
        self._unmatched_sells: Dict[str, dict[str, FillRecord]] = {}

        self.pairs: Dict[str, PairEngine] = {}
        for p in pairs_cfg:
            pc = PairConfig(symbol=p["symbol"], step_size=p["step_size"], tick_size=p.get("tick_size", 0.01), enabled=p.get("enabled", True))
            if pc.enabled:
                self.pairs[pc.symbol] = PairEngine(pc, state_dir=Path("data"))
                self._open_buys[pc.symbol] = {}
                self._unmatched_sells[pc.symbol] = {}

        # Backward compat: set trading_pair to first enabled pair
        self.trading_pair = list(self.pairs.keys())[0] if self.pairs else config.trading_pair

        # Environment
        self.env = os.environ.get("ENV", config.env)
        self.is_testnet = self.env == "paper"

        # Pair helpers (backward compat - will be replaced by engine properties)
        self.base_asset = self.trading_pair.split("-")[0]
        self.binance_symbol = self.trading_pair.replace("-", "")
        self.display_pair = self.trading_pair.replace("-", "/")

        # ── Health server ──
        start_health_server(port=8080)

        # ── Grid engine configuration ──
        self.levels = int(os.environ.get("GRID_LEVELS", grid_cfg.get("levels", config.levels)))
        self.capital_usdt = float(os.environ.get("GRID_CAPITAL_USDT", grid_cfg.get("capital_usdt", config.capital_usdt)))
        self.min_reserve = float(os.environ.get("MIN_USDT_RESERVE", grid_cfg.get("min_usdt_reserve", config.min_reserve)))
        self.order_refresh_time = grid_cfg.get("order_refresh_time", config.order_refresh_time)

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

        # Initialize indicators for each pair engine
        for engine in self.pairs.values():
            engine.bb = BollingerBands(self.bb_period, self.bb_std)
            engine.rsi = RSI(self.rsi_period)
            engine.ema = EMA(self.ema_period)
            engine.atr = ATR(self.atr_period, self.atr_multiplier)

        # Backward compat: set single-pair indicators
        first_engine = list(self.pairs.values())[0] if self.pairs else None
        if first_engine:
            self.bb = first_engine.bb
            self.rsi = first_engine.rsi
            self.ema = first_engine.ema
            self.atr = first_engine.atr
        else:
            self.bb = BollingerBands(self.bb_period, self.bb_std)
            self.rsi = RSI(self.rsi_period)
            self.ema = EMA(self.ema_period)
            self.atr = ATR(self.atr_period, self.atr_multiplier)

        # Grid managers per pair
        self.grid_managers: Dict[str, GridManager] = {}
        self.state_machines: Dict[str, GridStateMachine] = {}
        self.grid_order_trackers: Dict[str, OrderTracker] = {}
        self.grid_circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.position_guards: Dict[str, PositionGuard] = {}

        for symbol, engine in self.pairs.items():
            self.grid_managers[symbol] = GridManager(
                levels=self.levels,
                capital_usdt=self.capital_usdt,  # Will be managed by CapitalManager
                min_reserve=self.min_reserve,
                step_size=engine.step_size,
                tick_size=engine.tick_size,
                spacing_multiplier=self.atr_multiplier,
            )
            self.state_machines[symbol] = GridStateMachine()
            self.grid_order_trackers[symbol] = OrderTracker()
            self.grid_circuit_breakers[symbol] = CircuitBreaker(
                float(os.environ.get("MAX_DRAWDOWN_PCT", risk_cfg.get("max_drawdown_pct", config.max_drawdown_pct))),
                risk_cfg.get("daily_loss_limit_pct", config.daily_loss_limit_pct),
            )
            self.grid_circuit_breakers[symbol].set_peak_equity(self.capital_usdt)
            self.grid_circuit_breakers[symbol].set_start_of_day_equity(self.capital_usdt)
            self.position_guards[symbol] = PositionGuard(
                float(os.environ.get("MAX_BASE_EXPOSURE_PCT", risk_cfg.get("max_base_exposure_pct", config.max_base_exposure_pct))),
                self.min_reserve, self.capital_usdt,
            )

        # Backward compat: set single-pair instances
        if first_engine:
            self.grid_manager = self.grid_managers[first_engine.symbol]
            self.state_machine = self.state_machines[first_engine.symbol]
            self._grid_order_tracker = self.grid_order_trackers[first_engine.symbol]
            self.grid_circuit_breaker = self.grid_circuit_breakers[first_engine.symbol]
            self.position_guard = self.position_guards[first_engine.symbol]
            self.step_size = first_engine.step_size
        else:
            self.grid_manager = GridManager(
                levels=self.levels,
                capital_usdt=self.capital_usdt,
                min_reserve=self.min_reserve,
                step_size=config.step_size,
                spacing_multiplier=self.atr_multiplier,
            )
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
            self.step_size = float(grid_cfg.get("step_size", config.step_size))

        self._base_capital = self.capital_usdt
        self._initial_equity: Dict[str, float] = {}
        self._peak_equity = self.capital_usdt
        self._grid_dirty = True
        self._last_state_alert_time: dict[str, float] = {}
        self._state_alert_cooldown = 900
        self._manual_pause = False
        self._last_sod_reset: Optional[str] = None
        self._fee_rate: float = 0.00075
        self._overtrading_alerted_today: str = ""
        self._active_buy_spacing = 0.0
        self._active_sell_spacing = 0.0
        self._last_grid_place_time: Dict[str, float] = {}
        self._min_grid_refresh_sec = 300

        # ── Candle feeds per pair ──
        self.candle_feeds: Dict[str, CandleFeed] = {}
        for symbol, engine in self.pairs.items():
            self.candle_feeds[symbol] = CandleFeed(
                symbol=engine.binance_symbol,
                interval=trend_cfg.get("timeframe", "1h"),
                testnet=self.is_testnet,
            )

        # BTC candle feed for correlation gate (always needed, even when BTC trading is disabled)
        btc_symbol = "BTC-USDT"
        if btc_symbol not in self.candle_feeds:
            self.candle_feeds[btc_symbol] = CandleFeed(
                symbol="BTCUSDT",
                interval=trend_cfg.get("timeframe", "1h"),
                testnet=self.is_testnet,
            )

        # Backward compat: set single-pair candle feed
        if first_engine:
            self.candle_feed = self.candle_feeds[first_engine.symbol]
        else:
            self.candle_feed = CandleFeed(
                symbol=self.binance_symbol,
                interval=trend_cfg.get("timeframe", "1h"),
                testnet=self.is_testnet,
            )

        self._last_candle_time: Dict[str, Optional[pd.Timestamp]] = {}
        self._cached_indicators: Dict[str, Optional[tuple]] = {}
        self._cached_candles: Dict[str, Optional[pd.DataFrame]] = {}

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
            exit_signal_threshold=trend_cfg.get("exit_signal_threshold", 2),
        )

        trend_capital = float(os.environ.get("TREND_CAPITAL_USDT", trend_cfg.get("capital", 0)))
        self._trend_capital = trend_capital
        self._trend_max_total_positions = trend_cfg.get("max_total_positions", trend_cfg.get("max_positions", 2))
        self._position_managers: Dict[str, PositionManager] = {}
        for symbol in self.pairs:
            self._position_managers[symbol] = PositionManager(
                capital=trend_capital,
                max_positions=trend_cfg.get("max_positions", 2),
                risk_per_trade_pct=trend_cfg.get("risk_per_trade_pct", 2.0),
                max_position_pct=trend_cfg.get("max_position_pct", 25.0),
                trailing_stop_pct=trend_cfg.get("trailing_stop_pct", 1.5),
                trailing_activation_pct=trend_cfg.get("trailing_activation_pct", 1.5),
            )
        # Backward compat
        self._position_manager = list(self._position_managers.values())[0] if self._position_managers else PositionManager(
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
        self._trend_breaker.set_peak_equity(trend_capital)
        self._trend_breaker.set_start_of_day_equity(trend_capital)

        # ── ML Regime Classifier (per-pair) ──
        self._ml_models: Dict[str, RegimeClassifier] = {}
        self._ml_predictions: Dict[str, tuple] = {}
        self._ml_prediction_history: Dict[str, list] = {}
        self._ml_gc_counter = 0
        self._ml_model_mtimes: Dict[str, float] = {}

        if ML_AVAILABLE:
            for symbol in self.pairs:
                self._ml_predictions[symbol] = (None, 0.0, 0.0)
                self._ml_prediction_history[symbol] = []

            # Ensure BTC-USDT always has ML predictions (systemic signal for correlation gate)
            btc_symbol = "BTC-USDT"
            if btc_symbol not in self._ml_predictions:
                self._ml_predictions[btc_symbol] = (None, 0.0, 0.0)
                self._ml_prediction_history[btc_symbol] = []

            for symbol in list(self._ml_predictions.keys()):
                model_path = Path(f"models/regime_{symbol}.pkl")
                if model_path.exists():
                    try:
                        clf = RegimeClassifier(model_path=str(model_path))
                        clf.load_model()
                        self._ml_models[symbol] = clf
                        self._ml_model_mtimes[symbol] = os.path.getmtime(str(model_path))
                        logger.info(f"ML model loaded for {symbol} from {model_path}")
                    except Exception as e:
                        logger.warning(f"ML model load failed for {symbol}: {e}")
                else:
                    logger.warning(f"No ML model for {symbol} (rule-based fallback)")

            # Startup summary
            loaded = [s for s in self._ml_predictions if s in self._ml_models]
            missing = [s for s in self._ml_predictions if s not in self._ml_models]
            logger.info(
                f"ML Regime Classifier: {len(loaded)}/{len(self._ml_predictions)} pairs loaded"
                + (f" — missing: {missing}" if missing else "")
            )

            # Telegram ML status notification (deferred — self.telegram not yet initialized)
            self._ml_startup_msg = None
            if self._ml_models:
                self._ml_startup_msg = f"🧠 <b>ML Models Loaded: {len(loaded)}/{len(self._ml_predictions)}</b>\n"
                for s in loaded:
                    self._ml_startup_msg += f"  ✅ {s}\n"
                for s in missing:
                    self._ml_startup_msg += f"  ❌ {s} (rule-based)\n"
        else:
            for symbol in self.pairs:
                self._ml_predictions[symbol] = (None, 0.0, 0.0)
            logger.info("ML Regime Classifier: sklearn not available (rule-based only)")

        # Backward compat: single-pair ML classifier reference
        self._ml_classifier = list(self._ml_models.values())[0] if self._ml_models else None

        # ── Shared state ──
        self._last_price: Dict[str, float] = {}
        self._last_trend_score = None
        self._trend_force_close: bool = False
        self._tick_count = 0
        self._trend_tick_count: int = 0
        self._state_lock = threading.Lock()
        self._correlation_gate_active: Dict[str, bool] = {}

        # ── Capital Manager ──
        total_cap = float(grid_cfg.get("capital_usdt", config.capital_usdt)) + float(trend_cfg.get("capital", 5000))
        max_per_pair = float(trend_cfg.get("max_position_pct", 25.0)) / 100.0
        self._capital_mgr = CapitalManager(total_capital=total_cap, state_dir=Path("data"), max_per_pair=max_per_pair)
        self._capital_mgr.load()

        # Initialize per-pair state containers
        for symbol in self.pairs:
            self._last_price[symbol] = 0.0
            self._last_candle_time[symbol] = None
            self._cached_indicators[symbol] = None
            self._cached_candles[symbol] = None

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

        # Send deferred ML startup notification (telegram not available during ML init)
        if getattr(self, '_ml_startup_msg', None):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(self._ml_startup_msg))
                self._ml_startup_msg = None
            except RuntimeError:
                pass

        # System monitor
        self._sys_monitor = SystemAlertMonitor(self.telegram, interval_sec=300)
        self._sys_monitor.start()

        # ── Signal Copy Engine ──
        self._signal_engine = None
        signal_cfg = cfg.get("signal_copy", {})
        self.signal_exchange = signal_cfg.get("exchange", self.exchange)
        if signal_cfg.get("enabled", False):
            from src.signals.signal_engine import SignalEngine

            def _signal_btc_regime():
                pred = self._ml_predictions.get("BTC-USDT", (None, 0.0, 0.0))
                regime_map = {0: "RANGING", 1: "TRENDING", 2: "DANGER"}
                regime_str = regime_map.get(pred[0], "RANGING") if pred[0] is not None else "RANGING"
                return (regime_str, pred[1], pred[2])

            self._signal_engine = SignalEngine(
                config=signal_cfg,
                btc_regime_fn=_signal_btc_regime,
                telegram_send_fn=lambda msg: asyncio.get_event_loop().create_task(self.telegram.send(msg)) if self.telegram else None,
                buy_fn=lambda symbol, amount, price: self.buy(self.signal_exchange, symbol, amount, OrderType.LIMIT_MAKER, price=price),
                sell_fn=lambda symbol, amount, price: self.sell(self.signal_exchange, symbol, amount, OrderType.LIMIT_MAKER, price=price),
                get_price_fn=lambda symbol: self._get_signal_price(symbol),
            )
            self._signal_engine.start_listener()
            logger.info("Signal Copy Engine initialized")

        # Startup Telegram alert
        try:
            active_pairs = ", ".join(self.pairs.keys())
            active_engines = "Grid + Trend"
            signal_channels = 0
            signal_audit = False
            if self._signal_engine:
                active_engines += " + Signal"
                _ch = os.environ.get("SIGNAL_CHANNEL_IDS", "")
                signal_channels = len([c for c in _ch.split(",") if c.strip()])
                signal_audit = signal_cfg.get("audit_mode", False)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.telegram.alert_startup(
                    self.env, self.capital_usdt,
                    pairs=active_pairs, engines=active_engines,
                    grid_levels=self.levels,
                    signal_channels=signal_channels,
                    audit_mode=signal_audit,
                ))
        except RuntimeError:
            pass

        # ── Load state for each pair ──
        for symbol, engine in self.pairs.items():
            self._load_grid_state(engine)
            trend_path = engine.trend_state_path
            if trend_path.exists():
                self._position_managers[symbol].load_state(trend_path)

        # Backward compat: load legacy state files
        legacy_grid_path = Path("data/grid_state.json")
        if legacy_grid_path.exists() and first_engine:
            self._load_grid_state(first_engine, legacy_path=legacy_grid_path)

        legacy_trend_path = Path("data/trend_state.json")
        if legacy_trend_path.exists() and first_engine:
            self._position_managers[first_engine.symbol].load_state(legacy_trend_path)

        # Reconcile CapitalManager with restored positions
        self._capital_mgr._allocations.clear()
        for symbol, pm in self._position_managers.items():
            for pos in pm.get_all_positions():
                notional = pos.amount * pos.entry_price
                self._capital_mgr.allocate(symbol, "trend", notional)

        logger.info(f"Dual-engine strategy started on {self.exchange} with {len(self.pairs)} pair(s)")

        # ── Trading Engine (Rust indicators + StrategyHost) ──
        self._trading_host = None
        self._trading_engine_warmed_up: Dict[str, bool] = {}
        if _trading_engine_enabled:
            try:
                from src.trading_engine.adapter.hummingbot_integration import init_trading_engine
                connector = self.connectors.get(self.exchange)
                if connector:
                    pairs = list(self.pairs.keys())
                    te_config = {
                        "grid_levels": self.levels,
                        "capital": self.capital_usdt,
                        "spacing_atr_multiplier": self.atr_multiplier,
                        "ema_period": self.ema_period,
                        "rsi_period": self.rsi_period,
                        "atr_period": self.atr_period,
                        "bollinger_period": self.bb_period,
                        "bollinger_std_dev": self.bb_std,
                        "order_refresh_seconds": self.order_refresh_time,
                        "rsi_oversold": self.rsi_oversold,
                        "rsi_overbought": self.rsi_overbought,
                    }
                    self._trading_host = init_trading_engine(connector, self, pairs, te_config)
                    self._trading_engine_warmed_up = {s: False for s in pairs}
                    logger.info(f"Trading engine ENABLED — Rust indicators active for {len(pairs)} pairs: {pairs}")
            except Exception as e:
                logger.error(f"Trading engine init failed, falling back to Python indicators: {e}")
                import traceback as _tb
                logger.error(_tb.format_exc())
                self._trading_host = None

        # Time drift check — warn if system clock is out of sync with Binance
        self._check_time_drift()

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

    # ── Time Drift Check ──

    def _check_time_drift(self):
        try:
            start = time_mod.time() * 1000
            with urllib.request.urlopen("https://api.binance.com/api/v3/time", timeout=5) as resp:
                server_time = json.loads(resp.read().decode())["serverTime"]
            end = time_mod.time() * 1000
            latency = (end - start) / 2
            drift = abs((start + latency) - server_time)
            if drift > 1500:
                logger.warning(f"Time drift detected: {drift:.0f}ms from Binance. Signed trades may fail (-1021). Sync NTP.")
            else:
                logger.info(f"Time sync OK: {drift:.0f}ms drift, {latency:.0f}ms latency")
        except Exception as e:
            logger.info(f"Time drift check skipped: {e}")

    # ── Force-Ready Watchdog ──

    def _get_signal_price(self, symbol: str) -> float:
        """Get mid price from the signal exchange connector."""
        try:
            connector = self.connectors.get(self.signal_exchange)
            if connector:
                price = connector.get_mid_price(symbol)
                if price:
                    return float(price)
        except Exception:
            pass
        return self._last_price.get(symbol, 0)

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

            # Update current price for all pairs
            connector = self.connectors.get(self.exchange)
            if not connector:
                return

            for symbol, engine in self.pairs.items():
                try:
                    mid_price = connector.get_mid_price(symbol)
                    if mid_price:
                        self._last_price[symbol] = float(mid_price)
                        engine.last_price = float(mid_price)
                except Exception:
                    pass

            # ── Grid Engine (all pairs) ──
            for symbol, engine in self.pairs.items():
                self._grid_tick(engine)

            # ── Trend Engine (all pairs) ──
            for symbol, engine in self.pairs.items():
                self._trend_tick(engine)

            # ── Signal Copy Engine ──
            if self._signal_engine is not None:
                try:
                    signal_connector = self.connectors.get(self.signal_exchange)
                    self._signal_engine.tick(signal_connector)
                except Exception as e:
                    logger.error(f"Signal engine tick error: {e}")

            # ── Periodic orphan cleanup ──
            if self._tick_count % 1000 == 0:
                self._cleanup_orphans()

            # Update health (using first pair for backward compat)
            first_engine = list(self.pairs.values())[0] if self.pairs else None
            total_trend_positions = sum(pm.open_count for pm in self._position_managers.values()) if self._position_managers else self._position_manager.open_count
            if first_engine:
                update_health(
                    grid_state=self.state_machines[first_engine.symbol].state.value,
                    trend_healthy=not self._trend_breaker.halted,
                    trend_positions=total_trend_positions,
                    last_signal_score=self._last_trend_score.total if self._last_trend_score else 0,
                )
            else:
                update_health(
                    grid_state=self.state_machine.state.value,
                    trend_healthy=not self._trend_breaker.halted,
                    trend_positions=total_trend_positions,
                    last_signal_score=self._last_trend_score.total if self._last_trend_score else 0,
                )
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"CRASH in on_tick: {e}\n{tb}")
            self._safe_telegram_crash("on_tick", str(e), tb)

    # ── Grid Engine Tick ──

    def _grid_tick(self, engine: PairEngine):
        """Process grid tick for a single pair."""
        circuit_breaker = self.grid_circuit_breakers[engine.symbol]
        if circuit_breaker.halted:
            return

        if self._manual_pause:
            if self._grid_dirty:
                self._cancel_all_orders(engine, "manual_pause")
                self._grid_dirty = False
            return

        now = pd.Timestamp.now(tz="UTC")

        # Start-of-day equity reset + daily report (only for first pair to avoid spam)
        today_str = now.strftime("%Y-%m-%d")
        if self._last_sod_reset != today_str and engine.symbol == list(self.pairs.keys())[0]:
            equity = self._estimate_equity(
                (self._cached_indicators.get(engine.symbol) or [None, None, None, None, 0])[4] or 0
            )
            circuit_breaker.set_start_of_day_equity(equity)
            # Also reset trend circuit breaker start-of-day equity
            trend_equity = self._estimate_trend_equity()
            self._trend_breaker.set_start_of_day_equity(trend_equity)
            self._last_sod_reset = today_str
            self.event_log.log("daily_reset", equity=round(equity, 2))
            logger.info(f"Start-of-day equity reset: ${equity:.2f}")
            self._send_daily_report(equity)

        # Overtrading check (only for first pair)
        if self._overtrading_alerted_today != today_str and engine.symbol == list(self.pairs.keys())[0]:
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

        # Fetch candles for this pair
        should_fetch = (
            self._last_candle_time.get(engine.symbol) is None or
            now - self._last_candle_time[engine.symbol] >= pd.Timedelta(minutes=55)
        )

        if should_fetch:
            try:
                df = self.candle_feeds[engine.symbol].fetch_candles(limit=250)
            except Exception as e:
                logger.error(f"Candle fetch failed for {engine.symbol}: {e}")
                return

            if df.empty or len(df) < self.bb_period:
                return

            closes = df["close"]
            highs = df["high"]
            lows = df["low"]
            current_price = float(closes.iloc[-1])

            bb_result = engine.bb.calculate(closes)
            rsi_value = engine.rsi.calculate(closes)
            ema_value = engine.ema.calculate(closes)
            atr_value = engine.atr.calculate(highs, lows, closes)

            if any(v is None for v in [bb_result, rsi_value, ema_value, atr_value]):
                return

            self._cached_indicators[engine.symbol] = (bb_result, rsi_value, ema_value, atr_value, current_price)
            self._last_candle_time[engine.symbol] = now
            self._cached_candles[engine.symbol] = df
            self._grid_dirty = True

            # ML Prediction (per-pair, throttled to 60s)
            now_ts = time_mod.time()
            _, _, last_ts = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
            if now_ts - last_ts >= 60:
                self._run_ml_prediction(engine.symbol)

            # BTC correlation gate: fetch candles + run ML prediction for BTC-USDT
            # (BTC may be disabled as a trading pair but is always needed as systemic signal)
            if engine.symbol == list(self.pairs.keys())[0] and "BTC-USDT" not in self.pairs:
                btc_ts_key = "BTC-USDT"
                has_feed = btc_ts_key in self.candle_feeds
                _, _, btc_last_ts = self._ml_predictions.get(btc_ts_key, (None, 0.0, 0.0))
                if now_ts - btc_last_ts >= 60 and has_feed:
                    try:
                        btc_df = self.candle_feeds[btc_ts_key].fetch_candles(limit=250)
                        if btc_df is None or btc_df.empty:
                            self.event_log.log("btc_correlation_fetch", status="empty", pair="BTC-USDT")
                        elif len(btc_df) < 50:
                            self.event_log.log("btc_correlation_fetch", status="insufficient", rows=len(btc_df), pair="BTC-USDT")
                        else:
                            self._cached_candles[btc_ts_key] = btc_df
                            self._run_ml_prediction(btc_ts_key)
                            btc_regime, btc_conf, _ = self._ml_predictions.get(btc_ts_key, (None, 0.0, 0.0))
                            regime_names = {0: "RANGING", 1: "TRENDING", 2: "DANGER"}
                            self.event_log.log("btc_correlation_gate",
                                regime=regime_names.get(btc_regime, "UNKNOWN"),
                                confidence=round(btc_conf, 3),
                                raw_regime=btc_regime,
                                pair="BTC-USDT")
                    except Exception as e:
                        self.event_log.log("btc_correlation_fetch", status="error", error=str(e), pair="BTC-USDT")
                elif not has_feed:
                    self.event_log.log("btc_correlation_fetch", status="no_feed", pair="BTC-USDT")

            # BNB rebalancer check (every indicator refresh cycle)
            if engine.symbol == list(self.pairs.keys())[0]:
                try:
                    bnb_bal = 0.0
                    connector = self.connectors.get(self.exchange)
                    if connector and hasattr(connector, 'ready') and connector.ready:
                        try:
                            balances = connector.balance
                            if "BNB" in balances:
                                bnb_qty = float(balances["BNB"].total)
                                bnb_price = float(self._last_price.get("BNB-USDT", 600))
                                bnb_bal = bnb_qty * bnb_price
                        except Exception:
                            pass
                    result = self._bnb_rebalancer.evaluate(bnb_bal, available_usdt=self._get_usdt_balance(engine))
                    if result.action == "buy":
                        logger.info(f"BNB rebalancer: {result.reason} — buying ${result.amount_usdt:.2f}")
                        self.event_log.log("bnb_rebalance", action="buy", amount=result.amount_usdt, reason=result.reason)
                    elif result.action == "sell":
                        logger.info(f"BNB rebalancer: {result.reason} — selling ${result.amount_usdt:.2f}")
                        self.event_log.log("bnb_rebalance", action="sell", amount=result.amount_usdt, reason=result.reason)
                except Exception as e:
                    logger.debug(f"BNB rebalancer check skipped: {e}")

            ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))

            # Keep inline state machine in sync (used for event logging + health endpoint)
            # even when trading engine handles actual grid decisions.
            self.state_machines[engine.symbol].evaluate(
                price=current_price, rsi=rsi_value, ema_200=ema_value,
                bb_lower=bb_result.lower, bb_upper=bb_result.upper,
                rsi_overbought=self.rsi_overbought, rsi_oversold=self.rsi_oversold,
                ml_regime=ml_regime if ml_regime is not None else 0,
                ml_confidence=ml_confidence,
            )


            # Use 6 decimal places for price-based indicators to preserve
            # precision for low-price assets like DOGE (~$0.10).
            # round(0.10138, 2)=0.1 loses all variance; round(0.10138, 6)=0.101380.
            _dp = 6  # decimal places for price-type values
            self.event_log.log("indicators_updated",
                rsi=round(rsi_value, 2), bb_upper=round(bb_result.upper, _dp),
                bb_mid=round(bb_result.mid, _dp), bb_lower=round(bb_result.lower, _dp),
                ema_200=round(ema_value, _dp), atr=round(atr_value, _dp),
                price=round(current_price, _dp), grid_state=self.state_machines[engine.symbol].state.value,
                ml_confidence=round(ml_confidence, 3),
                ml_regime=ml_regime if ml_regime is not None else 0,
                pair=engine.symbol,
            )

            # ── Feed bar to trading engine (Rust indicators + StrategyHost) ──
            if self._trading_host is not None:
                from src.trading_engine.adapter.hummingbot_integration import tick_trading_engine
                # Warm up Rust indicators from full candle history on first fetch
                if not self._trading_engine_warmed_up.get(engine.symbol, False):
                    for strategy in self._trading_host.strategies:
                        if hasattr(strategy, 'ema') and strategy.instrument_id == engine.symbol:
                            for i in range(len(df)):
                                c = float(df["close"].iloc[i])
                                h = float(df["high"].iloc[i])
                                l = float(df["low"].iloc[i])
                                strategy.ema.update(c)
                                strategy.rsi.update(c)
                                strategy.atr.update_bar(c, h, l, c)
                                strategy.bollinger.update(c)
                            logger.info(f"Rust indicators warmed up for {engine.symbol} from {len(df)} historical bars")
                            break
                    self._trading_engine_warmed_up[engine.symbol] = True
                # Feed latest bar to trading engine
                bar = {
                    "open": float(df["open"].iloc[-1]),
                    "high": float(df["high"].iloc[-1]),
                    "low": float(df["low"].iloc[-1]),
                    "close": current_price,
                    "volume": float(df["volume"].iloc[-1]) if "volume" in df.columns else 0.0,
                    "timestamp": int(time_mod.time()),
                }
                tick_trading_engine(self._trading_host, engine.symbol, bar)
        else:
            if self._cached_indicators.get(engine.symbol) is None:
                return
            bb_result, rsi_value, ema_value, atr_value, current_price = self._cached_indicators[engine.symbol]

        # Skip inline grid logic when trading engine handles it
        if self._trading_host is not None:
            return

        # Evaluate state
        state_machine = self.state_machines[engine.symbol]
        prev_state = state_machine.state
        ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
        new_state = state_machine.evaluate(
            price=current_price, rsi=rsi_value, ema_200=ema_value,
            bb_lower=bb_result.lower, bb_upper=bb_result.upper,
            rsi_overbought=self.rsi_overbought, rsi_oversold=self.rsi_oversold,
            ml_regime=ml_regime if ml_regime is not None else 0,
            ml_confidence=ml_confidence,
        )

        if new_state != prev_state:
            trigger_reason = self._determine_trigger_reason(
                prev_state, new_state, current_price, rsi_value, ema_value, bb_result,
                ml_confidence=ml_confidence,
            )
            logger.info(f"Grid state for {engine.symbol}: {prev_state.value} -> {new_state.value} ({trigger_reason})")
            self.event_log.log("state_changed",
                previous_state=prev_state.value, new_state=new_state.value,
                trigger_reason=trigger_reason, price=round(current_price, 2),
                rsi=round(rsi_value, 2), ema_200=round(ema_value, 2),
                pair=engine.symbol,
            )
            self._notify_state_change(new_state, prev_state, trigger_reason, current_price, rsi_value, bb_result, ema_value, atr_value, self._active_buy_spacing, engine)
            self._grid_dirty = True

        if state_machine.is_paused:
            if self._grid_dirty:
                self._cancel_all_orders(engine, "state_paused")
                self._grid_dirty = False
            return

        equity = self._estimate_equity(current_price, engine)
        circuit_breaker.update_peak(equity)
        if circuit_breaker.check(equity) or circuit_breaker.check_daily(equity):
            self._cancel_all_orders(engine, "circuit_breaker")
            self.event_log.log("circuit_breaker",
                drawdown_pct=round(((self._peak_equity - equity) / self._peak_equity) * 100, 2) if self._peak_equity > 0 else 0,
                peak_equity=round(self._peak_equity, 2), current_equity=round(equity, 2),
                pair=engine.symbol,
            )
            logger.critical(f"Grid circuit breaker triggered for {engine.symbol}!")
            set_halted("circuit_breaker")
            return

        if self._grid_dirty:
            now_ts = time_mod.time()
            elapsed = now_ts - self._last_grid_place_time.get(engine.symbol, 0)
            if elapsed < self._min_grid_refresh_sec:
                return
            live_equity = self._estimate_equity(current_price, engine)
            if engine.symbol not in self._initial_equity:
                self._initial_equity[engine.symbol] = live_equity
            init_eq = self._initial_equity[engine.symbol]
            growth_ratio = live_equity / init_eq if init_eq > 0 else 1.0
            compound_capital = self._base_capital * growth_ratio
            compound_capital = max(compound_capital, self._base_capital)
            # Scale grid capital by ML regime confidence
            ml_regime_gr, ml_confidence_gr, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
            if ml_regime_gr == 0:  # RANGING
                scale = 1.0 if ml_confidence_gr > 0.7 else 0.8
            elif ml_regime_gr == 1:  # TRENDING
                scale = 0.6
            else:
                scale = 1.0
            compound_capital *= scale
            self.grid_managers[engine.symbol].capital_usdt = compound_capital
            grid = self.grid_managers[engine.symbol].calculate_grid(bb_result, atr_value)
            self._active_buy_spacing = grid.buy_spacing
            self._active_sell_spacing = grid.sell_spacing
            self._place_grid_orders(grid, current_price, engine)
            self._last_grid_place_time[engine.symbol] = now_ts
            logger.info(f"Grid updated for {engine.symbol}: buy_spacing=${grid.buy_spacing:.2f}, sell_spacing=${grid.sell_spacing:.2f} | compound=${compound_capital:.2f}")
            deployed = sum(l["price"] * l["quantity"] for l in grid.buy_levels)
            self.event_log.log("grid_recalculated",
                bb_upper=round(bb_result.upper, 2), bb_lower=round(bb_result.lower, 2),
                buy_spacing=round(grid.buy_spacing, 2), sell_spacing=round(grid.sell_spacing, 2),
                num_buy_levels=len(grid.buy_levels), num_sell_levels=len(grid.sell_levels),
                total_capital_deployed=round(deployed, 2),
                pair=engine.symbol,
            )
            self._grid_dirty = False

    # ── ML Prediction ──

    def _run_ml_prediction(self, pair: str):
        """Run ML regime prediction for a single pair. Updates self._ml_predictions[pair]."""

        if pair not in self._ml_models:
            return

        # Hot-reload: check if model file was updated
        model_path = Path(f"models/regime_{pair}.pkl")
        if model_path.exists():
            current_mtime = os.path.getmtime(str(model_path))
            last_mtime = self._ml_model_mtimes.get(pair, 0.0)
            if current_mtime > last_mtime:
                try:
                    new_clf = RegimeClassifier(model_path=str(model_path))
                    new_clf.load_model()
                    self._ml_models[pair] = new_clf
                    self._ml_model_mtimes[pair] = current_mtime
                    logger.info(f"Hot-reloaded ML model for {pair} (mtime changed)")
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(self.telegram.send(
                                f"🔄 <b>ML Model Hot-Reloaded</b>\n"
                                f"Pair: {pair}\n"
                                f"Reason: model file updated"
                            ))
                    except RuntimeError:
                        pass
                except Exception as e:
                    logger.warning(f"Hot-reload failed for {pair}: {e} — keeping existing model")

        candles = self._cached_candles.get(pair)
        if candles is None or len(candles) < 50:
            return

        try:
            df_features = calculate_technical_features(candles)
            if df_features.empty:
                return

            last_features = df_features.iloc[[-1]][self.FEATURE_COLS]

            # Check for NaN in features
            if last_features.isna().any(axis=1).iloc[0]:
                logger.warning(f"ML features contain NaN for {pair}, skipping prediction")
                return

            model = self._ml_models[pair]
            regime = model.predict_class(last_features)
            regime_probs = model.predict_proba_full(last_features)
            confidence = regime_probs.get(regime, 0.0)

            # Per-pair danger override using rolling ATR percentile
            norm_atr = last_features['normalized_atr'].iloc[0]
            ret = abs(last_features['returns'].iloc[0])
            atr_threshold = df_features['normalized_atr'].quantile(0.95)

            if norm_atr > atr_threshold and ret < 0.005 and regime != 2:
                regime = 2
                confidence = 0.80
                logger.info(f"ML Danger override for {pair}: ATR={norm_atr:.4f} > p95={atr_threshold:.4f}")

            # Update cache
            self._ml_predictions[pair] = (regime, confidence, time_mod.time())

            # Track prediction history for staleness detection
            self._ml_prediction_history[pair].append((regime, confidence, time_mod.time()))
            if len(self._ml_prediction_history[pair]) > 1440:
                self._ml_prediction_history[pair] = self._ml_prediction_history[pair][-1440:]

            # GC management: collect every ~5 minutes
            self._ml_gc_counter += 1
            gc_interval = 5 * len(self.pairs)
            if self._ml_gc_counter % gc_interval == 0:
                gc_mod.collect()

            # Periodic staleness check (~every 20th prediction for this pair)
            if len(self._ml_prediction_history[pair]) % 20 == 0:
                self._check_ml_staleness(pair)

            # Cleanup intermediate objects
            del df_features, last_features

            REGIME_NAMES = {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}
            logger.info(
                f"ML {pair}: {REGIME_NAMES.get(regime, 'UNKNOWN')} "
                f"({confidence*100:.1f}%) | probs={regime_probs}"
            )
        except Exception as e:
            logger.error(f"ML prediction failed for {pair}: {e}")

    def _check_ml_staleness(self, pair: str):
        """Check if ML predictions for a pair are stuck on one regime."""
        history = self._ml_prediction_history.get(pair, [])
        if len(history) < 20:
            return

        cutoff = time_mod.time() - 86400  # last 24h
        recent = [(r, c, t) for r, c, t in history if t >= cutoff]
        if len(recent) < 20:
            return

        regimes = set(r for r, c, t in recent)
        if len(regimes) == 1:
            stuck_regime = recent[0][0]
            REGIME_NAMES = {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}
            logger.warning(
                f"ML model for {pair} may be stale — predicted "
                f"{REGIME_NAMES.get(stuck_regime, 'UNKNOWN')} for {len(recent)} "
                f"consecutive predictions over 24h"
            )

    # ── Trend Engine Tick ──

    def _trend_tick(self, engine: PairEngine):
        """Process trend tick for a single pair."""
        if not self._trend_enabled:
            return

        pm = self._position_managers.get(engine.symbol, self._position_manager)

        # Update trailing stops
        if self._last_price.get(engine.symbol, 0) > 0:
            for pos in pm.get_all_positions():
                pm.update_trailing(pos, self._last_price[engine.symbol])

        # Update trend circuit breaker and check drawdown/daily limits
        if pm.open_count > 0:
            trend_equity = self._estimate_trend_equity()
            self._trend_breaker.update_peak(trend_equity)
            if self._trend_breaker.check(trend_equity) or self._trend_breaker.check_daily(trend_equity):
                logger.critical(f"Trend circuit breaker triggered! Equity: ${trend_equity:.2f}")
                self._close_all_trend_positions(engine)
                return

        # Check exits every tick (SL/TP/trailing)
        if pm.open_count > 0:
            self._check_trend_exits(engine)

        # Check signal-based exit (throttled to every 55 ticks)
        if pm.open_count > 0 and self._trend_tick_count % 55 == 0:
            self._check_signal_exit(engine)

        # Force close
        if self._trend_force_close:
            self._close_all_trend_positions(engine)
            self._trend_force_close = False

        # Evaluate signals every 55 ticks (~1 min apart)
        if (self._last_price.get(engine.symbol, 0) > 0
                and pm._capital > 0
                and self._trend_tick_count % 55 == 0):
            self._evaluate_trend_signals(engine)

    # ── Fill Handler ──

    def did_fill_order(self, event):
        try:
            # Route fill to trading engine for state synchronization
            if self._trading_host is not None:
                try:
                    from src.trading_engine.adapter.hummingbot_integration import route_fill
                    route_fill(self._trading_host, event)
                except Exception as te_err:
                    logger.debug(f"Trading engine fill routing skipped: {te_err}")

            order_id = str(getattr(event, 'order_id', getattr(event, 'client_order_id', '')))
            # Route to trend if order_id matches any per-pair position manager
            is_trend = False
            for pm in self._position_managers.values():
                if pm.get_position(order_id) or pm.get_position_by_exit(order_id):
                    is_trend = True
                    break
            if not is_trend:
                is_trend = self._position_manager.get_position(order_id) or self._position_manager.get_position_by_exit(order_id)

            if is_trend:
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

        # Find which pair's manager owns this position
        pm = None
        for symbol, mgr in self._position_managers.items():
            if mgr.get_position(order_id) or mgr.get_position_by_exit(order_id):
                pm = mgr
                break
        if pm is None:
            pm = self._position_manager

        pos = pm.get_position(order_id)
        if pos:
            # It's an entry fill
            pos.entry_price = price
            pos.amount = quantity  # update to actual filled amount
            trend_pair = getattr(pos, 'pair', self.trading_pair)
            trend_display = trend_pair.replace("-", "/")
            trend_base = trend_pair.split("-")[0]
            logger.info(f"TREND ENTRY FILLED: {quantity} {trend_base} @ ${price:,.2f}")
            msg = (
                f"🚀 <b>TREND IN: {trend_display}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📦 <b>Size:</b> {quantity} {trend_base}\n"
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

        pos = pm.get_position_by_exit(order_id)
        if pos:
            # It's an exit fill
            closed = pm.finalize_exit(pos.entry_order_id, price, fee)
            if closed:
                trend_pair = getattr(pos, 'pair', self.trading_pair)
                if hasattr(self, '_capital_mgr'):
                    self._capital_mgr.release(trend_pair, "trend")
                    self._capital_mgr.save()
                self._trend_journal.log_trade(
                    side="SELL", entry_price=closed["entry_price"], exit_price=closed["exit_price"],
                    amount=closed["amount"], fee=round(fee, 2), pnl=closed["pnl"],
                    pnl_pct=closed["pnl_pct"], stop_loss=closed["stop_loss"],
                    take_profit=closed["take_profit"], exit_reason=closed["exit_reason"],
                    signal_score=getattr(pos, 'signal_score', 0), duration_minutes=closed["duration_minutes"],
                )
                trend_engine = self.pairs.get(trend_pair)
                self._save_trend_state(trend_engine)
                trend_display = trend_pair.replace("-", "/")
                trend_base = trend_pair.split("-")[0]
                logger.info(f"TREND EXIT FILLED ({closed['exit_reason']}): {closed['amount']:.1f} {trend_base} @ ${price:.2f} | PnL ${closed['pnl']:+.2f}")

                pnl_sign = "+" if closed["pnl"] >= 0 else ""
                emoji = "💚" if closed["pnl"] >= 0 else "🔴"
                msg = (
                    f"{emoji} <b>TREND OUT: {trend_display}</b>\n"
                    f"•••\n"
                    f"🔔 <b>{closed['exit_reason'].upper()}</b> ({closed['duration_minutes']}m)\n"
                    f"🔵 <b>In:</b>  ${closed['entry_price']:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {closed['amount']} {trend_base}\n"
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

    def _resolve_fill_pair(self, order_id: str):
        """Find which pair a fill belongs to by checking per-pair order trackers."""
        for symbol, tracker in self.grid_order_trackers.items():
            if tracker.get(order_id):
                return symbol
        # Fallback: try the legacy single-pair tracker
        return self.trading_pair if not self.pairs else list(self.pairs.keys())[0]

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

        # Resolve which pair this fill belongs to
        fill_symbol = self._resolve_fill_pair(order_id)
        engine = self.pairs.get(fill_symbol) if self.pairs else None
        display_pair = fill_symbol.replace("-", "/")
        base_asset = fill_symbol.split("-")[0]

        # Use per-pair state
        state_machine = self.state_machines.get(fill_symbol, self.state_machine)
        order_tracker = self.grid_order_trackers.get(fill_symbol, self._grid_order_tracker)

        logger.info(f"Grid fill: side={side} pair={fill_symbol} price=${price:,.2f} qty={quantity} order_id={order_id}")

        rsi_val = 0.0
        bb_upper = 0.0
        bb_lower = 0.0
        ema_val = 0.0
        atr_val = 0.0
        grid_state_val = state_machine.state.value

        cached = self._cached_indicators.get(fill_symbol)
        if cached is not None:
            bb_r, rsi_r, ema_r, atr_r, _ = cached
            rsi_val = rsi_r
            bb_upper = bb_r.upper
            bb_lower = bb_r.lower
            ema_val = ema_r
            atr_val = atr_r

        grid_level = 0
        grid_order = order_tracker.mark_filled(order_id)
        if grid_order:
            grid_level = grid_order.level
        elif cached is not None:
            bb_r = cached[0]
            mid = bb_r.mid
            spacing = self._active_buy_spacing if is_buy else self._active_sell_spacing
            if spacing <= 0:
                spacing = atr_val * self.atr_multiplier if atr_val > 0 else 1
            grid_level = int(round(abs(price - mid) / spacing)) if spacing > 0 else 0

        fee_est = quantity * price * self._fee_rate
        usdt_bal = self._get_usdt_balance(engine)
        base_bal = self._get_base_balance(engine)
        equity = self._estimate_equity(price, engine)
        position_guard = self.position_guards.get(fill_symbol, self.position_guard)
        exposure_pct = position_guard.base_exposure_pct(base_bal, price, equity)

        self.event_log.log("trade_filled",
            side=side, price=round(price, 2), quantity=quantity,
            grid_level=grid_level, fee_estimate=round(fee_est, 4),
            rsi=round(rsi_val, 2), bb_upper=round(bb_upper, 2),
            bb_lower=round(bb_lower, 2), ema_200=round(ema_val, 2),
            atr=round(atr_val, 2), usdt_balance=round(usdt_bal, 2),
            base_balance=round(base_bal, 4), equity=round(equity, 2),
            engine="grid", pair=fill_symbol,
        )

        if side == "BUY":
            buy_fill = FillRecord(
                order_id=order_id, side=side, price=price, quantity=quantity,
                grid_level=grid_level, timestamp=time_mod.time(),
                rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower,
                ema_200=ema_val, atr=atr_val, grid_state=grid_state_val, fee=fee_est,
            )

            pair_sells = self._unmatched_sells.get(fill_symbol, {})
            matching_sell = pair_sells.pop(order_id, None)
            if not matching_sell and pair_sells:
                oldest_sell_id = min(pair_sells, key=lambda k: pair_sells[k].timestamp)
                matching_sell = pair_sells.pop(oldest_sell_id)

            if matching_sell:
                entry_price = matching_sell.price
                gross_pnl = (entry_price - price) * quantity
                duration_min = int((time_mod.time() - matching_sell.timestamp) / 60)
                total_fee = matching_sell.fee + fee_est
                net_pnl = gross_pnl - total_fee

                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=display_pair, side="SELL", entry_price=entry_price, exit_price=price,
                    quantity=quantity, gross_pnl=round(gross_pnl, 4), fee=round(total_fee, 4),
                    net_pnl=round(net_pnl, 4), grid_level=grid_level, duration_min=duration_min,
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower, ema_200=ema_val, atr=atr_val,
                    grid_state=grid_state_val,
                )
                self.journal.log_trade(trade)

                pnl_sign = "+" if net_pnl >= 0 else ""
                telegram_msg = (
                    f"{'💚' if net_pnl >= 0 else '🔴'} <b>Trade Closed — {display_pair}</b>\n"
                    f"•••\n"
                    f"📈 BUY closed SELL position  |  Grid Level {grid_level}\n"
                    f"⏱ <b>Dur:</b> {duration_min}m\n"
                    f"🔵 <b>In:</b>  ${entry_price:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {base_asset}\n"
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
                self._open_buys.setdefault(fill_symbol, {})[order_id] = buy_fill
                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=display_pair, side="BUY", entry_price=price, exit_price=price,
                    quantity=quantity, gross_pnl=0.0, fee=fee_est, net_pnl=-fee_est,
                    grid_level=grid_level, duration_min=0, rsi=rsi_val, bb_upper=bb_upper,
                    bb_lower=bb_lower, ema_200=ema_val, atr=atr_val, grid_state=grid_state_val,
                )
                self.journal.log_trade(trade)
                buy_msg = (
                    f"📈 <b>BUY Filled — {display_pair}</b>\n"
                    f"•••\n"
                    f"💵 <b>Price:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {base_asset}\n"
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
                logger.info(f"BUY filled: {quantity} {base_asset} @ ${price:,.2f} | Level {grid_level}")

            self._save_grid_state(engine)
            self._grid_dirty = True

        elif side == "SELL":
            fee = fee_est
            pair_buys = self._open_buys.get(fill_symbol, {})
            matching_buy = pair_buys.pop(order_id, None)
            if not matching_buy and pair_buys:
                oldest_id = min(pair_buys, key=lambda k: pair_buys[k].timestamp)
                matching_buy = pair_buys.pop(oldest_id)

            self._save_grid_state(engine)

            if matching_buy:
                entry_price = matching_buy.price
                gross_pnl = (price - entry_price) * quantity
                duration_min = int((time_mod.time() - matching_buy.timestamp) / 60)
                total_fee = matching_buy.fee + fee
                net_pnl = gross_pnl - total_fee

                trade = Trade(
                    timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
                    pair=display_pair, side="SELL", entry_price=entry_price, exit_price=price,
                    quantity=quantity, gross_pnl=round(gross_pnl, 4), fee=round(total_fee, 4),
                    net_pnl=round(net_pnl, 4), grid_level=grid_level, duration_min=duration_min,
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower, ema_200=ema_val, atr=atr_val,
                    grid_state=grid_state_val,
                )
                self.journal.log_trade(trade)

                pnl_sign = "+" if net_pnl >= 0 else ""
                telegram_msg = (
                    f"{'💚' if net_pnl >= 0 else '🔴'} <b>Trade Closed — {display_pair}</b>\n"
                    f"•••\n"
                    f"📉 SELL  |  Grid Level {grid_level}\n"
                    f"⏱ <b>Dur:</b> {duration_min}m\n"
                    f"🔵 <b>In:</b> ${entry_price:,.2f}\n"
                    f"⚪️ <b>Out:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {base_asset}\n"
                    f"•••\n"
                    f"💰 <b>Gross:</b> {pnl_sign}${gross_pnl:.2f}\n"
                    f"💸 <b>Fee:</b> -${total_fee:.2f}\n"
                    f"<b>📊 NET: {pnl_sign}${net_pnl:.2f}</b>\n"
                    f"•••\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}  |  <b>Exp:</b> {exposure_pct:.0f}%\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )
            else:
                self._unmatched_sells.setdefault(fill_symbol, {})[order_id] = FillRecord(
                    order_id=order_id, side="SELL", price=price, quantity=quantity,
                    grid_level=grid_level, timestamp=time_mod.time(),
                    rsi=rsi_val, bb_upper=bb_upper, bb_lower=bb_lower,
                    ema_200=ema_val, atr=atr_val, grid_state=grid_state_val, fee=fee,
                )
                self._save_grid_state(engine)
                self.event_log.log("sell_buffered",
                    side="SELL", price=price, quantity=quantity, grid_level=grid_level,
                    fee_estimate=round(fee, 4), unmatched_sell_count=len(self._unmatched_sells.get(fill_symbol, {})),
                )
                telegram_msg = (
                    f"🟡 <b>SELL Filled (buffered) — {display_pair}</b>\n"
                    f"•••\n"
                    f"📉 SELL  |  Grid Level {grid_level}\n"
                    f"💵 <b>Price:</b> ${price:,.2f}\n"
                    f"📦 <b>Size:</b> {quantity} {base_asset}\n"
                    f"🔄 Buffered sells: {len(self._unmatched_sells.get(fill_symbol, {}))}\n"
                    f"🏦 <b>Eq:</b> ${equity:,.2f}\n"
                    f"⚙️ <b>Env:</b> {self.env.upper()}"
                )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(telegram_msg))
            except RuntimeError:
                pass

            logger.info(f"SELL filled: {quantity} {base_asset} @ ${price:,.2f} | Level {grid_level}")
            self._grid_dirty = True

    # ── Trend Engine Methods ──

    def _estimate_trend_equity(self) -> float:
        """Estimate trend engine equity: base capital + unrealized PnL from all open positions."""
        equity = self._trend_capital
        for symbol, pm in self._position_managers.items():
            for pos in pm.get_all_positions():
                current_price = self._last_price.get(symbol, pos.entry_price)
                equity += (current_price - pos.entry_price) * pos.amount
        return equity

    def _check_trend_exits(self, engine: PairEngine):
        if not self._last_price.get(engine.symbol, 0):
            return
        pm = self._position_managers.get(engine.symbol, self._position_manager)

        # ── Stale exit cleanup: force-close zombie positions ──
        # Positions with exit_order_id set but is_closed=False for >10 minutes
        # means the exit order never filled (e.g. LIMIT_MAKER on paper trading).
        # Reset their exit state so they can be re-evaluated by check_exits().
        current_price = self._last_price[engine.symbol]
        for pos in pm.get_all_positions():
            if pos.exit_order_id and not pos.is_closed:
                try:
                    entry_time = pos.entry_time
                    if isinstance(entry_time, str):
                        from datetime import datetime as _dt
                        entry_time = _dt.fromisoformat(entry_time)
                    if entry_time:
                        age_hours = (pm._positions.get(pos.entry_order_id, pos).entry_time
                                     if hasattr(pos, 'entry_time') else None)
                        # If exit was pending for a long time, force-close at market
                        if pos.exit_reason:
                            logger.info(f"Force-closing stale trend position {engine.symbol}: "
                                        f"exit_reason={pos.exit_reason} has been pending, closing at market")
                            # Record the forced close directly
                            closed = pm.finalize_exit(pos.entry_order_id, current_price, fee=0.0)
                            if closed:
                                self._trend_journal.log_trade(
                                    side="SELL", entry_price=closed["entry_price"],
                                    exit_price=current_price, amount=closed["amount"],
                                    fee=0.0, pnl=closed["pnl"], pnl_pct=closed["pnl_pct"],
                                    stop_loss=closed["stop_loss"], take_profit=closed["take_profit"],
                                    exit_reason=f"force_close_{closed['exit_reason']}",
                                    signal_score=getattr(pos, 'signal_score', 0),
                                    duration_minutes=closed["duration_minutes"],
                                )
                                logger.info(f"Trend force-close {engine.symbol}: PnL={closed['pnl']:.2f} ({closed['pnl_pct']:.2f}%)")
                                self._save_trend_state(engine)
                except Exception as e:
                    logger.debug(f"Stale exit check error for {engine.symbol}: {e}")

        # ── Normal SL/TP/trailing exit checks ──
        exits = pm.check_exits(current_price)
        for exit_info in exits:
            pos = pm.get_position(exit_info["order_id"])
            if pos:
                self._execute_trend_exit(pos, exit_info, engine)

    def _check_signal_exit(self, engine: PairEngine):
        """Check if signal score has degraded enough to exit positions."""
        if not self._last_price.get(engine.symbol, 0):
            return
        candles = self._cached_candles.get(engine.symbol)
        if candles is None or len(candles) < 200:
            return

        pm = self._position_managers.get(engine.symbol, self._position_manager)
        current_score = self._trend_manager.evaluate(candles, self._last_price[engine.symbol])
        if self._trend_manager.should_exit(current_score):
            for pos in pm.get_all_positions():
                if not pos.exit_order_id:
                    logger.info(f"Signal degradation exit for {engine.symbol}: score={current_score.total}")
                    self._execute_trend_exit(pos, {
                        "order_id": pos.entry_order_id,
                        "exit_price": self._last_price[engine.symbol],
                        "reason": "signal_degradation",
                    }, engine)
                    break  # exit one position per tick

    def _execute_trend_exit(self, pos, exit_info: dict, engine: PairEngine):
        exit_price = exit_info["exit_price"]
        reason = exit_info["reason"]
        amount = Decimal(str(pos.amount)).quantize(Decimal("0.01"))

        try:
            # Use LIMIT (not LIMIT_MAKER) so paper trading fills the exit immediately.
            # LIMIT_MAKER orders sit unfilled on paper trade connectors, causing zombie positions.
            order_id = self.sell(self.exchange, engine.symbol, amount, OrderType.LIMIT)
            logger.info(f"Trend SELL order placed for {engine.symbol}: {amount} @ {exit_price} reason={reason}")
        except Exception as e:
            logger.error(f"Trend sell failed for {engine.symbol}: {e}")
            return

        self._position_managers.get(engine.symbol, self._position_manager).mark_exit_pending(pos.entry_order_id, str(order_id), reason)
        self._save_trend_state(engine)

    def _evaluate_trend_signals(self, engine: PairEngine):
        pm = self._position_managers.get(engine.symbol, self._position_manager)
        if not pm.can_open():
            return
        # Global position limit across all pairs
        total_positions = sum(mgr.open_count for mgr in self._position_managers.values())
        if total_positions >= self._trend_max_total_positions:
            return
        if self._trend_breaker.halted:
            return

        # ML gate: per-pair regime check
        if engine.symbol in self._ml_models:
            ml_regime, ml_confidence, _ = self._ml_predictions.get(
                engine.symbol, (None, 0.0, 0.0)
            )
            if ml_regime is not None:
                if ml_regime == 2:  # Danger — block all entries
                    return
                if ml_regime == 1 and ml_confidence < 0.5:  # Uncertain trending
                    return
                if ml_regime == 0 and ml_confidence >= 0.65:  # Confident ranging — grid only
                    return

        # Cross-asset correlation gate: block trend entries on altcoins when BTC is DANGER
        if engine.symbol != "BTC-USDT" and self._btc_danger_active():
            return

        candles = self._cached_candles.get(engine.symbol)
        if candles is None or len(candles) < 200:
            return

        score = self._trend_manager.evaluate(candles, self._last_price[engine.symbol])
        self._last_trend_score = score

        self.event_log.log("trend_score", total=score.total, max=7, details=score.details, pair=engine.symbol)

        if self._trend_manager.should_enter(score):
            confirmed = self._trend_manager.confirm_entry(score)
            self.event_log.log("trend_confirm", score=score.total, confirmed=confirmed,
                               pending=self._trend_manager._pending_ticks,
                               required=self._trend_manager._confirmation_ticks,
                               pair=engine.symbol)
            if confirmed:
                self._open_trend_position(candles, score, engine)

    def _open_trend_position(self, candles: pd.DataFrame, score, engine: PairEngine):
        sr_levels = self._trend_manager._sr.detect(candles)
        # Reuse cached ATR from per-pair indicators (avoids cold-start with no warmup)
        cached = self._cached_indicators.get(engine.symbol)
        atr_val = cached[3] if cached else None

        sl = self._trend_manager.calculate_stop_loss(self._last_price[engine.symbol], sr_levels, atr_val)
        tp = self._trend_manager.calculate_take_profit(self._last_price[engine.symbol], sl)

        pm = self._position_managers.get(engine.symbol, self._position_manager)
        _, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
        amount = pm.calculate_position_size(self._last_price[engine.symbol], sl,
                                            confidence=ml_confidence)
        if amount <= 0:
            return

        amount_dec = Decimal(str(amount)).quantize(Decimal("0.01"))
        try:
            # Use LIMIT (not LIMIT_MAKER) so paper trading fills the entry immediately.
            # LIMIT_MAKER orders sit unfilled on paper trade connectors, causing
            # decimal.InvalidOperation crashes when subsequent sell orders cross them.
            order_id = self.buy(self.exchange, engine.symbol, amount_dec, OrderType.LIMIT)
        except Exception as e:
            logger.error(f"Trend buy failed for {engine.symbol}: {e}")
            return

        entry_time = datetime.now(timezone.utc).isoformat()
        pos = pm.open_position(
            entry_order_id=str(order_id), entry_price=self._last_price[engine.symbol],
            amount=amount, stop_loss=sl, take_profit=tp, entry_time=entry_time,
        )

        if pos:
            notional = pos.amount * pos.entry_price
            if hasattr(self, '_capital_mgr'):
                self._capital_mgr.allocate(engine.symbol, "trend", notional)
                self._capital_mgr.save()
            self._save_trend_state(engine)
            self.event_log.log("trend_entry", amount=round(amount, 2), price=self._last_price[engine.symbol],
                               sl=sl, tp=tp, score=score.total, pair=engine.symbol)
            self._trend_breaker.set_peak_equity(pm._capital + pos.amount * self._last_price[engine.symbol])

    def _close_all_trend_positions(self, engine: PairEngine):
        logger.warning(f"Closing all trend positions for {engine.symbol}...")
        pm = self._position_managers.get(engine.symbol, self._position_manager)
        for pos in pm.get_all_positions():
            self._execute_trend_exit(pos, {
                "order_id": pos.entry_order_id,
                "exit_price": self._last_price.get(engine.symbol, 0) or pos.entry_price,
                "reason": "manual_close",
            }, engine)

    def _save_trend_state(self, engine: PairEngine):
        path = engine.trend_state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        pm = self._position_managers.get(engine.symbol, self._position_manager)
        pm.save_state(path)

    # ── Grid Helper Methods ──

    def _place_grid_orders(self, grid, current_price: float, engine: PairEngine):
        self._cancel_all_orders(engine, "grid_refresh")
        connector = self.connectors.get(self.exchange)
        if not connector:
            return
        if hasattr(connector, 'ready') and not connector.ready:
            return

        usdt_bal = self._get_usdt_balance(engine)
        base_bal = self._get_base_balance(engine)
        equity = self._estimate_equity(current_price, engine)
        position_guard = self.position_guards[engine.symbol]
        exposure_pct = position_guard.base_exposure_pct(base_bal, current_price, equity)

        # Cross-asset correlation gate: halt altcoin buys when BTC is in DANGER
        is_altcoin = engine.symbol != "BTC-USDT"
        gate_active = is_altcoin and self._btc_danger_active()
        if gate_active:
            if not self._correlation_gate_active.get(engine.symbol, False):
                self._correlation_gate_active[engine.symbol] = True
                logger.warning(f"Correlation gate ACTIVATED for {engine.symbol}: BTC DANGER — halting buy-side")
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(
                            f"🛑 <b>Correlation Gate</b>\n"
                            f"BTC regime: DANGER\n"
                            f"Action: {engine.symbol} buy-side HALTED"
                        ))
                except RuntimeError:
                    pass
        else:
            if self._correlation_gate_active.get(engine.symbol, False):
                self._correlation_gate_active[engine.symbol] = False
                logger.info(f"Correlation gate DEACTIVATED for {engine.symbol}: BTC no longer in DANGER")
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(
                            f"✅ <b>Correlation Gate Lifted</b>\n"
                            f"BTC regime: no longer DANGER\n"
                            f"Action: {engine.symbol} buy-side RESUMED"
                        ))
                except RuntimeError:
                    pass

        buys_placed = 0
        sells_placed = 0
        indicators = self._cached_indicators.get(engine.symbol)
        current_rsi = indicators[1] if indicators else None
        pair_buys = self._open_buys.get(engine.symbol, {})
        filled_buy_levels = set(fill.grid_level for fill in pair_buys.values())
        filled_buy_prices = [fill.price for fill in pair_buys.values()]
        min_spacing = grid.buy_spacing * 0.5 if grid.buy_spacing > 0 else 0.5

        for level in grid.buy_levels:
            if gate_active:
                continue
            if current_rsi and current_rsi > 60:
                continue
            if level["price"] >= current_price:
                continue
            if level["level"] in filled_buy_levels:
                continue
            if any(abs(level["price"] - fp) < min_spacing for fp in filled_buy_prices):
                continue
            order_usdt = level["price"] * level["quantity"]
            if not position_guard.can_place_order(
                current_base=base_bal, base_price=current_price,
                current_usdt=usdt_bal, order_usdt=order_usdt, equity=equity,
            ):
                continue
            if not (math.isfinite(level["price"]) and math.isfinite(level["quantity"]) and level["price"] > 0):
                continue
            buys_placed += 1
            client_order_id = self.buy(
                connector_name=self.exchange, trading_pair=engine.symbol,
                amount=Decimal(str(level["quantity"])), order_type=OrderType.LIMIT_MAKER,
                price=Decimal(str(level["price"])),
            )
            if client_order_id:
                self.grid_order_trackers[engine.symbol].add(GridOrder(
                    order_id=client_order_id, level=level["level"],
                    side=OrderSide.BUY, price=level["price"], quantity=level["quantity"],
                ))

        # Place sells for each open buy at a price that guarantees profit.
        # Uses entry_price + sell_spacing, NOT bb.mid + sell_spacing.
        min_sell_spacing = grid.sell_spacing if grid.sell_spacing > 0 else grid.mid_price * 0.002
        for buy in list(self._open_buys.get(engine.symbol, {}).values()):
            if current_rsi and current_rsi < 40:
                continue
            profit_price = buy.price + min_sell_spacing
            sell_price = max(profit_price, current_price + min_sell_spacing * 0.5)
            sell_price = round(sell_price, 2)
            if sell_price <= current_price:
                continue
            if sell_price <= buy.price:
                continue
            base_balance = self._get_base_balance(engine)
            if buy.quantity > base_balance:
                continue
            if not (math.isfinite(sell_price) and sell_price > 0 and math.isfinite(buy.quantity) and buy.quantity > 0):
                continue
            sells_placed += 1
            client_order_id = self.sell(
                connector_name=self.exchange, trading_pair=engine.symbol,
                amount=Decimal(str(buy.quantity)), order_type=OrderType.LIMIT_MAKER,
                price=Decimal(str(sell_price)),
            )
            if client_order_id:
                self.grid_order_trackers[engine.symbol].add(GridOrder(
                    order_id=client_order_id, level=buy.grid_level,
                    side=OrderSide.SELL, price=sell_price, quantity=buy.quantity,
                ))

        logger.info(f"Grid for {engine.symbol}: buys={buys_placed} sells={sells_placed} | open_buys={len(self._open_buys.get(engine.symbol, {}))} unmatched={len(self._unmatched_sells.get(engine.symbol, {}))}")

    def _cancel_all_orders(self, engine: PairEngine, reason: str = "grid_refresh"):
        try:
            active = self.get_active_orders(self.exchange)
        except Exception:
            active = []
        for order in active:
            # Only cancel orders for this specific pair
            if order.trading_pair != engine.symbol:
                continue
            self.event_log.log("order_cancelled",
                order_id=str(order.client_order_id),
                side="BUY" if order.is_buy else "SELL",
                price=float(order.price), reason=reason,
                pair=engine.symbol,
            )
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)
        self.grid_order_trackers[engine.symbol].cancel_all()
        self.grid_order_trackers[engine.symbol].clear_history()

    def _regime_name(self, regime: int = None) -> str:
        if regime is None:
            regime = 0
        return {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}.get(regime, 'UNKNOWN')

    def _btc_danger_active(self) -> bool:
        """Check if BTC regime is DANGER — cross-asset correlation gate.
        When BTC signals DANGER, altcoin buy-side operations halt.
        Defaults to safe (True) if BTC model missing or no prediction.
        """
        btc_pred = self._ml_predictions.get("BTC-USDT")
        if btc_pred is None or "BTC-USDT" not in self._ml_models:
            return True
        btc_regime = btc_pred[0]
        if btc_regime is None:
            return True
        return btc_regime == 2

    def _ml_summary(self) -> str:
        """One-line ML status for all pairs with models (used in daily report)."""
        parts = []
        for symbol in self._ml_models:
            r, c, _ = self._ml_predictions.get(symbol, (0, 0.0, 0.0))
            if r is None:
                r = 0
            parts.append(f"{symbol}: {self._regime_name(r)} ({c*100:.0f}%)")
        return " | ".join(parts) if parts else ""

    def _determine_trigger_reason(self, prev_state, new_state, price, rsi, ema_200, bb, ml_confidence: float = 0.0) -> str:
        if new_state == GridState.PAUSED:
            if rsi > self.rsi_overbought:
                return f"rsi_overbought ({rsi:.1f} > {self.rsi_overbought})"
            if price < ema_200:
                return f"price_below_ema ({price:,.0f} < {ema_200:,.0f})"
            return "combined_pause_signal"
        if new_state == GridState.REACTIVATING:
            return f"rsi_oversold_bounce ({rsi:.1f} < {self.rsi_oversold}, near BB lower)"
        if new_state == GridState.DANGER:
            return f"ml_danger_regime (confidence={ml_confidence:.0%})"
        if new_state == GridState.ACTIVE:
            if prev_state == GridState.PAUSED:
                return f"conditions_cleared (rsi={rsi:.1f}, price>ema)"
            if prev_state == GridState.REACTIVATING:
                return f"bounce_confirmed (rsi={rsi:.1f})"
            return "initial_activation"
        return "unknown"

    def _get_usdt_balance(self, engine: PairEngine = None) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        balance = getattr(connector, "get_balance", lambda x: None)("USDT")
        if balance is None:
            return 0.0
        return float(balance.available if hasattr(balance, 'available') else balance)

    def _get_base_balance(self, engine: PairEngine = None) -> float:
        connector = self.connectors.get(self.exchange)
        if not connector:
            return 0.0
        base_asset = engine.base_asset if engine else self.base_asset
        balance = getattr(connector, "get_balance", lambda x: None)(base_asset)
        if balance is None:
            return 0.0
        return float(balance.available if hasattr(balance, 'available') else balance)

    def _estimate_equity(self, base_price: float, engine: PairEngine = None) -> float:
        return self._get_usdt_balance(engine) + (self._get_base_balance(engine) * base_price)

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

            # Signal Copy Engine summary
            sig_section = ""
            if self._signal_engine:
                sig_status = self._signal_engine.get_status()
                sig_risk = sig_status.get("risk", {})
                sig_journal = self._signal_engine._journal
                sig_today = sig_journal.summary(days=0) if sig_journal else {}
                sig_net = sig_today.get("total_pnl", 0.0) or 0.0
                sig_trades = sig_today.get("total_trades", 0) or 0
                sig_win_rate = sig_today.get("win_rate", 0) or 0
                sig_mode = "AUDIT" if sig_status.get("audit_mode") else "LIVE"
                total_net_today += sig_net
                sig_section = (
                    f"•••\n"
                    f"📡 <b>SIGNAL COPY BOT</b> ({sig_mode})\n"
                    f"📊 Trades: {sig_trades} | Win: {sig_win_rate}%\n"
                    f"📈 Net Today: {fmt(sig_net)}\n"
                    f"📡 Positions: {sig_status.get('open_positions', 0)} | "
                    f"Signals today: {sig_risk.get('trades_today', 0)}\n"
                )

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
                f"{sig_section}"
                f"•••\n"
                f"🏆 <b>COMBINED PNL</b>\n"
                f"📈 Net Today: <b>{fmt(total_net_today)}</b>\n"
                f"📆 Net Week:  {fmt(total_net_week)}\n"
                f"🗓 Net Month: {fmt(total_net_month)}\n"
                f"•••\n"
                f"🏦 <b>Eq:</b> ${equity:,.2f} ({growth_pct:+.1f}% vs base)\n"
                f"{'🤖 ML: ' + self._ml_summary() + chr(10) if self._ml_classifier else ''}"
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

    def _notify_state_change(self, new_state, prev_state, trigger_reason, price, rsi, bb, ema, atr, actual_spacing=0, engine: PairEngine = None):
        display_pair = engine.display_pair if engine else self.display_pair
        state_key = f"{new_state.value}_{engine.symbol}" if engine else new_state.value
        now = time_mod.time()
        last_alert = self._last_state_alert_time.get(state_key, 0)
        if now - last_alert < self._state_alert_cooldown:
            return
        self._last_state_alert_time[state_key] = now

        spacing = actual_spacing if actual_spacing > 0 else (atr * self.atr_multiplier if atr else 0)
        # Resolve per-pair ML regime for notifications
        ml_regime, ml_confidence, _ = (0, 0.0, 0.0)
        if engine and engine.symbol in self._ml_models:
            ml_regime, ml_confidence, _ = self._ml_predictions.get(
                engine.symbol, (0, 0.0, 0.0)
            )
            if ml_regime is None:
                ml_regime = 0
        ml_line = f"🤖 ML: {self._regime_name(ml_regime)} ({ml_confidence*100:.0f}%)" if engine and engine.symbol in self._ml_models else ""

        if new_state == GridState.ACTIVE:
            msg = (
                f"🟢 <b>Grid ACTIVATED — {display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📐 <b>Range:</b> ${bb.lower:,.2f} → ${bb.upper:,.2f}\n"
                f"📏 <b>Space:</b> ${spacing:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.2f}\n"
                f"{'🤖 ' + ml_line + chr(10) if ml_line else ''}"
                f"⚠️ <b>Why:</b> {trigger_reason}"
            )
        elif new_state == GridState.PAUSED:
            msg = (
                f"⏸️ <b>Grid PAUSED — {display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.2f}\n"
                f"{'🤖 ' + ml_line + chr(10) if ml_line else ''}"
                f"⚠️ <b>Why:</b> {trigger_reason}\n"
                f"💤 Holding USDT until re-entry signal."
            )
        elif new_state == GridState.REACTIVATING:
            msg = (
                f"🔄 <b>Grid REACTIVATING — {display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"📐 <b>Range:</b> ${bb.lower:,.2f} → ${bb.upper:,.2f}\n"
                f"📊 RSI: {rsi:.1f}  |  EMA200: ${ema:,.2f}\n"
                f"{'🤖 ' + ml_line + chr(10) if ml_line else ''}"
                f"⚠️ <b>Why:</b> {trigger_reason}"
            )
        elif new_state == GridState.DANGER:
            msg = (
                f"🔴 <b>Grid DANGER MODE — {display_pair}</b>\n"
                f"•••\n"
                f"💵 <b>Price:</b> ${price:,.2f}\n"
                f"🤖 ML: DANGER ({ml_confidence*100:.0f}%)\n"
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

    def _save_grid_state(self, engine: PairEngine = None):
        """Save grid state for a specific pair or all pairs."""
        if engine:
            self._save_grid_state_single(engine)
        else:
            # Save all pairs
            for eng in self.pairs.values():
                self._save_grid_state_single(eng)

    def _save_grid_state_single(self, engine: PairEngine):
        """Save grid state for a single pair."""
        try:
            path = engine.grid_state_path
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "last_sod_reset": self._last_sod_reset,
                "open_buys": {
                    oid: {"order_id": f.order_id, "side": f.side, "price": f.price,
                          "quantity": f.quantity, "grid_level": f.grid_level, "timestamp": f.timestamp,
                          "rsi": f.rsi, "bb_upper": f.bb_upper, "bb_lower": f.bb_lower,
                          "ema_200": f.ema_200, "atr": f.atr, "grid_state": f.grid_state, "fee": f.fee}
                    for oid, f in self._open_buys.get(engine.symbol, {}).items()
                },
                "unmatched_sells": {
                    oid: {"order_id": f.order_id, "side": f.side, "price": f.price,
                          "quantity": f.quantity, "grid_level": f.grid_level, "timestamp": f.timestamp,
                          "rsi": f.rsi, "bb_upper": f.bb_upper, "bb_lower": f.bb_lower,
                          "ema_200": f.ema_200, "atr": f.atr, "grid_state": f.grid_state, "fee": f.fee}
                    for oid, f in self._unmatched_sells.get(engine.symbol, {}).items()
                }
            }
            tmp = path.with_suffix('.tmp')
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logger.error(f"Failed to save grid state for {engine.symbol}: {e}")

    def _load_grid_state(self, engine: PairEngine = None, legacy_path: Path = None):
        """Load grid state for a specific pair or from legacy path."""
        if legacy_path:
            self._load_grid_state_single(legacy_path, engine)
        elif engine:
            self._load_grid_state_single(engine.grid_state_path, engine)

    def _load_grid_state_single(self, path: Path, engine: PairEngine = None):
        """Load grid state from a specific path."""
        try:
            if path.exists():
                with open(path, "r") as f:
                    data = json.load(f)
                    self._last_sod_reset = data.get("last_sod_reset", "")
                    pair_buys = self._open_buys.setdefault(engine.symbol, {})
                    for oid, d in data.get("open_buys", {}).items():
                        pair_buys[oid] = FillRecord(**d)
                    pair_sells = self._unmatched_sells.setdefault(engine.symbol, {})
                    for oid, d in data.get("unmatched_sells", {}).items():
                        pair_sells[oid] = FillRecord(**d)
                sym = engine.symbol if engine else "?"
                logger.info(f"Restored {len(self._open_buys.get(sym, {}))} open buys, {len(self._unmatched_sells.get(sym, {}))} unmatched sells for {sym} from {path}")
        except Exception as e:
            logger.error(f"Failed to load grid state from {path}: {e}")

    def _cleanup_orphans(self):
        now = time_mod.time()
        ttl = 86400 * 7
        changed = False
        for symbol in list(self._open_buys.keys()):
            for oid in [k for k, f in self._open_buys[symbol].items() if (now - f.timestamp) > ttl]:
                self._open_buys[symbol].pop(oid)
                changed = True
            for oid in [k for k, f in self._unmatched_sells.get(symbol, {}).items() if (now - f.timestamp) > ttl]:
                self._unmatched_sells[symbol].pop(oid)
                changed = True
        if changed:
            self._save_grid_state()  # Save all pairs

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
        # Stop trading engine host
        if getattr(self, '_trading_host', None) is not None:
            try:
                self._trading_host.stop()
                logger.info("Trading engine host stopped")
            except Exception:
                pass

        # Save state for all pairs
        for engine in self.pairs.values():
            self._save_grid_state(engine)
            self._save_trend_state(engine)

        # Save capital manager state
        if hasattr(self, '_capital_mgr'):
            self._capital_mgr.save()

        # Stop signal engine listener
        if hasattr(self, '_signal_engine') and self._signal_engine is not None:
            self._signal_engine.stop_listener()

        super().on_stop()
        if hasattr(self, "_sys_monitor"):
            self._sys_monitor.stop()
        if hasattr(self, "_telegram_commands"):
            self._telegram_commands.stop()
        try:
            # Cancel orders for all pairs
            for engine in self.pairs.values():
                self._cancel_all_orders(engine, "graceful_shutdown")
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
        logger.info(f"Dual-engine strategy stopped — all orders cancelled for {len(self.pairs)} pair(s)")
