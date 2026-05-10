import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class SupportResistance:
    """Detects support and resistance levels from candle data."""

    def __init__(self, cluster_pct: float = 0.005, min_touches: int = 2,
                 lookback: int = 50) -> None:
        self._cluster_pct = cluster_pct
        self._min_touches = min_touches
        self._lookback = lookback

    def detect(self, df: pd.DataFrame) -> list[dict]:
        if len(df) < 5:
            return []

        df = df.tail(self._lookback)
        pivots = self._find_pivots(df)
        if not pivots:
            return []

        clustered = self._cluster_pivots(pivots)
        current_price = float(df["close"].iloc[-1])

        levels = []
        for price, touches in clustered.items():
            if touches < self._min_touches:
                continue
            level_type = "support" if price <= current_price else "resistance"
            strength = min(touches / 5.0, 1.0)
            levels.append({
                "price": price,
                "type": level_type,
                "touches": touches,
                "strength": round(strength, 2),
            })

        levels.sort(key=lambda l: l["price"])
        return levels

    def nearest_support(self, levels: list[dict], price: float) -> Optional[dict]:
        supports = [l for l in levels if l["type"] == "support" and l["price"] <= price]
        if not supports:
            return None
        return max(supports, key=lambda l: l["price"])

    def nearest_resistance(self, levels: list[dict], price: float) -> Optional[dict]:
        resistances = [l for l in levels if l["type"] == "resistance" and l["price"] >= price]
        if not resistances:
            return None
        return min(resistances, key=lambda l: l["price"])

    def _find_pivots(self, df: pd.DataFrame) -> list[tuple[str, float]]:
        pivots = []
        highs = df["high"].tolist()
        lows = df["low"].tolist()

        for i in range(2, len(df) - 2):
            if highs[i] >= max(highs[i - 2:i]) and highs[i] >= max(highs[i + 1:i + 3]):
                pivots.append(("resistance", float(highs[i])))
            if lows[i] <= min(lows[i - 2:i]) and lows[i] <= min(lows[i + 1:i + 3]):
                pivots.append(("support", float(lows[i])))

        return pivots

    def _cluster_pivots(self, pivots: list[tuple[str, float]]) -> dict[float, int]:
        if not pivots:
            return {}

        sorted_pivots = sorted(pivots, key=lambda p: p[1])
        clusters: dict[float, int] = {}

        current_price = sorted_pivots[0][1]
        current_touches = 1

        for i in range(1, len(sorted_pivots)):
            price = sorted_pivots[i][1]
            threshold = current_price * self._cluster_pct

            if abs(price - current_price) <= threshold:
                current_price = (current_price * current_touches + price) / (current_touches + 1)
                current_touches += 1
            else:
                clusters[round(current_price, 4)] = current_touches
                current_price = price
                current_touches = 1

        clusters[round(current_price, 4)] = current_touches
        return clusters
