"""
Phase 1 Backtest: VectorBT Parameter Sweep
Optimize BB period, RSI thresholds, and ATR multiplier.

Run: python backtest/vectorbt_sweep.py
Target: Sharpe > 1.2, Max Drawdown < 8%, 200+ trades
"""

import os
import pandas as pd
import numpy as np
from itertools import product


def fetch_data(symbol: str = "BTCUSDT", start: str = "2025-01-01",
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

    for bb_p, rsi_low, rsi_high, atr_m in product(
        bb_periods, rsi_oversold, rsi_overbought, atr_multipliers
    ):
        close = df["Close"]

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
            pf = vbt.Portfolio.from_signals(
                close=close, entries=entries, exits=exits,
                freq="1h", init_cash=200, fees=0.00075,
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
        return results_df
    results_df = results_df.sort_values("sharpe_ratio", ascending=False)
    print("\n=== TOP 10 PARAMETER COMBINATIONS ===")
    print(results_df.head(10).to_string(index=False))

    passing = results_df[
        (results_df["sharpe_ratio"] > 1.2) &
        (results_df["total_trades"] > 200) &
        (results_df["max_drawdown_pct"] < 8)
    ]
    print(f"\n=== PASSING CRITERIA: {len(passing)} / {len(results_df)} ===")
    if not passing.empty:
        print(passing.to_string(index=False))
    return results_df


if __name__ == "__main__":
    print("Fetching BTC/USDT 1h data...")
    df = fetch_data()
    print(f"Data shape: {df.shape}")
    results = run_sweep(df)
    os.makedirs("reports", exist_ok=True)
    results.to_csv("reports/parameter_sweep_results.csv", index=False)
    print("\nResults saved to reports/parameter_sweep_results.csv")
