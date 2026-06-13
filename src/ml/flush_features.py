# src/ml/flush_features.py
"""Single train/serve feature vector for the flush-reversion entry gate.

ONE function used for both training (from the labeled dataset) and serving (from
the Rust request) — eliminates train/serve feature drift.
"""
import pandas as pd

FEATURE_COLUMNS = [
    "drop_frac", "bid_refill_ratio", "sell_flow_decay", "liq_cascade_score",
    "regime_trending", "volatility", "rsi", "volume_spike", "hour", "weekday",
]


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period, min_periods=1).mean()
    loss = (-delta).where(delta < 0, 0.0).rolling(period, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def features_at_flush(bars: pd.DataFrame, features: pd.DataFrame, idx) -> dict:
    """Feature vector at flush bar `idx`. `features` is compute_features(bars)."""
    close = bars["close"]
    vol = bars["buy_vol"] + bars["sell_vol"]  # synthetic total volume
    i = bars.index.get_loc(idx)
    rets = close.pct_change()
    volatility = float(rets.iloc[max(0, i - 30):i + 1].std()) if i > 0 else 0.0
    rsi_series = _rsi(close)
    window = vol.iloc[max(0, i - 30):i + 1]
    volume_spike = float(vol.iloc[i] / (window.mean() + 1e-9))
    ts = bars.index[i]
    return {
        "drop_frac": float(features.loc[idx, "drop_frac"]),
        "bid_refill_ratio": float(features.loc[idx, "bid_refill_ratio"]),
        "sell_flow_decay": float(features.loc[idx, "sell_flow_decay"]),
        "liq_cascade_score": float(features.loc[idx, "liq_cascade_score"]),
        "regime_trending": int(bool(features.loc[idx, "regime_trending"])),
        "volatility": volatility,
        "rsi": float(rsi_series.iloc[i]),
        "volume_spike": volume_spike,
        "hour": int(getattr(ts, "hour", 0)),
        "weekday": int(ts.weekday() if hasattr(ts, "weekday") else getattr(ts, "weekday", 0)),
    }
