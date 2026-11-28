"""End-to-end smoke test of the scout backtest pipeline on synthetic coins."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.scout.panel import build_panel, find_setups
from backtest.scout.portfolio import run_portfolio
from backtest.scout.walk_forward import folds, metrics, random_entry_null
from src.scout import market_state as ms
from src.scout.config import BreakoutParams, CommonParams, CostParams, PortfolioParams, PullbackParams
from src.scout.setups import detect_breakout, detect_pullback


def make_hourly(seed, drift, start="2023-01-01", days=330):
    n = days * 24
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.006, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.006, n))
    vol = rng.lognormal(10, 0.6, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol,
                         "quote_volume": vol * close}, index=idx)


def test_pipeline_runs_end_to_end():
    syms = {"BTCUSDT": make_hourly(0, 0.0002), "AAAUSDT": make_hourly(1, 0.0006),
            "BBBUSDT": make_hourly(2, -0.0004), "CCCUSDT": make_hourly(3, 0.0)}
    hourly = {s: df.drop(columns="quote_volume") for s, df in syms.items()}
    daily = {s: df.resample("1D").agg({"close": "last", "quote_volume": "sum"}) * [1, 1e3]
             for s, df in syms.items()}

    def loader(sym, interval):
        if sym not in hourly:
            raise FileNotFoundError(sym)
        return hourly[sym]

    p = build_panel("2023-08-01", "2023-11-26", syms=list(syms), daily=daily, bars_loader=loader)
    assert p.n == len(p.index) and set(p.syms) == set(syms)
    late = p.member.iloc[-1]
    assert late.all()                                    # all 4 qualify after 30 days
    r = p.rank.iloc[-1]
    assert r.between(0, 1).all()
    assert set(np.unique(p.state)) <= set(ms.NAMES)

    cands = find_setups(p, detect_pullback, {"d": PullbackParams(), "loose": PullbackParams(rank_min=0.6)},
                        bars_loader=loader)
    assert len(cands["loose"]) >= len(cands["d"])
    brk = find_setups(p, detect_breakout, {"d": BreakoutParams(rvol_min=1.0)}, bars_loader=loader)["d"]
    for c in (cands["loose"], brk):
        if c.empty:
            continue
        assert c["sym"].isin(list(syms)).all()
        # every candidate is inside the universe and allowed by the state at its hour
        for row in c.itertuples():
            assert p.member.iloc[row.t][row.sym]
            assert ms.allows(p.state[row.t], row.side)

    allc = pd.concat([x for x in (cands["loose"], brk) if not x.empty])
    trades, skips = run_portfolio(allc, p, PortfolioParams(), CostParams())
    fd = folds(p.index)[:1]
    m = metrics(trades, fd, 10_000)
    assert m["trades"] == len(trades)
    if trades:
        # the null only needs real trades to mirror; 5 runs keeps the test fast
        fd_all = [{"name": "all", "train": (0, 0), "test": (0, p.n)}]
        null = random_entry_null(p, trades[:10], fd_all, CostParams(), PortfolioParams(), CommonParams(), 5, 1)
        assert len(null) == 5


def test_pipeline_with_funding():
    from src.scout.config import FundingParams
    from src.scout.setups import detect_funding_fade
    syms = {"BTCUSDT": make_hourly(0, 0.0002), "AAAUSDT": make_hourly(1, 0.0006),
            "BBBUSDT": make_hourly(2, -0.0004), "CCCUSDT": make_hourly(3, 0.0)}
    hourly = {s: df.drop(columns="quote_volume") for s, df in syms.items()}
    daily = {s: df.resample("1D").agg({"close": "last", "quote_volume": "sum"}) * [1, 1e3]
             for s, df in syms.items()}
    rng = np.random.default_rng(7)

    def floader(sym):
        t = pd.date_range("2023-01-01", "2023-11-26", freq="8h", tz="UTC")
        return pd.DataFrame({"time": t, "f8": rng.normal(0, 0.0008, len(t))})

    p = build_panel("2023-08-01", "2023-11-26", syms=list(syms), daily=daily,
                    bars_loader=lambda s, i: hourly[s], funding_loader=floader)
    assert p.is_print.values.sum() > 0 and p.f_rank.stack().between(0, 1).all()
    c = find_setups(p, detect_funding_fade, {"l": FundingParams(f_min=0.0003, min_stop_atr=1.0)},
                    bars_loader=lambda s, i: hourly[s])["l"]
    assert not c.empty
    for row in c.itertuples():
        assert p.is_print.iloc[row.t][row.sym] and p.state[row.t] != ms.NO_TRADE
    trades, _ = run_portfolio(c, p)
    if trades:
        fd_all = [{"name": "all", "train": (0, 0), "test": (0, p.n)}]
        null = random_entry_null(p, trades[:5], fd_all, CostParams(), PortfolioParams(), CommonParams(), 3, 1,
                                 direction_gate=False)
        assert len(null) == 3
