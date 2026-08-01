"""Conservative, report-only promotion gates for ML/RL comparisons."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_RETURN_TOLERANCE = 0.02
_MATERIAL_RELATIVE_IMPROVEMENT = 0.10


def _first(metrics: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in metrics and metrics[key] is not None:
            return metrics[key]
    return default


def _number(metrics: Mapping[str, Any], *keys: str) -> float | None:
    value = _first(metrics, *keys)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_counts(metrics: Mapping[str, Any]) -> list[float]:
    counts: list[float] = []
    for key in ("trade_count", "ppo_trade_count", "rf_trade_count", "ml_trade_count", "ta_trade_count"):
        value = _number(metrics, key)
        if value is not None:
            counts.append(value)

    strategy_map = next(
        (metrics[key] for key in ("trade_counts_by_strategy", "trades_by_strategy")
         if isinstance(metrics.get(key), Mapping)),
        None,
    )
    if strategy_map is not None:
        for strategy in ("ppo", "rf", "ta"):
            value = strategy_map.get(strategy)
            if value is None:
                counts.append(0.0)
            else:
                try:
                    counts.append(float(value))
                except (TypeError, ValueError):
                    counts.append(0.0)

    regime_map = next(
        (metrics[key] for key in ("trade_counts_by_regime", "trade_count_by_regime", "trades_by_regime")
         if isinstance(metrics.get(key), Mapping)),
        None,
    )
    if regime_map is not None:
        regime_names = set()
        for strategy_values in regime_map.values():
            if isinstance(strategy_values, Mapping):
                regime_names.update(strategy_values)
        for strategy in ("ppo", "rf", "ta"):
            strategy_values = regime_map.get(strategy)
            for regime in regime_names:
                value = strategy_values.get(regime) if isinstance(strategy_values, Mapping) else None
                try:
                    counts.append(float(value))
                except (TypeError, ValueError):
                    counts.append(0.0)
    return counts


def _window_count(metrics: Mapping[str, Any]) -> int | None:
    direct = _number(metrics, "window_count", "windows", "slices", "n_windows")
    if direct is not None:
        return int(direct)
    for key in ("per_window", "per_slice", "window_metrics", "slice_metrics"):
        value = metrics.get(key)
        if isinstance(value, (list, tuple)):
            return len(value)
    return None


def _materially_lower(value: float, baseline: float) -> bool:
    if baseline <= 0:
        return value < baseline
    return value <= baseline * (1.0 - _MATERIAL_RELATIVE_IMPROVEMENT)


def evaluate(metrics: dict, min_trades: int = 100) -> dict[str, Any]:
    """Evaluate a candidate without activating or routing it automatically.

    The result is deliberately a decision object only. Callers must perform
    any deployment or routing change separately after human review.
    """
    if min_trades <= 0:
        raise ValueError("min_trades must be positive")
    reasons: list[str] = []
    counts = _sample_counts(metrics)
    if not counts or any(count < min_trades for count in counts):
        reasons.append("inconclusive_sample")

    windows = _window_count(metrics)
    if windows is not None and windows < 2:
        reasons.append("insufficient_windows")

    # PPO-vs-RF is the canonical RL comparison; ML-vs-TA aliases support the
    # same report/gate for the supervised candidate without changing callers.
    candidate_prefix = "ppo" if any(key.startswith("ppo_") for key in metrics) else "ml"
    baseline_prefix = "rf" if candidate_prefix == "ppo" else "ta"
    candidate_pf = _number(metrics, f"{candidate_prefix}_profit_factor", "profit_factor")
    baseline_pf = _number(metrics, f"{baseline_prefix}_profit_factor")
    candidate_return = _number(metrics, f"{candidate_prefix}_return", f"{candidate_prefix}_total_return", "total_return")
    baseline_return = _number(metrics, f"{baseline_prefix}_return", f"{baseline_prefix}_total_return")
    candidate_dd = _number(metrics, f"{candidate_prefix}_max_drawdown", "max_drawdown")
    baseline_dd = _number(metrics, f"{baseline_prefix}_max_drawdown")
    candidate_exposure = _number(metrics, f"{candidate_prefix}_exposure", f"{candidate_prefix}_time_in_market", "time_in_market")
    baseline_exposure = _number(metrics, f"{baseline_prefix}_exposure", f"{baseline_prefix}_time_in_market")

    if candidate_dd is None or baseline_dd is None:
        reasons.append("missing_drawdown")
    elif candidate_dd > baseline_dd:
        reasons.append("drawdown_increase")
    if candidate_exposure is None or baseline_exposure is None:
        reasons.append("missing_exposure")
    elif candidate_exposure > baseline_exposure:
        reasons.append("exposure_increase")

    risk_improvement = (
        candidate_pf is not None
        and baseline_pf is not None
        and candidate_pf > baseline_pf
    )
    return_parity = (
        candidate_return is not None
        and baseline_return is not None
        and candidate_return >= baseline_return - _RETURN_TOLERANCE
    )
    materially_lower_risk = (
        candidate_dd is not None
        and baseline_dd is not None
        and candidate_exposure is not None
        and baseline_exposure is not None
        and _materially_lower(candidate_dd, baseline_dd)
        and _materially_lower(candidate_exposure, baseline_exposure)
    )
    # Equal PF plus materially lower risk is also a useful risk-adjusted parity
    # case when a report does not carry return columns (the canonical brief's
    # PPO-vs-RF example uses this shape).
    pf_parity = (
        candidate_pf is not None
        and baseline_pf is not None
        and candidate_pf >= baseline_pf
    )
    if not risk_improvement and not (return_parity and materially_lower_risk) and not (pf_parity and materially_lower_risk):
        reasons.append("no_risk_adjusted_improvement_or_return_parity")

    blocking = {
        "inconclusive_sample",
        "insufficient_windows",
        "missing_drawdown",
        "drawdown_increase",
        "missing_exposure",
        "exposure_increase",
        "no_risk_adjusted_improvement_or_return_parity",
    }
    eligible = not any(reason in blocking for reason in reasons)
    if eligible:
        reasons.append("human_review_required")
    return {"eligible": eligible, "reasons": reasons}
