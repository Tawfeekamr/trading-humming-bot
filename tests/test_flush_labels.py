# tests/test_flush_labels.py
import pandas as pd
from backtest.mean_reversion.labels import label_flushes
from backtest.mean_reversion.features import compute_features
from backtest.mean_reversion.backtest import run_single


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_label_matches_run_single_tp_outcome():
    # 30 flat @100, flush to 94 (entry), then +2% TP at 96.
    bars = _bars([100.0] * 30 + [94.0, 94.0, 96.0])
    feats = compute_features(bars, bar="1s")
    labels = label_flushes(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, max_hold=10)
    assert len(labels) == 1
    assert labels.iloc[0]["label"] == 1  # +2% (95.88) hit before -4% (90.24) -> winner
    r = run_single(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None and r["total_return_pct"] > 0


def test_label_matches_run_single_stop_outcome():
    # 30 flat @100, flush to 94 (entry), then -4% stop at 90.
    bars = _bars([100.0] * 30 + [94.0, 90.0])
    feats = compute_features(bars, bar="1s")
    labels = label_flushes(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, max_hold=10)
    assert len(labels) == 1
    assert labels.iloc[0]["label"] == 0  # stop hit first -> loser
    r = run_single(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None and r["total_return_pct"] < 0
