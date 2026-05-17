import os
import time
import json
import logging
import logging.handlers
import threading
import http.client
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src.monitoring.system_monitor import get_stats

logger = logging.getLogger(__name__)

# Direct file logging — bypasses Python logging entirely (Hummingbot overrides it)
_log_dir = Path(os.environ.get("LOG_DIR", "logs"))
_log_dir.mkdir(parents=True, exist_ok=True)
_tg_log_path = _log_dir / "telegram.log"


def _log(msg: str):
    with open(_tg_log_path, "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


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
        self._last_update_id = 0
        self._initialized = False
        self._init_retries = 0

    _started = False
    _start_lock = threading.Lock()

    def start(self):
        if not self._token or not self._chat_id:
            logger.warning(
                f"Telegram commands disabled — token={'SET' if self._token else 'MISSING'}, "
                f"chat_id={'SET' if self._chat_id else 'MISSING'}"
            )
            return

        with self._start_lock:
            if TelegramCommandHandler._started:
                logger.info("Telegram commands already running — skipping duplicate start")
                return
            TelegramCommandHandler._started = True

        logger.info(f"Telegram commands initializing with chat_id={self._chat_id}")

    # ── HTTP helpers (plain http.client, no asyncio) ────────────────

    def _tg_get(self, path, params=None, timeout=10):
        """HTTP GET to Telegram API via http.client."""
        try:
            query = f"?{urllib.parse.urlencode(params)}" if params else ""
            conn = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
            conn.request("GET", f"/bot{self._token}/{path}{query}")
            resp = conn.getresponse()
            data = json.loads(resp.read())
            conn.close()
            return data
        except Exception as e:
            logger.warning(f"Telegram GET failed: {e}")
            return {}

    def _tg_post(self, path, data, timeout=10):
        """HTTP POST to Telegram API via http.client."""
        try:
            body = urllib.parse.urlencode(data)
            conn = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
            conn.request("POST", f"/bot{self._token}/{path}", body=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp = conn.getresponse()
            result = json.loads(resp.read())
            conn.close()
            return result
        except Exception as e:
            logger.warning(f"Telegram POST failed: {e}")

    # ── Poll-on-tick (called from strategy's on_tick) ────────────────

    def poll_once(self):
        """Non-blocking poll for Telegram updates. Call from on_tick each tick."""
        if not self._token or not self._chat_id:
            return

        # One-time init: clear webhook and send startup ping
        if not self._initialized:
            self._init_retries += 1
            if self._init_retries < 5:
                return  # Wait a few ticks for the event loop to stabilize
            _log(f"[INFO] Telegram poll_once: initializing (retries={self._init_retries})")
            try:
                resp = self._tg_get("deleteWebhook", params={"drop_pending_updates": "true"}, timeout=10)
                _log(f"[INFO] Telegram webhook cleared: {resp}")

                ping_text = (
                    "📡 <b>Telegram Command Handler Online</b>\n"
                    "Commands: /status /pnl /balance /capital /price /trades /pending /fees /system /clear /help\n"
                    "Trend: /trend_status /trend_capital /trend_pnl /trend_close /trend_history"
                )
                self._tg_post("sendMessage", data={
                    "chat_id": self._chat_id,
                    "text": ping_text,
                    "parse_mode": "HTML",
                })
                _log("[INFO] Telegram startup ping sent")
                self._initialized = True
            except Exception as e:
                _log(f"[WARN] Telegram init failed: {e}")
                self._init_retries = 3  # Retry after a few more ticks
            return

        # Non-blocking getUpdates (timeout=0 returns immediately)
        try:
            data = self._tg_get("getUpdates", params={
                "offset": str(self._last_update_id + 1),
                "timeout": "0",
                "allowed_updates": '["message"]',
            }, timeout=10)

            if not data.get("ok"):
                _log(f"[WARN] getUpdates returned: {data}")

            for update in data.get("result", []):
                self._last_update_id = update["update_id"]
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")

                _log(f"[DEBUG] update: chat_id={chat_id} text={text[:50]}")

                if chat_id != self._chat_id or not text.startswith("/"):
                    continue

                cmd = text.split("@")[0][1:].split()[0].lower()
                _log(f"[INFO] command: /{cmd}")
                handler = self._commands.get(cmd)
                if not handler:
                    continue

                self._dispatch(handler, chat_id, msg)

        except Exception as e:
            _log(f"[ERROR] Telegram poll error: {e}")

    @property
    def _commands(self):
        return {
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
            "trend_status": self._cmd_trend_status,
            "trend_capital": self._cmd_trend_capital,
            "trend_pnl": self._cmd_trend_pnl,
            "trend_close": self._cmd_trend_close,
            "trend_history": self._cmd_trend_history,
        }

    def _dispatch(self, handler, chat_id: str, msg: dict):
        """Call a command handler and send the reply."""
        class _MockMessage:
            def __init__(self, msg_dict, cid):
                self.text = msg_dict.get("text", "")
                self._chat_id = cid
                self._reply = None
                self._parse_mode = ""
            def reply_text(self, text, **kw):
                self._reply = text
                self._parse_mode = kw.get("parse_mode", "")

        class _MockUpdate:
            def __init__(self, msg_dict, cid):
                self.message = _MockMessage(msg_dict, cid)
                self.effective_chat = type("C", (), {"id": int(cid)})()

        try:
            mock_update = _MockUpdate(msg, chat_id)
            handler(mock_update, None)
            reply = mock_update.message._reply
            if reply:
                _log(f"[DEBUG] sending reply ({len(reply)} chars)")
                result = self._tg_post("sendMessage", data={
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": mock_update.message._parse_mode,
                })
                _log(f"[DEBUG] sendMessage result: {result}")
            else:
                _log("[WARN] handler produced no reply")
        except Exception as e:
            _log(f"[ERROR] Telegram command handler error: {e}")
            try:
                self._tg_post("sendMessage", data={
                    "chat_id": chat_id,
                    "text": f"⚠️ Error: {e}",
                })
            except Exception:
                pass

    def _fmt_pnl(self, val):
        if val is None:
            return "$0.00"
        sign = "+" if val >= 0 else ""
        return f"{sign}${val:.2f}"

    def _require_grid(self, update):
        if self.journal is not None and self.state_machine is not None:
            return True
        update.message.reply_text(
            "Grid commands not available in trend-only mode.\n"
            "Use: /trend_status /trend_capital /trend_pnl /trend_close /trend_history"
        )
        return False

    def _cmd_status(self, update, context):
        try:
            if not self._require_grid(update):
                return
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
                f"•••\n"
                f"Grid: <b>{state}</b>\n"
                f"Mode: {mode}\n"
                f"Circuit Breaker: {cb_status}\n"
                f"⏱ Uptime: {hours}h {minutes}m {secs}s\n"
                f"•••\n"
                f"📐 Grid: {levels} buy + {levels} sell levels\n"
                f"📏 Spacing: ${spacing_buy:.0f} (buy) / ${spacing_sell:.0f} (sell)\n"
                f"📋 Pending orders: {pending}\n"
                f"•••\n"
                f"💰 Base capital: ${base_capital:,.0f}\n"
                f"📈 Compound: ${compound:,.2f} ({growth_pct:+.1f}%)",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /status: {e}")
            update.message.reply_text(f"⚠️ Error getting status: {e}")

    def _cmd_pnl(self, update, context):
        try:
            if not self._require_grid(update):
                return
            logger.info("Telegram /pnl received")
            today = self.journal.summary_today()
            week = self.journal.summary_this_week()
            month = self.journal.summary_this_month()
            alltime = self.journal.summary_all_time()

            logger.info(f"Telegram /pnl response: today={today['net_pnl']}, all={alltime['net_pnl']}")
            update.message.reply_text(
                f"💰 <b>P&L Report</b>\n"
                f"•••\n"
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
            base_bal = strategy._get_base_balance()
            base_value = base_bal * price if price else 0
            equity = usdt + base_value
            base_asset = getattr(strategy, 'base_asset', 'SOL')

            mode = strategy.env.upper()
            base_cap = getattr(strategy, '_base_capital', strategy.capital_usdt)
            compound = strategy.grid_manager.capital_usdt
            growth_pct = ((compound - base_cap) / base_cap * 100) if base_cap > 0 else 0

            update.message.reply_text(
                f"💰 <b>Account Balance</b>\n"
                f"•••\n"
                f"💵 USDT: ${usdt:,.2f}\n"
                f"◎ {base_asset}:  {base_bal:.4f} (${base_value:,.2f})\n"
                f"•••\n"
                f"📊 Equity: ${equity:,.2f}\n"
                f"Mode: {mode}\n"
                f"•••\n"
                f"📐 Grid capital: ${compound:,.2f} ({growth_pct:+.1f}%)\n"
                f"📏 Base capital: ${base_cap:,.0f}\n"
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
                f"•••\n"
                f"Before: ${old_capital:,.0f}\n"
                f"Now:    ${new_capital:,.0f}\n"
                f"•••\n"
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
            if not self._require_grid(update):
                return
            logger.info("Telegram /trades received")
            trades = self.journal.get_trades()
            if not trades:
                update.message.reply_text("No trades recorded yet.")
                return

            lines = ["📜 <b>Last 5 Trades</b>", "•••"]
            for t in trades[:5]:
                pnl = t.get("net_pnl", 0)
                sign = "+" if pnl >= 0 else ""
                emoji = "✅" if pnl >= 0 else "❌"
                ts = t.get("timestamp", "")
                price = t.get("exit_price", 0)
                side = t.get("side", "?")
                lines.append(f"{emoji} {side} @ ${price:,.2f}  {sign}${pnl:.2f}  {ts[:16]}")

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

            lines = [f"📋 <b>Pending Orders ({len(pending)})</b>", "•••"]

            if buys:
                buys.sort(key=lambda o: o.price, reverse=True)
                lines.append(f"📈 <b>BUY ({len(buys)})</b>")
                for o in buys:
                    val = o.price * o.quantity
                    lines.append(f"  L{o.level}: ${o.price:,.2f} × {o.quantity:.4f} (${val:.2f})")

            if sells:
                sells.sort(key=lambda o: o.price)
                lines.append(f"📉 <b>SELL ({len(sells)})</b>")
                for o in sells:
                    val = o.price * o.quantity
                    lines.append(f"  L{o.level}: ${o.price:,.2f} × {o.quantity:.4f} (${val:.2f})")

            lines.append("•••")
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
                f"📜 <b>Last 30 log lines ({today})</b>\n•••\n<pre>{tail[:3900]}</pre>",
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
                f"🚨 <b>Recent Errors</b>\n•••\n<pre>{tail[:3900]}</pre>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /errors: {e}")
            update.message.reply_text(f"⚠️ Error reading crash log: {e}")

    def _cmd_fees(self, update, context):
        try:
            if not self._require_grid(update):
                return
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
                f"•••\n"
                f"📅 Today:   ${today['total_fees']:.2f} "
                f"(ratio: {fmt_ratio(today['fee_to_gross_ratio'])}, "
                f"{today['trade_count']} trades)\n"
                f"📆 Week:    ${week['total_fees']:.2f} "
                f"(ratio: {fmt_ratio(week['fee_to_gross_ratio'])})\n"
                f"🗓 Month:   ${month['total_fees']:.2f} "
                f"(ratio: {fmt_ratio(month['fee_to_gross_ratio'])})\n"
                f"•••\n"
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
            display_pair = getattr(self.strategy, 'display_pair', 'SOL/USDT')

            indicators = self.strategy.get_indicators_snapshot()
            live_price = indicators[4] if indicators else None

            if live_price is None:
                update.message.reply_text("⚠️ Could not fetch live price.")
                return

            if not indicators or not indicators[0]:
                update.message.reply_text(f"💲 <b>{display_pair}</b>: ${live_price:,.2f}", parse_mode="HTML")
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
                f"◎ <b>{display_pair}</b>\n"
                f"•••\n"
                f"💲 <b>${live_price:,.2f}</b>\n"
                f"•••\n"
                f"📊 RSI (14): {rsi:.1f}  [{rsi_zone}]\n"
                f"{ema_emoji} EMA 200: ${ema:,.2f}  ({pct_from_ema:+.1f}%)\n"
                f"📏 ATR (14): ${atr:,.2f}\n"
                f"•••\n"
                f"📈 BB Upper: ${bb_upper:,.2f}\n"
                f"📊 BB Mid:   ${bb_mid:,.2f}\n"
                f"📉 BB Lower: ${bb_lower:,.2f}",
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

            state_file = data_dir / "grid_state.json"
            if state_file.exists():
                state_file.unlink()
                state_file.write_text("{}")
                cleared.append("grid_state.json")

            script_name = getattr(self.strategy, 'script_file_name', 'ta_grid_btcusdt.py').replace('.py', '')
            strat_log = log_dir / f"logs_{script_name}.log"
            if strat_log.exists():
                strat_log.unlink()
                strat_log.touch()
                cleared.append(strat_log.name)

            self.event_log.log("logs_cleared", source="telegram", files=cleared)
            logger.info(f"Telegram /clear — cleared: {cleared}")

            files_list = "\n".join(f"  🗑 {f}" for f in cleared) if cleared else "  (nothing to clear)"
            update.message.reply_text(
                f"🧹 <b>Logs & Data Cleared</b>\n"
                f"•••\n"
                f"{files_list}\n"
                f"•••\n"
                f"Bot continues running — fresh start.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /clear: {e}")
            update.message.reply_text(f"⚠️ Error clearing logs: {e}")

    # ── Trend Commands ────────────────────────────────────────────

    def _cmd_trend_status(self, update, context):
        try:
            logger.info("Telegram /trend_status received")
            strategy = self.strategy
            if not hasattr(strategy, '_trend_manager') or strategy._trend_manager is None:
                update.message.reply_text("Trend engine not active")
                return

            tm = strategy._trend_manager
            pm = strategy._position_manager
            lines = ["TREND ENGINE", chr(9473) * 33]
            positions = pm.get_all_positions()
            lines.append(f"Open positions: {len(positions)}/{pm._max_positions}")

            for pos in positions:
                current = getattr(strategy, '_last_price', pos.entry_price)
                pnl_pct = (current - pos.entry_price) / pos.entry_price * 100 if current and pos.entry_price else 0
                lines.append(f"  {pos.amount:.2f} SOL @ ${pos.entry_price:.2f} | SL ${pos.stop_loss:.2f} TP ${pos.take_profit:.2f}")
                lines.append(f"  P&L: {pnl_pct:+.1f}% | Trail: ${pos.trailing_stop:.2f}")

            lines.append(f"Capital: ${pm._capital:.2f}")

            if hasattr(strategy, '_last_trend_score') and strategy._last_trend_score:
                score = strategy._last_trend_score
                lines.append(f"Signal score: {score.total}/7")
                for d in score.details:
                    lines.append(f"  +{d['points']} {d['signal']}: {d['note']}")

            update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in /trend_status: {e}")
            update.message.reply_text(f"⚠️ Error getting trend status: {e}")

    def _cmd_trend_capital(self, update, context):
        try:
            logger.info("Telegram /trend_capital received")
            strategy = self.strategy
            if not hasattr(strategy, '_position_manager') or strategy._position_manager is None:
                update.message.reply_text("Trend engine not active")
                return

            text = update.message.text.strip()
            parts = text.split()

            if len(parts) < 2:
                update.message.reply_text(
                    f"Current trend capital: ${strategy._position_manager._capital:.2f}\n"
                    f"Usage: /trend_capital &lt;amount&gt;"
                )
                return

            try:
                amount = float(parts[1].replace(",", ""))
            except ValueError:
                update.message.reply_text("Invalid amount. Usage: /trend_capital 2000")
                return

            if amount < 0:
                update.message.reply_text("Capital must be >= 0")
                return

            old = strategy._position_manager._capital
            strategy._position_manager._capital = amount
            self.event_log.log("trend_capital_updated", old=old, new=amount)
            logger.info(f"Telegram /trend_capital: ${old:.2f} → ${amount:.2f}")

            update.message.reply_text(
                f"✅ Trend capital updated\n"
                f"Before: ${old:.2f}\n"
                f"Now: ${amount:.2f}"
            )
        except Exception as e:
            logger.error(f"Error in /trend_capital: {e}")
            update.message.reply_text(f"⚠️ Error updating trend capital: {e}")

    def _cmd_trend_pnl(self, update, context):
        try:
            logger.info("Telegram /trend_pnl received")
            strategy = self.strategy
            if not hasattr(strategy, '_trend_journal') or strategy._trend_journal is None:
                update.message.reply_text("Trend engine not active")
                return

            journal = strategy._trend_journal
            summary = journal.summary()
            perf = journal.performance()

            lines = ["TREND P&L", chr(9473) * 33]
            lines.append(f"Total trades: {summary['total_trades']}")
            lines.append(f"Win rate: {summary['win_rate']:.1f}% ({summary['wins']}W / {summary['losses']}L)")
            lines.append(f"Total P&L: ${summary['total_pnl']:.2f}")
            lines.append(f"Profit factor: {perf['profit_factor']:.2f}")
            lines.append(f"Avg win: ${perf['avg_win']:.2f} | Avg loss: ${perf['avg_loss']:.2f}")
            lines.append(f"Avg duration: {perf['avg_duration']:.0f} min")

            update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in /trend_pnl: {e}")
            update.message.reply_text(f"⚠️ Error getting trend P&L: {e}")

    def _cmd_trend_close(self, update, context):
        try:
            logger.info("Telegram /trend_close received")
            strategy = self.strategy
            if not hasattr(strategy, '_position_manager') or strategy._position_manager is None:
                update.message.reply_text("Trend engine not active")
                return

            pm = strategy._position_manager
            positions = pm.get_all_positions()

            if not positions:
                update.message.reply_text("No open trend positions")
                return

            strategy._trend_force_close = True
            logger.info(f"Telegram /trend_close — closing {len(positions)} position(s)")
            update.message.reply_text(f"Closing {len(positions)} trend position(s) on next tick...")
        except Exception as e:
            logger.error(f"Error in /trend_close: {e}")
            update.message.reply_text(f"⚠️ Error closing trend positions: {e}")

    def _cmd_trend_history(self, update, context):
        try:
            logger.info("Telegram /trend_history received")
            strategy = self.strategy
            if not hasattr(strategy, '_trend_journal') or strategy._trend_journal is None:
                update.message.reply_text("Trend engine not active")
                return

            trades = strategy._trend_journal.recent_trades(limit=10)

            if not trades:
                update.message.reply_text("No trend trades yet")
                return

            lines = ["TREND HISTORY", chr(9473) * 33]
            for t in trades:
                emoji = "+" if t["pnl"] >= 0 else "-"
                lines.append(f"{emoji} {t['side']} {t['amount']:.1f}@${t['entry_price']:.2f}->${t['exit_price']:.2f} | ${t['pnl']:+.2f} ({t['exit_reason']})")

            update.message.reply_text("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in /trend_history: {e}")
            update.message.reply_text(f"⚠️ Error getting trend history: {e}")

    def _cmd_help(self, update, context):
        logger.info("Telegram /help received")
        base_asset = getattr(self.strategy, 'base_asset', 'SOL')
        display_pair = getattr(self.strategy, 'display_pair', 'SOL/USDT')
        update.message.reply_text(
            "📖 <b>Available Commands</b>\n"
            "•••\n"
            "/status — Grid state, mode, uptime, pending orders\n"
            "/pnl — Today / week / month / all-time P&L\n"
            f"/balance — USDT, {base_asset}, equity, and grid capital\n"
            "/capital &lt;amount&gt; — Update grid capital (no redeploy)\n"
            f"/price — Current {display_pair} price with indicators\n"
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
            "/trend_status — Trend engine status and positions\n"
            "/trend_capital &lt;amount&gt; — Update trend trading capital\n"
            "/trend_pnl — Trend strategy P&L report\n"
            "/trend_close — Force close all trend positions\n"
            "/trend_history — Recent trend trade history\n"
            "/help — This message",
            parse_mode="HTML"
        )
