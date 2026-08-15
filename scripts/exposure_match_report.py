#!/usr/bin/env python3
"""Exposure-matched baseline analysis (audit Task 6).

Reads the persisted walk-forward return series (reports/returns/) and the
walk-forward JSON reports, builds over the IDENTICAL timestamps:
  1. buy & hold scaled to PPO's realised time-in-market fraction
  2. a random-entry baseline matching PPO's exposure and trade-length
     distribution over >= 200 seeds

Reports where PPO's MaxDD and total return fall within the random-baseline
distribution (percentiles), and states plainly whether the drawdown
advantage survives exposure matching.

Usage:
    python scripts/exposure_match_report.py PAIR [--seeds 200] \
        [--returns-dir reports/returns] [--out reports/exposure_match_PAIR.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rl.exposure_match import (  # noqa: E402
    percentile_of,
    random_entry_returns,
    scaled_buy_hold_returns_constant,
)
from src.rl.risk_stats import max_drawdown, sharpe_annualized  # noqa: E402


def load_pooled(pair: str, returns_dir: str) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate per-fold PPO/RF/TA CSVs in fold order."""
    d = Path(returns_dir)
    folds = sorted(
        {p.name.split("_fold")[1].split(".")[0] for p in d.glob(f"{pair}_*_fold*.csv")},
        key=int,
    )
    ppo, rf, ta = [], [], []
    for f in folds:
        ppo.append(np.loadtxt(d / f"{pair}_ppo_fold{f}.csv", delimiter=",", skiprows=1, usecols=1))
        rf.append(np.loadtxt(d / f"{pair}_rf_fold{f}.csv", delimiter=",", skiprows=1, usecols=1))
        ta.append(np.loadtxt(d / f"{pair}_ta_fold{f}.csv", delimiter=",", skiprows=1, usecols=1))
    return np.concatenate(ppo), np.concatenate(rf), np.concatenate(ta)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pair")
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--returns-dir", default="reports/returns")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ppo, rf, ta = load_pooled(args.pair, args.returns_dir)
    n = len(ppo)
    if n < 100:
        print(f"insufficient pooled bars: {n}", file=sys.stderr)
        return 1

    # PPO's realised exposure: fraction of bars with a non-zero return.
    ppo_exposure = float(np.mean(ppo != 0.0))
    # Trade-length proxy: contiguous runs of non-zero returns.
    runs, run = [], 0
    for r in ppo:
        if r != 0.0:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    avg_trade_length = int(np.mean(runs)) if runs else 24

    # Benchmark = TA comparator over identical timestamps (passive holding).
    bh = ta

    # 1. Scaled buy & hold at PPO's exposure fraction.
    scaled = scaled_buy_hold_returns_constant(bh, ppo_exposure)
    scaled_maxdd = max_drawdown(scaled)
    scaled_total = float(np.sum(np.log1p(scaled)))

    # 2. Random-entry baselines.
    dd_dist, ret_dist = [], []
    for seed in range(args.seeds):
        rret, expo, trades = random_entry_returns(
            bh, target_exposure=ppo_exposure, avg_trade_length=avg_trade_length,
            seed=seed,
        )
        dd_dist.append(max_drawdown(rret))
        ret_dist.append(float(np.sum(np.log1p(rret))))
    dd_dist, ret_dist = np.array(dd_dist), np.array(ret_dist)

    ppo_maxdd = max_drawdown(ppo)
    ppo_total = float(np.sum(np.log1p(ppo)))

    dd_pct = percentile_of(ppo_maxdd, dd_dist)
    ret_pct = percentile_of(ppo_total, ret_dist)

    survives_dd = ppo_maxdd < float(np.percentile(dd_dist, 50))

    report = {
        "pair": args.pair,
        "pooled_bars": n,
        "ppo_exposure": ppo_exposure,
        "ppo_avg_trade_length_bars": avg_trade_length,
        "ppo": {
            "max_drawdown": ppo_maxdd,
            "total_log_return": ppo_total,
            "sharpe_all_bars": sharpe_annualized(ppo),
        },
        "scaled_buy_hold_at_same_exposure": {
            "max_drawdown": scaled_maxdd,
            "total_log_return": scaled_total,
        },
        "random_entry_baseline": {
            "seeds": args.seeds,
            "max_drawdown": {
                "p5": float(np.percentile(dd_dist, 5)),
                "median": float(np.percentile(dd_dist, 50)),
                "p95": float(np.percentile(dd_dist, 95)),
            },
            "total_log_return": {
                "p5": float(np.percentile(ret_dist, 5)),
                "median": float(np.percentile(ret_dist, 50)),
                "p95": float(np.percentile(ret_dist, 95)),
            },
        },
        "ppo_percentile_in_random_distribution": {
            "max_drawdown": dd_pct,
            "total_log_return": ret_pct,
        },
        "drawdown_advantage_survives_exposure_matching": survives_dd,
    }
    out = args.out or f"reports/exposure_match_{args.pair}.json"
    Path(out).write_text(json.dumps(report, indent=2, sort_keys=True))

    print(json.dumps(report, indent=2, sort_keys=True))
    verdict = (
        "PPO's drawdown advantage SURVIVES exposure matching "
        f"(MaxDD {ppo_maxdd:.4f} below the random-baseline median "
        f"{np.percentile(dd_dist, 50):.4f})"
        if survives_dd
        else "PPO's drawdown advantage does NOT survive exposure matching "
        f"(MaxDD {ppo_maxdd:.4f} at percentile {dd_pct:.0f} of the "
        f"exposure-matched random distribution)"
    )
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
