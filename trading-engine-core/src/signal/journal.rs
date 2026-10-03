use crate::signal::types::SignalTrade;
use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::{error, info};

pub struct SignalJournal {
    conn: Mutex<Connection>,
}

impl SignalJournal {
    pub fn new() -> Result<Self> {
        Self::new_at(&PathBuf::from("data"))
    }

    /// Test seam: build the journal inside `dir` instead of the cwd `data/`.
    pub fn new_at(dir: &std::path::Path) -> Result<Self> {
        std::fs::create_dir_all(dir)?;
        let db_path = dir.join("signal_journal.db");
        // First-open race: when two openers hit a BRAND-NEW db file (fresh CI
        // checkout, parallel manage_* engine tests, restart overlap), both run
        // the WAL-mode transition on the empty file and the loser errors with
        // a lock failure the busy timeout does not cover (CI panic
        // "Signal journal creation failed", 2026-10-02). Its retry finds the
        // file already initialized and succeeds, so retry a few times before
        // giving up. Every step below is idempotent.
        let mut last_err = None;
        for _ in 0..5 {
            match Self::open_journal(&db_path) {
                Ok(journal) => return Ok(journal),
                Err(e) => {
                    last_err = Some(e);
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
            }
        }
        Err(last_err.unwrap())
    }

    fn open_journal(db_path: &std::path::Path) -> Result<Self> {
        let conn = Connection::open(db_path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL;")?;
        let journal = Self { conn: Mutex::new(conn) };
        journal.init_db()?;
        if let Ok(n) = journal.dedup_closes() {
            if n > 0 {
                info!("Signal journal dedup: removed {} duplicate CLOSE rows (kept partials + earliest full close per position)", n);
            }
        }
        Ok(journal)
    }

    fn init_db(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS raw_messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                channel_id    INTEGER,
                channel_name  TEXT,
                message_id    INTEGER,
                text          TEXT,
                parsed_action TEXT,
                parsed_pair   TEXT,
                parse_reasoning TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                symbol          TEXT NOT NULL,
                channel_name    TEXT,
                action          TEXT,
                entry_price     REAL,
                current_price   REAL,
                quantity        REAL,
                realized_pnl    REAL,
                exit_reason     TEXT,
                signal_confidence TEXT,
                stop_loss       REAL,
                take_profits    TEXT,
                tp1_hit         INTEGER DEFAULT 0,
                tp2_hit         INTEGER DEFAULT 0,
                tp3_hit         INTEGER DEFAULT 0,
                raw_message     TEXT,
                parse_reasoning TEXT,
                is_audit        INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_st_timestamp ON signal_trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_st_channel ON signal_trades(channel_name);
            CREATE INDEX IF NOT EXISTS idx_rm_timestamp ON raw_messages(timestamp);"
        )?;
        Ok(())
    }

    pub fn log_raw_message(
        &self,
        channel_id: i64,
        channel_name: &str,
        message_id: i32,
        text: &str,
        parsed_action: &str,
        parsed_pair: &str,
        parse_reasoning: &str,
    ) {
        let conn = self.conn.lock().unwrap();
        if let Err(e) = conn.execute(
            "INSERT INTO raw_messages (timestamp, channel_id, channel_name, message_id, text, parsed_action, parsed_pair, parse_reasoning)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            (Utc::now().to_rfc3339(), channel_id, channel_name, message_id, text, parsed_action, parsed_pair, parse_reasoning),
        ) {
            error!("Signal journal write failed: {}", e);
        }
    }

    pub fn log_trade(&self, trade: &SignalTrade) {
        let conn = self.conn.lock().unwrap();
        // Split into two inserts to avoid rusqlite's 16-param tuple limit
        // First: insert the trade with first 14 fields
        let result = conn.execute(
            "INSERT INTO signal_trades
             (timestamp, symbol, channel_name, action, entry_price, current_price, quantity,
              realized_pnl, exit_reason, signal_confidence, stop_loss, take_profits,
              tp1_hit, tp2_hit)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            rusqlite::params![
                &trade.timestamp, &trade.symbol, &trade.channel_name, &trade.action,
                trade.entry_price, trade.current_price, trade.quantity,
                trade.realized_pnl, &trade.exit_reason, &trade.signal_confidence,
                trade.stop_loss, &trade.take_profits,
                trade.tp1_hit, trade.tp2_hit,
            ],
        );
        // Then update the remaining fields
        if let Ok(_) = result {
            let row_id = conn.last_insert_rowid();
            let _ = conn.execute(
                "UPDATE signal_trades SET tp3_hit=?1, raw_message=?2, parse_reasoning=?3, is_audit=?4 WHERE id=?5",
                rusqlite::params![trade.tp3_hit, &trade.raw_message, &trade.parse_reasoning, trade.is_audit, row_id],
            );
        } else if let Err(e) = result {
            error!("Signal trade journal write failed: {}", e);
        }
    }

    pub fn summary(&self, days: i32) -> SummaryResult {
        let conn = self.conn.lock().unwrap();
        let where_clause = if days == 0 {
            format!("timestamp >= '{}'", Utc::now().format("%Y-%m-%d"))
        } else if days > 0 {
            let cutoff = Utc::now() - chrono::Duration::days(days as i64);
            format!("timestamp >= '{}'", cutoff.to_rfc3339())
        } else {
            "1=1".to_string()
        };

        match conn.query_row(
            &format!(
                "SELECT COUNT(*), COALESCE(SUM(realized_pnl), 0), COALESCE(AVG(realized_pnl), 0),
                        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
                 FROM signal_trades WHERE {}", where_clause),
            [],
            |row| {
                let total: i64 = row.get(0)?;
                let total_pnl: f64 = row.get(1)?;
                let _avg_pnl: f64 = row.get(2)?;
                let wins: i64 = row.get::<_, i64>(3)?;
                let win_rate = if total > 0 { wins as f64 / total as f64 * 100.0 } else { 0.0 };
                Ok(SummaryResult {
                    total_trades: total as u32,
                    total_pnl,
                    win_rate: (win_rate * 10.0).round() / 10.0,
                })
            }
        ) {
            Ok(s) => s,
            Err(_) => SummaryResult { total_trades: 0, total_pnl: 0.0, win_rate: 0.0 },
        }
    }

    /// Idempotent boot-time cleanup of ghost CLOSE rows left by the
    /// Rust/Python dual-write duplicate-close bug: after one manager closed a
    /// position, the other's stale snapshot re-closed it seconds later,
    /// journaling a duplicate FULL close.
    ///
    /// A position's legitimate lifecycle is: first tp1 partial, first tp2
    /// partial, and ONE full close (any reason other than tp1/tp2 — tp3,
    /// stop_loss, manual, trader_close). So we keep the earliest tp1/tp2 row
    /// per (symbol, entry_price) — preserving the partial ladder — plus the
    /// earliest FULL close per (symbol, entry_price), and delete every later
    /// full close. A ghost must not be rescued by its reason string: the
    /// racing mirror often logged the ghost under a DIFFERENT reason than the
    /// real close (real `tp3` + ghost `stop_loss`), so full-close reasons are
    /// never grouped — position-level "first full close wins" is the only rule.
    ///
    /// The earlier GROUP BY (symbol, entry_price) variant kept ONLY the
    /// earliest row per position, silently eating the tp2 and final-close legs
    /// of every multi-TP position on every boot since 2026-06-15. Safe to run
    /// on every boot (no-op once clean). Known limit: two separate positions
    /// in the same symbol at the identical entry price are indistinguishable
    /// (no position id exists in this schema).
    pub fn dedup_closes(&self) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let n = conn.execute(
            "DELETE FROM signal_trades
             WHERE action LIKE 'CLOSE_%'
               AND id NOT IN (
                 SELECT MIN(id) FROM signal_trades
                 WHERE action LIKE 'CLOSE_%'
                   AND exit_reason IN ('tp1','tp2')
                 GROUP BY symbol, entry_price, exit_reason
                 UNION
                 SELECT MIN(id) FROM signal_trades
                 WHERE action LIKE 'CLOSE_%'
                   AND exit_reason NOT IN ('tp1','tp2')
                 GROUP BY symbol, entry_price
               )",
            [],
        )?;
        Ok(n)
    }

    /// Every CLOSE_* trade as (symbol, entry_price, exit_reason, tp3_hit) — used
    /// by the startup self-heal to mark open-but-already-closed positions closed.
    pub fn closed_entries(&self) -> Result<Vec<(String, f64, String, bool)>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT symbol, entry_price, exit_reason, tp3_hit FROM signal_trades WHERE action LIKE 'CLOSE_%'"
        )?;
        let out = stmt.query_map([], |row| {
            let tp3: i32 = row.get(3)?;
            Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?, row.get::<_, String>(2)?, tp3 != 0))
        })?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(out)
    }

    pub fn recent_signals(&self, limit: usize) -> Vec<RecentSignal> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = match conn.prepare(
            "SELECT timestamp, channel_name, parsed_action, parsed_pair, text
             FROM raw_messages ORDER BY id DESC LIMIT ?1"
        ) {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };

        let rows = stmt.query_map([limit as i64], |row| {
            Ok(RecentSignal {
                timestamp: row.get(0).unwrap_or_default(),
                channel: row.get(1).unwrap_or_default(),
                action: row.get(2).unwrap_or_default(),
                pair: row.get(3).unwrap_or_default(),
                text: row.get(4).unwrap_or_default(),
            })
        });

        match rows {
            Ok(r) => r.filter_map(|x| x.ok()).collect(),
            Err(_) => Vec::new(),
        }
    }
}

pub struct SummaryResult {
    pub total_trades: u32,
    pub total_pnl: f64,
    pub win_rate: f64,
}

pub struct RecentSignal {
    pub timestamp: String,
    pub channel: String,
    pub action: String,
    pub pair: String,
    pub text: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close_trade(symbol: &str, entry: f64, reason: &str) -> SignalTrade {
        SignalTrade {
            timestamp: Utc::now().to_rfc3339(),
            symbol: symbol.to_string(),
            channel_name: "test".to_string(),
            action: format!("CLOSE_{}", reason),
            entry_price: entry,
            current_price: 100.0,
            quantity: 1.0,
            realized_pnl: 1.0,
            exit_reason: reason.to_string(),
            signal_confidence: "high".to_string(),
            stop_loss: 95.0,
            take_profits: "[]".to_string(),
            tp1_hit: 1,
            tp2_hit: 1,
            tp3_hit: 1,
            raw_message: String::new(),
            parse_reasoning: String::new(),
            is_audit: 0,
        }
    }

    /// Same as close_trade, with an explicit timestamp — the production ghost
    /// close landed seconds after the real one, and dedup must catch it at any
    /// time distance, not just same-instant writes.
    fn close_trade_at(symbol: &str, entry: f64, reason: &str, ts: &str) -> SignalTrade {
        let mut t = close_trade(symbol, entry, reason);
        t.timestamp = ts.to_string();
        t
    }

    fn journal_in_temp_dir() -> (SignalJournal, PathBuf) {
        // Uniqueness MUST NOT rely on the timestamp alone: two tests calling
        // this within the same clock tick (cargo test runs the module's tests
        // in parallel) silently shared ONE database file and deleted each
        // other's rows — a ~15% flake in both dedup tests. A process-global
        // counter is collision-proof regardless of timing.
        static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let n = SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "sig_journal_test_{}_{}",
            n,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let journal = SignalJournal::new_at(&dir).expect("journal in temp dir");
        (journal, dir)
    }

    /// The CLOSE rows still in the journal, as (symbol, exit_reason) pairs.
    fn surviving_closes(journal: &SignalJournal) -> Vec<(String, String)> {
        let conn = journal.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT symbol, exit_reason FROM signal_trades WHERE action LIKE 'CLOSE_%' ORDER BY id")
            .unwrap();
        stmt.query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap()
    }

    /// A position's legitimate lifecycle is tp1 + tp2 partials, then ONE full
    /// close. The dual-manager race appended a ghost re-close of the SAME
    /// remaining quantity seconds later — dedup must drop only that ghost, not
    /// the tp2/tp3 legs (the 2026-10-01 boot ate tp2+final rows because the old
    /// GROUP BY (symbol, entry_price) kept just the single earliest row).
    #[test]
    fn dedup_keeps_partials_and_drops_only_ghost_full_closes() {
        let (journal, dir) = journal_in_temp_dir();
        // Real lifecycle: tp1, tp2, tp3 full close …
        journal.log_trade(&close_trade("DASH-USDT", 59.115, "tp1"));
        journal.log_trade(&close_trade("DASH-USDT", 59.115, "tp2"));
        journal.log_trade(&close_trade("DASH-USDT", 59.115, "tp3"));
        // … then the Rust mirror's ghost re-close seconds later.
        journal.log_trade(&close_trade("DASH-USDT", 59.115, "stop_loss"));

        let n = journal.dedup_closes().expect("dedup runs");

        assert_eq!(n, 1, "only the ghost full close is a duplicate");
        assert_eq!(
            surviving_closes(&journal),
            vec![
                ("DASH-USDT".to_string(), "tp1".to_string()),
                ("DASH-USDT".to_string(), "tp2".to_string()),
                ("DASH-USDT".to_string(), "tp3".to_string()),
            ],
            "partials + the real full close must survive; only the ghost dies"
        );
        let _ = std::fs::remove_dir_all(dir);
    }

    /// Two stop_loss rows for one position (real close, then ghost): the ghost
    /// is the LATER full close and must be the one deleted.
    #[test]
    fn dedup_drops_later_ghost_of_same_reason() {
        let (journal, dir) = journal_in_temp_dir();
        journal.log_trade(&close_trade("GRAM-USDT", 1.3755, "stop_loss")); // real
        journal.log_trade(&close_trade("GRAM-USDT", 1.3755, "stop_loss")); // ghost

        let n = journal.dedup_closes().expect("dedup runs");

        assert_eq!(n, 1);
        assert_eq!(
            surviving_closes(&journal),
            vec![("GRAM-USDT".to_string(), "stop_loss".to_string())]
        );
        let _ = std::fs::remove_dir_all(dir);
    }

    /// The production double-close (4efac99, dual-manager race) was two exit
    /// signals for one position seconds apart — the real close, then the second
    /// manager's ghost of the same remaining quantity. Dedup must collapse that
    /// to exactly one journal row regardless of the time gap.
    #[test]
    fn ghost_close_4s_after_real_close_is_deduped() {
        let (journal, dir) = journal_in_temp_dir();
        journal.log_trade(&close_trade_at("SKY-USDT", 0.72, "stop_loss", "2026-10-01T12:00:00+00:00"));
        journal.log_trade(&close_trade_at("SKY-USDT", 0.72, "stop_loss", "2026-10-01T12:00:04+00:00"));

        let n = journal.dedup_closes().expect("dedup runs");

        assert_eq!(n, 1, "the 4s-later ghost is the duplicate");
        assert_eq!(
            surviving_closes(&journal).len(),
            1,
            "exactly one close survives for the position"
        );
        let _ = std::fs::remove_dir_all(dir);
    }

    /// Positions with a single clean close (the common case) are untouched,
    /// and re-running dedup on clean data deletes nothing (idempotent boot).
    #[test]
    fn dedup_is_noop_for_clean_history() {
        let (journal, dir) = journal_in_temp_dir();
        journal.log_trade(&close_trade("TRX-USDT", 0.338, "stop_loss"));
        journal.log_trade(&close_trade("XRP-USDT", 1.5, "tp3"));

        assert_eq!(journal.dedup_closes().unwrap(), 0);
        assert_eq!(journal.dedup_closes().unwrap(), 0, "idempotent on re-run");
        assert_eq!(surviving_closes(&journal).len(), 2);
        let _ = std::fs::remove_dir_all(dir);
    }

    /// Two SignalEngine instances can open the same BRAND-NEW journal file
    /// concurrently (cargo test runs the manage_* engine tests in parallel on
    /// a fresh CI checkout where data/signal_journal.db doesn't exist yet).
    /// Both run the WAL-mode first-initialization of the empty file; the
    /// loser's open errors with a lock/mode-transition failure that the busy
    /// timeout does NOT cover, which panicked "Signal journal creation
    /// failed" in CI on 2026-10-02. Both openers must succeed.
    #[test]
    fn concurrent_first_open_both_succeed() {
        for i in 0..10 {
            let dir = std::env::temp_dir().join(format!(
                "sig_journal_race_{}_{}",
                i,
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let opener_dir = dir.clone();
            let other = std::thread::spawn(move || SignalJournal::new_at(&opener_dir).is_ok());
            let mine = SignalJournal::new_at(&dir).is_ok();

            assert!(mine, "iter {i}: our open failed on a fresh db");
            assert!(other.join().unwrap(), "iter {i}: racing open failed on a fresh db");
            let _ = std::fs::remove_dir_all(dir);
        }
    }
}
