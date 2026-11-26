"""The single-fee ablation switch changes the reward's fee term and nothing else.

``EnvConfig.reward_fee_term`` defaults to True, which is the frozen evaluation:
equity is net of fees AND the reward subtracts ``fee_rate * turnover`` again.
Setting it to False drops only that explicit term. These tests pin both facts
so the ablation can never silently change the frozen behaviour.
"""
from __future__ import annotations

import pytest

pytest.importorskip("gymnasium")

import numpy as np

from src.rl.env import EnvConfig, TradingEnv
from tests.test_rl_env import _synthetic_df

ACTIONS = [4, 4, 9, 1, 1, 1, 7, 9, 5, 5, 0, 0, 9, 3, 3, 3]  # mix of engines + FLAT


def _rollout(cfg: EnvConfig):
    df = _synthetic_df(n_bars=300, seed=7)
    env = TradingEnv(df, cfg)
    env.reset(seed=42)
    out = []
    for a in ACTIONS * 8:
        _, r, term, trunc, info = env.step(a)
        out.append((r, info["equity"], info["turnover"]))
        if term or trunc:
            break
    return out


def test_default_config_keeps_double_counted_fee():
    assert EnvConfig().reward_fee_term is True


def test_ablation_changes_only_the_fee_term():
    base_cfg = EnvConfig(window_length=200, warmup_bars=50)
    abl_cfg = EnvConfig(window_length=200, warmup_bars=50, reward_fee_term=False)
    base, abl = _rollout(base_cfg), _rollout(abl_cfg)
    assert len(base) == len(abl) > 0

    prev_equity = base_cfg.initial_capital
    traded = 0
    for (r0, eq0, to0), (r1, eq1, to1) in zip(base, abl):
        # Same actions -> identical trading, equity and turnover.
        assert eq0 == pytest.approx(eq1)
        assert to0 == pytest.approx(to1)
        # The reward differs by exactly the explicit fee term.
        fee_term = base_cfg.fee_rate * to0 / prev_equity
        assert r1 - r0 == pytest.approx(fee_term, abs=1e-12)
        traded += to0 > 0
        prev_equity = eq0
    assert traded > 0, "rollout must include trades for the test to mean anything"


def test_no_trades_means_no_difference():
    cfg_a = EnvConfig(window_length=100, warmup_bars=50)
    cfg_b = EnvConfig(window_length=100, warmup_bars=50, reward_fee_term=False)
    for cfg_x, cfg_y in [(cfg_a, cfg_b)]:
        ea, eb = TradingEnv(_synthetic_df(seed=3), cfg_x), TradingEnv(_synthetic_df(seed=3), cfg_y)
        ea.reset(seed=1)
        eb.reset(seed=1)
        for _ in range(40):
            ra = ea.step(9)[1]
            rb = eb.step(9)[1]
            assert ra == rb
    assert np.isfinite(ra)
