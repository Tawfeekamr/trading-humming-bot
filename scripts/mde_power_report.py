#!/usr/bin/env python3
"""Minimum-detectable-effect and power analysis (batch 2 task 3).

Uses the actual per-bar PPO/RF series and the same Newey-West HAC variance
estimator as the paired mean-difference test, so the MDE reflects the
test's real autocorrelation-adjusted precision.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rl.evaluate import newey_west_lag  # noqa: E402
from src.rl.risk_stats import stationary_bootstrap_ci  # noqa: E402


def hac_variance(d: np.ndarray, max_lag: int) -> float:
    d_mean = np.mean(d)
    n = len(d)
    gamma_0 = np.var(d, ddof=0)
    hac_var = gamma_0
    for lag in range(1, max_lag + 1):
        if n > lag:
            gamma_k = np.sum((d[:-lag] - d_mean) * (d[lag:] - d_mean)) / n
            hac_var += 2 * (1 - lag / (max_lag + 1)) * gamma_k
    return max(hac_var, gamma_0)


def mde(hac_var: float, n: int, power: float = 0.80, alpha: float = 0.05) -> float:
    """Two-sided MDE for a mean with known HAC variance: delta such that
    P(|Z| > z_{a/2} | mu=delta) = power."""
    z_a = norm.ppf(1 - alpha / 2)
    z_b = norm.ppf(power)
    return (z_a + z_b) * np.sqrt(hac_var / n)


def achieved_power(delta: float, hac_var: float, n: int, alpha: float = 0.05) -> float:
    if hac_var <= 0 or n == 0:
        return float("nan")
    se = np.sqrt(hac_var / n)
    z_a = norm.ppf(1 - alpha / 2)
    return float(norm.cdf(delta / se - z_a) + norm.cdf(-delta / se - z_a))


def n_required(delta: float, hac_var: float, power: float = 0.80, alpha: float = 0.05) -> int:
    if delta == 0:
        return -1
    z_a = norm.ppf(1 - alpha / 2)
    z_b = norm.ppf(power)
    return int(np.ceil(((z_a + z_b) ** 2) * hac_var / delta**2))


def load_pooled(pair: str) -> tuple[np.ndarray, np.ndarray]:
    d = Path("reports/returns")
    folds = sorted({p.name.split("_fold")[1].split(".")[0] for p in d.glob(f"{pair}_ppo_fold*.csv")}, key=int)
    ppo = np.concatenate([np.loadtxt(d / f"{pair}_ppo_fold{f}.csv", delimiter=",", skiprows=1, usecols=1) for f in folds])
    rf = np.concatenate([np.loadtxt(d / f"{pair}_rf_fold{f}.csv", delimiter=",", skiprows=1, usecols=1) for f in folds])
    return ppo, rf


def main() -> int:
    out = {}
    for pair in ("ETHUSDT", "BNBUSDT"):
        ppo, rf = load_pooled(pair)
        d = ppo - rf
        n = len(d)
        lag = newey_west_lag(n)
        hv = hac_variance(d, lag)
        delta_obs = float(np.mean(d))

        mde_bar = mde(hv, n)
        # cumulative over the span: sum of per-bar diffs (log-return approx)
        cum_obs = float(np.sum(d)) * 100
        cum_mde = mde_bar * n * 100
        power_obs = achieved_power(delta_obs, hv, n)
        n_req = n_required(delta_obs, hv)
        years_req = n_req / 8760 if n_req > 0 else float("inf")

        # MaxDD bootstrap distribution: power via CI-width logic
        from src.rl.risk_stats import max_drawdown
        _, lo, hi, block = stationary_bootstrap_ci(
            ppo, rf, lambda a, b: max_drawdown(a) - max_drawdown(b),
            n_boot=500, seed=42,
        )
        dd_obs = max_drawdown(ppo) - max_drawdown(rf)
        dd_mde = (hi - lo) / 2  # half-width of the 95% CI ~ MDE at ~80% power

        entry = {
            "n_bars": n,
            "hac_lag": lag,
            "observed_per_bar_mean_diff": delta_obs,
            "observed_cumulative_diff_pct": cum_obs,
            "mde_per_bar_80pct_power": float(mde_bar),
            "mde_cumulative_pct_80pct_power": float(cum_mde),
            "achieved_power_for_observed_diff": float(power_obs),
            "n_required_for_observed_diff_80pct": int(n_req),
            "n_required_years_hourly": float(years_req),
            "maxdd": {
                "observed_diff": float(dd_obs),
                "bootstrap_ci95": [float(lo), float(hi)],
                "ci_half_width_mde_equivalent": float(dd_mde),
                "block_length": int(block),
            },
        }
        out[pair] = entry
        print(f"\n=== {pair} ===")
        print(f"n={n}, HAC lag={lag}")
        print(f"observed per-bar diff: {delta_obs:.6f} ({delta_obs*1e4:.2f} bp/bar)")
        print(f"observed cumulative diff: {cum_obs:+.2f}%")
        print(f"MDE (80% power): {mde_bar:.6f}/bar = {cum_mde:.2f}% cumulative")
        print(f"achieved power at observed diff: {power_obs:.4f}")
        print(f"n required: {n_req:,} bars = {years_req:.1f} years")
        print(f"MaxDD diff: {dd_obs:+.4f}, CI [{lo:.4f},{hi:.4f}], half-width {dd_mde:.4f}")

    Path("reports/mde_power.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
