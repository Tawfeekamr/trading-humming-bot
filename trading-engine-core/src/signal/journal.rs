use crate::signal::types::SignalTrade;
use anyhow::Result;
use chrono::Utc;
use rusqlite::Connection;
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::error;

pub struct SignalJournal {
    conn: Mutex<Connection>,
}

impl SignalJournal {
    pub fn new() -> Result<Self> {
        let dir = PathBuf::from("data");
        std::fs::create_dir_all(&dir)?;
        let db_path = dir.join("signal_journal.db");
        let conn = Connection::open(&db_path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL;")?;
        let journal = Self { conn: Mutex::new(conn) };
        journal.init_db()?;
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
