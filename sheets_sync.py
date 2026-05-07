"""
sheets_sync.py
──────────────
Syncs every trade to Google Sheets automatically.
Sheet layout:
  Tab 1 "Trades"    — one row per trade, all fields
  Tab 2 "Daily PnL" — auto-aggregated by day
  Tab 3 "Summary"   — live totals (today / week / month / all-time)

Setup:
  1. Go to console.cloud.google.com
  2. Create a project → enable Google Sheets API + Google Drive API
  3. Create a Service Account → download JSON key
  4. Save key as: keys/google_service_account.json
  5. Share your Google Sheet with the service account email
  6. Set GOOGLE_SHEET_ID in .env
"""

import os
import json
from datetime import datetime
from pathlib import Path
from dataclasses import asdict

import gspread
from google.oauth2.service_account import Credentials

from src.journal.trade_journal import Trade, TradeJournal


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TRADES_HEADERS = [
    "ID", "Timestamp", "Pair", "Side", "Entry Price", "Exit Price",
    "Quantity (BTC)", "Gross PnL ($)", "Fee ($)", "Net PnL ($)",
    "Grid Level", "Duration (min)", "RSI", "BB Upper", "BB Lower",
    "EMA 200", "ATR", "Grid State"
]

DAILY_HEADERS = [
    "Date", "Total Trades", "Winning", "Losing", "Win Rate (%)",
    "Gross PnL ($)", "Fees ($)", "Net PnL ($)", "Best Trade ($)", "Worst Trade ($)"
]

SUMMARY_HEADERS = ["Period", "Trades", "Win Rate", "Net PnL ($)", "Fees ($)", "Best ($)", "Worst ($)"]


class SheetsSync:
    def __init__(self, journal: TradeJournal):
        self.journal = journal
        self.sheet_id = os.environ["GOOGLE_SHEET_ID"]
        self._client = None

    def _get_client(self) -> gspread.Client:
        if self._client:
            return self._client
        key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH", "keys/google_service_account.json")
        creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
        self._client = gspread.authorize(creds)
        return self._client

    def _get_or_create_worksheet(self, spreadsheet, title: str, rows=1000, cols=20):
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
            return ws

    def setup_sheet(self):
        """Run once to create tabs and headers."""
        client = self._get_client()
        ss = client.open_by_key(self.sheet_id)

        # Tab 1 — Trades
        trades_ws = self._get_or_create_worksheet(ss, "📋 Trades")
        if trades_ws.row_count < 2 or trades_ws.cell(1, 1).value != "ID":
            trades_ws.clear()
            trades_ws.append_row(TRADES_HEADERS)
            self._format_header(trades_ws)

        # Tab 2 — Daily PnL
        daily_ws = self._get_or_create_worksheet(ss, "📅 Daily PnL")
        if daily_ws.cell(1, 1).value != "Date":
            daily_ws.clear()
            daily_ws.append_row(DAILY_HEADERS)
            self._format_header(daily_ws)

        # Tab 3 — Summary
        summary_ws = self._get_or_create_worksheet(ss, "📊 Summary")
        if summary_ws.cell(1, 1).value != "Period":
            summary_ws.clear()
            summary_ws.append_row(SUMMARY_HEADERS)
            self._format_header(summary_ws)

        print("✅ Google Sheet setup complete.")

    def _format_header(self, ws):
        """Bold + background color for header row."""
        ws.format("1:1", {
            "textFormat": {"bold": True, "fontSize": 11},
            "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.15},
        })

    # ── Sync a Single Trade ────────────────────────────────────────

    def sync_trade(self, trade_id: int, trade: Trade):
        """Append one trade row to the Trades tab."""
        client = self._get_client()
        ss = client.open_by_key(self.sheet_id)
        ws = ss.worksheet("📋 Trades")

        row = [
            trade_id,
            trade.timestamp,
            trade.pair,
            trade.side,
            trade.entry_price,
            trade.exit_price,
            trade.quantity,
            round(trade.gross_pnl, 4),
            round(trade.fee, 4),
            round(trade.net_pnl, 4),
            trade.grid_level,
            trade.duration_min,
            round(trade.rsi, 2),
            round(trade.bb_upper, 2),
            round(trade.bb_lower, 2),
            round(trade.ema_200, 2),
            round(trade.atr, 2),
            trade.grid_state,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")

        # Color row green/red based on PnL
        last_row = len(ws.col_values(1))
        color = {"red": 0.85, "green": 0.95, "blue": 0.85} if trade.net_pnl > 0 \
               else {"red": 0.95, "green": 0.85, "blue": 0.85}
        ws.format(f"{last_row}:{last_row}", {"backgroundColor": color})

    # ── Refresh Daily Tab ──────────────────────────────────────────

    def refresh_daily_tab(self):
        """Rewrite the Daily PnL tab from SQLite data."""
        client = self._get_client()
        ss = client.open_by_key(self.sheet_id)
        ws = ss.worksheet("📅 Daily PnL")

        rows = self.journal._query("""
            SELECT
                DATE(timestamp)   AS date,
                COUNT(*)          AS total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS winning,
                SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losing,
                ROUND(SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS win_rate,
                ROUND(SUM(gross_pnl), 2) AS gross_pnl,
                ROUND(SUM(fee), 2)       AS fees,
                ROUND(SUM(net_pnl), 2)   AS net_pnl,
                ROUND(MAX(net_pnl), 2)   AS best_trade,
                ROUND(MIN(net_pnl), 2)   AS worst_trade
            FROM trades
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
        """)

        ws.clear()
        ws.append_row(DAILY_HEADERS)
        self._format_header(ws)

        for r in rows:
            ws.append_row(list(r.values()), value_input_option="USER_ENTERED")

    # ── Refresh Summary Tab ────────────────────────────────────────

    def refresh_summary_tab(self):
        """Update the live summary tab with all periods."""
        client = self._get_client()
        ss = client.open_by_key(self.sheet_id)
        ws = ss.worksheet("📊 Summary")

        periods = {
            "⏰ This Hour":  self.journal.summary_this_hour(),
            "📅 Today":      self.journal.summary_today(),
            "📆 This Week":  self.journal.summary_this_week(),
            "🗓 This Month": self.journal.summary_this_month(),
            "🏦 All Time":   self.journal.summary_all_time(),
        }

        ws.clear()
        ws.append_row(SUMMARY_HEADERS)
        self._format_header(ws)

        for label, s in periods.items():
            ws.append_row([
                label,
                s["total_trades"] or 0,
                f"{s['win_rate']}%",
                round(s["net_pnl"] or 0, 2),
                round(abs(s["total_fees"] or 0), 2),
                round(s["best_trade"] or 0, 2),
                round(s["worst_trade"] or 0, 2),
            ], value_input_option="USER_ENTERED")

        # Timestamp of last update
        ws.append_row([])
        ws.append_row([f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"])
