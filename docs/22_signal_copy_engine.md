# 4th Engine: AI Signal Copy Trading Engine

## Overview

The Signal Copy Trading Engine is the 4th trading engine alongside Grid (engine 1), Trend (engine 2), and Momentum (engine 3). It connects to a professional trader's Telegram channel, reads their trade signal messages in real-time, uses an LLM (AI) to parse unstructured text into structured trade instructions, validates them against risk rules, and auto-executes on Binance.

**Input:** Professional trader's Telegram channel messages
**AI Parser:** LLM (Gemini / OpenAI / ZhipuAI GLM) for natural language → structured signal
**Capital:** Isolated 10% of total equity (configurable, max $1,000 hard cap)
**Max Positions:** 3 concurrent signal trades
**Goal:** Mirror a professional trader's calls with automated risk management and instant execution

---

## How It Works

```
Telegram Channel Message Received:
│
├── Channel Listener (Telethon MTProto client) receives message in real-time
│   └── Only processes messages from configured channel ID(s)
│
├── AI Parser (LLM) extracts structured signal from natural language:
│   ├── Pair: "BTC/USDT"
│   ├── Direction: "LONG" or "SHORT" (spot = BUY only)
│   ├── Entry zone: $95,000 – $96,000
│   ├── Stop-loss: $93,500
│   ├── Take-profit targets: TP1=$98,000, TP2=$100,000, TP3=$103,000
│   ├── Leverage: N/A (spot only)
│   └── Confidence: "high" / "medium" / "low"
│
├── Signal Validator checks:
│   ├── Is pair available on Binance FZE?
│   ├── Is risk:reward ratio ≥ 1.5?
│   ├── Is stop-loss distance ≤ 5% from entry?
│   ├── Does capital allocation pass PositionGuard?
│   ├── Is BTC regime NOT DANGER? (correlation gate)
│   └── Is daily loss limit not exceeded?
│
├── All checks pass?
│   ├── NO → Log rejection + Telegram alert: "⚠️ Signal rejected: {reason}"
│   │
│   └── YES → Execute:
│       ├── Place LIMIT BUY at top of entry zone (or MARKET if "market entry")
│       ├── Set stop-loss as OCO or manual check
│       ├── Split position: 33% TP1, 33% TP2, 34% TP3
│       └── Send confirmation: "✅ Signal executed: BUY 0.05 BTC @ $95,500"
│
└── Position Management (on every tick):
    ├── Monitor stop-loss (hard exit)
    ├── Trail stop after TP1 hits (move SL to entry = breakeven)
    ├── Trail stop after TP2 hits (move SL to TP1)
    ├── Full exit at TP3 or manual "/signal_close" command
    └── Trader sends "close" or "exit" message → AI parses → auto-close
```

---

## Why This Is Different from Engine 3 (Momentum)

| Aspect | Engine 3 (Momentum) | Engine 4 (Signal Copy) |
|--------|---------------------|----------------------|
| **Signal source** | Automated scanner (algorithms) | Human trader (Telegram messages) |
| **Hold time** | 5–30 minutes | Hours to days |
| **Entry logic** | RVOL + momentum score + ML | Trader's analysis (parsed by AI) |
| **TP/SL** | Fixed % rules | Trader-specified price levels |
| **Pairs** | Any USDT pair (auto-discovered) | Only pairs the trader calls |
| **Risk** | 5% capital, tight stops | 10% capital, wider stops |

---

## Telegram Channel Listener

### Architecture Choice: Telethon (MTProto) vs Bot API

The Bot API (used by your existing `telegram_bot.py` and `telegram_commands.py`) **cannot read messages from channels or groups** unless the bot is explicitly added as an admin. Most professional signal channels won't add a random bot.

**Solution: Telethon** — a Python library that connects to Telegram as a **user client** (not a bot). It uses your personal Telegram account via the MTProto protocol to read any channel you've joined, exactly like the Telegram app on your phone.

### File: `src/signals/channel_listener.py`

```python
from telethon import TelegramClient, events
import os
import asyncio
import logging
from typing import Callable, Optional
from collections import deque

logger = logging.getLogger(__name__)


class ChannelListener:
    """Listens to Telegram channels for trade signals using Telethon (MTProto).
    
    Telethon connects as a USER (not a bot) using your personal Telegram account.
    This allows reading messages from ANY channel you've joined — no admin access needed.
    
    First run requires interactive phone number + OTP verification.
    After that, the session file persists the auth.
    """

    def __init__(self, api_id: int, api_hash: str, 
                 channel_ids: list[int],
                 session_name: str = "signal_listener",
                 on_signal: Optional[Callable] = None):
        """
        Args:
            api_id: From https://my.telegram.org/apps
            api_hash: From https://my.telegram.org/apps
            channel_ids: List of Telegram channel/group IDs to monitor.
                         Get via: forward a message from the channel to @userinfobot
                         or use Telethon: `async for dialog in client.iter_dialogs(): print(dialog.id, dialog.name)`
            session_name: Name for the Telethon session file (persists auth)
            on_signal: Callback function(channel_id: int, message_text: str, message_id: int, timestamp: float)
        """
        self._api_id = api_id
        self._api_hash = api_hash
        self._channel_ids = set(channel_ids)
        self._session_name = session_name
        self._on_signal = on_signal
        self._client: Optional[TelegramClient] = None
        self._running = False
        
        # Message dedup (prevent processing edits/duplicates)
        self._processed_ids: deque = deque(maxlen=500)
    
    async def start(self):
        """Connect to Telegram and start listening.
        
        First run: Will prompt for phone number + OTP code in terminal.
        Subsequent runs: Uses saved session file (no prompt needed).
        
        Session file is saved as: data/{session_name}.session
        """
        session_path = f"data/{self._session_name}"
        self._client = TelegramClient(session_path, self._api_id, self._api_hash)
        
        await self._client.start()
        me = await self._client.get_me()
        logger.info(f"Telethon connected as: {me.first_name} (ID: {me.id})")
        
        # Register handler for new messages in target channels
        @self._client.on(events.NewMessage(chats=list(self._channel_ids)))
        async def handler(event):
            # Dedup
            if event.message.id in self._processed_ids:
                return
            self._processed_ids.append(event.message.id)
            
            text = event.message.text or ""
            if not text.strip():
                return  # Skip empty/media-only messages
            
            logger.info(f"Signal channel message: [{event.chat_id}] {text[:100]}...")
            
            if self._on_signal:
                try:
                    self._on_signal(
                        channel_id=event.chat_id,
                        message_text=text,
                        message_id=event.message.id,
                        timestamp=event.message.date.timestamp(),
                    )
                except Exception as e:
                    logger.error(f"Signal callback error: {e}")
        
        # Also handle edited messages (trader updates SL/TP)
        @self._client.on(events.MessageEdited(chats=list(self._channel_ids)))
        async def edit_handler(event):
            text = event.message.text or ""
            if not text.strip():
                return
            
            logger.info(f"Signal channel EDIT: [{event.chat_id}] {text[:100]}...")
            
            if self._on_signal:
                try:
                    self._on_signal(
                        channel_id=event.chat_id,
                        message_text=f"[EDIT] {text}",
                        message_id=event.message.id,
                        timestamp=event.message.date.timestamp(),
                    )
                except Exception as e:
                    logger.error(f"Signal edit callback error: {e}")
        
        self._running = True
        logger.info(f"Listening to {len(self._channel_ids)} channel(s)")
        
        # Keep running until stopped
        await self._client.run_until_disconnected()
    
    async def stop(self):
        self._running = False
        if self._client:
            await self._client.disconnect()
```

### Telethon Setup (One-Time)

```bash
# 1. Go to https://my.telegram.org/apps
# 2. Create a new application
# 3. Copy "App api_id" and "App api_hash"
# 4. Add to .env:
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890

# 5. Signal channel ID(s) — comma-separated
# Get channel ID: forward any message from the channel to @userinfobot
# Channel IDs are usually negative numbers like -1001234567890
SIGNAL_CHANNEL_IDS=-1001234567890,-1009876543210
```

### First Run Authentication

On first run, Telethon prompts in the terminal:

```
Please enter your phone number (with country code): +971XXXXXXXX
Please enter the code you received: 12345
Signed in successfully as Amro
```

After this, a session file (`data/signal_listener.session`) is created. All subsequent runs use this file — no more prompts. This file is gitignored.

---

## AI Signal Parser

### File: `src/signals/signal_parser.py`

Uses an LLM to convert unstructured trader messages into structured trade signals. Supports multiple AI providers.

### What the AI Parses

Professional trader messages come in many formats:

**Example 1 — Clean format:**
```
🟢 BTC/USDT LONG
Entry: 95,000 - 96,000
SL: 93,500
TP1: 98,000
TP2: 100,000
TP3: 103,000
Risk: Medium
```

**Example 2 — Informal:**
```
btc looking good here, buying around 95-96k
stop below 93.5
targets 98, 100, 103
```

**Example 3 — Update/Close:**
```
close btc, we hit TP2 🎯
moving SL to entry on ETH
```

**Example 4 — Not a signal (noise):**
```
Good morning everyone! Market looks interesting today 🔥
Remember to always DYOR!
```

The AI must handle ALL of these formats and correctly identify non-signal messages.

### Signal Data Structure

```python
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class SignalAction(Enum):
    OPEN_LONG = "OPEN_LONG"     # Buy on spot
    CLOSE = "CLOSE"             # Close existing position
    UPDATE_SL = "UPDATE_SL"     # Move stop-loss
    UPDATE_TP = "UPDATE_TP"     # Add/modify take-profit
    NOT_A_SIGNAL = "NOT_A_SIGNAL"  # General chat, not actionable


class SignalConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ParsedSignal:
    action: SignalAction
    pair: Optional[str] = None         # "BTC-USDT" (our format)
    entry_low: Optional[float] = None  # Bottom of entry zone
    entry_high: Optional[float] = None # Top of entry zone (or same as low for exact entry)
    stop_loss: Optional[float] = None
    take_profits: list[float] = field(default_factory=list)  # [TP1, TP2, TP3]
    confidence: SignalConfidence = SignalConfidence.MEDIUM
    raw_message: str = ""
    parse_reasoning: str = ""          # LLM's explanation of how it parsed
    is_market_entry: bool = False      # True = market order, False = limit in zone
```

### LLM Prompt Engineering

```python
class SignalParser:
    """Parses trader messages into structured signals using an LLM."""

    SYSTEM_PROMPT = """You are a trading signal parser. Your job is to extract structured trade information from Telegram messages sent by a professional crypto trader.

RULES:
1. Only extract ACTIONABLE trading signals. General market commentary, motivation posts, or questions are NOT signals.
2. All trading is SPOT only (no futures, no leverage, no shorts). If the trader says "SHORT", the action is NOT_A_SIGNAL.
3. Normalize all pairs to Binance format: "BTC-USDT", "ETH-USDT", etc.
4. If the trader gives a price range for entry (e.g., "95-96k"), extract both entry_low and entry_high.
5. If only one entry price is given, set entry_low = entry_high.
6. Take-profit targets should be sorted ascending (lowest first).
7. If the message says "close", "exit", "take profit", "out" for a specific pair, the action is CLOSE.
8. If the message updates stop-loss only (e.g., "move SL to entry"), the action is UPDATE_SL.
9. If no stop-loss is given, set stop_loss to null — the system will reject the signal.
10. Convert shorthand: "95k" = 95000, "0.5" for a low-price coin = 0.5, etc.

OUTPUT FORMAT (JSON only, no markdown):
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

    def __init__(self, provider: str = "gemini", api_key: str = "",
                 model: str = "gemini-2.5-flash"):
        """
        Args:
            provider: "gemini", "openai", or "zhipu"
            api_key: API key for the provider
            model: Model name (e.g., "gemini-2.5-flash", "gpt-4o-mini", "glm-4-flash")
        """
        self._provider = provider
        self._api_key = api_key
        self._model = model

    def parse(self, message: str) -> ParsedSignal:
        """Parse a trader's message into a structured signal.
        
        Makes a synchronous HTTP call to the LLM API.
        Timeout: 10 seconds.
        Fallback: Returns NOT_A_SIGNAL if LLM fails or returns invalid JSON.
        """
        prompt = f"Parse this trading signal message:\n\n{message}"
        
        try:
            response_json = self._call_llm(prompt)
            return self._json_to_signal(response_json, message)
        except Exception as e:
            logger.error(f"Signal parsing failed: {e}")
            return ParsedSignal(action=SignalAction.NOT_A_SIGNAL, raw_message=message)

    def _call_llm(self, prompt: str) -> dict:
        """HTTP call to LLM provider. Returns parsed JSON dict."""
        # Implementation varies by provider:
        # - gemini: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
        # - openai: POST https://api.openai.com/v1/chat/completions
        # - zhipu: POST https://open.bigmodel.cn/api/paas/v4/chat/completions
        #
        # All use http.client (same pattern as telegram_commands.py _tg_post)
        # No external library needed — plain HTTPS request
        ...

    def _json_to_signal(self, data: dict, raw_message: str) -> ParsedSignal:
        """Convert LLM JSON output to ParsedSignal dataclass."""
        action_str = data.get("action", "NOT_A_SIGNAL")
        try:
            action = SignalAction(action_str)
        except ValueError:
            action = SignalAction.NOT_A_SIGNAL
        
        # Normalize pair format
        pair = data.get("pair")
        if pair:
            pair = pair.upper().replace("/", "-")
            if not pair.endswith("-USDT"):
                pair = f"{pair}-USDT"
        
        # Parse take profits (ensure sorted ascending)
        tps = data.get("take_profits", [])
        tps = sorted([float(tp) for tp in tps if tp is not None])
        
        # Parse confidence
        conf_str = data.get("confidence", "medium")
        try:
            confidence = SignalConfidence(conf_str)
        except ValueError:
            confidence = SignalConfidence.MEDIUM
        
        return ParsedSignal(
            action=action,
            pair=pair,
            entry_low=data.get("entry_low"),
            entry_high=data.get("entry_high"),
            stop_loss=data.get("stop_loss"),
            take_profits=tps,
            confidence=confidence,
            raw_message=raw_message,
            parse_reasoning=data.get("reasoning", ""),
            is_market_entry=data.get("is_market_entry", False),
        )
```

### LLM Provider Implementations

Use plain `http.client` (same pattern as `telegram_commands.py` lines 63–89). No external library needed.

#### Gemini

```python
def _call_gemini(self, prompt: str) -> dict:
    import http.client
    import json
    
    body = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": self.SYSTEM_PROMPT}]},
            {"role": "model", "parts": [{"text": "Understood. Send me the message to parse."}]},
            {"role": "user", "parts": [{"text": prompt}]},
        ],
        "generationConfig": {
            "temperature": 0.1,  # Low temp for deterministic parsing
            "responseMimeType": "application/json",
        }
    })
    
    conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=10)
    conn.request("POST", 
        f"/v1beta/models/{self._model}:generateContent?key={self._api_key}",
        body=body,
        headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    
    # Extract text from Gemini response
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)
```

#### OpenAI

```python
def _call_openai(self, prompt: str) -> dict:
    body = json.dumps({
        "model": self._model,
        "messages": [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    })
    
    conn = http.client.HTTPSConnection("api.openai.com", timeout=10)
    conn.request("POST", "/v1/chat/completions", body=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {self._api_key}",
    })
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    
    text = data["choices"][0]["message"]["content"]
    return json.loads(text)
```

#### ZhipuAI (GLM) — Already in .env

```python
def _call_zhipu(self, prompt: str) -> dict:
    body = json.dumps({
        "model": self._model,  # "glm-4-flash"
        "messages": [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    })
    
    conn = http.client.HTTPSConnection("open.bigmodel.cn", timeout=10)
    conn.request("POST", "/api/paas/v4/chat/completions", body=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {self._api_key}",
    })
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    
    text = data["choices"][0]["message"]["content"]
    return json.loads(text)
```

---

## Signal Validator

### File: `src/signals/signal_validator.py`

Validates parsed signals against risk rules before execution.

```python
class SignalValidator:
    """Validates parsed signals before execution."""

    def __init__(self, config: dict, available_pairs: set[str]):
        """
        Args:
            config: signal_copy section from strategy.yaml
            available_pairs: Set of pairs available on Binance FZE (from exchangeInfo)
        """
        self._min_rr_ratio = config.get("min_rr_ratio", 1.5)
        self._max_sl_distance_pct = config.get("max_sl_distance_pct", 5.0)
        self._max_entry_zone_pct = config.get("max_entry_zone_pct", 3.0)
        self._available_pairs = available_pairs
        self._blacklisted_pairs = set(config.get("blacklisted_pairs", []))

    def validate(self, signal: ParsedSignal) -> tuple[bool, str]:
        """
        Returns (valid: bool, rejection_reason: str).
        Empty string if valid.
        """
        # 1. Must be an actionable signal
        if signal.action == SignalAction.NOT_A_SIGNAL:
            return False, "Not a trade signal"
        
        # 2. For OPEN_LONG: pair must exist on Binance FZE
        if signal.action == SignalAction.OPEN_LONG:
            binance_symbol = signal.pair.replace("-", "") if signal.pair else ""
            if binance_symbol not in self._available_pairs:
                return False, f"Pair {signal.pair} not available on Binance FZE"
        
        # 3. Not blacklisted
        if signal.pair in self._blacklisted_pairs:
            return False, f"Pair {signal.pair} is blacklisted"
        
        # 4. Must have stop-loss for new positions
        if signal.action == SignalAction.OPEN_LONG and signal.stop_loss is None:
            return False, "No stop-loss specified — too risky"
        
        # 5. Must have at least one take-profit
        if signal.action == SignalAction.OPEN_LONG and not signal.take_profits:
            return False, "No take-profit target specified"
        
        # 6. Stop-loss distance check (max 5% from entry)
        if signal.action == SignalAction.OPEN_LONG and signal.stop_loss and signal.entry_high:
            sl_distance = (signal.entry_high - signal.stop_loss) / signal.entry_high * 100
            if sl_distance > self._max_sl_distance_pct:
                return False, f"SL distance {sl_distance:.1f}% > max {self._max_sl_distance_pct}%"
            if sl_distance <= 0:
                return False, f"SL {signal.stop_loss} is above entry {signal.entry_high}"
        
        # 7. Risk:reward ratio check (min 1.5:1 using TP1)
        if signal.action == SignalAction.OPEN_LONG and signal.take_profits and signal.stop_loss and signal.entry_high:
            risk = signal.entry_high - signal.stop_loss
            reward = signal.take_profits[0] - signal.entry_high  # TP1
            if risk <= 0:
                return False, "Invalid risk calculation (SL >= entry)"
            rr = reward / risk
            if rr < self._min_rr_ratio:
                return False, f"R:R {rr:.2f} < min {self._min_rr_ratio}"
        
        # 8. Entry zone not too wide (max 3%)
        if signal.action == SignalAction.OPEN_LONG and signal.entry_low and signal.entry_high:
            zone_pct = (signal.entry_high - signal.entry_low) / signal.entry_low * 100
            if zone_pct > self._max_entry_zone_pct:
                return False, f"Entry zone {zone_pct:.1f}% too wide (max {self._max_entry_zone_pct}%)"
        
        return True, ""
```

---

## Signal Engine (Orchestrator)

### File: `src/signals/signal_engine.py`

Main orchestrator. Receives raw messages from ChannelListener, parses via AI, validates, and executes.

```python
class SignalEngineState(Enum):
    LISTENING = "LISTENING"
    PARSING = "PARSING"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"


class SignalEngine:
    def __init__(self, config: dict, capital_manager: CapitalManager,
                 btc_regime_fn: callable, telegram: TelegramBot):
        self._config = config
        self._capital_mgr = capital_manager
        self._get_btc_regime = btc_regime_fn
        self._telegram = telegram
        
        self._state = SignalEngineState.LISTENING
        self._enabled = config.get("enabled", False)
        self._manual_pause = False
        
        # Sub-components
        self._listener = ChannelListener(
            api_id=int(os.environ.get("TELEGRAM_API_ID", 0)),
            api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
            channel_ids=[int(c) for c in os.environ.get("SIGNAL_CHANNEL_IDS", "").split(",") if c.strip()],
            on_signal=self._on_message_received,
        )
        
        ai_provider = config.get("ai_provider", "gemini")
        ai_key_env = config.get("ai_api_key_env", "GEMINI_API_KEY")
        self._parser = SignalParser(
            provider=ai_provider,
            api_key=os.environ.get(ai_key_env, ""),
            model=config.get("ai_model", "gemini-2.5-flash"),
        )
        
        self._validator = SignalValidator(config, available_pairs=set())
        self._risk = SignalRiskGuard(config)
        self._position_mgr = SignalPositionManager(config)
        self._journal = SignalJournal()
        
        # Message queue (thread-safe) — Telethon runs in its own thread
        self._message_queue: queue.Queue = queue.Queue(maxsize=50)
        
        # Fetch available pairs on startup
        self._refresh_available_pairs()
    
    def _on_message_received(self, channel_id: int, message_text: str,
                              message_id: int, timestamp: float):
        """Callback from ChannelListener. Called from Telethon's event loop thread.
        
        Puts message into thread-safe queue for processing in on_tick.
        """
        self._message_queue.put({
            "channel_id": channel_id,
            "text": message_text,
            "message_id": message_id,
            "timestamp": timestamp,
        })
    
    def tick(self, connector):
        """Called from on_tick(). Processes queued messages and manages positions."""
        if not self._enabled or self._manual_pause:
            return
        
        # Process queued messages (non-blocking)
        while not self._message_queue.empty():
            try:
                msg = self._message_queue.get_nowait()
                self._process_message(msg, connector)
            except queue.Empty:
                break
        
        # Manage open positions (check SL/TP/trailing)
        self._manage_positions(connector)
    
    def _process_message(self, msg: dict, connector):
        """Full pipeline: parse → validate → execute."""
        text = msg["text"]
        
        # Step 1: AI Parse
        signal = self._parser.parse(text)
        logger.info(f"Signal parsed: action={signal.action.value}, pair={signal.pair}, "
                     f"reasoning={signal.parse_reasoning}")
        
        # Log all parsed signals (even NOT_A_SIGNAL) for audit
        self._journal.log_raw_message(
            channel_id=msg["channel_id"],
            message_id=msg["message_id"],
            text=text,
            parsed_action=signal.action.value,
            parsed_pair=signal.pair,
            timestamp=msg["timestamp"],
        )
        
        if signal.action == SignalAction.NOT_A_SIGNAL:
            return
        
        # Handle CLOSE / UPDATE signals
        if signal.action == SignalAction.CLOSE:
            self._handle_close_signal(signal, connector)
            return
        if signal.action == SignalAction.UPDATE_SL:
            self._handle_update_sl(signal)
            return
        if signal.action == SignalAction.UPDATE_TP:
            self._handle_update_tp(signal)
            return
        
        # Step 2: Validate
        valid, reason = self._validator.validate(signal)
        if not valid:
            logger.info(f"Signal rejected: {reason}")
            # Send Telegram alert about rejection
            asyncio.get_event_loop().create_task(self._telegram.send(
                f"⚠️ <b>Signal Rejected</b>\n"
                f"Pair: {signal.pair}\n"
                f"Reason: {reason}\n"
                f"Message: {text[:100]}"
            ))
            return
        
        # Step 3: Risk checks
        if not self._risk.can_trade():
            logger.info("Signal blocked by risk guard")
            return
        
        # BTC correlation gate
        btc_regime, _, _ = self._get_btc_regime()
        if btc_regime == "DANGER" and self._config.get("use_btc_correlation_gate", True):
            logger.info("Signal blocked: BTC DANGER")
            return
        
        # Step 4: Execute
        self._execute_signal(signal, connector)
    
    def _execute_signal(self, signal: ParsedSignal, connector):
        """Place entry order for a new OPEN_LONG signal."""
        # Calculate position size
        budget = self._risk.get_budget_for_trade(signal)
        if budget <= 0:
            return
        
        # Allocate capital
        allocated = self._capital_mgr.allocate(signal.pair, "signal", budget)
        if not allocated:
            logger.warning(f"Capital allocation failed for {signal.pair}")
            return
        
        # Determine entry price
        if signal.is_market_entry:
            entry_price = None  # Market order
        else:
            # Use top of entry zone for limit order (conservative)
            entry_price = signal.entry_high
        
        # Calculate quantity
        effective_entry = entry_price or signal.entry_high
        quantity = budget / effective_entry
        
        # Get step_size for this pair from exchange info
        step_size = self._get_step_size(signal.pair)
        quantity = self._round_to_step(quantity, step_size)
        
        if quantity <= 0:
            self._capital_mgr.release(signal.pair, "signal")
            return
        
        # Place order
        try:
            order_type = OrderType.LIMIT_MAKER if entry_price else OrderType.MARKET
            # ... place order via connector (same pattern as grid/trend engines)
            
            # Open position tracking
            self._position_mgr.open_position(
                symbol=signal.pair,
                entry_price=effective_entry,
                amount=quantity,
                stop_loss=signal.stop_loss,
                take_profits=signal.take_profits,
                signal_confidence=signal.confidence.value,
                raw_message=signal.raw_message,
            )
            
            # Telegram confirmation
            tp_str = " / ".join([f"${tp:,.0f}" for tp in signal.take_profits])
            asyncio.get_event_loop().create_task(self._telegram.send(
                f"✅ <b>Signal Executed</b>\n"
                f"•••\n"
                f"📈 BUY {signal.pair}\n"
                f"💲 Entry: ${effective_entry:,.2f}\n"
                f"🛑 SL: ${signal.stop_loss:,.2f}\n"
                f"🎯 TP: {tp_str}\n"
                f"📦 Qty: {quantity:.4f}\n"
                f"💰 Budget: ${budget:,.0f}\n"
                f"📊 Confidence: {signal.confidence.value}\n"
                f"•••\n"
                f"Source: {signal.raw_message[:80]}"
            ))
            
            self._risk.record_trade_opened()
            
        except Exception as e:
            logger.error(f"Signal execution failed: {e}")
            self._capital_mgr.release(signal.pair, "signal")
```

### Position Management with TP Scaling

```python
    def _manage_positions(self, connector):
        """Check all signal positions for SL/TP hits."""
        for pos in self._position_mgr.get_all_positions():
            if pos.is_closed:
                continue
            
            current_price = self._get_current_price(connector, pos.symbol)
            if current_price <= 0:
                continue
            
            # Stop-loss hit
            if current_price <= pos.stop_loss:
                self._close_position(connector, pos, current_price, "stop_loss")
                continue
            
            # Take-profit hits (partial closes)
            # TP1 hit: close 33%, move SL to entry (breakeven)
            if not pos.tp1_hit and len(pos.take_profits) >= 1 and current_price >= pos.take_profits[0]:
                pos.tp1_hit = True
                self._partial_close(connector, pos, 0.33, pos.take_profits[0], "tp1")
                pos.stop_loss = pos.entry_price  # Move SL to breakeven
                logger.info(f"Signal TP1 hit for {pos.symbol}, SL moved to breakeven")
            
            # TP2 hit: close 33%, move SL to TP1
            if not pos.tp2_hit and len(pos.take_profits) >= 2 and current_price >= pos.take_profits[1]:
                pos.tp2_hit = True
                self._partial_close(connector, pos, 0.50, pos.take_profits[1], "tp2")  # 50% of remaining
                pos.stop_loss = pos.take_profits[0]  # Move SL to TP1
                logger.info(f"Signal TP2 hit for {pos.symbol}, SL moved to TP1")
            
            # TP3 hit: close remaining
            if not pos.tp3_hit and len(pos.take_profits) >= 3 and current_price >= pos.take_profits[2]:
                pos.tp3_hit = True
                self._close_position(connector, pos, pos.take_profits[2], "tp3")
```

---

## Signal Position Manager

### File: `src/signals/signal_position.py`

```python
@dataclass
class SignalPosition:
    symbol: str
    entry_order_id: str
    entry_price: float
    amount: float                 # Total amount (decreases on partial closes)
    stop_loss: float              # Moves up as TPs are hit
    take_profits: list[float]     # [TP1, TP2, TP3]
    signal_confidence: str
    raw_message: str
    entry_timestamp: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    amount_closed: float = 0.0    # Cumulative amount closed
    realized_pnl: float = 0.0    # Cumulative realized P&L from partial closes
    exit_order_id: str = ""
    exit_reason: str = ""
    is_closed: bool = False


class SignalPositionManager:
    def __init__(self, config: dict):
        self._max_positions = config.get("max_positions", 3)
        self._positions: dict[str, SignalPosition] = {}  # keyed by symbol
        self._lock = threading.Lock()
    
    # Methods follow same pattern as trend/position_manager.py:
    # - open_position(), get_position(), get_all_positions()
    # - partial_close() — new: reduces amount and tracks realized PnL
    # - finalize_exit() — full close with final P&L calculation
    # - save_state() / load_state() — JSON persistence to data/signal_state_{symbol}.json
```

---

## Signal Risk Guard

### File: `src/signals/signal_risk.py`

```python
class SignalRiskGuard:
    """Risk management for signal copy trading."""

    def __init__(self, config: dict):
        self._capital_pct = config.get("capital_pct", 10.0)
        self._max_capital = config.get("max_capital_usdt", 1000)
        self._max_positions = config.get("max_positions", 3)
        self._per_trade_pct = config.get("per_trade_risk_pct", 3.0)
        self._daily_loss_limit_pct = config.get("daily_loss_limit_pct", 5.0)
        self._max_trades_per_day = config.get("max_trades_per_day", 10)
        self._min_confidence = config.get("min_confidence", "low")  # "low", "medium", "high"
        
        # Daily tracking
        self._trades_today = 0
        self._daily_pnl = 0.0
        self._halted = False
        self._last_reset_date = ""
    
    def can_trade(self) -> bool:
        self._maybe_reset_daily()
        if self._halted:
            return False
        if self._trades_today >= self._max_trades_per_day:
            return False
        return True
    
    def get_budget_for_trade(self, signal: ParsedSignal) -> float:
        """Calculate position size based on signal confidence and risk per trade.
        
        Position sizing by confidence:
        - HIGH:   full risk (per_trade_risk_pct)
        - MEDIUM: 66% of risk
        - LOW:    33% of risk
        
        Also caps by SL distance: risk_amount / sl_distance = position_size
        """
        conf_multiplier = {"high": 1.0, "medium": 0.66, "low": 0.33}
        mult = conf_multiplier.get(signal.confidence.value, 0.33)
        
        total_budget = min(self._max_capital, ...)  # Calculate from equity
        risk_amount = total_budget * self._per_trade_pct / 100 * mult
        
        if signal.stop_loss and signal.entry_high:
            sl_distance = signal.entry_high - signal.stop_loss
            if sl_distance > 0:
                position_size_usdt = risk_amount / (sl_distance / signal.entry_high)
                return min(position_size_usdt, total_budget / self._max_positions)
        
        return total_budget / self._max_positions
```

---

## Signal Journal

### File: `src/signals/signal_journal.py`

SQLite journal in `data/signal_journal.db`. Two tables:

**Table 1: `raw_messages`** — Audit log of every message received
```sql
CREATE TABLE raw_messages (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    channel_id INTEGER,
    message_id INTEGER,
    text TEXT,
    parsed_action TEXT,
    parsed_pair TEXT
);
```

**Table 2: `signal_trades`** — Executed trades with full P&L
```sql
CREATE TABLE signal_trades (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    symbol TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity REAL,
    gross_pnl REAL,
    fee REAL,
    net_pnl REAL,
    hold_duration_min INTEGER,
    exit_reason TEXT,          -- "stop_loss" | "tp1" | "tp2" | "tp3" | "manual" | "btc_danger"
    signal_confidence TEXT,
    raw_message TEXT,
    tp1_price REAL,
    tp2_price REAL,
    tp3_price REAL,
    tp1_hit INTEGER,          -- 0 or 1
    tp2_hit INTEGER,
    tp3_hit INTEGER,
    ai_provider TEXT,
    parse_reasoning TEXT
);
```

---

## Integration with Main Strategy

### Changes to `ta_grid_trend.py`

#### In `__init__()` (after momentum engine init):

```python
# ── Signal Copy Engine ──
signal_cfg = cfg.get("signal_copy", {})
self._signal_engine = None
if signal_cfg.get("enabled", False):
    from src.signals.signal_engine import SignalEngine
    
    self._signal_engine = SignalEngine(
        config=signal_cfg,
        capital_manager=self._capital_mgr,
        btc_regime_fn=_get_btc_regime,  # Same function as momentum engine
        telegram=self.telegram,
    )
    # Start Telethon listener in background thread
    threading.Thread(target=self._start_signal_listener, daemon=True).start()
    logger.info("Signal Copy Engine initialized")
```

#### In `on_tick()` (after momentum tick):

```python
# ── Signal Copy Engine ──
if self._signal_engine is not None:
    try:
        connector = self.connectors.get(self.exchange)
        if connector:
            self._signal_engine.tick(connector)
    except Exception as e:
        logger.error(f"Signal tick error: {e}")
```

### Changes to `capital_manager.py`

```python
EngineType = Literal["grid", "trend", "momentum", "signal"]
```

### Changes to `strategy.yaml`

```yaml
# ── Signal Copy Trading Engine ────────────────────────────────
signal_copy:
  enabled: false                    # Start disabled
  capital_pct: 10.0                 # % of total capital for signal trades
  max_capital_usdt: 1000            # Hard cap

  # AI Parser
  ai_provider: "gemini"             # "gemini", "openai", or "zhipu"
  ai_api_key_env: "GEMINI_API_KEY"  # Env var name containing the API key
  ai_model: "gemini-2.5-flash"      # Model name

  # Position management
  max_positions: 3                  # Max concurrent signal positions
  per_trade_risk_pct: 3.0           # Risk per trade (% of signal budget)
  min_rr_ratio: 1.5                 # Minimum risk:reward for TP1
  max_sl_distance_pct: 5.0          # Max stop-loss distance (%)
  max_entry_zone_pct: 3.0           # Max entry zone width (%)

  # TP scaling
  tp1_close_pct: 33                 # Close 33% at TP1, move SL to entry
  tp2_close_pct: 50                 # Close 50% of remaining at TP2, move SL to TP1
  tp3_close_pct: 100                # Close 100% remaining at TP3

  # Risk controls
  daily_loss_limit_pct: 5.0         # Halt after losing this % of signal capital
  max_trades_per_day: 10            # Maximum signal trades per day
  min_confidence: "low"             # Minimum AI-parsed confidence to accept
  use_btc_correlation_gate: true    # Pause when BTC is DANGER
  blacklisted_pairs: []             # Pairs to never trade from signals

  # Telethon
  session_name: "signal_listener"   # Session file name (saved in data/)
```

### Changes to `.env.example`

```bash
# ── SIGNAL COPY TRADING (Telethon) ─────────────────────────────
# Get from: https://my.telegram.org/apps
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here

# Signal channel IDs (comma-separated, get from @userinfobot)
SIGNAL_CHANNEL_IDS=-1001234567890

# AI Provider for signal parsing
GEMINI_API_KEY=your_gemini_api_key_here
# Or: OPENAI_API_KEY=your_openai_key_here
# Or: ZHIPU_API_KEY already configured above
```

### Telegram Commands

Add to `telegram_commands.py`:

| Command | Description |
|---------|-------------|
| `/signal_status` | Engine state, listener status, open positions, recent signals |
| `/signal_pnl` | P&L for signal trades (today/week/month) with win rate by confidence level |
| `/signal_pause` | Pause signal processing (keeps listening but doesn't execute) |
| `/signal_resume` | Resume signal processing |
| `/signal_close <PAIR>` | Manually close a signal position (e.g., `/signal_close BTC-USDT`) |
| `/signal_history` | Last 10 signals received with parse results |

---

## File Structure

```
src/
  signals/
    __init__.py                   — Package init
    channel_listener.py           — ChannelListener (Telethon MTProto client)
    signal_parser.py              — SignalParser (LLM-based message parsing)
    signal_validator.py           — SignalValidator (risk/pair/R:R checks)
    signal_engine.py              — SignalEngine (orchestrator)
    signal_position.py            — SignalPositionManager (TP scaling, partial closes)
    signal_risk.py                — SignalRiskGuard (daily limits, confidence sizing)
    signal_journal.py             — SignalJournal (SQLite: raw_messages + signal_trades)

config/
  strategy.yaml                   — signal_copy: section added

data/
  signal_listener.session          — Telethon auth session (gitignored)
  signal_journal.db                — SQLite trade + message log

tests/
  test_signal_parser.py            — AI parsing tests (mock LLM responses)
  test_signal_validator.py         — Validation rule tests
  test_signal_engine.py            — Full cycle integration tests
  test_signal_position.py          — TP scaling + partial close tests
```

---

## Dependencies

Add to `requirements.txt`:

```
telethon>=1.37.0    # Telegram MTProto client for channel reading
```

No additional LLM library needed — all AI calls use plain `http.client` (same as existing Telegram HTTP helpers).

---

## Build Order

1. `src/signals/__init__.py` — package setup
2. `src/signals/channel_listener.py` — Telethon listener (test: connect, print channel messages)
3. `src/signals/signal_parser.py` — AI parser (test: mock LLM, parse sample messages)
4. `src/signals/signal_validator.py` — validation rules (test: valid/invalid signal scenarios)
5. `src/signals/signal_risk.py` — risk guard (test: daily limits, confidence sizing)
6. `src/signals/signal_position.py` — position manager (test: TP scaling, partial closes)
7. `src/signals/signal_journal.py` — SQLite journal (test: log message, log trade, query)
8. `src/signals/signal_engine.py` — orchestrator (test: full cycle with mocks)
9. Modify `capital_manager.py` — add "signal" engine type
10. Modify `strategy.yaml` — add signal_copy config block
11. Modify `.env.example` — add Telethon + AI env vars
12. Modify `ta_grid_trend.py` — wire signal engine into on_tick()
13. Modify `telegram_commands.py` — add /signal_* commands
14. Write tests: `test_signal_parser.py`, `test_signal_validator.py`, `test_signal_engine.py`, `test_signal_position.py`

---

## Safety Notes

> **No SHORT positions.** The engine only executes LONG (BUY) on spot. If the trader says "short" or "sell", the AI parser classifies it as NOT_A_SIGNAL.

> **No leverage.** All trades are spot. If the message mentions "10x" or "leverage", the AI ignores the leverage part and only executes the spot direction.

> **No blind trust.** Every signal goes through validation (R:R check, SL distance, pair availability) BEFORE execution. Bad signals are rejected and logged.

> **Audit trail.** Every raw message + parse result is stored in SQLite, even non-signals. This creates a full audit trail for reviewing the AI parser's accuracy.

> **Capital isolation.** Signal capital is separate from grid/trend/momentum. A bad signal trader cannot drain your grid profits.

> **Manual override.** `/signal_pause` instantly stops all new signal execution. `/signal_close BTC-USDT` force-closes any position.
