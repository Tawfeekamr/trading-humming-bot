import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.indicators.ema import EMA
from src.indicators.rsi import RSI
from src.indicators.atr import ATR
from src.trend.candlestick_patterns import CandlestickPatterns
from src.trend.support_resistance import SupportResistance

logger = logging.getLogger(__name__)


@dataclass
class SignalScore:
    total: int = 0
    details: list[dict] = field(default_factory=list)


class TrendManager:
    def __init__(self, ema_fast: int = 20, ema_slow: int = 50, ema_trend: int = 200,
                 rsi_period: int = 14, rsi_min: float = 40, rsi_max: float = 70,
                 min_signal_score: int = 3, confirmation_ticks: int = 2,
                 sl_buffer_pct: float = 0.2, rr_ratio: float = 2.0,
                 exit_signal_threshold: int = 2) -> None:
        self._ema_fast = EMA(ema_fast)
        self._ema_slow = EMA(ema_slow)
        self._ema_trend = EMA(ema_trend)
        self._rsi = RSI(rsi_period)
        self._atr = ATR(14)
        self._patterns = CandlestickPatterns()
        self._sr = SupportResistance()

        self._rsi_min = rsi_min
        self._rsi_max = rsi_max
        self._min_signal_score = min_signal_score
        self._confirmation_ticks = confirmation_ticks
        self._sl_buffer_pct = sl_buffer_pct / 100.0
        self._rr_ratio = rr_ratio
        self._exit_signal_threshold = exit_signal_threshold

        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None
        self._pending_ticks: int = 0
        self._pending_score: Optional[SignalScore] = None

    def evaluate(self, candles: pd.DataFrame, current_price: float) -> SignalScore:
        score = SignalScore()
        closes = candles["close"]

        # 1. EMA Cross (+1)
        ema_f = self._ema_fast.calculate(closes)
        ema_s = self._ema_slow.calculate(closes)
        ema_t = self._ema_trend.calculate(closes)

        if ema_f is not None and ema_s is not None:
            if ema_f > ema_s:
                crossed = (self._prev_ema_fast is not None
                           and self._prev_ema_slow is not None
                           and self._prev_ema_fast <= self._prev_ema_slow)
                if crossed or self._prev_ema_fast is None:
                    score.total += 1
                    score.details.append({"signal": "ema_cross", "points": 1, "note": "EMA fast > slow"})
            self._prev_ema_fast = ema_f
            self._prev_ema_slow = ema_s

        # 2. Trend filter (+1)
        if ema_f is not None and ema_s is not None and ema_t is not None:
            if current_price > ema_t and ema_f > ema_s:
                score.total += 1
                score.details.append({"signal": "trend_filter", "points": 1, "note": f"price({current_price:.2f}) > EMA200({ema_t:.2f})"})

        # 3. RSI Confirmation (+1)
        rsi_val = self._rsi.calculate(closes)
        if rsi_val is not None and self._rsi_min <= rsi_val <= self._rsi_max:
            score.total += 1
            score.details.append({"signal": "rsi_filter", "points": 1, "note": f"RSI={rsi_val:.1f} in [{self._rsi_min}-{self._rsi_max}]"})

        # 4. At Support (+2)
        sr_levels = self._sr.detect(candles)
        support = self._sr.nearest_support(sr_levels, current_price)
        if support is not None:
            distance_pct = abs(current_price - support["price"]) / current_price
            if distance_pct <= 0.01:
                score.total += 2
                score.details.append({"signal": "at_support", "points": 2, "note": f"Support at {support['price']:.2f} ({distance_pct*100:.1f}% away)"})

        # 5. Bullish Candlestick Pattern (+2)
        patterns = self._patterns.detect(candles)
        if patterns:
            best = patterns[0]
            score.total += 2
            score.details.append({"signal": "candlestick", "points": 2, "note": f"Pattern: {best['name']}"})

        return score

    def should_enter(self, score: SignalScore) -> bool:
        return score.total >= self._min_signal_score

    def should_exit(self, score: SignalScore) -> bool:
        return score.total < self._exit_signal_threshold

    def confirm_entry(self, score: SignalScore) -> bool:
        if not self.should_enter(score):
            self._pending_ticks = 0
            self._pending_score = None
            return False
        if self._pending_score is not None and self._pending_score.total >= self._min_signal_score:
            self._pending_ticks += 1
        else:
            self._pending_ticks = 1
            self._pending_score = score
        if self._pending_ticks >= self._confirmation_ticks:
            self._pending_ticks = 0
            self._pending_score = None
            return True
        return False

    def calculate_stop_loss(self, entry_price: float, sr_levels: list[dict],
                            atr_value: Optional[float] = None) -> float:
        support = None
        for level in sr_levels:
            if level["type"] == "support" and level["price"] < entry_price:
                if support is None or level["price"] > support["price"]:
                    support = level
        if support is not None:
            return round(support["price"] * (1 - self._sl_buffer_pct), 2)
        if atr_value and atr_value > 0:
            return round(entry_price - 2 * atr_value, 2)
        return round(entry_price * 0.97, 2)

    def calculate_take_profit(self, entry_price: float, stop_loss: float) -> float:
        risk_distance = entry_price - stop_loss
        return round(entry_price + risk_distance * self._rr_ratio, 2)

    def reset_confirmation(self) -> None:
        self._pending_ticks = 0
        self._pending_score = None
