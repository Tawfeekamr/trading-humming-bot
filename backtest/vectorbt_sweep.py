"""
Weekly Parameter Sweep — Actionable Recommendations
Optimizes BB period, RSI thresholds, and ATR multiplier.
Compares best params against live config and outputs recommendations.

Run: python backtest/vectorbt_sweep.py --pair ETHUSDT
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from itertools import product

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.reporting import compute_benchmark, monte_carlo_simulation, BacktestResult, format_report

# Cache directory for downloaded data
DATA_CACHE_DIR = Path(__file__).parent / "data_cache"

# Current live strategy params (from config/strategy.yaml)
LIVE_PARAMS = {
    "bb_period": 20,
    "rsi_oversold": 35,
    "rsi_overbought": 70,
    "atr_multiplier": 1.5,
}


def fetch_data(symbol: str = "ETHUSDT", start: str = "2025-01-01",
               end: str = "2026-05-31") -> pd.DataFrame:
    """Fetch data with local Parquet cache — skips download if cached today."""
    DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = DATA_CACHE_DIR / f"{symbol}_{start}_{end}.parquet"

    # Use cache if less than 24h old
    if cache_file.exists():
        age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            print(f"Loading cached data for {symbol} ({age_hours:.1f}h old)")
            return pd.read_parquet(cache_file)

    try:
        import vectorbt as vbt
        print(f"Downloading {symbol} {start} → {end} (1h)...")
        df = vbt.BinanceData.download(
            symbol, start=start, end=end, interval="1h"
        ).get()
        if not df.empty:
            df.to_parquet(cache_file)
            print(f"Cached {len(df)} bars → {cache_file.name}")
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()


def run_sweep(df: pd.DataFrame):
    bb_periods = [15, 20, 25]
    rsi_oversold = [30, 35, 40]
    rsi_overbought = [65, 70, 75]
    atr_multipliers = [0.5, 0.8, 1.0, 1.5]

    results = []

    # Compute HODL benchmark
    close = df["Close"]
    benchmark = compute_benchmark(close)
    hodl_pct = benchmark.iloc[-1]
    print(f"\n=== HODL BENCHMARK: {hodl_pct:.2f}% ===")

    for bb_p, rsi_low, rsi_high, atr_m in product(
        bb_periods, rsi_oversold, rsi_overbought, atr_multipliers
    ):
        sma = close.rolling(bb_p).mean()
        std = close.rolling(bb_p).std()

        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta).where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        ema = close.ewm(span=200).mean()

        high = df["High"]
        low = df["Low"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=14).mean()

        spacing = atr * atr_m

        entries = (
            (close < sma - spacing) &
            (rsi < rsi_low) &
            (close > ema)
        )
        exits = (
            (close > sma + spacing) |
            (rsi > rsi_high) |
            (close < ema)
        )

        try:
            import vectorbt as vbt
            pf = vbt.Portfolio.from_signals(
                close=close, entries=entries, exits=exits,
                freq="1h", init_cash=200,
                fees=0.001,
                slippage=0.0005,
            )
            stats = pf.stats()
            results.append({
                "bb_period": bb_p,
                "rsi_oversold": rsi_low,
                "rsi_overbought": rsi_high,
                "atr_multiplier": atr_m,
                "total_trades": int(stats.get("Total Trades", 0)),
                "total_return_pct": float(stats.get("Total Return [%]", 0)),
                "sharpe_ratio": float(stats.get("Sharpe Ratio", 0)),
                "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0)),
                "win_rate": float(stats.get("Win Rate [%]", 0)),
            })
        except Exception:
            continue

    results_df = pd.DataFrame(results)
    if results_df.empty:
        print("No results generated.")
        return results_df, hodl_pct, {}

    results_df = results_df.sort_values("sharpe_ratio", ascending=False)
    print("\n=== TOP 5 PARAMETER COMBINATIONS ===")
    print(results_df.head(5).to_string(index=False))

    # Adaptive criteria based on market regime
    if hodl_pct < -30:
        regime = "BEAR"
        criteria = {"sharpe": 0.0, "max_dd": 35, "min_trades": 50, "min_win_rate": 40}
    elif hodl_pct < -10:
        regime = "WEAK"
        criteria = {"sharpe": 0.3, "max_dd": 25, "min_trades": 80, "min_win_rate": 42}
    elif hodl_pct < 20:
        regime = "NEUTRAL"
        criteria = {"sharpe": 0.6, "max_dd": 15, "min_trades": 100, "min_win_rate": 45}
    else:
        regime = "BULL"
        criteria = {"sharpe": 1.2, "max_dd": 8, "min_trades": 200, "min_win_rate": 48}

    passing = results_df[
        (results_df["sharpe_ratio"] > criteria["sharpe"]) &
        (results_df["total_trades"] > criteria["min_trades"]) &
        (results_df["max_drawdown_pct"] < criteria["max_dd"]) &
        (results_df["win_rate"] > criteria["min_win_rate"])
    ]

    results_df["beat_hodl_pct"] = results_df["total_return_pct"] - hodl_pct
    beat_hodl_count = int((results_df["beat_hodl_pct"] > 0).sum())

    print(f"\n=== {regime} MARKET (HODL: {hodl_pct:.1f}%) — {len(passing)}/{len(results_df)} pass, {beat_hodl_count}/{len(results_df)} beat HODL ===")

    # ── Compare against live params ──────────────────────────────
    best = results_df.iloc[0]
    recommendation = {}

    for param, live_val in LIVE_PARAMS.items():
        sweep_vals = results_df[param].unique()
        best_val = best[param]

        # Find how live params rank among all combos
        if param in ("bb_period", "rsi_oversold", "rsi_overbought", "atr_multiplier"):
            # Compare: how does the live value perform vs the best?
            live_rows = results_df[results_df[param] == live_val]
            if len(live_rows) > 0:
                live_best_sharpe = live_rows["sharpe_ratio"].max()
                delta_sharpe = best["sharpe_ratio"] - live_best_sharpe
                recommendation[param] = {
                    "live": live_val,
                    "best": float(best_val),
                    "delta_sharpe": round(delta_sharpe, 3),
                    "suggest_change": bool(abs(delta_sharpe) > 0.2 and best_val != live_val),
                }

    # Build suggestion text
    suggestions = []
    for param, info in recommendation.items():
        if info["suggest_change"]:
            suggestions.append(f"  {param}: {info['live']} → {int(info['best'])} (Δ Sharpe +{info['delta_sharpe']:.2f})")

    if suggestions:
        print(f"\n=== RECOMMENDED PARAM CHANGES vs LIVE CONFIG ===")
        for s in suggestions:
            print(s)
    else:
        print(f"\n=== LIVE PARAMS ARE OPTIMAL (no changes recommended) ===")

    return results_df, hodl_pct, recommendation


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VectorBT Parameter Sweep")
    parser.add_argument("--pair", type=str, default="ETHUSDT", help="Trading pair")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    df = fetch_data(symbol=args.pair)
    if df.empty:
        print(f"No data for {args.pair}")
        exit(1)

    results, hodl, recommendations = run_sweep(df)

    # Build output
    best = None
    beat_hodl_count = 0
    passing_count = 0
    if results is not None and not results.empty:
        best = results.loc[results['sharpe_ratio'].idxmax()]
        beat_hodl_count = int((results["total_return_pct"] > hodl).sum())

        # Regime-based passing
        if hodl < -30:
            passing_count = int(((results["sharpe_ratio"] > 0) & (results["max_drawdown_pct"] < 35) & (results["total_trades"] > 50)).sum())
        elif hodl < -10:
            passing_count = int(((results["sharpe_ratio"] > 0.3) & (results["max_drawdown_pct"] < 25) & (results["total_trades"] > 80)).sum())
        elif hodl < 20:
            passing_count = int(((results["sharpe_ratio"] > 0.6) & (results["max_drawdown_pct"] < 15) & (results["total_trades"] > 100)).sum())
        else:
            passing_count = int(((results["sharpe_ratio"] > 1.2) & (results["max_drawdown_pct"] < 8) & (results["total_trades"] > 200)).sum())

    output = {
        "pair": args.pair,
        "best_sharpe": round(float(best['sharpe_ratio']), 3) if best is not None else None,
        "best_return_pct": round(float(best['total_return_pct']), 2) if best is not None else None,
        "best_params": {
            "bb_period": int(best['bb_period']),
            "rsi_oversold": int(best['rsi_oversold']),
            "rsi_overbought": int(best['rsi_overbought']),
            "atr_multiplier": float(best['atr_multiplier']),
        } if best is not None else None,
        "total_combinations": len(results) if results is not None else 0,
        "passing_count": passing_count,
        "beat_hodl_count": beat_hodl_count,
        "hodl_return_pct": round(hodl, 2),
        "recommendations": recommendations,
    }

    output_path = args.output or f"backtest/results/{args.pair}_sweep.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nBest for {args.pair}: Sharpe={output['best_sharpe']}, Return={output['best_return_pct']}%")
    print(f"Results saved to {output_path}")
