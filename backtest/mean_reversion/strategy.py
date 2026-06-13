# backtest/mean_reversion/strategy.py
"""Entry-signal logic for the mean-reversion port (mirrors Rust on_tick entry gate)."""
import pandas as pd

from .features import ENTER_THRESHOLD


def entry_signal(features: pd.DataFrame, drop_thr: float,
                 enter_threshold: float = ENTER_THRESHOLD) -> pd.Series:
    """A flush (drop_frac > drop_thr) that also clears the classifier score."""
    return (features["drop_frac"] > drop_thr) & (features["score"] >= enter_threshold)
