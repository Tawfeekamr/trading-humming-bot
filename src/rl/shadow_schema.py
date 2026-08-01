"""Validated, shadow-only routing decision contract.

The schema deliberately uses the canonical action map as its source of truth:
there is no second list of engines or size multipliers that can drift from PPO.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping

from src.rl.action_map import ACTION_TO_ENGINE_SIZE


_VALID_ENGINES = {engine for engine, _size in ACTION_TO_ENGINE_SIZE}
_VALID_MODES = {"shadow"}


@dataclass(frozen=True, slots=True)
class ShadowRoutingDecision:
    """One PPO decision that is safe to persist outside active routing state."""

    timestamp_ms: int
    pair: str
    action: int
    engine: str
    size_mult: float
    model_version: str
    model_sha256: str
    observation_age_ms: int
    mode: str = "shadow"

    @classmethod
    def from_action(
        cls,
        pair: str,
        action: int,
        decision: Mapping[str, Any],
        model_version: str,
        *,
        timestamp_ms: int | None = None,
        observation_age_ms: int = 0,
        model_sha256: str = "",
    ) -> "ShadowRoutingDecision":
        """Build a decision from the live decoder payload.

        ``decision`` uses the live router's ``active_engine`` key while the
        persisted contract intentionally calls that field ``engine``.
        """
        if timestamp_ms is None:
            import time

            timestamp_ms = time.time_ns() // 1_000_000
        engine = decision.get("engine", decision.get("active_engine"))
        if engine is None or "size_mult" not in decision:
            raise ValueError("decoded action must contain engine and size_mult")
        return cls(
            timestamp_ms=int(timestamp_ms),
            pair=str(pair),
            action=int(action),
            engine=str(engine),
            size_mult=float(decision["size_mult"]),
            model_version=str(model_version),
            model_sha256=str(model_sha256),
            observation_age_ms=int(observation_age_ms),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON representation."""
        return asdict(self)


def validate_decision(
    decision: dict, now_ms: int, max_age_ms: int
) -> tuple[bool, str]:
    """Validate an untrusted decision before it enters the shadow journal.

    The returned reason is stable enough for logs and focused tests. Validation
    is intentionally fail-safe: malformed input returns ``(False, reason)``
    rather than raising from a logger or routing loop.
    """
    if not isinstance(decision, dict):
        return False, "decision must be an object"
    for field in ("model_version", "model_sha256"):
        value = decision.get(field)
        if not isinstance(value, str) or not value.strip():
            return False, f"missing or invalid {field}"

    if decision.get("mode") not in _VALID_MODES:
        return False, "mode must be shadow"

    try:
        action = decision["action"]
    except KeyError:
        return False, "missing action"
    if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action < len(ACTION_TO_ENGINE_SIZE):
        return False, "action outside 0..9"

    engine = decision.get("engine")
    if engine not in _VALID_ENGINES:
        return False, "unknown engine"

    try:
        size_mult = float(decision["size_mult"])
    except (KeyError, TypeError, ValueError):
        return False, "missing or invalid size multiplier"
    if not isfinite(size_mult):
        return False, "invalid size multiplier"
    expected_engine, expected_size = ACTION_TO_ENGINE_SIZE[action]
    if engine != expected_engine or size_mult != float(expected_size):
        return False, "size multiplier outside action map"

    try:
        age = int(decision["observation_age_ms"])
    except (KeyError, TypeError, ValueError):
        return False, "missing or invalid observation age"
    if age < 0 or age > int(max_age_ms):
        return False, "stale observation"

    try:
        timestamp_ms = int(decision["timestamp_ms"])
    except (KeyError, TypeError, ValueError):
        return False, "missing or invalid timestamp"
    if timestamp_ms < 0:
        return False, "invalid timestamp"
    if int(now_ms) - timestamp_ms > int(max_age_ms):
        return False, "stale observation"

    pair = decision.get("pair")
    if not isinstance(pair, str) or not pair:
        return False, "missing pair"
    return True, "valid"
