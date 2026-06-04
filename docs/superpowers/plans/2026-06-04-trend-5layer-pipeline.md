# Trend Strategy 5-Layer Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the trend strategy's correlated EMA+RSI scoring with a 5-layer pipeline: ADX gate → direction → volume-mandatory confirmations → ATR trailing exit.

**Architecture:** New MACD indicator + rewrite of trend.rs. Direction framework (+1/-1/0) drives both entry and exit. Long-only execution (dir=-1 exits and blocks new longs). Volume is mandatory for activation (S2 AND (S1 OR S3)).

**Tech Stack:** Rust, existing indicators (ADX, Choppiness, VolumeSma, ATR, EMA, RSI), new MACD indicator, serde for config.

**Spec:** `docs/superpowers/specs/2026-06-04-trend-strategy-redesign.md`

---

### Task 1: MACD Indicator

**Files:**
- Create: `trading-engine-core/src/indicators/macd.rs`
- Modify: `trading-engine-core/src/indicators/mod.rs`

- [ ] **Step 1: Create MACD indicator with tests**

Create `trading-engine-core/src/indicators/macd.rs`:

```rust
/// MACD (Moving Average Convergence Divergence) — standard 12/26/9.
///
/// Initialization requires `slow + signal` bars (~35) so the histogram is stable.
/// The histogram measures *acceleration* of the trend, not just alignment.
#[derive(Debug, Clone)]
pub struct Macd {
    fast_ema: f64,
    slow_ema: f64,
    signal_ema: f64,
    fast_alpha: f64,
    slow_alpha: f64,
    signal_alpha: f64,
    fast_count: u32,
    slow_count: u32,
    signal_count: u32,
    macd_value: f64,
    signal_value: f64,
    histogram_value: f64,
    // Seeding accumulators (SMA over first `period` values)
    fast_sum: f64,
    slow_sum: f64,
    macd_sum: f64,
    initialized: bool,
}

impl Macd {
    pub fn new(fast: u32, slow: u32, signal: u32) -> Self {
        Self {
            fast_ema: 0.0,
            slow_ema: 0.0,
            signal_ema: 0.0,
            fast_alpha: 2.0 / (fast as f64 + 1.0),
            slow_alpha: 2.0 / (slow as f64 + 1.0),
            signal_alpha: 2.0 / (signal as f64 + 1.0),
            fast_count: 0,
            slow_count: 0,
            signal_count: 0,
            macd_value: 0.0,
            signal_value: 0.0,
            histogram_value: 0.0,
            fast_sum: 0.0,
            slow_sum: 0.0,
            macd_sum: 0.0,
            initialized: false,
        }
    }

    pub fn default_12_26_9() -> Self {
        Self::new(12, 26, 9)
    }

    pub fn update(&mut self, price: f64) {
        // Seed fast EMA with SMA over first `fast` values
        self.fast_count += 1;
        if (self.fast_count as usize) <= 12 {
            self.fast_sum += price;
            self.fast_ema = self.fast_sum / self.fast_count as f64;
        } else {
            self.fast_ema = self.fast_alpha * (price - self.fast_ema) + self.fast_ema;
        }

        // Seed slow EMA with SMA over first `slow` values
        self.slow_count += 1;
        if (self.slow_count as usize) <= 26 {
            self.slow_sum += price;
            self.slow_ema = self.slow_sum / self.slow_count as f64;
        } else {
            self.slow_ema = self.slow_alpha * (price - self.slow_ema) + self.slow_ema;
        }

        // MACD line = fast EMA - slow EMA
        self.macd_value = self.fast_ema - self.slow_ema;

        // Signal line = EMA of MACD line (seed with SMA over first `signal` values)
        if self.slow_count >= 26 {
            self.signal_count += 1;
            if self.signal_count == 1 {
                // Start signal accumulator
                self.macd_sum = self.macd_value;
            } else if (self.signal_count as usize) <= 9 {
                self.macd_sum += self.macd_value;
                self.signal_ema = self.macd_sum / self.signal_count as f64;
            } else {
                self.signal_ema = self.signal_alpha * (self.macd_value - self.signal_ema) + self.signal_ema;
            }
        }

        self.signal_value = self.signal_ema;
        self.histogram_value = self.macd_value - self.signal_value;

        // Initialized after slow + signal bars so histogram is stable
        // slow_count starts at 1 and we need signal_count >= 9
        self.initialized = self.signal_count >= 9 && self.slow_count >= 26;
    }

    pub fn macd_line(&self) -> f64 { self.macd_value }
    pub fn signal_line(&self) -> f64 { self.signal_value }
    pub fn histogram(&self) -> f64 { self.histogram_value }
    pub fn is_initialized(&self) -> bool { self.initialized }

    pub fn reset(&mut self) {
        self.fast_ema = 0.0;
        self.slow_ema = 0.0;
        self.signal_ema = 0.0;
        self.fast_count = 0;
        self.slow_count = 0;
        self.signal_count = 0;
        self.macd_value = 0.0;
        self.signal_value = 0.0;
        self.histogram_value = 0.0;
        self.fast_sum = 0.0;
        self.slow_sum = 0.0;
        self.macd_sum = 0.0;
        self.initialized = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_macd_not_initialized_before_35_bars() {
        let mut macd = Macd::new(12, 26, 9);
        for i in 0..34 {
            macd.update(100.0 + i as f64 * 0.5);
        }
        assert!(!macd.is_initialized());
        macd.update(117.0);
        assert!(macd.is_initialized());
    }

    #[test]
    fn test_macd_uptrend_positive_histogram() {
        let mut macd = Macd::new(12, 26, 9);
        // Strong uptrend
        for i in 0..50 {
            macd.update(100.0 + i as f64 * 2.0);
        }
        assert!(macd.is_initialized());
        assert!(macd.histogram() > 0.0, "Histogram should be positive in uptrend, got {}", macd.histogram());
        assert!(macd.macd_line() > macd.signal_line());
    }

    #[test]
    fn test_macd_downtrend_negative_histogram() {
        let mut macd = Macd::new(12, 26, 9);
        // Strong downtrend
        for i in 0..50 {
            macd.update(200.0 - i as f64 * 2.0);
        }
        assert!(macd.is_initialized());
        assert!(macd.histogram() < 0.0, "Histogram should be negative in downtrend, got {}", macd.histogram());
    }

    #[test]
    fn test_macd_reset() {
        let mut macd = Macd::new(12, 26, 9);
        for i in 0..40 {
            macd.update(100.0 + i as f64);
        }
        assert!(macd.is_initialized());
        macd.reset();
        assert!(!macd.is_initialized());
    }
}
```

- [ ] **Step 2: Register MACD in indicators/mod.rs**

Add to `trading-engine-core/src/indicators/mod.rs`:
- Add `pub mod macd;` to the module declarations
- Add `pub use macd::Macd;` to the public exports

- [ ] **Step 3: Run tests**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib indicators::macd`
Expected: All 4 MACD tests PASS

- [ ] **Step 4: Commit**

```bash
git add trading-engine-core/src/indicators/macd.rs trading-engine-core/src/indicators/mod.rs
git commit -m "feat: add MACD indicator with SMA seeding and stable histogram init"
```

---

### Task 2: Update TrendConfig

**Files:**
- Modify: `trading-engine-core/src/config.rs` (TrendConfig struct, lines 111-147)

- [ ] **Step 1: Add new fields to TrendConfig**

Add these fields to the `TrendConfig` struct (after line 146, before the closing `}`):

```rust
    // 5-layer pipeline thresholds
    #[serde(default = "default_25")]
    pub adx_gate_threshold: f64,
    #[serde(default = "default_20")]
    pub adx_exit_threshold: f64,
    #[serde(default = "default_38")]
    pub choppiness_threshold: f64,
    #[serde(default = "default_1_2")]
    pub volume_ratio_threshold: f64,
    #[serde(default = "default_65")]
    pub rsi_long_max: f64,
    #[serde(default = "default_35")]
    pub rsi_short_min: f64,
    #[serde(default = "default_3")]
    pub atr_trailing_mult: f64,
```

Add the default functions near the existing ones (after the `default_10k` function):

```rust
fn default_25() -> f64 { 25.0 }
fn default_20() -> f64 { 20.0 }
fn default_38() -> f64 { 38.0 }
fn default_1_2() -> f64 { 1.2 }
fn default_65() -> f64 { 65.0 }
fn default_35() -> f64 { 35.0 }
fn default_3() -> f64 { 3.0 }
```

- [ ] **Step 2: Verify compilation**

Run: `cargo check --manifest-path trading-engine-core/Cargo.toml`
Expected: Compiles with warnings only (no errors — new fields have defaults)

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/config.rs
git commit -m "feat: add 5-layer pipeline thresholds to TrendConfig"
```

---

### Task 3: Rewrite TrendStrategy — Struct and Indicators

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs` (full rewrite)

This is the core rewrite. The entire file gets replaced. We do it in stages: first the struct + constructor + indicator updates, then the pipeline methods, then on_tick, then status.

- [ ] **Step 1: Replace imports and struct definition**

Replace the top of `trend.rs` (lines 1-100 — everything before `impl TrendStrategy`) with:

```rust
use crate::config::TrendConfig;
use crate::indicators::{Ema, Rsi, Atr, Adx, Choppiness, Macd, VolumeSma};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use async_trait::async_trait;
use anyhow::Result;

/// Direction from EMA cross + price position.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Direction {
    Up,    // +1: EMA fast > slow AND close > slow
    Down,  // -1: EMA fast < slow AND close < slow
    Flat,  //  0: mixed signals
}

/// Spot long-only: dir=Down exits longs and blocks new entries, never shorts.
const TRADE_SHORTS: bool = false;

/// Take-profit level with close percentage
#[derive(Debug, Clone)]
pub struct TpLevel {
    pub price: f64,
    pub close_pct: f64,
    pub filled: bool,
}

/// A trend position with direction-aware trailing stop.
#[derive(Debug, Clone)]
pub struct TrendPosition {
    pub side: OrderSide,
    pub entry_price: f64,
    pub stop_loss: f64,
    pub quantity: f64,
    pub remaining_qty: f64,
    pub trailing_stop: Option<f64>,
    pub highest_since_entry: f64,  // for long trailing
    pub lowest_since_entry: f64,   // for short trailing (future)
    pub tp_levels: Vec<TpLevel>,
}

impl TrendPosition {
    pub fn calculate_tp_levels(entry_price: f64, stop_loss: f64, risk_reward_ratio: f64, runner_pct: f64) -> Vec<TpLevel> {
        let risk = entry_price - stop_loss;
        let tp3_close = if runner_pct > 0.0 { 1.0 - runner_pct } else { 1.0 };
        vec![
            TpLevel { price: entry_price + risk * 1.0, close_pct: 0.33, filled: false },
            TpLevel { price: entry_price + risk * 1.5, close_pct: 0.50, filled: false },
            TpLevel { price: entry_price + risk * risk_reward_ratio, close_pct: tp3_close, filled: false },
        ]
    }
}

pub struct TrendStrategy {
    pair: String,
    config: TrendConfig,

    // Direction indicators
    ema_fast: Ema,
    ema_slow: Ema,
    // Gate indicators
    adx: Adx,
    choppiness: Choppiness,
    // Confirmation indicators
    macd: Macd,
    volume_sma: VolumeSma,
    rsi: Rsi,
    // Exit indicator
    atr: Atr,

    // State
    position: Option<TrendPosition>,

    // Capital tracking
    initial_capital: f64,
    realized_pnl: f64,
}
```

- [ ] **Step 2: Replace constructor and indicator methods**

Replace `impl TrendStrategy` constructor + update methods (everything up to but not including the `#[async_trait]` block) with:

```rust
impl TrendStrategy {
    pub fn new(pair: &str, config: &TrendConfig) -> Self {
        let capital = config.capital;
        Self {
            pair: pair.to_string(),
            config: TrendConfig {
                ema_fast: config.ema_fast,
                ema_slow: config.ema_slow,
                ema_trend: config.ema_trend,
                rsi_period: config.rsi_period,
                rsi_min: config.rsi_min,
                rsi_max: config.rsi_max,
                min_signal_score: config.min_signal_score,
                confirmation_ticks: config.confirmation_ticks,
                risk_reward_ratio: config.risk_reward_ratio,
                capital: config.capital,
                risk_per_trade_pct: config.risk_per_trade_pct,
                max_position_pct: config.max_position_pct,
                trailing_stop_pct: config.trailing_stop_pct,
                trailing_stop_atr_mult: config.trailing_stop_atr_mult,
                trailing_activation_pct: config.trailing_activation_pct,
                exit_signal_threshold: config.exit_signal_threshold,
                sl_buffer_pct: config.sl_buffer_pct,
                adx_gate_threshold: config.adx_gate_threshold,
                adx_exit_threshold: config.adx_exit_threshold,
                choppiness_threshold: config.choppiness_threshold,
                volume_ratio_threshold: config.volume_ratio_threshold,
                rsi_long_max: config.rsi_long_max,
                rsi_short_min: config.rsi_short_min,
                atr_trailing_mult: config.atr_trailing_mult,
            },
            ema_fast: Ema::new(config.ema_fast),
            ema_slow: Ema::new(config.ema_slow),
            adx: Adx::new(14),
            choppiness: Choppiness::new(14),
            macd: Macd::default_12_26_9(),
            volume_sma: VolumeSma::new(20),
            rsi: Rsi::new(config.rsi_period),
            atr: Atr::new(14),
            position: None,
            initial_capital: capital,
            realized_pnl: 0.0,
        }
    }

    pub fn update_indicators(&mut self, bar: &Bar) {
        self.ema_fast.update(bar.close);
        self.ema_slow.update(bar.close);
        self.adx.update_bar(bar.open, bar.high, bar.low, bar.close);
        self.choppiness.update_bar(bar.open, bar.high, bar.low, bar.close, None);
        self.macd.update(bar.close);
        self.volume_sma.update(bar.volume);
        self.rsi.update(bar.close);
        self.atr.update_bar(bar.open, bar.high, bar.low, bar.close);
    }

    /// Global readiness gate — all indicators must be initialized before any pipeline evaluation.
    /// Pre-warmup defaults to WAITING (never to a state that permits entry).
    fn indicators_ready(&self) -> bool {
        self.ema_fast.is_initialized()
            && self.ema_slow.is_initialized()
            && self.adx.is_initialized()
            && self.choppiness.is_initialized()
            && self.macd.is_initialized()
            && self.rsi.is_initialized()
            && self.atr.is_initialized()
            && self.volume_sma.is_initialized()
    }

    // ── 5-Layer Pipeline ─────────────────────────────────────────────

    /// Layer 1: GATE — does a trend exist?
    fn gate(&self) -> bool {
        let adx_thresh = if self.config.adx_gate_threshold > 0.0 { self.config.adx_gate_threshold } else { 25.0 };
        let chop_thresh = if self.config.choppiness_threshold > 0.0 { self.config.choppiness_threshold } else { 38.0 };
        self.adx.adx() > adx_thresh && self.choppiness.value() < chop_thresh
    }

    /// Layer 2: DIRECTION — +1 / -1 / 0
    fn direction(&self, price: f64) -> Direction {
        let ema_fast_val = self.ema_fast.value();
        let ema_slow_val = self.ema_slow.value();
        if ema_fast_val > ema_slow_val && price > ema_slow_val {
            Direction::Up
        } else if ema_fast_val < ema_slow_val && price < ema_slow_val {
            Direction::Down
        } else {
            Direction::Flat
        }
    }

    /// Layer 3: SCORE — S1 (momentum), S2 (participation), S3 (entry timing).
    /// Returns (s1, s2, s3) as bools.
    fn score(&self, dir: Direction) -> (bool, bool, bool) {
        let dir_sign = match dir {
            Direction::Up => 1.0,
            Direction::Down => -1.0,
            Direction::Flat => 0.0,
        };

        // S1: MACD histogram sign matches direction
        let s1 = dir != Direction::Flat && self.macd.histogram().signum() == dir_sign;

        // S2: Volume above threshold (mandatory — best fake-breakout filter)
        let vol_thresh = if self.config.volume_ratio_threshold > 0.0 { self.config.volume_ratio_threshold } else { 1.2 };
        let s2 = self.volume_sma.volume_ratio() > vol_thresh;

        // S3: RSI not chasing
        let rsi_val = self.rsi.value();
        let rsi_long_max = if self.config.rsi_long_max > 0.0 { self.config.rsi_long_max } else { 65.0 };
        let rsi_short_min = if self.config.rsi_short_min > 0.0 { self.config.rsi_short_min } else { 35.0 };
        let s3 = match dir {
            Direction::Up => rsi_val < rsi_long_max,
            Direction::Down => rsi_val > rsi_short_min,
            Direction::Flat => false,
        };

        (s1, s2, s3)
    }

    /// Layer 4: ACTIVATE — trend_exists AND dir==Up AND S2 AND (S1 OR S3).
    /// Long-only: dir=Down never activates (exits + blocks new longs).
    fn should_activate(&self, price: f64) -> bool {
        if !self.gate() { return false; }
        let dir = self.direction(price);
        if TRADE_SHORTS {
            // Future: activate on both directions
            if dir == Direction::Flat { return false; }
        } else {
            // Spot long-only: only activate on Up
            if dir != Direction::Up { return false; }
        }
        let (s1, s2, s3) = self.score(dir);
        s2 && (s1 || s3) // Volume mandatory + one other confirmation
    }

    /// Layer 5: EXIT — ADX dying OR direction flipped OR ATR trailing stop hit.
    fn should_exit(&self, price: f64, entry_dir: Direction) -> (bool, String) {
        let adx_exit = if self.config.adx_exit_threshold > 0.0 { self.config.adx_exit_threshold } else { 20.0 };
        if self.adx.adx() < adx_exit {
            return (true, format!("ADX dying ({:.1}<{:.0})", self.adx.adx(), adx_exit));
        }
        let current_dir = self.direction(price);
        if current_dir != entry_dir && current_dir != Direction::Flat {
            return (true, "Direction flipped".to_string());
        }
        // ATR trailing stop checked in on_tick (needs position state)
        (false, String::new())
    }

    fn calculate_stop_loss(&self, entry_price: f64) -> f64 {
        let atr_val = self.atr.value();
        entry_price - 2.0 * atr_val
    }

    fn calculate_quantity(&self, entry_price: f64, stop_loss: f64) -> f64 {
        let sl_distance = entry_price - stop_loss;
        if sl_distance <= 0.0 { return 0.0; }
        let current_capital = self.config.capital + self.realized_pnl;
        let risk_amount = current_capital * (self.config.risk_per_trade_pct / 100.0);
        let max_position_value = current_capital * (self.config.max_position_pct / 100.0);
        let qty_by_risk = risk_amount / sl_distance;
        let max_qty = max_position_value / entry_price;
        qty_by_risk.min(max_qty)
    }

    pub fn position(&self) -> Option<&TrendPosition> { self.position.as_ref() }
}
```

- [ ] **Step 3: Verify compilation so far**

Run: `cargo check --manifest-path trading-engine-core/Cargo.toml 2>&1 | tail -5`
Expected: May have warnings about unused imports/fields. Fix any errors only.

- [ ] **Step 4: Commit**

```bash
git add trading-engine-core/src/strategy/trend.rs
git commit -m "feat: trend strategy 5-layer pipeline — struct, indicators, gate/direction/score"
```

---

### Task 4: Rewrite on_tick — Entry, Exit, Trailing Stop

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs` (Strategy impl)

- [ ] **Step 1: Replace the Strategy impl block**

Replace the entire `#[async_trait] impl Strategy for TrendStrategy` block with:

```rust
#[async_trait]
impl Strategy for TrendStrategy {
    fn name(&self) -> &str { "trend" }
    fn trading_pair(&self) -> &str { &self.pair }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        let mut orders = Vec::new();

        // Update indicators from recent bars
        for bar in &ctx.recent_bars {
            self.update_indicators(bar);
        }

        if !self.indicators_ready() {
            return Ok(orders);
        }

        let current_price = ctx.order_book.mid_price().unwrap_or_else(|| {
            ctx.recent_bars.last().map(|b| b.close).unwrap_or(0.0)
        });
        if current_price <= 0.0 {
            return Ok(orders);
        }

        // ── If in position: check exits ──
        if let Some(pos) = &mut self.position {
            // Update highest/lowest since entry for trailing stop
            if current_price > pos.highest_since_entry {
                pos.highest_since_entry = current_price;
            }
            if current_price < pos.lowest_since_entry {
                pos.lowest_since_entry = current_price;
            }

            // Stop-loss hit
            if current_price <= pos.stop_loss {
                let sell_qty = pos.remaining_qty;
                let entry = pos.entry_price;
                self.realized_pnl += (current_price - entry) * sell_qty;
                self.position = None;
                orders.push(OrderRequest {
                    symbol: self.pair.clone(),
                    side: OrderSide::Sell,
                    order_type: OrderTypeReq::Limit,
                    price: Some(current_price),
                    quantity: sell_qty,
                    time_in_force: Some(TimeInForceReq::Gtc),
                    client_order_id: None,
                });
                return Ok(orders);
            }

            // TP level hits — partial exits
            for tp in &mut pos.tp_levels {
                if tp.filled { continue; }
                if current_price >= tp.price {
                    let sell_qty = pos.remaining_qty * tp.close_pct;
                    if sell_qty > 0.0 {
                        tp.filled = true;
                        pos.remaining_qty -= sell_qty;
                        self.realized_pnl += (current_price - pos.entry_price) * sell_qty;
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(),
                            side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit,
                            price: Some(current_price),
                            quantity: sell_qty,
                            time_in_force: Some(TimeInForceReq::Gtc),
                            client_order_id: None,
                        });
                        if pos.remaining_qty <= 0.0001 {
                            self.position = None;
                            return Ok(orders);
                        }
                    }
                }
            }

            // ── ATR trailing stop (Chandelier Exit) ──
            if let Some(pos) = &mut self.position {
                let atr_mult = if self.config.atr_trailing_mult > 0.0 {
                    self.config.atr_trailing_mult
                } else {
                    3.0
                };
                let atr_val = self.atr.value();

                let new_trail = match pos.side {
                    OrderSide::Buy => pos.highest_since_entry - atr_mult * atr_val,
                    OrderSide::Sell => pos.lowest_since_entry + atr_mult * atr_val,
                };

                pos.trailing_stop = Some(match pos.trailing_stop {
                    Some(prev) => match pos.side {
                        OrderSide::Buy => new_trail.max(prev),  // ratchet up only
                        OrderSide::Sell => new_trail.min(prev),  // ratchet down only
                    },
                    None => new_trail,
                });

                // Check trailing stop hit
                if let Some(trail) = pos.trailing_stop {
                    let hit = match pos.side {
                        OrderSide::Buy => current_price <= trail,
                        OrderSide::Sell => current_price >= trail,
                    };
                    if hit {
                        let sell_qty = pos.remaining_qty;
                        let entry = pos.entry_price;
                        self.realized_pnl += (current_price - entry) * sell_qty;
                        self.position = None;
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(),
                            side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit,
                            price: Some(current_price),
                            quantity: sell_qty,
                            time_in_force: Some(TimeInForceReq::Gtc),
                            client_order_id: None,
                        });
                        return Ok(orders);
                    }
                }
            }

            // ── Direction flip / ADX exit ──
            if let Some(pos) = &self.position {
                let entry_dir = match pos.side {
                    OrderSide::Buy => Direction::Up,
                    OrderSide::Sell => Direction::Down,
                };
                let (exit, reason) = self.should_exit(current_price, entry_dir);
                if exit {
                    let sell_qty = pos.remaining_qty;
                    self.realized_pnl += (current_price - pos.entry_price) * sell_qty;
                    self.position = None;
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(),
                        side: OrderSide::Sell,
                        order_type: OrderTypeReq::Limit,
                        price: Some(current_price),
                        quantity: sell_qty,
                        time_in_force: Some(TimeInForceReq::Gtc),
                        client_order_id: None,
                    });
                    return Ok(orders);
                }
            }
        }

        // ── No position: check for entry ──
        if self.position.is_none() && self.should_activate(current_price) {
            let stop_loss = self.calculate_stop_loss(current_price);
            let quantity = self.calculate_quantity(current_price, stop_loss);
            if quantity > 0.0 {
                orders.push(OrderRequest {
                    symbol: self.pair.clone(),
                    side: OrderSide::Buy,
                    order_type: OrderTypeReq::Limit,
                    price: Some(current_price),
                    quantity,
                    time_in_force: Some(TimeInForceReq::Gtc),
                    client_order_id: None,
                });
            }
        }

        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        match fill.side {
            OrderSide::Buy => {
                let stop_loss = self.calculate_stop_loss(fill.price);
                let tp_levels = TrendPosition::calculate_tp_levels(
                    fill.price, stop_loss, self.config.risk_reward_ratio, 0.10,
                );
                self.position = Some(TrendPosition {
                    side: OrderSide::Buy,
                    entry_price: fill.price,
                    stop_loss,
                    quantity: fill.quantity,
                    remaining_qty: fill.quantity,
                    trailing_stop: None,
                    highest_since_entry: fill.price,
                    lowest_since_entry: fill.price,
                    tp_levels,
                });
            }
            OrderSide::Sell => {
                if let Some(mut pos) = self.position.take() {
                    pos.remaining_qty -= fill.quantity;
                    if pos.remaining_qty <= 0.0001 {
                        self.position = None;
                    } else {
                        self.position = Some(pos);
                    }
                }
            }
        }
        Ok(Vec::new())
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> { Ok(Vec::new()) }
    async fn on_stop(&mut self) -> Result<()> { Ok(()) }

    fn status(&self) -> StrategyStatus {
        // Covered in Task 5
        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state: "WAITING".to_string(),
            pnl: 0.0,
            open_orders: 0,
            details: String::new(),
        }
    }

    fn current_capital(&self) -> f64 { self.config.capital + self.realized_pnl }
    fn initial_capital(&self) -> f64 { self.initial_capital }
}
```

- [ ] **Step 2: Verify compilation**

Run: `cargo check --manifest-path trading-engine-core/Cargo.toml 2>&1 | tail -10`
Expected: Compiles with warnings only

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/strategy/trend.rs
git commit -m "feat: trend on_tick with direction-aware entry, ATR trailing stop, dir-flip exit"
```

---

### Task 5: Direction-Aware Status Reporting

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs` (status method)

- [ ] **Step 1: Replace the status() method placeholder**

Replace the placeholder `status()` in the Strategy impl with:

```rust
    fn status(&self) -> StrategyStatus {
        let (state, details, pnl) = if let Some(pos) = &self.position {
            let unrealized_pnl = match pos.side {
                OrderSide::Buy => (self.ema_fast.value() - pos.entry_price) * pos.remaining_qty,
                OrderSide::Sell => (pos.entry_price - self.ema_fast.value()) * pos.remaining_qty,
            };
            let side_str = match pos.side { OrderSide::Buy => "LONG", OrderSide::Sell => "SHORT" };
            let trail_str = match pos.trailing_stop {
                Some(ts) => format!(" | Trail: ${:.2}", ts),
                None => String::new(),
            };
            let adx_str = format!("ADX: {:.1}", self.adx.adx());
            let dir_str = match self.direction(self.ema_fast.value()) {
                Direction::Up => "dir: +1",
                Direction::Down => "dir: -1",
                Direction::Flat => "dir: 0",
            };
            (
                "POSITION".to_string(),
                format!(
                    "{} {:.4} @ ${:.2} | SL: ${:.2}{} | {} | {}",
                    side_str, pos.remaining_qty, pos.entry_price, pos.stop_loss,
                    trail_str, adx_str, dir_str
                ),
                unrealized_pnl,
            )
        } else if !self.indicators_ready() {
            ("WAITING".to_string(), "⏳ All indicators warming up".to_string(), 0.0)
        } else {
            let gate = self.gate();
            let dir = self.direction(self.ema_fast.value());
            let (s1, s2, s3) = self.score(dir);
            let dir_str = match dir {
                Direction::Up => "+1",
                Direction::Down => "-1",
                Direction::Flat => "0",
            };
            let reason = if !gate { "No trend gate" }
                         else if dir == Direction::Flat { "Mixed direction" }
                         else if dir == Direction::Down && !TRADE_SHORTS { "dir=-1 blocks longs" }
                         else if !s2 { "No volume (S2 mandatory)" }
                         else { "Waiting for confirmation" };
            (
                "WAITING".to_string(),
                format!(
                    "Gate: {}{} | dir: {} | S1:{} S2:{} S3:{} | ADX={:.1} CHOP={:.0} RSI={:.1} | {}",
                    if gate { "✅" } else { "❌" },
                    if self.choppiness.value() < 38.0 { "✅" } else { "❌" },
                    dir_str,
                    if s1 { "✅" } else { "❌" },
                    if s2 { "✅" } else { "❌" },
                    if s3 { "✅" } else { "❌" },
                    self.adx.adx(),
                    self.choppiness.value(),
                    self.rsi.value(),
                    reason,
                ),
                0.0,
            )
        };

        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state,
            pnl,
            open_orders: 0,
            details,
        }
    }
```

- [ ] **Step 2: Verify compilation and run all tests**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib 2>&1 | tail -10`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/strategy/trend.rs
git commit -m "feat: trend status shows 5-layer pipeline state (gate/direction/score)"
```

---

### Task 6: Wire TrendConfig in main.rs

**Files:**
- Modify: `trading-engine-core/src/main.rs` (TrendConfig fields)

- [ ] **Step 1: Verify main.rs doesn't need changes**

The TrendConfig is loaded from YAML via serde. New fields have `#[serde(default = ...)]` so existing config files work without changes. No main.rs changes needed.

Run: `cargo check --manifest-path trading-engine-core/Cargo.toml 2>&1 | tail -5`
Expected: Compiles clean

---

### Task 7: Build, Test, Verify

- [ ] **Step 1: Run full test suite**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib 2>&1 | tail -15`
Expected: All 55+ tests PASS (including new MACD tests)

- [ ] **Step 2: Run cargo check for warnings**

Run: `cargo check --manifest-path trading-engine-core/Cargo.toml 2>&1 | grep -E "error|warning.*trend"`
Expected: No errors. Warnings about unused fields in old config are OK.

- [ ] **Step 3: Final commit and push**

```bash
git push origin main
```

- [ ] **Step 4: Verify in production**

After CI deploys, check `/api/v1/strategies` — trend entries should show:
```
Gate: ✅✅ | dir: -1 | S1:✅ S2:❌ S3:✅ | ADX=93.8 CHOP=37 RSI=29.8 | dir=-1 blocks longs
```
Instead of the old `Score: 0/3 | EMA❌ | RSI: 29.8❌` format.

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Layer 1 (Gate): `gate()` method — Task 3
- ✅ Layer 2 (Direction): `direction()` method — Task 3
- ✅ Layer 3 (Score): `score()` method with S1/S2/S3 — Task 3
- ✅ Layer 4 (Activate): `should_activate()` with S2 mandatory — Task 3
- ✅ Layer 5 (Exit): ADX<20, dir flip, ATR trailing stop — Task 4
- ✅ MACD indicator: new file with stable init at slow+signal — Task 1
- ✅ Warm-up guard: `indicators_ready()` checks all 8 indicators — Task 3
- ✅ Long-only guard: `TRADE_SHORTS = false` — Task 3
- ✅ Status reporting: 5-layer state shown — Task 5
- ✅ Config: new thresholds with defaults — Task 2

**Placeholder scan:** No TBDs, no TODOs, all steps have actual code.

**Type consistency:**
- `Direction` enum (Up/Down/Flat) used consistently across gate(), direction(), score(), should_activate(), should_exit(), status()
- `TrendPosition` has `highest_since_entry` and `lowest_since_entry` — used in Task 4 trailing stop
- Config field names match between TrendConfig definition and constructor
