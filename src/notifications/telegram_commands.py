"""
telegram_commands.py — Interactive Telegram bot commands.
Runs a python-telegram-bot Application in a background thread.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

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

    def start(self):
        if not self._token or not self._chat_id:
            logger.warning(
                f"Telegram commands disabled — token={'SET' if self._token else 'MISSING'}, "
                f"chat_id={'SET' if self._chat_id else 'MISSING'}"
            )
            return

        logger.info(f"Telegram commands starting with chat_id={self._chat_id}")

        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CommandHandler("status", self._cmd_status, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("pnl", self._cmd_pnl, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("reset", self._cmd_reset, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("trades", self._cmd_trades, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("logs", self._cmd_logs, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("errors", self._cmd_errors, filters=filters.Chat(int(self._chat_id))))
        self._app.add_handler(CommandHandler("help", self._cmd_help, filters=filters.Chat(int(self._chat_id))))

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Telegram command handler started")

    def stop(self):
        if self._app:
            self._app.stop_running()
            logger.info("Telegram command handler stopped")

    def _run(self):
        self._app.run_polling(drop_pending_updates=True, close_loop=False)

    def _fmt_pnl(self, val):
        if val is None:
            return "$0.00"
        sign = "+" if val >= 0 else ""
        return f"{sign}${val:.2f}"

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            uptime_s = int(time.time() - self._started_at)
            hours, remainder = divmod(uptime_s, 3600)
            minutes, secs = divmod(remainder, 60)

            state = self.state_machine.state.value
            mode = self.strategy.env.upper()
            cb_status = "🛑 HALTED" if self.circuit_breaker.halted else "✅ OK"
            pending = self.strategy.order_tracker.total_pending

            await update.message.reply_text(
                f"📊 <b>Bot Status</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grid: <b>{state}</b>\n"
                f"Mode: {mode}\n"
                f"Circuit Breaker: {cb_status}\n"
                f"⏱ Uptime: {hours}h {minutes}m {secs}s\n"
                f"📋 Pending orders: {pending}"
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
                f"🏦 All:   {self._fmt_pnl(alltime['net_pnl'])}  ({alltime['total_trades']} trades)"
            )
        except Exception as e:
            logger.error(f"Error in /pnl: {e}")
            await update.message.reply_text(f"⚠️ Error getting P&L: {e}")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            self.strategy.manual_pause = True
            self.event_log.log("manual_pause", source="telegram")
            await update.message.reply_text(
                "⏸️ Grid manually paused. Use /resume to restart."
            )
        except Exception as e:
            logger.error(f"Error in /pause: {e}")
            await update.message.reply_text(f"⚠️ Error pausing: {e}")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            self.strategy.manual_pause = False
            self.event_log.log("manual_resume", source="telegram")
            await update.message.reply_text(
                "🟢 Grid resumed. Will activate on next valid signal."
            )
        except Exception as e:
            logger.error(f"Error in /resume: {e}")
            await update.message.reply_text(f"⚠️ Error resuming: {e}")

    async def _cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            indicators = self.strategy.get_indicators_snapshot()
            equity = self.strategy._estimate_equity(
                indicators[4] if indicators else 0
            )
            self.circuit_breaker.reset(equity)
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

            await update.message.reply_text("\n".join(lines))
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
            await update.message.reply_text(f"📜 <b>Last 30 log lines ({today})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{tail[:3900]}</pre>")
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
            await update.message.reply_text(f"🚨 <b>Recent Errors</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{tail[:3900]}</pre>")
        except Exception as e:
            logger.error(f"Error in /errors: {e}")
            await update.message.reply_text(f"⚠️ Error reading crash log: {e}")

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
            "/logs — Last 30 lines from today's bot log\n"
            "/errors — Recent errors and crashes\n"
            "/help — This message"
        )
