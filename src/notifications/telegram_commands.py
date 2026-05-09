import os
import time
import json
import logging
import threading
import subprocess
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src.monitoring.system_monitor import get_stats

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

        logger.info(f"Telegram commands initializing with chat_id={self._chat_id}")

        try:
            # Start raw polling in background thread
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
        """Runs the telegram polling loop directly (synchronous)."""
        try:
            logger.info("Telegram _run: starting synchronous polling...")
            self._poll_forever()
        except Exception as e:
            logger.error(f"Telegram polling thread crashed: {e}", exc_info=True)

    def _tg_get(self, path, params=None, timeout=35):
        """HTTP GET to Telegram API via subprocess to avoid thread deadlock."""
        url = f"https://api.telegram.org/bot{self._token}/{path}"
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}"
        script = (
            "import urllib.request, json, sys\n"
            f"r = urllib.request.urlopen({url!r}, timeout={timeout})\n"
            "sys.stdout.write(r.read().decode())\n"
        )
        result = subprocess.run(
            ["python", "-c", script],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        if result.returncode != 0 and result.stderr:
            logger.warning(f"Telegram GET failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout else {}

    def _tg_post(self, path, data, timeout=10):
        """HTTP POST to Telegram API via subprocess to avoid thread deadlock."""
        url = f"https://api.telegram.org/bot{self._token}/{path}"
        encoded = urllib.parse.urlencode(data)
        script = (
            "import urllib.request\n"
            f"req = urllib.request.Request({url!r}, data={encoded!r}.encode())\n"
            f"urllib.request.urlopen(req, timeout={timeout})\n"
        )
        result = subprocess.run(
            ["python", "-c", script],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        if result.returncode != 0 and result.stderr:
            logger.warning(f"Telegram POST failed: {result.stderr.strip()}")

    def _poll_forever(self):
        """Synchronous getUpdates-based polling loop using subprocess curl."""
        last_update_id = 0
        logger.info("Telegram _poll_forever: clearing webhook...")

        # Clear webhook
        try:
            resp = self._tg_get("deleteWebhook", {"drop_pending_updates": "true"}, timeout=10)
            logger.info(f"Telegram webhook cleared: {resp}")
        except Exception as e:
            logger.warning(f"Telegram deleteWebhook failed: {e}")

        # Send startup ping
        try:
            ping_text = (
                "📡 <b>Telegram Command Handler Online</b>\n"
                "Commands: /status /pnl /balance /capital /price /trades /pending /fees /system /clear /help"
            )
            self._tg_post("sendMessage", {
                "chat_id": self._chat_id,
                "text": ping_text,
                "parse_mode": "HTML",
            })
            logger.info("Telegram startup ping sent")
        except Exception as e:
            logger.warning(f"Telegram startup ping failed: {e}")

        # Build command dispatch table
        commands = {
            "status": self._cmd_status,
            "pnl": self._cmd_pnl,
            "balance": self._cmd_balance,
            "capital": self._cmd_capital,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "reset": self._cmd_reset,
            "trades": self._cmd_trades,
            "pending": self._cmd_pending,
            "logs": self._cmd_logs,
            "errors": self._cmd_errors,
            "fees": self._cmd_fees,
            "price": self._cmd_price,
            "system": self._cmd_server,
            "server": self._cmd_server,
            "clear": self._cmd_clear,
            "help": self._cmd_help,
        }

        logger.info("Telegram command handler online — raw polling active")

        # Poll loop
        while True:
            try:
                data = self._tg_get("getUpdates", {
                    "offset": str(last_update_id + 1),
                    "timeout": "30",
                    "allowed_updates": '["message"]',
                })

                for update in data.get("result", []):
                    last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")

                    if chat_id != self._chat_id or not text.startswith("/"):
                        continue

                    cmd = text.split("@")[0][1:].split()[0].lower()
                    handler = commands.get(cmd)
                    if not handler:
                        continue

                    self._dispatch(handler, chat_id, msg)

            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                time.sleep(5)

    def _send_message(self, chat_id: str, text: str, parse_mode: str = ""):
        """Send a message to Telegram chat."""
        self._tg_post("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        })

    def _dispatch(self, handler, chat_id: str, msg: dict):
        """Call a command handler and send the reply text back."""
        handler_self = self

        class _MockMessage:
            def __init__(self, msg_dict, cid):
                self.text = msg_dict.get("text", "")
                self._chat_id = cid
            def reply_text(self, text, **kw):
                handler_self._tg_post("sendMessage", {
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": kw.get("parse_mode", ""),
                })

        class _MockUpdate:
            def __init__(self, msg_dict, cid):
                self.message = _MockMessage(msg_dict, cid)
                self.effective_chat = type("C", (), {"id": int(cid)})()

        try:
            mock_update = _MockUpdate(msg, chat_id)
            handler(mock_update, None)
        except Exception as e:
            logger.error(f"Telegram command handler error: {e}", exc_info=True)
            try:
                self._send_message(chat_id, f"⚠️ Error: {e}")
            except Exception:
                pass

    def _fmt_pnl(self, val):
        if val is None:
            return "$0.00"
        sign = "+" if val >= 0 else ""
        return f"{sign}${val:.2f}"

    def _cmd_status(self, update, context):
        try:
            logger.info("Telegram /status received")
            uptime_s = int(time.time() - self._started_at)
            hours, remainder = divmod(uptime_s, 3600)
            minutes, secs = divmod(remainder, 60)

            state = self.state_machine.state.value
            mode = self.strategy.env.upper()
            cb_status = "🛑 HALTED" if self.circuit_breaker.halted else "✅ OK"
            pending = self.strategy.order_tracker.total_pending
            levels = self.strategy.levels
            spacing_buy = getattr(self.strategy, '_active_buy_spacing', 0)
            spacing_sell = getattr(self.strategy, '_active_sell_spacing', 0)
            base_capital = getattr(self.strategy, '_base_capital', self.strategy.capital_usdt)
            compound = self.strategy.grid_manager.capital_usdt
            growth_pct = ((compound - base_capital) / base_capital * 100) if base_capital > 0 else 0

            logger.info(f"Telegram /status response: state={state}, mode={mode}, cb={cb_status}, pending={pending}")
            update.message.reply_text(
                f"📊 <b>Bot Status</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grid: <b>{state}</b>\n"
                f"Mode: {mode}\n"
                f"Circuit Breaker: {cb_status}\n"
                f"⏱ Uptime: {hours}h {minutes}m {secs}s\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📐 Grid: {levels} buy + {levels} sell levels\n"
                f"📏 Spacing: ${spacing_buy:.0f} (buy) / ${spacing_sell:.0f} (sell)\n"
                f"📋 Pending orders: {pending}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Base capital: ${base_capital:,.0f}\n"
                f"📈 Compound: ${compound:,.2f} ({growth_pct:+.1f}%)",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /status: {e}")
            update.message.reply_text(f"⚠️ Error getting status: {e}")

    def _cmd_pnl(self, update, context):
        try:
            logger.info("Telegram /pnl received")
            today = self.journal.summary_today()
            week = self.journal.summary_this_week()
            month = self.journal.summary_this_month()
            alltime = self.journal.summary_all_time()

            logger.info(f"Telegram /pnl response: today={today['net_pnl']}, all={alltime['net_pnl']}")
            update.message.reply_text(
                f"💰 <b>P&L Report</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Today: {self._fmt_pnl(today['net_pnl'])}  ({today['total_trades']} trades, {today['win_rate']}%)\n"
                f"📆 Week:  {self._fmt_pnl(week['net_pnl'])}\n"
                f"🗓 Month: {self._fmt_pnl(month['net_pnl'])}\n"
                f"🏦 All:   {self._fmt_pnl(alltime['net_pnl'])}  ({alltime['total_trades']} trades)",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /pnl: {e}")
            update.message.reply_text(f"⚠️ Error getting P&L: {e}")

    def _cmd_balance(self, update, context):
        try:
            logger.info("Telegram /balance received")
            strategy = self.strategy
            indicators = strategy.get_indicators_snapshot()
            price = indicators[4] if indicators else 0

            usdt = strategy._get_usdt_balance()
            btc = strategy._get_btc_balance()
            btc_value = btc * price if price else 0
            equity = usdt + btc_value

            mode = strategy.env.upper()
            base = getattr(strategy, '_base_capital', strategy.capital_usdt)
            compound = strategy.grid_manager.capital_usdt
            growth_pct = ((compound - base) / base * 100) if base > 0 else 0

            update.message.reply_text(
                f"💰 <b>Account Balance</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 USDT: ${usdt:,.2f}\n"
                f"₿ BTC:  {btc:.8f} (${btc_value:,.2f})\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Equity: ${equity:,.2f}\n"
                f"Mode: {mode}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📐 Grid capital: ${compound:,.2f} ({growth_pct:+.1f}%)\n"
                f"📏 Base capital: ${base:,.0f}\n"
                f"💡 Change: /capital &lt;amount&gt;",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /balance: {e}")
            update.message.reply_text(f"⚠️ Error getting balance: {e}")

    def _cmd_capital(self, update, context):
        try:
            text = update.message.text.strip()
            parts = text.split()

            if len(parts) < 2:
                base = getattr(self.strategy, '_base_capital', self.strategy.capital_usdt)
                update.message.reply_text(
                    f"📐 Current base capital: ${base:,.0f}\n"
                    f"Usage: /capital &lt;amount&gt;\n"
                    f"Example: /capital 2000",
                    parse_mode="HTML"
                )
                return

            try:
                new_capital = float(parts[1].replace(",", ""))
            except ValueError:
                update.message.reply_text("⚠️ Invalid amount. Example: /capital 2000")
                return

            if new_capital < 100:
                update.message.reply_text("⚠️ Minimum capital is $100.")
                return

            old_capital = getattr(self.strategy, '_base_capital', self.strategy.capital_usdt)
            self.strategy._base_capital = new_capital
            self.strategy.capital_usdt = new_capital
            self.strategy.grid_manager.capital_usdt = new_capital
            self.strategy.position_guard.total_capital = new_capital
            self.strategy._grid_dirty = True

            self.event_log.log("capital_updated",
                old_capital=old_capital,
                new_capital=new_capital,
                source="telegram",
            )
            logger.info(f"Telegram /capital: ${old_capital:,.0f} → ${new_capital:,.0f}")

            update.message.reply_text(
                f"✅ <b>Capital Updated</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Before: ${old_capital:,.0f}\n"
                f"Now:    ${new_capital:,.0f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grid will recalculate on next refresh.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /capital: {e}")
            update.message.reply_text(f"⚠️ Error updating capital: {e}")

    def _cmd_pause(self, update, context):
        try:
            self.strategy.manual_pause = True
            self.event_log.log("manual_pause", source="telegram")
            logger.info("Telegram /pause — grid paused")
            update.message.reply_text(
                "⏸️ Grid manually paused. Use /resume to restart."
            )
        except Exception as e:
            logger.error(f"Error in /pause: {e}")
            update.message.reply_text(f"⚠️ Error pausing: {e}")

    def _cmd_resume(self, update, context):
        try:
            self.strategy.manual_pause = False
            self.event_log.log("manual_resume", source="telegram")
            logger.info("Telegram /resume — grid resumed")
            update.message.reply_text(
                "🟢 Grid resumed. Will activate on next valid signal."
            )
        except Exception as e:
            logger.error(f"Error in /resume: {e}")
            update.message.reply_text(f"⚠️ Error resuming: {e}")

    def _cmd_reset(self, update, context):
        try:
            indicators = self.strategy.get_indicators_snapshot()
            equity = self.strategy._estimate_equity(
                indicators[4] if indicators else 0
            )
            self.circuit_breaker.reset(equity)
            self.event_log.log("circuit_breaker_reset", source="telegram", equity=round(equity, 2))
            logger.info(f"Telegram /reset — circuit breaker reset, equity=${equity:.2f}")
            update.message.reply_text(
                "🔄 Circuit breaker reset. Bot will resume on next tick."
            )
        except Exception as e:
            logger.error(f"Error in /reset: {e}")
            update.message.reply_text(f"⚠️ Error resetting: {e}")

    def _cmd_trades(self, update, context):
        try:
            logger.info("Telegram /trades received")
            trades = self.journal.get_trades()
            if not trades:
                update.message.reply_text("No trades recorded yet.")
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

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /trades: {e}")
            update.message.reply_text(f"⚠️ Error getting trades: {e}")

    def _cmd_pending(self, update, context):
        try:
            logger.info("Telegram /pending received")
            tracker = self.strategy.order_tracker
            pending = tracker.pending_orders()

            if not pending:
                update.message.reply_text("📋 No pending orders.")
                return

            buys = [o for o in pending if o.side.value == "BUY"]
            sells = [o for o in pending if o.side.value == "SELL"]

            lines = [f"📋 <b>Pending Orders ({len(pending)})</b>", "━━━━━━━━━━━━━━━━━━━━━━"]

            if buys:
                buys.sort(key=lambda o: o.price, reverse=True)
                lines.append(f"📈 <b>BUY ({len(buys)})</b>")
                for o in buys:
                    val = o.price * o.quantity
                    lines.append(f"  L{o.level}: ${o.price:,.2f} × {o.quantity:.8f} (${val:.2f})")

            if sells:
                sells.sort(key=lambda o: o.price)
                lines.append(f"📉 <b>SELL ({len(sells)})</b>")
                for o in sells:
                    val = o.price * o.quantity
                    lines.append(f"  L{o.level}: ${o.price:,.2f} × {o.quantity:.8f} (${val:.2f})")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            total_buy = sum(o.price * o.quantity for o in buys)
            total_sell = sum(o.price * o.quantity for o in sells)
            lines.append(f"💰 Buy value: ${total_buy:,.2f} | Sell value: ${total_sell:,.2f}")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /pending: {e}")
            update.message.reply_text(f"⚠️ Error getting pending orders: {e}")

    def _cmd_logs(self, update, context):
        try:
            logger.info("Telegram /logs received")
            log_dir = os.environ.get("LOG_DIR", "logs")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = Path(log_dir) / f"bot_{today}.log"
            if not log_file.exists():
                update.message.reply_text(f"No log file for today ({today}).")
                return
            lines = log_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            tail = "\n".join(lines[-30:])
            update.message.reply_text(
                f"📜 <b>Last 30 log lines ({today})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{tail[:3900]}</pre>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /logs: {e}")
            update.message.reply_text(f"⚠️ Error reading logs: {e}")

    def _cmd_errors(self, update, context):
        try:
            logger.info("Telegram /errors received")
            log_dir = os.environ.get("LOG_DIR", "logs")
            crash_file = Path(log_dir) / "crashes.log"
            if not crash_file.exists():
                update.message.reply_text("✅ No errors logged — all clean!")
                return
            lines = crash_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            if not lines or (len(lines) == 1 and not lines[0]):
                update.message.reply_text("✅ No errors logged — all clean!")
                return
            tail = "\n".join(lines[-40:])
            update.message.reply_text(
                f"🚨 <b>Recent Errors</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{tail[:3900]}</pre>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /errors: {e}")
            update.message.reply_text(f"⚠️ Error reading crash log: {e}")

    def _cmd_fees(self, update, context):
        try:
            logger.info("Telegram /fees received")
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

            update.message.reply_text(
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
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /fees: {e}")
            update.message.reply_text(f"⚠️ Error getting fee analysis: {e}")

    def _cmd_price(self, update, context):
        try:
            logger.info("Telegram /price received")

            # Fetch real-time price from Binance ticker
            script = (
                "from binance.client import Client\n"
                "import json, sys\n"
                "c = Client('', '')\n"
                "t = c.get_symbol_ticker(symbol='BTCUSDT')\n"
                "sys.stdout.write(t['price'])\n"
            )
            result = subprocess.run(
                ["python", "-c", script],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0 or not result.stdout:
                update.message.reply_text("⚠️ Could not fetch live price.")
                return
            live_price = float(result.stdout.strip())

            # Get cached indicators for context
            indicators = self.strategy.get_indicators_snapshot()

            if not indicators or not indicators[0]:
                update.message.reply_text(f"💲 <b>BTC/USDT</b>: ${live_price:,.2f}", parse_mode="HTML")
                return

            bb = indicators[0]
            rsi = indicators[1]
            ema = indicators[2]
            atr = indicators[3]

            bb_upper = bb.upper if bb else 0
            bb_lower = bb.lower if bb else 0
            bb_mid = bb.mid if bb else 0

            pct_from_ema = ((live_price - ema) / ema * 100) if ema else 0
            ema_emoji = "🟢" if pct_from_ema >= 0 else "🔴"
            rsi_zone = "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL"

            update.message.reply_text(
                f"₿ <b>BTC/USDT — LIVE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💲 <b>${live_price:,.2f}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 RSI (14): {rsi:.1f}  [{rsi_zone}]\n"
                f"{ema_emoji} EMA 200: ${ema:,.0f}  ({pct_from_ema:+.1f}%)\n"
                f"📏 ATR (14): ${atr:,.0f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 BB Upper: ${bb_upper:,.0f}\n"
                f"📊 BB Mid:   ${bb_mid:,.0f}\n"
                f"📉 BB Lower: ${bb_lower:,.0f}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /price: {e}")
            update.message.reply_text(f"⚠️ Error getting price: {e}")

    def _cmd_server(self, update, context):
        try:
            logger.info("Telegram /server received")
            stats = get_stats()
            logger.info(f"Telegram /server response: cpu={stats.cpu_percent:.0f}% ram={stats.ram_percent:.0f}% disk={stats.disk_percent:.0f}%")
            update.message.reply_text(
                stats.format_telegram(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /server: {e}")
            update.message.reply_text(f"⚠️ Error getting server status: {e}")

    def _cmd_clear(self, update, context):
        try:
            log_dir = Path(os.environ.get("LOG_DIR", "logs"))
            data_dir = Path("data")
            cleared = []

            # Clear log files — remove and recreate so FileHandlers reset
            for f in log_dir.glob("bot_*.log"):
                f.unlink(missing_ok=True)
                f.touch()
                cleared.append(f.name)
            for f in log_dir.glob("events_*.jsonl"):
                f.unlink(missing_ok=True)
                f.touch()
                cleared.append(f.name)
            crash_file = log_dir / "crashes.log"
            if crash_file.exists():
                crash_file.unlink()
                crash_file.touch()
                cleared.append("crashes.log")

            # Clear grid state only (preserve trades.json)
            state_file = data_dir / "grid_state.json"
            if state_file.exists():
                state_file.unlink()
                state_file.write_text("{}")
                cleared.append("grid_state.json")

            # Clear strategy-specific log
            strat_log = log_dir / "logs_ta_grid_btcusdt.log"
            if strat_log.exists():
                strat_log.unlink()
                strat_log.touch()
                cleared.append(strat_log.name)

            self.event_log.log("logs_cleared", source="telegram", files=cleared)
            logger.info(f"Telegram /clear — cleared: {cleared}")

            files_list = "\n".join(f"  🗑 {f}" for f in cleared) if cleared else "  (nothing to clear)"
            update.message.reply_text(
                f"🧹 <b>Logs & Data Cleared</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{files_list}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Bot continues running — fresh start.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /clear: {e}")
            update.message.reply_text(f"⚠️ Error clearing logs: {e}")

    def _cmd_help(self, update, context):
        logger.info("Telegram /help received")
        update.message.reply_text(
            "📖 <b>Available Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "/status — Grid state, mode, uptime, pending orders\n"
            "/pnl — Today / week / month / all-time P&L\n"
            "/balance — USDT, BTC, equity, and grid capital\n"
            "/capital <amount> — Update grid capital (no redeploy)\n"
            "/price — Current BTC/USDT price with indicators\n"
            "/pause — Manually pause grid (cancel all orders)\n"
            "/resume — Resume grid trading\n"
            "/reset — Reset circuit breaker after halt\n"
            "/trades — Last 5 closed trades\n"
            "/pending — Show all pending buy/sell orders\n"
            "/fees — Fee analysis and overtrading detection\n"
            "/system — CPU, RAM, Disk usage (alerts at 75%)\n"
            "/logs — Last 30 lines from today's bot log\n"
            "/errors — Recent errors and crashes\n"
            "/clear — Clear logs and grid state (keeps trade history)\n"
            "/help — This message",
            parse_mode="HTML"
        )
