import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class CandlestickPatterns:
    """Detects bullish candlestick patterns for trend entry signals."""

    def detect(self, df: pd.DataFrame) -> list[dict]:
        if len(df) < 1:
            return []

        patterns = []
        last = self._candle(df, -1)
        if last:
            p = self._check_single(last)
            if p:
                patterns.append(p)

        if len(df) >= 2:
            prev = self._candle(df, -2)
            if prev and last:
                p = self._check_double(prev, last)
                if p:
                    patterns.append(p)

        if len(df) >= 3:
            first = self._candle(df, -3)
            mid = self._candle(df, -2)
            if first and mid and last:
                p = self._check_triple(first, mid, last)
                if p:
                    patterns.append(p)

        return patterns

    def _candle(self, df: pd.DataFrame, idx: int) -> Optional[dict]:
        try:
            row = df.iloc[idx]
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        except (IndexError, KeyError, ValueError):
            return None

        body = abs(c - o)
        total_range = h - l
        if total_range == 0:
            return None

        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        is_green = c > o
        is_red = c < o

        return {
            "open": o, "high": h, "low": l, "close": c,
            "body": body, "range": total_range,
            "upper_shadow": upper_shadow, "lower_shadow": lower_shadow,
            "is_green": is_green, "is_red": is_red,
        }

    def _bullish_result(self, name: str) -> dict:
        return {"name": name, "type": "bullish", "signal": "bull", "score": 2}

    def _check_single(self, c: dict) -> Optional[dict]:
        if (c["lower_shadow"] >= 2 * c["body"]
                and c["upper_shadow"] <= c["body"] * 0.5
                and c["body"] > 0):
            return self._bullish_result("hammer")

        if (c["is_green"]
                and c["body"] > 0
                and c["upper_shadow"] < c["body"] * 0.1
                and c["lower_shadow"] < c["body"] * 0.1):
            return self._bullish_result("bullish_marubozu")

        return None

    def _check_double(self, prev: dict, curr: dict) -> Optional[dict]:
        if (prev["is_red"]
                and curr["is_green"]
                and curr["open"] <= prev["close"]
                and curr["close"] >= prev["open"]
                and curr["body"] > prev["body"]):
            return self._bullish_result("bullish_engulfing")

        if (prev["is_red"]
                and curr["is_green"]
                and curr["open"] > prev["close"]
                and curr["close"] < prev["open"]
                and curr["body"] < prev["body"] * 0.5):
            return self._bullish_result("bullish_harami")

        return None

    def _check_triple(self, first: dict, mid: dict, last: dict) -> Optional[dict]:
        first_is_large_red = first["is_red"] and first["body"] > mid["body"] * 2
        mid_is_small = mid["body"] < first["body"] * 0.5
        last_is_large_green = last["is_green"] and last["body"] > mid["body"] * 2

        if first_is_large_red and mid_is_small and last_is_large_green:
            return self._bullish_result("morning_star")

        return None
