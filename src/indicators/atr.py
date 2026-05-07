# src/indicators/atr.py
import pandas as pd


class ATR:
    def __init__(self, period: int = 14, spacing_multiplier: float = 0.8):
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
        return float(atr_series.iloc[-1])

    def grid_spacing(self, atr_value: float) -> float:
        return atr_value * self.spacing_multiplier
