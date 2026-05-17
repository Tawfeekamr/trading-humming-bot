"""
pnl_reporter.py
───────────────
Sends formatted P&L summaries to Telegram.
Covers: per-trade alerts, hourly, daily, weekly, monthly reports.
Schedule via APScheduler or call directly from Hummingbot script.
"""

import os
import asyncio
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode
from src.journal.trade_journal import TradeJournal, Trade


class PnLReporter:
    def __init__(self, journal: TradeJournal):
        self.journal = journal
        self.bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
        self.chat_id = os.environ["TELEGRAM_CHAT_ID"]
        self.env = os.environ.get("ENV", "paper").upper()

    async def _send(self, message: str):
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode=ParseMode.HTML
        )

    # ── Per-Trade Alert ────────────────────────────────────────────

    async def alert_trade(self, trade: Trade):
        """Sent immediately after every trade closes."""
        emoji   = "💚" if trade.net_pnl > 0 else "🔴"
        sign    = "+" if trade.net_pnl > 0 else ""
        side_em = "📈 BUY" if trade.side == "BUY" else "📉 SELL"

        msg = (
            f"{emoji} <b>Trade Closed — {trade.pair}</b>\n"
            f"•••\n"
            f"{side_em}  |  Grid Level {trade.grid_level}\n"
            f"⏱ Duration:    {trade.duration_min} min\n"
            f"🔵 Entry:      ${trade.entry_price:,.2f}\n"
            f"🔵 Exit:       ${trade.exit_price:,.2f}\n"
            f"📦 Qty:        {trade.quantity}\n"
            f"•••\n"
            f"💰 Gross PnL:  {sign}${trade.gross_pnl:.2f}\n"
            f"💸 Fee:        -${abs(trade.fee):.2f}\n"
            f"<b>📊 Net PnL:   {sign}${trade.net_pnl:.2f}</b>\n"
            f"•••\n"
            f"RSI: {trade.rsi:.1f}  |  Grid: {trade.grid_state}\n"
            f"🌐 Mode: {self.env}"
        )
        await self._send(msg)

    # ── Hourly Report ──────────────────────────────────────────────

    async def report_hourly(self):
        s = self.journal.summary_this_hour()
        if s["total_trades"] == 0:
            return  # silence if no trades this hour

        sign = "+" if (s["net_pnl"] or 0) >= 0 else ""
        msg = (
            f"⏰ <b>Hourly Report — SOL/USDT</b>\n"
            f"•••\n"
            f"📊 Trades:     {s['total_trades']}  "
            f"(✅{s['winning']} / ❌{s['losing']})\n"
            f"🎯 Win Rate:   {s['win_rate']}%\n"
            f"•••\n"
            f"💰 Gross:      {sign}${s['gross_pnl']:.2f}\n"
            f"💸 Fees:       -${abs(s['total_fees']):.2f}\n"
            f"<b>📈 Net PnL:   {sign}${s['net_pnl']:.2f}</b>\n"
            f"🌐 Mode: {self.env}"
        )
        await self._send(msg)

    # ── Daily Report ───────────────────────────────────────────────

    async def report_daily(self):
        s   = self.journal.summary_today()
        sw  = self.journal.summary_this_week()
        sm  = self.journal.summary_this_month()
        bw  = self.journal.best_worst_trades(limit=1)

        sign_d = "+" if (s["net_pnl"] or 0) >= 0 else ""
        sign_w = "+" if (sw["net_pnl"] or 0) >= 0 else ""
        sign_m = "+" if (sm["net_pnl"] or 0) >= 0 else ""

        best  = bw["best"][0]  if bw["best"]  else None
        worst = bw["worst"][0] if bw["worst"] else None

        msg = (
            f"📅 <b>Daily Report — {datetime.utcnow().strftime('%b %d, %Y')}</b>\n"
            f"•••\n"
            f"📊 Trades:      {s['total_trades']}  "
            f"(✅{s['winning']} / ❌{s['losing']})\n"
            f"🎯 Win Rate:    {s['win_rate']}%\n"
            f"•••\n"
            f"💰 Gross PnL:   {sign_d}${s['gross_pnl']:.2f}\n"
            f"💸 Fees paid:   -${abs(s['total_fees']):.2f}\n"
            f"<b>📈 Net Today:  {sign_d}${s['net_pnl']:.2f}</b>\n"
            f"•••\n"
            f"📆 This Week:   {sign_w}${sw['net_pnl']:.2f}\n"
            f"🗓 This Month:  {sign_m}${sm['net_pnl']:.2f}\n"
            f"🌐 Mode: {self.env}\n"
        )

        if best:
            msg += (
                f"•••\n"
                f"🏆 Best trade:  +${best['net_pnl']:.2f} "
                f"@ ${best['exit_price']:,.0f}\n"
            )
        if worst:
            msg += (
                f"💔 Worst trade: ${worst['net_pnl']:.2f} "
                f"@ ${worst['exit_price']:,.0f}\n"
            )

        await self._send(msg)

    # ── Monthly Report ─────────────────────────────────────────────

    async def report_monthly(self):
        s  = self.journal.summary_this_month()
        sa = self.journal.summary_all_time()

        sign_m = "+" if (s["net_pnl"] or 0) >= 0 else ""
        sign_a = "+" if (sa["net_pnl"] or 0) >= 0 else ""
        month  = datetime.utcnow().strftime("%B %Y")

        msg = (
            f"🗓 <b>Monthly Report — {month}</b>\n"
            f"•••\n"
            f"📊 Total Trades:   {s['total_trades']}\n"
            f"✅ Winning:        {s['winning']}  ({s['win_rate']}%)\n"
            f"❌ Losing:         {s['losing']}\n"
            f"•••\n"
            f"💰 Gross PnL:      {sign_m}${s['gross_pnl']:.2f}\n"
            f"💸 Total Fees:     -${abs(s['total_fees']):.2f}\n"
            f"<b>📈 Net PnL:      {sign_m}${s['net_pnl']:.2f}</b>\n"
            f"📊 Avg per trade:  {sign_m}${s['avg_pnl']:.2f}\n"
            f"🏆 Best trade:     +${s['best_trade']:.2f}\n"
            f"💔 Worst trade:    ${s['worst_trade']:.2f}\n"
            f"•••\n"
            f"🏦 All-time Net:   {sign_a}${sa['net_pnl']:.2f}\n"
            f"📊 All-time Trades:{sa['total_trades']}\n"
            f"🌐 Mode: {self.env}\n"
        )
        await self._send(msg)

    # ── Grid State Alerts ──────────────────────────────────────────

    async def alert_grid_activated(self, price, bb_lower, bb_upper, rsi, spacing):
        await self._send(
            f"🟢 <b>Grid ACTIVATED — SOL/USDT</b>\n"
            f"•••\n"
            f"💵 Price:    ${price:,.2f}\n"
            f"📐 Range:    ${bb_lower:,.0f} → ${bb_upper:,.0f}\n"
            f"📏 Spacing:  ${spacing:,.0f} per level\n"
            f"📊 RSI:      {rsi:.1f}"
        )

    async def alert_grid_paused(self, price, reason, rsi):
        await self._send(
            f"⏸️ <b>Grid PAUSED — SOL/USDT</b>\n"
            f"•••\n"
            f"💵 Price:   ${price:,.2f}\n"
            f"⚠️ Reason:  {reason}\n"
            f"📊 RSI:     {rsi:.1f}\n"
            f"💤 Holding USDT until re-entry signal."
        )

    async def alert_circuit_breaker(self, drawdown_pct, equity):
        await self._send(
            f"🚨 <b>CIRCUIT BREAKER TRIGGERED</b>\n"
            f"•••\n"
            f"📉 Drawdown:  -{drawdown_pct:.1f}%\n"
            f"🏦 Equity:    ${equity:,.2f}\n"
            f"🛑 Bot halted. All orders cancelled.\n"
            f"Manual review required before restarting."
        )
