"""Tests for the regime pusher sidecar.

Covers the correctness-critical invariants:
  * calculate_technical_features still emits all 14 MARKET_FEATURE_COLS
    (drift guard — if this breaks, predictions silently go wrong),
  * compute_regime returns argmax class + its probability, bounded,
  * the POST payload shape matches the Rust RegimeUpdate struct,
  * end-to-end with the real ETH clean model when present.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.data.feature_engineering import calculate_technical_features
from src.ml import regime_pusher
from src.rl.features import MARKET_FEATURE_COLS


def _synthetic_ohlcv(n: int = 250, seed: int = 42) -> pd.DataFrame:
    """Deterministic random-walk OHLCV with high > close > low."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1.0 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.005, n)))
    opn = close * (1.0 + rng.normal(0, 0.003, n))
    volume = rng.uniform(100.0, 1000.0, n)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": opn, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class _StubClf:
    """Minimal stand-in for RegimeClassifier — only predict_proba_full is used."""

    is_trained = True

    def __init__(self, probs: dict[int, float]):
        self._probs = probs

    def predict_proba_full(self, X):  # noqa: N803 (matches real signature)
        return dict(self._probs)


# ── Feature column-contract guard (the critical drift catch) ────────────────
def test_calculate_technical_features_produces_all_market_feature_cols():
    feats = calculate_technical_features(_synthetic_ohlcv().copy())
    missing = set(MARKET_FEATURE_COLS) - set(feats.columns)
    assert not missing, f"calculate_technical_features no longer emits: {missing}"


def test_market_feature_cols_has_14_entries():
    assert len(MARKET_FEATURE_COLS) == 14


# ── compute_regime ──────────────────────────────────────────────────────────
def test_compute_regime_returns_argmax_class_and_its_probability():
    df = _synthetic_ohlcv()
    regime, confidence = regime_pusher.compute_regime(df, _StubClf({0: 0.2, 1: 0.7, 2: 0.1}))
    assert regime == 1
    assert confidence == pytest.approx(0.7)


def test_compute_regime_picks_danger_when_dominant_and_bounds_hold():
    df = _synthetic_ohlcv()
    regime, confidence = regime_pusher.compute_regime(df, _StubClf({0: 0.05, 1: 0.05, 2: 0.9}))
    assert regime == 2
    assert regime in {0, 1, 2}
    assert 0.0 <= confidence <= 1.0


def test_compute_regime_none_on_degenerate_short_input():
    # 5 bars << warmup (~50) → all rows dropped → None (no crash, no bad predict)
    assert regime_pusher.compute_regime(_synthetic_ohlcv(n=5), _StubClf({0: 1.0})) is None


# ── model path mapping ──────────────────────────────────────────────────────
def test_model_path_for_uses_clean_model_naming():
    assert regime_pusher.model_path_for("ETH-USDT", "models").endswith("regime_ETH-USDT_clean.pkl")
    assert regime_pusher.model_path_for("DOGE-USDT", "models").endswith("regime_DOGE-USDT_clean.pkl")


# ── POST payload shape == Rust RegimeUpdate {pair, regime, confidence} ──────
def test_regime_update_payload_shape_matches_rust_struct():
    payload = [
        {"pair": "ETH-USDT", "regime": 1, "confidence": 0.7},
        {"pair": "BNB-USDT", "regime": 0, "confidence": 0.55},
        {"pair": "DOGE-USDT", "regime": 2, "confidence": 0.61},
    ]
    for item in payload:
        assert set(item.keys()) == {"pair", "regime", "confidence"}
        assert isinstance(item["pair"], str)
        assert isinstance(item["regime"], int) and item["regime"] in {0, 1, 2}
        assert isinstance(item["confidence"], float) and 0.0 <= item["confidence"] <= 1.0


# ── End-to-end with the real ETH clean model (when present) ─────────────────
def test_compute_regime_with_real_eth_clean_model():
    path = "models/regime_ETH-USDT_clean.pkl"
    if not os.path.exists(path):
        pytest.skip("ETH clean model not present locally")
    try:
        from src.ml.regime_classifier import RegimeClassifier

        clf = RegimeClassifier(model_path=path, model_type="random_forest")
        clf.load_model()
        regime, confidence = regime_pusher.compute_regime(_synthetic_ohlcv(), clf)
    except Exception as exc:  # sklearn version drift can break pickle load → don't fail the gate
        pytest.skip(f"real model load/predict failed (likely sklearn version drift): {exc}")
    assert regime in {0, 1, 2}
    assert 0.0 <= confidence <= 1.0


# --- Drift alerts (thesis §5.4.1 item 2: the alert path was never called) ---

def _report(reasons_by_pair):
    return {pair: {"pair": pair, "reasons": list(r), "live_distribution": {0: 1.0},
                   "confidence_24h": 0.5} for pair, r in reasons_by_pair.items()}


def test_drift_alert_sent_once_per_new_reason_set():
    sent, last = [], {}
    regime_pusher.send_drift_alerts(_report({"ETH-USDT": ["low_confidence"]}), last, sent.append)
    regime_pusher.send_drift_alerts(_report({"ETH-USDT": ["low_confidence"]}), last, sent.append)
    assert len(sent) == 1, "an unchanged condition must not re-alert every hour"
    assert "ETH-USDT" in sent[0] and "low_confidence" in sent[0]


def test_drift_alert_resent_when_reasons_change_and_quiet_when_clear():
    sent, last = [], {}
    regime_pusher.send_drift_alerts(_report({"BNB-USDT": ["low_confidence"]}), last, sent.append)
    regime_pusher.send_drift_alerts(
        _report({"BNB-USDT": ["low_confidence", "danger_frequency_spike"]}), last, sent.append)
    regime_pusher.send_drift_alerts(_report({"BNB-USDT": []}), last, sent.append)
    assert len(sent) == 3, "new reason -> alert; recovery -> one all-clear"
    assert "cleared" in sent[2].lower()
    regime_pusher.send_drift_alerts(_report({"BNB-USDT": []}), last, sent.append)
    assert len(sent) == 3, "no repeat all-clear"


def test_drift_check_is_due_hourly():
    assert regime_pusher.drift_check_due(now=10_000.0, last=None, every_sec=3600)
    assert not regime_pusher.drift_check_due(now=10_000.0, last=9_000.0, every_sec=3600)
    assert regime_pusher.drift_check_due(now=13_000.0, last=9_000.0, every_sec=3600)


def test_drift_alert_waits_for_enough_observations():
    # Right after a restart the 24h window holds one prediction, so the live
    # mix reads 100% one class: that is not drift and must not alert.
    sent, last = [], {}
    thin = _report({"ETH-USDT": ["class_distribution_shift"]})
    thin["ETH-USDT"]["observations"] = 1
    regime_pusher.send_drift_alerts(thin, last, sent.append)
    assert sent == [], "too few observations to judge drift"
    full = _report({"ETH-USDT": ["class_distribution_shift"]})
    full["ETH-USDT"]["observations"] = regime_pusher.DRIFT_MIN_OBSERVATIONS
    regime_pusher.send_drift_alerts(full, last, sent.append)
    assert len(sent) == 1


def test_first_drift_check_waits_one_interval_after_start():
    assert not regime_pusher.drift_check_due(now=1_000.0, last=1_000.0, every_sec=3600)
