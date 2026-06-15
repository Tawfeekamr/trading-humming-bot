use crate::models::order::OrderSide;
use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::error;

pub struct SwingJournal {
    conn: Mutex<Connection>,
}

fn migrations() -> Migrations<'static> {
    Migrations::new(vec![M::up(
        "CREATE TABLE IF NOT EXISTS swing_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            pair            TEXT NOT NULL,
            side            TEXT NOT NULL,
            entry_price     REAL NOT NULL,
            exit_price      REAL NOT NULL,
            quantity        REAL NOT NULL,
            pnl             REAL NOT NULL,
            exit_reason     TEXT NOT NULL,
            duration_mins   INTEGER NOT NULL,
            runner_exit     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_st_timestamp ON swing_trades(timestamp);",
    )])
}

impl SwingJournal {
    pub fn new() -> Result<Self> {
        let path = std::env::var("SWING_JOURNAL_PATH")
            .unwrap_or_else(|_| "data/swing_journal.db".to_string());
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

    #[allow(clippy::too_many_arguments)]
    pub fn log_trade(
        &self,
        pair: &str,
        side: OrderSide,
        entry_price: f64,
        exit_price: f64,
        quantity: f64,
        pnl: f64,
        exit_reason: &str,
        duration_mins: i64,
        runner_exit: &str,
    ) {
        let side_str = match side {
            OrderSide::Buy => "BUY",
            OrderSide::Sell => "SELL",
        };
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT INTO swing_trades
             (timestamp, pair, side, entry_price, exit_price, quantity, pnl, exit_reason, duration_mins, runner_exit)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params![
                Utc::now().to_rfc3339(), pair, side_str, entry_price, exit_price, quantity, pnl, exit_reason, duration_mins, runner_exit,
            ],
        ) {
            error!("Swing journal write failed: {}", e);
        }
    }

    pub fn count(&self) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare("SELECT COUNT(*) FROM swing_trades")?;
        let count: usize = stmt.query_row([], |row| row.get(0))?;
        Ok(count)
    }

    pub fn realized_pnl(&self, pair: &str) -> f64 {
        let conn = self.conn.lock().unwrap();
        let mut stmt = match conn.prepare("SELECT SUM(pnl) FROM swing_trades WHERE pair = ?1") {
            Ok(s) => s,
            Err(_) => return 0.0,
        };
        stmt.query_row(rusqlite::params![pair], |row| {
            let pnl: Option<f64> = row.get(0)?;
            Ok(pnl.unwrap_or(0.0))
        }).unwrap_or(0.0)
    }
}
