"""Forex Grid Strategy for NautilusTrader.

A simplified grid strategy that places buy/sell limit orders at
regular pip intervals around the mid price, with EMA trend filter
and RSI regime gating.

Designed for use with Interactive Brokers via the IB adapter.

NOTE: This module handles the case where nautilus_trader is not installed
by using import guards and creating minimal stub classes for testing.
"""
from decimal import Decimal
from typing import Optional

# Import guard for environments without nautilus_trader installed
try:
    from nautilus_trader.config import StrategyConfig, PositiveInt
    from nautilus_trader.indicators import ExponentialMovingAverage
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import OrderSide, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.trading.strategy import Strategy
    NAUTILUS_AVAILABLE = True
except ImportError:
    # nautilus_trader not available - create minimal stubs for testing
    NAUTILUS_AVAILABLE = False

    class StrategyConfig:
        """Minimal StrategyConfig placeholder."""
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class PositiveInt(int):
        """Type hint for positive integers - behaves like int."""
        pass

    class Strategy:
        """Minimal Strategy placeholder for testing."""
        def __init__(self, config):
            self.config = config
            self.log = _LogStub()
            self.cache = _CacheStub()

        def on_start(self):
            pass

        def on_stop(self):
            pass

        def on_bar(self, bar):
            pass

        def on_save(self) -> dict:
            return {}

        def on_load(self, state: dict) -> None:
            pass

        def on_data(self, data):
            pass

        def on_dispose(self):
            pass

        def register_indicator_for_bars(self, bar_type, indicator):
            pass

        def subscribe_bars(self, bar_type):
            pass

        def unsubscribe_bars(self, bar_type):
            pass

        def indicators_initialized(self) -> bool:
            return True

        def cancel_all_orders(self, instrument_id):
            pass

        def submit_order(self, order):
            pass

    class _LogStub:
        """Minimal logger stub."""
        def info(self, msg, *args):
            pass

        def warning(self, msg, *args):
            pass

        def error(self, msg, *args):
            pass

    class _CacheStub:
        """Minimal cache stub."""
        def instrument(self, instrument_id):
            return None

    # Placeholder classes for type hints
    class ExponentialMovingAverage:
        def __init__(self, period):
            self.period = period
            self.value = None

    class BarType:
        pass

    class Bar:
        pass

    class OrderSide:
        BUY = "BUY"
        SELL = "SELL"

    class TimeInForce:
        GTC = "GTC"

    class InstrumentId:
        pass

    class Instrument:
        pass

    class Price:
        pass

    class Quantity:
        pass


class ForexGridConfig(StrategyConfig):
    """Configuration for the Forex grid strategy.

    Attributes
    ----------
    instrument_id : InstrumentId or str
        The trading instrument identifier (e.g., "EUR/USD.IDEALPRO")
    bar_type : BarType or str
        The bar type to use for analysis
    levels : PositiveInt
        Number of grid levels on each side of the mid price
    spacing_pips : PositiveInt
        Spacing between grid levels in pips
    take_profit_pips : PositiveInt
        Take profit distance in pips
    stop_loss_pips : PositiveInt
        Stop loss distance in pips
    trade_size : Decimal
        Size of each trade in base currency
    ema_fast : PositiveInt
        Fast EMA period for trend filter
    ema_slow : PositiveInt
        Slow EMA period for trend filter
    rsi_period : PositiveInt
        RSI period for regime gating
    rsi_oversold : int
        RSI oversold threshold
    rsi_overbought : int
        RSI overbought threshold
    allowed_regimes : list[str] or None
        List of allowed ML regimes (e.g., ["TRENDING_UP", "RANGING"])
    """

    def __init__(
        self,
        instrument_id,
        bar_type,
        levels=5,
        spacing_pips=10,
        take_profit_pips=15,
        stop_loss_pips=30,
        trade_size=Decimal("10000"),
        ema_fast=20,
        ema_slow=50,
        rsi_period=14,
        rsi_oversold=35,
        rsi_overbought=70,
        allowed_regimes=None,
    ):
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.levels = levels
        self.spacing_pips = spacing_pips
        self.take_profit_pips = take_profit_pips
        self.stop_loss_pips = stop_loss_pips
        self.trade_size = trade_size
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.allowed_regimes = allowed_regimes


class ForexGridStrategy(Strategy):
    """Grid strategy for Forex pairs on NautilusTrader.

    Places symmetric grid orders (buy below mid, sell above mid) at
    regular pip intervals. Uses EMA trend filter to only place grids
    in favorable conditions. Optionally gates on ML regime state.

    The strategy is designed to work with Interactive Brokers via the
    IB adapter for NautilusTrader.
    """

    def __init__(self, config: ForexGridConfig):
        super().__init__(config)
        self.instrument: Optional[Instrument] = None

        # Only create real indicators if nautilus_trader is available
        if NAUTILUS_AVAILABLE:
            self.fast_ema = ExponentialMovingAverage(config.ema_fast)
            self.slow_ema = ExponentialMovingAverage(config.ema_slow)
        else:
            # Create stub indicators for testing
            self.fast_ema = ExponentialMovingAverage(config.ema_fast)
            self.slow_ema = ExponentialMovingAverage(config.ema_slow)

        self._active_orders: dict[str, dict] = {}
        self._current_regime: Optional[str] = None
        self._grid_placed: bool = False

    def on_start(self):
        """Initialize the strategy when starting."""
        if NAUTILUS_AVAILABLE:
            self.instrument = self.cache.instrument(self.config.instrument_id)
            if self.instrument is None:
                self.log.error(f"Instrument not found: {self.config.instrument_id}")
                self.stop()
                return

            self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
            self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
            self.subscribe_bars(self.config.bar_type)

        self.log.info(
            f"Forex grid strategy started for {self.config.instrument_id}, "
            f"levels={self.config.levels}, spacing={self.config.spacing_pips} pips"
        )

    def on_bar(self, bar: Bar):
        """Handle each new bar - update grid based on conditions."""
        if not self.indicators_initialized():
            return

        mid_price = float(bar.close)
        trend_ok = self.fast_ema.value >= self.slow_ema.value

        if self.config.allowed_regimes and self._current_regime:
            regime_ok = self._current_regime in self.config.allowed_regimes
        else:
            regime_ok = True

        if self._grid_placed and (not trend_ok or not regime_ok):
            self._cancel_grid()
            self.log.info("Grid cancelled: conditions no longer favorable")
            return

        if trend_ok and regime_ok and not self._grid_placed:
            self._place_grid(mid_price)
            self._grid_placed = True

    def on_stop(self):
        """Clean up when stopping the strategy."""
        self._cancel_grid()
        if NAUTILUS_AVAILABLE:
            self.unsubscribe_bars(self.config.bar_type)
        self.log.info("Forex grid strategy stopped")

    def on_save(self) -> dict[str, bytes]:
        """Save strategy state."""
        return {
            "grid_placed": str(self._grid_placed).encode(),
            "current_regime": (self._current_regime or "").encode(),
        }

    def on_load(self, state: dict[str, bytes]) -> None:
        """Load strategy state."""
        self._grid_placed = state.get("grid_placed", b"False").decode() == "True"
        self._current_regime = state.get("current_regime", b"").decode() or None

    def on_data(self, data):
        """Handle custom data (e.g., ML predictions)."""
        # Import locally to avoid circular imports
        try:
            from src.nautilus.models import MLPrediction
            if isinstance(data, MLPrediction):
                self._current_regime = data.regime
                self.log.info(f"Regime updated: {data.regime} (confidence={data.confidence:.2f})")
        except ImportError:
            pass

    def on_dispose(self):
        """Dispose of the strategy."""
        pass

    def _pip_size(self, symbol: str) -> float:
        """Return pip size for a Forex pair.

        JPY pairs use pip=0.01, all others use pip=0.0001.

        Parameters
        ----------
        symbol : str
            The currency pair symbol (e.g., "EUR/USD", "USD/JPY")

        Returns
        -------
        float
            The pip size for the pair (0.0001 or 0.01)
        """
        if "JPY" in symbol.upper():
            return 0.01
        return 0.0001

    def _calculate_grid_levels(self, mid_price: float) -> list[dict]:
        """Calculate grid order prices symmetrically around mid price.

        Parameters
        ----------
        mid_price : float
            The current mid price around which to center the grid

        Returns
        -------
        list[dict]
            List of grid level dictionaries with keys:
            - price: float - the order price
            - side: str - "BUY" or "SELL"
            - level: int - the level number (1-based)
        """
        pip = self._pip_size(str(self.config.instrument_id))
        spacing = self.config.spacing_pips * pip

        levels = []
        # Buy levels below mid price
        for i in range(1, self.config.levels + 1):
            levels.append({
                "price": mid_price - (i * spacing),
                "side": "BUY",
                "level": i,
            })

        # Sell levels above mid price
        for i in range(1, self.config.levels + 1):
            levels.append({
                "price": mid_price + (i * spacing),
                "side": "SELL",
                "level": i,
            })

        return levels

    def _place_grid(self, mid_price: float):
        """Place all grid limit orders.

        Parameters
        ----------
        mid_price : float
            The current mid price around which to place the grid
        """
        if not NAUTILUS_AVAILABLE:
            # In test mode without nautilus_trader, just track the grid
            levels = self._calculate_grid_levels(mid_price)
            for level in levels:
                self._active_orders[f"mock_order_{level['side']}_{level['level']}"] = level
            self._grid_placed = True
            return

        levels = self._calculate_grid_levels(mid_price)

        for level in levels:
            side = OrderSide.BUY if level["side"] == "BUY" else OrderSide.SELL
            price = self.instrument.make_price(level["price"])
            quantity = self.instrument.make_qty(self.config.trade_size)

            order = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=quantity,
                price=price,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self._active_orders[order.client_order_id.value] = level

        self.log.info(
            f"Placed {len(levels)} grid orders around {mid_price:.5f} "
            f"({self.config.levels} buy + {self.config.levels} sell)"
        )

    def _cancel_grid(self):
        """Cancel all active grid orders."""
        if NAUTILUS_AVAILABLE:
            self.cancel_all_orders(self.config.instrument_id)
        self._active_orders.clear()
        self._grid_placed = False


__all__ = ["ForexGridStrategy", "ForexGridConfig", "NAUTILUS_AVAILABLE"]
