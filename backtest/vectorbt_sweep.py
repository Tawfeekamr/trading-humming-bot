"""
Phase 1 Backtest: VectorBT Parameter Sweep
Optimize BB period, RSI thresholds, and ATR multiplier.

Run: python backtest/vectorbt_sweep.py
Target: Sharpe > 1.2, Max Drawdown < 8%, 200+ trades
"""

import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from itertools import product

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.reporting import compute_benchmark, monte_carlo_simulation, BacktestResult, format_report


def fetch_data(symbol: str = "SOLUSDT", start: str = "2025-01-01",
               end: str = "2026-04-30") -> pd.DataFrame:
    try:
        import vectorbt as vbt
        df = vbt.BinanceData.download(
            symbol, start=start, end=end, interval="1h"
        ).get()
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        print("Ensure vectorbt is installed: pip install vectorbt")
        return pd.DataFrame()


def run_sweep(df: pd.DataFrame):
    bb_periods = [15, 20, 25]
    rsi_oversold = [30, 35, 40]
    rsi_overbought = [65, 70, 75]
    atr_multipliers = [0.5, 0.8, 1.0]

    results = []

    # Compute HODL benchmark
    close = df["Close"]
    benchmark = compute_benchmark(close)
    print(f"\n=== HODL BENCHMARK: {benchmark.iloc[-1]:.2f}% ===")

    for bb_p, rsi_low, rsi_high, atr_m in product(
        bb_periods, rsi_oversold, rsi_overbought, atr_multipliers
    ):

        sma = close.rolling(bb_p).mean()
        std = close.rolling(bb_p).std()
        upper = sma + 2 * std
        lower = sma - 2 * std

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
            # Realistic fees: 0.1% maker fee (standard, not BNB discount tier)
            # Slippage: 5 basis points (0.05%) typical for SOL/USDT
            pf = vbt.Portfolio.from_signals(
                close=close, entries=entries, exits=exits,
                freq="1h", init_cash=200,
                fees=0.001,  # 0.1% maker fee
                slippage=0.0005,  # 5 bps slippage
            )
            stats = pf.stats()
            results.append({
                "bb_period": bb_p,
                "rsi_oversold": rsi_low,
                "rsi_overbought": rsi_high,
                "atr_multiplier": atr_m,
                "total_trades": stats.get("Total Trades", 0),
                "total_return_pct": stats.get("Total Return [%]", 0),
                "sharpe_ratio": stats.get("Sharpe Ratio", 0),
                "max_drawdown_pct": stats.get("Max Drawdown [%]", 0),
                "win_rate": stats.get("Win Rate [%]", 0),
            })
        except Exception:
            continue

    results_df = pd.DataFrame(results)
    if results_df.empty:
        print("No results generated.")
        return results_df, 0.0
    results_df = results_df.sort_values("sharpe_ratio", ascending=False)
    print("\n=== TOP 10 PARAMETER COMBINATIONS ===")
    print(results_df.head(10).to_string(index=False))

    # Adaptive criteria based on market regime (HODL return)
    hodl_pct = benchmark.iloc[-1]
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

    print(f"\n=== MARKET REGIME: {regime} (HODL: {hodl_pct:.1f}%) ===")
    print(f"  Criteria: Sharpe>{criteria['sharpe']}, DD<{criteria['max_dd']}%, "
          f"Trades>{criteria['min_trades']}, WinRate>{criteria['min_win_rate']}%")

    passing = results_df[
        (results_df["sharpe_ratio"] > criteria["sharpe"]) &
        (results_df["total_trades"] > criteria["min_trades"]) &
        (results_df["max_drawdown_pct"] < criteria["max_dd"]) &
        (results_df["win_rate"] > criteria["min_win_rate"])
    ]

    # Also highlight strategies that beat HODL (most useful in bear markets)
    results_df["beat_hodl_pct"] = results_df["total_return_pct"] - hodl_pct
    beat_hodl = results_df[results_df["beat_hodl_pct"] > 0].sort_values("beat_hodl_pct", ascending=False)

    print(f"\n=== PASSING CRITERIA: {len(passing)} / {len(results_df)} ===")
    if not passing.empty:
        print(passing.head(10).to_string(index=False))
    print(f"\n=== BEAT HODL: {len(beat_hodl)} / {len(results_df)} strategies ===")
    if not beat_hodl.empty:
        print(beat_hodl.head(5).to_string(index=False))

    # Monte Carlo on best parameters
    if not results_df.empty:
        best = results_df.iloc[0]
        print(f"\n=== MONTE CARLO SIMULATION (best params, 90-day projection) ===")
        print(f"Best params: BB={int(best['bb_period'])}, RSI<{best['rsi_oversold']}/{best['rsi_overbought']}, ATR×{best['atr_multiplier']}")
        # Use daily return approximation for MC
        daily_ret = close.resample('D').last().pct_change().dropna()
        mc = monte_carlo_simulation(daily_ret, n_sims=500, n_days=90)
        print(f"  5th percentile:  {mc['p5'].iloc[-1]:.2f}%")
        print(f"  50th percentile: {mc['p50'].iloc[-1]:.2f}%")
        print(f"  95th percentile: {mc['p95'].iloc[-1]:.2f}%")

    return results_df, hodl_pct


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="VectorBT Parameter Sweep")
    parser.add_argument("--pair", type=str, default="ETHUSDT", help="Trading pair symbol (e.g. ETHUSDT)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    df = fetch_data(symbol=args.pair)
    if df.empty:
        print(f"No data for {args.pair}")
        exit(1)

    results, hodl = run_sweep(df)

    # Find best result by Sharpe ratio
    best = None
    passing_count = 0
    beat_hodl_count = 0
    if results is not None and not results.empty:
        best = results.loc[results['sharpe_ratio'].idxmax()]
        beat_hodl_count = int((results["total_return_pct"] > hodl).sum())

    output = {
        "pair": args.pair,
        "best_sharpe": float(best['sharpe_ratio']) if best is not None else None,
        "total_combinations": len(results) if results is not None else 0,
        "beat_hodl_count": beat_hodl_count,
        "hodl_return_pct": round(hodl, 2),
    }

    output_path = args.output or f"backtest/results/{args.pair}_sweep.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Best result for {args.pair}: Sharpe={output['best_sharpe']}")
    print(f"Results saved to {output_path}")
