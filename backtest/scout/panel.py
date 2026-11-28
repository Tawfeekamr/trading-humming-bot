"""Build the cross-sectional panel the walk-forward needs.

Pass 1 (per coin): features → keep OHLC, 1h log returns and the four
cross-sectional inputs (ret24, ret168, above_ema50_4h, natr1h).
Panel step: universe membership per hour, relative-strength rank, breadth,
median NATR, BTC daily trend → market state per hour.
Pass 2 (per coin): features again + rank + state → run detectors for every
grid cell, rows outside the universe dropped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from src.scout import indicators as ind
from src.scout import market_state as ms
from src.scout.data import load_bars
from src.scout.features import hourly_features
from src.scout.universe import build_daily_universe, load_daily_all

logger = logging.getLogger(__name__)

BTC = "BTCUSDT"


@dataclass
class Panel:
    index: pd.DatetimeIndex
    syms: list[str]
    ohlc: dict[str, tuple] = field(default_factory=dict)
    rets: dict[str, np.ndarray] = field(default_factory=dict)
    atr1h: dict[str, np.ndarray] = field(default_factory=dict)
    member: pd.DataFrame | None = None       # bool, hours × syms
    rank: pd.DataFrame | None = None         # float, hours × syms
    breadth: np.ndarray | None = None
    med_natr: np.ndarray | None = None
    btc_up: np.ndarray | None = None
    state: np.ndarray | None = None
    universe_daily: pd.DataFrame | None = None

    @property
    def n(self) -> int:
        return len(self.index)

    @property
    def day_index(self) -> np.ndarray:
        if not hasattr(self, "_days"):
            self._days = (self.index.asi8 // (86_400 * 10**9)).astype(np.int64)
        return self._days


def pct_rank_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True, method="average")


def compute_rank(ret24: pd.DataFrame, ret168: pd.DataFrame, member: pd.DataFrame) -> pd.DataFrame:
    """Mean of cross-sectional pct ranks of (coin − BTC) 24h and 168h returns."""
    rs24 = ret24.sub(ret24[BTC], axis=0).where(member)
    rs7 = ret168.sub(ret168[BTC], axis=0).where(member)
    return (pct_rank_rows(rs24) + pct_rank_rows(rs7)) / 2.0


def btc_up_hourly(btc_daily: pd.DataFrame, index: pd.DatetimeIndex) -> np.ndarray:
    """BTC daily close > EMA200(1d); day D is known at the close of hour D+23h."""
    up = (btc_daily["close"] > ind.ema(btc_daily["close"], 200)).astype(float)
    up = up.where(ind.ema(btc_daily["close"], 200).notna())
    up.index = up.index + pd.Timedelta(hours=23)
    return up.reindex(index, method="ffill", limit=48).to_numpy(dtype=float)


def build_panel(start: str, end: str, syms: Iterable[str] | None = None,
                daily: dict | None = None, bars_loader: Callable = load_bars) -> Panel:
    daily = daily if daily is not None else load_daily_all()
    U = build_daily_universe(daily)
    index = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23), freq="h")
    if syms is None:
        win = U.loc[start:end]
        syms = sorted(set(win.columns[win.any(axis=0)]) | {BTC})
    syms = list(syms)
    p = Panel(index=index, syms=[], universe_daily=U)
    cols = {k: {} for k in ("ret24", "ret168", "above", "natr")}
    for k, s in enumerate(syms, 1):
        try:
            bars = bars_loader(s, "1h")
        except FileNotFoundError:
            continue
        bars = bars[~bars.index.duplicated()].sort_index()
        f = hourly_features(bars).reindex(index)
        b = bars.reindex(index)
        p.syms.append(s)
        p.ohlc[s] = tuple(b[c].to_numpy(dtype=np.float64) for c in ("open", "high", "low", "close"))
        p.rets[s] = np.log(b["close"]).diff().to_numpy(dtype=np.float32)
        p.atr1h[s] = f["atr1h"].to_numpy(dtype=np.float64)
        cols["ret24"][s] = f["ret24"].astype(np.float32)
        cols["ret168"][s] = f["ret168"].astype(np.float32)
        cols["above"][s] = f["above_ema50_4h"].astype(np.float32)
        cols["natr"][s] = f["natr1h"].astype(np.float32)
        if k % 50 == 0:
            logger.info("pass1 %d/%d", k, len(syms))
    days = index.floor("D")
    member = U.reindex(columns=p.syms).reindex(days).fillna(False).astype(bool)
    member.index = index
    has_data = pd.DataFrame({s: ~np.isnan(p.ohlc[s][3]) for s in p.syms}, index=index)
    member &= has_data
    p.member = member
    ret24 = pd.DataFrame(cols["ret24"])
    ret168 = pd.DataFrame(cols["ret168"])
    p.rank = compute_rank(ret24, ret168, member).astype(np.float32)
    above = pd.DataFrame(cols["above"]).where(member)
    natr = pd.DataFrame(cols["natr"]).where(member)
    p.breadth = above.mean(axis=1, skipna=True).to_numpy(dtype=float)
    p.med_natr = natr.median(axis=1, skipna=True).to_numpy(dtype=float)
    p.btc_up = btc_up_hourly(daily[BTC], index)
    p.state = ms.classify(p.breadth, p.btc_up, p.med_natr)
    return p


def find_setups(p: Panel, detector_fn: Callable, param_sets: dict,
                bars_loader: Callable = load_bars) -> dict:
    """Run one detector for every param set. Returns {cell_key: candidates DataFrame}."""
    out = {k: [] for k in param_sets}
    pos = pd.Series(np.arange(p.n), index=p.index)
    for s in p.syms:
        bars = bars_loader(s, "1h")
        bars = bars[~bars.index.duplicated()].sort_index()
        f = hourly_features(bars).reindex(p.index)
        f["rank"] = p.rank[s].to_numpy(dtype=float)
        f["state"] = p.state
        f = f[p.member[s].to_numpy()]
        if f.empty:
            continue
        for key, params in param_sets.items():
            c = detector_fn(f, params)
            if c.empty:
                continue
            c = c.assign(sym=s, t=pos.loc[c.index].to_numpy(), state=f.loc[c.index, "state"].to_numpy())
            out[key].append(c)
    return {k: (pd.concat(v) if v else pd.DataFrame()) for k, v in out.items()}
