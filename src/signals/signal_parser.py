"""
signal_parser.py — Parses trader Telegram messages into structured signals using ZhipuAI GLM.

Uses GLM-4-Flash for fast, cheap structured extraction.
HTTP calls via http.client (no external library needed).
"""

import http.client
import json
import logging
import os
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
    is_market_entry: bool = False


SYSTEM_PROMPT = """You are a trading signal parser. Extract structured trade information from Telegram messages sent by professional crypto traders.

RULES:
1. Only extract ACTIONABLE trading signals. General market commentary, motivation posts, questions, or charts without clear entry/exit are NOT signals.
2. All trading is SPOT only (no futures, no leverage, no shorts). If the trader says "SHORT" or "SELL" as an opening position (not closing), the action is NOT_A_SIGNAL.
3. Normalize all pairs to format: "BTC-USDT", "ETH-USDT", etc. Add -USDT suffix if missing.
4. If the trader gives a price range for entry (e.g., "95-96k"), extract both entry_low and entry_high.
5. If only one entry price is given, set entry_low = entry_high.
6. Take-profit targets should be sorted ascending (lowest first).
7. If the message says "close", "exit", "take profit", "out", "book" for a specific pair, the action is CLOSE.
8. If the message updates stop-loss only (e.g., "move SL to entry"), the action is UPDATE_SL.
9. If no stop-loss is given for a new position, set stop_loss to null.
10. Convert shorthand: "95k" = 95000, "0.5" stays 0.5, "$100" = 100.0
11. If the message is just market commentary, analysis, or chat with no specific entry/exit, action is NOT_A_SIGNAL.

OUTPUT FORMAT (JSON only, no markdown, no code blocks):
{
    "action": "OPEN_LONG" | "CLOSE" | "UPDATE_SL" | "UPDATE_TP" | "NOT_A_SIGNAL",
    "pair": "BTC-USDT" | null,
    "entry_low": 95000.0 | null,
    "entry_high": 96000.0 | null,
    "stop_loss": 93500.0 | null,
    "take_profits": [98000.0, 100000.0, 103000.0],
    "confidence": "high" | "medium" | "low",
    "is_market_entry": false,
    "reasoning": "Brief explanation of your parsing"
}"""


class SignalParser:
    """Parses trader messages into structured signals using ZhipuAI GLM."""

    def __init__(self, api_key: str = "", model: str = "glm-4-flash"):
        self._api_key = api_key or os.environ.get("ZHIPU_API_KEY", "")
        self._model = model

    def parse(self, message: str) -> ParsedSignal:
        """Parse a trader's message into a structured signal."""
        if not self._api_key:
            logger.warning("ZHIPU_API_KEY not set, cannot parse signals")
            return ParsedSignal(action=SignalAction.NOT_A_SIGNAL, raw_message=message)

        prompt = f"Parse this trading signal message:\n\n{message}"

        try:
            response_json = self._call_glm(prompt)
            return self._json_to_signal(response_json, message)
        except Exception as e:
            logger.error(f"Signal parsing failed: {e}")
            return ParsedSignal(action=SignalAction.NOT_A_SIGNAL, raw_message=message)

    def _call_glm(self, prompt: str) -> dict:
        """HTTP call to ZhipuAI GLM API. Returns parsed JSON dict."""
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        })

        conn = http.client.HTTPSConnection("open.bigmodel.cn", timeout=15)
        conn.request("POST", "/api/paas/v4/chat/completions", body=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        })
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()

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

        tps = data.get("take_profits", [])
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
            is_market_entry=data.get("is_market_entry", False),
        )
