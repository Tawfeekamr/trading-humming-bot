"""Per-coin hourly feature frame (pre-registration §4, §6).

`hourly_features(bars_1h)` returns one row per 1h bar. Row t holds only what
is known at the CLOSE of bar t:
  * 1h columns from bars <= t
  * 4h columns from the last 4h bar that has fully closed by t
    (a 4h bar starting at h closes with the 1h bar starting at h+3)

The same function runs on full history (backtest) and on the latest few
hundred bars (live), so the detectors see identical inputs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

# Hourly rows needed before every column is warm: EMA200 on 4h ≈ 800 1h bars.
WARMUP_1H = 4 * 200 + 8


def _to_4h(bars_1h: pd.DataFrame) -> pd.DataFrame:
    g = bars_1h.resample("4h", origin="epoch", label="left", closed="left")
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
        "n": g["close"].count(),
    })
    full = out["n"] == 4            # incomplete 4h bars are never used
    return out[full].drop(columns="n")


def hourly_features(bars_1h: pd.DataFrame) -> pd.DataFrame:
    """bars_1h: index = UTC open_time (hourly), columns open/high/low/close/volume."""
    b = bars_1h
    f = pd.DataFrame(index=b.index)
    f["open"], f["high"], f["low"], f["close"] = b["open"], b["high"], b["low"], b["close"]

    f["atr1h"] = ind.atr(b["high"], b["low"], b["close"], 14)
    f["natr1h"] = f["atr1h"] / b["close"]
    f["rsi1h"] = ind.rsi(b["close"], 14)
    f["rsi1h_prev"] = f["rsi1h"].shift(1)
    f["rsi1h_min3_prior"] = f["rsi1h"].shift(1).rolling(3, min_periods=3).min()
    f["rsi1h_max3_prior"] = f["rsi1h"].shift(1).rolling(3, min_periods=3).max()
    f["high_prev"] = b["high"].shift(1)
    f["low_prev"] = b["low"].shift(1)
    f["low_min3"] = b["low"].rolling(3, min_periods=3).min()
    f["high_max3"] = b["high"].rolling(3, min_periods=3).max()
    f["low_min6"] = b["low"].rolling(6, min_periods=6).min()
    f["high_max6"] = b["high"].rolling(6, min_periods=6).max()
    # Returns use positional shifts, so a gap in the index means NaN, not a wrong return.
    f["ret24"] = b["close"] / b["close"].shift(24, freq="h").reindex(b.index) - 1.0
    f["ret168"] = b["close"] / b["close"].shift(168, freq="h").reindex(b.index) - 1.0

    q = _to_4h(b)
    q4 = pd.DataFrame(index=q.index)
    q4["close4h"] = q["close"]
    q4["atr4h"] = ind.atr(q["high"], q["low"], q["close"], 14)
    q4["ema20_4h"] = ind.ema(q["close"], 20)
    q4["ema50_4h"] = ind.ema(q["close"], 50)
    q4["ema200_4h"] = ind.ema(q["close"], 200)
    q4["adx4h"] = ind.adx(q["high"], q["low"], q["close"], 14)
    q4["adx4h_prev"] = q4["adx4h"].shift(1)
    q4["dc_high4h"], q4["dc_low4h"] = ind.donchian_prev(q["high"], q["low"], 20)
    q4["rvol4h"] = ind.rvol(q["volume"], 20)
    # Available at the close of the 4h bar's last hour → index the row at h+3.
    q4.index = q4.index + pd.Timedelta(hours=3)
    q4["is_4h_close"] = 1.0
    f = f.join(q4, how="left")
    f["is_4h_close"] = f["is_4h_close"].fillna(0.0).astype(bool)
    four_cols = [c for c in q4.columns if c != "is_4h_close"]
    f[four_cols] = f[four_cols].ffill(limit=4)   # carry until the next 4h close only
    f["above_ema50_4h"] = (f["close4h"] > f["ema50_4h"]).astype(float).where(f["ema50_4h"].notna())
    return f


def to_float32(f: pd.DataFrame) -> pd.DataFrame:
    out = f.copy()
    for c in out.columns:
        if out[c].dtype == np.float64:
            out[c] = out[c].astype(np.float32)
    return out
