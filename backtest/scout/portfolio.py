"""Portfolio simulation (pre-registration §7–§8).

Walks candidate setups in time order and applies: entry-zone check at the
next open, max open positions, one position per coin, 12h cooldown after a
stop exit, correlation cap vs open positions, daily realised-loss limit, and
risk-based sizing with a notional cap. Each trade's path is simulated by
`sim.simulate_trade`; the portfolio only decides which trades happen.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.scout import market_state as ms
from src.scout.config import CostParams, PortfolioParams

from .sim import simulate_trade


@dataclass
class Trade:
    sym: str
    detector: str
    side: int
    signal_idx: int
    entry_idx: int
    exit_idx: int
    reason: str
    entry_fill: float
    stop: float
    qty: float
    risk: float
    pnl: float
    r: float
    state: int
    score: float
    stop_dist_atr1h: float
    tps_hit: int
    legs: list


def corr_ok(rets: dict, sym: str, open_syms, t: int, window: int, cmax: float) -> bool:
    if not open_syms:
        return True
    a = rets[sym][max(0, t - window + 1): t + 1]
    for other in open_syms:
        b = rets[other][max(0, t - window + 1): t + 1]
        m = ~(np.isnan(a) | np.isnan(b))
        if m.sum() < window // 2:
            continue   # not enough overlap to judge → allow
        x, y = a[m], b[m]
        if x.std() == 0 or y.std() == 0:
            continue
        if float(np.corrcoef(x, y)[0, 1]) > cmax:
            return False
    return True


def run_portfolio(cands: pd.DataFrame, panel, pp: PortfolioParams = PortfolioParams(),
                  costs: CostParams = CostParams()) -> tuple[list[Trade], dict]:
    """cands: columns t (signal bar index), sym, side, entry_low, entry_high,
    stop, tp1..tp3, score, state, stop_dist_atr1h, detector."""
    trades: list[Trade] = []
    skips: dict[str, int] = defaultdict(int)
    if cands.empty:
        return trades, dict(skips)
    slip = costs.slippage_bps / 1e4
    days = panel.day_index            # np.ndarray int day number per hour index
    open_pos: dict[str, int] = {}     # sym → exit_idx
    cooldown: dict[str, int] = {}
    pending: list[tuple[int, float]] = []   # heap of realised legs
    day_pnl: dict[int, float] = defaultdict(float)
    loss_cap = -pp.daily_loss_pct * pp.budget

    cands = cands.sort_values(["t", "score"], ascending=[True, False])
    for t, grp in cands.groupby("t", sort=True):
        t = int(t)
        while pending and pending[0][0] <= t:
            idx, pnl = heapq.heappop(pending)
            day_pnl[int(days[idx])] += pnl
        for s in [s for s, e in open_pos.items() if e <= t]:
            del open_pos[s]
        i = t + 1
        if i >= panel.n:
            continue
        if day_pnl[int(days[i])] <= loss_cap:
            skips["daily_loss"] += len(grp)
            continue
        for row in grp.itertuples(index=False):
            if len(open_pos) >= pp.max_positions:
                skips["capacity"] += 1
                continue
            if row.sym in open_pos:
                skips["already_open"] += 1
                continue
            if cooldown.get(row.sym, -1) >= i:
                skips["cooldown"] += 1
                continue
            O, H, L, C = panel.ohlc[row.sym]
            o = O[i]
            if math.isnan(o) or not (row.entry_low <= o <= row.entry_high):
                skips["chased"] += 1
                continue
            if not corr_ok(panel.rets, row.sym, list(open_pos), t, pp.corr_window, pp.corr_max):
                skips["correlated"] += 1
                continue
            entry = o * (1.0 + row.side * slip)
            per_unit = abs(entry - row.stop)
            if per_unit <= 0 or (row.side > 0 and entry <= row.stop) or (row.side < 0 and entry >= row.stop):
                skips["bad_stop"] += 1
                continue
            risk_usd = pp.budget * pp.risk_pct * float(ms.size_mult(row.state))
            qty = min(risk_usd / per_unit, pp.max_notional / entry)
            res = simulate_trade(O, H, L, C, i, int(row.side), float(row.stop),
                                 (row.tp1, row.tp2, row.tp3), qty, costs, pp.max_hold_bars)
            if res is None:
                skips["no_data"] += 1
                continue
            trades.append(Trade(row.sym, row.detector, int(row.side), t, i, res.exit_idx,
                                res.reason, res.entry_fill, float(row.stop), qty, res.risk,
                                res.pnl, res.r, int(row.state), float(row.score),
                                float(row.stop_dist_atr1h), res.tps_hit, res.legs))
            for leg in res.legs:
                heapq.heappush(pending, leg)
            open_pos[row.sym] = res.exit_idx
            if res.reason in ("stop", "breakeven"):
                # any stop-triggered exit starts the 12h cooldown on that coin
                cooldown[row.sym] = res.exit_idx + pp.cooldown_bars
    return trades, dict(skips)
