# backtest/mean_reversion/backtest.py
"""vectorbt sweep engine: SL/TP exits, IS/OOS split, metrics, report, CLI."""
from pathlib import Path

import pandas as pd

from .features import compute_features
from .strategy import entry_signal

# Spec §4 grid.
DROP_THRS = [0.03, 0.04, 0.05, 0.06, 0.08]
TP_STOPS = [0.01, 0.015, 0.02, 0.03, 0.04]
STOP_STOPS = [0.02, 0.03, 0.04, 0.05, 0.06]
BASE_SIZES = [50, 100, 200, 500]

# Deployed live config (headline reference).
LIVE_CONFIG = {"drop_thr": 0.05, "tp": 0.02, "stop": 0.04, "base_size": 100}

INIT_CASH = 1000.0
FEES = 0.001        # 0.1% per side (matches paper FEE_RATE)
SLIPPAGE = 0.0005   # 5 bps (matches existing sweep)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "mean_reversion"


def run_single(bars: pd.DataFrame, features: pd.DataFrame, drop_thr: float,
               tp: float, stop: float, base_size: float, bar: str = "1s"):
    """Run one config. Returns a metrics dict, or None if no entries."""
    import vectorbt as vbt

    entries = entry_signal(features, drop_thr)
    if not entries.any():
        return None

    # Flat $ sizing per trade (avoids per-bar Series size; base_size sweep stays
    # meaningful as account-% risk vs INIT_CASH).
    pf = vbt.Portfolio.from_signals(
        close=bars["close"],
        entries=entries,
        sl_stop=stop,
        tp_stop=tp,
        size=base_size,
        size_type="value",
        init_cash=INIT_CASH,
        fees=FEES,
        slippage=SLIPPAGE,
        freq=bar,
    )
    stats = pf.stats()
    return {
        "drop_thr": drop_thr, "tp": tp, "stop": stop, "base_size": base_size,
        "total_trades": int(stats.get("Total Trades", 0)),
        "total_return_pct": float(stats.get("Total Return [%]", 0.0)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", 0.0)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0.0)),
        "win_rate": float(stats.get("Win Rate [%]", 0.0)),
    }
