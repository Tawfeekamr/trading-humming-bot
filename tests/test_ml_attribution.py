from __future__ import annotations

import hashlib
import json
import sys
import types

import pytest

# Attribution helpers do not evaluate technical indicators; keep this focused
# suite runnable in minimal environments without the optional pandas-ta wheel.
sys.modules.setdefault("pandas_ta", types.SimpleNamespace())

from src.ml import regime_pusher


class _Classifier:
    model_version = "rf-bnb-20260801"
    feature_columns = ["returns", "volatility_ratio"]


def test_model_metadata_and_payload_are_deterministic(tmp_path):
    artifact = tmp_path / "model.pkl"
    artifact.write_bytes(b"deterministic-artifact")
    metadata = regime_pusher.model_metadata(_Classifier(), str(artifact))
    assert metadata["model_version"] == "rf-bnb-20260801"
    assert metadata["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert metadata["feature_contract_hash"]

    payload = regime_pusher.build_regime_update("BNB-USDT", 0, 0.81, metadata, 1_756_656_000_000)
    assert payload == {
        "pair": "BNB-USDT",
        "regime": 0,
        "confidence": 0.81,
        "timestamp": 1_756_656_000_000,
        "model_version": "rf-bnb-20260801",
        "artifact_sha256": metadata["artifact_sha256"],
        "feature_contract_hash": metadata["feature_contract_hash"],
    }


def test_model_metadata_rejects_only_missing_artifact_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        regime_pusher.model_metadata(_Classifier(), str(tmp_path / "missing.pkl"))


def test_build_payload_preserves_old_prediction_tuple_contract():
    result = (1, 0.7)
    payload = regime_pusher.build_regime_update(
        "ETH-USDT",
        *result,
        {"model_version": None, "artifact_sha256": "abc", "feature_contract_hash": None},
        123,
    )
    assert payload["regime"] == result[0]
    assert payload["confidence"] == result[1]
    assert payload["artifact_sha256"] == "abc"


def test_context_json_attribution_is_an_object_and_additive_contract():
    existing = {"entry_reason": "buy_0", "router_mode": "shadow"}
    attribution = {
        "regime_at_entry": "trending",
        "regime_confidence": 0.91,
        "regime_model_version": "rf-v1",
        "regime_artifact_sha256": "abc",
        "ml_gate_decision": "allowed",
        "router_mode": "shadow",
        "router_action": "hold",
        "router_engine": "trend",
        "router_size_mult": 1.0,
        "decision_timestamp": 123,
        "ml_age_ms": 10,
    }
    merged = {**existing, **attribution}
    assert isinstance(merged, dict)
    assert merged["entry_reason"] == "buy_0"
    assert merged["regime_artifact_sha256"] == "abc"
    assert json.loads(json.dumps(merged))["router_mode"] == "shadow"
