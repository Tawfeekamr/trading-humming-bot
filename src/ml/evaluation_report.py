"""Deterministic walk-forward evaluation metrics and JSON reports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

_BOOTSTRAP_SEED = 42
_BOOTSTRAP_SAMPLES = 2000


def _as_array(values: Sequence[float], name: str) -> np.ndarray:
    array = np.asarray(list(values), dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    return array


def _drawdown(returns: np.ndarray) -> float:
    if not len(returns):
        return 0.0
    # The evaluator passes normalized per-step PnL. An additive equity curve
    # makes fees (also normalized PnL) and the reported net PnL comparable.
    equity = 1.0 + np.cumsum(returns)
    peaks = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(peaks > 0, (peaks - equity) / peaks, 0.0)
    return float(np.max(drawdowns, initial=0.0))


def _profit_factor(returns: np.ndarray) -> float | None:
    gains = float(np.sum(returns[returns > 0]))
    losses = float(-np.sum(returns[returns < 0]))
    if losses == 0.0:
        return None if gains > 0.0 else 0.0
    return gains / losses


def _point_metrics(
    returns: np.ndarray,
    exposure: np.ndarray,
    fees: float,
    trade_count: int,
) -> dict[str, float | int | None]:
    net_pnl = float(np.sum(returns) - fees)
    return {
        "trade_count": int(trade_count),
        "net_pnl": net_pnl,
        "total_return": net_pnl,
        "profit_factor": _profit_factor(returns),
        "max_drawdown": _drawdown(returns),
        "time_in_market": float(np.mean(exposure)) if len(exposure) else 0.0,
        "fees": float(fees),
    }

def _bootstrap_intervals(
    returns: np.ndarray, exposure: np.ndarray, fees: float
) -> dict[str, dict[str, float | None]]:
    if not len(returns):
        names = ("net_pnl", "total_return", "profit_factor", "max_drawdown", "time_in_market")
        return {name: {"level": 0.95, "low": 0.0, "high": 0.0} for name in names}

    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    indices = rng.integers(0, len(returns), size=(_BOOTSTRAP_SAMPLES, len(returns)))
    sampled_returns = returns[indices]
    sampled_exposure = exposure[indices] if len(exposure) else np.zeros_like(sampled_returns)
    net_pnl = sampled_returns.sum(axis=1) - fees
    total_return = net_pnl
    gains = np.maximum(sampled_returns, 0.0).sum(axis=1)
    losses = np.maximum(-sampled_returns, 0.0).sum(axis=1)
    profit_factor = np.divide(
        gains, losses, out=np.full_like(gains, np.nan), where=losses > 0
    )
    equity = 1.0 + np.cumsum(sampled_returns, axis=1)
    peaks = np.maximum.accumulate(equity, axis=1)
    drawdowns = np.divide(
        peaks - equity,
        peaks,
        out=np.zeros_like(equity),
        where=peaks > 0,
    ).max(axis=1)
    time_in_market = sampled_exposure.mean(axis=1)

    values = {
        "net_pnl": net_pnl,
        "total_return": total_return,
        "profit_factor": profit_factor,
        "max_drawdown": drawdowns,
        "time_in_market": time_in_market,
    }
    intervals: dict[str, dict[str, float | None]] = {}
    for name, samples in values.items():
        finite = samples[np.isfinite(samples)]
        if not len(finite):
            low = high = None
        else:
            low, high = np.quantile(finite, [0.025, 0.975])
            low, high = float(low), float(high)
        intervals[name] = {"level": 0.95, "low": low, "high": high}
    return intervals


def summarize_returns(
    returns: Sequence[float],
    exposure: Sequence[float],
    fees: float,
    trade_count: int | None = None,
) -> dict[str, Any]:
    """Summarize normalized returns with fixed-seed bootstrap intervals."""
    ret = _as_array(returns, "returns")
    exp = _as_array(exposure, "exposure")
    if len(ret) != len(exp):
        raise ValueError("returns and exposure must have the same length")
    if np.any((exp < 0.0) | (exp > 1.0)):
        raise ValueError("exposure values must be between 0 and 1")
    try:
        fee_total = float(fees)
    except (TypeError, ValueError) as exc:
        raise ValueError("fees must be numeric") from exc
    if not np.isfinite(fee_total) or fee_total < 0:
        raise ValueError("fees must be a finite non-negative number")
    if trade_count is None:
        independent_trades = len(ret)
    else:
        if isinstance(trade_count, bool) or int(trade_count) != trade_count or trade_count < 0:
            raise ValueError("trade_count must be a non-negative integer")
        independent_trades = int(trade_count)

    metrics = _point_metrics(ret, exp, fee_total, independent_trades)
    metrics["confidence_intervals"] = _bootstrap_intervals(ret, exp, fee_total)
    return metrics


def _json_safe(value: Any) -> Any:
    import math

    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    return value


def write_report(path: str, metadata: dict, metrics: dict, slices: list[dict]) -> None:
    """Write a stable, key-sorted JSON report including all OOS slices."""
    payload = _json_safe({"metadata": metadata, "metrics": metrics, "slices": slices})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
 
