# src/indicators/ema.py
import math
import pandas as pd


class EMA:
    def __init__(self, period: int = 200):
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period

    def calculate(self, closes: pd.Series) -> float | None:
        if len(closes) < self.period:
            return None

        # Check for NaN in input series
        if closes.isna().any():
            return None

        result = float(closes.ewm(span=self.period, adjust=False).mean().iloc[-1])

        # Validate result is finite
        if not math.isfinite(result):
            return None

        return result
