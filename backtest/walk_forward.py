"""
Phase 2 Backtest: Walk-Forward Out-of-Sample Validation
Test best parameters from sweep on unseen data periods.

Run: python backtest/walk_forward.py
Target: Consistent results across bull/bear/sideways
"""

import os
import pandas as pd


def fetch_data(symbol: str = "BTCUSDT", start: str = "2024-01-01",
               end: str = "2026-04-30") -> pd.DataFrame:
    try:
        import vectorbt as vbt
        df = vbt.BinanceData.download(
            symbol, start=start, end=end, interval="1h"
        ).get()
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()


def apply_strategy(df: pd.DataFrame, bb_period: int = 20,
                   rsi_oversold: float = 35, rsi_overbought: float = 70,
                   atr_multiplier: float = 0.8):
    import vectorbt as vbt
    close = df["Close"]
    sma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    ema = close.ewm(span=200).mean()
    high, low = df["High"], df["Low"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14).mean()
    spacing = atr * atr_multiplier

    entries = (close < sma - spacing) & (rsi < rsi_oversold) & (close > ema)
    exits = (close > sma + spacing) | (rsi > rsi_overbought) | (close < ema)

    return vbt.Portfolio.from_signals(
        close=close, entries=entries, exits=exits,
        freq="1h", init_cash=200, fees=0.00075,
    )


def walk_forward_test(df: pd.DataFrame, train_months: int = 6,
                      test_months: int = 3):
    df.index = pd.to_datetime(df.index)
    start = df.index[0]
    end = df.index[-1]

    results = []
    train_delta = pd.DateOffset(months=train_months)
    test_delta = pd.DateOffset(months=test_months)

    current = start
    while current + train_delta + test_delta <= end:
        train_end = current + train_delta
        test_end = train_end + test_delta

        test_df = df.loc[train_end:test_end]
        if len(test_df) < 100:
            current = train_end
            continue

        pf = apply_strategy(test_df)
        stats = pf.stats()

        results.append({
            "train_period": f"{current.date()} -> {train_end.date()}",
            "test_period": f"{train_end.date()} -> {test_end.date()}",
            "total_return": stats.get("Total Return [%]", 0),
            "sharpe": stats.get("Sharpe Ratio", 0),
            "max_drawdown": stats.get("Max Drawdown [%]", 0),
            "trades": stats.get("Total Trades", 0),
            "win_rate": stats.get("Win Rate [%]", 0),
        })
        current = train_end

    results_df = pd.DataFrame(results)
    print("\n=== WALK-FORWARD RESULTS ===")
    print(results_df.to_string(index=False))
    print(f"\nAverage Return: {results_df['total_return'].mean():.2f}%")
    print(f"Average Sharpe: {results_df['sharpe'].mean():.2f}")
    print(f"Worst Drawdown: {results_df['max_drawdown'].min():.2f}%")
    return results_df


if __name__ == "__main__":
    print("Fetching BTC/USDT historical data...")
    df = fetch_data()
    print(f"Data: {df.shape[0]} rows from {df.index[0]} to {df.index[-1]}")
    results = walk_forward_test(df)
    os.makedirs("reports", exist_ok=True)
    results.to_csv("reports/walk_forward_results.csv", index=False)
    print("\nResults saved to reports/walk_forward_results.csv")
