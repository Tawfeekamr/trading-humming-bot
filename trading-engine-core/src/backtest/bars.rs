//! 1h kline download + cache + parse for the backtest harness.
use std::io::Read;
use std::path::PathBuf;
use anyhow::{bail, Context, Result};
use chrono::NaiveDate;
use crate::models::bar::Bar;

const BASE: &str = "https://data.binance.vision/data/spot/daily/klines";

/// Parse Binance kline CSV bytes into Bars. Columns (no header):
/// open_time(ms), o, h, l, c, vol, close_time, quote_vol, count, tbv, tbqv, ignore
pub fn parse_kline_csv(bytes: &[u8]) -> Result<Vec<Bar>> {
    let mut rdr = csv::ReaderBuilder::new().has_headers(false).from_reader(bytes);
    let mut out = Vec::new();
    for rec in rdr.records() {
        let rec = rec?;
        if rec.is_empty() { continue; }
        let ts: i64 = rec[0].parse()
            .with_context(|| format!("bad open_time: {}", &rec[0]))?;
        let open: f64  = rec[1].parse()?;
        let high: f64  = rec[2].parse()?;
        let low: f64   = rec[3].parse()?;
        let close: f64 = rec[4].parse()?;
        let volume: f64 = rec[5].parse()?;
        out.push(Bar::new(open, high, low, close, volume, ts));
    }
    out.sort_by_key(|b| b.timestamp);
    Ok(out)
}

fn cache_dir() -> PathBuf {
    PathBuf::from("backtest/data_cache/klines")
}

/// Download + cache 1h bars for [start, end] inclusive. Missing days are skipped.
pub fn load_bars(symbol: &str, start: NaiveDate, end: NaiveDate) -> Result<Vec<Bar>> {
    let mut all = Vec::new();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(60)).build()?;
    let mut d = start;
    while d <= end {
        let dir = cache_dir().join(symbol).join("1h");
        std::fs::create_dir_all(&dir).ok();
        let file = dir.join(format!("{}-1h-{}.csv", symbol, d));
        let csv_bytes = if file.exists() {
            std::fs::read(&file)?
        } else {
            let url = format!("{}/{}/{}/{}-1h-{}.zip", BASE, symbol, "1h", symbol, d);
            match client.get(&url).send() {
                Ok(resp) if resp.status().is_success() => {
                    let zbytes = resp.bytes()?;
                    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(zbytes))
                        .with_context(|| format!("zip parse {}", url))?;
                    let mut buf = Vec::new();
                    zip.by_index(0)?.read_to_end(&mut buf)?;
                    std::fs::write(&file, &buf)?;
                    buf
                }
                Ok(resp) if resp.status() == reqwest::StatusCode::NOT_FOUND => {
                    d += chrono::Duration::days(1);
                    continue;
                }
                Ok(resp) => bail!("kline fetch {}: HTTP {}", d, resp.status()),
                Err(e) => {
                    eprintln!("warn: {} {}: {} (skipped)", symbol, d, e);
                    d += chrono::Duration::days(1);
                    continue;
                }
            }
        };
        all.extend(parse_kline_csv(&csv_bytes).unwrap_or_else(|e| {
            eprintln!("warn: parse {}: {}", d, e);
            Vec::new()
        }));
        d += chrono::Duration::days(1);
    }
    all.sort_by_key(|b| b.timestamp);
    all.dedup_by_key(|b| b.timestamp);
    Ok(all)
}
