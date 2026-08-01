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


def test_drawdown_parity_can_qualify_ppo():
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
    assert result["eligible"] is True
    assert "human_review_required" in result["reasons"]
