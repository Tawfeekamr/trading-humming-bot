# Image Signal Parser Design

**Date:** 2026-06-12
**Status:** Approved
**Scope:** Extend existing Signal Copy Engine to parse trading signals from Telegram images (TradingView charts)

## Problem

A Telegram trading channel sends signals as TradingView chart images with Entry/SL/TP annotations. The existing Signal Copy Engine only handles text messages — images are silently ignored. We need to extract structured trading signals from these images and route them through the existing execution pipeline.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| AI Provider | DeepSeek Vision (`deepseek-chat`) | Same API key already configured. Supports vision via chat completions API. ~$0.001-0.003/image. |
| Architecture | Extend existing SignalParser | Reuses all validation, risk, execution, journaling. Minimal new code (~205 lines). |
| Channel | Separate Telegram channel via new Telethon listener | Image signals come from a different channel than text signals. |
| Quality Check | Sanity validation only | Valid pair, reasonable SL/TP, sane R:R, price near market. No technical confluence scoring. |
| Execution | Shares existing signal engine capital pool ($1,000 / 3 positions) | Same risk budget, same position tracking, same Telegram commands. |
| Image Format | Consistent TradingView charts with Entry/SL/TP lines | Single extraction prompt works for all signals from this channel. |
| Per-channel control | Enable/disable per image channel via config + Telegram commands | Operator can toggle image parsing without redeploying. |

## Data Flow

```
Telegram Channel B (images)
        |
        v
ImageChannelListener          NEW - downloads .jpg via Telethon
        |
        | bytes (base64)
        v
SignalParser.parse_image()    NEW - DeepSeek Vision extraction
        |
        | ParsedSignal {pair, direction, entry, sl, tp1, tp2, confidence}
        v
SignalValidator.sanity_check()  NEW - image-specific sanity checks
        |
   PASS / REJECT
        |
   PASS v
SignalEngine.execute()        EXISTING - unchanged
  -> position sizing -> risk check -> place order
        |
        v
SignalJournal.log()           EXISTING - logs source as "image:channel_name"
Telegram notification         EXISTING - marks as "Image Signal"
```

## Configuration

New `image_channels` section in `config/strategy.yaml` under `signals:`:

```yaml
signals:
  # Existing text channels
  channels:
    - name: "primary_text_channel"
      channel_id: "-1001234567890"
      enabled: true
      parse_images: false

  # New image channels
  image_channels:
    - name: "tradingview_signals"
      channel_id: "-1009876543210"
      enabled: true                # master on/off for this channel
      parse_images: true           # enable image parsing
      min_confidence: 0.6          # reject if AI confidence < 60%
      max_signals_per_day: 10      # rate limit per channel

    - name: "backup_signals"
      channel_id: "-1005555555555"
      enabled: false               # disabled, images ignored
      parse_images: true
```

### Telegram Commands

- `/signal_channels` — shows all channels including image channels with status
- `/signal_images on <name>` — enable image parsing for a specific channel
- `/signal_images off <name>` — disable image parsing for a specific channel

## Components

### 1. ImageChannelListener (NEW)

**File:** `src/signals/image_channel_listener.py` (~80 lines)

**Responsibilities:**
- Connect to Telegram via Telethon (reuse existing client setup from `channel_listener.py`)
- Listen for new photo messages in configured image channels
- Download image to bytes buffer (no disk writes)
- Skip non-image messages (text, stickers, forwards without media)
- Forward image bytes + channel metadata to `SignalParser.parse_image()`
- Rate limiting: max N images/day per channel (configurable)
- Respect per-channel `enabled` flag

**NOT responsible for:**
- Parsing the image (SignalParser)
- Validating the signal (SignalValidator)
- Executing trades (SignalEngine)

**Dependencies:**
- `telethon` (already in requirements.txt)
- `SignalParser.parse_image()` for extraction
- Config from `strategy.yaml` image_channels section

### 2. SignalParser.parse_image() (EXTEND)

**File:** `src/signals/signal_parser.py` (+50 lines)

**Purpose:** Send image to DeepSeek Vision API, extract structured signal.

**Input:** `image_bytes: bytes`, `channel_name: str`
**Output:** `ParsedSignal` (same dataclass as text parsing)

**Process:**
1. Base64 encode the image bytes
2. Build DeepSeek Vision API request:
   - Endpoint: `POST https://api.deepseek.com/chat/completions`
   - Model: `deepseek-chat` (supports vision)
   - Auth: same `DEEPSEEK_API_KEY` env var
   - Messages: system prompt + user message with image content
   - Temperature: 0.1
3. Parse JSON response into `ParsedSignal` struct
4. Set `source = f"image:{channel_name}"` for audit trail

**Vision extraction prompt:**
```
You are a trading signal parser. Analyze this TradingView chart image
and extract the trading signal. Return ONLY valid JSON:
{
  "pair": "SYMBOL/USDT",
  "direction": "LONG" or "SHORT",
  "entry": float,
  "stop_loss": float,
  "take_profits": [float, ...],
  "timeframe": "1H/4H/1D",
  "confidence": 0.0-1.0
}
If no clear signal is found, return: {"error": "no_signal_found", "reason": "..."}
```

**Error cases:**
- DeepSeek returns non-JSON → log error, skip signal
- `error` field in response → log reason, skip signal
- API timeout (30s) → retry 1x, then skip
- API 5xx → retry 1x, then skip

### 3. Sanity Validation (EXTEND)

**File:** `src/signals/signal_validator.py` (+30 lines)

**Checks performed on image-parsed signals before execution:**

| Check | Rule | Rejection Reason |
|-------|------|------------------|
| Pair exists | Pair is tradeable on Binance | "CAKE/USDT not available on exchange" |
| Entry near market | Entry within 5% of current mid price | "ETH entry $1671 but market at $1720" |
| SL distance | Stop loss 0.5-15% from entry | "SL too tight (0.1%) / too wide (20%)" |
| TP exists | At least 1 take profit level | "No take profit levels found" |
| TP direction | LONG: TP > entry, SHORT: TP < entry | "TP $1500 below LONG entry $1671" |
| R:R ratio | Risk:Reward >= 1:1 | "R:R 0.5:1 below minimum 1:1" |
| Confidence | AI confidence >= min_confidence from config | "Low confidence (0.4), skipping" |

### 4. SignalEngine Wiring (EXTEND)

**File:** `src/signals/signal_engine.py` (+20 lines)

**Changes:**
- Initialize `ImageChannelListener` for each enabled image channel in config
- Start image listeners alongside existing text channel listeners
- Route parsed signals through existing `validate()` → `execute()` pipeline
- On startup, log: `Image channels: [tradingview_signals (enabled), backup_signals (disabled)]`

### 5. Telegram Commands (EXTEND)

**File:** `src/notifications/telegram_commands.py` (+15 lines)

**New command:** `/signal_images <on|off> <channel_name>`
- Toggles `enabled` field in runtime config for the named image channel
- Persists to config file so it survives restarts
- Response: `Image signals [enabled/disabled] for tradingview_signals`

**Updated command:** `/signal_channels`
- Now includes image channels with status emoji: `📷 tradingview_signals (enabled)` or `📷 backup_signals (disabled)`

### 6. Config (MODIFY)

**File:** `config/strategy.yaml` (+10 lines)

Add `image_channels` list under `signals:` section with channel credentials and per-channel settings.

## Error Handling

| Scenario | Response | Alert? |
|----------|----------|--------|
| Image unreadable/blurry | Reject with reason | Yes - Telegram |
| DeepSeek API timeout | Retry 1x, skip if still fails | No - log only |
| DeepSeek 5xx error | Retry 1x, skip if still fails | No - log only |
| No signal in image | Log "no signal found" | No - too noisy |
| Invalid pair (not on Binance) | Reject with reason | Yes - Telegram |
| Stale signal (price moved >5%) | Reject with reason | Yes - Telegram |
| Capital pool full (3/3 positions) | Skip with reason | Yes - Telegram |
| Rate limit hit (max/day/channel) | Skip silently | No - log only |
| AI confidence < threshold | Reject with reason | Yes - Telegram |

**Principle:** Never silently execute a bad or ambiguous signal. Always prefer skipping over losing money on a misread image.

## File Changes Summary

```
NEW FILES:
  src/signals/image_channel_listener.py     (~80 lines)

MODIFIED FILES:
  src/signals/signal_parser.py              (+50 lines - parse_image method)
  src/signals/signal_validator.py           (+30 lines - image sanity checks)
  src/signals/signal_engine.py              (+20 lines - wire up image listener)
  src/notifications/telegram_commands.py    (+15 lines - /signal_images command)
  config/strategy.yaml                      (+10 lines - image_channels config)

TOTAL: ~205 lines of new/modified code
```

## What Does NOT Change

- Rust trading engine — no changes
- Order execution layer — no changes
- Existing text signal parsing — no changes
- Signal journal schema — no changes (source field distinguishes "image:channel" vs "text:channel")
- Risk management rules — no changes
- Capital allocation — no changes (shared pool)
- Database — no schema changes

## Testing Plan

1. **Unit test: parse_image()** — mock DeepSeek Vision response, verify ParsedSignal extraction
2. **Unit test: sanity validation** — test each rejection rule with edge cases
3. **Integration test: end-to-end** — send test image via Telegram, verify signal appears in journal
4. **Manual test: real image** — send the CAKE/USDT example image, verify correct extraction
5. **Manual test: bad image** — send a non-chart image, verify graceful rejection

## Out of Scope

- Technical confluence scoring (may add later)
- Multi-channel deduplication (same signal from text + image)
- Image storage/archival (images are processed in-memory, not saved)
- Support for non-TradingView chart formats
