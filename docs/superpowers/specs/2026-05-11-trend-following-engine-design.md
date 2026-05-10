# Trend-Following Engine Design Spec

**Date:** 2026-05-11
**Status:** Draft
**Author:** User + Claude

## Overview

Add a trend-following trading engine to the existing grid trading bot. The trend engine runs alongside the grid engine inside the same Hummingbot v2 container with full capital and state isolation. The grid bot code (`ta_grid_btcusdt.py`) is NOT modified — it is imported as-is.

## Architecture

```
ta_grid_trend.py (new strategy entry point, replaces ta_grid_btcusdt.py as active script)
├── Grid Engine (imports TAGridSOLUSDT from ta_grid_btcusdt.py — ZERO changes)
│   ├── Own capital pool (set via /capital)
│   ├── Own order tracker
│   └── Own trade journal (grid_trades table)
│
└── Trend Engine (new code in src/trend/)
    ├── trend_manager.py — signal scoring, entry/exit logic
    ├── position_manager.py — open positions, stop-loss, trailing stop
    ├── candlestick_patterns.py — bullish/bearish pattern detection
    ├── support_resistance.py — S/R level detection for smart stop-loss
    ├── Own capital pool (set via /trend_capital)
    ├── Own position tracking
    └── Own trade journal (trend_trades table)
```

Both engines run inside `on_tick()` independently. They never share orders, positions, or capital.

## Signal Scoring System

The trend engine does NOT enter on a single indicator. It scores bull signals. Trade opens only when bull score ≥ 3.

### Score Contributions

| Signal | Condition | Bull Points |
|---|---|---|
| **EMA Cross** | EMA 20 crosses above EMA 50 | +1 |
| **Trend Filter** | Price > EMA 200 AND EMA 20 > EMA 50 | +1 |
| **RSI Confirmation** | RSI between 40-70 | +1 |
| **At Support** | Price within 1% of detected support level | +2 |
| **Bullish Candle** | Bullish candlestick pattern detected | +2 |

**Maximum possible score: 7 points**
**Entry threshold: ≥ 3 bull points**

### Indicator Details

**Moving Averages (already available in src/indicators/ema.py):**
- EMA 20 (fast)
- EMA 50 (slow)
- EMA 200 (trend filter)
- Crossover detected by comparing current vs previous tick EMA values

**RSI (already available in src/indicators/rsi.py):**
- Period: 14 (default)
- Filter zone: 40-70 (not overbought, not oversold)

**Support/Resistance (new module):**
- Scan last 50 candles for swing highs and swing lows
- Cluster nearby pivots within 0.5% into single levels
- A level is valid only if touched ≥ 2 times
- "At support" = price within 1% of a support level

**Candlestick Patterns (new module):**
- Detect on last 3 candles
- Bullish patterns scored (+2):
  - Single: Hammer, Bullish Marubozu
  - Double: Bullish Engulfing, Bullish Harami
  - Triple: Morning Star
- Start with 5 patterns. Expand later.

## Entry Logic

1. Calculate all signal scores on each tick
2. If bull_score ≥ 3 AND no open trend position on this pair → enter LONG
3. Require 2-tick confirmation (signal holds for ~2 minutes)
4. Max 2 open trend positions at once
5. If already in a trend position, skip (no pyramiding)

**Position sizing:**
```
position_size = (trend_capital × 0.02) / stop_loss_distance_pct
Cap: never exceed 25% of trend_capital on a single trade
```

Example: $2k capital, 3% stop distance = ($2000 × 0.02) / 0.03 = $1,333 per trade (~14 SOL at $94)

## Exit Logic

| Exit Type | Trigger | Priority |
|---|---|---|
| **Take-profit** | Entry price + 2× risk distance (2:1 R:R) | - |
| **Trailing stop** | Moves SL up by 1.5% as price moves in favor | Activates after +1.5% profit |
| **Signal exit** | Bull score drops below 2 | Monitored each tick |
| **Hard stop-loss** | Below nearest support level - 0.2% buffer | Set at entry, never moves down |

**Stop-loss placement:**
- Find nearest support level below entry price
- SL = support level - 0.2% buffer
- Fallback: if no support found, use entry - 2× ATR

**Take-profit calculation:**
```
risk_distance = entry_price - stop_loss_price
take_profit = entry_price + (risk_distance × 2.0)
```

## Risk Management

**Per-trade risk:**
- 2% of trend capital per trade
- Max 25% of trend capital in a single position

**Portfolio limits:**
- Max 2 open trend positions
- Max 10% drawdown from trend capital peak → pause trend engine (reuse CircuitBreaker)
- Daily loss limit: 5% of trend capital → halt for the day

**Capital isolation:**
- Grid pool: set by `/capital` (currently $5k)
- Trend pool: set by `/trend_capital` (e.g. $2k)
- On startup: validate grid_capital + trend_capital ≤ available balance
- If insufficient balance, log warning and use available funds proportionally

## State Management

**Trend state file:** `data/trend_state.json`
```json
{
  "trend_capital": 2000.0,
  "peak_capital": 2000.0,
  "open_positions": [
    {
      "entry_order_id": "abc123",
      "entry_price": 94.20,
      "amount": 14.15,
      "stop_loss": 91.30,
      "trailing_stop": 91.30,
      "take_profit": 100.00,
      "entry_time": "2026-05-11T10:30:00Z",
      "current_sl_distance_pct": 3.08
    }
  ],
  "prev_ema_fast": 93.10,
  "prev_ema_slow": 93.05,
  "signal_state": "confirming",
  "pending_signal_ticks": 1,
  "daily_pnl": -15.30,
  "daily_loss_limit_hit": false
}
```

**Grid state:** `data/grid_state.json` — unchanged, managed by existing grid code.

**Trade journal:** New `trend_trades` table
```
id, timestamp, side, amount, price, fee, pnl, pnl_pct,
entry_price, stop_loss, take_profit, exit_reason,
signal_score, duration_minutes
```

Exit reasons: `take_profit`, `trailing_stop`, `signal_exit`, `stop_loss`, `manual_close`

## Telegram Commands

**New commands (all prefixed `/trend_`):**

| Command | Description |
|---|---|
| `/trend_status` | Active positions, EMA values, current bull score, signal state |
| `/trend_capital <amount>` | Set trend capital (e.g. `/trend_capital 2000`) |
| `/trend_pnl` | P&L summary: total trades, win rate, total P&L, avg win/loss |
| `/trend_close` | Manually close all open trend positions at market |
| `/trend_history` | Last 10 trend trades with entry/exit details |

**Notifications (same bot, same chat):**
- `TREND BUY FILLED: 14 SOL @ $94.20 | SL: $91.30 | TP: $100.00 | Score: 4/7`
- `TREND SELL FILLED (take_profit): 14 SOL @ $100.00 | +$81.20 (+6.1%)`
- `TREND SELL FILLED (stop_loss): 14 SOL @ $91.30 | -$40.80 (-3.1%)`
- `TREND: Signal forming | Bull score: 3/7 | EMA20=93.4 > EMA50=93.1 | Confirming (1/2)...`
- `TREND: Stopped — daily loss limit reached (-$100)`

**Grid bot notifications:** unchanged, continue working as before.

## Performance Metrics

Logged every 10 cycles and available via `/trend_pnl`:
- Total trades
- Win rate %
- Total P&L (USDT)
- Average win / average loss
- Profit factor (gross wins / gross losses)
- Largest win / largest loss
- Average hold duration

## File Structure

### New Files

```
src/trend/
├── __init__.py
├── trend_manager.py           # Signal scoring, entry/exit decisions
├── position_manager.py        # Position tracking, SL/TP/trailing management
├── candlestick_patterns.py    # Bullish/bearish pattern detection
└── support_resistance.py      # S/R level detection and clustering

hummingbot_files/scripts/
└── ta_grid_trend.py           # New dual-engine strategy entry point

hummingbot_files/conf/scripts/
└── ta_grid_trend_conf.yml     # V2 config pointing to new strategy
```

### Modified Files

```
src/notifications/telegram_commands.py  # Add /trend_* command handlers
docker-entrypoint.sh                    # Change SCRIPT_CONFIG to ta_grid_trend_conf.yml
```

### Files NOT Modified

```
hummingbot_files/scripts/ta_grid_btcusdt.py  # ZERO changes
src/grid/                                      # ZERO changes
src/indicators/                                # Reused as-is
src/risk/circuit_breaker.py                    # Reused as-is
src/journal/trade_journal.py                   # Reused as-is (trend engine creates its own table)
```

### Reused Modules (import only)

- `src/indicators/ema.py` — EMA 20, 50, 200 calculation
- `src/indicators/rsi.py` — RSI confirmation filter
- `src/indicators/atr.py` — Fallback stop-loss distance
- `src/indicators/bollinger_bands.py` — Volatility context
- `src/risk/circuit_breaker.py` — Drawdown/daily loss protection
- `src/journal/trade_journal.py` — Trade logging
- `src/notifications/` — Telegram alerts

## Config Additions to `config/strategy.yaml`

```yaml
trend:
  enabled: true
  capital: 2000
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  rsi_period: 14
  rsi_min: 40
  rsi_max: 70
  min_signal_score: 3
  confirmation_ticks: 2
  risk_per_trade_pct: 2.0
  max_position_pct: 25.0
  max_positions: 2
  trailing_stop_pct: 1.5
  trailing_activation_pct: 1.5
  rr_ratio: 2.0
  sl_buffer_pct: 0.2
  max_drawdown_pct: 10.0
  daily_loss_limit_pct: 5.0
  timeframe: "1h"
```

## Docker Deployment

- Same EC2 instance (t3.small)
- Same Docker container — `docker-compose.yml` unchanged
- `docker-entrypoint.sh` updated to point to `ta_grid_trend_conf.yml`
- GitHub Actions deploy pipeline unchanged (auto-deploys on push to main)

## Testing Plan

1. **Unit tests** for each new module:
   - `test_candlestick_patterns.py` — pattern detection with known candle data
   - `test_support_resistance.py` — level detection and clustering
   - `test_trend_manager.py` — signal scoring logic
   - `test_position_manager.py` — SL/TP/trailing calculations

2. **Integration test** — simulate 30 days of candle data, verify entry/exit logic

3. **Paper trading** — run alongside grid bot for 30 days before committing real capital

## Migration Path

1. Deploy new strategy alongside grid engine
2. Set `/trend_capital 0` initially (trend engine inactive, grid runs normally)
3. Set `/trend_capital 100` for micro paper testing
4. Monitor via `/trend_status` and `/trend_pnl`
5. Scale up capital once confidence established
