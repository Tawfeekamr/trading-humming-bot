//! Phase-2 capstone CLI: load config, fetch real 1h bars, drive ANY of the
//! four production engines (grid/trend/swing/MR) over them via
//! `run_engine_on_bars`, compute metrics, and write the JSON + markdown
//! report.
//!
//! Usage:
//!   backtest_replay [PAIR] [MONTHS] [ENGINE] [--config PATH] [--engine NAME]
//!
//! Defaults: PAIR=ETHUSDT, MONTHS=6, ENGINE=grid, PATH=config/strategy.yaml.
//! Run from the repo root so the relative config path resolves.
//!
//! The 3rd positional is interpreted as ENGINE if it matches one of
//! `grid|trend|swing|mean_reversion`; otherwise it falls back to Phase-1's
//! legacy interpretation as CONFIG_PATH (back-compat). The `--engine` flag
//! wins over the positional form when both are given.
//!
//! Phase-2 fidelity gaps (stamped here for the record; same caveves apply to
//! every engine that touches the affected path):
//!   * `perp_bars` = a clone of the SPOT bars for `trend` + `trade_shorts`.
//!     Real historical perp klines are deferred — spot ≈ perp for major coins
//!     so the short-side MTM is approximate, not exact.
//!   * `funding_rate = None` — flat-funding assumption (zero per-bar accrual).
//!     Real per-roll funding history is deferred with the perp klines.
//!   * `regime = None` in TickContext unless `--regime-file <path>` is supplied,
//!     in which case per-bar ML regime labels are injected (closes this gap).
//!   * MR `bid_depth` is degenerate: replay synthesizes a mid-only book, so
//!     depth is ~1 unit (faithful L2 history is out of scope).
use std::path::Path;

use trading_engine_core::backtest::bars;
use trading_engine_core::backtest::replay::{run_engine_on_bars, EngineKind, ReplayConfig};
use trading_engine_core::backtest::report;
use trading_engine_core::config::AppConfig;

/// Map a CLI string to an `EngineKind`. Returns `None` for unknown names so the
/// caller can decide whether to treat the positional as a config path instead.
fn parse_engine(s: &str) -> Option<EngineKind> {
    match s {
        "grid" => Some(EngineKind::Grid),
        "trend" => Some(EngineKind::Trend),
        _ => None,
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // ---- Arg parsing (no extra dep; std::env::args iteration) ----
    // Positional: <pair> <months> <engine-or-config-path>
    // Flags:      --engine NAME, --config PATH
    let mut pair: String = "ETHUSDT".into();
    let mut months: u32 = 6;
    let mut engine: Option<EngineKind> = None;
    let mut cfg_path: String = "config/strategy.yaml".into();
    let mut validate = false;
    let mut sweep = false;
    let mut regime_file: Option<String> = None;
    let mut start_override: Option<String> = None;
    let mut end_override: Option<String> = None;

    let mut args = std::env::args().skip(1);
    let mut positional_idx = 0;
    while let Some(a) = args.next() {
        if a == "--engine" {
            let v = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("--engine requires a value"))?;
            engine = Some(parse_engine(&v).ok_or_else(|| {
                anyhow::anyhow!(
                    "unknown engine '{}': expected grid|trend|swing|mean_reversion",
                    v
                )
            })?);
        } else if a == "--config" {
            cfg_path = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("--config requires a value"))?;
        } else if a == "--regime-file" {
            regime_file = Some(
                args.next()
                    .ok_or_else(|| anyhow::anyhow!("--regime-file requires a value"))?,
            );
        } else if a == "--start" {
            start_override = Some(
                args.next()
                    .ok_or_else(|| anyhow::anyhow!("--start requires a value (YYYY-MM-DD)"))?,
            );
        } else if a == "--end" {
            end_override = Some(
                args.next()
                    .ok_or_else(|| anyhow::anyhow!("--end requires a value (YYYY-MM-DD)"))?,
            );
        } else if a == "--validate" {
            validate = true;
        } else if a == "--sweep" {
            sweep = true;
        } else if a.starts_with("--") {
            return Err(anyhow::anyhow!("unknown flag: {}", a));
        } else {
            // Positional slot routing.
            match positional_idx {
                0 => pair = a,
                1 => {
                    months = a
                        .parse()
                        .map_err(|_| anyhow::anyhow!("months must be a positive integer"))?
                }
                2 => {
                    // Engine name → engine; otherwise legacy Phase-1 config path.
                    if let Some(k) = parse_engine(&a) {
                        engine = Some(k);
                    } else {
                        cfg_path = a;
                    }
                }
                _ => return Err(anyhow::anyhow!("too many positional args")),
            }
            positional_idx += 1;
        }
    }
    let kind = engine.unwrap_or(EngineKind::Grid);

    // ---- Load config + bars ----
    let cfg = AppConfig::load(&cfg_path)?;
    let parse_day = |s: &str| -> anyhow::Result<chrono::NaiveDate> {
        chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d")
            .map_err(|e| anyhow::anyhow!("bad date '{}': {}", s, e))
    };
    let end = match &end_override {
        Some(s) => parse_day(s)?,
        None => chrono::Utc::now().date_naive(),
    };
    let start = match &start_override {
        Some(s) => parse_day(s)?,
        None => end - chrono::Duration::days(30 * months as i64),
    };
    println!(
        "Loading {} 1h bars {} → {} (engine={:?}) ...",
        pair, start, end, kind
    );
    // `load_bars` uses `reqwest::blocking::Client` which creates its own
    // runtime; dropping it inside `#[tokio::main]` panics. Push it onto a
    // blocking thread so the inner runtime is dropped there safely.
    let pair_for_load = pair.clone();
    let bars =
        tokio::task::spawn_blocking(move || bars::load_bars(&pair_for_load, start, end))
            .await??;
    println!("{} bars loaded", bars.len());

    // Optional ML regime timeline: inject per-bar regime labels into the
    // replay (closes the "regime=None" fidelity gap). None → back-compat.
    let regime = match &regime_file {
        Some(p) => {
            let tl = trading_engine_core::backtest::replay::RegimeTimeline::from_json_file(
                std::path::Path::new(p),
            )?;
            println!("Loaded regime timeline from {}", p);
            Some(tl)
        }
        None => None,
    };

    // ---- Build ReplayConfig ----
    // tick/step: prefer the configured pair entry; fall back to first pair
    // (Phase-1 behavior when the requested symbol isn't in cfg.pairs).
    let (tick_size, step_size) = cfg
        .pairs
        .get(&pair)
        .map(|p| (p.tick_size, p.step_size))
        .or_else(|| cfg.pairs.values().next().map(|p| (p.tick_size, p.step_size)))
        .unwrap_or((0.01, 0.0001));

    // Trend short-side MTM needs a perp series. FIDELITY GAP (Phase-2): we
    // reuse the spot bars as a perp proxy (spot ≈ perp for major coins, so
    // MTM is approximate) and assume flat funding (`None` = zero per-bar
    // accrual). Real perp klines + funding history are deferred to a later
    // phase; the dispatcher handles `None` gracefully.
    let perp_bars = if kind == EngineKind::Trend && cfg.trend.trade_shorts {
        Some(bars.clone())
    } else {
        None
    };

    // All four engines consume the 1h bar stream; swing's internal 4h HTF
    // aggregation doesn't change the 1h bar clock, so bar_hours=1.0 is
    // correct for all engines (Sharpe annualization = 24*365 bars/year).
    let bar_hours = 1.0;

    let rc = ReplayConfig {
        symbol: pair.clone(),
        init_cash: cfg.capital.account_usdt,
        warmup_bars: 220,
        bar_hours,
        engine: kind,
        grid: cfg.grid.clone(),
        tick_size,
        step_size,
        taker_fee_bps: cfg.paper.taker_fee_bps,
        maker_fee_bps: cfg.paper.maker_fee_bps,
        slippage_bps: cfg.paper.slippage_bps,
        trend: cfg.trend.clone(),
        perp_bars,
        // Flat-funding assumption (Phase-2 gap; documented above).
        funding_rate: None,
        swing: cfg.swing.clone(),
        mean_reversion: cfg.mean_reversion.clone(),
        regime,
    };

    // ---- Run + report ----
    if sweep {
        let rep =
            trading_engine_core::backtest::sweep::run_sweep(kind, &rc, bars, 1.0 / 3.0, bar_hours)
                .await?;
        let cand_sharpe = rep.candidate.as_ref().map(|c| c.oos.sharpe).unwrap_or(0.0);
        println!(
            "sweep engine={:?} decision={} best={} baseline_oos_sharpe={:.2} candidate_oos_sharpe={:.2} reasons={:?}",
            kind,
            if rep.decision.apply { "APPLY" } else { "KEEP" },
            rep.best_label.as_deref().unwrap_or("none"),
            rep.baseline.oos.sharpe,
            cand_sharpe,
            rep.decision.gate_reasons,
        );
        trading_engine_core::backtest::report::write_sweep_report(
            std::path::Path::new("backtest/results/replay"),
            &pair,
            &rep,
        )?;
    } else if validate {
        use trading_engine_core::backtest::validation::run_validation;
        let rep = run_validation(kind, &rc, bars, 1.0 / 3.0, bar_hours).await?;
        println!(
            "validate engine={:?} full_sharpe={:.2} is_sharpe={:.2} oos_sharpe={:.2} gap={:.2} overfit={}",
            kind,
            rep.full.sharpe,
            rep.is_metrics.sharpe,
            rep.oos.sharpe,
            rep.is_oos_sharpe_gap,
            rep.overfit_suspect,
        );
        report::write_validation_report(
            Path::new("backtest/results/replay"),
            &pair,
            kind,
            &rep,
        )?;
    } else {
        let run = run_engine_on_bars(kind, &rc, bars).await?;
        let m = report::compute(&run, 0.0, bar_hours);
        report::write_report(Path::new("backtest/results/replay"), &pair, &run, &m)?;
        println!(
            "engine={:?} trades={} return_pct={:.2} sharpe={:.3} max_dd_pct={:.2} win_pct={:.2} hodl_pct={:.2}",
            kind,
            m.total_trades,
            m.total_return_pct,
            m.sharpe,
            m.max_drawdown_pct,
            m.win_rate_pct,
            m.hodl_return_pct,
        );
        println!("{:#?}", m);
    }
    Ok(())
}
