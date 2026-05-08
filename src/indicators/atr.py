# src/indicators/atr.py
import math
import pandas as pd


class ATR:
    def __init__(self, period: int = 14, spacing_multiplier: float = 0.8):
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        if spacing_multiplier <= 0:
            raise ValueError(f"spacing_multiplier must be positive, got {spacing_multiplier}")
        self.period = period
        self.spacing_multiplier = spacing_multiplier

    def calculate(self, highs: pd.Series, lows: pd.Series, closes: pd.Series) -> float | None:
        if len(closes) < self.period + 1:
            return None
        prev_close = closes.shift(1)
        tr1 = highs - lows
        tr2 = (highs - prev_close).abs()
        tr3 = (lows - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = true_range.ewm(span=self.period, adjust=False).mean()
        result = float(atr_series.iloc[-1])

        # Validate result is finite and positive
        if not math.isfinite(result) or result <= 0:
            return None

        return result

    def grid_spacing(self, atr_value: float) -> float:
        return atr_value * self.spacing_multiplier
