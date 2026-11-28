"""Setup detectors (pre-registration §6).

Pure and vectorised: each detector takes a feature frame (from
`features.hourly_features`, plus `rank` and `state` columns) and returns the
rows that are setups. Backtest passes full history; live passes the latest
rows. Same code, same answers.

Output columns: side (+1/-1), close, entry_low, entry_high, stop, tp1..tp3,
stop_dist_atr1h (stop distance in 1h-ATR units, used by the random null),
score, detector.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import market_state as ms
from .config import BreakoutParams, CommonParams, FundingParams, PullbackParams

OUT_COLS = ["side", "close", "entry_low", "entry_high", "stop", "tp1", "tp2", "tp3",
            "stop_dist_atr1h", "score", "detector", "size_mult"]


def _b(x) -> np.ndarray:
    """NaN-safe boolean array (comparisons with NaN are already False)."""
    return np.asarray(x, dtype=bool)


def _finalize(f: pd.DataFrame, side: np.ndarray, lo: np.ndarray, hi: np.ndarray,
              stop: np.ndarray, detector: str, common: CommonParams,
              direction_gate: bool = True, strength: np.ndarray | None = None) -> pd.DataFrame:
    c = f["close"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        risk = np.abs(c - stop)
        dist = risk / c
        dist_atr = risk / f["atr1h"].to_numpy(dtype=float)
    state = f["state"].to_numpy() if "state" in f else np.full(len(f), ms.NEUTRAL)
    ok = (side != 0) & (dist >= common.min_stop_pct) & (dist <= common.max_stop_pct)
    if direction_gate:
        ok &= _b(ms.allows(state, side))
        size = ms.size_mult(state)
    else:   # contrarian detectors: only the NO_TRADE gate, full size otherwise (v3 §6)
        ok &= state != ms.NO_TRADE
        size = np.where(state != ms.NO_TRADE, 1.0, 0.0)
    ok &= np.where(side > 0, stop < c, stop > c)
    rank = f["rank"].to_numpy(dtype=float) if strength is None else strength
    out = pd.DataFrame({
        "side": side, "close": c, "entry_low": lo, "entry_high": hi, "stop": stop,
        "tp1": c + side * risk, "tp2": c + 2 * side * risk, "tp3": c + 3 * side * risk,
        "stop_dist_atr1h": dist_atr,
        "score": 100.0 * np.where(side > 0, rank, 1.0 - rank),
        "detector": detector,
        "size_mult": np.broadcast_to(np.asarray(size, dtype=float), c.shape),
    }, index=f.index)
    return out[ok]


def detect_breakout(f: pd.DataFrame, p: BreakoutParams = BreakoutParams(),
                    common: CommonParams = CommonParams()) -> pd.DataFrame:
    rank = f["rank"]
    base = _b(f["is_4h_close"]) & _b(f["rvol4h"] >= p.rvol_min) \
        & _b(f["adx4h"] >= p.adx_min) & _b(f["adx4h"] > f["adx4h_prev"])
    long_ = base & _b(rank >= p.rank_long) & _b(f["close4h"] > f["dc_high4h"])
    short = base & _b(rank <= 1.0 - p.rank_long) & _b(f["close4h"] < f["dc_low4h"])
    side = np.where(long_, 1, np.where(short, -1, 0))
    a4 = f["atr4h"].to_numpy(dtype=float)
    lvl = np.where(side > 0, f["dc_high4h"].to_numpy(dtype=float), f["dc_low4h"].to_numpy(dtype=float))
    lo = np.where(side > 0, lvl, lvl - p.zone_atr * a4)
    hi = np.where(side > 0, lvl + p.zone_atr * a4, lvl)
    stop = lvl - side * p.stop_atr * a4
    return _finalize(f, side, lo, hi, stop, "breakout", common)


def detect_pullback(f: pd.DataFrame, p: PullbackParams = PullbackParams(),
                    common: CommonParams = CommonParams()) -> pd.DataFrame:
    rank, c = f["rank"], f["close"]
    e20, e50, e200, a4 = f["ema20_4h"], f["ema50_4h"], f["ema200_4h"], f["atr4h"]
    long_ = (_b(rank >= p.rank_min) & _b(e20 > e50) & _b(e50 > e200)
             & _b(f["low_min3"] <= e20 + p.touch_atr4h * a4) & _b(c > e50)
             & _b(f["rsi1h_min3_prior"] >= p.rsi_lo) & _b(f["rsi1h_min3_prior"] <= p.rsi_hi)
             & _b(f["rsi1h"] > f["rsi1h_prev"]) & _b(c > f["high_prev"]))
    short = (_b(rank <= 1.0 - p.rank_min) & _b(e20 < e50) & _b(e50 < e200)
             & _b(f["high_max3"] >= e20 - p.touch_atr4h * a4) & _b(c < e50)
             & _b(f["rsi1h_max3_prior"] >= 100.0 - p.rsi_hi)
             & _b(f["rsi1h_max3_prior"] <= 100.0 - p.rsi_lo)
             & _b(f["rsi1h"] < f["rsi1h_prev"]) & _b(c < f["low_prev"]))
    side = np.where(long_, 1, np.where(short, -1, 0))
    cv = c.to_numpy(dtype=float)
    a1 = f["atr1h"].to_numpy(dtype=float)
    long_stop = np.minimum(f["low_min6"].to_numpy(dtype=float) - p.stop_buffer_atr * a1,
                           cv - p.min_stop_atr * a1)
    short_stop = np.maximum(f["high_max6"].to_numpy(dtype=float) + p.stop_buffer_atr * a1,
                            cv + p.min_stop_atr * a1)
    stop = np.where(side > 0, long_stop, short_stop)
    return _finalize(f, side, cv - p.zone_atr * a1, cv + p.zone_atr * a1, stop, "pullback", common)


def detect_funding_fade(f: pd.DataFrame, p: FundingParams = FundingParams(),
                        common: CommonParams = CommonParams()) -> pd.DataFrame:
    """v3 detector C: fade the crowded side on a funding print row once the bar turns."""
    f8, fr, c = f["f8"], f["f_rank"], f["close"]
    pr = _b(f["is_print"])
    short = (pr & _b(f8 >= p.f_min) & _b(fr >= p.rank_hi) & _b(f["ret24"] > 0)
             & _b(c < f["low_prev"]))
    long_ = (pr & _b(f8 <= -p.f_min) & _b(fr <= 1.0 - p.rank_hi) & _b(f["ret24"] < 0)
             & _b(c > f["high_prev"]))
    side = np.where(long_, 1, np.where(short, -1, 0))
    cv = c.to_numpy(dtype=float)
    a1 = f["atr1h"].to_numpy(dtype=float)
    long_stop = np.minimum(f["low_min6"].to_numpy(dtype=float) - p.stop_buffer_atr * a1,
                           cv - p.min_stop_atr * a1)
    short_stop = np.maximum(f["high_max6"].to_numpy(dtype=float) + p.stop_buffer_atr * a1,
                            cv + p.min_stop_atr * a1)
    stop = np.where(side > 0, long_stop, short_stop)
    # _finalize scores longs as 100·x and shorts as 100·(1−x). Passing x = 1 − f_rank
    # gives shorts 100·f_rank and longs 100·(1 − f_rank): more extreme = higher score.
    strength = 1.0 - fr.to_numpy(dtype=float)
    out = _finalize(f, side, cv - p.zone_atr * a1, cv + p.zone_atr * a1, stop, "funding",
                    common, direction_gate=False, strength=strength)
    return out


def oi_filter(cands: pd.DataFrame, oi_chg: pd.Series, min_chg: float = 0.10) -> pd.DataFrame:
    """v3 detector D = C candidates whose 24h OI change >= min_chg. oi_chg is indexed
    like cands' rows by (sym, t); missing OI → dropped."""
    if cands.empty:
        return cands
    key = pd.MultiIndex.from_arrays([cands["sym"], cands["t"]])
    v = oi_chg.reindex(key).to_numpy(dtype=float)
    out = cands[np.asarray(v >= min_chg, dtype=bool)].copy()
    out["detector"] = "funding_oi"
    return out


DETECTORS = {"breakout": detect_breakout, "pullback": detect_pullback, "funding": detect_funding_fade}
