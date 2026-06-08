# Trend Strategy — Unified Scoring System

## Overview

The trend strategy uses a **weighted scoring system (0–9)** to evaluate entry quality. Each indicator contributes points based on how strongly it confirms a trend. An entry is taken when the total score meets or exceeds `entry_score_threshold` (default: 5) AND the EMA direction is clearly Up.

This replaces the previous binary gate system where a single bad indicator (e.g., CHOP slightly above threshold) could block an otherwise strong entry.

## Score Components

| Component | Max Points | Indicator | What It Measures |
|---|---|---|---|
| **ADX** | 3 | Trend strength | How strong is the directional move? |
| **CHOP** | 2 | Trend quality | How clean is the trend (vs choppy noise)? |
| **Volume** | 2 | Participation | Is the market actively supporting this move? |
| **MACD** | 1 | Momentum | Is momentum aligned with the trend direction? |
| **RSI** | 1 | Timing | Is RSI in a favorable zone (not overbought)? |
| **Total** | **9** | | |

## Scoring Rules

### ADX — Trend Strength (0–3)

ADX measures trend strength regardless of direction.

| ADX Value | Score | Meaning |
|---|---|---|
| > 50 | 3 | Strong trend |
| > 30 | 2 | Moderate trend |
| > 20 | 1 | Weak trend |
| ≤ 20 | 0 | No trend / ranging |

### CHOP — Choppiness Index (0–2)

Lower CHOP = cleaner trend. Higher CHOP = choppy/ranging.

| CHOP Value | Score | Meaning |
|---|---|---|
| < 30 | 2 | Clean, directional move |
| < 50 | 1 | Acceptable — some noise but trending |
| ≥ 50 | 0 | Too choppy — likely ranging |

### Volume — Participation (0–2)

Volume ratio = current bar volume / 20-bar SMA of volume.

| Volume Ratio | Score | Meaning |
|---|---|---|
| > 1.5 | 2 | High participation — strong conviction |
| > 0.9 | 1 | Normal — adequate participation |
| ≤ 0.9 | 0 | Low — weak / off-hours |

### MACD — Momentum Alignment (0–1)

MACD histogram sign matches trend direction.

| Condition | Score |
|---|---|
| Histogram aligned with direction | 1 |
| Not aligned | 0 |

### RSI — Entry Timing (0–1)

RSI confirms the entry is not at an extreme.

| Condition | Score |
|---|---|
| Longs: RSI < 65 | 1 |
| Shorts: RSI > 35 | 1 |
| Otherwise | 0 |

## Entry Rule

```
ENTER when: score >= entry_score_threshold AND direction == Up
```

- `direction == Up` is a hard requirement (EMA fast > slow AND price > slow)
- `entry_score_threshold` is configurable (default: 5)

## Examples

### BNB-USDT (Strong trend, mild chop, low volume)
```
ADX = 68 → 3 (strong)
CHOP = 51 → 1 (acceptable)
Volume = 0.71 → 0 (low)
MACD = aligned → 1
RSI = 60.5 → 1 (favorable)

Total: 6/9 → ENTER ✅ (6 >= 5)
```

### ETH-USDT (Strong clean trend)
```
ADX = 91 → 3 (strong)
CHOP = 33 → 2 (clean)
Volume = 1.3 → 1 (normal)
MACD = aligned → 1
RSI = 58 → 1 (favorable)

Total: 8/9 → ENTER ✅ (8 >= 5)
```

### XRP-USDT (No trend)
```
ADX = 6.3 → 0 (no trend)
CHOP = 48 → 1 (acceptable)
Volume = 0.5 → 0 (low)
MACD = aligned → 1
RSI = 58 → 1 (favorable)

Total: 3/9 → NO ENTRY ❌ (3 < 5)
```

### DOGE-USDT (Clean trend)
```
ADX = 93 → 3 (strong)
CHOP = 16 → 2 (very clean)
Volume = 1.8 → 2 (high)
MACD = aligned → 1
RSI = 55 → 1 (favorable)

Total: 9/9 → ENTER ✅ (9 >= 5)
```

## Configuration

```yaml
trend:
  entry_score_threshold: 5   # Min score (0-9) to enter
  rsi_long_max: 65           # RSI threshold for timing point
```

## Status Display

The `/status` Telegram command shows the score breakdown:

```
Score:6/5 (A:3 C:1 V:0 M:1 R:1) | dir:+1 | ADX=68 CHOP=51 RSI=60 | Score 6<5
```

Where: `A`=ADX, `C`=CHOP, `V`=Volume, `M`=MACD, `R`=RSI

## Why Scoring Beats Binary Gates

**Before (binary):**
- ADX=68 ✅ but CHOP=51 ❌ → entire entry blocked
- Volume < 1.2 → mandatory block regardless of other signals
- No partial credit — one bad number kills everything

**After (scoring):**
- ADX=68 gives 3 points → strong foundation
- CHOP=51 gives 1 point → partial credit (not zero)
- Volume=0 gives 0 points → penalized but not fatal
- MACD + RSI add 2 more → total 6, enough to enter
- **Result:** valid entries that binary gates would have missed

## Exit Logic (unchanged)

Exit conditions remain independent of the scoring system:
1. Stop loss hit (2× ATR from entry)
2. Take profit levels (TP1 at 1R, TP2 at 1.5R, TP3 at 2R)
3. Trailing stop (Chandelier Exit: 2.5× ATR)
4. ADX dying (drops below 20)
5. Direction flip (EMA cross reverses)
