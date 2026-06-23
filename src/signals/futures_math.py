# src/signals/futures_math.py
"""Pure futures math: estimated liquidation price, side-aware PnL, SL-before-
liquidation gate. Pure functions so they're trivially unit-testable.

Liquidation is an ESTIMATE (isolated margin, maintenance rate default 0.4%).
The exchange's actual liquidation_price (from get_position) is authoritative
and should be logged after open; this estimate is the pre-open gate.
"""
from typing import Literal

Side = Literal["long", "short"]


def estimate_liquidation(entry: float, leverage: float, side: Side, maint_rate: float = 0.004) -> float:
    if leverage <= 0:
        return entry
    if side == "long":
        return entry * (1 - 1.0 / leverage + maint_rate)
    return entry * (1 + 1.0 / leverage - maint_rate)


def pnl(side: Side, entry: float, exit_price: float, qty: float) -> float:
    if side == "long":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


def sl_triggers_before_liquidation(side: Side, entry: float, sl: float, leverage: float) -> bool:
    liq = estimate_liquidation(entry, leverage, side)
    if side == "long":
        return sl > liq
    return sl < liq
