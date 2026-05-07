# src/indicators/bollinger.py
import pandas as pd
from dataclasses import dataclass


@dataclass
class BBResult:
    upper: float
    mid: float
    lower: float


class BollingerBands:
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        self.period = period
        self.std_dev = std_dev

    def calculate(self, closes: pd.Series) -> BBResult | None:
        if len(closes) < self.period:
            return None
        window = closes.iloc[-self.period:]
        mid = window.mean()
        std = window.std()
        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std
        return BBResult(upper=upper, mid=mid, lower=lower)
