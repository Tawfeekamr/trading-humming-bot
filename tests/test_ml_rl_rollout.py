"""Deterministic end-to-end verification tests for the paper-only ML/RL rollout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.verify_ml_rl_rollout import verify_ml_rl_rollout


def _report(**overrides):
    payload = {
        "metadata": {
            "feature_contract_hash": "features-v1",
            "routing_mode": "shadow",
            "model_paths": [],
            "verification_now_ms": 1_700_000_000_000,
        },
        "metrics": {"trade_count": 120},
        "promotion": {"eligible": True, "reasons": ["human_review_required"]},
        "ppo_active": False,
        "attribution_missing_count": 0,
        "metrics_by_regime": {"ranging": {"trade_count": 120}},
    }
    payload.update(overrides)
    return payload


def _shadow(path: Path, *, timestamp_ms: int = 1_700_000_000_000, age: int = 100):
    path.write_text(
        json.dumps(
            {
                "timestamp_ms": timestamp_ms,
                "pair": "ETH-USDT",
                "action": 0,
                "engine": "grid",
                "size_mult": 0.5,
                "model_version": "ppo-test",
                "model_sha256": "a" * 64,
                "observation_age_ms": age,
                "mode": "shadow",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_report(path: Path, payload: dict):
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_rollout_verifier_rejects_active_ppo(tmp_path):
    result = verify_ml_rl_rollout(
        str(tmp_path), str(tmp_path / "report.json"), str(tmp_path / "shadow.jsonl")
    )
    assert result["ok"] is False
    assert "ppo_active" in result["failures"]


def test_rollout_verifier_accepts_valid_shadow_evidence(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report())
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is True
    assert result["ppo_active"] is False
    assert result["attribution_coverage"]["ratio"] == 1.0


def test_rollout_verifier_rejects_stale_shadow_record(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report())
    _shadow(shadow_path, timestamp_ms=1_700_000_000_000, age=180_001)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "shadow_records_stale" in result["failures"]


def test_rollout_verifier_rejects_manifest_checksum_and_feature_hash(tmp_path):
    model = tmp_path / "model.pkl"
    model.write_bytes(b"model")
    manifest = {
        "pair": "ETH-USDT",
        "timeframe": "1h",
        "train_start": "2026-01-01",
        "train_end": "2026-02-01",
        "feature_contract_hash": "wrong",
        "label_params": {},
        "class_distribution": {"0": 1.0},
        "metrics": {},
        "source_commit": "test",
        "artifact_sha256": "0" * 64,
    }
    (tmp_path / "model.pkl.metadata.json").write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report(metadata={"feature_contract_hash": "features-v1", "model_paths": ["model.pkl"]}))
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "metadata_checksum_mismatch" in result["failures"]
    assert "feature_hash_mismatch" in result["failures"]


def test_rollout_verifier_rejects_active_cache_mode_and_reports_coverage(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "strategy.yaml").write_text("routing:\n  mode: live\n", encoding="utf-8")
    _write_report(report_path, _report(ppo_active=True, attribution_missing_count=1, metrics={"trade_count": 2}))
    _shadow(shadow_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "routing_cache.json").write_text("{}", encoding="utf-8")
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "ppo_active" in result["failures"]
    assert "shadow_mode" in result["failures"]
    assert result["attribution_coverage"]["missing"] == 1
    assert result["attribution_coverage"]["ratio"] == 0.5


def test_rollout_verifier_reads_promotion_gate_from_metrics(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report(promotion=None, metrics={"trade_count": 120, "promotion": {"eligible": True, "reasons": ["human_review_required"]}}))
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert "report_gate" not in result["failures"]


def test_rollout_verifier_rejects_mixed_inconclusive_and_blocking_gate(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report(promotion={"eligible": False, "reasons": ["inconclusive_sample", "drawdown_increase"]}))
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert "report_gate_rejected" in result["failures"]
    assert "report_gate_inconclusive" not in result["warnings"]


def test_rollout_verifier_requires_fresh_shadow_evidence(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report())
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "shadow_records_missing" in result["failures"]


def test_rollout_verifier_rejects_untracked_active_cache(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report())
    _shadow(shadow_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "routing_cache.json").write_text("{}", encoding="utf-8")
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "active_routing_cache_changed" in result["failures"]


def test_rollout_verifier_checks_evaluator_model_checksum_claims(tmp_path):
    model = tmp_path / "rf.pkl"
    model.write_bytes(b"rf-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    manifest = {
        "pair": "ETH-USDT", "timeframe": "1h", "train_start": "2026-01-01", "train_end": "2026-02-01",
        "feature_contract_hash": "features-v1", "label_params": {}, "class_distribution": {"0": 1.0},
        "metrics": {}, "source_commit": "test", "artifact_sha256": digest,
    }
    (tmp_path / "rf.pkl.metadata.json").write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report(metadata={"feature_contract_hash": "features-v1", "routing_mode": "shadow", "model_paths": ["rf.pkl"], "rf_model": "rf.pkl", "rf_model_sha256": "0" * 64, "verification_now_ms": 1_700_000_000_000}))
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "metadata_checksum_mismatch" in result["failures"]


def test_rollout_verifier_does_not_claim_full_attribution_without_trade_total(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(report_path, _report(metrics={}))
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["attribution_coverage"]["ratio"] is None
    assert "attribution_unknown" in result["warnings"]


def test_rollout_verifier_rejects_unbound_rf_checksum_claim(tmp_path):
    report_path = tmp_path / "report.json"
    shadow_path = tmp_path / "shadow.jsonl"
    _write_report(
        report_path,
        _report(
            metadata={
                "feature_contract_hash": "features-v1",
                "routing_mode": "shadow",
                "model_paths": [],
                "rf_model_sha256": "a" * 64,
                "verification_now_ms": 1_700_000_000_000,
            }
        ),
    )
    _shadow(shadow_path)
    result = verify_ml_rl_rollout(str(tmp_path), str(report_path), str(shadow_path))
    assert result["ok"] is False
    assert "metadata_checksum_mismatch" in result["failures"]