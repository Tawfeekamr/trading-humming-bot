"""Custom data models for NautilusTrader inter-process communication.

These are msgspec-serializable data types that flow through
NautilusTrader's pub/sub message bus.

NOTE: These models are designed to work with or without nautilus_trader installed.
When nautilus_trader is available, they inherit from its Data base class.
When not available, they provide a standalone implementation.
"""
try:
    from nautilus_trader.core.data import Data
    from nautilus_trader.model.data import DataType
    NAUTILUS_AVAILABLE = True
except ImportError:
    # nautilus_trader not available - create minimal base class
    NAUTILUS_AVAILABLE = False

    class Data:
        """Minimal base class when nautilus_trader is not available."""
        pass

    class DataType:
        """Minimal DataType placeholder when nautilus_trader is not available."""
        def __init__(self, data_type):
            self.data_type = data_type

        def __repr__(self):
            return f"DataType({self.data_type.__name__})"


class MLPrediction(Data):
    """Regime prediction from the shared FastAPI ML service.

    This is published by the ML poller actor and consumed by strategies
    that use regime state to gate their trading logic.

    Attributes
    ----------
    instrument_id : str
        The trading instrument identifier (e.g., "EUR/USD.IDEALPRO")
    regime : str
        The predicted regime (e.g., "TRENDING_UP", "RANGING", "TRENDING_DOWN")
    confidence : float
        Model confidence score between 0.0 and 1.0
    ts_event : int
        Unix timestamp when the prediction was made
    ts_init : int
        Unix timestamp when the object was initialized
    """

    def __init__(
        self,
        instrument_id: str,
        regime: str,
        confidence: float,
        ts_event: int,
        ts_init: int,
    ):
        self.instrument_id = instrument_id
        self.regime = regime
        self.confidence = confidence
        self.ts_event = ts_event
        self.ts_init = ts_init

    def __repr__(self) -> str:
        return (
            f"MLPrediction(instrument_id={self.instrument_id}, "
            f"regime={self.regime}, confidence={self.confidence:.2f})"
        )


__all__ = ["MLPrediction", "NAUTILUS_AVAILABLE"]
if NAUTILUS_AVAILABLE:
    __all__.extend(["Data", "DataType"])
