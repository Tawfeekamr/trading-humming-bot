use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::OnceLock;
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
    /// Directory holding the unified db — also where the per-engine journals
    /// live. Backfill reads them as siblings so a test (or alt deploy) that
    /// points TRADES_JOURNAL_PATH at a temp dir gets self-consistent sources.
    data_dir: PathBuf,
}

fn migrations() -> Migrations<'static> {
    Migrations::new(vec![
        M::up(
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
        ),
        M::up("ALTER TABLE trades ADD COLUMN is_backfilled INTEGER DEFAULT 0;"),
    ])
}

impl UnifiedTradeJournal {
    pub fn new() -> Result<Self> {
        let path = std::env::var("TRADES_JOURNAL_PATH")
            .unwrap_or_else(|_| "data/trades.db".to_string());
        Self::new_at(PathBuf::from(path))
    }

    /// Construct pointing at an explicit db path. The per-engine journals are
    /// resolved as siblings (same directory), so this is all a test needs to
    /// isolate the unified journal + its backfill sources in a temp dir without
    /// touching process-global env vars.
    pub fn new_at(path: PathBuf) -> Result<Self> {
        let data_dir = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("."));
        if !data_dir.as_os_str().is_empty() {
            std::fs::create_dir_all(&data_dir)?;
        }
        let conn = Connection::open(&path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")?;
        let journal = Self { conn: Mutex::new(conn), data_dir };
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
        // Subtract round-trip fees (0.1% maker each side = 0.2%) from the
        // gross P&L so /pnl_all and /trades show NET profit. The paper engine
        // already deducts fees from the balance; this makes the reporting match.
        const FEE_RATE: f64 = 0.001; // per side
        let net_pnl = if let (Some(ep), Some(xp), Some(qty)) = (entry_price, exit_price, quantity) {
            let notional = (ep * qty) + (xp * qty); // entry + exit notional
            pnl - (notional * FEE_RATE) // subtract round-trip fee
        } else {
            pnl // no notional info → log as-is (rare)
        };
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT OR IGNORE INTO trades
             (timestamp, engine, pair, side, entry_price, exit_price, quantity, pnl, exit_reason, duration_mins)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params![
                Utc::now().to_rfc3339(), engine, pair, side, entry_price, exit_price, quantity,
                net_pnl, exit_reason, duration_mins,
            ],
        ) {
            error!("Unified journal write failed: {}", e);
        }
    }

    /// True if a live (is_backfilled=0) row exists for `engine`+`pair` within
    /// ±60s of `ts` — i.e. the close was already written by `log_unified()` and
    /// the journal row we're about to backfill is a duplicate.
    ///
    /// Why a window and not an exact match: the live log stamps `Utc::now()` at
    /// write time while the per-engine journal stores the close's own timestamp,
    /// so the two are a few ms apart (and net-vs-raw PnL differs too, defeating
    /// the `UNIQUE(engine,timestamp,pair,pnl)` index). In `signal/engine.rs:
    /// record_close` the journal write and the `log_unified` call are
    /// back-to-back with no await between them, so 60s is a ~10,000x margin.
    /// Returns false on an unparseable timestamp (safe default: backfill it).
    fn live_log_exists(conn: &Connection, engine: &str, pair: &str, ts: &str) -> bool {
        let Some(t) = DateTime::parse_from_rfc3339(ts)
            .ok()
            .map(|d| d.with_timezone(&Utc))
        else {
            return false;
        };
        let lo = (t - Duration::seconds(60)).to_rfc3339();
        let hi = (t + Duration::seconds(60)).to_rfc3339();
        conn.query_row(
            "SELECT 1 FROM trades
             WHERE engine = ?1 AND pair = ?2 AND is_backfilled = 0
               AND timestamp BETWEEN ?3 AND ?4
             LIMIT 1",
            rusqlite::params![engine, pair, lo, hi],
            |_| Ok(true),
        )
        .unwrap_or(false)
    }

    /// Rebuild backfilled rows from the per-engine journals each startup. Direct
    /// writes from engines (is_backfilled=0) are preserved. Copies the real
    /// exit_reason so /trades shows [tp3], [stop_loss], etc. instead of [backfilled].
    pub fn backfill_from_engine_journals(&self) -> Result<usize> {
        // (engine, db_file, table, pnl_col, pair_col, reason_col, optional WHERE).
        // db_file is a bare filename resolved under self.data_dir (sibling of the
        // unified db) — see new_at().
        let sources: &[(&str, &str, &str, &str, &str, &str, &str)] = &[
            ("grid",   "grid_journal.db",            "grid_trades",   "realized_pnl", "pair",   "exit_reason", ""),
            ("trend",  "trend_journal.db",           "trend_trades",  "pnl",          "pair",   "exit_reason", ""),
            ("swing",  "swing_journal.db",           "swing_trades",  "pnl",          "pair",   "exit_reason", ""),
            ("mr",     "mean_reversion_journal.db",  "mr_trades",     "pnl",          "pair",   "exit_reason", ""),
            ("signal", "signal_journal.db",          "signal_trades", "realized_pnl", "symbol", "exit_reason", "WHERE action LIKE 'CLOSE_%'"),
        ];
        let mut total = 0usize;
        let conn = self.conn.lock().unwrap();
        // Clear backfilled rows + rebuild. Catches both formats:
        // - New format: is_backfilled=1 (real exit_reasons like tp3, stop_loss)
        // - Old format: exit_reason='backfilled' (from before is_backfilled column existed)
        // Direct writes from log_unified (is_backfilled=0, real exit_reason) are preserved.
        let _ = conn.execute("DELETE FROM trades WHERE is_backfilled = 1 OR exit_reason = 'backfilled'", []);
        // Purge phantom MR trades from the bar-replay bug.
        let _ = conn.execute(
            "DELETE FROM trades WHERE engine = 'mr' AND timestamp LIKE '2026-06-15T22:39%'",
            [],
        );
        for (engine, db_file, tbl, col, pair_col, reason_col, where_clause) in sources {
            let db = self.data_dir.join(db_file);
            if !db.exists() { continue; }
            match Connection::open(&db) {
                Ok(src) => {
                    // SELECT timestamp, pair, pnl, exit_reason — with graceful fallback
                    // if the reason column doesn't exist (uses 'fill' as default).
                    let has_reason = src.prepare(&format!("SELECT {} FROM {} LIMIT 0", reason_col, tbl)).is_ok();
                    let reason_expr = if has_reason { reason_col.to_string() } else { "'fill'".to_string() };
                    let sql = format!("SELECT timestamp, {}, {}, {} FROM {} {}", pair_col, col, reason_expr, tbl, where_clause);
                    let mut stmt = match src.prepare(&sql) { Ok(s) => s, Err(e) => { error!("backfill {} prepare: {}", engine, e); continue; } };
                    let rows = stmt.query_map([], |r| {
                        Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, f64>(2)?, r.get::<_, String>(3)?))
                    });
                    if let Ok(rows) = rows {
                        for row in rows.flatten() {
                            let (ts, pair, pnl, reason) = row;
                            // Skip closes already written live — see live_log_exists.
                            if Self::live_log_exists(&conn, engine, &pair, &ts) {
                                continue;
                            }
                            let _ = conn.execute(
                                "INSERT OR IGNORE INTO trades (timestamp, engine, pair, pnl, exit_reason, is_backfilled) VALUES (?1, ?2, ?3, ?4, ?5, 1)",
                                rusqlite::params![ts, engine, pair, pnl, reason],
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
/// Cached journal so connection setup + migrations run exactly once, not on every
/// log call (which raced under concurrent closes and churned a fresh connection +
/// migration-check per insert). (#2 of the concurrency audit.)
static JOURNAL: OnceLock<Option<UnifiedTradeJournal>> = OnceLock::new();

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
    let journal = JOURNAL.get_or_init(|| UnifiedTradeJournal::new().ok());
    if let Some(j) = journal.as_ref() {
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a signal_journal.db (sibling of the unified db) with the given closes.
    /// Mirrors the columns backfill reads: timestamp, symbol, action, realized_pnl,
    /// exit_reason.
    fn make_signal_journal(dir: &std::path::Path, rows: &[(String, String, f64)]) {
        let db = dir.join("signal_journal.db");
        let c = Connection::open(&db).unwrap();
        c.execute_batch(
            "CREATE TABLE IF NOT EXISTS signal_trades (
                id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, action TEXT,
                realized_pnl REAL, exit_reason TEXT)",
        )
        .unwrap();
        for (ts, sym, pnl) in rows {
            c.execute(
                "INSERT INTO signal_trades (timestamp, symbol, action, realized_pnl, exit_reason)
                 VALUES (?1, ?2, 'CLOSE_stop_loss', ?3, 'stop_loss')",
                rusqlite::params![ts, sym, pnl],
            )
            .unwrap();
        }
    }

    fn count(j: &UnifiedTradeJournal, engine: &str, pair: &str) -> i64 {
        let conn = j.conn.lock().unwrap();
        conn.query_row(
            "SELECT COUNT(*) FROM trades WHERE engine=?1 AND pair=?2",
            rusqlite::params![engine, pair],
            |r| r.get::<_, i64>(0),
        )
        .unwrap()
    }

    /// The bug: record_close() live-logs a signal close (is_backfilled=0, net PnL)
    /// AND writes signal_journal.db, which backfill then re-imports (raw PnL) → the
    /// same close appears twice. Backfill must skip a close already live-logged.
    #[test]
    fn backfill_skips_signal_close_already_live_logged() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();

        // 1. Live-log the close exactly as signal/engine.rs:record_close does.
        j.log_trade(
            "signal", "ADA-USDT", Some("BUY"),
            Some(0.65), Some(0.62), Some(5830.9), -84.716,
            Some("stop_loss"), None,
        );
        // 2. Same close is also in the per-engine journal (raw PnL, ~ms apart).
        make_signal_journal(tmp.path(), &[(Utc::now().to_rfc3339(), "ADA-USDT".into(), -82.8)]);

        j.backfill_from_engine_journals().unwrap();

        // Must be ONE row — the authoritative live log — not two.
        assert_eq!(count(&j, "signal", "ADA-USDT"), 1);
    }

    /// Backfill still recovers a close that was never live-logged (e.g. a crash
    /// between the journal write and log_unified). The dedup must not be so eager
    /// that it drops legitimate recovery rows.
    #[test]
    fn backfill_recovers_signal_close_when_not_live_logged() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();

        // No live row — only the journal has this close.
        make_signal_journal(tmp.path(), &[(Utc::now().to_rfc3339(), "ADA-USDT".into(), -82.8)]);

        j.backfill_from_engine_journals().unwrap();

        assert_eq!(count(&j, "signal", "ADA-USDT"), 1);
    }

    /// The dedup window is bounded: a live log from 5 minutes ago is a DIFFERENT
    /// (earlier) trade, so a new journal close must still be backfilled.
    #[test]
    fn backfill_inserts_when_live_log_is_outside_window() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();

        // An OLD live log (5 min ago) — distinct from the new close.
        let old_ts = (Utc::now() - Duration::minutes(5)).to_rfc3339();
        {
            let conn = j.conn.lock().unwrap();
            conn.execute(
                "INSERT INTO trades (timestamp, engine, pair, pnl, exit_reason, is_backfilled)
                 VALUES (?1, 'signal', 'ADA-USDT', -10.0, 'stop_loss', 0)",
                rusqlite::params![old_ts],
            )
            .unwrap();
        }
        make_signal_journal(tmp.path(), &[(Utc::now().to_rfc3339(), "ADA-USDT".into(), -82.8)]);

        j.backfill_from_engine_journals().unwrap();

        // Two distinct trades: the old live log + the recovered new close.
        assert_eq!(count(&j, "signal", "ADA-USDT"), 2);
    }
}
