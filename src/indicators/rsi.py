# src/indicators/rsi.py
import pandas as pd


class RSI:
    def __init__(self, period: int = 14):
        self.period = period

    def calculate(self, closes: pd.Series) -> float | None:
        if len(closes) < self.period + 1:
            return None
        delta = closes.diff().iloc[-(self.period + 1):]
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.iloc[1:].mean()
        avg_loss = loss.iloc[1:].mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
