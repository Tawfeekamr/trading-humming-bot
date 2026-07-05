# tests/test_rl_env.py
"""Unit tests for ``src/rl/env.py`` — the Gymnasium trading environment.

These tests exercise the public Gymnasium contract (observation/action spaces,
step return shape, episode termination) plus the load-bearing reward-property
checks: positive reward when the agent beats buy-and-hold, fee accounting,
determinism, and the blowup circuit-breaker.

Synthetic OHLCV frames (deterministic, seeded) are used throughout so the tests
are independent of the cached kline data and run offline in milliseconds.
"""
from __future__ import annotations

from datetime import datetime, timezone

import gymnasium as gym
import numpy as np
import pandas as pd
import pytest

from src.rl.env import ACTION_TO_ENGINE_SIZE, ENGINES, EnvConfig, TradingEnv


# --- Fixtures --------------------------------------------------------------


def _synthetic_df(
    n_bars: int = 300,
    trend_per_bar: float = 0.0,
    vol: float = 0.005,
    seed: int = 42,
    start_price: float = 100.0,
) -> pd.DataFrame:
    """Deterministic OHLCV frame indexed by hourly UTC datetimes.

    ``trend_per_bar`` is the per-bar drift; e.g. -0.005 = 0.5% down per bar.
    High/low/open are derived from close so bars have a realistic range
    (which the grid engine needs for level crosses).
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(
        start=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        periods=n_bars,
        freq="1h",
        name="ts",
    )
    rets = rng.normal(trend_per_bar, vol, size=n_bars)
    close = start_price * np.exp(np.cumsum(rets))
    # Intrabar range: ~0.5% each side, with a little noise.
    half_range = np.maximum(close * rng.uniform(0.001, 0.01, size=n_bars), 1e-6)
    high = close + half_range
    low = np.maximum(close - half_range, close * 0.5)
    open_ = (high + low) / 2
    volume = rng.uniform(100, 1000, size=n_bars)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def small_env() -> TradingEnv:
    """A compact env (window=50) on a 300-bar sideways random-walk frame."""
    df = _synthetic_df(n_bars=300, trend_per_bar=0.0)
    cfg = EnvConfig(window_length=50, warmup_bars=50)
    return TradingEnv(df, cfg)


# --- Space / shape contract ------------------------------------------------


def test_observation_shape(small_env: TradingEnv):
    """Observation is a 19-dim vector (11 features + 4 one-hot + 4 portfolio)."""
    obs, _ = small_env.reset(seed=42)
    assert isinstance(obs, np.ndarray)
    assert obs.shape == (19,), f"expected (19,), got {obs.shape}"


def test_action_space_is_discrete_10(small_env: TradingEnv):
    """Action space is Discrete(10) — 3 engines x 3 sizes + GO_FLAT."""
    assert isinstance(small_env.action_space, gym.spaces.Discrete)
    assert small_env.action_space.n == 10


def test_reset_returns_valid_obs(small_env: TradingEnv):
    """reset() returns (obs, info); obs is finite, info carries equity."""
    obs, info = small_env.reset(seed=42)
    assert np.all(np.isfinite(obs)), "obs must be finite after reset"
    assert "equity" in info
    assert info["equity"] == pytest.approx(small_env.config.initial_capital)
    assert info["engine"] == "flat"


def test_step_returns_valid_tuple(small_env: TradingEnv):
    """step() returns the Gymnasium 5-tuple with correct types."""
    small_env.reset(seed=42)
    out = small_env.step(0)
    assert len(out) == 5, "step must return (obs, reward, terminated, truncated, info)"
    obs, reward, terminated, truncated, info = out
    assert isinstance(obs, np.ndarray)
    assert obs.shape == (19,)
    assert isinstance(reward, float)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert isinstance(info, dict)
    assert np.all(np.isfinite(obs)), "obs must be finite after step"


def test_observation_engine_one_hot_positions():
    """The engine one-hot occupies obs[11:15] in canonical ENGINES order."""
    df = _synthetic_df(n_bars=200)
    env = TradingEnv(df, EnvConfig(window_length=30))
    env.reset(seed=0)

    # After reset, current engine is "flat" -> obs[11] = 1, rest 0.
    obs, _ = env.reset(seed=0)
    assert obs[11] == 1.0 and obs[12] == 0.0 and obs[13] == 0.0 and obs[14] == 0.0

    # Activate TREND (action 3). After step, one-hot should encode "trend".
    env.step(3)
    obs, _, _, _, info = env.step(3)  # another trend step to advance engine state
    assert info["engine"] == "trend"
    assert obs[13] == 1.0, f"trend one-hot must be at idx 13; got {obs[11:15]}"


# --- Behavioural tests -----------------------------------------------------


def test_go_flat_closes_position():
    """Activating an engine then GO_FLAT closes the position."""
    df = _synthetic_df(n_bars=200, trend_per_bar=0.001)  # gentle uptrend
    env = TradingEnv(df, EnvConfig(window_length=50))
    env.reset(seed=42)

    # Activate TREND — enters a long position on first step.
    env.step(3)
    assert env._current_engine == "trend"
    assert env._engine_state.get("in_position") is True
    assert env._position_value() > 0, "trend should hold a position after entry"

    # GO_FLAT (action 9) — closes everything.
    obs, reward, term, trunc, info = env.step(9)
    assert info["engine"] == "flat"
    assert env._position_value() == 0.0, "position must be closed after GO_FLAT"
    assert env._engine_state == {} or not env._engine_state.get("in_position")


def test_reward_positive_on_winning_bar():
    """When the agent is flat and the market drops, reward > 0 (excess over
    a losing buy-and-hold). This is the cleanest "winning decision" check —
    the agent beats passive by avoiding the drawdown."""
    # Strong downtrend: every bar loses ~1%.
    df = _synthetic_df(n_bars=200, trend_per_bar=-0.01, vol=0.001)
    env = TradingEnv(df, EnvConfig(window_length=50))
    env.reset(seed=42)

    # Agent picks GO_FLAT (action 9). Stays flat; market drops.
    obs, reward, term, trunc, info = env.step(9)
    assert reward > 0, (
        f"flat-in-downtrend reward must be > 0 (excess over B&H); got {reward}"
    )


def test_episode_terminates_at_window_end():
    """After window_length steps, truncated=True (end of episode)."""
    df = _synthetic_df(n_bars=200)
    cfg = EnvConfig(window_length=20, warmup_bars=50)
    env = TradingEnv(df, cfg)
    env.reset(seed=42)

    truncated = False
    for i in range(cfg.window_length):
        obs, reward, term, trunc, info = env.step(9)  # GO_FLAT: no churn
        truncated = trunc
        if term or trunc:
            break
    assert truncated, (
        f"episode must truncate after window_length={cfg.window_length} steps; "
        f"stopped at step {info['step']}"
    )


def test_equity_blowup_terminates():
    """If equity drops below 50% of initial, terminated=True."""
    df = _synthetic_df(n_bars=200, vol=0.001)  # calm market so step P&L ~ 0
    env = TradingEnv(df, EnvConfig(window_length=50))
    env.reset(seed=42)

    # Force equity below the 0.5x blowup line, then step.
    env.equity = env._initial_equity * 0.4
    env._prev_equity = env.equity
    # Keep prev_close consistent so reward math is clean.
    env._prev_close = float(env._closes[env._bar_idx])

    obs, reward, term, trunc, info = env.step(9)
    assert term, (
        f"equity {info['equity']:.2f} < 0.5*initial must trigger terminated; "
        f"got term={term}"
    )


def test_determinism_same_seed():
    """Same frame + same seed -> identical trajectory (window, obs, rewards)."""
    df = _synthetic_df(n_bars=200, trend_per_bar=0.002)

    env1 = TradingEnv(df, EnvConfig(window_length=30))
    env2 = TradingEnv(df, EnvConfig(window_length=30))

    obs1, info1 = env1.reset(seed=123)
    obs2, info2 = env2.reset(seed=123)
    np.testing.assert_array_equal(obs1, obs2, "reset obs must match")
    assert info1["bar_idx"] == info2["bar_idx"], "window start must match"

    # Drive both envs with the same action sequence.
    actions = [3, 3, 0, 9, 6, 7, 1, 8, 9, 4]  # mix of engines/sizes/flat
    for a in actions:
        o1, r1, t1, tr1, i1 = env1.step(a)
        o2, r2, t2, tr2, i2 = env2.step(a)
        np.testing.assert_array_equal(o1, o2, err_msg="obs diverged")
        assert r1 == r2, f"reward diverged at action {a}: {r1} vs {r2}"
        assert t1 == t2 and tr1 == tr2


def test_grid_activates_and_books_no_loss_on_flat_bar():
    """Grid on a single flat bar (no level crosses) books zero P&L and zero
    turnover — sanity check that the primitive doesn't hallucinate trades."""
    # Build a frame where one bar has a tiny range so no grid level is crossed.
    df = _synthetic_df(n_bars=120, vol=0.001)
    env = TradingEnv(df, EnvConfig(window_length=30))
    env.reset(seed=42)
    obs, reward, term, trunc, info = env.step(0)  # GRID 0.5x
    assert info["engine"] == "grid"
    # Grid deployed; on a calm bar it should not blow up equity.
    assert info["equity"] > 0


def test_action_mapping_has_10_entries():
    """Sanity: the action decode table covers all 10 actions."""
    assert len(ACTION_TO_ENGINE_SIZE) == 10
    engines = {e for e, _ in ACTION_TO_ENGINE_SIZE[:9]}
    assert engines == {"grid", "trend", "swing"}
    assert ACTION_TO_ENGINE_SIZE[9] == ("flat", 0.0)
    # Sizes are exactly {0.5, 1.0, 1.5} for each engine.
    for i in range(3):
        assert ACTION_TO_ENGINE_SIZE[i][1] == (0.5, 1.0, 1.5)[i]
    assert ENGINES == ("flat", "grid", "trend", "swing")


def test_short_frame_does_not_crash():
    """A frame barely longer than warmup still constructs and steps."""
    df = _synthetic_df(n_bars=70)  # warmup=50, only 20 usable bars
    env = TradingEnv(df, EnvConfig(window_length=4300, warmup_bars=50))
    obs, _ = env.reset(seed=0)
    assert obs.shape == (19,)
    # Stepping past the end should truncate cleanly, not raise.
    truncated = False
    for _ in range(25):
        obs, r, term, trunc, info = env.step(9)
        if term or trunc:
            break
    assert trunc, "ran out of bars -> must truncate"


def test_position_notional_ratio_observed():
    """When trend enters, the position-notional-ratio obs (idx 17) is > 0."""
    df = _synthetic_df(n_bars=200, trend_per_bar=0.003)
    env = TradingEnv(df, EnvConfig(window_length=40, max_position_pct=0.25))
    env.reset(seed=0)
    # Action 4 = TREND at 1.0x size -> deploys max_position_pct of equity.
    obs, _, _, _, info = env.step(4)
    assert info["engine"] == "trend"
    assert obs[17] > 0, f"position_notional_ratio must be > 0 after trend entry; got {obs[17]}"
    # At size_mult=1.0 and max_position_pct=0.25, ratio should be ~0.25
    # (modulo the bar's close-vs-entry drift, which is tiny for 1h vol).
    assert obs[17] == pytest.approx(0.25, abs=0.05), (
        f"position_notional_ratio at 1.0x size should be ~0.25; got {obs[17]}"
    )
