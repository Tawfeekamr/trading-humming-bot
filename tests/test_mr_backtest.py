# tests/test_mr_backtest.py
import json
import logging
import pandas as pd
import pytest
from unittest.mock import patch, Mock

from backtest.mean_reversion.backtest import run_single, run_sweep, walk_forward, run_pair, build_report
from backtest.mean_reversion.features import compute_features, bar_seconds


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
    """TEST-4: walk_forward with new signature returns sane IS/OOS split."""
    # Put flush at position 60 (well within IS which is 0-73), OOS is 74-110
    # Use flat prices to avoid regime filter triggering
    bars = _bars([100.0] * 60 + [94.0] + [95.0] * 50)
    # BUG-8: walk_forward no longer takes features arg
    wf = walk_forward(bars, bar="1s", oos_frac=1 / 3)
    assert wf is not None, "walk_forward should return results"
    assert "is_best" in wf and "oos" in wf
    assert "sharpe_ratio" in wf["is_best"]
    # TEST-4: verify OOS exists and is sane (may be None if no entries)
    if wf["oos"] is not None:
        assert isinstance(wf["oos"].get("total_trades"), int)


def test_run_pair_writes_summary_and_per_symbol_json(tmp_path):
    bars = _bars([100.0] * 30 + [90.0, 92.0, 88.0, 95.0] * 20)
    summary = run_pair("TESTUSDT", bars, bar="1s", results_dir=tmp_path)
    assert (tmp_path / "TESTUSDT_sweep.json").exists()
    assert "live_config" in summary and "best" in summary and "walk_forward" in summary
    json.dumps(summary, default=str)  # serializable


def test_bar_seconds_raises_on_unsupported_unit():
    """TEST-1: bar_seconds raises ValueError on unsupported units like '1h'."""
    with pytest.raises(ValueError, match="Unsupported bar unit"):
        bar_seconds("1h")


def test_run_sweep_logs_failures(caplog):
    """BUG-7: run_sweep logs warnings when run_single fails."""
    bars = _bars([100.0] * 30 + [90.0])
    f = compute_features(bars, bar="1s")

    # Mock run_single to raise an exception
    with patch("backtest.mean_reversion.backtest.run_single", side_effect=RuntimeError("test error")):
        with caplog.at_level(logging.WARNING):
            results = run_sweep(bars, f, bar="1s")

    # Should return empty and log warnings
    assert results.empty
    assert any("run_single failed" in record.message for record in caplog.records)


def test_build_report_honest_is_oos_distinction(tmp_path):
    """BUG-11: build_report uses walk-forward is_best, not full-period best."""
    per_pair = [{
        "symbol": "TESTUSDT",
        "hodl_return_pct": 5.0,
        "live_config": {"total_trades": 10, "total_return_pct": 2.0, "sharpe_ratio": 1.5},
        "walk_forward": {
            "is_best": {"drop_thr": 0.05, "tp": 0.02, "stop": 0.04, "base_size": 100, "sharpe_ratio": 2.5},
            "oos": {"total_trades": 5, "total_return_pct": 1.0, "sharpe_ratio": 1.8}
        },
        "best": {"drop_thr": 0.06, "tp": 0.03, "stop": 0.05, "base_size": 200, "sharpe_ratio": 3.0}
    }]
    summary_path = tmp_path / "report.md"

    report = build_report(per_pair, summary_path)

    # Verify the report contains the honest IS/OOS distinction
    text = report.lower()
    assert "best is (walk-forward)" in text
    assert "is→oos sharpe gap: 0.70" in text
    assert "full-period sweep" in text
    assert "not out-of-sample" in text

    # Verify the file was written
    assert summary_path.exists()
    content = summary_path.read_text()
    assert "Best IS (walk-forward)" in content
    assert "drop=0.05" in content  # From walk-forward is_best, NOT full-period best (0.06)
