import os
import time
import logging
import threading
import asyncio
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    def __init__(self, journal, state_machine, circuit_breaker, position_guard, event_logger, strategy):
        self.journal = journal
        self.state_machine = state_machine
        self.circuit_breaker = circuit_breaker
        self.position_guard = position_guard
        self.event_log = event_logger
        self.strategy = strategy
        self._token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._started_at = time.time()
        self._app = None
        self._thread = None
        self._loop = None

    def start(self):
        if not self._token or not self._chat_id:
            logger.warning(
                f"Telegram commands disabled — token={'SET' if self._token else 'MISSING'}, "
                f"chat_id={'SET' if self._chat_id else 'MISSING'}"
            )
            return

        logger.info(f"Telegram commands initializing with chat_id={self._chat_id}")

        try:
            # Build application
            self._app = Application.builder().token(self._token).build()

            # Add command handlers with strict chat filter
            chat_filter = filters.Chat(int(self._chat_id))
            self._app.add_handler(CommandHandler("status", self._cmd_status, filters=chat_filter))
            self._app.add_handler(CommandHandler("pnl", self._cmd_pnl, filters=chat_filter))
            self._app.add_handler(CommandHandler("pause", self._cmd_pause, filters=chat_filter))
            self._app.add_handler(CommandHandler("resume", self._cmd_resume, filters=chat_filter))
            self._app.add_handler(CommandHandler("reset", self._cmd_reset, filters=chat_filter))
            self._app.add_handler(CommandHandler("trades", self._cmd_trades, filters=chat_filter))
            self._app.add_handler(CommandHandler("logs", self._cmd_logs, filters=chat_filter))
            self._app.add_handler(CommandHandler("errors", self._cmd_errors, filters=chat_filter))
            self._app.add_handler(CommandHandler("fees", self._cmd_fees, filters=chat_filter))
            self._app.add_handler(CommandHandler("help", self._cmd_help, filters=chat_filter))

            # Add catch-all debug handler to identify chat ID mismatches
            # This logs ANY message that wasn't handled by the above (including from other chats)
            self._app.add_handler(MessageHandler(filters.ALL & ~chat_filter, self._cmd_debug))

            # Start in background thread
            self._thread = threading.Thread(target=self._run, daemon=True, name="TelegramBotThread")
            self._thread.start()
            logger.info("Telegram command handler thread launched")

        except Exception as e:
            logger.error(f"Failed to start Telegram commands: {e}", exc_info=True)

    def stop(self):
        if self._app:
            # Note: stopping a running polling from another thread is complex in PTB v20+
            # We rely on daemon thread for clean exit if needed
            logger.info("Telegram command handler stop requested")

    def _run(self):
        """Runs the telegram polling in a dedicated event loop."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            logger.info("Starting Telegram polling loop...")
            self._loop.run_until_complete(self._poll_forever())
        except Exception as e:
            logger.error(f"Telegram polling thread crashed: {e}", exc_info=True)

    async def _poll_forever(self):
        """Low-level polling that works inside Docker daemon threads.
        run_polling() uses signal handlers that fail in daemon threads,
        so we use the async API directly.
        """
        try:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)

            # Send startup ping
            ping_msg = (
                f"📡 <b>Telegram Command Handler Online</b>\n"
                f"Chat ID: <code>{self._chat_id}</code>\n"
                f"Commands: /status /pnl /trades /fees /help"
            )
            await self._app.bot.send_message(
                chat_id=self._chat_id, text=ping_msg, parse_mode=ParseMode.HTML
            )
            logger.info("Telegram command handler online — polling active")

            # Keep the loop alive
            while True:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Telegram _poll_forever crashed: {e}", exc_info=True)

    def _fmt_pnl(self, val):
        if val is None:
            return "$0.00"
        sign = "+" if val >= 0 else ""
        return f"{sign}${val:.2f}"

    async def _cmd_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Logs details of messages that failed the chat filter."""
        chat = update.effective_chat
        user = update.effective_user
        msg_text = update.message.text if update.message and update.message.text else "NON-TEXT"
        logger.warning(
            f"Unauthorized access attempt! "
            f"Chat ID: {chat.id} ({chat.type}), "
            f"User: {user.username if user else 'N/A'} ({user.id if user else 'N/A'}), "
            f"Message: {msg_text}"
        )
        # We don't reply to unauthorized users to avoid being a spam vector

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            uptime_s = int(time.time() - self._started_at)
            hours, remainder = divmod(uptime_s, 3600)
            minutes, secs = divmod(remainder, 60)

            state = self.state_machine.state.value
            mode = self.strategy.env.upper()
            cb_status = "🛑 HALTED" if self.circuit_breaker.halted else "✅ OK"
            pending = self.strategy._grid_order_tracker.total_pending

            await update.message.reply_text(
                f"📊 <b>Bot Status</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grid: <b>{state}</b>\n"
                f"Mode: {mode}\n"
                f"Circuit Breaker: {cb_status}\n"
                f"⏱ Uptime: {hours}h {minutes}m {secs}s\n"
                f"📋 Pending orders: {pending}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error in /status: {e}")
            await update.message.reply_text(f"⚠️ Error getting status: {e}")

    async def _cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            today = self.journal.summary_today()
            week = self.journal.summary_this_week()
            month = self.journal.summary_this_month()
            alltime = self.journal.summary_all_time()

            await update.message.reply_text(
                f"💰 <b>P&L Report</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Today: {self._fmt_pnl(today['net_pnl'])}  ({today['total_trades']} trades, {today['win_rate']}%)\n"
                f"📆 Week:  {self._fmt_pnl(week['net_pnl'])}\n"
                f"🗓 Month: {self._fmt_pnl(month['net_pnl'])}\n"
                f"🏦 All:   {self._fmt_pnl(alltime['net_pnl'])}  ({alltime['total_trades']} trades)",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error in /pnl: {e}")
            await update.message.reply_text(f"⚠️ Error getting P&L: {e}")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            self.strategy._manual_pause = True
            self.event_log.log("manual_pause", source="telegram")
            await update.message.reply_text(
                "⏸️ Grid manually paused. Use /resume to restart."
            )
        except Exception as e:
            logger.error(f"Error in /pause: {e}")
            await update.message.reply_text(f"⚠️ Error pausing: {e}")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            self.strategy._manual_pause = False
            self.event_log.log("manual_resume", source="telegram")
            await update.message.reply_text(
                "🟢 Grid resumed. Will activate on next valid signal."
            )
        except Exception as e:
            logger.error(f"Error in /resume: {e}")
            await update.message.reply_text(f"⚠️ Error resuming: {e}")

    async def _cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            self.circuit_breaker.halted = False
            equity = self.strategy._estimate_equity(
                self.strategy._cached_indicators[4] if self.strategy._cached_indicators else 0
            )
            self.circuit_breaker.set_peak_equity(max(equity, 0))
            self.circuit_breaker.set_start_of_day_equity(max(equity, 0))
            self.event_log.log("circuit_breaker_reset", source="telegram", equity=round(equity, 2))
            await update.message.reply_text(
                "🔄 Circuit breaker reset. Bot will resume on next tick."
            )
        except Exception as e:
            logger.error(f"Error in /reset: {e}")
            await update.message.reply_text(f"⚠️ Error resetting: {e}")

    async def _cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            trades = self.journal.get_trades()
            if not trades:
                await update.message.reply_text("No trades recorded yet.")
                return

            lines = ["📜 <b>Last 5 Trades</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
            for t in trades[:5]:
                pnl = t.get("net_pnl", 0)
                sign = "+" if pnl >= 0 else ""
                emoji = "✅" if pnl >= 0 else "❌"
                ts = t.get("timestamp", "")
                price = t.get("exit_price", 0)
                side = t.get("side", "?")
                lines.append(f"{emoji} {side} @ ${price:,.0f}  {sign}${pnl:.2f}  {ts[:16]}")

            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Error in /trades: {e}")
            await update.message.reply_text(f"⚠️ Error getting trades: {e}")

    async def _cmd_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            log_dir = os.environ.get("LOG_DIR", "logs")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = Path(log_dir) / f"bot_{today}.log"
            if not log_file.exists():
                await update.message.reply_text(f"No log file for today ({today}).")
                return
            lines = log_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            tail = "\n".join(lines[-30:])
            await update.message.reply_text(
                f"📜 <b>Last 30 log lines ({today})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{tail[:3900]}</pre>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error in /logs: {e}")
            await update.message.reply_text(f"⚠️ Error reading logs: {e}")

    async def _cmd_errors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            log_dir = os.environ.get("LOG_DIR", "logs")
            crash_file = Path(log_dir) / "crashes.log"
            if not crash_file.exists():
                await update.message.reply_text("✅ No errors logged — all clean!")
                return
            lines = crash_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            if not lines or (len(lines) == 1 and not lines[0]):
                await update.message.reply_text("✅ No errors logged — all clean!")
                return
            tail = "\n".join(lines[-40:])
            await update.message.reply_text(
                f"🚨 <b>Recent Errors</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{tail[:3900]}</pre>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error in /errors: {e}")
            await update.message.reply_text(f"⚠️ Error reading crash log: {e}")

    async def _cmd_fees(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            today = self.journal.fee_summary(
                datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
            )
            week = self.journal.fee_summary(
                (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            )
            month = self.journal.fee_summary(
                datetime.now(timezone.utc).strftime("%Y-%m-01 00:00:00")
            )
            ot = self.journal.is_overtrading()

            def fmt_ratio(r):
                return f"{r:.1%}" if r > 0 else "N/A"

            ot_emoji = "🚨" if ot["is_overtrading"] else "✅"
            ot_status = "YES - fees too high!" if ot["is_overtrading"] else "Normal"

            await update.message.reply_text(
                f"💸 <b>Fee Analysis</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Today:   ${today['total_fees']:.2f} "
                f"(ratio: {fmt_ratio(today['fee_to_gross_ratio'])}, "
                f"{today['trade_count']} trades)\n"
                f"📆 Week:    ${week['total_fees']:.2f} "
                f"(ratio: {fmt_ratio(week['fee_to_gross_ratio'])})\n"
                f"🗓 Month:   ${month['total_fees']:.2f} "
                f"(ratio: {fmt_ratio(month['fee_to_gross_ratio'])})\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{ot_emoji} Overtrading: {ot_status}\n"
                f"📊 Fee ratio: {fmt_ratio(ot['fee_to_gross_ratio'])} "
                f"(threshold: {ot['threshold']:.0%})",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error in /fees: {e}")
            await update.message.reply_text(f"⚠️ Error getting fee analysis: {e}")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📖 <b>Available Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "/status — Grid state, mode, uptime, pending orders\n"
            "/pnl — Today / week / month / all-time P&L\n"
            "/pause — Manually pause grid (cancel all orders)\n"
            "/resume — Resume grid trading\n"
            "/reset — Reset circuit breaker after halt\n"
            "/trades — Last 5 closed trades\n"
            "/fees — Fee analysis and overtrading detection\n"
            "/logs — Last 30 lines from today's bot log\n"
            "/errors — Recent errors and crashes\n"
            "/help — This message",
            parse_mode=ParseMode.HTML
        )
