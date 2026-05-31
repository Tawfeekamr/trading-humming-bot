"""
Weekly Parameter Sweep — Grid + Trend, Actionable Recommendations
Sweeps grid (BB/RSI/ATR) and trend (EMA/RSI) parameters.
Compares best params against live config and writes Telegram summary.

Run: python backtest/vectorbt_sweep.py --pair ETHUSDT
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from itertools import product

sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.reporting import compute_benchmark, monte_carlo_simulation, BacktestResult, format_report

DATA_CACHE_DIR = Path(__file__).parent / "data_cache"

# Current live strategy params (from config/strategy.yaml)
LIVE_GRID_PARAMS = {
    "bb_period": 20,
    "rsi_oversold": 35,
    "rsi_overbought": 70,
    "atr_multiplier": 1.5,
}
LIVE_TREND_PARAMS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "rsi_min": 40,
    "rsi_max": 70,
}


def fetch_data(symbol: str = "ETHUSDT", start: str = "2025-01-01",
               end: str = "2026-05-31") -> pd.DataFrame:
    """Fetch data with local Parquet cache (24h TTL)."""
    DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = DATA_CACHE_DIR / f"{symbol}_{start}_{end}.parquet"

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


def compute_indicators(close, bb_period, rsi_oversold, rsi_overbought, atr_mult,
                       ema_fast, ema_slow, ema_trend_span=200):
    """Compute all indicators needed by both strategies."""
    sma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()

    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    ema200 = close.ewm(span=ema_trend_span).mean()
    ema_f = close.ewm(span=ema_fast).mean()
    ema_s = close.ewm(span=ema_slow).mean()

    return sma, std, rsi, ema200, ema_f, ema_s


def run_grid_sweep(df: pd.DataFrame):
    """Sweep grid strategy parameters: BB/RSI/ATR."""
    bb_periods = [15, 20, 25]
    rsi_oversold = [30, 35, 40]
    rsi_overbought = [65, 70, 75]
    atr_multipliers = [0.5, 0.8, 1.0, 1.5]

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14).mean()

    results = []
    for bb_p, rsi_low, rsi_high, atr_m in product(
        bb_periods, rsi_oversold, rsi_overbought, atr_multipliers
    ):
        sma = close.rolling(bb_p).mean()
        std = close.rolling(bb_p).std()
        spacing = atr * atr_m

        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta).where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        ema = close.ewm(span=200).mean()

        entries = (close < sma - spacing) & (rsi < rsi_low) & (close > ema)
        exits = (close > sma + spacing) | (rsi > rsi_high) | (close < ema)

        try:
            import vectorbt as vbt
            pf = vbt.Portfolio.from_signals(
                close=close, entries=entries, exits=exits,
                freq="1h", init_cash=200, fees=0.001, slippage=0.0005,
            )
            stats = pf.stats()
            results.append({
                "strategy": "grid",
                "bb_period": bb_p, "rsi_oversold": rsi_low,
                "rsi_overbought": rsi_high, "atr_multiplier": atr_m,
                "total_trades": int(stats.get("Total Trades", 0)),
                "total_return_pct": float(stats.get("Total Return [%]", 0)),
                "sharpe_ratio": float(stats.get("Sharpe Ratio", 0)),
                "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0)),
                "win_rate": float(stats.get("Win Rate [%]", 0)),
            })
        except Exception:
            continue

    return pd.DataFrame(results)


def run_trend_sweep(df: pd.DataFrame):
    """Sweep trend strategy parameters: EMA fast/slow + RSI bounds."""
    ema_fast_vals = [12, 20, 30]
    ema_slow_vals = [40, 50, 60]
    rsi_min_vals = [30, 40, 50]
    rsi_max_vals = [60, 70, 80]

    close = df["Close"]

    results = []
    for ema_f, ema_s, rsi_min, rsi_max in product(
        ema_fast_vals, ema_slow_vals, rsi_min_vals, rsi_max_vals
    ):
        if ema_f >= ema_s:
            continue

        ema_fast = close.ewm(span=ema_f).mean()
        ema_slow = close.ewm(span=ema_s).mean()
        ema_trend = close.ewm(span=200).mean()

        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta).where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # Long: EMA fast crosses above EMA slow, RSI in range, above trend
        entries = (
            (ema_fast > ema_slow) &
            (ema_fast.shift(1) <= ema_slow.shift(1)) &
            (rsi > rsi_min) & (rsi < rsi_max) &
            (close > ema_trend)
        )
        # Exit: EMA fast crosses below slow, or RSI overbought, or below trend
        exits = (
            ((ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))) |
            (rsi > rsi_max + 10) |
            (close < ema_trend)
        )

        try:
            import vectorbt as vbt
            pf = vbt.Portfolio.from_signals(
                close=close, entries=entries, exits=exits,
                freq="1h", init_cash=200, fees=0.001, slippage=0.0005,
            )
            stats = pf.stats()
            results.append({
                "strategy": "trend",
                "ema_fast": ema_f, "ema_slow": ema_s,
                "rsi_min": rsi_min, "rsi_max": rsi_max,
                "total_trades": int(stats.get("Total Trades", 0)),
                "total_return_pct": float(stats.get("Total Return [%]", 0)),
                "sharpe_ratio": float(stats.get("Sharpe Ratio", 0)),
                "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0)),
                "win_rate": float(stats.get("Win Rate [%]", 0)),
            })
        except Exception:
            continue

    return pd.DataFrame(results)


def build_telegram_summary(pair: str, grid_best, trend_best, hodl_pct,
                           grid_beat, grid_total, trend_beat, trend_total,
                           grid_suggestions, trend_suggestions):
    """Build a pre-formatted Telegram HTML message."""
    pair_display = pair.replace("USDT", "/USDT")

    if hodl_pct < -30:
        regime_emoji = "🐻"
    elif hodl_pct < -10:
        regime_emoji = "📉"
    elif hodl_pct < 20:
        regime_emoji = "↔️"
    else:
        regime_emoji = "🚀"

    lines = [
        f"📊 <b>Sweep: {pair_display}</b>",
        f"{regime_emoji} HODL: {hodl_pct:+.0f}%",
        "•••",
    ]

    # Grid
    if grid_best is not None:
        g_sharpe = grid_best['sharpe_ratio']
        g_return = grid_best['total_return_pct']
        g_emoji = "✅" if grid_beat > grid_total // 2 else "⚠️"
        lines.append(f"🤖 <b>Grid:</b> Sharpe={g_sharpe:.2f} | Return={g_return:+.1f}% | Beat HODL: {grid_beat}/{grid_total}")
        for s in grid_suggestions:
            lines.append(f"  🔧 {s}")

    # Trend
    if trend_best is not None:
        t_sharpe = trend_best['sharpe_ratio']
        t_return = trend_best['total_return_pct']
        t_emoji = "✅" if trend_beat > trend_total // 2 else "⚠️"
        lines.append(f"📈 <b>Trend:</b> Sharpe={t_sharpe:.2f} | Return={t_return:+.1f}% | Beat HODL: {trend_beat}/{trend_total}")
        for s in trend_suggestions:
            lines.append(f"  🔧 {s}")

    if not grid_suggestions and not trend_suggestions:
        lines.append("✅ Live params optimal — no changes needed")

    return "\n".join(lines)


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

    close = df["Close"]
    hodl_pct = float(compute_benchmark(close).iloc[-1])
    print(f"\n=== HODL BENCHMARK: {hodl_pct:.2f}% ===")

    # ── Run both sweeps ──────────────────────────────────────────
    print("\n" + "="*50)
    print("GRID SWEEP")
    print("="*50)
    grid_results = run_grid_sweep(df)
    print(f"Grid: {len(grid_results)} combinations tested")

    print("\n" + "="*50)
    print("TREND SWEEP")
    print("="*50)
    trend_results = run_trend_sweep(df)
    print(f"Trend: {len(trend_results)} combinations tested")

    # ── Analyze results ──────────────────────────────────────────
    grid_best = None
    grid_beat = 0
    grid_suggestions = []
    grid_recommendations = {}

    if not grid_results.empty:
        grid_results = grid_results.sort_values("sharpe_ratio", ascending=False)
        grid_best = grid_results.iloc[0]
        grid_beat = int((grid_results["total_return_pct"] > hodl_pct).sum())
        print(f"\n=== GRID TOP 3 ===")
        print(grid_results.head(3).to_string(index=False))

        for param, live_val in LIVE_GRID_PARAMS.items():
            live_rows = grid_results[grid_results[param] == live_val]
            if len(live_rows) > 0:
                live_sharpe = float(live_rows["sharpe_ratio"].max())
                best_val = float(grid_best[param])
                delta = float(grid_best["sharpe_ratio"]) - live_sharpe
                grid_recommendations[param] = {
                    "live": live_val, "best": best_val,
                    "delta_sharpe": round(delta, 3),
                    "suggest_change": bool(abs(delta) > 0.2 and best_val != live_val),
                }
                if grid_recommendations[param]["suggest_change"]:
                    grid_suggestions.append(f"{param}: {live_val} → {int(best_val)} (Δ +{delta:.2f})")

        if grid_suggestions:
            print(f"\n=== GRID: RECOMMEND CHANGES ===")
            for s in grid_suggestions:
                print(f"  {s}")
        else:
            print(f"\n=== GRID: Live params optimal ===")

    trend_best = None
    trend_beat = 0
    trend_suggestions = []
    trend_recommendations = {}

    if not trend_results.empty:
        trend_results = trend_results.sort_values("sharpe_ratio", ascending=False)
        trend_best = trend_results.iloc[0]
        trend_beat = int((trend_results["total_return_pct"] > hodl_pct).sum())
        print(f"\n=== TREND TOP 3 ===")
        print(trend_results.head(3).to_string(index=False))

        for param, live_val in LIVE_TREND_PARAMS.items():
            live_rows = trend_results[trend_results[param] == live_val]
            if len(live_rows) > 0:
                live_sharpe = float(live_rows["sharpe_ratio"].max())
                best_val = float(trend_best[param])
                delta = float(trend_best["sharpe_ratio"]) - live_sharpe
                trend_recommendations[param] = {
                    "live": live_val, "best": best_val,
                    "delta_sharpe": round(delta, 3),
                    "suggest_change": bool(abs(delta) > 0.2 and best_val != live_val),
                }
                if trend_recommendations[param]["suggest_change"]:
                    trend_suggestions.append(f"{param}: {live_val} → {int(best_val)} (Δ +{delta:.2f})")

        if trend_suggestions:
            print(f"\n=== TREND: RECOMMEND CHANGES ===")
            for s in trend_suggestions:
                print(f"  {s}")
        else:
            print(f"\n=== TREND: Live params optimal ===")

    # ── Write JSON output ────────────────────────────────────────
    output = {
        "pair": args.pair,
        "hodl_return_pct": round(hodl_pct, 2),
        "grid": {
            "best_sharpe": round(float(grid_best['sharpe_ratio']), 3) if grid_best is not None else None,
            "best_return_pct": round(float(grid_best['total_return_pct']), 2) if grid_best is not None else None,
            "best_params": {k: int(grid_best[k]) if k != 'atr_multiplier' else float(grid_best[k])
                           for k in ['bb_period', 'rsi_oversold', 'rsi_overbought', 'atr_multiplier']
                           } if grid_best is not None else None,
            "total_combinations": len(grid_results),
            "beat_hodl_count": grid_beat,
            "recommendations": grid_recommendations,
        },
        "trend": {
            "best_sharpe": round(float(trend_best['sharpe_ratio']), 3) if trend_best is not None else None,
            "best_return_pct": round(float(trend_best['total_return_pct']), 2) if trend_best is not None else None,
            "best_params": {k: int(trend_best[k])
                           for k in ['ema_fast', 'ema_slow', 'rsi_min', 'rsi_max']
                           } if trend_best is not None else None,
            "total_combinations": len(trend_results),
            "beat_hodl_count": trend_beat,
            "recommendations": trend_recommendations,
        },
    }

    output_path = args.output or f"backtest/results/{args.pair}_sweep.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {output_path}")

    # ── Write Telegram summary file ─────────────────────────────
    tg_msg = build_telegram_summary(
        args.pair, grid_best, trend_best, hodl_pct,
        grid_beat, len(grid_results),
        trend_beat, len(trend_results),
        grid_suggestions, trend_suggestions,
    )
    tg_path = Path(output_path).parent / f"{args.pair}_telegram.txt"
    with open(tg_path, "w") as f:
        f.write(tg_msg)
    print(f"Telegram summary → {tg_path}")
