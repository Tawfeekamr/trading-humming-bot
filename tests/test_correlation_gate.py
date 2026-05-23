# tests/test_correlation_gate.py
"""
Tests for cross-asset ML correlation gate.
When BTC signals DANGER (regime 2), all altcoin buy-side operations halt.
"""
import pytest


class TestCorrelationGate:
    """Test BTC DANGER → altcoin buy halt logic."""

    def _make_gate(self, btc_regime=None, btc_confidence=0.0, btc_model_loaded=True):
        return {
            "ml_predictions": {
                "BTC-USDT": (btc_regime, btc_confidence, 0.0),
                "ETH-USDT": (0, 0.8, 0.0),
                "BNB-USDT": (0, 0.7, 0.0),
            },
            "ml_models": {"BTC-USDT": True} if btc_model_loaded else {},
        }

    def _btc_danger_active(self, state, pair):
        if pair == "BTC-USDT":
            return False
        btc_pred = state["ml_predictions"].get("BTC-USDT")
        if btc_pred is None or "BTC-USDT" not in state["ml_models"]:
            return True
        btc_regime = btc_pred[0]
        if btc_regime is None:
            return True
        return btc_regime == 2

    def test_altcoin_buy_blocked_when_btc_danger(self):
        state = self._make_gate(btc_regime=2, btc_confidence=0.9)
        assert self._btc_danger_active(state, "ETH-USDT") is True
        assert self._btc_danger_active(state, "BNB-USDT") is True

    def test_altcoin_buy_allowed_when_btc_ranging(self):
        state = self._make_gate(btc_regime=0, btc_confidence=0.8)
        assert self._btc_danger_active(state, "ETH-USDT") is False

    def test_altcoin_buy_allowed_when_btc_trending(self):
        state = self._make_gate(btc_regime=1, btc_confidence=0.7)
        assert self._btc_danger_active(state, "ETH-USDT") is False

    def test_btc_pair_never_blocked(self):
        state = self._make_gate(btc_regime=2, btc_confidence=0.9)
        assert self._btc_danger_active(state, "BTC-USDT") is False

    def test_safe_default_when_no_btc_prediction(self):
        state = self._make_gate(btc_regime=None, btc_confidence=0.0)
        assert self._btc_danger_active(state, "ETH-USDT") is True

    def test_safe_default_when_btc_model_missing(self):
        state = self._make_gate(btc_model_loaded=False)
        assert self._btc_danger_active(state, "ETH-USDT") is True

    def test_gate_transition_tracked(self):
        transitions = []
        state1 = self._make_gate(btc_regime=0)
        state2 = self._make_gate(btc_regime=2)
        state3 = self._make_gate(btc_regime=0)

        was_active = self._btc_danger_active(state1, "ETH-USDT")
        now_active = self._btc_danger_active(state2, "ETH-USDT")
        if was_active != now_active:
            transitions.append(("activated", "ETH-USDT"))

        was_active = now_active
        now_active = self._btc_danger_active(state3, "ETH-USDT")
        if was_active != now_active:
            transitions.append(("deactivated", "ETH-USDT"))

        assert len(transitions) == 2
        assert transitions[0][0] == "activated"
        assert transitions[1][0] == "deactivated"
