# src/rl/env.py
"""Gymnasium trading environment for the RL PPO execution-routing pipeline.

The environment replays 1h OHLCV bars and lets a PPO agent choose, each bar,
which of three simplified execution engines (grid / trend / swing) to run, at
what size multiplier (0.5x / 1.0x / 1.5x), or to go flat. The reward is
**excess return over buy-and-hold** minus fees and a drawdown-step penalty —
so the agent is explicitly rewarded only for beating passive, net of costs.

The engine primitives are deliberately simplified bar-level numpy functions
(~30-50 lines each), not the full Rust engines. They are deterministic and
directionally correct (grid profits in ranges, trend profits in trends, swing
profits on reversals) but do not match the production engines tick-for-tick.
Speed matters: PPO calls ``step()`` millions of times during training.

Observation (25-dim, ``Box(-inf, inf)``):
    [0:17]   market+time features from ``compute_features`` (14 market + 3 time).
    [17:21]  engine one-hot — flat / grid / trend / swing.
    [21]     unrealized PnL %  = (equity - initial) / initial.
    [22]     drawdown-from-peak = (peak - equity) / peak.
    [23]     position notional ratio = |position_value| / initial_equity.
    [24]     active bar count normalized = bars_in_engine / max_bars_per_engine.

Action (``Discrete(10)``):
    0-2: GRID  at 0.5x / 1.0x / 1.5x size.
    3-5: TREND at 0.5x / 1.0x / 1.5x size.
    6-8: SWING at 0.5x / 1.0x / 1.5x size.
    9:   GO_FLAT — close everything.

Reward:
    r_t = (equity_return_t - bench_return_t)
          - fee_rate * turnover_t
          - lambda_dd * dd_step_t

Design rationale (see ``docs/superpowers/specs/2026-06-18-rl-execution-agent-design.md``
§6 for the full argument):
    * **Excess-over-passive** — de-biases reward from market direction; the
      agent cannot win by riding a bull market.
    * **Cost inside reward** — prevents the classic RL churn-to-death failure.
    * **Drawdown penalty** — risk-adjusted signal so high-return/high-drawdown
      policies are not favoured.
"""
from __future__ import annotations

from dataclasses import dataclass

import gymnasium as gym
import numpy as np
import pandas as pd

from src.rl.features import FEATURE_COLS, compute_features

# --- Action decoding -------------------------------------------------------

# Single source of truth lives in src.rl.action_map (pure module, no heavy
# deps) so the live router can import it without pulling gymnasium. Re-exported
# here for backwards compatibility.
from src.rl.action_map import ACTION_TO_ENGINE_SIZE  # noqa: E402,F401

# Engine names in canonical one-hot order.
ENGINES: tuple[str, ...] = ("flat", "grid", "trend", "swing")
_ENGINE_INDEX: dict[str, int] = {name: i for i, name in enumerate(ENGINES)}


@dataclass
class EnvConfig:
    """Configuration for ``TradingEnv``.

    All monetary/size parameters are in dollar terms (equity units) unless
    otherwise noted. The defaults match the spec's §6 reward and §7.1 engine
    parameters.
    """

    # --- Account / reward ---
    initial_capital: float = 10_000.0
    fee_rate: float = 0.001  # 0.1% per side (maker).
    lambda_dd: float = 0.5  # drawdown-step penalty weight.
    window_length: int = 4_300  # ~6 months of 1h bars per episode.
    max_position_pct: float = 0.6666  # max notional / equity for trend/swing.

    # --- Observation helpers ---
    # Normaliser for the "bars in engine" observation; the spec frames the
    # agent as making 6-month routing decisions, so a 200-bar (~1 week) half-life
    # gives meaningful resolution rather than dividing by the full 4300-step
    # window (which would crush the value to ~0).
    max_bars_per_engine: int = 200

    # --- Feature/indicator windows ---
    atr_period: int = 14
    warmup_bars: int = 50  # skip first N bars so features are warmed up.

    # --- Engine parameters (simplified primitives) ---
    grid_spacing_atr: float = 1.5  # ATR multiplier between grid levels.
    grid_levels: int = 5  # buy levels below + sell levels above anchor.
    grid_level_pct: float = (
        0.10  # each level deploys 10% of equity * size_mult.
    )
    trend_trailing_atr: float = 2.5  # Chandelier trailing-stop ATR multiplier.
    swing_tp_atr: float = 1.5  # swing take-profit ATR multiplier.
    swing_sl_atr: float = 2.5  # swing stop-loss ATR multiplier.

    # Trend direction: allow shorts (close < sma_fast -> short). Off by default
    # matches the spot-only deployed system; flip to True for the futures ablation.
    allow_shorts: bool = False


# --- Engine primitives -----------------------------------------------------


def _initial_engine_state(engine: str, size_mult: float) -> dict:
    """Return a fresh state dict for ``engine`` at activation time."""
    if engine == "grid":
        return {
            "deployed": False,
            "inventory": 0.0,
            "avg_cost": 0.0,
            "anchor": 0.0,
            "buy_levels": np.empty(0, dtype=np.float64),
            "sell_levels": np.empty(0, dtype=np.float64),
            "size_mult": size_mult,
        }
    if engine in ("trend", "swing"):
        return {
            "in_position": False,
            "side": 0,
            "size": 0.0,
            "entry": 0.0,
            "extreme": 0.0,
            "trail": 0.0,
            "tp": 0.0,
            "sl": 0.0,
            "size_mult": size_mult,
        }
    return {"size_mult": size_mult}


# --- The environment -------------------------------------------------------


class TradingEnv(gym.Env):
    """A Gymnasium environment that replays 1h bars and routes capital across
    three simplified engines.

    The environment is deterministic given (data, seed): engine primitives are
    pure functions of (bar, state, config) and the only randomness is the
    episode window offset, controlled by ``reset(seed=...)``.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        df: pd.DataFrame,
        config: EnvConfig | None = None,
    ) -> None:
        """Initialise the env on a pre-loaded OHLCV frame.

        Args:
            df: OHLCV DataFrame indexed by UTC datetime, as produced by
                ``src.rl.data.load_klines``. Must contain at least
                ``warmup_bars + window_length + 1`` rows for a full episode;
                shorter frames still work but produce shorter episodes.
            config: ``EnvConfig`` (defaults if None).
        """
        super().__init__()
        self.config = config or EnvConfig()

        if df.empty:
            raise ValueError("TradingEnv requires a non-empty OHLCV frame.")
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                raise ValueError(f"df missing required column: {col}")

        self._raw_df = df
        # Exposed so evaluators can map each collected return to its bar's
        # timestamp (alignment by timestamp, not by array position).
        self.frame_index = df.index

        # Precompute features and indicators ONCE on the full frame (cheap O(N)
        # pandas/numpy ops); per-step work is then pure lookup + arithmetic).
        feats = compute_features(df)[FEATURE_COLS]
        self._features = feats.to_numpy(dtype=np.float64)

        self._opens = df["open"].to_numpy(dtype=np.float64)
        self._highs = df["high"].to_numpy(dtype=np.float64)
        self._lows = df["low"].to_numpy(dtype=np.float64)
        self._closes = df["close"].to_numpy(dtype=np.float64)

        # Simple-moving-average of close (trend entry signal) and ATR (raw,
        # not normalised — engine primitives need dollar-ATR). Both match
        # the formulas in src/rl/features.py.
        closes_s = df["close"]
        self._sma_fast = (
            closes_s.rolling(window=20).mean().to_numpy(dtype=np.float64)
        )

        prev_close = closes_s.shift(1)
        true_range = np.maximum.reduce(
            [
                df["high"].to_numpy() - df["low"].to_numpy(),
                np.abs(df["high"].to_numpy() - prev_close.to_numpy()),
                np.abs(df["low"].to_numpy() - prev_close.to_numpy()),
            ]
        )
        tr_series = pd.Series(true_range, index=df.index)
        self._atr = (
            tr_series.rolling(window=self.config.atr_period)
            .mean()
            .to_numpy(dtype=np.float64)
        )

        # Spaces.
        n_features = self._features.shape[1]  # 11
        obs_dim = n_features + len(ENGINES) + 4  # 11 + 4 + 4 = 19
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float64
        )
        self.action_space = gym.spaces.Discrete(10)

        # Episode state (filled by reset).
        self.equity: float = self.config.initial_capital
        self._initial_equity: float = self.config.initial_capital
        self._peak_equity: float = self.config.initial_capital
        self._prev_equity: float = self.config.initial_capital
        self._prev_close: float = float(self._closes[0])
        self._prev_dd: float = 0.0
        self._realized_pnl: float = 0.0
        self._last_turnover: float = 0.0
        self._current_engine: str = "flat"
        self._current_size_mult: float = 1.0
        self._bars_in_engine: int = 0
        self._engine_state: dict = _initial_engine_state("flat", 1.0)
        self._bar_idx: int = 0
        self._step_count: int = 0
        self._window_start: int = 0

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        """Reset the episode. Picks a random window of bars (seed-controlled)
        and returns the initial observation."""
        super().reset(seed=seed)
        cfg = self.config

        self.equity = cfg.initial_capital
        self._initial_equity = cfg.initial_capital
        self._peak_equity = cfg.initial_capital
        self._prev_equity = cfg.initial_capital
        self._prev_dd = 0.0
        self._realized_pnl = 0.0
        self._last_turnover = 0.0
        self._current_engine = "flat"
        self._current_size_mult = 1.0
        self._bars_in_engine = 0
        self._engine_state = _initial_engine_state("flat", 1.0)
        self._step_count = 0

        self._window_start = self._pick_window_start()
        self._bar_idx = self._window_start
        # prev_close is the close of the first bar in the window — the price
        # the agent "sees" at reset and the reference for the first step's
        # execution + buy-and-hold benchmark.
        self._prev_close = float(self._closes[self._window_start])
        return self._build_obs(), self._build_info()

    def step(self, action):
        """Advance one bar: apply the action, run the engine through the bar,
        compute reward, return (obs, reward, terminated, truncated, info)."""
        cfg = self.config
        self._step_count += 1

        # Move to the next bar (the bar the action will be applied through).
        self._bar_idx += 1
        if self._bar_idx >= len(self._closes):
            # Ran out of bars (frame shorter than window): truncate cleanly.
            # Clamp bar_idx so _build_obs (which indexes features/closes) works.
            self._bar_idx = len(self._closes) - 1
            return (
                self._build_obs(),
                0.0,
                False,
                True,
                self._build_info(),
            )

        bar_idx = self._bar_idx
        prev_close = self._prev_close
        equity_start = self.equity

        bar_high = self._highs[bar_idx]
        bar_low = self._lows[bar_idx]
        bar_close = self._closes[bar_idx]
        atr = self._safe_atr(bar_idx)
        bar = {"high": bar_high, "low": bar_low, "close": bar_close}

        # --- Decode action and apply engine switch ---
        engine, size_mult = ACTION_TO_ENGINE_SIZE[int(action)]
        turnover = 0.0

        if engine == "flat":
            # GO_FLAT: close any open position.
            turnover += self._close_engine(prev_close)
            self._current_engine = "flat"
            self._current_size_mult = 0.0
            self._bars_in_engine = 0
            self._engine_state = _initial_engine_state("flat", 0.0)
        elif engine == self._current_engine:
            # Same engine: just update the size multiplier (no trade cost).
            # Affects future deployments/re-entries only; current position is
            # left untouched (avoids churn-on-resize).
            self._current_size_mult = size_mult
            self._engine_state["size_mult"] = size_mult
            self._bars_in_engine += 1
        else:
            # Engine switch: close current at prev_close, activate new engine.
            turnover += self._close_engine(prev_close)
            self._current_engine = engine
            self._current_size_mult = size_mult
            self._bars_in_engine = 0
            self._engine_state = _initial_engine_state(engine, size_mult)

        # --- Run the (now current) engine through this bar ---
        bar_pnl, engine_turnover = self._run_engine(
            self._current_engine, bar, prev_close, atr, bar_idx
        )
        turnover += engine_turnover

        # --- Update equity (NET of fees — this is the actual account equity
        # the agent would see in a paper/live broker). Used for the blowup
        # check, drawdown, and position sizing. ---
        fees = turnover * cfg.fee_rate
        self.equity = equity_start + bar_pnl - fees
        self._realized_pnl += bar_pnl  # bar_pnl includes realised portion
        self._last_turnover = turnover

        # --- Drawdown / peak tracking ---
        if self.equity > self._peak_equity:
            self._peak_equity = self.equity
        dd = max(0.0, (self._peak_equity - self.equity) / self._peak_equity)
        dd_step = max(0.0, dd - self._prev_dd)
        self._prev_dd = dd

        # --- Reward (excess return - fees - drawdown penalty) ---
        # Note: equity is net of fees, so equity_return already embeds the fee
        # drag. The explicit ``- fee_rate * turnover_norm`` term amplifies the
        # cost signal a second time — a deliberate anti-churn knob standard in
        # RL trading rewards. Toggle by halving fee_rate here if training
        # produces an overly-frozen policy.
        equity_return = (self.equity - equity_start) / max(equity_start, 1e-8)
        bench_return = (bar_close - prev_close) / max(prev_close, 1e-8)
        turnover_norm = turnover / max(equity_start, 1e-8)
        reward = (
            (equity_return - bench_return)
            - cfg.fee_rate * turnover_norm
            - cfg.lambda_dd * dd_step
        )

        # --- Advance state ---
        self._prev_equity = self.equity
        self._prev_close = bar_close

        terminated = self.equity < 0.5 * self._initial_equity
        truncated = self._step_count >= cfg.window_length

        return (
            self._build_obs(),
            float(reward),
            terminated,
            truncated,
            self._build_info(),
        )

    # ------------------------------------------------------------------
    # Engine dispatch
    # ------------------------------------------------------------------

    def _run_engine(
        self,
        engine: str,
        bar: dict,
        prev_close: float,
        atr: float,
        bar_idx: int,
    ) -> tuple[float, float]:
        """Run ``engine`` through one bar. Returns (bar_pnl, turnover_notional).

        ``bar_pnl`` is the engine's total contribution to equity for this bar
        (realised + change in mark-to-market). Entry/exit fees are NOT
        subtracted here — the caller scales total turnover by fee_rate.
        """
        if engine == "grid":
            return self._grid_step(bar, prev_close, atr)
        if engine == "trend":
            return self._trend_step(bar, prev_close, atr, bar_idx)
        if engine == "swing":
            return self._swing_step(bar, prev_close, atr)
        # flat: no position, no P&L.
        return 0.0, 0.0

    def _close_engine(self, prev_close: float) -> float:
        """Close the current engine's open position at ``prev_close``.

        Returns the notional traded (used for fee accounting). The mark-to-
        market was already added to equity in prior bars, so closing itself
        does not change equity — it only converts unrealised to realised.
        """
        eng = self._current_engine
        state = self._engine_state
        turnover = 0.0

        if eng in ("trend", "swing"):
            if state.get("in_position"):
                turnover = abs(state["size"] * prev_close)
                state["in_position"] = False
        elif eng == "grid":
            inv = state.get("inventory", 0.0)
            if inv > 1e-12:
                turnover = abs(inv * prev_close)
                state["inventory"] = 0.0
                state["avg_cost"] = 0.0
                state["deployed"] = False
        return turnover

    # ------------------------------------------------------------------
    # Engine primitives (grid / trend / swing)
    # ------------------------------------------------------------------

    def _grid_step(
        self, bar: dict, prev_close: float, atr: float
    ) -> tuple[float, float]:
        """Mean-reversion grid: deploy symmetric levels around the activation
        price; each bar, harvest any level crosses.

        Inventory accumulates when buy levels cross; realised profit books
        when sell levels cross against average cost. P&L = realised + MTM change.
        """
        cfg = self.config
        state = self._engine_state
        high, low, close = bar["high"], bar["low"], bar["close"]

        # First call after activation: deploy the level ladder around prev_close.
        if not state["deployed"]:
            spacing = max(cfg.grid_spacing_atr * atr, 1e-6)
            anchor = prev_close
            levels_down = np.array(
                [anchor - i * spacing for i in range(1, cfg.grid_levels + 1)],
                dtype=np.float64,
            )
            levels_up = np.array(
                [anchor + i * spacing for i in range(1, cfg.grid_levels + 1)],
                dtype=np.float64,
            )
            state["anchor"] = anchor
            state["buy_levels"] = levels_down
            state["sell_levels"] = levels_up
            state["deployed"] = True

        # level_notional recomputed each bar from current equity + size_mult —
        # lets a same-engine size update propagate to future level touches.
        level_notional = self.equity * cfg.grid_level_pct * state["size_mult"]

        inv_start = state["inventory"]
        avg_cost_start = state["avg_cost"]
        mtm_start = (
            (prev_close - avg_cost_start) * inv_start if inv_start > 0 else 0.0
        )

        inventory = inv_start
        avg_cost = avg_cost_start
        realised = 0.0
        turnover = 0.0

        # Buy-level crosses (accumulate inventory).
        for bl in state["buy_levels"]:
            if low <= bl <= high:
                units = level_notional / bl
                if inventory + units > 0:
                    avg_cost = (avg_cost * inventory + bl * units) / (
                        inventory + units
                    )
                inventory += units
                turnover += level_notional

        # Sell-level crosses (realise against avg cost).
        for sl in state["sell_levels"]:
            if low <= sl <= high and inventory > 1e-12:
                units_to_sell = min(level_notional / sl, inventory)
                realised += (sl - avg_cost) * units_to_sell
                inventory -= units_to_sell
                turnover += units_to_sell * sl
                if inventory <= 1e-12:
                    inventory = 0.0
                    avg_cost = 0.0

        state["inventory"] = inventory
        state["avg_cost"] = avg_cost

        mtm_end = (close - avg_cost) * inventory if inventory > 0 else 0.0
        bar_pnl = realised + (mtm_end - mtm_start)
        return bar_pnl, turnover

    def _trend_step(
        self, bar: dict, prev_close: float, atr: float, bar_idx: int
    ) -> tuple[float, float]:
        """Trend-following: enter on momentum (close vs SMA-20), trail a
        Chandelier stop (2.5xATR from extreme). Long-only by default."""
        cfg = self.config
        state = self._engine_state
        high, low, close = bar["high"], bar["low"], bar["close"]

        # (Re-)enter if flat.
        if not state.get("in_position"):
            sma = self._sma_fast[bar_idx - 1] if bar_idx > 0 else prev_close
            if not np.isfinite(sma) or sma <= 0:
                sma = prev_close
            if cfg.allow_shorts and prev_close < sma:
                side = -1
            else:
                side = 1
            notional = self.equity * cfg.max_position_pct * state["size_mult"]
            size = notional / max(prev_close, 1e-8)
            state.update(
                {
                    "in_position": True,
                    "side": side,
                    "size": size,
                    "entry": prev_close,
                    "extreme": prev_close,
                }
            )
            turnover = notional
        else:
            turnover = 0.0

        side, size = state["side"], state["size"]

        # Update extreme + trailing stop.
        if side == 1:
            state["extreme"] = max(state["extreme"], high)
        else:
            state["extreme"] = min(state["extreme"], low)
        trail = state["extreme"] - side * cfg.trend_trailing_atr * atr
        state["trail"] = trail

        bar_pnl: float
        if (side == 1 and low <= trail) or (side == -1 and high >= trail):
            # Stop hit: close at trail price. Bar P&L = (exit - prev_close) * size * side.
            bar_pnl = (trail - prev_close) * size * side
            turnover += abs(size * trail)
            state["in_position"] = False
        else:
            # Position held: MTM change vs prev_close.
            bar_pnl = (close - prev_close) * size * side

        return bar_pnl, turnover

    def _swing_step(
        self, bar: dict, prev_close: float, atr: float
    ) -> tuple[float, float]:
        """Swing-reversal: enter long immediately on activation, fixed TP/SL
        bracket (1.5xATR up / 2.5xATR down). Stays flat after a close until the
        engine is re-activated by the agent."""
        cfg = self.config
        state = self._engine_state
        high, low, close = bar["high"], bar["low"], bar["close"]

        # Enter on activation (long only — matches the deployed spot engine).
        if not state.get("in_position"):
            notional = self.equity * cfg.max_position_pct * state["size_mult"]
            size = notional / max(prev_close, 1e-8)
            state.update(
                {
                    "in_position": True,
                    "side": 1,
                    "size": size,
                    "entry": prev_close,
                    "tp": prev_close + cfg.swing_tp_atr * atr,
                    "sl": prev_close - cfg.swing_sl_atr * atr,
                }
            )
            turnover = notional
        else:
            turnover = 0.0

        side, size = state["side"], state["size"]

        bar_pnl: float
        # Order matters: check the bad outcome (SL) first under ambiguity so we
        # don't double-count a violent bar as both TP and SL hitting.
        if low <= state["sl"]:
            bar_pnl = (state["sl"] - prev_close) * size * side
            turnover += abs(size * state["sl"])
            state["in_position"] = False
        elif high >= state["tp"]:
            bar_pnl = (state["tp"] - prev_close) * size * side
            turnover += abs(size * state["tp"])
            state["in_position"] = False
        else:
            bar_pnl = (close - prev_close) * size * side

        return bar_pnl, turnover

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_atr(self, bar_idx: int) -> float:
        """ATR at ``bar_idx`` with a fallback to bar range (defensive — warmup
        should normally guarantee a real value)."""
        atr = self._atr[bar_idx]
        if not np.isfinite(atr) or atr <= 0:
            range_fallback = self._highs[bar_idx] - self._lows[bar_idx]
            atr = range_fallback if range_fallback > 0 else 1e-6
        return float(atr)

    def _pick_window_start(self) -> int:
        """Pick the reset bar index. Random within [warmup, n_bars - window - 1]
        so a full window fits; falls back to ``warmup_bars`` if the frame is
        shorter than one window (episode then runs to end-of-data)."""
        cfg = self.config
        n_bars = len(self._closes)
        max_start = n_bars - cfg.window_length - 1
        if max_start < cfg.warmup_bars:
            return cfg.warmup_bars
        # np_random is the gym-provided Generator (seed-controlled).
        return int(self.np_random.integers(cfg.warmup_bars, max_start + 1))

    def _position_value(self) -> float:
        """Mark-to-market notional of the currently open position (always >= 0)."""
        eng = self._current_engine
        state = self._engine_state
        close = self._prev_close
        if eng in ("trend", "swing") and state.get("in_position"):
            return abs(state["size"] * close)
        if eng == "grid" and state.get("inventory", 0.0) > 0:
            return abs(state["inventory"] * close)
        return 0.0

    def _build_obs(self) -> np.ndarray:
        """Assemble the 19-dim observation vector."""
        cfg = self.config
        feats = self._features[self._bar_idx]

        one_hot = np.zeros(len(ENGINES), dtype=np.float64)
        one_hot[_ENGINE_INDEX[self._current_engine]] = 1.0

        unrealised_pct = (self.equity - self._initial_equity) / max(
            self._initial_equity, 1e-8
        )
        dd = (
            0.0
            if self._peak_equity <= 0
            else ((self._peak_equity - self.equity) / self._peak_equity)
        )
        pos_ratio = self._position_value() / max(self._initial_equity, 1e-8)
        bars_norm = min(
            self._bars_in_engine / max(cfg.max_bars_per_engine, 1), 1.0
        )

        obs = np.concatenate(
            [
                feats.astype(np.float64),
                one_hot,
                np.array(
                    [unrealised_pct, dd, pos_ratio, bars_norm],
                    dtype=np.float64,
                ),
            ]
        )
        # Defensive: replace any stray NaN/inf (shouldn't happen, but PPO will
        # silently diverge if it does) with 0.
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

    def _build_info(self) -> dict:
        """Per-step diagnostic dict (exposed to the agent + logged)."""
        return {
            "equity": float(self.equity),
            "initial_equity": float(self._initial_equity),
            "peak_equity": float(self._peak_equity),
            "realized_pnl": float(self._realized_pnl),
            "turnover": float(self._last_turnover),
            "bars_in_engine": int(self._bars_in_engine),
            "engine": self._current_engine,
            "in_position": bool(self._engine_state.get("in_position", False)),
            "size_mult": float(self._current_size_mult),
            "step": int(self._step_count),
            "bar_idx": int(self._bar_idx),
        }
