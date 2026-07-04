//! Phase-1 capstone CLI: load config, fetch real 1h bars, drive the production
//! `GridStrategy` over them via `run_grid_on_bars`, compute metrics, and write
//! the JSON + markdown report.
//!
//! Usage: `backtest_replay [PAIR] [MONTHS] [CONFIG_PATH]`
//! Defaults: PAIR=ETHUSDT, MONTHS=6, CONFIG_PATH=config/strategy.yaml.
//! Run from the repo root so the relative config path resolves.
use std::path::Path;

use trading_engine_core::backtest::report;
use trading_engine_core::backtest::replay::{run_grid_on_bars, EngineKind, ReplayConfig};
use trading_engine_core::backtest::bars;
use trading_engine_core::config::AppConfig;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let pair = std::env::args().nth(1).unwrap_or_else(|| "ETHUSDT".into());
    let months: u32 = std::env::args()
        .nth(2)
        .and_then(|s| s.parse().ok())
        .unwrap_or(6);
    // Resolution #4: default config path, optional 3rd-arg override.
    let cfg_path = std::env::args().nth(3).unwrap_or_else(|| "config/strategy.yaml".into());

    let cfg = AppConfig::load(&cfg_path)?;
    let end = chrono::Utc::now().date_naive();
    let start = end - chrono::Duration::days(30 * months as i64);
    println!("Loading {} 1h bars {} → {} ...", pair, start, end);
    // `load_bars` uses `reqwest::blocking::Client` which creates its own
    // runtime; dropping it inside `#[tokio::main]` panics. Push it onto a
    // blocking thread so the inner runtime is dropped there safely.
    let pair_for_load = pair.clone();
    let bars =
        tokio::task::spawn_blocking(move || bars::load_bars(&pair_for_load, start, end))
            .await??;
    println!("{} bars loaded", bars.len());

    // Resolution #1: ReplayConfig has NO start/end fields — they're consumed
    // locally above (passed to `load_bars`) and not needed by `run_grid_on_bars`
    // (which takes the bars directly).
    // Resolution #2: PaperConfig field names verified in config.rs:
    //   slippage_bps / taker_fee_bps / maker_fee_bps (snake_case, serde-default).
    let rc = ReplayConfig {
        symbol: pair.clone(),
        init_cash: cfg.capital.account_usdt,
        warmup_bars: 220,
        bar_hours: 1.0,
        engine: EngineKind::Grid,
        grid: cfg.grid.clone(),
        tick_size: cfg.pairs.values().next().map(|p| p.tick_size).unwrap_or(0.01),
        step_size: cfg.pairs.values().next().map(|p| p.step_size).unwrap_or(0.0001),
        taker_fee_bps: cfg.paper.taker_fee_bps,
        maker_fee_bps: cfg.paper.maker_fee_bps,
        slippage_bps: cfg.paper.slippage_bps,
        trend: cfg.trend.clone(),
        perp_bars: None,
        funding_rate: None,
    };
    let run = run_grid_on_bars(&rc, bars).await?;
    let m = report::compute(&run, 0.0, 1.0);
    report::write_report(Path::new("backtest/results/replay"), &pair, &run, &m)?;
    println!("{:#?}", m);
    Ok(())
}
