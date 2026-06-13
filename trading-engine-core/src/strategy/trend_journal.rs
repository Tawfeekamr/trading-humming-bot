use crate::models::order::OrderSide;
use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::error;

/// Persistent journal of closed trend trades — partial TP fills and full exits
/// (stop-loss, trailing stop, signal exit). Mirrors the legacy `trend_trades`
/// schema so pre-existing rows are preserved; a `pair` column is added via a
/// tracked migration (v2) so trades from multiple pairs are distinguishable.
pub struct TrendJournal {
    conn: Mutex<Connection>,
}

/// Schema migrations, applied via SQLite's `user_version` pragma so each step
/// runs exactly once per database file (no re-running, no "column already
/// exists" errors across restarts).
fn migrations() -> Migrations<'static> {
    Migrations::new(vec![
        // v1 — base table. CREATE TABLE IF NOT EXISTS leaves the legacy Python
        // engine's existing table (and its rows) untouched on EC2.
        M::up(
            "CREATE TABLE IF NOT EXISTS trend_trades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT NOT NULL,
                side             TEXT NOT NULL,
                entry_price      REAL NOT NULL,
                exit_price       REAL NOT NULL,
                amount           REAL NOT NULL,
                fee              REAL DEFAULT 0,
                pnl              REAL NOT NULL,
                pnl_pct          REAL NOT NULL,
                stop_loss        REAL NOT NULL,
                take_profit      REAL NOT NULL,
                exit_reason      TEXT NOT NULL,
                signal_score     INTEGER DEFAULT 0,
                duration_minutes INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_tt_timestamp ON trend_trades(timestamp);",
        ),
        // v2 — add `pair`. Runs once; skipped on every subsequent boot.
        M::up("ALTER TABLE trend_trades ADD COLUMN pair TEXT NOT NULL DEFAULT '';"),
    ])
}

impl TrendJournal {
    /// Open the production journal at `data/trend_journal.db`.
    /// The caller treats failure as soft (trading must never block on the journal).
    pub fn new() -> Result<Self> {
        let path = std::env::var("TREND_JOURNAL_PATH")
            .unwrap_or_else(|_| "data/trend_journal.db".to_string());
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
        // WAL + a busy timeout so concurrent per-pair writers (and concurrent
        // first-boot migrations) serialize instead of hard-failing.
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")?;
        let journal = Self { conn: Mutex::new(conn) };
        journal.init_db()?;
        Ok(journal)
    }

    fn init_db(&self) -> Result<()> {
        let mut conn = self.conn.lock().unwrap();
        // Migrations are transactional and versioned (user_version), so on a
        // simultaneous multi-pair boot only one connection performs the DDL;
        // the rest block on the write lock, then see the advanced version and
        // skip. No check-then-alter race.
        migrations().to_latest(&mut conn)?;
        Ok(())
    }

    /// Persist one close event (a partial TP fill or a full exit).
    #[allow(clippy::too_many_arguments)]
    pub fn log_trade(
        &self,
        pair: &str,
        side: OrderSide,
        entry_price: f64,
        exit_price: f64,
        amount: f64,
        pnl: f64,
        stop_loss: f64,
        take_profit: f64,
        exit_reason: &str,
        duration_minutes: i64,
    ) {
        let side_str = match side {
            OrderSide::Buy => "BUY",
            OrderSide::Sell => "SELL",
        };
        let cost_basis = (entry_price * amount).abs();
        let pnl_pct = if cost_basis > 0.0 { pnl / cost_basis * 100.0 } else { 0.0 };

        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT INTO trend_trades
             (timestamp, side, entry_price, exit_price, amount, fee, pnl, pnl_pct,
              stop_loss, take_profit, exit_reason, signal_score, duration_minutes, pair)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            rusqlite::params![
                Utc::now().to_rfc3339(),
                side_str,
                entry_price,
                exit_price,
                amount,
                0.0_f64,
                pnl,
                pnl_pct,
                stop_loss,
                take_profit,
                exit_reason,
                0_i64,
                duration_minutes,
                pair,
            ],
        ) {
            error!("Trend journal write failed: {}", e);
        }
    }
}
