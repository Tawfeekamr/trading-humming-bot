from __future__ import annotations

import hashlib
import json

import pytest

from src.ml.model_metadata import read_metadata, write_metadata


def _metadata():
    return {
        "pair": "ETH-USDT",
        "timeframe": "1h",
        "train_start": "2026-01-01",
        "train_end": "2026-04-01",
        "feature_contract_hash": "feature-hash",
        "label_params": {"danger_threshold": 0.03},
        "class_distribution": {"0": 0.6, "1": 0.3, "2": 0.1},
        "metrics": {"accuracy": 0.8},
        "source_commit": "abc123",
    }


def test_write_and_read_metadata_is_adjacent_and_checksummed(tmp_path):
    artifact = tmp_path / "model.pkl"
    artifact.write_bytes(b"artifact")

    digest = write_metadata(str(artifact), _metadata())

    assert digest == hashlib.sha256(b"artifact").hexdigest()
    manifest = tmp_path / "model.pkl.metadata.json"
    assert manifest.exists()
    loaded = read_metadata(str(artifact))
    assert loaded["artifact_sha256"] == digest
    assert loaded["pair"] == "ETH-USDT"


def test_metadata_is_immutable_and_artifact_tampering_fails(tmp_path):
    artifact = tmp_path / "model.pkl"
    artifact.write_bytes(b"artifact")
    write_metadata(str(artifact), _metadata())

    with pytest.raises((FileExistsError, ValueError)):
        write_metadata(str(artifact), {**_metadata(), "pair": "BNB-USDT"})

    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        read_metadata(str(artifact))


def test_missing_required_manifest_key_is_rejected(tmp_path):
    artifact = tmp_path / "model.pkl"
    artifact.write_bytes(b"artifact")
    manifest = tmp_path / "model.pkl.metadata.json"
    manifest.write_text(json.dumps({"pair": "ETH-USDT"}))

    with pytest.raises(ValueError, match="required"):
        read_metadata(str(artifact))


def test_classifier_save_writes_manifest_and_load_validates_it(tmp_path):
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from src.ml.regime_classifier import RegimeClassifier

    artifact = tmp_path / "model.pkl"
    clf = RegimeClassifier(model_path=str(artifact))
    clf.model = RandomForestClassifier(n_estimators=2, random_state=1)
    X = np.asarray([[0.0], [1.0], [2.0], [3.0]])
    clf.train(X, np.asarray([0, 0, 1, 1]))
    clf.feature_columns = ["returns"]
    clf.pair = "ETH-USDT"
    clf.timeframe = "1h"
    clf.train_start = "2026-01-01"
    clf.train_end = "2026-04-01"
    clf.label_params = {"window_bars": 24}
    clf.metrics = {"accuracy": 0.75}
    clf.save_model()
    loaded = RegimeClassifier(model_path=str(artifact))
    loaded.load_model()
    assert loaded.is_trained is True
    assert loaded.predict_class(np.asarray([[0.1]])) in {0, 1}
    assert loaded.metadata["pair"] == "ETH-USDT"
    assert loaded.metadata["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_same_path_retrain_does_not_replace_immutable_artifact(tmp_path):
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from src.ml.regime_classifier import RegimeClassifier

    artifact = tmp_path / "model.pkl"
    clf = RegimeClassifier(model_path=str(artifact))
    clf.model = RandomForestClassifier(n_estimators=2, random_state=1)
    clf.train(np.asarray([[0.0], [1.0], [2.0], [3.0]]), np.asarray([0, 0, 1, 1]))
    clf.feature_columns = ["returns"]
    clf.pair, clf.timeframe = "ETH-USDT", "1h"
    clf.train_start, clf.train_end = "2026-01-01", "2026-04-01"
    clf.label_params, clf.metrics = {"window_bars": 24}, {"accuracy": 0.75}
    clf.save_model()
    original = artifact.read_bytes()

    clf.model = RandomForestClassifier(n_estimators=2, random_state=9)
    clf.train(np.asarray([[0.0], [1.0], [2.0], [3.0]]), np.asarray([1, 1, 0, 0]))
    with pytest.raises(FileExistsError):
        clf.save_model()
    assert artifact.read_bytes() == original


def test_load_models_rejects_tampered_artifact_and_keeps_fallback(tmp_path):
    import pickle
    from src.ml.regime_pusher import load_models

    artifact = tmp_path / "regime_ETH-USDT_clean.pkl"
    artifact.write_bytes(pickle.dumps({"model": object(), "version": 1}))
    write_metadata(str(artifact), _metadata())
    artifact.write_bytes(artifact.read_bytes() + b"tamper")

    assert load_models(["ETH-USDT"], str(tmp_path)) == {}
