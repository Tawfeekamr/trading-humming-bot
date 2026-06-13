# backtest/mean_reversion/features.py
"""Trade-flow features for the mean-reversion port.

These replace the four constants the live Rust strategy hardcodes into its
classifier (retrace=0.8, sell_flow_decay=0.8, liq_cascade=0.8, corr=0.2),
making the backtest more rigorous than the live code on those dimensions.
Cross-market correlation has no per-second historical source, so it is set to 0
(live uses a 0.2 constant; impact is minor).
"""
import pandas as pd

# Live ClassifierCfg defaults (config.rs) — fixed per the design spec.
W_RETRACE = 1.0
W_REFILL = 1.0
W_EXHAUST = 1.0
W_LIQ = 0.5
W_CORR = 1.5
ENTER_THRESHOLD = 2.0
FULL_SIZE_MARGIN = 1.5

EPS = 1e-9
WINDOW_SECONDS = 30


def bar_seconds(bar: str) -> int:
    bar = bar.strip()
    if bar.endswith("min"):
        return int(bar[:-3]) * 60
    if bar.endswith("m"):
        return int(bar[:-1]) * 60
    if bar.endswith("s"):
        return max(1, int(bar[:-1]))
    raise ValueError(f"Unsupported bar unit: {bar}")


def window_bars_for(bar: str, window_seconds: int = WINDOW_SECONDS) -> int:
    return max(1, window_seconds // bar_seconds(bar))


def compute_features(bars: pd.DataFrame, bar: str = "1s") -> pd.DataFrame:
    w = window_bars_for(bar)
    smooth = max(1, w // 6)  # ~5s smoothing for a 30s window
    close = bars["close"]
    buy_vol = bars["buy_vol"]
    sell_vol = bars["sell_vol"]

    # Faithful to live (oldest.price - mid) / oldest.price, ~30s ago.
    drop_frac = (close.shift(w) - close) / close.shift(w)

    # Buy-pressure restoration proxy for live bid_depth / oldest_bid_depth.
    buy_smooth = buy_vol.rolling(smooth, min_periods=1).mean()
    bid_refill_ratio = (buy_smooth / (buy_smooth.shift(w) + EPS)).clip(0, 3)

    # Dump exhaustion: recent sell intensity vs the window peak. Low = exhausted.
    sell_smooth = sell_vol.rolling(smooth, min_periods=1).mean()
    sell_flow_decay = (sell_smooth / (sell_vol.rolling(w, min_periods=1).max() + EPS)).clip(0, 1)

    # Liquidation-cascade spike: peak vs mean per-bar sell volume.
    liq_cascade_score = (
        sell_vol.rolling(w, min_periods=1).max() / (sell_vol.rolling(w, min_periods=1).mean() + EPS)
    ).clip(0, 10)

    score = (
        W_RETRACE * drop_frac
        + W_REFILL * bid_refill_ratio
        + W_EXHAUST * sell_flow_decay
        + W_LIQ * liq_cascade_score
        - W_CORR * 0.0  # cross_market_corr unavailable historically
    )
    size_mult = ((score - ENTER_THRESHOLD) / FULL_SIZE_MARGIN).clip(0, 1)

    return pd.DataFrame(
        {
            "drop_frac": drop_frac,
            "bid_refill_ratio": bid_refill_ratio,
            "sell_flow_decay": sell_flow_decay,
            "liq_cascade_score": liq_cascade_score,
            "score": score,
            "size_mult": size_mult,
        },
        index=bars.index,
    )
