use crate::models::order::OrderSide;
use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::error;

/// Persistent journal of grid fills (one row per fill). Mirrors TrendJournal's
/// migration/WAL approach. Shared across all pairs (pair is a column).
pub struct GridJournal {
    conn: Mutex<Connection>,
}

/// Schema migrations, applied via SQLite's `user_version` pragma so each step
/// runs exactly once per database file.
fn migrations() -> Migrations<'static> {
    Migrations::new(vec![M::up(
        "CREATE TABLE IF NOT EXISTS grid_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            pair            TEXT NOT NULL,
            side            TEXT NOT NULL,
            level           TEXT NOT NULL,
            price           REAL NOT NULL,
            quantity        REAL NOT NULL,
            fee             REAL DEFAULT 0,
            realized_pnl    REAL NOT NULL,
            running_total   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gt_timestamp ON grid_trades(timestamp);",
    )])
}

impl GridJournal {
    /// Open the production journal at `data/grid_journal.db`. Failure is soft
    /// (trading must never block on the journal).
    pub fn new() -> Result<Self> {
        let path = std::env::var("GRID_JOURNAL_PATH")
            .unwrap_or_else(|_| "data/grid_journal.db".to_string());
        Self::open(&path)
    }

    /// Open a journal at an explicit path (used by tests).
    pub fn open(path: &str) -> Result<Self> {
        let p = PathBuf::from(path);
        if let Some(parent) = p.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let conn = Connection::open(&p)?;
        // WAL + a busy timeout so concurrent writers serialize instead of failing.
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

    /// Persist one fill. `running_total` is the strategy's cumulative realized
    /// PnL at the moment of this fill.
    pub fn log_fill(
        &self,
        pair: &str,
        side: OrderSide,
        level: &str,
        price: f64,
        quantity: f64,
        fee: f64,
        realized_pnl: f64,
        running_total: f64,
    ) {
        let side_str = match side {
            OrderSide::Buy => "BUY",
            OrderSide::Sell => "SELL",
        };
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT INTO grid_trades
             (timestamp, pair, side, level, price, quantity, fee, realized_pnl, running_total)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            rusqlite::params![
                Utc::now().to_rfc3339(),
                pair,
                side_str,
                level,
                price,
                quantity,
                fee,
                realized_pnl,
                running_total,
            ],
        ) {
            error!("Grid journal write failed: {}", e);
        }
    }

    /// Row count (testing/diagnostics).
    pub fn count(&self) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        Ok(conn.query_row("SELECT COUNT(*) FROM grid_trades", [], |r| r.get(0))?)
    }
}
