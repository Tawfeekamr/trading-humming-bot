"""
signal_parser.py — Parses trader Telegram messages into structured signals using DeepSeek.

Uses DeepSeek-Chat (V4) for fast structured extraction.
HTTP calls via http.client (no external library needed).
"""

import http.client
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SignalAction(Enum):
    OPEN_LONG = "OPEN_LONG"
    CLOSE = "CLOSE"
    UPDATE_SL = "UPDATE_SL"
    UPDATE_TP = "UPDATE_TP"
    NOT_A_SIGNAL = "NOT_A_SIGNAL"


class SignalConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ParsedSignal:
    action: SignalAction
    pair: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profits: list[float] = field(default_factory=list)
    confidence: SignalConfidence = SignalConfidence.MEDIUM
    raw_message: str = ""
    parse_reasoning: str = ""
    quality_score: int = 5
    quality_reason: str = ""
    is_market_entry: bool = False
    # DeepSeek entry-tuning salvage (entry-zone case only). When a live_price is
    # supplied and price has moved outside the entry zone, DeepSeek decides in the
    # same parse call: entry_tuned=True → entry shifted to the live price, original
    # SL/TPs kept; stale=True → the move already happened, skip.
    entry_tuned: bool = False
    stale: bool = False


# Pre-scan a message for its ticker so we can fetch a live price BEFORE the
# (single) DeepSeek call. Best-effort: DeepSeek reconciles the final pair; this
# only provides a price hint. Returns "BASE-USDT" or None.
_PAIR_RE = re.compile(r"\$?\b([A-Z][A-Z0-9]{1,9})\b\s*[/\-]?\s*(?:USDT|USD|USDC)\b")
_BARE_TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9]{1,9})\b")


def _extract_candidate_pair(text: str) -> Optional[str]:
    m = _PAIR_RE.search(text)
    if m:
        return f"{m.group(1)}-USDT"
    m = _BARE_TICKER_RE.search(text)
    if m:
        return f"{m.group(1)}-USDT"
    return None


SYSTEM_PROMPT = """You are a trading signal parser and quality scorer. Extract structured trade information from Telegram messages and score signal quality.

RULES:
1. Only extract ACTIONABLE trading signals. General market commentary, motivation posts, questions, or charts without clear entry/exit are NOT signals.
2. We trade SPOT only. Ignore any leverage mentions (e.g. "2-5x", "10x") — still extract the signal. Only reject if the direction is explicitly SHORT/SELL as an opening position.
3. Normalize all pairs to format: "BTC-USDT", "ETH-USDT", etc. Add -USDT suffix if missing.
4. If the trader gives a price range for entry (e.g., "95-96k"), extract both entry_low and entry_high.
5. If only one entry price is given, set entry_low = entry_high.
6. Take-profit targets should be sorted ascending (lowest first).
7. If the message says "close", "exit", "take profit", "out", "book" for a specific pair, the action is CLOSE.
8. If the message updates stop-loss only (e.g., "move SL to entry"), the action is UPDATE_SL.
9. If no stop-loss is given for a new position, set stop_loss to null.
10. Convert shorthand: "95k" = 95000, "0.5" stays 0.5, "$100" = 100.0
11. If the message is just market commentary, analysis, or chat with no specific entry/exit, action is NOT_A_SIGNAL.
12. STOP-LOSS ADJUSTMENT: If the signal's SL is unreasonable (more than 30% below entry for LONG, or more than 30% above entry for SHORT), adjust it to a sensible level:
    - For LONG: set SL to entry_price * 0.85 (15% below entry) as a maximum
    - For SHORT: set SL to entry_price * 1.15 (15% above entry) as a maximum
    - Always keep the original SL in reasoning: "Original SL $X adjusted to $Y (too far from entry)"
    - Reduce quality_score by 2 points for signals requiring SL adjustment
    - This ensures we capture the trade instead of rejecting it, while managing risk

CRITICAL — Distinguishing NEW ENTRY signals from RESULT UPDATES:
A message is a NEW ENTRY signal (action: OPEN_LONG) if it has an ENTRY price/zone and TARGETS without checkmarks (✅).
A message is a RESULT UPDATE (action: NOT_A_SIGNAL) if targets have ✅ checkmarks, show "X% Profit", or say "Loss" — these report past results, not new trades.
A message that has ENTRY + TARGETS + STOP LOSS but NO checkmarks (✅) is ALWAYS a new entry signal, regardless of whether the same signal ID appeared in earlier result updates.

Examples:
- "ENTRY: 56.80 - 57.00 | TARGETS: 59.50 - 62.00 - 65.00 | STOP LOSS: 52.00" → OPEN_LONG (new entry)
- "Target 1: 59.50✅ | Target 2: 62.00✅ | 🔥70.2% Profit🔥" → NOT_A_SIGNAL (result update)
- "ENTRY: 0.2805 - 0.2825 | TARGETS: 0.2950 - 0.3075 | STOP LOSS: 0.2550" → OPEN_LONG (new entry)
- "STOP LOSS: 0.0650 | 🚫19.4% Loss🚫" → NOT_A_SIGNAL (past result)
- "📍SIGNAL ID: #2144📍 COIN: $HYPE/USDT ENTRY: 56.80-57.00 TARGETS: 59.50-62.00-65.00 STOP LOSS: 52.00" → OPEN_LONG

QUALITY SCORING (1-10):
Score the signal's trading quality based on:
- Risk:Reward ratio (higher R:R = better score)
- Stop-loss proximity (too tight = risky, too wide = sloppy)
- Number of take-profit levels (more TPs = better planned)
- Entry zone clarity (specific prices > vague ranges)
- Technical reasoning mentioned (support/resistance, patterns = bonus)
- Signal source credibility indicators

Scoring guide:
- 8-10: Excellent R:R (2:1+), clear SL, multiple TPs, technical confluence
- 5-7: Decent signal but some weaknesses (wide SL, few TPs, no reasoning)
- 1-4: Poor signal (no SL, unrealistic TPs, vague entry, no structure)

LIVE PRICE TUNING (only applies when a live_price for the pair is provided in the message):
If a live_price is provided AND it is for the SAME pair as the signal, compare it to the entry zone:
- live_price INSIDE [entry_low, entry_high]: normal signal. entry_tuned=false, stale=false.
- live_price OUTSIDE the zone but the setup is STILL VALID at live_price (price moved only modestly past the zone, and the risk:reward to the take_profits using the ORIGINAL stop_loss remains acceptable): TUNE the entry — set entry_low = entry_high = live_price, KEEP stop_loss and take_profits UNCHANGED, set entry_tuned=true, stale=false. Note in reasoning ("entry tuned to live price X; original SL/TPs kept").
- The move has ALREADY HAPPENED (live_price at or above the first take_profit, or risk:reward at live_price with the original stop_loss is poor): set entry_tuned=false, stale=true. Note in reasoning ("stale — price already at TP1").
- live_price for a DIFFERENT pair than the signal, or no live_price given: ignore it. entry_tuned=false, stale=false.
The goal: capture still-valid trades whose entry zone the price has moved slightly past, while marking stale signals where the move already completed. When tuning, NEVER change the stop_loss or take_profits — only the entry.

OUTPUT FORMAT (JSON only, no markdown, no code blocks):
{
    "action": "OPEN_LONG" | "CLOSE" | "UPDATE_SL" | "UPDATE_TP" | "NOT_A_SIGNAL",
    "pair": "BTC-USDT" | null,
    "entry_low": 95000.0 | null,
    "entry_high": 96000.0 | null,
    "stop_loss": 93500.0 | null,
    "take_profits": [98000.0, 100000.0, 103000.0],
    "confidence": "high" | "medium" | "low",
    "quality_score": 8,
    "quality_reason": "Strong R:R of 2.5:1, tight SL, 3 TPs with technical confluence",
    "is_market_entry": false,
    "entry_tuned": false,
    "stale": false,
    "reasoning": "Brief explanation of your parsing"
}"""


class SignalParser:
    """Parses trader messages into structured signals using ZhipuAI GLM."""

    def __init__(self, api_key: str = "", model: str = "deepseek-chat"):
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._model = model

    # Markers that indicate a result update, not a new entry signal
    RESULT_MARKERS = ("✅", "🔥", "🚫", "% Profit", "% Loss", "[EDIT]",
                      "TP1 HIT", "TP2 HIT", "TP3 HIT", "BREAKEVEN EXIT",
                      "STOP LOSS HIT", "CANCELLED")

    def parse(self, message: str, live_price: Optional[float] = None,
              live_pair: Optional[str] = None) -> ParsedSignal:
        """Parse a trader's message into a structured signal.

        When live_price + live_pair are supplied (pre-fetched for the message's
        ticker), DeepSeek judges IN THIS SAME CALL whether a signal whose price
        has moved outside the entry zone is still tunable (entry shifted to the
        live price, original SL/TPs kept) or stale. See LIVE PRICE TUNING in
        SYSTEM_PROMPT. Entry-zone scope only.
        """
        if not self._api_key:
            logger.warning("DEEPSEEK_API_KEY not set, cannot parse signals")
            return ParsedSignal(action=SignalAction.NOT_A_SIGNAL, raw_message=message)

        # Fast pre-filter: skip result updates without calling DeepSeek
        if any(marker.lower() in message.lower() for marker in self.RESULT_MARKERS):
            logger.debug(f"Pre-filtered as result update (skipped DeepSeek): {message[:80]}")
            return ParsedSignal(action=SignalAction.NOT_A_SIGNAL, raw_message=message,
                                parse_reasoning="Pre-filtered: contains result update markers")

        prompt = f"Parse this trading signal message:\n\n{message}"
        if live_price is not None and live_pair:
            prompt += f"\n\n[Context] live_price for {live_pair}: {live_price}"

        try:
            response_json = self._call_glm(prompt)
            return self._json_to_signal(response_json, message)
        except Exception as e:
            logger.error(f"Signal parsing failed: {e}")
            return ParsedSignal(action=SignalAction.NOT_A_SIGNAL, raw_message=message)

    def _call_glm(self, prompt: str) -> dict:
        """HTTP call to DeepSeek API. Returns parsed JSON dict."""
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        })

        conn = http.client.HTTPSConnection("api.deepseek.com", timeout=30)
        conn.request("POST", "/chat/completions", body=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        })
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()

        if "error" in data:
            raise RuntimeError(f"GLM API error {data['error'].get('code', '?')}: {data['error'].get('message', data['error'])}")

        text = data["choices"][0]["message"]["content"]
        # Strip markdown code blocks if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())

    def _json_to_signal(self, data: dict, raw_message: str) -> ParsedSignal:
        """Convert LLM JSON output to ParsedSignal dataclass."""
        action_str = data.get("action", "NOT_A_SIGNAL")
        try:
            action = SignalAction(action_str)
        except ValueError:
            action = SignalAction.NOT_A_SIGNAL

        pair = data.get("pair")
        if pair:
            pair = pair.upper().replace("/", "-")
            if not pair.endswith("-USDT"):
                pair = f"{pair}-USDT"

        tps = data.get("take_profits") or []
        tps = sorted([float(tp) for tp in tps if tp is not None])

        conf_str = data.get("confidence", "medium")
        try:
            confidence = SignalConfidence(conf_str)
        except ValueError:
            confidence = SignalConfidence.MEDIUM

        entry_low = data.get("entry_low")
        entry_high = data.get("entry_high")
        if entry_low is not None:
            entry_low = float(entry_low)
        if entry_high is not None:
            entry_high = float(entry_high)

        stop_loss = data.get("stop_loss")
        if stop_loss is not None:
            stop_loss = float(stop_loss)

        return ParsedSignal(
            action=action,
            pair=pair,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            take_profits=tps,
            confidence=confidence,
            raw_message=raw_message,
            parse_reasoning=data.get("reasoning", ""),
            quality_score=max(1, min(10, int(data.get("quality_score", 5)))),
            quality_reason=data.get("quality_reason", ""),
            is_market_entry=data.get("is_market_entry", False),
            entry_tuned=bool(data.get("entry_tuned", False)),
            stale=bool(data.get("stale", False)),
        )
