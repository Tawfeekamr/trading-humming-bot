use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use rusqlite::Connection;
use rusqlite_migration::{M, Migrations};
use serde::Serialize;
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

#[derive(Debug, Clone, Serialize)]
pub struct PaperPromotionCandidate {
    pub engine: String,
    pub pair: String,
    pub trades: usize,
    pub net_pnl: f64,
    pub gross_win: f64,
    pub gross_loss: f64,
    pub profit_factor: f64,
    pub win_rate_pct: f64,
    pub first_trade: String,
    pub last_trade: String,
    pub state: String,
    pub open_orders: usize,
    pub status_pnl: f64,
    pub status_details: String,
    pub promotable: bool,
    pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PaperPromotionReport {
    pub ready: bool,
    pub min_trades: usize,
    pub min_profit_factor: f64,
    pub min_win_rate_pct: f64,
    pub candidates: Vec<PaperPromotionCandidate>,
}

/// One row of the unified trades table, serialized for `/api/v1/trades`.
#[derive(Debug, Clone, Serialize)]
pub struct TradeRow {
    pub id: i64,
    pub timestamp: String,
    pub engine: String,
    pub pair: String,
    pub side: Option<String>,
    pub entry_price: Option<f64>,
    pub exit_price: Option<f64>,
    pub quantity: Option<f64>,
    pub pnl: f64,
    pub exit_reason: Option<String>,
    pub duration_mins: Option<i64>,
    // ── audit payload (additive; NULL for rows logged before this migration) ──
    pub sl_price: Option<f64>,
    pub tp_price: Option<f64>,
    pub signal_score: Option<f64>,
    pub regime_at_entry: Option<String>,
    pub entry_reason: Option<String>,
    pub fees: Option<f64>,
    pub r_multiple: Option<f64>,
    pub context_json: Option<String>,
}

/// The full context around one trade, persisted for auditability. Each engine
/// fills what it has; the rest stay `None`. `context_json` is the escape hatch
/// for engine-specific detail (indicator snapshots, signal factors, raw message)
/// so a new field never needs a migration.
#[derive(Debug, Default, Clone)]
pub struct TradeContext {
    pub sl_price: Option<f64>,
    pub tp_price: Option<f64>,
    pub signal_score: Option<f64>,
    pub regime_at_entry: Option<String>,
    pub entry_reason: Option<String>,
    pub fees: Option<f64>,
    pub r_multiple: Option<f64>,
    pub context_json: Option<String>,
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
        // Audit payload — additive, nullable. Old rows stay NULL.
        M::up(
            "ALTER TABLE trades ADD COLUMN sl_price REAL;
             ALTER TABLE trades ADD COLUMN tp_price REAL;
             ALTER TABLE trades ADD COLUMN signal_score REAL;
             ALTER TABLE trades ADD COLUMN regime_at_entry TEXT;
             ALTER TABLE trades ADD COLUMN entry_reason TEXT;
             ALTER TABLE trades ADD COLUMN fees REAL;
             ALTER TABLE trades ADD COLUMN r_multiple REAL;
             ALTER TABLE trades ADD COLUMN context_json TEXT;",
        ),
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
        ctx: &TradeContext,
    ) {
        // Subtract round-trip fees (0.1% maker each side = 0.2%) from the
        // gross P&L so /pnl_all and /trades show NET profit. The paper engine
        // already deducts fees from the balance; this makes the reporting match.
        const FEE_RATE: f64 = 0.001; // per side
        let (net_pnl, computed_fees) = if let (Some(ep), Some(xp), Some(qty)) = (entry_price, exit_price, quantity) {
            let notional = (ep * qty) + (xp * qty); // entry + exit notional
            (pnl - (notional * FEE_RATE), Some(notional * FEE_RATE))
        } else {
            (pnl, None) // no notional info → log as-is (rare)
        };
        let fees = ctx.fees.or(computed_fees);
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT OR IGNORE INTO trades
             (timestamp, engine, pair, side, entry_price, exit_price, quantity, pnl, exit_reason,
              duration_mins, sl_price, tp_price, signal_score, regime_at_entry, entry_reason,
              fees, r_multiple, context_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18)",
            rusqlite::params![
                Utc::now().to_rfc3339(), engine, pair, side, entry_price, exit_price, quantity,
                net_pnl, exit_reason, duration_mins,
                ctx.sl_price, ctx.tp_price, ctx.signal_score, ctx.regime_at_entry, ctx.entry_reason,
                fees, ctx.r_multiple, ctx.context_json,
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
    /// Live trades for the `/api/v1/trades` dashboard endpoint. Returns only
    /// direct engine writes (is_backfilled=0), optionally filtered by engine /
    /// pair, capped to the most recent `limit`, ordered oldest→newest so the
    /// frontend can plot them left→right on the chart.
    pub fn recent_trades(
        &self,
        engine: Option<&str>,
        pair: Option<&str>,
        limit: u32,
    ) -> Result<Vec<TradeRow>> {
        let conn = self.conn.lock().unwrap();
        // (?N IS NULL OR col = ?N) lets one fixed SQL string cover all
        // filter combinations — no dynamic param binding (rusqlite rejects
        // unused named params).
        let mut stmt = conn.prepare(
            "SELECT id, timestamp, engine, pair, side, entry_price, exit_price,
                    quantity, pnl, exit_reason, duration_mins,
                    sl_price, tp_price, signal_score, regime_at_entry, entry_reason,
                    fees, r_multiple, context_json
             FROM trades
             WHERE is_backfilled = 0
               AND (?1 IS NULL OR engine = ?1)
               AND (?2 IS NULL OR pair = ?2)
             ORDER BY timestamp DESC LIMIT ?3",
        )?;
        let rows = stmt.query_map(
            rusqlite::params![engine, pair, limit],
            |r| Ok(TradeRow {
                id: r.get(0)?,
                timestamp: r.get(1)?,
                engine: r.get(2)?,
                pair: r.get(3)?,
                side: r.get(4)?,
                entry_price: r.get(5)?,
                exit_price: r.get(6)?,
                quantity: r.get(7)?,
                pnl: r.get(8)?,
                exit_reason: r.get(9)?,
                duration_mins: r.get(10)?,
                sl_price: r.get(11)?,
                tp_price: r.get(12)?,
                signal_score: r.get(13)?,
                regime_at_entry: r.get(14)?,
                entry_reason: r.get(15)?,
                fees: r.get(16)?,
                r_multiple: r.get(17)?,
                context_json: r.get(18)?,
            }),
        )?;
        let mut out: Vec<TradeRow> = rows.collect::<Result<_, _>>()?;
        out.reverse(); // picked DESC (most recent) → return ascending
        Ok(out)
    }

    /// Copy signal trades' real SL / TP1 / reasoning / TP-ladder from
    /// signal_journal.db into the trades.db audit columns (for rows where they're
    /// still NULL — logged before the audit migration, or backfilled rows).
    /// Idempotent: only fills NULLs, so it's safe to run every startup.
    pub fn enrich_signal_audit(&self) -> Result<usize> {
        let db = self.data_dir.join("signal_journal.db");
        if !db.exists() { return Ok(0); }
        let mut conn = self.conn.lock().unwrap();
        let _ = conn.execute("DETACH DATABASE sig;", []); // ignore "no such database" if not attached
        conn.execute("ATTACH DATABASE ?1 AS sig;", rusqlite::params![db.to_string_lossy()])?;
        let updated = conn.execute(
            "UPDATE trades AS t
             SET sl_price     = COALESCE(t.sl_price, s.stop_loss),
                 tp_price     = COALESCE(t.tp_price, json_extract(s.take_profits, '$[0]')),
                 entry_reason = COALESCE(t.entry_reason, s.signal_confidence),
                 context_json = COALESCE(t.context_json, json_object(
                    'take_profits', s.take_profits,
                    'tp_hits', json_object('tp1', s.tp1_hit, 'tp2', s.tp2_hit, 'tp3', s.tp3_hit),
                    'channel', s.channel_name,
                    'raw_message', substr(s.raw_message, 1, 500)
                 ))
             FROM sig.signal_trades AS s
             WHERE t.engine = 'signal'
               AND s.symbol = t.pair
               AND ABS(strftime('%s', s.timestamp) - strftime('%s', t.timestamp)) < 60
               AND (t.sl_price IS NULL OR t.tp_price IS NULL OR t.entry_reason IS NULL OR t.context_json IS NULL)",
            [],
        )?;
        let _ = conn.execute("DETACH DATABASE sig;", []);
        info!("Signal audit enrichment: updated {} rows from signal_journal.db", updated);
        Ok(updated)
    }

    pub fn promotion_report_since(
        &self,
        statuses: &[crate::strategy::StrategyStatus],
        min_trades: usize,
        min_profit_factor: f64,
        min_win_rate_pct: f64,
    ) -> Result<PaperPromotionReport> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT engine, pair,
                    COUNT(*) AS trades,
                    COALESCE(SUM(pnl), 0.0) AS net_pnl,
                    COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0.0) AS gross_win,
                    ABS(COALESCE(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END), 0.0)) AS gross_loss,
                    COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
                    MIN(timestamp) AS first_trade,
                    MAX(timestamp) AS last_trade
             FROM trades
             WHERE engine IN ('grid', 'trend', 'swing', 'mr')
             GROUP BY engine, pair
             ORDER BY net_pnl DESC, trades DESC",
        )?;
        let mut rows = stmt.query([])?;
        let mut candidates = Vec::new();
        while let Some(row) = rows.next()? {
            let engine: String = row.get(0)?;
            let pair: String = row.get(1)?;
            let trades_i: i64 = row.get(2)?;
            let trades = trades_i.max(0) as usize;
            let net_pnl: f64 = row.get(3)?;
            let gross_win: f64 = row.get(4)?;
            let gross_loss: f64 = row.get(5)?;
            let wins_i: i64 = row.get(6)?;
            let first_trade: String = row.get(7)?;
            let last_trade: String = row.get(8)?;
            let profit_factor = if gross_loss > 0.0 {
                gross_win / gross_loss
            } else if gross_win > 0.0 {
                f64::INFINITY
            } else {
                0.0
            };
            let win_rate_pct = if trades > 0 {
                (wins_i.max(0) as f64) * 100.0 / trades as f64
            } else {
                0.0
            };
            let status = statuses
                .iter()
                .find(|s| s.name == engine && s.pair == pair);
            let state = status.map(|s| s.state.clone()).unwrap_or_else(|| "UNKNOWN".to_string());
            let open_orders = status.map(|s| s.open_orders).unwrap_or(0);
            let status_pnl = status.map(|s| s.pnl).unwrap_or(0.0);
            let status_details = status.map(|s| s.details.clone()).unwrap_or_else(|| "No live status snapshot".to_string());
            let mut blockers = Vec::new();
            if trades < min_trades {
                blockers.push(format!("trades {trades} < {min_trades}"));
            }
            if profit_factor < min_profit_factor {
                blockers.push(format!("profit_factor {:.2} < {:.2}", profit_factor, min_profit_factor));
            }
            if win_rate_pct < min_win_rate_pct {
                blockers.push(format!("win_rate {:.1}% < {:.1}%", win_rate_pct, min_win_rate_pct));
            }
            if state == "POSITION" {
                blockers.push("open position".to_string());
            }
            let promotable = blockers.is_empty();
            candidates.push(PaperPromotionCandidate {
                engine,
                pair,
                trades,
                net_pnl,
                gross_win,
                gross_loss,
                profit_factor,
                win_rate_pct,
                first_trade,
                last_trade,
                state,
                open_orders,
                status_pnl,
                status_details,
                promotable,
                blockers,
            });
        }
        let ready = candidates.iter().any(|c| c.promotable);
        Ok(PaperPromotionReport {
            ready,
            min_trades,
            min_profit_factor,
            min_win_rate_pct,
            candidates,
        })
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
    side: Option<&str>,
    entry_price: Option<f64>,
    exit_price: Option<f64>,
    quantity: Option<f64>,
    pnl: f64,
    exit_reason: Option<&str>,
    duration_mins: Option<i64>,
    ctx: &TradeContext,
) {
    let journal = JOURNAL.get_or_init(|| UnifiedTradeJournal::new().ok());
    if let Some(j) = journal.as_ref() {
        j.log_trade(engine, pair, side, entry_price, exit_price, quantity, pnl, exit_reason, duration_mins, ctx);
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

/// On startup: backfill signal trades' audit fields (SL/TP/reasoning/TP-ladder)
/// from signal_journal.db into trades.db. Idempotent — safe every boot.
pub fn enrich_signal_audit_if_present() {
    match UnifiedTradeJournal::new() {
        Ok(j) => { let _ = j.enrich_signal_audit(); }
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
            Some("stop_loss"), None, &TradeContext::default(),
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

    #[test]
    fn promotion_report_marks_profitable_ready_strategy() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();
        let now = Utc::now().to_rfc3339();
        {
            let conn = j.conn.lock().unwrap();
            for pnl in [100.0, -40.0, 80.0] {
                conn.execute(
                    "INSERT INTO trades (timestamp, engine, pair, pnl, exit_reason, is_backfilled)
                     VALUES (?1, 'grid', 'ETH-USDT', ?2, 'test', 0)",
                    rusqlite::params![now, pnl],
                ).unwrap();
            }
        }
        let statuses = vec![crate::strategy::StrategyStatus {
            name: "grid".to_string(),
            pair: "ETH-USDT".to_string(),
            state: "WAITING".to_string(),
            pnl: 140.0,
            open_orders: 0,
            details: "Ready".to_string(),
        }];

        let report = j.promotion_report_since(&statuses, 3, 2.0, 50.0).unwrap();

        assert!(report.ready);
        assert_eq!(report.candidates[0].engine, "grid");
        assert_eq!(report.candidates[0].pair, "ETH-USDT");
        assert_eq!(report.candidates[0].trades, 3);
        assert!((report.candidates[0].profit_factor - 4.5).abs() < 1e-9);
        assert!(report.candidates[0].promotable);
    }

    /// /api/v1/trades backing query — what the local dashboard polls. Must
    /// return ONLY live rows (is_backfilled=0), honor engine/pair filters,
    /// cap to the most recent `limit`, and expose entry/exit/qty/pnl/reason.
    fn insert_full(
        j: &UnifiedTradeJournal, ts: &str, engine: &str, pair: &str, side: Option<&str>,
        entry: Option<f64>, exitp: Option<f64>, qty: Option<f64>, pnl: f64,
        reason: Option<&str>, dur: Option<i64>, backfilled: bool,
    ) {
        let conn = j.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO trades (timestamp, engine, pair, side, entry_price, exit_price,
                 quantity, pnl, exit_reason, duration_mins, is_backfilled)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",
            rusqlite::params![ts, engine, pair, side, entry, exitp, qty, pnl, reason, dur, backfilled as i64],
        ).unwrap();
    }

    #[test]
    fn recent_trades_returns_live_rows_filtered_capped_and_ascending() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();
        // Three live rows inserted out of chronological order + one backfilled row.
        insert_full(&j, "2026-07-20T10:00:00Z", "grid",  "ETH-USDT", Some("BUY"),
            Some(1800.0), Some(1810.0), Some(1.0), 10.0, Some("tp1"), Some(60), false);
        insert_full(&j, "2026-07-18T08:00:00Z", "grid",  "BNB-USDT", Some("BUY"),
            Some(600.0),  Some(612.0),  Some(5.0), 60.0, Some("tp2"), Some(40), false);
        insert_full(&j, "2026-07-19T09:00:00Z", "trend", "ETH-USDT", Some("BUY"),
            Some(1790.0), Some(1850.0), Some(2.0), 120.0, Some("tp3"), Some(300), false);
        insert_full(&j, "2026-07-17T00:00:00Z", "grid",  "ETH-USDT", None,
            None, None, None, -5.0, Some("backfilled"), None, true);

        // No filter → 3 live rows (backfill excluded), ordered ascending by time.
        let all = j.recent_trades(None, None, 100).unwrap();
        assert_eq!(all.len(), 3, "backfilled row must be excluded");
        assert_eq!(all[0].pair, "BNB-USDT");               // 07-18
        assert_eq!(all[1].pair, "ETH-USDT");               // 07-19 trend
        assert_eq!(all[1].engine, "trend");
        assert_eq!(all[2].engine, "grid");                 // 07-20 grid
        // Fields round-trip.
        assert_eq!(all[2].side.as_deref(), Some("BUY"));
        assert_eq!(all[2].entry_price, Some(1800.0));
        assert_eq!(all[2].exit_price, Some(1810.0));
        assert_eq!(all[2].quantity, Some(1.0));
        assert_eq!(all[2].pnl, 10.0);
        assert_eq!(all[2].exit_reason.as_deref(), Some("tp1"));
        assert_eq!(all[2].duration_mins, Some(60));

        // Engine filter.
        let grid = j.recent_trades(Some("grid"), None, 100).unwrap();
        assert_eq!(grid.len(), 2);
        assert!(grid.iter().all(|t| t.engine == "grid"));

        // Pair filter.
        let eth = j.recent_trades(None, Some("ETH-USDT"), 100).unwrap();
        assert_eq!(eth.len(), 2);
        assert!(eth.iter().all(|t| t.pair == "ETH-USDT"));

        // Limit = most recent N, still returned ascending.
        let limited = j.recent_trades(None, None, 2).unwrap();
        assert_eq!(limited.len(), 2);
        assert_eq!(limited[0].engine, "trend");   // 07-19 (older of the two latest)
        assert_eq!(limited[1].engine, "grid");    // 07-20
    }

    #[test]
    fn log_trade_persists_audit_context_and_round_trips() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();
        let ctx = TradeContext {
            sl_price: Some(1790.0),
            tp_price: Some(1850.0),
            signal_score: Some(4.0),
            regime_at_entry: Some("ranging@0.97".into()),
            entry_reason: Some("ema_cross+rsi+regime".into()),
            fees: Some(0.62),
            r_multiple: Some(1.0),
            context_json: Some(r#"{"factors":["ema30>40","rsi58"]}"#.into()),
        };
        j.log_trade(
            "trend", "ETH-USDT", Some("buy"), Some(1800.0), Some(1850.0), Some(2.0),
            100.0, Some("tp1"), Some(60), &ctx,
        );
        let rows = j.recent_trades(None, None, 10).unwrap();
        assert_eq!(rows.len(), 1);
        let r = &rows[0];
        assert_eq!(r.sl_price, Some(1790.0));
        assert_eq!(r.tp_price, Some(1850.0));
        assert_eq!(r.signal_score, Some(4.0));
        assert_eq!(r.regime_at_entry.as_deref(), Some("ranging@0.97"));
        assert_eq!(r.entry_reason.as_deref(), Some("ema_cross+rsi+regime"));
        assert_eq!(r.fees, Some(0.62));
        assert_eq!(r.r_multiple, Some(1.0));
        assert_eq!(r.context_json.as_deref(), Some(r#"{"factors":["ema30>40","rsi58"]}"#));
    }

    #[test]
    fn enrich_signal_audit_copies_levels_from_signal_journal() {
        let tmp = tempfile::tempdir().unwrap();
        let j = UnifiedTradeJournal::new_at(tmp.path().join("trades.db")).unwrap();
        // A signal trade in trades.db with NULL audit fields.
        {
            let c = j.conn.lock().unwrap();
            c.execute(
                "INSERT INTO trades (timestamp, engine, pair, side, entry_price, exit_price, quantity, pnl, exit_reason, is_backfilled)
                 VALUES ('2026-07-22T07:37:00Z','signal','CRO-USDT','BUY',0.0576,0.0580,29084.0,16.85,'tp1',0)",
                [],
            ).unwrap();
        }
        // The matching rich row in signal_journal.db (full schema).
        let s = Connection::open(tmp.path().join("signal_journal.db")).unwrap();
        s.execute_batch(
            "CREATE TABLE signal_trades (id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT,
             channel_name TEXT, action TEXT, entry_price REAL, current_price REAL, quantity REAL,
             realized_pnl REAL, exit_reason TEXT, signal_confidence TEXT, stop_loss REAL,
             take_profits TEXT, tp1_hit INTEGER, tp2_hit INTEGER, tp3_hit INTEGER, raw_message TEXT);",
        ).unwrap();
        s.execute(
            "INSERT INTO signal_trades (timestamp,symbol,channel_name,action,entry_price,current_price,quantity,realized_pnl,exit_reason,signal_confidence,stop_loss,take_profits,tp1_hit,tp2_hit,tp3_hit,raw_message)
             VALUES ('2026-07-22T07:37:05Z','CRO-USDT','BK','CLOSE_tp1',0.0576,0.0580,29084.0,16.85,'tp1','HIGH',0.0550,'[0.058,0.060,0.063]',1,0,0,'buy CRO here')",
            [],
        ).unwrap();

        let n = j.enrich_signal_audit().unwrap();
        assert_eq!(n, 1, "one signal row should be enriched");
        let row: (Option<f64>, Option<f64>, Option<String>, Option<String>) = {
            let c = j.conn.lock().unwrap();
            c.query_row(
                "SELECT sl_price, tp_price, entry_reason, context_json FROM trades WHERE engine='signal'",
                [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            ).unwrap()
        };
        assert_eq!(row.0, Some(0.0550));            // sl_price <- stop_loss
        assert_eq!(row.1, Some(0.058));             // tp_price <- take_profits[0]
        assert_eq!(row.2.as_deref(), Some("HIGH")); // entry_reason <- signal_confidence
        assert!(row.3.as_deref().unwrap().contains("\"tp1\":1")); // context_json has tp-hit
    }
}
