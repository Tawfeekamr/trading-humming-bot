from __future__ import annotations


def test_small_sample_is_inconclusive():
    from src.ml.promotion_gate import evaluate

    result = evaluate(
        {
            "trade_count": 12,
            "profit_factor": 2.0,
            "ppo_max_drawdown": 0.05,
            "rf_max_drawdown": 0.10,
        }
    )
    assert result["eligible"] is False
    assert "inconclusive_sample" in result["reasons"]


def test_drawdown_parity_without_regime_counts_is_inconclusive():
    from src.ml.promotion_gate import evaluate

    result = evaluate(
        {
            "trade_count": 140,
            "ppo_profit_factor": 1.2,
            "rf_profit_factor": 1.2,
            "ppo_max_drawdown": 0.05,
            "rf_max_drawdown": 0.10,
            "ppo_exposure": 0.52,
            "rf_exposure": 0.74,
            "window_count": 3,
        }
    )
    assert result["eligible"] is False
    assert "inconclusive_sample" in result["reasons"]


def test_gate_rejects_missing_or_underfloor_strategy_and_regime_counts():
    from src.ml.promotion_gate import evaluate

    base = {
        "ppo_profit_factor": 1.3,
        "rf_profit_factor": 1.2,
        "ta_profit_factor": 1.1,
        "ppo_max_drawdown": 0.05,
        "rf_max_drawdown": 0.10,
        "ppo_exposure": 0.52,
        "rf_exposure": 0.74,
        "window_count": 3,
        "trade_counts_by_strategy": {"ppo": 140, "rf": 140, "ta": 140},
        "trade_counts_by_regime": {"ppo": {"trend": 140}, "rf": {"trend": 140}, "ta": {"trend": 140}},
    }
    assert evaluate(base)["eligible"] is True

    missing_rf = {**base, "trade_counts_by_strategy": {"ppo": 140, "ta": 140}}
    assert evaluate(missing_rf)["eligible"] is False
    assert "inconclusive_sample" in evaluate(missing_rf)["reasons"]

    underfloor = {
        **base,
        "trade_counts_by_regime": {
            "ppo": {"trend": 140, "range": 99},
            "rf": {"trend": 140},
            "ta": {"trend": 140},
        },
    }
    assert evaluate(underfloor)["eligible"] is False
    assert "inconclusive_sample" in evaluate(underfloor)["reasons"]


def test_flat_regime_counts_are_validated():
    from src.ml.promotion_gate import evaluate

    metrics = {
        "trade_count": 140,
        "ppo_profit_factor": 1.2,
        "rf_profit_factor": 1.2,
        "ppo_max_drawdown": 0.05,
        "rf_max_drawdown": 0.10,
        "ppo_exposure": 0.52,
        "rf_exposure": 0.74,
        "window_count": 3,
        "trade_counts_by_regime": {"trend": 50},
    }
    result = evaluate(metrics)
    assert result["eligible"] is False
    assert "inconclusive_sample" in result["reasons"]
