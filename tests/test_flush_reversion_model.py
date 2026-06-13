# tests/test_flush_reversion_model.py
from src.ml.flush_reversion_model import FlushReversionClassifier, build_dataset, walk_forward_evaluate
from backtest.mean_reversion.features import compute_features
from backtest.mean_reversion.labels import label_flushes
import pandas as pd


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_reproducible_training_and_oos():
    prices = ([100.0] * 35 + [94.0, 96.0]) * 2 + ([100.0] * 35 + [94.0, 90.0]) * 2
    bars = _bars(prices)
    feats = compute_features(bars, bar="1s")
    labels = label_flushes(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, max_hold=10)
    ds = build_dataset(bars, feats, labels)
    a = FlushReversionClassifier(random_state=42); b = FlushReversionClassifier(random_state=42)
    a.fit(ds, ds["label"]); b.fit(ds, ds["label"])
    assert list(a.predict_proba(ds)) == list(b.predict_proba(ds))  # reproducible
    metrics = walk_forward_evaluate(ds, test_frac=0.5)
    assert "oos_accuracy" in metrics and "oos_precision" in metrics and "n_test" in metrics
