# src/indicators/ema.py
import pandas as pd


class EMA:
    def __init__(self, period: int = 200):
        self.period = period

    def calculate(self, closes: pd.Series) -> float | None:
        if len(closes) < self.period:
            return None
        return float(closes.ewm(span=self.period, adjust=False).mean().iloc[-1])
