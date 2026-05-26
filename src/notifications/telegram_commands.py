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
                    "•••\n"
                    "<b>System:</b> /status /system /price /logs /errors\n"
                    "<b>Grid:</b> /grid_status /pnl /balance /capital /trades /pending /fees /pause /resume /clear\n"
                    "<b>Trend:</b> /trend_status /trend_capital /trend_pnl /trend_close /trend_history\n"
                    "<b>Signal:</b> /signal_status /signal_pnl /signal_channels /signal_history /signal_pause /signal_resume /signal_close\n"
                    "••• /help for details"
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
            # System
            "status": self._cmd_status,
            "system": self._cmd_server,
            "server": self._cmd_server,
            "help": self._cmd_help,
            "logs": self._cmd_logs,
            "errors": self._cmd_errors,
            "price": self._cmd_price,
            # Grid
            "grid_status": self._cmd_grid_status,
            "pnl": self._cmd_pnl,
            "balance": self._cmd_balance,
            "capital": self._cmd_capital,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "reset": self._cmd_reset,
            "trades": self._cmd_trades,
            "pending": self._cmd_pending,
            "fees": self._cmd_fees,
            "clear": self._cmd_clear,
            # Trend
            "trend_status": self._cmd_trend_status,
            "trend_capital": self._cmd_trend_capital,
            "trend_pnl": self._cmd_trend_pnl,
            "trend_close": self._cmd_trend_close,
            "trend_history": self._cmd_trend_history,
            # Signal
            "signal_status": self._cmd_signal_status,
            "signal_pnl": self._cmd_signal_pnl,
            "signal_channels": self._cmd_signal_channels,
            "signal_history": self._cmd_signal_history,
            "signal_pause": self._cmd_signal_pause,
            "signal_resume": self._cmd_signal_resume,
            "signal_close": self._cmd_signal_close,
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
        """Daily summary for all engines — the main overview command."""
        try:
            logger.info("Telegram /status received")
            strategy = self.strategy
            uptime_s = int(time.time() - self._started_at)
            hours, remainder = divmod(uptime_s, 3600)
            minutes, secs = divmod(remainder, 60)
            mode = strategy.env.upper()

            lines = [
                f"📊 <b>Daily Status</b> — {mode}",
                f"•••",
                f"⏱ Up: {hours}h {minutes}m",
            ]

            # Grid P&L today
            try:
                grid_summary = self.journal.summary_today()
                grid_trades = grid_summary.get("total_trades", 0)
                grid_pnl = grid_summary.get("net_pnl", 0)
                grid_sign = "+" if grid_pnl >= 0 else ""
                lines.append(f"🤖 <b>Grid:</b> {grid_trades} trades | P&L: {grid_sign}${grid_pnl:.2f}")
            except Exception:
                lines.append(f"🤖 <b>Grid:</b> Active")

            # Trend P&L today
            try:
                trend_journal = getattr(strategy, '_trend_journal', None)
                if trend_journal:
                    ts = trend_journal.summary_today()
                    trend_trades = ts.get("total_trades", 0)
                    trend_pnl = ts.get("net_pnl", 0)
                    trend_sign = "+" if trend_pnl >= 0 else ""
                    lines.append(f"📈 <b>Trend:</b> {trend_trades} trades | P&L: {trend_sign}${trend_pnl:.2f}")
                else:
                    trend_cap = getattr(strategy, '_trend_capital', 0)
                    lines.append(f"📈 <b>Trend:</b> Capital ${trend_cap:,.0f}" if trend_cap else "📈 <b>Trend:</b> Disabled")
            except Exception:
                lines.append(f"📈 <b>Trend:</b> Active")

            # Signal P&L today
            sig = getattr(strategy, '_signal_engine', None)
            if sig:
                sig_mode = "AUDIT" if sig._audit_mode else "LIVE"
                sig_today = sig._journal.summary(days=0)
                sig_trades = sig_today.get("total_trades", 0)
                sig_pnl = sig_today.get("total_pnl", 0)
                sig_sign = "+" if sig_pnl >= 0 else ""
                sig_stats = sig.get_status()
                lines.append(f"📡 <b>Signal ({sig_mode}):</b> {sig_trades} trades | P&L: {sig_sign}${sig_pnl:.2f} | State: {sig_stats.get('state', 'N/A')}")
            else:
                lines.append(f"📡 <b>Signal:</b> Disabled")

            # ML regime
            if hasattr(strategy, '_ml_classifier') and strategy._ml_classifier:
                lines.append(f"🧠 <b>ML:</b> {strategy._ml_summary()}")

            # Server
            stats = get_stats()
            lines.append(f"•••")
            lines.append(f"💻 CPU: {stats.cpu_percent:.0f}% | RAM: {stats.ram_percent:.0f}% | Disk: {stats.disk_percent:.0f}%")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /status: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_grid_status(self, update, context):
        try:
            if not self._require_grid(update):
                return
            logger.info("Telegram /status received")
            uptime_s = int(time.time() - self._started_at)
            hours, remainder = divmod(uptime_s, 3600)
            minutes, secs = divmod(remainder, 60)

            mode = self.strategy.env.upper()
            cb_status = "🛑 HALTED" if self.circuit_breaker.halted else "✅ OK"

            # Check if multi-pair mode
            if hasattr(self.strategy, 'pairs') and self.strategy.pairs:
                # Multi-pair mode: show all pairs
                lines = [
                    f"📊 <b>Bot Status</b>",
                    "•••",
                    f"Mode: {mode} | CB: {cb_status}",
                    f"⏱ <b>Up:</b> {hours}h {minutes}m {secs}s",
                    "•••"
                ]

                # Get capital info from CapitalManager
                total_capital = getattr(self.strategy, '_capital_mgr', None)
                if total_capital:
                    capital_info = f"💰 Capital: ${total_capital.total_capital:,.0f} | Available: ${total_capital.available:,.0f}"
                else:
                    capital_info = "💰 Capital: N/A"

                # Show each pair's grid state
                grid_order_trackers = getattr(self.strategy, 'grid_order_trackers', {})
                for symbol, engine in self.strategy.pairs.items():
                    state_machine = self.strategy.state_machines.get(symbol)
                    grid_manager = self.strategy.grid_managers.get(symbol)
                    per_pair_tracker = grid_order_trackers.get(symbol)

                    if state_machine and grid_manager:
                        state = state_machine.state.value
                        pending = per_pair_tracker.total_pending if per_pair_tracker else 0
                        lines.append(f"{engine.display_pair} | Grid: <b>{state}</b> | Pending: {pending}")

                lines.append("•••")
                lines.append(capital_info)

                logger.info(f"Telegram /status response: multi-pair mode with {len(self.strategy.pairs)} pairs")
                update.message.reply_text("\n".join(lines), parse_mode="HTML")
            else:
                # Single-pair mode (backward compatibility)
                state = self.state_machine.state.value
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
                    f"CB: {cb_status}\n"
                    f"⏱ <b>Up:</b> {hours}h {minutes}m {secs}s\n"
                    f"•••\n"
                    f"📐 Grid: {levels} buy + {levels} sell levels\n"
                    f"📏 <b>Space:</b> ${spacing_buy:.0f}/${spacing_sell:.0f}\n"
                    f"📋 <b>Pending:</b> {pending}\n"
                    f"•••\n"
                    f"💰 <b>Base:</b> ${base_capital:,.0f}\n"
                    f"📈 <b>Comp:</b> ${compound:,.2f} ({growth_pct:+.1f}%)",
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
            # Multi-pair: use first pair's price
            if isinstance(indicators, dict):
                first = next((v for v in indicators.values() if v is not None), None)
                price = first[4] if first else 0
            else:
                price = indicators[4] if indicators else 0

            usdt = strategy._get_usdt_balance()
            base_bal = strategy._get_base_balance()
            base_value = base_bal * price if price else 0
            equity = usdt + base_value
            base_asset = getattr(strategy, 'base_asset', 'CRYPTO')

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
                f"📊 <b>Eq:</b> ${equity:,.2f}\n"
                f"⚙️ <b>Env:</b> {mode}\n"
                f"•••\n"
                f"📐 <b>Grid:</b> ${compound:,.2f} ({growth_pct:+.1f}%)\n"
                f"📏 <b>Base:</b> ${base_cap:,.0f}\n"
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
            # Multi-pair: use first pair's price
            if isinstance(indicators, dict):
                first = next((v for v in indicators.values() if v is not None), None)
                price = first[4] if first else 0
            else:
                price = indicators[4] if indicators else 0
            equity = self.strategy._estimate_equity(price
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

    def _cmd_price(self, update, context=None):
        try:
            logger.info("Telegram /price received")
            snapshot = self.strategy.get_indicators_snapshot()

            # Multi-pair: snapshot is Dict[str, Optional[tuple]]
            if isinstance(snapshot, dict):
                args = getattr(context, 'args', None) or []
                target_symbol = args[0].upper().replace("/", "-") if args else None

                # If specific pair requested, show detail view
                if target_symbol:
                    data = snapshot.get(target_symbol)
                    if data is None:
                        update.message.reply_text(f"⚠️ No data for {target_symbol}")
                        return
                    self._send_price_detail(update, target_symbol, data)
                    return

                # Show all pairs summary
                lines = ["💲 <b>Live Prices</b>\n•••"]
                for symbol, data in snapshot.items():
                    if data is not None:
                        price = data[4]
                        display = symbol.replace("-", "/")
                        lines.append(f"<b>{display}</b>: ${price:,.2f}")
                    else:
                        display = symbol.replace("-", "/")
                        lines.append(f"<b>{display}</b>: ⏳ loading")
                update.message.reply_text("\n".join(lines), parse_mode="HTML")
                return

            # Legacy single-pair: snapshot is a tuple
            if not snapshot:
                update.message.reply_text("⚠️ Could not fetch live price.")
                return

            display_pair = getattr(self.strategy, 'display_pair', 'Multi-pair')
            self._send_price_detail(update, display_pair, snapshot)
        except Exception as e:
            logger.error(f"Error in /price: {e}")
            update.message.reply_text(f"⚠️ Error getting price: {e}")

    def _send_price_detail(self, update, display_pair, indicators):
        """Send detailed price + indicators for a single pair."""
        live_price = indicators[4] if indicators else None
        if live_price is None:
            update.message.reply_text(f"⚠️ No price for {display_pair}")
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

    def _cmd_server(self, update, context):
        try:
            logger.info("Telegram /system received")
            stats = get_stats()
            strategy = self.strategy

            lines = [
                f"🖥️ <b>System Status</b>",
                f"•••",
                f"⚙️ <b>Mode:</b> {strategy.env.upper()}",
                f"💰 <b>Capital:</b> ${strategy.capital_usdt:,.0f}",
                f"📊 <b>Pairs:</b> {', '.join(strategy.pairs.keys())}" if hasattr(strategy, 'pairs') else "",
                f"•••",
            ]

            # Grid Engine
            lines.append(f"🤖 <b>Grid Engine</b>")
            state_machines = getattr(strategy, 'state_machines', {})
            if hasattr(strategy, 'pairs') and strategy.pairs:
                for sym in strategy.pairs:
                    sm = state_machines.get(sym)
                    state_str = sm.state.value if sm else "UNKNOWN"
                    emoji = "🟢" if "ACTIVE" in state_str else "🔴"
                    lines.append(f"  {emoji} {sym}: {state_str}")
            else:
                lines.append(f"  ⚪ No pairs")

            # Trend Engine
            trend_cap = getattr(strategy, '_trend_capital', 0)
            if trend_cap and trend_cap > 0:
                trend_positions = 0
                for sym, eng in strategy.pairs.items():
                    pm = strategy._position_managers.get(sym) if hasattr(strategy, '_position_managers') else None
                    if pm and hasattr(pm, 'has_open_position') and pm.has_open_position():
                        trend_positions += 1
                lines.append(f"📈 <b>Trend Engine</b>")
                lines.append(f"  Capital: ${trend_cap:,.0f} | Positions: {trend_positions}")
            else:
                lines.append(f"📈 <b>Trend Engine</b>: Capital=$0 (disabled)")

            # Signal Copy Engine
            sig = getattr(strategy, '_signal_engine', None)
            if sig:
                status = sig.get_status()
                mode = "AUDIT" if status.get("audit_mode") else "LIVE"
                lines.append(f"📡 <b>Signal Copy Engine</b>")
                lines.append(f"  Mode: {mode} | State: {status.get('state', 'N/A')}")
                lines.append(f"  Positions: {status.get('open_positions', 0)} | Trades today: {status.get('risk', {}).get('trades_today', 0)}")
            else:
                lines.append(f"📡 <b>Signal Copy Engine</b>: Disabled")

            # ML
            if hasattr(strategy, '_ml_classifier') and strategy._ml_classifier:
                lines.append(f"🧠 <b>ML:</b> {strategy._ml_summary()}")

            # Server resources
            lines.append(f"•••")
            lines.append(f"💻 CPU: {stats.cpu_percent:.0f}% | RAM: {stats.ram_percent:.0f}% | Disk: {stats.disk_percent:.0f}%")
            lines.append(f"💾 {stats.disk_used_gb:.1f}/{stats.disk_total_gb:.1f} GB")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /system: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

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

            # Check if multi-pair mode
            if hasattr(strategy, 'pairs') and strategy.pairs:
                # Multi-pair mode: show all pairs' trend positions
                lines = ["🤖 <b>TREND ENGINE</b>", "•••"]

                total_open = 0
                total_max = 0

                # Count total positions across all pairs
                for symbol, engine in strategy.pairs.items():
                    pm = strategy._position_managers.get(symbol)
                    if pm:
                        positions = pm.get_all_positions()
                        total_open += len(positions)
                        total_max = getattr(pm, '_max_positions', 8)

                lines.append(f"Open positions: {total_open}/{total_max}")
                lines.append("•••")

                # Show positions per pair
                for symbol, engine in strategy.pairs.items():
                    pm = strategy._position_managers.get(symbol)
                    if not pm:
                        continue

                    positions = pm.get_all_positions()
                    if positions:
                        lines.append(f"{engine.display_pair} ({len(positions)} open)")
                        for pos in positions:
                            current_price = strategy._last_price.get(symbol, pos.entry_price) if hasattr(strategy, '_last_price') else pos.entry_price
                            pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100 if current_price and pos.entry_price else 0
                            sign = "+" if pnl_pct >= 0 else ""
                            lines.append(f"  {pos.amount:.2f} {engine.base_asset} @ ${pos.entry_price:.2f} | SL ${pos.stop_loss:.2f} TP ${pos.take_profit:.2f}")
                            lines.append(f"  P&L: {sign}{pnl_pct:.1f}% | Trail: ${pos.trailing_stop:.2f}")
                    else:
                        lines.append(f"{engine.display_pair} — No positions")

                lines.append("•••")

                # Get capital info from CapitalManager
                if hasattr(strategy, '_capital_mgr'):
                    lines.append(f"Capital: ${strategy._capital_mgr.total_capital:.2f} | Available: ${strategy._capital_mgr.available:.2f}")
                elif hasattr(strategy, '_position_manager'):
                    lines.append(f"Capital: ${strategy._position_manager._capital:.2f}")

                logger.info(f"Telegram /trend_status response: multi-pair mode with {total_open} positions")
                update.message.reply_text("\n".join(lines), parse_mode="HTML")
            else:
                # Single-pair mode (backward compatibility)
                tm = strategy._trend_manager
                pm = strategy._position_manager
                lines = ["🤖 <b>TREND ENGINE</b>", "•••"]
                positions = pm.get_all_positions()
                lines.append(f"Open positions: {len(positions)}/{pm._max_positions}")

                for pos in positions:
                    current = getattr(strategy, '_last_price', pos.entry_price)
                    pnl_pct = (current - pos.entry_price) / pos.entry_price * 100 if current and pos.entry_price else 0
                    lines.append(f"  {pos.amount:.6f} base @ ${pos.entry_price:.2f} | SL ${pos.stop_loss:.2f} TP ${pos.take_profit:.2f}")
                    lines.append(f"  P&L: {pnl_pct:+.1f}% | Trail: ${pos.trailing_stop:.2f}")

                lines.append(f"Capital: ${pm._capital:.2f}")

                if hasattr(strategy, '_last_trend_score') and strategy._last_trend_score:
                    score = strategy._last_trend_score
                    lines.append(f"Signal score: {score.total}/7")
                    for d in score.details:
                        lines.append(f"  +{d['points']} {d['signal']}: {d['note']}")

                update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /trend_status: {e}")
            update.message.reply_text(f"⚠️ Error getting trend status: {e}")

    def _cmd_trend_capital(self, update, context=None):
        try:
            logger.info("Telegram /trend_capital received")
            strategy = self.strategy
            if not hasattr(strategy, '_position_managers') and not hasattr(strategy, '_position_manager'):
                update.message.reply_text("Trend engine not active")
                return

            text = update.message.text.strip()
            parts = text.split()

            # Get first available manager for display
            if hasattr(strategy, '_position_managers') and strategy._position_managers:
                first_pm = next(iter(strategy._position_managers.values()))
            else:
                first_pm = strategy._position_manager

            if len(parts) < 2:
                update.message.reply_text(
                    f"Current trend capital: ${first_pm._capital:.2f}\n"
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

            old = first_pm._capital
            # Update all per-pair managers
            if hasattr(strategy, '_position_managers'):
                for pm in strategy._position_managers.values():
                    pm._capital = amount
            else:
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

            lines = ["📈 <b>TREND P&L</b>", "•••"]
            lines.append(f"Total trades: {summary['total_trades']}")
            lines.append(f"Win rate: {summary['win_rate']:.1f}% ({summary['winning']}W / {summary['losing']}L)")
            lines.append(f"Total P&L: ${summary['net_pnl']:.2f}")
            lines.append(f"Profit factor: {perf['profit_factor']:.2f}")
            lines.append(f"Avg win: ${perf['avg_win']:.2f} | Avg loss: ${perf['avg_loss']:.2f}")
            lines.append(f"Avg duration: {perf['avg_duration']:.0f} min")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /trend_pnl: {e}")
            update.message.reply_text(f"⚠️ Error getting trend P&L: {e}")

    def _cmd_trend_close(self, update, context=None):
        try:
            logger.info("Telegram /trend_close received")
            strategy = self.strategy
            if not hasattr(strategy, '_position_managers') and not hasattr(strategy, '_position_manager'):
                update.message.reply_text("Trend engine not active")
                return

            # Count all positions across pairs
            total_positions = 0
            if hasattr(strategy, '_position_managers'):
                for pm in strategy._position_managers.values():
                    total_positions += len(pm.get_all_positions())
            else:
                total_positions = len(strategy._position_manager.get_all_positions())

            if not total_positions:
                update.message.reply_text("No open trend positions")
                return

            strategy._trend_force_close = True
            logger.info(f"Telegram /trend_close — closing {total_positions} position(s)")
            update.message.reply_text(f"Closing {total_positions} trend position(s) on next tick...")
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

            lines = ["📜 <b>TREND HISTORY</b>", "•••"]
            for t in trades:
                emoji = "+" if t["pnl"] >= 0 else "-"
                lines.append(f"{emoji} {t['side']} {t['amount']:.1f}@${t['entry_price']:.2f}->${t['exit_price']:.2f} | ${t['pnl']:+.2f} ({t['exit_reason']})")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /trend_history: {e}")
            update.message.reply_text(f"⚠️ Error getting trend history: {e}")

    def _cmd_help(self, update, context):
        logger.info("Telegram /help received")
        base_asset = getattr(self.strategy, 'base_asset', 'CRYPTO')
        display_pair = getattr(self.strategy, 'display_pair', 'Multi-pair')
        update.message.reply_text(
            "📖 <b>Available Commands</b>\n"
            "•••\n"
            "<b>System:</b>\n"
            "/status — Daily summary (all engines P&L + server)\n"
            "/system — Full engine details + server resources\n"
            f"/price — Current {display_pair} price with indicators\n"
            "/logs — Last 30 lines from today's bot log\n"
            "/errors — Recent errors and crashes\n"
            "•••\n"
            "<b>Grid:</b>\n"
            "/grid_status — Grid state, pending orders, uptime\n"
            "/pnl — Grid P&L (today/week/month)\n"
            f"/balance — USDT, {base_asset}, equity, grid capital\n"
            "/capital &lt;amount&gt; — Update grid capital\n"
            "/pause — Pause grid (cancel all orders)\n"
            "/resume — Resume grid trading\n"
            "/reset — Reset circuit breaker\n"
            "/trades — Last 5 closed grid trades\n"
            "/pending — All pending buy/sell orders\n"
            "/fees — Fee analysis and overtrading detection\n"
            "/clear — Clear logs and grid state\n"
            "•••\n"
            "<b>Trend:</b>\n"
            "/trend_status — Trend engine status and positions\n"
            "/trend_capital &lt;amount&gt; — Update trend capital\n"
            "/trend_pnl — Trend P&L report\n"
            "/trend_close — Force close all trend positions\n"
            "/trend_history — Recent trend trade history\n"
            "•••\n"
            "<b>Signal:</b>\n"
            "/signal_status — Signal engine status & positions\n"
            "/signal_pnl — Signal trades P&L report\n"
            "/signal_channels — Channel stats & approval rates\n"
            "/signal_history — Recent signal messages\n"
            "/signal_pause — Pause signal execution\n"
            "/signal_resume — Resume signal execution\n"
            "/signal_close &lt;PAIR&gt; — Close a signal position\n"
            "•••\n"
            "/help — This message",
            parse_mode="HTML"
        )

    # ── Signal Copy Commands ─────────────────────────────────────────

    def _cmd_signal_status(self, update, context):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return
        status = engine.get_status()
        risk = status.get("risk", {})
        positions = engine._position_mgr.get_open_positions()

        mode_tag = "AUDIT" if status.get("audit_mode") else "LIVE"
        lines = [
            f"📡 <b>SIGNAL ENGINE ({mode_tag})</b>",
            "•••",
            f"State: <b>{status['state']}</b>",
            f"Open positions: {status['open_positions']}/{engine._risk._max_positions}",
            f"Trades today: {risk.get('trades_today', 0)}/{risk.get('max_trades', 0)}",
            f"Daily P&L: ${risk.get('daily_pnl', 0):.2f}",
        ]

        if positions:
            lines.append("•••")
            lines.append("📈 <b>Open Positions:</b>")
            for pos in positions:
                pnl_pct = 0
                if pos.entry_price > 0:
                    pnl_pct = ((pos.remaining_amount * pos.entry_price) - (pos.amount * pos.entry_price)) / (pos.amount * pos.entry_price) * 100
                lines.append(
                    f"  {pos.symbol}: ${pos.entry_price:,.2f} ({pos.hold_minutes}m) "
                    f"SL=${pos.stop_loss:,.2f} TPs={'✅' if pos.tp1_hit else '⬜'}/{'✅' if pos.tp2_hit else '⬜'}/{'✅' if pos.tp3_hit else '⬜'}"
                )

        update.message.reply_text("\n".join(lines), parse_mode="HTML")

    def _cmd_signal_pnl(self, update, context):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return

        journal = engine._journal
        today = journal.summary(days=0)
        week = journal.summary(days=7)
        month = journal.summary(days=30)
        by_channel = journal.summary_by_channel(days=30)

        lines = [
            "📊 <b>SIGNAL P&L</b>",
            "•••",
            f"Today: {today['total_trades']} trades, ${today['total_pnl']:.2f} ({today['win_rate']:.0f}% win)",
            f"Week: {week['total_trades']} trades, ${week['total_pnl']:.2f} ({week['win_rate']:.0f}% win)",
            f"Month: {month['total_trades']} trades, ${month['total_pnl']:.2f} ({month['win_rate']:.0f}% win)",
        ]

        if by_channel:
            lines.append("•••")
            lines.append("📋 <b>By Channel (30d):</b>")
            for name, stats in by_channel.items():
                lines.append(f"  {name}: {stats['trades']} trades, ${stats['pnl']:.2f} ({stats['win_rate']:.0f}% win)")

        update.message.reply_text("\n".join(lines), parse_mode="HTML")

    def _cmd_signal_pause(self, update, context):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return
        engine.pause()
        update.message.reply_text("⏸ Signal engine paused.")

    def _cmd_signal_resume(self, update, context):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return
        engine.resume()
        update.message.reply_text("▶️ Signal engine resumed.")

    def _cmd_signal_close(self, update, context=None):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return
        text = getattr(update.message, 'text', '')
        parts = text.split()
        if len(parts) < 2:
            update.message.reply_text("Usage: /signal_close BTC-USDT")
            return
        pair = parts[1].upper().replace("/", "-")
        if not pair.endswith("-USDT"):
            pair = f"{pair}-USDT"
        result = engine.manual_close(pair)
        if result:
            update.message.reply_text(f"Closed signal position: {pair}")
        else:
            update.message.reply_text(f"No open signal position for {pair}")

    def _cmd_signal_history(self, update, context):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return

        signals = engine._journal.recent_signals(limit=10)
        if not signals:
            update.message.reply_text("No signals received yet.")
            return

        lines = ["📨 <b>Recent Signals</b>", "•••"]
        for s in signals:
            ts = s.get("timestamp", "")[:16]
            action = s.get("action", "?")
            pair = s.get("pair", "?")
            text = s.get("text", "")[:60]
            lines.append(f"{ts} [{action}] {pair}: {text}")

        update.message.reply_text("\n".join(lines), parse_mode="HTML")

    def _cmd_signal_channels(self, update, context):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return

        channel_ids_str = os.environ.get("SIGNAL_CHANNEL_IDS", "")
        channel_count = len([c for c in channel_ids_str.split(",") if c.strip()])
        mode_tag = "AUDIT" if engine._audit_mode else "LIVE"

        stats = engine._journal.channel_stats()
        total_msgs = sum(s["messages"] for s in stats)
        total_approved = sum(s["trades_approved"] for s in stats)
        total_rejected = sum(s["trades_rejected"] for s in stats)

        lines = [
            f"📡 <b>SIGNAL CHANNELS</b> ({mode_tag})",
            f"•••",
            f"Listening: <b>{channel_count}</b> channel(s)",
            f"Messages received: <b>{total_msgs}</b>",
            f"Signals approved: <b>{total_approved}</b>",
            f"Signals rejected: <b>{total_rejected}</b>",
        ]

        if stats:
            lines.append("•••")
            for s in stats:
                name = s["channel"]
                lines.append(f"📋 <b>{name}</b>")
                lines.append(f"  Messages: {s['messages']} | Signals: {s['signals']} | Noise: {s['not_signal']}")
                lines.append(f"  Approved: {s['trades_approved']} | Rejected: {s['trades_rejected']} | P&L: ${s['trades_pnl'] or 0:.2f}")
        else:
            lines.append("•••")
            lines.append("No messages received yet.")

        update.message.reply_text("\n".join(lines), parse_mode="HTML")
