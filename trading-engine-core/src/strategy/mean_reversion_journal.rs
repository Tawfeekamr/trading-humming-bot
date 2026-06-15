use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::error;

/// SQLite journal for mean-reversion trades, so MR realized P&L is queryable by
/// `/pnl_all` (it was previously in-memory only → MR was missing from the
/// consolidated report). One shared db, `pair` column distinguishes the 4 pairs.
pub struct MeanReversionJournal {
    conn: Mutex<Connection>,
}

fn migrations() -> Migrations<'static> {
    Migrations::new(vec![M::up(
        "CREATE TABLE IF NOT EXISTS mr_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            pair            TEXT NOT NULL,
            side            TEXT NOT NULL,
            entry_price     REAL NOT NULL,
            exit_price      REAL NOT NULL,
            quantity        REAL NOT NULL,
            pnl             REAL NOT NULL,
            exit_reason     TEXT NOT NULL,
            duration_mins   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mr_timestamp ON mr_trades(timestamp);",
    )])
}

impl MeanReversionJournal {
    pub fn new() -> Result<Self> {
        let path = std::env::var("MR_JOURNAL_PATH")
            .unwrap_or_else(|_| "data/mean_reversion_journal.db".to_string());
        Self::open(&path)
    }

    pub fn open(path: &str) -> Result<Self> {
        let p = PathBuf::from(path);
        if let Some(parent) = p.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let conn = Connection::open(&p)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")?;
        let journal = Self { conn: Mutex::new(conn) };
        journal.init_db()?;
        Ok(journal)
    }

    fn init_db(&self) -> Result<()> {
        let mut conn = self.conn.lock().unwrap();
        migrations().to_latest(&mut conn)?;
        Ok(())
    }

    /// Log a closed MR trade. MR is long-only (buy the flush, sell the bounce),
    /// so `side` is always the entry side "BUY".
    #[allow(clippy::too_many_arguments)]
    pub fn log_trade(
        &self,
        pair: &str,
        entry_price: f64,
        exit_price: f64,
        quantity: f64,
        pnl: f64,
        exit_reason: &str,
        duration_mins: i64,
    ) {
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT INTO mr_trades
             (timestamp, pair, side, entry_price, exit_price, quantity, pnl, exit_reason, duration_mins)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            rusqlite::params![
                Utc::now().to_rfc3339(), pair, "BUY", entry_price, exit_price, quantity, pnl, exit_reason, duration_mins,
            ],
        ) {
            error!("MR journal write failed: {}", e);
        }
    }
}
