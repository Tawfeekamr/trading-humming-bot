from __future__ import annotations

from src.ml.drift import compare_distribution, evaluate_drift


def test_distribution_drift_and_danger_spike():
    result = compare_distribution({0: 0.6, 1: 0.3, 2: 0.1}, {0: 0.2, 1: 0.3, 2: 0.5})
    assert result["max_abs_delta"] == 0.4
    assert result["deltas"] == {0: -0.4, 1: 0.0, 2: 0.4}
    reasons = evaluate_drift(
        {0: 0.6, 1: 0.3, 2: 0.1},
        {0: 0.2, 1: 0.3, 2: 0.5},
        confidence_24h=0.7,
        age_ms=1_000,
        ttl_ms=180_000,
    )
    assert reasons == ["class_distribution_shift", "danger_frequency_spike"]


def test_drift_reports_low_confidence_stale_and_feature_contract_mismatch():
    reasons = evaluate_drift(
        {0: 0.8, 1: 0.2, 2: 0.0},
        {0: 0.8, 1: 0.2, 2: 0.0},
        confidence_24h=0.4,
        age_ms=181_000,
        ttl_ms=180_000,
        feature_contract_match=False,
    )
    assert reasons == ["low_confidence", "stale_cache", "feature_contract_mismatch"]


def test_pusher_monitor_keeps_bounded_window_and_reports_without_disabling():
    from src.data.feature_contract import MARKET_FEATURE_COLS
    from src.ml.model_metadata import canonical_feature_contract_hash
    from src.ml.regime_pusher import RegimeDriftMonitor

    monitor = RegimeDriftMonitor(window_ms=100, max_events=3)
    metadata = {
        "class_distribution": {"0": 0.8, "1": 0.2, "2": 0.0},
        "feature_contract_hash": canonical_feature_contract_hash(MARKET_FEATURE_COLS),
    }
    monitor.observe("ETH-USDT", 2, 0.9, 1_000, metadata)
    monitor.observe("ETH-USDT", 2, 0.9, 1_050)
    monitor.observe("ETH-USDT", 2, 0.9, 1_101)
    report = monitor.collect_drift_report(now_ms=1_101)
    assert report["ETH-USDT"]["live_distribution"][2] == 1.0
    assert "class_distribution_shift" in report["ETH-USDT"]["reasons"]
    assert "danger_frequency_spike" in report["ETH-USDT"]["reasons"]
    assert len(monitor._windows["ETH-USDT"]) == 2
    assert "feature_contract_mismatch" not in report["ETH-USDT"]["reasons"]


def test_pusher_monitor_reports_feature_contract_mismatch():
    from src.ml.regime_pusher import RegimeDriftMonitor

    monitor = RegimeDriftMonitor()
    monitor.observe(
        "BNB-USDT",
        0,
        0.8,
        1_000,
        {
            "class_distribution": {"0": 1.0, "1": 0.0, "2": 0.0},
            "feature_contract_hash": "wrong-hash",
        },
    )
    report = monitor.collect_drift_report(now_ms=1_000)
    assert report["BNB-USDT"]["reasons"] == ["feature_contract_mismatch"]


def test_drift_report_includes_loaded_model_with_no_observations():
    from src.data.feature_contract import MARKET_FEATURE_COLS
    from src.ml.model_metadata import canonical_feature_contract_hash
    from src.ml.regime_pusher import RegimeDriftMonitor

    monitor = RegimeDriftMonitor()
    monitor._metadata["XRP-USDT"] = {
        "class_distribution": {"0": 0.7, "1": 0.3, "2": 0.0},
        "feature_contract_hash": canonical_feature_contract_hash(MARKET_FEATURE_COLS),
    }
    report = monitor.collect_drift_report(now_ms=1_000)
    assert report["XRP-USDT"]["live_distribution"] == {0: 0.0, 1: 0.0, 2: 0.0}
    assert "low_confidence" in report["XRP-USDT"]["reasons"]
    assert "stale_cache" in report["XRP-USDT"]["reasons"]


def test_pusher_reuses_validated_artifact_metadata_after_file_replacement(tmp_path):
    from types import SimpleNamespace
    from src.ml.regime_pusher import model_metadata

    clf = SimpleNamespace(
        _validated_artifact_metadata={
            "model_version": "rf-v1",
            "artifact_sha256": "validated-sha",
            "feature_contract_hash": "features-v1",
        }
    )
    path = tmp_path / "replaced.pkl"
    path.write_bytes(b"new-bytes")
    assert model_metadata(clf, str(path))["artifact_sha256"] == "validated-sha"
