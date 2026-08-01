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
    assert result["net_pnl"] == 0.07
    assert result["total_return"] == 0.07
    assert result["max_drawdown"] == pytest.approx(0.05 / 1.10)
    assert result["profit_factor"] == 2.6
    assert result["confidence_intervals"]["total_return"]["level"] == 0.95
    assert result["confidence_intervals"]["total_return"]["low"] <= result["total_return"] <= result["confidence_intervals"]["total_return"]["high"]


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
