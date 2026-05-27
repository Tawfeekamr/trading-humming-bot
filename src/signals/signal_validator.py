"""
signal_validator.py — Validates parsed signals against risk rules before execution.
"""

import logging
from typing import Optional

from .signal_parser import ParsedSignal, SignalAction

logger = logging.getLogger(__name__)


class SignalValidator:
    """Validates parsed signals before execution."""

    def __init__(self, config: dict, available_pairs: Optional[set[str]] = None):
        self._min_rr_ratio = config.get("min_rr_ratio", 1.5)
        self._max_sl_distance_pct = config.get("max_sl_distance_pct", 5.0)
        self._max_entry_zone_pct = config.get("max_entry_zone_pct", 3.0)
        self._available_pairs = available_pairs or set()
        self._blacklisted_pairs = set(config.get("blacklisted_pairs", []))

    def set_available_pairs(self, pairs: set[str]):
        self._available_pairs = pairs

    def validate(self, signal: ParsedSignal) -> tuple[bool, str]:
        """Returns (valid: bool, rejection_reason: str). Empty string if valid."""
        if signal.action == SignalAction.NOT_A_SIGNAL:
            return False, "Not a trade signal"

        if signal.action != SignalAction.OPEN_LONG:
            return True, ""  # CLOSE/UPDATE signals don't need full validation

        # Pair must exist on exchange
        if signal.pair:
            pair_variants = {signal.pair, signal.pair.replace("-", "")}
            if self._available_pairs and not pair_variants.intersection(self._available_pairs):
                return False, f"Pair {signal.pair} not available on exchange"

        # Not blacklisted
        if signal.pair in self._blacklisted_pairs:
            return False, f"Pair {signal.pair} is blacklisted"

        # Must have stop-loss
        if signal.stop_loss is None:
            return False, "No stop-loss specified"

        # Must have at least one take-profit
        if not signal.take_profits:
            return False, "No take-profit target specified"

        # Must have entry price
        if signal.entry_high is None and signal.entry_low is None:
            return False, "No entry price specified"

        entry = signal.entry_high or signal.entry_low
        if entry is None or entry <= 0:
            return False, "Invalid entry price"

        # Stop-loss distance check
        if signal.stop_loss >= entry:
            return False, f"SL {signal.stop_loss} >= entry {entry}"
        sl_distance = (entry - signal.stop_loss) / entry * 100
        if sl_distance > self._max_sl_distance_pct:
            return False, f"SL distance {sl_distance:.1f}% > max {self._max_sl_distance_pct}%"

        # Risk:reward ratio check (using TP3 if available, else TP2, else TP1)
        risk = entry - signal.stop_loss
        tp_index = min(2, len(signal.take_profits) - 1)  # TP3 preferred
        tp_label = f"TP{tp_index + 1}"
        reward = signal.take_profits[tp_index] - entry
        if reward <= 0:
            return False, f"{tp_label} {signal.take_profits[tp_index]} <= entry {entry}"
        rr = reward / risk
        if rr < self._min_rr_ratio:
            return False, f"R:R {rr:.2f} (vs {tp_label}) < min {self._min_rr_ratio}"

        # Entry zone width check
        if signal.entry_low is not None and signal.entry_high is not None and signal.entry_low > 0:
            zone_pct = (signal.entry_high - signal.entry_low) / signal.entry_low * 100
            if zone_pct > self._max_entry_zone_pct:
                return False, f"Entry zone {zone_pct:.1f}% too wide (max {self._max_entry_zone_pct}%)"

        return True, ""
