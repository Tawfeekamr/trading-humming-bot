"""Single-trade simulator on 1h bars (pre-registration §7).

Mirrors the live futures signal path (`SignalEngine._manage_futures_positions`):
  TP1 → close 33% and move stop to entry; TP2 → close 50% of remaining
  (stop stays at entry); TP3 → close the rest.
Conservative bar rules:
  * stop is checked before targets; a gap through the stop fills at the open
  * targets fill at the target price (live fills at market, usually better)
  * after a target fills, if the same bar also reached the (new) stop, the rest
    closes at the stop
Costs: fee + adverse slippage on every fill, funding per hour held.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.scout.config import CostParams

TP_CLOSE_PCT = (0.33, 0.50, 1.0)


@dataclass
class TradeResult:
    entry_idx: int
    entry_fill: float
    exit_idx: int
    reason: str            # stop | breakeven | tp3 | time | data_end
    pnl: float
    risk: float            # qty * |entry_fill - initial stop|
    legs: list = field(default_factory=list)   # (bar_idx, pnl) — realised cash flows
    tps_hit: int = 0

    @property
    def r(self) -> float:
        return self.pnl / self.risk if self.risk > 0 else 0.0


def simulate_trade(o, h, l, c, i: int, side: int, stop: float, tps, qty: float,
                   costs: CostParams, max_hold: int) -> TradeResult | None:
    """Enter at the open of bar i. Returns None if bar i has no data."""
    n = len(o)
    if i >= n or math.isnan(o[i]):
        return None
    slip = costs.slippage_bps / 1e4
    fee = costs.fee_bps / 1e4
    fund_h = costs.funding_per_8h / 8.0
    entry = float(o[i]) * (1.0 + side * slip)
    risk = qty * abs(entry - stop)
    legs = [(i, -qty * entry * fee)]
    remaining = qty
    stop_cur = stop
    hit = 0
    funding = 0.0
    last_valid = i

    def close_leg(j, price_raw, q):
        fill = price_raw * (1.0 - side * slip)
        legs.append((j, side * (fill - entry) * q - q * fill * fee))

    end = min(i + max_hold, n)
    for j in range(i, end):
        oj = o[j]
        if math.isnan(oj):
            continue
        last_valid = j
        hj, lj = h[j], l[j]
        funding += remaining * entry * fund_h
        adverse = lj if side > 0 else hj
        # 1) stop first
        if (adverse <= stop_cur) if side > 0 else (adverse >= stop_cur):
            px = stop_cur
            if j > i:   # gap through the stop fills at the open (entry bar opens at entry)
                px = min(stop_cur, oj) if side > 0 else max(stop_cur, oj)
            close_leg(j, px, remaining)
            legs.append((j, -funding))
            return TradeResult(i, entry, j, "breakeven" if hit else "stop",
                               sum(p for _, p in legs), risk, legs, hit)
        # 2) targets in order
        favorable = hj if side > 0 else lj
        while hit < 3 and ((favorable >= tps[hit]) if side > 0 else (favorable <= tps[hit])):
            q = remaining * TP_CLOSE_PCT[hit]
            close_leg(j, tps[hit], q)
            remaining -= q
            hit += 1
            if hit == 1:
                stop_cur = entry
            if hit == 3 or remaining <= 1e-15:
                legs.append((j, -funding))
                return TradeResult(i, entry, j, "tp3", sum(p for _, p in legs), risk, legs, hit)
            # same bar already reached the new stop → assume it filled after the target
            if (adverse <= stop_cur) if side > 0 else (adverse >= stop_cur):
                close_leg(j, stop_cur, remaining)
                legs.append((j, -funding))
                return TradeResult(i, entry, j, "breakeven", sum(p for _, p in legs), risk, legs, hit)
    reason = "time" if last_valid == end - 1 and end == i + max_hold else "data_end"
    close_leg(last_valid, float(c[last_valid]), remaining)
    legs.append((last_valid, -funding))
    return TradeResult(i, entry, last_valid, reason, sum(p for _, p in legs), risk, legs, hit)
