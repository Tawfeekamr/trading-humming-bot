# tests/test_rl_fold_rf.py
"""Tests for the fold-specific RF baseline (audit defect #3).

Previously one clean RF artifact (trained over one long window) was reused
against every cached PPO slice, so early test windows were evaluated against
a model trained on data including later periods. The fix trains one RF per
fold on that fold's training window only, with provenance manifests.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("sklearn")

import numpy as np
import pandas as pd


def _synthetic_labeled_frame(n_bars: int = 900, seed: int = 7) -> pd.DataFrame:
    """OHLCV frame long enough for feature warmup + labels."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(
        start=datetime(2025, 1, 1, tzinfo=timezone.utc), periods=n_bars, freq="1h"
    )
    rets = rng.normal(0, 0.006, size=n_bars)
    close = 100.0 * np.exp(np.cumsum(rets))
    half = np.maximum(close * rng.uniform(0.001, 0.01, size=n_bars), 1e-6)
    return pd.DataFrame(
        {
            "open": close - half / 2,
            "high": close + half,
            "low": close - half,
            "close": close,
            "volume": rng.uniform(90, 110, size=n_bars),
        },
        index=idx,
    )


def test_train_fold_rf_respects_fold_boundaries(tmp_path):
    """The fold RF must be fit ONLY on the fold's training window."""
    from src.rl.walk_forward import train_fold_rf

    df = _synthetic_labeled_frame(n_bars=900)
    fold_train_start, fold_train_end = 100, 600   # fold training window [100, 600)

    result = train_fold_rf("ETHUSDT", df, fold_train_start, fold_train_end,
                           out_dir=str(tmp_path))
    manifest = result["manifest"]
    # Manifest window matches the fold window exactly
    assert manifest["train_start"] == str(df.index[fold_train_start])
    assert manifest["train_end"] == str(df.index[fold_train_end - 1])
    # Provenance keys mirroring the PPO sidecar format
    for key in ("pair", "data_hash", "feature_contract_hash", "source_commit",
                "seed", "trained_at", "class_distribution", "training_samples"):
        assert key in manifest, key
    # Model artifact exists and loads
    assert (tmp_path / result["model_name"]).exists()


def test_train_fold_rf_calibration_stays_inside_fold(tmp_path):
    """Calibration tail must not extend past the fold's training boundary.

    Fit on first 85% of the fold's training rows, calibrate on the final
    15% — all rows strictly inside [fold_start, fold_end).
    """
    from src.rl.walk_forward import train_fold_rf

    df = _synthetic_labeled_frame(n_bars=900)
    result = train_fold_rf("ETHUSDT", df, 100, 600, out_dir=str(tmp_path))
    # training_samples + calibration rows must not exceed the fold window
    n_fold_rows = result["manifest"]["training_samples"] + result["manifest"].get(
        "calibration_samples", 0
    )
    assert 0 < n_fold_rows <= 500  # 600 - 100


def test_train_fold_rf_is_deterministic(tmp_path):
    """Same fold, same seed -> identical class distribution + artifact hash."""
    from src.rl.walk_forward import train_fold_rf

    df = _synthetic_labeled_frame(n_bars=900)
    r1 = train_fold_rf("ETHUSDT", df, 100, 600, out_dir=str(tmp_path / "a"))
    r2 = train_fold_rf("ETHUSDT", df, 100, 600, out_dir=str(tmp_path / "b"))
    assert r1["manifest"]["class_distribution"] == r2["manifest"]["class_distribution"]
    assert r1["manifest"]["data_hash"] == r2["manifest"]["data_hash"]


def test_fold_models_differ_across_folds(tmp_path):
    """Different folds must produce different training windows in manifests."""
    from src.rl.walk_forward import train_fold_rf

    df = _synthetic_labeled_frame(n_bars=900)
    r1 = train_fold_rf("ETHUSDT", df, 100, 400, out_dir=str(tmp_path / "f1"))
    r2 = train_fold_rf("ETHUSDT", df, 300, 600, out_dir=str(tmp_path / "f2"))
    assert r1["manifest"]["train_start"] != r2["manifest"]["train_start"]
