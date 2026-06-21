so # Equity Market Scanner — Implementation Plan

## Overview

A standalone Rust binary that scores 50-100 US stocks daily using the bot's existing indicator library, ranks them by a multi-factor score, and outputs the top candidates to CSV. This is the first step toward the RL multi-asset agent — the scanner IS the RL agent's observation → action pipeline.

## Architecture

```
cargo run --bin scan_equities
    │
    ├── Load symbol universe (config/scan_universe.txt)
    ├── Fetch daily bars from Yahoo Finance (free, no API key)
    ├── Score each symbol (reuse existing Rust indicators)
    ├── Rank by score, output top N
    └── Write CSV to data/scan_results_<date>.csv
```

## Files to Create

### 1. `config/scan_universe.txt`
Plain text, one ticker per line. Start with S&P 100 (most liquid):
```
AAPL
MSFT
GOOGL
AMZN
NVDA
META
TSLA
...
```

### 2. `trading-engine-core/src/scanner/mod.rs` (library)

Core scoring logic — reusable, not tied to the data source.

```rust
use crate::indicators::*;
use crate::models::bar::Bar;

#[derive(Debug, Clone)]
pub struct ScanResult {
    pub symbol: String,
    pub score: u32,       // 0-8
    pub price: f64,
    pub adx: f64,
    pub rsi: f64,
    pub atr_pct: f64,     // ATR as % of price
    pub volume_ratio: f64, // today's volume / 20-day average
    pub direction: ScanDirection,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ScanDirection {
    Long,   // EMA-50 rising
    Short,  // EMA-50 falling (skip for now — long-only)
    Flat,
}

/// Score a symbol from its daily bars.
/// Returns None if insufficient bars for indicator warmup.
pub fn score_symbol(symbol: &str, bars: &[Bar]) -> Option<ScanResult> {
    if bars.len() < 50 {
        return None;  // Need 50 bars for EMA-50 + ADX warmup
    }

    // Warm up indicators
    let mut adx = Adx::new(14);
    let mut atr = Atr::new(14);
    let mut rsi = Rsi::new(14);
    let mut macd = Macd::default_12_26_9();
    let mut ema50 = Ema::new(50);
    let mut ema20 = Ema::new(20);
    let mut volume_history: Vec<f64> = Vec::new();

    for bar in bars {
        adx.update_bar(bar.open, bar.high, bar.low, bar.close);
        atr.update_bar(bar.open, bar.high, bar.low, bar.close);
        rsi.update(bar.close);
        macd.update(bar.close);
        ema50.update(bar.close);
        ema20.update(bar.close);
        volume_history.push(bar.volume);
    }

    if !adx.is_initialized() || !macd.is_initialized() || !ema50.is_initialized() {
        return None;
    }

    let price = bars.last()?.close;
    let adx_val = adx.adx();
    let rsi_val = rsi.value();
    let atr_val = atr.value();
    let macd_hist = macd.histogram();
    let ema50_val = ema50.value();
    let ema20_val = ema20.value();

    // Direction: EMA-50 rising (compare last 5 bars' EMA trend)
    let direction = if ema20_val > ema50_val && price > ema50_val {
        ScanDirection::Long
    } else if ema20_val < ema50_val && price < ema50_val {
        ScanDirection::Short
    } else {
        ScanDirection::Flat
    };

    // Score 0-8
    let mut score = 0;

    // Factor 1: Trend strength (ADX) — max 3 points
    if adx_val > 40.0 { score += 3; }
    else if adx_val > 25.0 { score += 2; }
    else if adx_val > 20.0 { score += 1; }

    // Factor 2: Momentum (MACD histogram) — max 2 points
    if direction == ScanDirection::Long {
        if macd_hist > 0.0 { score += 2; }  // Bullish momentum aligned with uptrend
        else if macd_hist > -0.5 { score += 1; }  // Mild bearish in uptrend (pullback)
    }

    // Factor 3: RSI health — max 1 point (not overbought, not oversold)
    if rsi_val >= 40.0 && rsi_val <= 70.0 { score += 1; }

    // Factor 4: Volatility (ATR as % of price) — max 1 point
    let atr_pct = if price > 0.0 { (atr_val / price) * 100.0 } else { 0.0 };
    if atr_pct >= 1.5 { score += 1; }  // Enough room for TP targets

    // Factor 5: Volume — max 1 point
    let vol_avg = if volume_history.len() >= 20 {
        volume_history[volume_history.len()-21..volume_history.len()-1].iter().sum::<f64>() / 20.0
    } else { 0.0 };
    let volume_ratio = if vol_avg > 0.0 { volume_history.last().unwrap_or(&0.0) / vol_avg } else { 1.0 };
    if volume_ratio >= 1.5 { score += 1; }

    Some(ScanResult {
        symbol: symbol.to_string(), score, price,
        adx: adx_val, rsi: rsi_val, atr_pct, volume_ratio, direction,
    })
}
```

### 3. `trading-engine-core/src/scanner/yahoo.rs`

Free Yahoo Finance data fetcher.

```rust
use crate::models::bar::Bar;
use anyhow::Result;

/// Fetch daily OHLCV bars from Yahoo Finance (free, no API key).
pub async fn fetch_daily_bars(symbol: &str) -> Result<Vec<Bar>> {
    let url = format!(
        "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=3mo&interval=1d",
        symbol
    );
    let client = reqwest::Client::new();
    let resp: serde_json::Value = client
        .get(&url)
        .header("User-Agent", "equity-scanner/1.0")
        .send().await?.json().await?;

    let timestamps = resp["chart"]["result"][0]["timestamp"]
        .as_array().ok_or_else(|| anyhow!("no timestamp array"))?;
    let quote = &resp["chart"]["result"][0]["indicators"]["quote"][0];

    let opens = quote["open"].as_array().ok_or_else(|| anyhow!("no open array"))?;
    let highs = quote["high"].as_array().ok_or_else(|| anyhow!("no high array"))?;
    let lows = quote["low"].as_array().ok_or_else(|| anyhow!("no low array"))?;
    let closes = quote["close"].as_array().ok_or_else(|| anyhow!("no close array"))?;
    let volumes = quote["volume"].as_array().ok_or_else(|| anyhow!("no volume array"))?;

    let mut bars = Vec::new();
    for i in 0..timestamps.len() {
        // Skip null values (holidays, missing data)
        let close = closes[i].as_f64();
        if let Some(c) = close {
            bars.push(Bar::new(
                opens[i].as_f64().unwrap_or(c),
                highs[i].as_f64().unwrap_or(c),
                lows[i].as_f64().unwrap_or(c),
                c,
                volumes[i].as_f64().unwrap_or(0.0),
                timestamps[i].as_i64().unwrap_or(0),
            ));
        }
    }
    Ok(bars)
}
```

### 4. `scripts/scan_equities.rs` (binary)

```rust
//! Equity Market Scanner — scores 50-100 US stocks daily.
//! Run after market close: `cargo run --bin scan_equities`
use std::fs;
use trading_engine_core::scanner::{self, ScanDirection};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // 1. Load symbol universe
    let universe: Vec<String> = fs::read_to_string("config/scan_universe.txt")?
        .lines()
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|l| l.trim().to_string())
        .collect();
    println!("Scanning {} symbols...", universe.len());

    // 2. Fetch + score each symbol
    let mut results = Vec::new();
    for symbol in &universe {
        match trading_engine_core::scanner::yahoo::fetch_daily_bars(symbol).await {
            Ok(bars) => {
                if let Some(result) = scanner::score_symbol(symbol, &bars) {
                    if result.direction == ScanDirection::Long {
                        results.push(result);
                    }
                }
            }
            Err(e) => eprintln!("  {} fetch failed: {}", symbol, e),
        }
    }

    // 3. Sort by score (descending)
    results.sort_by(|a, b| b.score.cmp(&a.score));

    // 4. Print top 10
    println!("\n{:<6} {:<6} {:>8} {:>6} {:>6} {:>6} {:>8} {:<6}",
        "Rank", "Symbol", "Price", "Score", "ADX", "RSI", "ATR%", "VolR");
    for (i, r) in results.iter().take(10).enumerate() {
        println!("{:<6} {:<6} {:>8.2} {:>6} {:>6.1} {:>6.1} {:>6.2}% {:>6.1}x",
            i+1, r.symbol, r.price, r.score, r.adx, r.rsi, r.atr_pct, r.volume_ratio);
    }

    // 5. Write CSV
    let date = chrono::Utc::now().format("%Y-%m-%d");
    let csv_path = format!("data/scan_results_{}.csv", date);
    let mut csv = String::from("rank,symbol,score,price,adx,rsi,atr_pct,volume_ratio,direction\n");
    for (i, r) in results.iter().enumerate() {
        csv.push_str(&format!("{},{},{},{:.2},{:.1},{:.1},{:.2},{:.1},{}\n",
            i+1, r.symbol, r.score, r.price, r.adx, r.rsi, r.atr_pct, r.volume_ratio, r.direction));
    }
    fs::write(&csv_path, csv)?;
    println!("\nFull results: {} ({} candidates)", csv_path, results.len());
    Ok(())
}
```

### 5. `trading-engine-core/tests/test_scanner.rs`

```rust
use trading_engine_core::scanner::score_symbol;
use trading_engine_core::models::bar::Bar;

fn trending_bars(n: usize, start: f64, drift: f64) -> Vec<Bar> {
    (0..n).map(|i| {
        let close = start + (i as f64) * drift;
        Bar::new(close - drift * 0.5, close + drift * 0.5, close - drift, close, 1_000_000.0, i as i64)
    }).collect()
}

fn flat_bars(n: usize, price: f64) -> Vec<Bar> {
    (0..n).map(|i| Bar::new(price, price + 0.01, price - 0.01, price, 500_000.0, i as i64)).collect()
}

#[test]
fn test_trending_stock_scores_high() {
    let bars = trending_bars(60, 100.0, 1.5);  // Strong uptrend: 100 → 187
    let result = score_symbol("TEST", &bars).unwrap();
    assert!(result.score >= 4, "trending stock should score ≥4, got {}", result.score);
    assert_eq!(result.direction.to_string(), "Long");
}

#[test]
fn test_flat_stock_scores_low() {
    let bars = flat_bars(60, 100.0);  // No movement
    let result = score_symbol("TEST", &bars).unwrap();
    assert!(result.score <= 3, "flat stock should score ≤3, got {}", result.score);
}

#[test]
fn test_insufficient_bars_returns_none() {
    let bars = flat_bars(30, 100.0);  // Only 30 bars (need 50)
    assert!(score_symbol("TEST", &bars).is_none());
}
```

## Scoring Model

| Factor | Max Points | Indicator | Criteria |
|---|---|---|---|
| Trend strength (ADX) | 3 | ADX-14 | >40→3, >25→2, >20→1 |
| Momentum (MACD) | 2 | MACD 12/26/9 | Positive & aligned with direction→2 |
| RSI health | 1 | RSI-14 | 40-70 (not overbought/oversold)→1 |
| Volatility | 1 | ATR-14 / price | >1.5% (enough room for TPs)→1 |
| Volume | 1 | Volume / SMA-20 | >1.5× average→1 |
| **Total** | **8** | | |

**Direction filter**: Long only (price > EMA-50 + EMA-20 > EMA-50). Short candidates are skipped for now.

## Data Source

**Yahoo Finance** (free, no API key):
```
GET https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=3mo&interval=1d
→ ~60 daily OHLCV bars (enough for indicator warmup)
→ Rate limit: ~2000 req/hour (plenty for 100 symbols)
```

When IBKR is wired, swap the fetcher — the scoring logic stays the same.

## How to Run

```bash
# After US market close (4:00 PM ET):
cd trading-engine-core
cargo run --bin scan_equities

# Output (console):
Scanning 50 symbols...
Rank   Symbol    Price  Score   ADX    RSI   ATR%   VolR
1      NVDA     880.20      7   42.3   58.0   2.8%   2.1x
2      AAPL     195.20      6   32.5   55.0   1.8%   1.8x
...

# Output (CSV):
data/scan_results_2026-06-21.csv
```

## What This Enables

1. **Daily scan**: Run after close → ranked list of best setups
2. **RL training data**: Historical scan results → which high-score stocks actually worked → reward signal
3. **IBKR handoff**: Swap Yahoo fetcher → IBKR Connector (scoring unchanged)
4. **Backtest**: Score today → did stock move up over next 5 days? → validate the scoring model
5. **Telegram integration**: Top 10 scan results sent daily via the existing Telegram bot

## Verification

1. `cargo run --bin scan_equities` prints ranked results
2. `data/scan_results_<date>.csv` is written
3. `cargo test --test test_scanner` passes
4. A trending stock (60 bars, rising price) scores ≥4; a flat stock scores ≤3
