from __future__ import annotations
import pytest

import json


def test_summarize_returns_has_deterministic_metrics_and_confidence_intervals():
    from src.ml.evaluation_report import summarize_returns

    result = summarize_returns(
        [0.10, -0.05, 0.02, 0.01],
        [1.0, 0.0, 0.5, 1.0],
        fees=0.01,
    )
    assert result["trade_count"] == 4
    # Canonical (batch 7) compounded total return: 1.10*0.95*1.02*1.01 - 1.
    # The old arithmetic value 0.07 is superseded.
    assert result["net_pnl"] == pytest.approx(1.10 * 0.95 * 1.02 * 1.01 - 1)
    assert result["total_return"] == pytest.approx(1.10 * 0.95 * 1.02 * 1.01 - 1)
    # Canonical multiplicative MaxDD: equity 1.10 -> 1.10*0.95 = 1.045;
    # peak 1.10, trough 1.045 -> dd = 0.055/1.10... NO: cumprod gives
    # 1.045 at the -5% bar; dd = (1.10-1.045)/1.10 = 0.05/1.10.
    # Multiplicative: peak 1.10, equity after -5% bar = 1.10*0.95=1.045,
    # dd = 0.055/1.10 = 0.05. Exactly 5%: the bar return IS the dd.
    assert result["max_drawdown"] == pytest.approx(0.05)
    assert result["profit_factor"] == 2.6
    assert result["confidence_intervals"]["total_return"]["level"] == 0.95
    assert result["confidence_intervals"]["total_return"]["low"] <= result["total_return"] <= result["confidence_intervals"]["total_return"]["high"]


def test_summarize_uses_independent_trade_count_override():
    from src.ml.evaluation_report import summarize_returns

    result = summarize_returns([0.1, -0.1, 0.2], [1.0, 1.0, 1.0], fees=0.0, trade_count=2)
    assert result["trade_count"] == 2


def test_zero_loss_profit_factor_is_valid_json(tmp_path):
    from src.ml.evaluation_report import summarize_returns, write_report

    path = tmp_path / "zero-loss.json"
    metrics = summarize_returns([0.1, 0.2], [1.0, 1.0], fees=0.0)
    write_report(str(path), {}, metrics, [])
    parsed = json.loads(path.read_text())
    assert parsed["metrics"]["profit_factor"] is None


def test_write_report_is_sorted_and_reproducible(tmp_path):
    from src.ml.evaluation_report import write_report

    path = tmp_path / "report.json"
    write_report(str(path), {"z": 1, "a": 2}, {"b": 1, "a": 2}, [{"slice": 1}])
    first = path.read_bytes()
    write_report(str(path), {"z": 1, "a": 2}, {"b": 1, "a": 2}, [{"slice": 1}])
    assert path.read_bytes() == first
    parsed = json.loads(first)
    assert list(parsed) == ["metadata", "metrics", "slices"]
    assert list(parsed["metadata"]) == ["a", "z"]
