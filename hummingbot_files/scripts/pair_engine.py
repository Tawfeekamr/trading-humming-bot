# hummingbot_files/scripts/pair_engine.py
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.indicators.bollinger import BollingerBands
from src.indicators.rsi import RSI
from src.indicators.ema import EMA
from src.indicators.atr import ATR


@dataclass
class PairConfig:
    """Configuration for a single trading pair."""
    symbol: str
    step_size: float
    tick_size: float = 0.01
    enabled: bool = True

    @property
    def base_asset(self) -> str:
        """Extract base asset from symbol (e.g., 'DOGE-USDT' -> 'DOGE')."""
        return self.symbol.split("-")[0]

    @property
    def binance_symbol(self) -> str:
        """Convert to Binance symbol format (e.g., 'DOGE-USDT' -> 'DOGEUSDT')."""
        return self.symbol.replace("-", "")

    @property
    def display_pair(self) -> str:
        """Convert to display format (e.g., 'DOGE-USDT' -> 'DOGE/USDT')."""
        return self.symbol.replace("-", "/")


class PairEngine:
    """Holds all per-pair state: indicators, grid, trend.

    Each pair gets its own instance with isolated indicator instances,
    grid state, trend positions, and state file paths.
    """

    def __init__(self, config: PairConfig, state_dir: Path = Path("data")):
        self.config = config
        self.symbol = config.symbol
        self.base_asset = config.base_asset
        self.binance_symbol = config.binance_symbol
        self.display_pair = config.display_pair
        self.step_size = config.step_size
        self.tick_size = config.tick_size
        self._state_dir = state_dir

        # Indicators — fresh instance per pair
        self.bb: Optional[BollingerBands] = BollingerBands()
        self.rsi: Optional[RSI] = RSI()
        self.ema: Optional[EMA] = EMA()
        self.atr: Optional[ATR] = ATR()

        # Grid state
        self.grid_state: Optional[Any] = None
        self.grid_orders: List[Any] = []

        # Trend state
        self.trend_positions: Dict[str, Any] = {}
        self.trend_signals: Optional[Any] = None

        # Last known price
        self.last_price: float = 0.0

    @property
    def grid_state_path(self) -> Path:
        """Path to this pair's grid state file."""
        return self._state_dir / f"grid_state_{self.base_asset}.json"

    @property
    def trend_state_path(self) -> Path:
        """Path to this pair's trend state file."""
        return self._state_dir / f"trend_state_{self.base_asset}.json"
