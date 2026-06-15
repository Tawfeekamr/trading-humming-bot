# Reversal Swing Bot — Deep Implementation Plan

**Date:** 2026-06-15
**Status:** Draft / Pending Approval
**Scope:** `trading-engine-core` (Rust)

---

## 1. Architecture Overview

The **Reversal Swing Bot** runs as a compiled Rust strategy (`SwingStrategy`) inside `trading-engine-core`. It leverages a Multi-Timeframe (MTF) architecture:
- **Higher Timeframe (HTF)** (e.g., 1h/4h) is used for regime identification (ADX with hysteresis) and boundary location (Donchian Channel).
- **Lower Timeframe (LTF)** (e.g., 5m) is used for entry execution triggers (candlestick reversal patterns + volume spikes).

```
LTF Tick Event
  │
  ├──► Update LTF Indicators (ATR, Volume SMA, Candlestick Reversal)
  │
  ├──► Is new HTF candle closed?
  │      └──► Yes: Update HTF Indicators (ADX, Donchian, RSI, MACD, Divergence)
  │      └──► No: Use cached values from last closed HTF candle (anti-repainting)
  │
  ├──► Evaluate Gates & Score
  │      ├──► Hard Gates (Location + Trigger Candle + Volume) -> Pass?
  │      └──► Booster Score (RSI + MACD + Divergence) -> >= 2 Points?
  │
  └──► Position Management & Exits
         ├──► Stop Loss (ATR-based)
         └──► Take Profit (HTF Midline scale-out + RUNNER_EXIT mode)
```

---

## 2. Component Specifications

### 2.1 Monotonic Deque Donchian Channel
Create `src/indicators/donchian.rs` implementing `DonchianChannel` using monotonic double-ended queues (`VecDeque`).
- **Time Complexity**: $O(1)$ amortized per update.
- **Space Complexity**: $O(N)$ where $N$ is the lookback period.
- **Deques**:
  - `max_deque`: Tracks indices of candidates for maximum high. Elements are stored in descending order of value.
  - `min_deque`: Tracks indices of candidates for minimum low. Elements are stored in ascending order of value.

### 2.2 Reversal Swing Strategy (`SwingStrategy`)
Create `src/strategy/swing.rs` implementing `Strategy` trait.

#### Config Overrides:
1. **RUNNER_EXIT Enum**:
   ```rust
   #[derive(Debug, Clone, Copy, Serialize, Deserialize)]
   pub enum RunnerExitMode {
       OppositeBand,
       ChandelierOnly,
       BandOrChandelier,
   }
   ```
2. **Capital-relative Position Sizing**:
   Calculate trade size from `self.current_capital()` (compound/realized allocator pool) rather than the raw connector balance.
   `qty = (self.current_capital() * RISK_PER_TRADE) / sl_distance`
3. **Startup Balance Reconciliation**:
   At startup (`on_start`), fetch the actual exchange balance for base/quote coins and align the strategy's internal position state. If exchange balance is zero, clear local position. If exchange has balance but state file is missing, construct an optimistic long position at current market price.

### 2.3 SQLite Journal
Create `src/strategy/swing_journal.rs` implementing `SwingJournal`.
- DB File: `data/swing_journal.db`
- Table: `swing_trades` containing fields: `pair`, `side`, `entry_price`, `exit_price`, `quantity`, `pnl`, `exit_reason`, `duration_mins`, `runner_exit_mode`.

---

## 3. Implementation Checklist

### Phase 1: Donchian Indicator
- [ ] Implement `src/indicators/donchian.rs`.
- [ ] Add unit test verifying lookback and max/min correctness.
- [ ] Register mod in `src/indicators/mod.rs`.

### Phase 2: Configuration & Strategy Scaffold
- [ ] Define `SwingConfig` in `src/config.rs` including `RunnerExitMode` and HTF/LTF periods.
- [ ] Create `src/strategy/swing_journal.rs`.
- [ ] Create `src/strategy/swing.rs` structure and registration in `src/strategy/mod.rs`.

### Phase 3: MTF & Logic Implementation
- [ ] Implement MTF updates ensuring HTF indicators only update on completed HTF bars.
- [ ] Implement hard gates and scoring rules.
- [ ] Implement capital-based position sizing.
- [ ] Implement `RUNNER_EXIT` exits and ATR stop loss.
- [ ] Implement startup balance reconciliation.

### Phase 4: Backtest Harness
- [ ] Implement `src/bin/backtest_swing.rs` to replay CSV data.
- [ ] Verify closed HTF-candle logic matches production execution.
- [ ] Run comparison tests on `RUNNER_EXIT` options.
