use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::{error, info};

/// Unified trade journal — ONE table every engine writes closed trades to, so
/// `/pnl_all` and cross-engine analytics are a single query instead of five.
/// Engines still keep their own per-engine journals (for `/trend_history` etc.);
/// this is the consolidated analytics layer on top.
///
/// `engine`: grid | trend | swing | mr | signal. Opened shared (WAL) — each
/// engine constructs its own handle; they all hit data/trades.db.
pub struct UnifiedTradeJournal {
    conn: Mutex<Connection>,
}

fn migrations() -> Migrations<'static> {
    Migrations::new(vec![M::up(
        "CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            engine          TEXT NOT NULL,
            pair            TEXT NOT NULL,
            side            TEXT,
            entry_price     REAL,
            exit_price      REAL,
            quantity        REAL,
            pnl             REAL NOT NULL,
            exit_reason     TEXT,
            duration_mins   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_engine ON trades(engine);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_dedup ON trades(engine, timestamp, pair, pnl);",
    )])
}

impl UnifiedTradeJournal {
    pub fn new() -> Result<Self> {
        let path = std::env::var("TRADES_JOURNAL_PATH")
            .unwrap_or_else(|_| "data/trades.db".to_string());
        let p = PathBuf::from(&path);
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

    /// True if the unified table has no rows yet (→ backfill from per-engine journals).
    pub fn is_empty(&self) -> bool {
        let conn = self.conn.lock().unwrap();
        conn.query_row("SELECT COUNT(*) FROM trades", [], |r| r.get::<_, i64>(0))
            .unwrap_or(0) == 0
    }

    #[allow(clippy::too_many_arguments)]
    pub fn log_trade(
        &self,
        engine: &str,
        pair: &str,
        side: Option<&str>,
        entry_price: Option<f64>,
        exit_price: Option<f64>,
        quantity: Option<f64>,
        pnl: f64,
        exit_reason: Option<&str>,
        duration_mins: Option<i64>,
    ) {
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT OR IGNORE INTO trades
             (timestamp, engine, pair, side, entry_price, exit_price, quantity, pnl, exit_reason, duration_mins)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params![
                Utc::now().to_rfc3339(), engine, pair, side, entry_price, exit_price, quantity,
                pnl, exit_reason, duration_mins,
            ],
        ) {
            error!("Unified journal write failed: {}", e);
        }
    }

    /// One-time backfill: copy (engine, timestamp, pair, pnl) from each per-engine
    /// journal into the unified table. Called once on startup when unified is empty,
    /// BEFORE the engines trade — so there's no overlap with direct writes. Only the
    /// PnL-relevant columns are copied (enough for /pnl_all; full fields come from
    /// future direct writes).
    pub fn backfill_from_engine_journals(&self) -> Result<usize> {
        // (engine, db, table, pnl_col, pair_col, optional WHERE). NB signal_trades
        // uses `symbol` not `pair` — that mismatch silently skipped signal history.
        let sources: &[(&str, &str, &str, &str, &str, &str)] = &[
            ("grid",   "data/grid_journal.db",            "grid_trades",   "realized_pnl", "pair",   ""),
            ("trend",  "data/trend_journal.db",           "trend_trades",  "pnl",          "pair",   ""),
            ("swing",  "data/swing_journal.db",           "swing_trades",  "pnl",          "pair",   ""),
            ("mr",     "data/mean_reversion_journal.db",  "mr_trades",     "pnl",          "pair",   ""),
            ("signal", "data/signal_journal.db",          "signal_trades", "realized_pnl", "symbol", "WHERE action LIKE 'CLOSE_%'"),
        ];
        let mut total = 0usize;
        let conn = self.conn.lock().unwrap();
        // Clear backfilled rows + rebuild from the per-engine journals each startup.
        // Direct-writes from engines (exit_reason != 'backfilled') are preserved.
        // This prevents the duplication bug where append-only backfills accumulated
        // one copy per restart (10 restarts = 10× inflation).
        let _ = conn.execute("DELETE FROM trades WHERE exit_reason = 'backfilled'", []);
        for (engine, db, tbl, col, pair_col, where_clause) in sources {
            if !std::path::Path::new(db).exists() { continue; }
            match Connection::open(db) {
                Ok(src) => {
                    let sql = format!("SELECT timestamp, {}, {} FROM {} {}", pair_col, col, tbl, where_clause);
                    let mut stmt = match src.prepare(&sql) { Ok(s) => s, Err(e) => { error!("backfill {} prepare: {}", engine, e); continue; } };
                    let rows = stmt.query_map([], |r| {
                        Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, f64>(2)?))
                    });
                    if let Ok(rows) = rows {
                        for row in rows.flatten() {
                            let (ts, pair, pnl) = row;
                            let _ = conn.execute(
                                "INSERT OR IGNORE INTO trades (timestamp, engine, pair, pnl, exit_reason) VALUES (?1, ?2, ?3, ?4, 'backfilled')",
                                rusqlite::params![ts, engine, pair, pnl],
                            );
                            total += 1;
                        }
                    }
                }
                Err(e) => error!("backfill {} open: {}", engine, e),
            }
        }
        info!("Unified journal backfill: copied {} rows from per-engine journals", total);
        Ok(total)
    }
}

/// One-shot write used by each engine's close path. Opens a transient connection
/// (fine at close frequency) so engines need no field/init/import — just one call.
#[allow(clippy::too_many_arguments)]
pub fn log_unified(
    engine: &str,
    pair: &str,
    entry_price: Option<f64>,
    exit_price: Option<f64>,
    quantity: Option<f64>,
    pnl: f64,
    exit_reason: Option<&str>,
    duration_mins: Option<i64>,
) {
    if let Ok(j) = UnifiedTradeJournal::new() {
        j.log_trade(engine, pair, Some("BUY"), entry_price, exit_price, quantity, pnl, exit_reason, duration_mins);
    }
}

/// Rebuild the unified table from the per-engine journals on startup. Idempotent
/// (clears + re-copies), so it self-heals any backfill bug. Call before trading.
pub fn backfill_unified_if_empty() {
    match UnifiedTradeJournal::new() {
        Ok(j) => { let _ = j.backfill_from_engine_journals(); }
        Err(e) => error!("Unified journal init failed: {}", e),
    }
}

/// Cumulative realized PnL for one engine+pair, read from the unified table. The
/// single source of truth for startup seeding (replaces per-engine journal/json
/// seeding — one mechanism, no drift).
pub fn realized_pnl(engine: &str, pair: &str) -> f64 {
    match UnifiedTradeJournal::new() {
        Ok(j) => {
            let conn = j.conn.lock().unwrap();
            conn.query_row(
                "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE engine=?1 AND pair=?2",
                rusqlite::params![engine, pair],
                |row| row.get::<_, f64>(0),
            ).unwrap_or(0.0)
        }
        Err(_) => 0.0,
    }
}
