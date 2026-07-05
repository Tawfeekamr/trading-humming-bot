#!/usr/bin/env python3
"""Sim-to-real fidelity: does the policy behave on the REAL Rust engines
consistently with what env.py (toy engines) predicted on the same bars?

This is the **falsification gate** for the PPO -> live pipeline. The PPO
policy was trained on ``src/rl/env.py`` (simplified toy engines). The paper
Rust instance runs the REAL engines. ``compare()`` asks whether the per-bar
equity trajectory the toy env predicted matches what the paper engine
produced on the SAME bars + SAME routing decisions.

If they diverge, the toy engines do not represent production -> the trained
policy is built on a wrong simulator -> **do not promote to live.**

Pass  = Pearson(equity_env, equity_paper) > PEARSON_THRESHOLD
       AND mean |per-bar P&L diff| < pnl_band.
Fail  = divergence (do not promote).

The two equity series are produced elsewhere (env via a replay through
``env.py``; paper via the running paper instance's equity log); this script
just consumes them.

Usage::

    python scripts/fidelity_check.py path/to/env_equity.csv path/to/paper_equity.csv

where each file is a comma-separated list of floats (one equity curve per
file, sampled on identical bars).
"""
from __future__ import annotations

import os
import sys

import numpy as np

# Make the repo root importable so `from scripts.fidelity_check import compare`
# works both when this file is run directly (``python scripts/fidelity_check.py``)
# and when it is imported from a test (``from scripts.fidelity_check import ...``).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# --- Pass/fail thresholds (named as constants so they are easy to tune). ---
# Defaults per the Task 10 spec / .superpowers/sdd spec; they are admittedly
# somewhat arbitrary and should be revisited once we have real env-vs-paper
# replay data to calibrate against.
PEARSON_THRESHOLD: float = 0.7
DEFAULT_PNL_BAND: float = 0.005


def compare(
    env_equity: list[float],
    paper_equity: list[float],
    pnl_band: float = DEFAULT_PNL_BAND,
) -> dict:
    """Compare the env.py equity trajectory to the paper Rust equity trajectory.

    Pure (no I/O), numpy-only. Both inputs are per-bar equity curves sampled
    on the SAME bars under the SAME routing decisions.

    Parameters
    ----------
    env_equity:
        Per-bar equity (USD) produced by replaying the policy through
        ``src/rl/env.py`` (the toy engines the policy was trained on).
    paper_equity:
        Per-bar equity (USD) produced by the paper Rust instance (the REAL
        engines) on the same bars + same routing decisions.
    pnl_band:
        Max tolerable mean absolute per-bar return difference. Defaults to
        ``0.005`` (50 bps / bar) per the spec.

    Returns
    -------
    dict with keys:
        ``pearson``                : float in [-1, 1]   (0.0 if undefined)
        ``mean_abs_bar_pnl_diff``  : float              (0.0 if empty)
        ``pass``                   : bool

    Edge cases
    ----------
    * Length mismatch      : truncate to the shorter series (no error).
    * Zero-variance series : Pearson is undefined -> return 0.0 (-> fail).
    * Empty / single point : Pearson 0.0, mean-abs-diff 0.0 (-> fail).
    """
    a = np.asarray(env_equity, dtype=float)
    b = np.asarray(paper_equity, dtype=float)

    # Truncate to the shorter series so a length mismatch doesn't crash.
    n = min(len(a), len(b))
    a = a[:n]
    b = b[:n]

    # Pearson is undefined for zero-variance series (constant equity) or for
    # series shorter than 2 points. In all those cases, correlation carries no
    # information about simulator fidelity -> return 0.0 -> verdict fails.
    if n >= 2 and a.std() > 0.0 and b.std() > 0.0:
        corr = float(np.corrcoef(a, b)[0, 1])
    else:
        corr = 0.0

    # Per-bar simple returns. ``np.maximum`` guards against zero-crossings
    # producing div-by-zero (a tiny epsilon keeps the math finite without
    # changing the verdict on any realistic equity series).
    if n >= 2:
        env_ret = np.diff(a) / np.maximum(a[:-1], 1e-8)
        pap_ret = np.diff(b) / np.maximum(b[:-1], 1e-8)
        # ``np.mean`` of an empty array raises a RuntimeWarning; this branch
        # only runs when n >= 2 so diff is non-empty -> safe.
        mean_diff = float(np.mean(np.abs(env_ret - pap_ret)))
    else:
        mean_diff = 0.0

    return {
        "pearson": corr,
        "mean_abs_bar_pnl_diff": mean_diff,
        "pass": corr > PEARSON_THRESHOLD and mean_diff < pnl_band,
    }


if __name__ == "__main__":
    # Args: path to env equity csv + path to paper equity log.
    # Each file is a comma-separated list of floats.
    if len(sys.argv) != 3:
        print(
            "usage: python scripts/fidelity_check.py "
            "<env_equity.csv> <paper_equity.csv>",
            file=sys.stderr,
        )
        sys.exit(2)

    env_eq = [float(x) for x in open(sys.argv[1]).read().split(",") if x.strip()]
    pap_eq = [float(x) for x in open(sys.argv[2]).read().split(",") if x.strip()]
    print(compare(env_eq, pap_eq))
