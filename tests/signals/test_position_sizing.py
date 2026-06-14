"""Risk-based position sizing for the signal engine.

The size is risk_amount / sl_distance (so a stop-out loses exactly
per_trade_risk_pct of capital), capped per-trade at max_position_pct of the
signal budget. Previously the cap was total_budget/max_positions — a flat
1/N slice that throttled every position regardless of how favorable the
risk:reward was.
"""
import pytest

from src.signals.signal_risk import SignalRiskGuard
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence


def _config(max_position_pct=25, **kw):
    cfg = {
        "max_capital_usdt": 10000,
        "capital_pct": 100.0,
        "max_positions": 5,
        "per_trade_risk_pct": 3.0,
        "max_position_pct": max_position_pct,
    }
    cfg.update(kw)
    return cfg


def _signal(confidence, entry, sl):
    return ParsedSignal(
        action=SignalAction.OPEN_LONG,
        pair="X-USDT",
        entry_low=entry,
        entry_high=entry,
        stop_loss=sl,
        confidence=confidence,
    )


def test_favorable_signal_deployed_up_to_max_position_pct_cap():
    """HIGH conf, 10% stop: risk_amount=$300, risk-based=$3000, capped at 25%=$2500."""
    rg = SignalRiskGuard(_config(max_position_pct=25))
    sig = _signal(SignalConfidence.HIGH, 100.0, 90.0)  # 10% stop
    assert rg.get_budget_for_trade(sig, 10000.0) == pytest.approx(2500.0)


def test_confidence_multiplier_throttles_below_cap():
    """MEDIUM conf (mult 0.66): risk_amount=$198, risk-based=$1980 < $2500 cap."""
    rg = SignalRiskGuard(_config(max_position_pct=25))
    sig = _signal(SignalConfidence.MEDIUM, 100.0, 90.0)
    assert rg.get_budget_for_trade(sig, 10000.0) == pytest.approx(1980.0, rel=1e-3)


def test_wide_stop_deploys_less():
    """HIGH conf, 25% stop: risk-based=$1200 < cap -> deploy $1200 (less capital at same $ risk)."""
    rg = SignalRiskGuard(_config(max_position_pct=25))
    sig = _signal(SignalConfidence.HIGH, 100.0, 75.0)  # 25% stop
    assert rg.get_budget_for_trade(sig, 10000.0) == pytest.approx(1200.0)


def test_cap_is_configurable():
    """Raising max_position_pct lets a favorable signal deploy more."""
    rg = SignalRiskGuard(_config(max_position_pct=40))  # cap = $4000
    sig = _signal(SignalConfidence.HIGH, 100.0, 90.0)  # risk-based $3000 < $4000
    assert rg.get_budget_for_trade(sig, 10000.0) == pytest.approx(3000.0)
