# src/indicators/bollinger.py
import math
import pandas as pd
from dataclasses import dataclass


@dataclass
class BBResult:
    upper: float
    mid: float
    lower: float


class BollingerBands:
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        if std_dev <= 0:
            raise ValueError(f"std_dev must be positive, got {std_dev}")
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

        # Validate all values are finite
        if not (math.isfinite(mid) and math.isfinite(std) and
                math.isfinite(upper) and math.isfinite(lower)):
            return None

        # Zero std means no volatility - invalid for grid trading
        if std == 0 or upper == lower:
            return None

        return BBResult(upper=upper, mid=mid, lower=lower)
