# src/rl/live_router.py
"""Per-bar PPO routing service: compute the action each 1h bar and push it to
the Rust engine's RoutingCache via ``POST /api/v1/routing``. Paper-gated only.

This module has two layers:

* **Pure decoder** (``decode_action``) — Task 7. Maps a PPO action int to the
  routing payload. Pure-Python (only imports ``src.rl.action_map``) so importing
  this module does NOT pull gymnasium / sb3 / pandas_ta / torch.

* **Observation builder + POST loop** (``build_observation`` / ``run_loop``) —
  Task 8. ``build_observation`` is also numpy-only and importable from a thin
  test environment; the heavy deps (sb3's ``PPORouter``, ``requests``,
  ``compute_features``, ``EnvConfig``) are imported *inside* ``run_loop`` so
  module load stays lean.

============================================================
KNOWN SIM-TO-REAL OBSERVATION GAP (Phase 1 — read before relying on this)
============================================================
The live router is stateless per-bar. It does NOT track the engine's current
one-hot state, drawdown-from-peak, position ratio, or bars-in-engine — those
live inside the Rust engine and are not exposed via any Phase-1 endpoint.

``build_observation`` therefore reconstructs the parts of ``TradingEnv._build_obs``
it *can* compute (the 17 market+time features, ``unrealised_pct`` from real
equity fetched via ``GET /api/v1/capital``) and zeros the rest:

    * ``engine one-hot``        → ``[1, 0, 0, 0]`` (FLAT). The policy was
      trained only on one-hot vectors with exactly one 1; an all-zeros vector
      would be out-of-distribution. FLAT is the in-distribution safe default
      AND matches the env's reset state. When the Rust engine is actually
      active (grid/trend/swing), this field is WRONG — documented.
    * ``dd`` (drawdown)         → ``0.0``. No peak-equity tracking in the
      router; the engine should expose this for a faithful obs.
    * ``pos_ratio``             → ``0.0``. Engine position notional is not
      observable from the router.
    * ``bars_norm``             → ``0.0``. Bars-in-engine counter lives in
      the engine.

The fidelity check (Task 10) is the safety net that determines whether this
gap breaks the policy. If it does, the fix is to expose engine state via a new
Rust endpoint (out of Phase-1 scope) — NOT to silently change the layout here.

The observation layout is byte-identical in *semantics* to
``TradingEnv._build_obs`` (src/rl/env.py):

    [0:17]   market+time features (FEATURE_COLS, in order).
    [17:21]  engine one-hot — flat / grid / trend / swing.
    [21]     unrealized PnL %  = (equity - initial) / initial.
    [22]     drawdown-from-peak = (peak - equity) / peak.
    [23]     position notional ratio = |position_value| / initial_equity.
    [24]     active bar count normalized = bars_in_engine / max_bars_per_engine.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from src.rl.action_map import ACTION_TO_ENGINE_SIZE

# Engine names in canonical one-hot order — must match ``src.rl.env.ENGINES``.
# Hardcoded here (rather than imported) so this module does not pull gymnasium
# at import time. ``src.rl.env`` is the source of truth; the test
# ``test_build_observation_matches_env_at_reset`` guards against drift.
_ENGINES: tuple[str, ...] = ("flat", "grid", "trend", "swing")


def decode_action(action: int) -> dict:
    """Map a PPO action int (0-9) to the routing payload.

    Mirrors ``src.rl.env.ACTION_TO_ENGINE_SIZE`` exactly — the policy is
    trained against that mapping, so the decoder must agree or actions get
    silently misinterpreted. The mapping lives in ``src.rl.action_map`` (a
    pure module with no numpy / gymnasium / sb3 / torch deps) so importing
    this module — and running its unit tests — works with only numpy present.
    """
    engine, size_mult = ACTION_TO_ENGINE_SIZE[int(action)]
    return {
        "active_engine": engine,
        "size_mult": float(size_mult),
        "flat": engine == "flat",
    }


def build_observation(
    feature_row: Any,
    account: dict,
    cfg: Any | None = None,
) -> np.ndarray:
    """Build the 25-dim PPO observation for a live bar.

    Replicates ``TradingEnv._build_obs`` column-for-column so the policy sees
    the same distribution it was trained on (modulo the state-fields gap
    documented at the top of this module).

    Args:
        feature_row: one row (pandas Series or 1-d array) of ``compute_features``
            output — the 17 market+time features in ``FEATURE_COLS`` order.
        account: dict with at least ``equity`` and ``initial_equity`` (floats).
            ``equity`` should be the REAL live equity (pulled via
            ``GET /api/v1/capital`` in ``run_loop``); ``initial_equity`` is the
            episode-reference capital the policy was trained against (defaults
            from ``EnvConfig.initial_capital`` = 10,000).
        cfg: optional ``EnvConfig``. Kept for forward-compat (e.g. when the
            engine starts exposing ``max_bars_per_engine``); currently unused
            because the state fields are zeroed per the documented gap.

    Returns:
        ``np.ndarray`` of shape ``(25,)``, dtype ``float64``, NaN/inf-cleaned.
    """
    feats = np.asarray(feature_row, dtype=np.float64).ravel()

    # Engine one-hot = FLAT (in-distribution safe default; see module docstring).
    one_hot = np.zeros(len(_ENGINES), dtype=np.float64)
    one_hot[0] = 1.0  # _ENGINES[0] == "flat"

    equity = float(account.get("equity", 0.0))
    initial_equity = float(account.get("initial_equity", 0.0))

    unrealised_pct = (equity - initial_equity) / max(initial_equity, 1e-8)

    # State fields the live router cannot observe — see module docstring.
    dd = 0.0
    pos_ratio = 0.0
    bars_norm = 0.0

    obs = np.concatenate(
        [
            feats,
            one_hot,
            np.array(
                [unrealised_pct, dd, pos_ratio, bars_norm],
                dtype=np.float64,
            ),
        ]
    )
    # Match env._build_obs's final defensive cleanup.
    return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)


def run_loop(
    pair: str,
    model_path: str,
    rust_url: str,
    bar_seconds: int = 3600,
) -> None:
    """Each closed 1h bar: compute features, predict, POST the routing decision.

    Blocking forever loop. Intended to be the entrypoint of a dedicated
    routing sidecar container (paper-gated): it predicts an action from the
    trained PPO policy and pushes the decoded routing payload to the Rust
    engine at ``POST /api/v1/routing``. The engine then pauses/activates
    strategies and scales sizes accordingly.

    Heavy imports (sb3 via ``PPORouter``, ``requests``, ``compute_features``,
    ``EnvConfig``) are deferred to here so importing this module stays light.

    Args:
        pair: trading pair, e.g. ``"BTCUSDT"`` — passed to ``load_klines``.
        model_path: path to the trained PPO ``.zip`` artifact.
        rust_url: base URL of the Rust engine (e.g. ``"http://localhost:8080"``).
        bar_seconds: sleep between bars (default 3600 = 1h).
    """
    import time
    from datetime import date, timedelta

    import requests

    from src.rl.data import load_klines
    from src.rl.features import FEATURE_COLS, compute_features
    from src.rl.router import PPORouter

    # Default config; we only use ``initial_capital`` as the unrealised_pct
    # denominator (matches the training-time reference capital).
    try:
        from src.rl.env import EnvConfig

        initial_capital = float(EnvConfig().initial_capital)
    except Exception:  # pragma: no cover — env.py import should not fail in
        # any environment where sb3 is installed (env.py imports gymnasium),
        # but we degrade gracefully rather than crash the loop on import.
        initial_capital = 10_000.0

    router = PPORouter(model_path)

    while True:
        end = date.today()
        start = end - timedelta(days=2)  # enough to warm up indicators
        df = load_klines(pair, start, end)
        feats = compute_features(df)[FEATURE_COLS]
        row = feats.iloc[-1]

        equity = _get_equity(rust_url)
        obs = build_observation(
            row,
            {"equity": equity, "initial_equity": initial_capital},
        )

        action = router.predict(obs)
        payload = decode_action(int(action))

        try:
            requests.post(
                f"{rust_url}/api/v1/routing",
                json=payload,
                timeout=5,
            )
        except requests.RequestException:
            # Network blip — log nothing here (no logger wired in Phase 1) and
            # retry on the next bar. The end-to-end paper run is the validator.
            pass

        time.sleep(bar_seconds)


def _get_equity(rust_url: str) -> float:
    """Fetch the real account equity from ``GET /api/v1/capital``.

    Returns the ``total_equity`` field (USDT + Σ(base × mid), portfolio MTM).
    Falls back to 10,000 if the field is missing — but does NOT swallow HTTP
    errors: a dead engine should propagate so the caller can see it.
    """
    import requests

    r = requests.get(f"{rust_url}/api/v1/capital", timeout=5)
    r.raise_for_status()
    return float(r.json().get("total_equity", 10_000.0))
