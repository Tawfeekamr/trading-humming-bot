# src/indicators/rsi.py
import math
import pandas as pd


class RSI:
    def __init__(self, period: int = 14):
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period

    def calculate(self, closes: pd.Series) -> float | None:
        if len(closes) < self.period + 1:
            return None

        delta = closes.diff().dropna()
        gains = delta.where(delta > 0, 0.0)
        losses = (-delta).where(delta < 0, 0.0)

        # Wilder's smoothing: first average is SMA, then EMA with alpha=1/period
        avg_gain = gains.iloc[:self.period].mean()
        avg_loss = losses.iloc[:self.period].mean()

        for i in range(self.period, len(gains)):
            avg_gain = (avg_gain * (self.period - 1) + gains.iloc[i]) / self.period
            avg_loss = (avg_loss * (self.period - 1) + losses.iloc[i]) / self.period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        result = 100.0 - (100.0 / (1.0 + rs))

        # Validate result is finite
        if not math.isfinite(result):
            return None

        return result
