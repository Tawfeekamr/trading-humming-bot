"""Deterministic regime-distribution drift checks."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CLASS_DISTRIBUTION_SHIFT = "class_distribution_shift"
DANGER_FREQUENCY_SPIKE = "danger_frequency_spike"
LOW_CONFIDENCE = "low_confidence"
STALE_CACHE = "stale_cache"
FEATURE_CONTRACT_MISMATCH = "feature_contract_mismatch"

REASON_CODES = (
    CLASS_DISTRIBUTION_SHIFT,
    DANGER_FREQUENCY_SPIKE,
    LOW_CONFIDENCE,
    STALE_CACHE,
    FEATURE_CONTRACT_MISMATCH,
)


def _distribution(value: Mapping[Any, Any]) -> Mapping[Any, Any]:
    nested = value.get("class_distribution") if isinstance(value, Mapping) else None
    return nested if isinstance(nested, Mapping) else value


def _class_key(value: Any) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def compare_distribution(training: dict[int, float], live: dict[int, float]) -> dict:
    """Return signed per-class live-minus-training deltas and max magnitude."""
    training_dist = _distribution(training)
    live_dist = _distribution(live)
    classes = sorted(
        {_class_key(key) for key in training_dist} | {_class_key(key) for key in live_dist},
        key=lambda key: (isinstance(key, str), key),
    )
    deltas = {
        key: round(
            float(live_dist.get(key, live_dist.get(str(key), 0.0)))
            - float(training_dist.get(key, training_dist.get(str(key), 0.0))),
            12,
        )
        for key in classes
    }
    max_abs_delta = max((abs(delta) for delta in deltas.values()), default=0.0)
    return {
        "deltas": deltas,
        "per_class_deltas": deltas,
        "max_abs_delta": float(max_abs_delta),
    }


def evaluate_drift(
    training: dict[int, float],
    live: dict[int, float],
    confidence_24h: float | None,
    age_ms: int,
    ttl_ms: int,
    *,
    feature_contract_match: bool | None = None,
) -> list[str]:
    """Emit stable reason codes in the documented order.

    Distribution shift is flagged above 20 percentage points. A danger class
    at more than three times its training frequency is separately flagged.
    """
    training_dist = _distribution(training)
    live_dist = _distribution(live)
    comparison = compare_distribution(training_dist, live_dist)
    if feature_contract_match is None:
        training_hash = training.get("feature_contract_hash") if isinstance(training, Mapping) else None
        live_hash = live.get("feature_contract_hash") if isinstance(live, Mapping) else None
        if training_hash is not None and live_hash is not None:
            feature_contract_match = training_hash == live_hash
    reasons: list[str] = []
    if comparison["max_abs_delta"] > 0.20:
        reasons.append(CLASS_DISTRIBUTION_SHIFT)
    training_danger = float(training_dist.get(2, training_dist.get("2", 0.0)))
    live_danger = float(live_dist.get(2, live_dist.get("2", 0.0)))
    if (training_danger == 0.0 and live_danger > 0.0) or (
        training_danger > 0.0 and live_danger > training_danger * 3.0
    ):
        reasons.append(DANGER_FREQUENCY_SPIKE)

    if confidence_24h is None or float(confidence_24h) < 0.55:
        reasons.append(LOW_CONFIDENCE)
    if int(age_ms) > int(ttl_ms):
        reasons.append(STALE_CACHE)
    if feature_contract_match is False:
        reasons.append(FEATURE_CONTRACT_MISMATCH)
    return reasons
