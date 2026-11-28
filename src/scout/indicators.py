"""Causal technical indicators (pre-registration §4).

Every function takes pandas Series and returns a Series on the same index.
A value at row t only uses rows <= t. Warm-up rows are NaN.
Wilder smoothing = RMA (EWM with alpha=1/n); EMA alpha = 2/(n+1); both seeded
with the first value (pandas ewm adjust=False).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[:1] = (high - low).iloc[:1]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = rma(d.clip(lower=0), n)
    loss = rma((-d).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + gain / loss)
    return out.where(loss != 0, 100.0).where(gain.notna())


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr_s = rma(true_range(high, low, close), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * rma(plus_dm, n) / tr_s
        mdi = 100.0 * rma(minus_dm, n) / tr_s
        dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi)
    return rma(dx.fillna(0.0).where(tr_s.notna()), n)


def donchian_prev(high: pd.Series, low: pd.Series, n: int = 20) -> tuple[pd.Series, pd.Series]:
    """Max high / min low of the PREVIOUS n bars (current bar excluded)."""
    return (high.shift(1).rolling(n, min_periods=n).max(),
            low.shift(1).rolling(n, min_periods=n).min())


def rvol(volume: pd.Series, n: int = 20) -> pd.Series:
    """Bar volume / mean volume of the previous n bars."""
    base = volume.shift(1).rolling(n, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        return volume / base
