# tests/test_rl_live_router.py
"""Unit tests for ``src/rl/live_router.py``.

Two layers of tests live here:

* **Task 7 — pure action decoder (``decode_action``).** These run with numpy +
  pytest only (no gymnasium / sb3 / pandas_ta) because the decoder's
  ``ACTION_TO_ENGINE_SIZE`` import is intentionally pure-Python so importing
  ``src.rl.live_router`` does not pull the heavy training stack at module load.

* **Task 8 — observation builder (``build_observation``).** These exercise the
  column-for-column parity between the live obs vector and
  ``TradingEnv._build_obs``. They require ``pandas_ta`` (for ``compute_features``)
  and ``gymnasium`` (for the env-parity check), so they self-skip when those
  packages are absent — run them with the conda base interpreter
  (``/opt/anaconda3/bin/python``), not the homebrew ``python3`` used for the
  Task 7 layer.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.rl.live_router import (
    _fetch_live_klines,
    _symbol_for_engine,
    build_observation,
    decode_action,
)


# --- Live data source (engine /api/v1/klines) — numpy/pytest only ----------


def test_symbol_for_engine_normalizes_all_forms():
    assert _symbol_for_engine("ETHUSDT") == "ETH-USDT"
    assert _symbol_for_engine("ETH-USDT") == "ETH-USDT"
    assert _symbol_for_engine("ETH/USDT") == "ETH-USDT"
    assert _symbol_for_engine("BTCUSDT") == "BTC-USDT"


def test_fetch_live_klines_normalizes_symbol_and_shape(monkeypatch):
    import requests as _requests

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                     "volume": 10.0, "timestamp": 1780000000000}]

    def _get(url, params=None, **kw):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(_requests, "get", _get)
    df = _fetch_live_klines("http://eng", "ETHUSDT")
    assert captured["params"]["symbol"] == "ETH-USDT"  # normalized to engine key
    assert captured["params"]["interval"] == "1h"
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_fetch_live_klines_raises_on_empty(monkeypatch):
    import requests as _requests

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    monkeypatch.setattr(_requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError):
        _fetch_live_klines("http://eng", "ETHUSDT")


# --- Task 7: pure action decoder (numpy-only) ------------------------------


def test_decode_action_grid_1x():
    assert decode_action(1) == {"active_engine": "grid", "size_mult": 1.0, "flat": False}


def test_decode_action_flat():
    assert decode_action(9) == {"active_engine": "flat", "size_mult": 0.0, "flat": True}


def test_decode_action_swing_1_5x():
    assert decode_action(8) == {"active_engine": "swing", "size_mult": 1.5, "flat": False}


# --- Task 8: observation builder (needs pandas_ta + gymnasium) -------------


def _synthetic_ohlcv(n_bars: int = 300, seed: int = 42) -> pd.DataFrame:
    """Deterministic OHLCV frame indexed by hourly UTC datetimes.

    Mirrors the fixture in ``tests/test_rl_env.py`` so the env-parity test
    compares like-for-like.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(
        start=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        periods=n_bars,
        freq="1h",
        name="ts",
    )
    rets = rng.normal(0.0, 0.005, size=n_bars)
    close = 100.0 * np.exp(np.cumsum(rets))
    half_range = np.maximum(close * rng.uniform(0.001, 0.01, size=n_bars), 1e-6)
    high = close + half_range
    low = np.maximum(close - half_range, close * 0.5)
    open_ = (high + low) / 2
    volume = rng.uniform(100, 1000, size=n_bars)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_build_observation_shape_matches_env():
    """Obs shape == env's real obs dimension (17 features + 4 one-hot + 4 state = 25).

    The env.py docstring still says 19 (stale — assumes 11 features), but
    ``FEATURE_COLS`` actually carries 17 columns (14 market + 3 time), so the
    real obs_dim computed in ``TradingEnv.__init__`` is 17 + 4 + 4 = 25. We
    assert the real number here.
    """
    pytest.importorskip("pandas_ta")
    from src.rl.features import FEATURE_COLS, compute_features

    df = _synthetic_ohlcv(n_bars=300)
    feats = compute_features(df)[FEATURE_COLS]
    row = feats.iloc[-1]

    account = {"equity": 10000.0, "initial_equity": 10000.0}
    obs = build_observation(row, account)

    assert isinstance(obs, np.ndarray)
    assert obs.shape == (25,), f"expected (25,), got {obs.shape}"
    # Sanity: NaN/inf cleaned (matches env's _build_obs final nan_to_num).
    assert np.isfinite(obs).all()


def test_build_observation_feature_slice_matches_input():
    """The first 17 entries of the obs must equal the feature row byte-for-byte."""
    pytest.importorskip("pandas_ta")
    from src.rl.features import FEATURE_COLS, compute_features

    df = _synthetic_ohlcv(n_bars=300)
    row = compute_features(df)[FEATURE_COLS].iloc[-1]

    obs = build_observation(row, {"equity": 10000.0, "initial_equity": 10000.0})
    np.testing.assert_allclose(obs[:17], row.to_numpy(dtype=np.float64), atol=1e-12)


def test_build_observation_flat_account_state_zeroed():
    """For a flat/zero account: one-hot = flat=[1,0,0,0], state fields = 0.

    The live router can't observe engine one-hot / drawdown / pos-ratio /
    bars-in-engine (those live in the Rust engine), so it reconstructs the
    ``_build_obs`` output for a flat account. one-hot MUST be flat (not
    zeros) so the policy sees an in-distribution observation.
    """
    pytest.importorskip("pandas_ta")
    from src.rl.features import FEATURE_COLS, compute_features

    df = _synthetic_ohlcv(n_bars=300)
    row = compute_features(df)[FEATURE_COLS].iloc[-1]

    obs = build_observation(row, {"equity": 10000.0, "initial_equity": 10000.0})
    np.testing.assert_array_equal(obs[17:21], np.array([1.0, 0.0, 0.0, 0.0]))
    np.testing.assert_allclose(obs[21:], np.zeros(4), atol=1e-12)


def test_build_observation_unrealised_pct_uses_real_equity():
    """unrealised_pct = (equity - initial) / initial — uses live equity, not hardcoded."""
    pytest.importorskip("pandas_ta")
    from src.rl.features import FEATURE_COLS, compute_features

    df = _synthetic_ohlcv(n_bars=300)
    row = compute_features(df)[FEATURE_COLS].iloc[-1]

    # Equity up 10% -> unrealised_pct ≈ 0.10.
    obs = build_observation(
        row, {"equity": 11000.0, "initial_equity": 10000.0}
    )
    assert obs[21] == pytest.approx(0.10, abs=1e-12)


def test_build_observation_matches_env_at_reset():
    """At reset the env is in flat/zero state — live obs must equal env obs.

    This is the load-bearing correctness check: it proves that, for a flat
    account, the live router reconstructs the EXACT observation the policy was
    trained against. (Once the engine activates, the live router's one-hot /
    pos_ratio / bars_norm become approximations — documented in
    ``live_router.build_observation``.)
    """
    pytest.importorskip("pandas_ta")
    pytest.importorskip("gymnasium")
    from src.rl.env import EnvConfig, TradingEnv
    from src.rl.features import FEATURE_COLS, compute_features

    df = _synthetic_ohlcv(n_bars=300)
    cfg = EnvConfig(window_length=50, warmup_bars=50)
    env = TradingEnv(df, cfg)
    env.reset(seed=42)  # sets current_engine="flat", equity=initial, bars_in_engine=0

    # Use the same bar the env was reset to so the feature row matches.
    row = compute_features(df)[FEATURE_COLS].iloc[env._bar_idx]
    account = {"equity": cfg.initial_capital, "initial_equity": cfg.initial_capital}

    live_obs = build_observation(row, account)
    env_obs = env._build_obs()

    np.testing.assert_allclose(live_obs, env_obs, atol=1e-10)


# --- I1: _get_equity must raise on schema drift (no silent 10k fallback) ---


def test_get_equity_raises_keyerror_when_total_equity_missing(monkeypatch):
    """A missing ``total_equity`` key must propagate ``KeyError``, not silently
    fall back to 10,000 — a fabricated equity would corrupt the falsification
    experiment's reward signal and hide a dead/wrong endpoint.
    """
    import requests

    from src.rl import live_router

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            # Schema drift: endpoint responded 200 but dropped total_equity.
            return {"usdt_balance": 5000.0}

    def _fake_get(*args, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(requests, "get", _fake_get)

    with pytest.raises(KeyError):
        live_router._get_equity("http://localhost:9999")


def test_get_equity_propagates_http_error(monkeypatch):
    """HTTP errors must still propagate via raise_for_status (unchanged)."""
    import requests

    from src.rl import live_router

    class _FakeResponse:
        def raise_for_status(self):
            raise requests.HTTPError("503 service unavailable")

        def json(self):
            return {"total_equity": 9000.0}

    def _fake_get(*args, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(requests, "get", _fake_get)

    with pytest.raises(requests.HTTPError):
        live_router._get_equity("http://localhost:9999")

