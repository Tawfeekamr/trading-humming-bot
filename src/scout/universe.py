"""Daily universe (pre-registration §3).

For UTC day D the universe uses daily bars up to D-1 only:
  * >= 30 daily bars of history
  * 30-day average quote volume >= $20M
  * top 60 by that average
Symbol filtering (USDT perp, no stablecoin/index) happens at download time
(`data.is_tradeable_symbol`) and is re-applied here.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd

from .data import cached_symbols, is_tradeable_symbol, load_bars

TOP_N = 60
MIN_AVG_QUOTE_VOL = 20e6
MIN_AGE_DAYS = 30
VOL_WINDOW = 30


def build_daily_universe(daily: Mapping[str, pd.DataFrame], top_n: int = TOP_N,
                         min_avg_vol: float = MIN_AVG_QUOTE_VOL,
                         min_age: int = MIN_AGE_DAYS) -> pd.DataFrame:
    """Return a bool DataFrame (index = UTC day, columns = symbols)."""
    vols, ages = {}, {}
    for sym, df in daily.items():
        if not is_tradeable_symbol(sym) or df.empty:
            continue
        idx = pd.date_range(df.index.min(), df.index.max(), freq="D")
        qv = df["quote_volume"].reindex(idx)
        # Shift 1 day: day D only sees data through D-1.
        vols[sym] = qv.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean().shift(1)
        ages[sym] = qv.notna().cumsum().shift(1)
    if not vols:
        return pd.DataFrame()
    vol = pd.DataFrame(vols)
    age = pd.DataFrame(ages).reindex_like(vol)
    eligible = vol.where((age >= min_age) & (vol >= min_avg_vol))
    rank = eligible.rank(axis=1, ascending=False, method="first")
    return (rank <= top_n).fillna(False)


def load_daily_all() -> dict[str, pd.DataFrame]:
    return {s: load_bars(s, "1d") for s in cached_symbols("1d")}


def ever_in_universe(start: str, end: str) -> set[str]:
    u = build_daily_universe(load_daily_all())
    window = u.loc[start:end]
    return set(window.columns[window.any(axis=0)])
