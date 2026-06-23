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
        self._min_quality_score = config.get("min_quality_score", 5)
        self._allow_shorts = config.get("allow_shorts", False)
        self._available_pairs = available_pairs or set()
        self._blacklisted_pairs = set(config.get("blacklisted_pairs", []))

    def set_available_pairs(self, pairs: set[str]):
        self._available_pairs = pairs

    def validate(self, signal: ParsedSignal) -> tuple[bool, str]:
        """Returns (valid: bool, rejection_reason: str). Empty string if valid."""
        if signal.action == SignalAction.NOT_A_SIGNAL:
            return False, "Not a trade signal"

        if signal.action not in (SignalAction.OPEN_LONG, SignalAction.OPEN_SHORT):
            return True, ""  # CLOSE/UPDATE signals don't need full validation

        # Pair must exist on Gate.io (skip check if pair list hasn't loaded yet)
        if signal.pair and self._available_pairs:
            pair_variants = {signal.pair, signal.pair.replace("-", "")}
            if not pair_variants.intersection(self._available_pairs):
                logger.warning(f"Pair {signal.pair} not found in {len(self._available_pairs)} Gate.io pairs — allowing anyway (may be newly listed)")
                # Don't reject — Gate.io lists new pairs frequently, and the exchange
                # will reject the order if the pair truly doesn't exist

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

        # Direction-aware stop-loss + take-profit validity + reward.
        is_short = signal.action == SignalAction.OPEN_SHORT
        if is_short and not self._allow_shorts:
            return False, "Short signals not enabled (spot)"

        if is_short:
            if signal.stop_loss <= entry:
                return False, f"SL {signal.stop_loss} <= entry {entry} (short SL must be above entry)"
            sl_distance = (signal.stop_loss - entry) / entry * 100
            # Farthest TP for a short = lowest price (largest reward). Select by
            # value rather than index so we don't depend on TP sort order.
            farthest_tp = min(signal.take_profits)
            tp_label = f"TP{signal.take_profits.index(farthest_tp) + 1}"
            reward = entry - farthest_tp
            if reward <= 0:
                return False, f"{tp_label} {farthest_tp} >= entry {entry} (short TP must be below entry)"
        else:
            if signal.stop_loss >= entry:
                return False, f"SL {signal.stop_loss} >= entry {entry}"
            sl_distance = (entry - signal.stop_loss) / entry * 100
            # Farthest TP for a long = highest price (largest reward).
            farthest_tp = max(signal.take_profits[:3])  # TP1-3 preferred
            tp_label = f"TP{signal.take_profits.index(farthest_tp) + 1}"
            reward = farthest_tp - entry
            if reward <= 0:
                return False, f"{tp_label} {farthest_tp} <= entry {entry}"

        # Stop-loss distance check — auto-tighten if slightly over max.
        # Tuned signals keep their original SL — DeepSeek already vetted the
        # tuned entry's validity and $-risk-per-trade is held constant by
        # position sizing (larger entry→SL distance → smaller size).
        if sl_distance > self._max_sl_distance_pct and not getattr(signal, "entry_tuned", False):
            if is_short:
                new_sl = round(entry * (1 + self._max_sl_distance_pct / 100), 6)
            else:
                new_sl = round(entry * (1 - self._max_sl_distance_pct / 100), 6)
            logger.warning(
                f"SL auto-tightened for {signal.pair}: {signal.stop_loss} "
                f"({sl_distance:.1f}% from entry) → {new_sl} ({self._max_sl_distance_pct}%)"
            )
            signal.stop_loss = new_sl
        elif sl_distance > self._max_sl_distance_pct:
            logger.info(
                f"SL kept wide for tuned {('short' if is_short else 'long')} {signal.pair}: "
                f"{signal.stop_loss} ({sl_distance:.1f}% from tuned entry) — original SL preserved"
            )

        # Risk:reward ratio check against the (possibly tightened) final SL.
        risk = (signal.stop_loss - entry) if is_short else (entry - signal.stop_loss)
        rr = reward / risk
        if rr < self._min_rr_ratio:
            return False, f"R:R {rr:.2f} (vs {tp_label}) < min {self._min_rr_ratio}"

        # Entry zone width check
        if signal.entry_low is not None and signal.entry_high is not None and signal.entry_low > 0:
            zone_pct = (signal.entry_high - signal.entry_low) / signal.entry_low * 100
            if zone_pct > self._max_entry_zone_pct:
                return False, f"Entry zone {zone_pct:.1f}% too wide (max {self._max_entry_zone_pct}%)"

        # AI quality score check
        if signal.quality_score < self._min_quality_score:
            return False, f"Quality score {signal.quality_score}/10 < min {self._min_quality_score} ({signal.quality_reason})"

        return True, ""
