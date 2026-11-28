"""Funding-rate and open-interest features (pre-registration v3 §3). Pure.

Timing: a 1h row with open time r closes at r+1h. A funding print at time T
(rounded to the second) belongs to the row whose bar contains it, using
(open, close]: row = ceil_hour(T) − 1h. So an 08:00:00 settlement is known at
the close of the 07:00 row.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FFILL_LIMIT_H = 8          # a print is "current" for at most 8 hourly rows
OI_MAX_STALENESS = pd.Timedelta(minutes=30)


def funding_hourly(prints: pd.DataFrame, index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """Return (f8 as known at each row's close, is_print flag) on `index`."""
    if prints.empty:
        nan = pd.Series(np.nan, index=index)
        return nan, pd.Series(False, index=index)
    row = prints["time"].dt.ceil("h") - pd.Timedelta(hours=1)
    s = pd.Series(prints["f8"].to_numpy(dtype=float), index=row)
    s = s[~s.index.duplicated(keep="last")]
    is_print = pd.Series(index.isin(s.index), index=index)
    f8 = s.reindex(index).ffill(limit=FFILL_LIMIT_H)
    return f8, is_print


def oi_at(metrics: pd.DataFrame, t: pd.Timestamp) -> float:
    """Last OI snapshot at or before t, if no older than 30 minutes; else NaN."""
    m = metrics[metrics["time"] <= t]
    if m.empty:
        return float("nan")
    last = m.iloc[-1]
    return float(last["oi"]) if t - last["time"] <= OI_MAX_STALENESS else float("nan")


def oi_change_24h(metrics: pd.DataFrame, bar_close: pd.Timestamp) -> float:
    now, before = oi_at(metrics, bar_close), oi_at(metrics, bar_close - pd.Timedelta(hours=24))
    if not (now > 0 and before > 0):
        return float("nan")
    return now / before - 1.0
