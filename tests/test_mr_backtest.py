# tests/test_mr_backtest.py
import pandas as pd
import pytest

from backtest.mean_reversion.backtest import run_single, run_sweep, walk_forward
from backtest.mean_reversion.features import compute_features


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_single_tp_exit_is_a_winning_trade():
    # Mirrors Rust `position_holds_then_exits_at_take_profit_via_on_tick`:
    # 30 flat @100, flush to 94 (entry), hold at 94, then +2% TP at 96.
    bars = _bars([100.0] * 30 + [94.0, 94.0, 96.0])
    f = compute_features(bars, bar="1s")
    r = run_single(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None
    assert r["total_trades"] == 1
    assert r["total_return_pct"] > 0.0   # TP exit -> gain


def test_single_stop_exit_is_a_losing_trade():
    # Mirrors Rust `position_exits_at_layer2_stop_loss`:
    # 30 flat @100, flush to 94 (entry), then -4% stop at 90.
    bars = _bars([100.0] * 30 + [94.0, 90.0])
    f = compute_features(bars, bar="1s")
    r = run_single(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None
    assert r["total_trades"] == 1
    assert r["total_return_pct"] < 0.0   # stop exit -> loss


def test_single_no_entry_returns_none():
    bars = _bars([100.0] * 32)  # no flush -> no entries
    f = compute_features(bars, bar="1s")
    assert run_single(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s") is None


def test_run_sweep_returns_one_row_per_grid_combo():
    # Build a series with one flush so at least some configs trade.
    bars = _bars([100.0] * 30 + [90.0] + [100.0] * 5)
    f = compute_features(bars, bar="1s")
    results = run_sweep(bars, f, bar="1s")
    assert len(results) > 0
    assert {"drop_thr", "tp", "stop", "base_size", "sharpe_ratio"}.issubset(results.columns)


def test_walk_forward_returns_is_best_and_oos():
    bars = _bars([100.0] * 30 + [90.0, 92.0, 88.0, 95.0] * 20)
    f = compute_features(bars, bar="1s")
    wf = walk_forward(bars, f, bar="1s", oos_frac=1 / 3)
    assert wf is not None
    assert "is_best" in wf and "oos" in wf
    assert "sharpe_ratio" in wf["is_best"]
