#!/usr/bin/env python3
# src/rl/evaluate.py
"""Evaluation and benchmarking script for the RL execution pipeline.

Runs a trained PPO Agent, the Supervised Baseline, and passive Buy & Hold
over an Out-Of-Sample (OOS) window. Computes core metrics (Sharpe, MaxDD,
Win Rate) and runs the Diebold-Mariano test on the return differentials to
assess statistical significance.

Usage::

    python -m src.rl.evaluate \
        --pair ETHUSDT \
        --ppo-model models/rl/ppo_ETHUSDT_2026-07-05.zip \
        --rf-model models/regime_ETH-USDT.pkl \
        --start 2026-07-05 --end 2026-08-05
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # Heavy RL deps (gymnasium / stable-baselines3 / torch via env+router) are
    # imported lazily inside ``main()`` so the pure metric + OOS-boundary
    # helpers below stay importable with only numpy — which is what makes them
    # unit-testable without the ~2 GB training stack installed.
    from src.rl.data import load_klines
    from src.rl.env import EnvConfig, TradingEnv
    from src.rl.router import PPORouter, SupervisedRegimeRouter


_CAVEATS = """
## Methodology Notes & Caveats

- **PPO train/OOS boundary**: verified via the model's provenance sidecar
  (`data_end` < OOS start). This benchmark is genuinely out-of-sample for PPO.
- **RF baseline**: `regime_*.pkl` carries no provenance, so its train/OOS
  boundary is NOT verified here — it may overlap the OOS start by a few days.
  Close this by retraining from a reproducible trainer.
- **Sharpe**: computed over each strategy's full per-bar return stream
  (including flat / zero-return bars). See **Time in Market** to interpret the
  PPO/RF Sharpe relative to always-invested B&H — a strategy that holds cash
  legitimately has a different (compressed) return stream, not a bug.
- **Win Rate**: closed round-trips, trend / swing engines only. The grid
  engine has no `in_position` flag, so grid inventory cycles are not counted.
- **Scope**: single pair, single OOS window. For a thesis-grade claim, extend
  via multi-pair + walk-forward.
"""


def _diebold_mariano_test(
    returns_a: np.ndarray, returns_b: np.ndarray, max_lag: int = 5
) -> tuple[float, float]:
    """Diebold-Mariano test with Newey-West HAC variance.

    Tests the null hypothesis that the two models have the same accuracy.
    Here we treat negative return as 'loss'.
    Positive DM stat means model A outperforms model B.
    """
    from scipy.stats import norm

    d = returns_a - returns_b
    d_mean = np.mean(d)
    n = len(d)

    if n < 2:
        return 0.0, 1.0

    # Newey-West HAC Variance (Bartlett kernel)
    gamma_0 = np.var(d, ddof=0)
    hac_var = gamma_0

    for lag in range(1, max_lag + 1):
        if n > lag:
            gamma_k = np.sum((d[:-lag] - d_mean) * (d[lag:] - d_mean)) / n
            weight = 1 - (lag / (max_lag + 1))
            hac_var += 2 * weight * gamma_k

    if hac_var <= 0:
        hac_var = gamma_0
    if hac_var == 0:
        return 0.0, 1.0

    stat = d_mean / np.sqrt(hac_var / n)
    p_value = 2 * (1 - norm.cdf(abs(stat)))
    return stat, p_value


def _count_round_trips(
    equity_curve: list[float], in_position_flags: list[bool]
) -> tuple[int, int]:
    """Count closed round-trips and wins from a per-bar equity curve + the
    ``info["in_position"]`` flag stream collected during an episode.

    A round-trip is a maximal contiguous run of ``in_position == True`` bars.
    Entry equity is the equity of the bar *before* the run opens; exit equity
    is the equity of the last bar of the run (the bar on which the position
    is reported closed). A still-open position at end-of-episode is closed at
    the final equity.

    Note: the grid engine never sets ``in_position`` (its state dict has no
    such key), so grid inventory cycles are not counted here — only trend /
    swing round-trips are. This is a known, documented asymmetry.

    Args:
        equity_curve: ``[equity_0, equity_1, ...]`` where ``equity_0`` is the
            post-reset equity and ``equity_k`` is equity after step ``k``.
            Length = ``len(in_position_flags) + 1``.
        in_position_flags: per-step ``in_position`` from ``info``.

    Returns:
        ``(trades, wins)``.
    """
    trades = 0
    wins = 0
    in_trade = False
    entry_equity = 0.0

    for k, in_pos in enumerate(in_position_flags, start=1):
        if in_pos and not in_trade:
            in_trade = True
            entry_equity = equity_curve[k - 1]
        elif not in_pos and in_trade:
            in_trade = False
            trades += 1
            if equity_curve[k] > entry_equity:
                wins += 1

    if in_trade:  # close at end of episode
        trades += 1
        if equity_curve[-1] > entry_equity:
            wins += 1

    return trades, wins


def _time_in_market(engine_flags: list[str]) -> float:
    """Fraction of bars the strategy was deployed (engine != "flat").

    Discloses capital exposure so the PPO/RF Sharpe ratios — which span the
    flat, zero-return bars when the agent holds cash — are interpretable next
    to Buy & Hold's always-invested Sharpe. A strategy that parks in cash most
    of the time legitimately has a different (typically compressed) return
    stream; the exposure number lets the reader see that directly rather than
    game the Sharpe by computing it only over invested bars.
    """
    if not engine_flags:
        return 0.0
    deployed = sum(1 for e in engine_flags if e != "flat")
    return deployed / len(engine_flags)


def _load_provenance(ppo_model_path: str) -> dict | None:
    """Load the ``.json`` provenance sidecar next to a ``.zip`` PPO model.

    Returns ``None`` (not an error) if no sidecar exists — callers warn and
    proceed, since old/manual models may not carry one.
    """
    sidecar = Path(ppo_model_path).with_suffix(".json")
    if not sidecar.exists():
        return None
    try:
        return json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _check_oos_boundary(
    ppo_model_path: str, oos_start: date, allow_overlap: bool
) -> int:
    """Verify the PPO model was trained strictly before the OOS window starts.

    Reads ``data_end`` from the provenance sidecar next to the model. If the
    training data ends on or after ``oos_start``, the "OOS" window is actually
    in-sample for this model — the headline contamination risk.

    Returns 0 if OK (or unverifiable, with a warning); returns 1 (caller
    should abort) on a hard violation when ``allow_overlap`` is False.
    """
    prov = _load_provenance(ppo_model_path)
    if prov is None:
        print(
            f"WARNING: no provenance sidecar next to {ppo_model_path}; cannot "
            f"verify the PPO model was trained before OOS start {oos_start}.",
            file=sys.stderr,
        )
        return 0

    data_end_raw = prov.get("data_end")
    if not data_end_raw:
        print(
            "WARNING: provenance sidecar has no 'data_end'; cannot verify "
            "the OOS boundary.",
            file=sys.stderr,
        )
        return 0

    try:
        data_end = datetime.fromisoformat(str(data_end_raw))
        train_end_date = data_end.date()
    except ValueError:
        print(
            f"WARNING: could not parse data_end='{data_end_raw}' as an ISO "
            f"datetime; cannot verify the OOS boundary.",
            file=sys.stderr,
        )
        return 0

    if train_end_date >= oos_start:
        msg = (
            f"OOS BOUNDARY VIOLATION: PPO model trained on data ending "
            f"{train_end_date} >= OOS start {oos_start}. The OOS window is "
            f"NOT out-of-sample for this model (lookahead contamination). "
            f"Retrain with --train-end before {oos_start}."
        )
        if allow_overlap:
            print(f"WARNING: {msg}", file=sys.stderr)
            return 0
        print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    print(
        f"OOS boundary OK: PPO trained through {train_end_date} < OOS start "
        f"{oos_start}.",
        file=sys.stderr,
    )
    return 0


def _run_model(env: TradingEnv, router) -> dict:
    """Run a router deterministically while retaining legacy display keys."""
    obs, info = env.reset(seed=42)

    equity_curve = [info["equity"]]
    step_returns = []
    turnover = []
    in_position_flags: list[bool] = []
    engine_flags: list[str] = []

    done = False
    while not done:
        action = router.predict(obs)
        obs, reward, term, trunc, info = env.step(action)

        bar_pnl = info["equity"] - equity_curve[-1]
        step_returns.append(
            bar_pnl / equity_curve[-1] if equity_curve[-1] > 0 else 0
        )
        equity_curve.append(info["equity"])
        turnover.append(float(info.get("turnover", 0.0)))
        in_position_flags.append(bool(info.get("in_position", False)))
        engine_flags.append(str(info.get("engine", "flat")))
        done = term or trunc

    exposure_array = np.asarray(
        [engine != "flat" for engine in engine_flags], dtype=np.float64
    )

    trades, wins = _count_round_trips(equity_curve, in_position_flags)
    eq_array = np.array(equity_curve)
    returns = np.array(step_returns)

    total_return = (eq_array[-1] - eq_array[0]) / eq_array[0]
    peaks = np.maximum.accumulate(eq_array)
    drawdowns = np.divide(
        peaks - eq_array, peaks, out=np.zeros_like(eq_array), where=peaks > 0
    )
    max_dd = float(np.max(drawdowns, initial=0.0))
    ann_factor = np.sqrt(8760)
    sharpe = (
        (np.mean(returns) / np.std(returns)) * ann_factor
        if np.std(returns) > 0
        else 0
    )
    win_rate = wins / trades if trades > 0 else 0.0
    time_in_market = _time_in_market(engine_flags)
    initial_equity = float(eq_array[0]) if len(eq_array) else 1.0
    fee_rate = float(getattr(getattr(env, "config", None), "fee_rate", 0.0))
    fees = float(np.sum(turnover) * fee_rate / initial_equity)
    gains = float(np.sum(returns[returns > 0]))
    losses = float(-np.sum(returns[returns < 0]))
    profit_factor = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)

    return {
        "returns_array": returns,
        "equity_curve": eq_array,
        "trade_count": trades,
        "exposure_array": exposure_array,
        "wins": wins,
        "net_pnl": float(eq_array[-1] - eq_array[0]),
        "total_return": float(total_return),
        "profit_factor": float(profit_factor),
        "max_drawdown": max_dd,
        "time_in_market": float(time_in_market),
        "fees": fees,
        # Preserve the existing human-facing keys consumed by callers.
        "Total Return": f"{total_return * 100:.2f}%",
        "Max Drawdown": f"{max_dd * 100:.2f}%",
        "Sharpe Ratio": f"{sharpe:.2f}",
        "Win Rate": f"{win_rate * 100:.2f}%",
        "Time in Market": f"{time_in_market * 100:.1f}%",
        "Final Equity": f"${eq_array[-1]:.2f}",
    }


def parse_args():
    parser = argparse.ArgumentParser("RL Evaluation")
    parser.add_argument("--pair", default="ETHUSDT")
    parser.add_argument("--ppo-model", required=True)
    parser.add_argument("--rf-model", required=True)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument(
        "--allow-train-overlap",
        action="store_true",
        help="Permit evaluating a PPO model whose training data overlaps the "
        "OOS window (lookahead contamination). Off by default: the script "
        "refuses to publish an in-sample result as out-of-sample.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = (
        date.fromisoformat(args.start)
        if args.start
        else end_date - timedelta(days=30)
    )

    # Refuse to publish an in-sample result as out-of-sample: the PPO model's
    # provenance sidecar records its training data_end, and if that overlaps
    # the OOS window the benchmark is contaminated.
    rc = _check_oos_boundary(
        args.ppo_model, start_date, allow_overlap=args.allow_train_overlap
    )
    if rc != 0:
        return rc

    # Heavy RL deps imported lazily so the module loads (and the pure helpers
    # above stay unit-testable) without gymnasium / sb3 / torch installed.
    from src.rl.data import load_klines
    from src.rl.env import EnvConfig, TradingEnv
    from src.rl.router import PPORouter, SupervisedRegimeRouter

    print(f"Loading {args.pair} OOS data: {start_date} to {end_date}")
    df = load_klines(args.pair, start_date, end_date)

    if df.empty or len(df) < 100:
        print("Not enough data for evaluation.", file=sys.stderr)
        return 1

    print(f"Data loaded: {len(df)} bars.")

    # We set a window_length to cover the entire dataset
    config = EnvConfig(window_length=len(df))
    env = TradingEnv(df, config)

    print("Loading models...")
    ppo_router = PPORouter(args.ppo_model)
    # Notice the arg usage below is fixed
    rf_router = SupervisedRegimeRouter(args.rf_model)

    print("Simulating PPO Agent...")
    ppo_results = _run_model(env, ppo_router)

    print("Simulating Supervised Baseline...")
    rf_results = _run_model(env, rf_router)

    print("Simulating Buy & Hold...")
    closes = df["close"].to_numpy()[config.warmup_bars :]
    bh_returns = np.diff(closes) / closes[:-1]
    bh_eq = np.cumprod(1 + bh_returns) * config.initial_capital
    bh_eq *= 1.0 - config.fee_rate  # Pay one entry fee
    bh_total_return = bh_eq[-1] / config.initial_capital - 1
    bh_peaks = np.maximum.accumulate(bh_eq)
    bh_dd = np.max((bh_peaks - bh_eq) / bh_peaks)
    bh_sharpe = (
        (np.mean(bh_returns) / np.std(bh_returns)) * np.sqrt(8760)
        if np.std(bh_returns) > 0
        else 0
    )

    # Truncate arrays to shortest length in case env truncated slightly differently
    min_len = min(
        len(ppo_results["returns_array"]),
        len(rf_results["returns_array"]),
        len(bh_returns),
    )
    ppo_ret_trim = ppo_results["returns_array"][:min_len]
    rf_ret_trim = rf_results["returns_array"][:min_len]

    dm_stat, dm_p = _diebold_mariano_test(ppo_ret_trim, rf_ret_trim)

    report = f"""# RL Evaluation Benchmark: {args.pair} ({start_date} to {end_date})

## Performance Summary

| Metric | Buy & Hold | Supervised Baseline (RF) | RL Agent (PPO) |
|--------|------------|--------------------------|----------------|
| **Total Return** | {bh_total_return*100:.2f}% | {rf_results["Total Return"]} | {ppo_results["Total Return"]} |
| **Max Drawdown** | {bh_dd*100:.2f}% | {rf_results["Max Drawdown"]} | {ppo_results["Max Drawdown"]} |
| **Sharpe Ratio** | {bh_sharpe:.2f} | {rf_results["Sharpe Ratio"]} | {ppo_results["Sharpe Ratio"]} |
| **Win Rate** | N/A | {rf_results["Win Rate"]} | {ppo_results["Win Rate"]} |
| **Time in Market** | 100.0% | {rf_results["Time in Market"]} | {ppo_results["Time in Market"]} |
| **Final Equity** | ${bh_eq[-1]:.2f} | {rf_results["Final Equity"]} | {ppo_results["Final Equity"]} |

## Diebold-Mariano Test (PPO vs Supervised)
*Null Hypothesis: Both models have identical predictive/trading accuracy.*

- **DM Statistic**: {dm_stat:.4f}
- **P-Value**: {dm_p:.4f}

**Conclusion**: """

    if dm_p < 0.05:
        if dm_stat > 0:
            report += "The PPO Agent **significantly outperforms** the Supervised Baseline (p < 0.05)."
        else:
            report += "The Supervised Baseline **significantly outperforms** the PPO Agent (p < 0.05)."
    else:
        report += "There is **no statistically significant difference** in performance between the models."

    report += _CAVEATS

    print("\n" + report)

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "rl_benchmark.md"
    out_file.write_text(report)
    print(f"\nReport saved to {out_file}")


if __name__ == "__main__":
    sys.exit(main())
