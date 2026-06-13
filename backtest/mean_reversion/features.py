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

# BUG-12: EMA regime filter threshold (approximation of live ML regime gate)
REGIME_THR = 0.002


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
    """Compute trade-flow features for mean-reversion entry logic.

    Returns DataFrame with:
    - drop_frac: price decline over window (0-1)
    - bid_refill_ratio: buy-pressure restoration (0-3)
    - sell_flow_decay: sell-exhaustion indicator (0-1)
    - liq_cascade_score: liquidation spike (0-3)
    - score: weighted classifier score
    - size_mult: position size multiplier (0-1)
    - regime_trending: EMA-based regime filter (bool)

    Note: regime_trending is an EMA approximation of the live ML regime gate.
    The live Rust strategy uses a MarketRegime::Trending classifier that has no
    historical per-second equivalent. This EMA-based approximation provides a
    conservative filter that reduces false entries during strong trends.
    """
    w = window_bars_for(bar)
    smooth = max(1, w // 6)  # ~5s smoothing for a 30s window
    close = bars["close"]
    buy_vol = bars["buy_vol"]
    sell_vol = bars["sell_vol"]

    # Faithful to live (oldest.price - mid) / oldest.price, ~30s ago.
    drop_frac = (close.shift(w) - close) / close.shift(w)
    # BUG-5: Fill NaN with 0 to avoid carrying into score/entry
    drop_frac = drop_frac.fillna(0.0)

    # Buy-pressure restoration proxy for live bid_depth / oldest_bid_depth.
    buy_smooth = buy_vol.rolling(smooth, min_periods=1).mean()
    bid_refill_ratio = (buy_smooth / (buy_smooth.shift(w) + EPS)).clip(0, 3)

    # Dump exhaustion: recent sell intensity vs the window peak. Low = exhausted.
    sell_smooth = sell_vol.rolling(smooth, min_periods=1).mean()
    sell_flow_decay = (sell_smooth / (sell_vol.rolling(w, min_periods=1).max() + EPS)).clip(0, 1)

    # Liquidation-cascade spike: peak vs mean per-bar sell volume.
    # BUG-6: Cap at (0, 3) so max contribution is 0.5*3 = 1.5 (cannot solo-clear ENTER_THRESHOLD=2.0)
    liq_cascade_score = (
        sell_vol.rolling(w, min_periods=1).max() / (sell_vol.rolling(w, min_periods=1).mean() + EPS)
    ).clip(0, 3)

    score = (
        W_RETRACE * drop_frac
        + W_REFILL * bid_refill_ratio
        + W_EXHAUST * sell_flow_decay
        + W_LIQ * liq_cascade_score
        - W_CORR * 0.0  # cross_market_corr unavailable historically
    )
    size_mult = ((score - ENTER_THRESHOLD) / FULL_SIZE_MARGIN).clip(0, 1)

    # BUG-12: EMA-based regime filter (approximation of live ML regime gate)
    ema_fast = close.ewm(span=60, min_periods=1).mean()
    ema_slow = close.ewm(span=300, min_periods=1).mean()
    regime_trending = ((ema_fast - ema_slow).abs() / ema_slow > REGIME_THR).fillna(False).astype(bool)

    return pd.DataFrame(
        {
            "drop_frac": drop_frac,
            "bid_refill_ratio": bid_refill_ratio,
            "sell_flow_decay": sell_flow_decay,
            "liq_cascade_score": liq_cascade_score,
            "score": score,
            "size_mult": size_mult,
            "regime_trending": regime_trending,
        },
        index=bars.index,
    )
