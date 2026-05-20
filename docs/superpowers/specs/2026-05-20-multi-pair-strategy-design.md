# Multi-Pair Strategy Design

**Date:** 2026-05-20
**Status:** Approved
**Approach:** A — Multi-Pair Strategy (single container, one strategy instance)

## Problem

The bot currently runs a single trading pair at a time. Switching pairs requires a config change and redeploy. To properly test and diversify, we need to run multiple pairs simultaneously.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Single strategy, per-pair engines | Fits t3.small (2GB RAM), moderate code changes |
| Capital | Shared pool ($5,000) | Capital-efficient, pairs compete for funds |
| Risk | Consolidated | Total portfolio drawdown/daily loss triggers halt |
| Engines | Both grid + trend per pair | Full strategy coverage |
| ML regime | Disabled for now | Requires per-pair training data; indicator signals suffice |
| Config | Global settings, per-pair symbol + step_size | YAGNI — add per-pair tuning later if needed |

## Configuration

Replace `pair` and `step_size` with a `pairs` list in `config/strategy.yaml`:

```yaml
pairs:
  - symbol: "DOGE-USDT"
    step_size: 1
    enabled: true
  - symbol: "ETH-USDT"
    step_size: 0.001
    enabled: true
  - symbol: "BTC-USDT"
    step_size: 0.00001
    enabled: false
  - symbol: "BNB-USDT"
    step_size: 0.01
    enabled: true
  - symbol: "XRP-USDT"
    step_size: 0.1
    enabled: true

exchange: "binance"
timeframe: "1h"
```

All other settings (grid levels, indicator periods, risk limits, trend params) remain global and apply uniformly to every pair.

## Architecture

```
TAGridTrendStrategy
├── Shared
│   ├── Capital pool ($5,000)
│   ├── Risk manager (consolidated)
│   └── Telegram notifier
│
├── Per-Pair State (dict keyed by pair symbol)
│   ├── PairEngine("DOGE-USDT")
│   │   ├── Indicators (BB, RSI, EMA, ATR)
│   │   ├── Grid engine
│   │   └── Trend engine
│   ├── PairEngine("ETH-USDT")
│   └── PairEngine("XRP-USDT")
```

### PairEngine

A dataclass or simple class holding per-pair state:
- `symbol`, `base_asset`, `binance_symbol`, `display_pair`, `step_size`
- Indicator instances: `BollingerBands`, `RSI`, `EMA`, `ATR`
- Grid state: levels, orders, spacing
- Trend state: positions, trailing stops

### Tick Flow

1. `on_tick()` iterates enabled pairs
2. Per pair: fetch mid_price from connector, update indicators
3. Evaluate grid conditions (activate/pause/reactivate)
4. Evaluate trend signals (entry/exit)
5. Before placing orders: check shared capital availability + risk limits
6. Save per-pair state files

### Capital Allocation

- First-come-first-served within each tick
- `max_position_pct` (25%) per pair prevents one pair from consuming all capital
- `capital_state.json` tracks allocation per pair

### Markets Registration

```python
markets[self.exchange] = {pair["symbol"]: {} for pair in self.pairs if pair["enabled"]}
```

Hummingbot natively supports multiple pairs in the markets dict.

## State Persistence

```
data/
├── trend_state_DOGE.json
├── trend_state_ETH.json
├── grid_state_DOGE.json
├── grid_state_ETH.json
└── capital_state.json
```

### capital_state.json

```json
{
  "total_capital": 5000.0,
  "grid_allocated": {"DOGE-USDT": 500, "ETH-USDT": 1200},
  "trend_allocated": {"DOGE-USDT": 250, "ETH-USDT": 500},
  "available": 2550.0
}
```

On startup: read all pair state files, sum allocations, recover positions. Orphaned state files (pair removed from config) are preserved but not loaded.

## Telegram Commands

| Command | Change |
|---------|--------|
| `/status` | Show all pairs' grid state in one message |
| `/trend_status` | Show all pairs' trend positions |
| `/daily_report` | Consolidated P&L across all pairs |
| Trade alerts | Already pair-aware via `display_pair` |
| `/start` | Lists all active pairs |

## Dashboard (Streamlit)

- Pair selector dropdown at top
- Per-pair charts and grid visualization
- Combined portfolio view with total P&L

## ML Regime Classifier

Disabled for multi-pair launch. The current model trains on single-pair features. Indicator-based signals (RSI, EMA-200, Bollinger) work per-pair with no extra work. Per-pair ML models can be added later.

## Risk Management

Consolidated across all pairs:
- `max_drawdown_pct`: Total portfolio drops 10% from peak → all pairs halt
- `daily_loss_limit_pct`: Combined daily loss hits 5% → all pairs halt
- `max_base_exposure_pct`: 80% max across all positions

## Files to Modify

| File | Change |
|------|--------|
| `config/strategy.yaml` | Replace `pair`/`step_size` with `pairs` list |
| `hummingbot_files/scripts/ta_grid_trend.py` | Major: add PairEngine, multi-pair tick loop, shared capital |
| `hummingbot_files/scripts/ta_grid_btcusdt.py` | Same changes as trend script |
| `telegram_bot/bot.py` | Update `/status`, `/trend_status`, `/daily_report` for multi-pair |
| `app.py` (dashboard) | Add pair selector, per-pair views |
| `docker-entrypoint.sh` | No change needed |
| `docker-compose.yml` | No change needed |

## Out of Scope

- Per-pair indicator tuning (global settings suffice)
- Per-pair ML models
- Multiple exchanges
- Dynamic pair add/remove without restart
