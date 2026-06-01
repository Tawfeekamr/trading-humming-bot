from .base import Strategy

# Lazy imports — trading_engine_core wheel may not be available
# in all environments (e.g., during initial setup or testing)
def __getattr__(name):
    if name == "GridStrategy":
        from .grid import GridStrategy
        return GridStrategy
    if name == "TrendStrategy":
        from .trend import TrendStrategy
        return TrendStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["Strategy", "GridStrategy", "TrendStrategy"]
