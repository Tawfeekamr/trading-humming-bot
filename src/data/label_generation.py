# src/data/label_generation.py
"""Regime labeling for the supervised Random-Forest baseline.

The committed ``regime_*.pkl`` models were trained by code that was never
committed (the original ``label_generation.py`` was missing from the repo), so
their label definition is unrecoverable. This module defines a **new,
documented, lookahead-free-per-bar** labeling scheme so the RF baseline can be
retrained reproducibly. The result is a *different* baseline than the legacy
``.pkl`` (which used unknown labels) — that is the point: this one is defensible.

Label definition (3-class, forward-looking):
    * **1 (trending)** — ``|forward return over horizon| >= trend_thr``: the
      market sustains a directional move over the next ``horizon`` bars.
    * **2 (danger)** — ``min forward return over horizon <= danger_thr``: a
      drawdown / crash unfolds within the next ``horizon`` bars.
    * **0 (ranging)** — neither: no sustained move and no upcoming drawdown.

The forward-looking target is the *supervised label* (intentional lookahead —
you train on past bars whose future is known, then predict on the test set
where it is not). Leakage is prevented by the trainer's temporal train/test
split, not by removing lookahead from the label.

Danger takes precedence over trending: a bar that precedes a violent move is
labelled danger regardless of direction, because "go flat ahead of a crash"
matters more to the router than "ride the trend."
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Default horizon = 24 bars (1 day of 1h); thresholds chosen so that on real
# crypto 1h data the three classes are all meaningfully populated.
DEFAULT_HORIZON = 24
DEFAULT_TREND_THR = 0.02  # 2% sustained move over horizon
DEFAULT_DANGER_THR = -0.03  # 3% drawdown within horizon

_NO_LABEL = -1  # sentinel for the final `horizon` bars (no future to inspect)


def generate_regime_labels(
    df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON,
    trend_thr: float = DEFAULT_TREND_THR,
    danger_thr: float = DEFAULT_DANGER_THR,
) -> pd.DataFrame:
    """Add a ``regime_label`` column (0/1/2, or -1 where no label) to ``df``.

    Args:
        df: frame with a ``close`` column (typically the output of
            ``calculate_technical_features``, which preserves ``close``).
        horizon: forward window in bars.
        trend_thr: |forward return| threshold for the trending class.
        danger_thr: min forward return threshold for the danger class (negative).

    Returns:
        A copy of ``df`` with an integer ``regime_label`` column. The last
        ``horizon`` rows carry ``-1`` (no future to label) and should be dropped
        before training.
    """
    if "close" not in df.columns:
        raise ValueError("generate_regime_labels requires a 'close' column")
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    close = df["close"].to_numpy(dtype=np.float64)
    n = len(close)
    labels = np.full(n, _NO_LABEL, dtype=np.int64)

    for i in range(n - horizon):
        future = close[i + 1 : i + 1 + horizon]
        fwd_ret = (future[-1] - close[i]) / close[i]
        min_ret = (future.min() - close[i]) / close[i]
        if min_ret <= danger_thr:
            labels[i] = 2
        elif abs(fwd_ret) >= trend_thr:
            labels[i] = 1
        else:
            labels[i] = 0

    out = df.copy()
    out["regime_label"] = labels
    return out


def generate_regime_labels_nowcast(
    df: pd.DataFrame,
    window: int = DEFAULT_HORIZON,
    trend_thr: float = DEFAULT_TREND_THR,
    danger_thr: float = DEFAULT_DANGER_THR,
) -> pd.DataFrame:
    """Add a trailing-window (now-cast) ``regime_label`` column (0/1/2, or -1).

    At each bar T, classifies the regime over the PAST ``window`` bars ending at
    and including T — ``close[T-window+1 … T]``. Fully deterministic from past
    data (no lookahead), unlike the forward-looking :func:`generate_regime_labels`.

    * **2 (danger)** — max drawdown within the window ≤ ``danger_thr``.
    * **1 (trending)** — ``|return over window|`` ≥ ``trend_thr``.
    * **0 (ranging)** — neither.

    Danger takes precedence over trending. The first ``window-1`` bars carry
    ``-1`` (insufficient history) and should be dropped before training.

    Args:
        df: frame with a ``close`` column (typically
            ``calculate_technical_features`` output, which preserves ``close``).
        window: trailing window in bars (default 24 = 1 day of 1h bars).
        trend_thr: |window return| threshold for the trending class.
        danger_thr: max-drawdown threshold for the danger class (negative).

    Returns:
        A copy of ``df`` with an integer ``regime_label`` column.
    """
    if "close" not in df.columns:
        raise ValueError("generate_regime_labels_nowcast requires a 'close' column")
    if window <= 0:
        raise ValueError("window must be positive")

    close = df["close"].to_numpy(dtype=np.float64)
    n = len(close)
    labels = np.full(n, _NO_LABEL, dtype=np.int64)

    for T in range(window - 1, n):
        w = close[T - window + 1 : T + 1]            # `window` bars, incl. T
        running_max = np.maximum.accumulate(w)
        window_dd = float(((w - running_max) / running_max).min())   # ≤ 0
        window_ret = float((w[-1] - w[0]) / w[0])
        if window_dd <= danger_thr:
            labels[T] = 2
        elif abs(window_ret) >= trend_thr:
            labels[T] = 1
        else:
            labels[T] = 0

    out = df.copy()
    out["regime_label"] = labels
    return out
