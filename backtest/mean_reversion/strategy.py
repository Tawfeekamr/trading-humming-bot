# backtest/mean_reversion/strategy.py
"""Entry-signal logic for the mean-reversion port (mirrors Rust on_tick entry gate)."""
import pandas as pd

from .features import ENTER_THRESHOLD


def entry_signal(features: pd.DataFrame, drop_thr: float,
                 enter_threshold: float = ENTER_THRESHOLD,
                 regime_filter: bool = True) -> pd.Series:
    """A flush (drop_frac > drop_thr) that also clears the classifier score.

    Args:
        features: DataFrame with feature columns
        drop_thr: Minimum price drop fraction to trigger
        enter_threshold: Minimum classifier score (default: ENTER_THRESHOLD)
        regime_filter: If True, filter out trending regimes (default: True)
    """
    sig = (features["drop_frac"] > drop_thr) & (features["score"] >= enter_threshold)
    # BUG-12: regime filter - exclude trending markets
    if regime_filter and "regime_trending" in features.columns:
        sig = sig & (~features["regime_trending"].fillna(False))
    return sig
