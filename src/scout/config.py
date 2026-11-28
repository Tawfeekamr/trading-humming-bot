"""Strict parameter objects. Unknown keys raise — no silently ignored config
(this repo has had three bugs from config keys nothing read)."""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping, Type, TypeVar

T = TypeVar("T")


def strict_from_dict(cls: Type[T], data: Mapping[str, Any] | None) -> T:
    data = dict(data or {})
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown config key(s) {unknown}")
    return cls(**data)


@dataclass(frozen=True)
class BreakoutParams:
    rank_long: float = 0.80
    rvol_min: float = 2.0
    adx_min: float = 20.0
    zone_atr: float = 0.5
    stop_atr: float = 1.5


@dataclass(frozen=True)
class PullbackParams:
    rank_min: float = 0.70
    touch_atr4h: float = 0.25
    rsi_lo: float = 40.0
    rsi_hi: float = 50.0
    stop_buffer_atr: float = 0.1
    min_stop_atr: float = 1.0
    zone_atr: float = 0.25


@dataclass(frozen=True)
class FundingParams:
    f_min: float = 0.0005          # |f8| threshold (0.05% per 8h)
    rank_hi: float = 0.90          # short needs f_rank >= this; long needs <= 1 - this
    stop_buffer_atr: float = 0.1
    min_stop_atr: float = 1.5
    zone_atr: float = 0.25


@dataclass(frozen=True)
class CommonParams:
    min_stop_pct: float = 0.005
    max_stop_pct: float = 0.10


@dataclass(frozen=True)
class CostParams:
    fee_bps: float = 5.0
    slippage_bps: float = 8.0
    funding_per_8h: float = 0.0001


@dataclass(frozen=True)
class PortfolioParams:
    budget: float = 10_000.0
    risk_pct: float = 0.0075
    max_notional: float = 7_500.0
    max_positions: int = 3
    max_hold_bars: int = 72
    cooldown_bars: int = 12
    corr_window: int = 720
    corr_max: float = 0.75
    daily_loss_pct: float = 0.03
