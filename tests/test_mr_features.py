# tests/test_mr_features.py
import pandas as pd
import pytest

from backtest.mean_reversion.features import compute_features, ENTER_THRESHOLD
from backtest.mean_reversion.strategy import entry_signal


def _bars(prices, buy=1.0, sell=1.0):
    return pd.DataFrame({"close": prices, "buy_vol": buy, "sell_vol": sell})


def test_drop_frac_measures_decline_over_window():
    bars = _bars([100.0] * 30 + [94.0])  # 6% drop over 30 bars
    f = compute_features(bars, bar="1s")  # window = 30 bars
    assert pd.isna(f["drop_frac"].iloc[0])           # warmup
    assert f["drop_frac"].iloc[-1] == pytest.approx(0.06)


def test_score_clears_threshold_on_uniform_volume_flush():
    bars = _bars([100.0] * 30 + [94.0])
    f = compute_features(bars, bar="1s")
    assert f["score"].iloc[-1] >= ENTER_THRESHOLD
    assert f["size_mult"].iloc[-1] > 0.0


def test_flush_outscores_flat():
    # The drop_frac term is what differentiates a flush from a flat market.
    flat = compute_features(_bars([100.0] * 31), bar="1s")
    flush = compute_features(_bars([100.0] * 30 + [94.0]), bar="1s")
    assert flush["score"].iloc[-1] > flat["score"].iloc[-1]
    assert flush["drop_frac"].iloc[-1] > flat["drop_frac"].iloc[-1]


def test_entry_signal_fires_on_flush_and_not_before():
    # Mirrors the Rust flush-entry scenario: 30 flat bars, then a 6% flush.
    bars = _bars([100.0] * 30 + [94.0])
    f = compute_features(bars, bar="1s")
    sig = entry_signal(f, drop_thr=0.05)
    assert sig.sum() == 1
    assert sig.iloc[-1] == True
    assert sig.iloc[:-1].sum() == 0


def test_entry_signal_respects_drop_threshold():
    # A 4% flush must NOT trigger at drop_thr=0.05, but does at 0.03.
    bars = _bars([100.0] * 30 + [96.0])
    f = compute_features(bars, bar="1s")
    assert entry_signal(f, drop_thr=0.05).sum() == 0
    assert entry_signal(f, drop_thr=0.03).sum() == 1
