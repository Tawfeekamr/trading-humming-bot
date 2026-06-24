import os
import time
import json
import html
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


def _fmt_price(value) -> str:
    """Compact price formatting for Telegram: 436 -> '436', 0.198 -> '0.198'."""
    if value is None:
        return "?"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_duration(seconds: float) -> str:
    """Human-readable hold time: 90 -> '1m', 3700 -> '1h2m', 90000 -> '1d1h'."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    m, _ = divmod(seconds, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m}m"
    d, h = divmod(h, 24)
    return f"{d}d{h}h"


def _signal_price(symbol: str) -> float:
    """Best-effort live mid-price from the Rust engine; 0.0 if unavailable."""
    try:
        import urllib.request
        sym = symbol.replace("-", "")
        url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + f"/api/v1/orderbook?symbol={sym}&limit=1"
        data = json.loads(urllib.request.urlopen(url, timeout=4).read())
        bids, asks = data.get("bids", []), data.get("asks", [])
        if bids and asks:
            return (float(bids[0][0]) + float(asks[0][0])) / 2
        if bids:
            return float(bids[0][0])
    except Exception:
        pass
    return 0.0


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

    def attach_signal_engines(self, spot_engine=None, futures_engine=None):
        """Hand the live signal engines to the handler so the control commands
        (/signal_pause, /signal_resume, /signal_pnl, /signal_inject,
        /signal_close) can drive them.

        The headless signal-listener builds the handler BEFORE the engines
        exist (the engines need config + keys), so the wiring happens here,
        after construction. Without this call those commands reply
        'Signal engine not configured.' The legacy Hummingbot script set
        strategy._signal_engine directly; the headless migration dropped it.
        """
        if spot_engine is not None:
            self.strategy._signal_engine = spot_engine
        if futures_engine is not None:
            self.strategy._futures_engine = futures_engine

    def _startup_message(self) -> str:
        """Bot-online ping listing every available command. Kept in sync with
        /help — both must advertise the full command set, including futures."""
        return (
            "📡 <b>Telegram Command Handler Online</b>\n"
            "•••\n"
            "<b>System:</b> /status /price /logs /errors /readiness\n"
            "<b>Overview:</b> /bots /pnl_all /trades /help\n"
            "<b>Capital:</b> /capital\n"
            "<b>Grid:</b> /grid_status\n"
            "<b>Trend:</b> /trend_status /trend_pnl /trend_history\n"
            "<b>Swing:</b> /swing_status\n"
            "<b>Signal:</b> /signal_status /signal_pnl /signal_history "
            "/signal_channels /signal_pause /signal_resume /signal_close /signal_inject\n"
            "<b>Futures:</b> /futures_status /futures_pnl\n"
            "<b>MR:</b> /mean_status /mean_pnl\n"
        )

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

                self._tg_post("sendMessage", data={
                    "chat_id": self._chat_id,
                    "text": self._startup_message(),
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
            "system": self._cmd_status,
            "server": self._cmd_status,
            "bots": self._cmd_bots,
            "help": self._cmd_help,
            "logs": self._cmd_logs,
            "errors": self._cmd_errors,
            "price": self._cmd_price,
            "readiness": self._cmd_readiness,
            # P&L + trades
            "pnl_all": self._cmd_pnl_all,
            "trades": self._cmd_trades,
            # Engine status (Rust API)
            "grid_status": self._cmd_grid_status,
            "trend_status": self._cmd_trend_status,
            "swing_status": self._cmd_swing_status,
            "swing_pnl": self._cmd_swing_status,
            "signal_status": self._cmd_signal_status,
            "signal_pnl": self._cmd_signal_pnl,
            "futures_status": self._cmd_futures_status,
            "futures_pnl": self._cmd_futures_pnl,
            "mean_status": self._cmd_mean_status,
            "mean_pnl": self._cmd_mean_status,
            "capital": self._cmd_capital,
            # Signal engine control
            "signal_channels": self._cmd_signal_channels,
            "signal_history": self._cmd_signal_history,
            "signal_pause": self._cmd_signal_pause,
            "signal_resume": self._cmd_signal_resume,
            "signal_inject": self._cmd_signal_inject,
            "signal_close": self._cmd_signal_close,
            # Trend history
            "trend_history": self._cmd_trend_history,
            "trend_pnl": self._cmd_trend_status,
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
        """Server health — stdlib only (no psutil/docker dependency)."""
        try:
            logger.info("Telegram /status received")
            import shutil, os
            uptime_s = int(time.time() - self._started_at)
            hours, remainder = divmod(uptime_s, 3600)
            minutes, secs = divmod(remainder, 60)
            # Disk (stdlib)
            disk = shutil.disk_usage("/")
            disk_total_gb = disk.total / (1024**3)
            disk_used_gb = disk.used / (1024**3)
            disk_pct = (disk.used / disk.total) * 100
            # RAM from /proc/meminfo
            try:
                with open("/proc/meminfo") as f:
                    ml = {l.split(":")[0].strip(): l.split(":")[1].strip() for l in f if ":" in l}
                mt = int(ml.get("MemTotal", "0 kB").split()[0]) * 1024
                ma = int(ml.get("MemAvailable", "0 kB").split()[0]) * 1024
                mu = mt - ma
                mem_pct = (mu / mt) * 100 if mt > 0 else 0
                mem_str = f"{mu/(1024**3):.1f}/{mt/(1024**3):.1f} GB"
            except Exception:
                mem_pct = 0
                mem_str = "?"
            # CPU from /proc/loadavg
            try:
                with open("/proc/loadavg") as f:
                    load1 = float(f.read().split()[0])
                cpu_pct = (load1 / (os.cpu_count() or 1)) * 100
            except Exception:
                cpu_pct = 0
            # Rust engine health
            try:
                import urllib.request
                req = urllib.request.Request(os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/health")
                urllib.request.urlopen(req, timeout=3)
                engine = "\U0001f7e2 Rust engine: Online"
            except Exception:
                engine = "\U0001f534 Rust engine: Offline"
            lines_out = [
                "\U0001f5a5 Server Status",
                "\u2022\u2022\u2022",
                f"\u23f1 Uptime: {hours}h {minutes}m",
                f"\U0001f4bb CPU: {cpu_pct:.0f}% (1m load avg)",
                f"\U0001f4be RAM: {mem_pct:.0f}% ({mem_str})",
                f"\U0001f4c0 Disk: {disk_pct:.0f}% ({disk_used_gb:.1f}/{disk_total_gb:.1f} GB)",
                "\u2022\u2022\u2022",
                engine,
                "\u2022\u2022\u2022",
                "<i>Use /bots for engine status, /pnl_all for P&L</i>",
            ]
            update.message.reply_text("\n".join(lines_out), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /status: {e}")
            update.message.reply_text(f"\u26a0 Error: {e}")

    def _cmd_grid_status(self, update, context):
        self._rust_strategy_status(update, "grid", "Grid Engine", "📊")
        return
        try:  # dead code — old implementation
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

                # Get capital info — try Rust API capital, fallback to config
                capital_info = "💰 Capital: N/A"
                trend_cap = getattr(self.strategy, '_trend_capital', 0)
                grid_pnl = getattr(self.strategy, 'grid_pnl', {})
                total_grid_pnl = sum(grid_pnl.values()) if grid_pnl else 0
                trend_statuses = getattr(self.strategy, '_trend_statuses', {})
                total_trend_pnl = sum(ts.get("pnl", 0) for ts in trend_statuses.values()) if trend_statuses else 0
                g_sign = "+" if total_grid_pnl >= 0 else ""
                t_sign = "+" if total_trend_pnl >= 0 else ""
                capital_info = (
                    f"💰 Grid P&L: {g_sign}${total_grid_pnl:.2f} | "
                    f"Trend P&L: {t_sign}${total_trend_pnl:.2f}"
                )

                # Show each pair's grid state (from Rust engine API via telegram_poll_loop)
                grid_order_trackers = getattr(self.strategy, 'grid_order_trackers', {})
                for symbol, engine in self.strategy.pairs.items():
                    state_machine = self.strategy.state_machines.get(symbol)
                    per_pair_tracker = grid_order_trackers.get(symbol)

                    if state_machine:
                        state = state_machine.state.value
                        pending = per_pair_tracker.total_pending if per_pair_tracker else 0
                        pair_pnl = grid_pnl.get(symbol, 0) if grid_pnl else 0
                        sign = "+" if pair_pnl >= 0 else ""
                        grid_detail = getattr(state_machine, 'details', '')
                        detail_line = f"\n  <i>{html.escape(grid_detail)}</i>" if grid_detail and state == "Paused" else ""
                        lines.append(
                            f"🤖 {engine.display_pair} | Grid: <b>{state}</b> | "
                            f"P&L: {sign}${pair_pnl:.2f} | Pending: {pending}{detail_line}"
                        )

                # Show trend status per pair from Rust API
                for symbol, engine in self.strategy.pairs.items():
                    ts = trend_statuses.get(symbol)
                    if ts:
                        t_state = ts.get("state", "WAITING")
                        t_pnl = ts.get("pnl", 0)
                        t_details = ts.get("details", "")
                        t_sign = "+" if t_pnl >= 0 else ""
                        trend_detail = f"\n  <i>{html.escape(t_details)}</i>" if t_details and t_state == "WAITING" else ""
                        lines.append(
                            f"📈 {engine.display_pair} | Trend: <b>{t_state}</b> | "
                            f"P&L: {t_sign}${t_pnl:.2f}{trend_detail}"
                        )

                lines.append("•••")
                lines.append(capital_info)
                if trend_cap:
                    lines.append(f"💰 Trend Capital: ${trend_cap:,.0f}")

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

    def _cmd_pnl_all(self, update, context):
        """Consolidated realized P&L across all engines (today / week / month)."""
        try:
            logger.info("Telegram /pnl_all received")
            import sqlite3
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")                              # since UTC midnight
            week = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
            month = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
            windows = [("Today", today), ("Week", week), ("Month", month)]
            # Single query against the unified trades table — every engine writes here.
            conn = sqlite3.connect("data/trades.db")
            totals = [0.0, 0.0, 0.0]
            body = ""
            for eng in ("Grid", "Trend", "Swing", "MR", "Signal"):
                per = []
                for i, (_, cutoff) in enumerate(windows):
                    v = conn.execute(
                        "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE engine=? AND timestamp >= ?",
                        (eng.lower(), cutoff)).fetchone()[0] or 0.0
                    per.append(v)
                    totals[i] += v
                body += f"{eng:<7}" + "".join(f"{self._fmt_pnl(v):>11}" for v in per) + "\n"
            conn.close()
            total_line = "TOTAL  " + "".join(f"{self._fmt_pnl(t):>11}" for t in totals)
            update.message.reply_text(
                "📊 <b>Consolidated P&amp;L</b> (realized)\n"
                "•••\n"
                f"{'':<7}{'Today':>11}{'Week':>11}{'Month':>11}\n"
                f"{body}"
                "•••\n"
                f"{total_line}\n"
                "<i>from unified trades table (all engines)</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /pnl_all: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

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

    def _rust_api(self, path: str, timeout: float = 5.0):
        """GET a JSON endpoint from the Rust engine API. Returns parsed JSON or None.

        The hybrid runner keeps live state (orders, order books, strategies) in
        the Rust engine; legacy Hummingbot attrs on the Python strategy proxy are
        faked as {} and crash on attribute access. Commands read the Rust API
        directly via this helper.
        """
        import urllib.request
        url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            logger.warning(f"Rust API {path} failed: {e}")
            return None

    def _cmd_pause(self, update, context):
        try:
            # Hybrid runner: no runtime pause switch. The legacy manual_pause attr
            # isn't read by the Rust engine. Risk is auto-managed by the circuit
            # breaker; to fully stop, stop the container.
            update.message.reply_text(
                "⏸️ No runtime pause on the hybrid runner — grid/trend run continuously in the Rust engine.\n"
                "Risk is auto-managed by the circuit breaker (halts new entries on drawdown).\n"
                "To fully stop: docker compose stop trading-bot-rust on the EC2 host."
            )
        except Exception as e:
            logger.error(f"Error in /pause: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_resume(self, update, context):
        try:
            update.message.reply_text(
                "▶️ The bot runs continuously — no paused state to resume.\n"
                "If the circuit breaker has halted new entries, it self-clears when MTM equity recovers above the threshold."
            )
        except Exception as e:
            logger.error(f"Error in /resume: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_reset(self, update, context):
        try:
            # Hybrid runner: the circuit breaker lives in the Rust engine (in-memory,
            # persisted to data/risk_state.json every tick). The Python side can't
            # reset it directly — the Rust engine's in-memory state is authoritative
            # and overwrites the file each tick. So this command can't force a reset
            # from here.
            update.message.reply_text(
                "🔄 Circuit breaker is Rust-managed (in-memory + data/risk_state.json).\n"
                "It self-clears when MTM equity recovers above ~$9.5k (5% daily limit).\n"
                "To force a reset: restart the container with risk_state.json cleared — "
                "the engine re-inits peak/sod from current equity on boot.\n"
                "⚠️ If equity is still in drawdown, it re-trips on the next tick."
            )
        except Exception as e:
            logger.error(f"Error in /reset: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_bots(self, update, context):
        """Show all engine statuses from Rust API."""
        try:
            import html as _html
            logger.info("Telegram /bots received")
            strategies = self._rust_api("/api/v1/strategies") or []
            if not strategies:
                update.message.reply_text("⚠️ Rust engine API unavailable.")
                return
            lines = ["🤖 <b>Engine Status</b>", "•••"]
            engines = {}
            for s in strategies:
                engines.setdefault(s.get("name", "?"), []).append(s)
            for name in ["grid", "trend", "swing", "mean_reversion"]:
                bots = engines.get(name, [])
                if not bots:
                    continue
                display = name.replace("mean_reversion", "MR").upper()
                total_pnl = sum(b.get("pnl", 0) for b in bots)
                lines.append(f"<b>{display}</b> P&L: {'+' if total_pnl>=0 else ''}${total_pnl:.2f}")
                for b in bots:
                    pair = b.get("pair", "?").replace("-", "/")
                    state = b.get("state", "?")
                    details = b.get("details", "")
                    emoji = "🟢" if state in ("Active", "IN_POSITION", "POSITION") else "🟡" if any(x in state for x in ["WAIT", "PAUSE", "SEARCH", "SCAN"]) else "⚪"
                    line = f"  {emoji} {pair}: {state}"
                    if details:
                        line += f"  <i>{_html.escape(details)}</i>"
                    lines.append(line)
            try:
                import json, sqlite3
                pos = {}
                try:
                    with open("data/signal_positions.json") as f:
                        pos = json.load(f)
                except Exception:
                    pass
                open_pos = {k: v for k, v in pos.items() if not v.get("is_closed")}
                c = sqlite3.connect("data/signal_journal.db")
                sig_pnl = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM signal_trades WHERE action LIKE 'CLOSE%'").fetchone()[0]
                c.close()
                lines.append(f"<b>SIGNAL</b> P&L: ${sig_pnl:.2f} | Open: {len(open_pos)}/5")
            except Exception:
                lines.append("<b>SIGNAL</b>: unavailable")
            lines.append("•••")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

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
            # Hybrid runner: open orders live in the Rust engine (the legacy
            # `strategy.order_tracker` is faked as {} by RunnerProxy, so
            # `.pending_orders()` crashed). Query the Rust API directly.
            import urllib.request
            url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/orders?symbol="
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    pending = json.loads(r.read())
            except Exception as e:
                update.message.reply_text(f"⚠️ Could not reach engine API: {e}")
                return

            if not pending:
                update.message.reply_text("📋 No pending orders.")
                return

            buys = [o for o in pending if str(o.get("side", "")).upper() == "BUY"]
            sells = [o for o in pending if str(o.get("side", "")).upper() == "SELL"]

            lines = [f"📋 <b>Pending Orders ({len(pending)})</b>", "•••"]

            if buys:
                buys.sort(key=lambda o: o.get("price") or 0, reverse=True)
                lines.append(f"📈 <b>BUY ({len(buys)})</b>")
                for o in buys:
                    p = o.get("price") or 0; q = o.get("quantity") or 0
                    lines.append(f"  {o.get('symbol','?')}: ${p:,.2f} × {q:.4f} (${p*q:.2f})")

            if sells:
                sells.sort(key=lambda o: o.get("price") or 0)
                lines.append(f"📉 <b>SELL ({len(sells)})</b>")
                for o in sells:
                    p = o.get("price") or 0; q = o.get("quantity") or 0
                    lines.append(f"  {o.get('symbol','?')}: ${p:,.2f} × {q:.4f} (${p*q:.2f})")

            lines.append("•••")
            total_buy = sum((o.get("price") or 0) * (o.get("quantity") or 0) for o in buys)
            total_sell = sum((o.get("price") or 0) * (o.get("quantity") or 0) for o in sells)
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
            # Hybrid runner: read live prices from the Rust engine order-book API
            # (legacy get_indicators_snapshot() is faked as {} -> not callable).
            strategies = self._rust_api("/api/v1/strategies") or []
            pairs = sorted({s.get("pair") for s in strategies if s.get("pair")})
            if not pairs:
                update.message.reply_text("⚠️ No pairs available from engine.")
                return

            lines = ["💲 <b>Live Prices</b>", "•••"]
            for pair in pairs:
                sym = pair.replace("-", "")
                ob = self._rust_api(f"/api/v1/orderbook?symbol={sym}&limit=1") or {}
                bids = ob.get("bids") or []
                asks = ob.get("asks") or []
                display = pair.replace("-", "/")
                if bids and asks and bids[0] and asks[0]:
                    mid = (float(bids[0][0]) + float(asks[0][0])) / 2
                    lines.append(f"<b>{display}</b>: ${mid:,.4f}")
                else:
                    lines.append(f"<b>{display}</b>: ⏳ loading")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
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

    def _cmd_readiness(self, update, context):
        """Production readiness score (0-100) with breakdown."""
        try:
            logger.info("Telegram /readiness received")

            # Initialize scores
            profitability_score = 0
            stability_score = 0
            strategy_score = 0
            risk_score = 15  # Assume configured (config has daily loss limit)
            maturity_score = 5  # Just started paper mode

            total_paper_pnl = 0.0
            api_responsive = False
            active_strategies = 0

            # Try to fetch status from Rust engine API
            try:
                import urllib.request
                req = urllib.request.Request(os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/strategies")
                resp = urllib.request.urlopen(req, timeout=5)
                data = json.loads(resp.read())
                api_responsive = True

                # API returns a JSON array of strategy statuses
                strategies = data if isinstance(data, list) else data.get("strategies", [])
                for status in strategies:
                    pnl = status.get("pnl", 0)
                    total_paper_pnl += pnl
                    state = status.get("state", "").upper()
                    if state in ["POSITION", "SCANNING", "WAITING", "SEARCHING"]:
                        active_strategies += 1

                logger.info(f"Readiness: API responsive, {active_strategies} active strategies, P&L=${total_paper_pnl:.2f}")

                # Also read from unified trades.db for total realized P&L (more accurate)
                try:
                    import sqlite3
                    c = sqlite3.connect("data/trades.db")
                    db_pnl = c.execute("SELECT COALESCE(SUM(pnl),0) FROM trades").fetchone()[0]
                    c.close()
                    if db_pnl > total_paper_pnl:
                        total_paper_pnl = db_pnl  # Use the higher (trades.db has full history)
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Readiness: Rust API unavailable ({e}), using fallback values")
                # Fallback: try to estimate from Python strategy
                grid_pnl = getattr(self.strategy, 'grid_pnl', {})
                total_paper_pnl = sum(grid_pnl.values()) if grid_pnl else 0.0
                trend_statuses = getattr(self.strategy, '_trend_statuses', {})
                for ts in trend_statuses.values():
                    total_paper_pnl += ts.get("pnl", 0)
                if hasattr(self.strategy, 'pairs') and self.strategy.pairs:
                    active_strategies = len(self.strategy.pairs) * 2  # Rough estimate
                else:
                    active_strategies = 1

            # Compute scores
            # Profitability (30 pts)
            if total_paper_pnl > 0:
                profitability_score = 30
            elif total_paper_pnl < 0:
                profitability_score = 0
            else:
                profitability_score = 15  # No data yet

            # Stability (20 pts) - based on API responsiveness
            if api_responsive:
                stability_score = 20
            else:
                stability_score = 10  # Few errors

            # Strategy coverage (20 pts)
            if active_strategies >= 4:
                strategy_score = 20
            elif active_strategies == 3:
                strategy_score = 15
            elif active_strategies == 2:
                strategy_score = 10
            else:
                strategy_score = 5

            # Total score
            total_score = profitability_score + stability_score + strategy_score + risk_score + maturity_score

            # Grade
            if total_score >= 90:
                grade = "A"
            elif total_score >= 80:
                grade = "B+"
            elif total_score >= 70:
                grade = "B"
            elif total_score >= 60:
                grade = "C"
            else:
                grade = "D"

            # Build response
            sign = "+" if total_paper_pnl >= 0 else ""
            lines = [
                f"🎯 Production Readiness: {total_score}/100 ({grade})",
                "•••",
            ]

            # Profitability
            pnl_emoji = "✅" if profitability_score == 30 else "⚠️" if profitability_score == 15 else "❌"
            lines.append(f"📊 Profitability: {pnl_emoji} {sign}${total_paper_pnl:.2f} paper P&L ({profitability_score}/30)")

            # Stability
            stability_emoji = "✅" if stability_score == 20 else "⚠️"
            stability_text = "API responsive" if stability_score == 20 else "API unreachable"
            lines.append(f"⏱ Stability: {stability_emoji} {stability_text} ({stability_score}/20)")

            # Strategy coverage
            strategy_emoji = "✅" if strategy_score >= 15 else "⚠️"
            lines.append(f"🔧 Strategies: {strategy_emoji} {active_strategies} active ({strategy_score}/20)")

            # Risk safety
            lines.append(f"🛡 Risk: ✅ Limits configured ({risk_score}/15)")

            # Paper maturity
            lines.append(f"⏳ Paper Maturity: ⚠️ Just started (5/15)")

            # Recommendation
            lines.append("•••")
            if total_score >= 80:
                lines.append("💡 Recommendation: Strong candidate for live trading!")
            elif total_score >= 60:
                lines.append("💡 Recommendation: Keep paper trading until August, then go live with $5-10K first.")
            else:
                lines.append("💡 Recommendation: Continue paper trading to build track record.")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /readiness: {e}")
            update.message.reply_text(f"⚠️ Error computing readiness: {e}")

    def _cmd_server(self, update, context):
        try:
            logger.info("Telegram /system received")
            stats = get_stats()
            strategy = self.strategy

            lines = [
                f"🖥️ <b>System Status</b>",
                f"•••",
                f"⚙️ <b>Mode:</b> {strategy.env.upper()}",
            ]
            capital_usdt = getattr(strategy, 'capital_usdt', 0)
            if isinstance(capital_usdt, (int, float)) and capital_usdt > 0:
                lines.append(f"💰 <b>Capital:</b> ${capital_usdt:,.0f}")
            if hasattr(strategy, 'pairs') and strategy.pairs:
                lines.append(f"📊 <b>Pairs:</b> {', '.join(strategy.pairs.keys())}")
            lines.append(f"•••")

            # Grid Engine
            lines.append(f"🤖 <b>Grid Engine</b>")
            state_machines = getattr(strategy, 'state_machines', {})
            if hasattr(strategy, 'pairs') and strategy.pairs:
                for sym in strategy.pairs:
                    sm = state_machines.get(sym)
                    state_str = sm.state.value if sm else "UNKNOWN"
                    emoji = "🟢" if "ACTIVE" in state_str else "🟡" if "PAUSED" in state_str else "🔴"
                    lines.append(f"  {emoji} {sym}: {state_str}")
            else:
                lines.append(f"  ⚪ No pairs")

            # Trend Engine — read positions from Rust API statuses
            trend_cap = getattr(strategy, '_trend_capital', 0)
            trend_statuses = getattr(strategy, '_trend_statuses', {})
            if trend_cap and trend_cap > 0:
                trend_positions = sum(
                    1 for ts in trend_statuses.values()
                    if ts.get("state") == "POSITION"
                )
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

            # ML Regime — from Rust engine strategies API
            regime_parts = []
            if hasattr(strategy, 'pairs') and strategy.pairs:
                for sym in strategy.pairs:
                    sm = state_machines.get(sym)
                    if sm and hasattr(sm, 'details') and sm.details:
                        detail = sm.details
                        first_seg = detail.split('|')[0].strip()
                        regime_parts.append(f"{sym}: {html.escape(first_seg)}")
            if regime_parts:
                lines.append(f"🧠 <b>ML Regime:</b> {' | '.join(regime_parts)}")

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
        self._rust_strategy_status(update, "trend", "Trend Engine", "📈")
        return
        try:  # dead code — old implementation
            logger.info("Telegram /trend_status received")
            strategy = self.strategy

            # Read live status from Rust engine API (populated by telegram_poll_loop)
            trend_statuses = getattr(strategy, '_trend_statuses', {})
            if not trend_statuses:
                update.message.reply_text("Trend engine not active — no status from Rust engine")
                return

            lines = ["📈 <b>TREND ENGINE</b>", "•••"]

            total_open = 0
            total_pnl = 0.0

            # Show per-pair trend status from Rust API
            for symbol, engine in (getattr(strategy, 'pairs', {}) or {}).items():
                ts = trend_statuses.get(symbol)
                if not ts:
                    lines.append(f"<b>{engine.display_pair}:</b> No data")
                    continue

                state = ts.get("state", "UNKNOWN")
                pnl = ts.get("pnl", 0)
                details = ts.get("details", "")
                total_pnl += pnl

                if state == "POSITION":
                    total_open += 1
                    sign = "+" if pnl >= 0 else ""
                    lines.append(f"<b>{engine.display_pair}:</b> {state}")
                    lines.append(f"  {details}")
                    lines.append(f"  Unrealized P&L: {sign}${pnl:.2f}")
                else:
                    lines.append(f"<b>{engine.display_pair}:</b> {state}")
                    if details:
                        # Format: "Score:4/5 (A:0 C:1 V:1 M:1 R:1) | dir:+1 | ADX=68 CHOP=51 RSI=60 | Score 4<5"
                        parts = details.split(" | ")
                        if len(parts) >= 2:
                            # First segment: score breakdown
                            lines.append(f"  {html.escape(parts[0])}")
                            # Remaining: dir + indicators + reason
                            rest = html.escape(" | ".join(parts[1:]))
                            lines.append(f"  <i>{rest}</i>")
                        else:
                            lines.append(f"  {html.escape(details)}")

            lines.append("•••")
            sign = "+" if total_pnl >= 0 else ""
            lines.append(f"Open: {total_open} | Total P&L: {sign}${total_pnl:.2f}")

            capital = getattr(strategy, '_trend_capital', 0)
            if capital:
                lines.append(f"Capital: ${capital:,.0f}")

            logger.info(f"Telegram /trend_status response: {total_open} open positions")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /trend_status: {e}")
            update.message.reply_text(f"⚠️ Error getting trend status: {e}")

    def _cmd_trend_capital(self, update, context=None):
        try:
            logger.info("Telegram /trend_capital received")
            strategy = self.strategy
            trend_statuses = getattr(strategy, '_trend_statuses', {})
            if not trend_statuses:
                update.message.reply_text("Trend engine not active")
                return

            text = update.message.text.strip()
            parts = text.split()
            current_capital = getattr(strategy, '_trend_capital', 0)

            if len(parts) < 2:
                update.message.reply_text(
                    f"Current trend capital: ${current_capital:,.0f}\n"
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

            old = current_capital
            strategy._trend_capital = amount
            logger.info(f"Telegram /trend_capital: ${old:,.0f} → ${amount:,.0f}")

            update.message.reply_text(
                f"✅ Trend capital updated\n"
                f"Before: ${old:,.0f}\n"
                f"Now: ${amount:,.0f}"
            )
        except Exception as e:
            logger.error(f"Error in /trend_capital: {e}")
            update.message.reply_text(f"⚠️ Error updating trend capital: {e}")

    def _cmd_trend_pnl(self, update, context):
        try:
            logger.info("Telegram /trend_pnl received")
            strategy = self.strategy
            trend_statuses = getattr(strategy, '_trend_statuses', {})
            if not trend_statuses:
                update.message.reply_text("Trend engine not active")
                return

            lines = ["📈 <b>TREND P&L</b>", "•••"]
            total_pnl = 0.0
            open_positions = 0
            for symbol, ts in trend_statuses.items():
                pnl = ts.get("pnl", 0)
                state = ts.get("state", "WAITING")
                total_pnl += pnl
                if state == "POSITION":
                    open_positions += 1
                pair_display = symbol.replace("-", "/")
                sign = "+" if pnl >= 0 else ""
                lines.append(f"  {pair_display}: {sign}${pnl:.2f} ({state})")

            sign = "+" if total_pnl >= 0 else ""
            lines.append("•••")
            # "pnl" from Rust status() is realized P&L when flat, realized +
            # unrealized when in a position — so the total is just "Total P&L",
            # not "Unrealized" (which was wrong whenever the engine was flat).
            lines.append(f"Total P&L: {sign}${total_pnl:.2f}")
            lines.append(f"Open positions: {open_positions}")

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
            # Unified source: prefer the Python _trend_journal if wired; fall
            # back to the Rust engine's SQLite journal. The hybrid RunnerProxy
            # leaves _trend_journal=None, but trend trades live in
            # data/trend_journal.db (shared volume) — reading it directly fixes
            # the false "Trend engine not active" on the hybrid runner.
            trades = []
            journal = getattr(strategy, '_trend_journal', None)
            if journal is not None:
                try:
                    trades = journal.recent_trades(limit=10)
                except Exception as e:
                    logger.warning(f"_trend_journal.recent_trades failed: {e}")
            if not trades:
                trades = self._rust_trend_trades(limit=10)

            if not trades:
                update.message.reply_text("No trend trades yet")
                return

            lines = ["📜 <b>TREND HISTORY</b>", "•••"]
            for t in trades:
                emoji = "+" if t["pnl"] >= 0 else "-"
                pair = t.get("pair", "")
                pair_str = f"{pair} " if pair else ""
                lines.append(f"{emoji} {pair_str}{t['side']} {t['amount']:.1f}@${t['entry_price']:.2f}->${t['exit_price']:.2f} | ${t['pnl']:+.2f} ({t['exit_reason']})")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /trend_history: {e}")
            update.message.reply_text(f"⚠️ Error getting trend history: {e}")

    def _rust_trend_trades(self, limit: int = 10) -> list:
        """Read recent closed trend trades from the Rust engine's SQLite journal.

        Used when the Python _trend_journal isn't wired (hybrid runner). The
        data/ volume is shared with the Rust container, which writes
        trend_trades in WAL mode — a concurrent read here is safe.
        """
        import sqlite3
        path = os.environ.get("TREND_JOURNAL_PATH", "data/trend_journal.db")
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT side, entry_price, exit_price, amount, pnl, exit_reason, pair "
                "FROM trend_trades ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [
                {
                    "side": r["side"] or "",
                    "entry_price": r["entry_price"] or 0.0,
                    "exit_price": r["exit_price"] or 0.0,
                    "amount": r["amount"] or 0.0,
                    "pnl": r["pnl"] or 0.0,
                    "exit_reason": r["exit_reason"] or "",
                    "pair": r["pair"] or "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Rust trend journal read failed ({path}): {e}")
            return []

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
            "/readiness — Production readiness score (0-100)\n"
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
            "/signal_inject &lt;text&gt; — Manually inject a signal for execution\n"
            "•••\n"
            "<b>Futures:</b>\n"
            "/futures_status — Futures engine status & positions\n"
            "/futures_pnl — Futures P&L report\n"
            "•••\n"
            "<b>Mean-Reversion:</b>\n"
            "/mean_status — Mean-reversion engine status\n"
            "•••\n"
            "/help — This message",
            parse_mode="HTML"
        )

    # ── Signal Copy Commands ─────────────────────────────────────────

    def _cmd_signal_status(self, update, context):
        try:
            logger.info("Telegram /signal_status received")
            pos = {}
            try:
                with open("data/signal_positions.json") as f:
                    pos = json.load(f)
            except Exception:
                pass
            open_pos = {k: v for k, v in pos.items() if not v.get("is_closed")}
            realized_total = sum((p.get("realized_pnl") or 0) for p in pos.values())
            n_closed = sum(1 for p in pos.values() if p.get("is_closed"))

            lines = ["📡 <b>SIGNAL ENGINE</b>", "•••"]
            lines.append(f"Open: {len(open_pos)} | Closed: {n_closed} | Realized P&L: ${realized_total:+.2f}")
            if not open_pos:
                lines.append("No open positions.")
            else:
                lines.append("•••")
                for sym, p in list(open_pos.items())[:8]:
                    entry = p.get("entry_price") or 0
                    tps = p.get("take_profits") or []
                    conf = p.get("signal_confidence", "?")
                    ch = (p.get("channel_name") or "")[:18]
                    realized = p.get("realized_pnl") or 0
                    entry_ts = p.get("entry_timestamp") or 0
                    hold = _fmt_duration(time.time() - entry_ts) if entry_ts else "?"
                    now_price = _signal_price(sym)
                    marks = []
                    for i, tp in enumerate(tps):
                        hit = p.get(f"tp{i+1}_hit") if i < 3 else None
                        if hit:
                            marks.append(f"✅{_fmt_price(tp)}")
                        elif now_price and tp and now_price >= tp:
                            marks.append(f"🎯{_fmt_price(tp)}")
                        else:
                            marks.append(f"⬜{_fmt_price(tp)}")
                    tp_str = " ".join(marks) if marks else "N/A"
                    now_str = ""
                    if now_price and entry:
                        now_str = f" | Now {_fmt_price(now_price)} ({(now_price-entry)/entry*100:+.1f}%)"
                    lines.append(f"<b>{sym}</b> [{conf}] {ch}")
                    lines.append(f"  Entry {_fmt_price(entry)}{now_str} | SL {_fmt_price(p.get('stop_loss'))}")
                    lines.append(f"  TPs {tp_str}")
                    lines.append(f"  Held {hold} | realized ${realized:+.2f}")
            lines.append("•••")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

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

    # ── Futures Commands ──────────────────────────────────────────────
    # Both engines now run in the single signal-listener container. The futures
    # engine writes its state to a namespaced file (data/signal_positions_futures.json)
    # so it doesn't collide with the spot engine's data/signal_positions.json.

    def _cmd_futures_status(self, update, context):
        try:
            logger.info("Telegram /futures_status received")
            pos = {}
            try:
                with open("data/signal_positions_futures.json") as f:
                    pos = json.load(f)
            except Exception:
                pass
            open_pos = {k: v for k, v in pos.items() if not v.get("is_closed")}
            realized_total = sum((p.get("realized_pnl") or 0) for p in pos.values())
            n_closed = sum(1 for p in pos.values() if p.get("is_closed"))

            lines = ["📈 <b>FUTURES ENGINE</b>", "•••"]
            lines.append(f"Open: {len(open_pos)} | Closed: {n_closed} | Realized P&L: ${realized_total:+.2f}")
            if not open_pos:
                lines.append("No open positions.")
            else:
                lines.append("•••")
                for sym, p in list(open_pos.items())[:8]:
                    entry = p.get("entry_price") or 0
                    tps = p.get("take_profits") or []
                    conf = p.get("signal_confidence", "?")
                    ch = (p.get("channel_name") or "")[:18]
                    realized = p.get("realized_pnl") or 0
                    entry_ts = p.get("entry_timestamp") or 0
                    hold = _fmt_duration(time.time() - entry_ts) if entry_ts else "?"
                    now_price = _signal_price(sym)
                    marks = []
                    for i, tp in enumerate(tps):
                        hit = p.get(f"tp{i+1}_hit") if i < 3 else None
                        if hit:
                            marks.append(f"✅{_fmt_price(tp)}")
                        elif now_price and tp and now_price >= tp:
                            marks.append(f"🎯{_fmt_price(tp)}")
                        else:
                            marks.append(f"⬜{_fmt_price(tp)}")
                    tp_str = " ".join(marks) if marks else "N/A"
                    now_str = ""
                    if now_price and entry:
                        now_str = f" | Now {_fmt_price(now_price)} ({(now_price-entry)/entry*100:+.1f}%)"
                    lines.append(f"<b>{sym}</b> [{conf}] {ch}")
                    lines.append(f"  Entry {_fmt_price(entry)}{now_str} | SL {_fmt_price(p.get('stop_loss'))}")
                    lines.append(f"  TPs {tp_str}")
                    lines.append(f"  Held {hold} | realized ${realized:+.2f}")
            lines.append("•••")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_futures_pnl(self, update, context):
        try:
            logger.info("Telegram /futures_pnl received")
            pos = {}
            try:
                with open("data/signal_positions_futures.json") as f:
                    pos = json.load(f)
            except Exception:
                pass
            closed = [p for p in pos.values() if p.get("is_closed")]
            realized_total = sum((p.get("realized_pnl") or 0) for p in closed)
            wins = [p for p in closed if (p.get("realized_pnl") or 0) > 0]
            win_rate = (len(wins) / len(closed) * 100) if closed else 0.0

            lines = ["📊 <b>FUTURES P&L</b>", "•••"]
            lines.append(f"Closed trades: {len(closed)}")
            lines.append(f"Realized P&L: ${realized_total:+.2f}")
            lines.append(f"Win rate: {win_rate:.0f}% ({len(wins)}/{len(closed)})")
            if closed:
                lines.append("•••")
                lines.append("📋 <b>Recent (last 8):</b>")
                recent = sorted(
                    closed,
                    key=lambda p: p.get("exit_timestamp") or p.get("entry_timestamp") or 0,
                    reverse=True,
                )[:8]
                for p in recent:
                    sym = p.get("symbol") or "?"
                    pnl = p.get("realized_pnl") or 0
                    reason = (p.get("exit_reason") or "")[:18]
                    lines.append(f"  {sym}: ${pnl:+.2f} ({reason})")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

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

    def _cmd_signal_inject(self, update, context=None):
        engine = getattr(self.strategy, '_signal_engine', None)
        if engine is None:
            update.message.reply_text("Signal engine not configured.")
            return
        text = getattr(update.message, 'text', '')
        # Get everything after /signal_inject
        signal_text = text.split(None, 1)
        if len(signal_text) < 2:
            update.message.reply_text("Usage: /signal_inject <signal message text>")
            return
        signal_text = signal_text[1].strip()
        if not signal_text:
            update.message.reply_text("Usage: /signal_inject <signal message text>")
            return

        # Resolve the signal connector defensively. The hybrid RunnerProxy's
        # __getattr__ returns {} for legacy Hummingbot attributes (connectors /
        # signal_exchange), so a naive `connectors.get(signal_exchange)` becomes
        # `{}.get({})` → "unhashable type: 'dict'" and the whole command crashes
        # before inject runs. When no real connector is wired, pass None — the
        # signal engine then uses its _get_price_fn / Gate.io REST fallback for
        # price, max_capital_usdt for equity, and the _buy_fn callback to place
        # the order (none of which need this connector object).
        connector = None
        try:
            conns = getattr(self.strategy, 'connectors', None)
            exch = getattr(self.strategy, 'signal_exchange', None)
            if isinstance(conns, dict) and isinstance(exch, str):
                connector = conns.get(exch)
        except Exception:
            connector = None
        try:
            signal = engine.inject_signal(signal_text, connector)
            reply = (
                f"📡 <b>Signal Injected</b>\n"
                f"Action: {signal.action.value}\n"
                f"Pair: {signal.pair or 'N/A'}\n"
                f"Entry: {signal.entry_low} - {signal.entry_high}\n"
                f"SL: {signal.stop_loss}\n"
                f"TPs: {signal.take_profits}\n"
                f"Quality: {signal.quality_score}/10 — {signal.quality_reason[:100]}\n"
                f"Reasoning: {signal.parse_reasoning[:200]}"
            )
            update.message.reply_text(reply, parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Signal inject failed: {e}")

    def _cmd_signal_history(self, update, context):
        try:
            logger.info("Telegram /signal_history received")
            pos = {}
            try:
                with open("data/signal_positions.json") as f:
                    pos = json.load(f)
            except Exception:
                pass
            closed = [p for p in pos.values() if p.get("is_closed")]
            closed.sort(key=lambda p: (p.get("entry_timestamp") or 0), reverse=True)
            closed = closed[:10]
            if not closed:
                update.message.reply_text("📨 No closed signal trades yet.")
                return
            lines = ["📨 <b>Signal History (last 10 closed)</b>", "•••"]
            for p in closed:
                sym = p.get("symbol", "?")
                entry = p.get("entry_price") or 0
                conf = p.get("signal_confidence", "?")
                rpnl = p.get("realized_pnl") or 0
                reason = p.get("exit_reason") or "?"
                entry_ts = p.get("entry_timestamp") or 0
                when = datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%m-%d %H:%M") if entry_ts else "?"
                sign = "+" if rpnl >= 0 else ""
                lines.append(f"<b>{sym}</b> [{conf}] {_fmt_price(entry)} → {reason} · {sign}${rpnl:.2f}")
                lines.append(f"  opened {when} UTC")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_capital(self, update, context):
        try:
            logger.info("Telegram /capital received")
            import urllib.request, json
            url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/capital"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=8) as resp:
                d = json.loads(resp.read().decode())
            te = float(d.get("total_equity") or 0)
            usdt = float(d.get("usdt_balance") or 0)
            locked = float(d.get("locked_in_positions") or 0)
            reserve = float(d.get("reserve") or 0)
            free = float(d.get("free_capital") or 0)
            pct = float(d.get("reserve_limit_pct") or 0)
            sc = d.get("deployed_capital") or {}
            lines = ["💰 <b>Capital</b>", "•••"]
            lines.append(f"Total equity: <b>${te:,.2f}</b>")
            lines.append(f"USDT: ${usdt:,.2f} | Locked in positions: ${locked:,.2f}")
            lines.append(f"Reserve ({pct:.0f}%): ${reserve:,.2f}")
            lines.append(f"<b>Free capital: ${free:,.2f}</b>")
            if sc:
                lines.append("•••")
                lines.append("Deployed capital:")
                for name, amt in sc.items():
                    lines.append(f"  {name}: ${float(amt):,.2f}")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_signal_channels(self, update, context):
        try:
            logger.info("Telegram /signal_channels received")
            import yaml
            from src.signals.signal_journal import SignalJournal

            channel_ids_str = os.environ.get("SIGNAL_CHANNEL_IDS", "")
            channel_count = len([c for c in channel_ids_str.split(",") if c.strip()])

            # Audit mode from config (the engine isn't reachable from the handler
            # in the hybrid runner — read it from strategy.yaml directly).
            audit = False
            try:
                with open("config/strategy.yaml") as f:
                    cfg = yaml.safe_load(f) or {}
                audit = bool((cfg.get("signal_copy") or {}).get("audit_mode", False))
            except Exception:
                pass
            mode_tag = "AUDIT" if audit else "LIVE"

            stats = SignalJournal().channel_stats()
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
                    lines.append(f"📋 <b>{s['channel']}</b>")
                    lines.append(f"  Messages: {s['messages']} | Signals: {s['signals']} | Noise: {s['not_signal']}")
                    lines.append(f"  Approved: {s['trades_approved']} | Rejected: {s['trades_rejected']} | P&L: ${s['trades_pnl'] or 0:.2f}")
            else:
                lines.append("•••")
                lines.append("No messages received yet.")

            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_mean_status(self, update, context):
        """Show mean-reversion strategy status from Rust engine API."""
        try:
            logger.info("Telegram /mean_status received")

            # Try to fetch from Rust engine API
            try:
                import urllib.request
                import json
                req = urllib.request.Request(os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/strategies")
                resp = urllib.request.urlopen(req, timeout=5)
                data = json.loads(resp.read())

                # Filter for mean-reversion strategies
                mean_strategies = [s for s in data if s.get("name") == "mean_reversion"]

                if mean_strategies:
                    lines = ["📉 <b>Mean-Reversion Engine</b>", "•••"]
                    total_pnl = 0.0

                    for status in mean_strategies:
                        symbol = status.get("pair", "UNKNOWN")
                        state = status.get("state", "UNKNOWN")
                        pnl = status.get("pnl", 0)
                        details = status.get("details", "")
                        total_pnl += pnl

                        pair_display = symbol.replace("-", "/")
                        sign = "+" if pnl >= 0 else ""
                        lines.append(f"<b>{pair_display}:</b> {state}")
                        if details:
                            lines.append(f"  <i>{html.escape(details)}</i>")
                        lines.append(f"  P&L: {sign}${pnl:.2f}")

                    lines.append("•••")
                    sign = "+" if total_pnl >= 0 else ""
                    lines.append(f"<b>Total P&L: {sign}${total_pnl:.2f}</b>")

                    update.message.reply_text("\n".join(lines), parse_mode="HTML")
                    return
            except Exception as e:
                logger.debug(f"Rust engine API unavailable: {e}")

            # Fallback message with known config
            update.message.reply_text(
                "📉 <b>Mean-Reversion Engine</b>\n"
                "•••\n"
                "Status: Scanning for flash dips (≥2% in 30s)\n"
                "Config: TP +2% | SL -3% | Gates: relaxed\n"
                "Waiting for flush events (~1 every 10 days on average)",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in /mean_status: {e}")
            update.message.reply_text(f"⚠️ Error getting mean-reversion status: {e}")

    def _cmd_trades(self, update, context):
        """Show recent individual trades across all bots from trades.db."""
        try:
            import sqlite3
            logger.info("Telegram /trades received")
            conn = sqlite3.connect("data/trades.db")
            # ORDER BY timestamp (trade time), NOT id — backfill re-inserts old
            # trades with fresh high IDs every restart, so id-ordering surfaces
            # stale backfilled trades as "most recent". Filter qty=0/pnl=0 rows,
            # which are paper-engine artifacts, not real trades.
            rows = conn.execute(
                "SELECT timestamp, engine, pair, pnl, exit_reason FROM trades "
                "WHERE NOT (pnl = 0 AND COALESCE(quantity, 0) = 0) "
                "ORDER BY timestamp DESC LIMIT 15"
            ).fetchall()
            conn.close()
            if not rows:
                update.message.reply_text("No trades yet.")
                return
            lines = ["📜 <b>Recent Trades</b> (all engines)", "•••"]
            for ts, engine, pair, pnl, reason in rows:
                # Date + time (MM-DD HH:MM) so old vs recent is visible.
                when = f"{ts[5:10]} {ts[11:16]}" if len(ts) >= 16 else ts
                sign = "+" if pnl >= 0 else ""
                emoji = "🟢" if pnl >= 0 else "🔴"
                p = pair.replace("-USDT", "").replace("-USD", "")
                lines.append(f"{when}  {engine:<6} {p:<8} {reason:<12} {emoji} {sign}${pnl:.2f}")
            lines.append("•••")
            lines.append(f"<i>{len(rows)} most recent</i>")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")

    def _rust_strategy_status(self, update, engine_name, title, emoji):
        """Generic Rust-API strategy status. Calls /api/v1/strategies, filters by name."""
        try:
            logger.info(f"Telegram /{engine_name}_status received")
            strategies = self._rust_api("/api/v1/strategies") or []
            matching = [s for s in strategies if s.get("name") == engine_name]
            if not matching:
                update.message.reply_text(f"{title} not active or unavailable.")
                return
            import html as _html
            lines = [f"{emoji} <b>{title.upper()}</b>", "•••"]
            total_pnl = 0.0
            for s in matching:
                pair = s.get("pair", "?").replace("-", "/")
                state = s.get("state", "?")
                pnl = s.get("pnl", 0)
                details = s.get("details", "")
                total_pnl += pnl
                lines.append(f"<b>{pair}:</b> {state}")
                if details:
                    lines.append(f"  <i>{_html.escape(details)}</i>")
                lines.append(f"  P&L: {'+' if pnl >= 0 else ''}${pnl:.2f}")
            lines.append("•••")
            lines.append(f"<b>Total P&L: {'+' if total_pnl >= 0 else ''}${total_pnl:.2f}</b>")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /{engine_name}_status: {e}")
            update.message.reply_text(f"⚠️ Error: {e}")

    def _cmd_swing_status(self, update, context):
        """Show swing strategy status from Rust engine API."""
        try:
            logger.info("Telegram /swing_status received")

            try:
                import urllib.request
                import json
                req = urllib.request.Request(os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/strategies")
                resp = urllib.request.urlopen(req, timeout=5)
                data = json.loads(resp.read())

                swing_strategies = [s for s in data if s.get("name") == "swing"]

                if swing_strategies:
                    lines = ["🎣 <b>Swing Engine</b>", "•••"]
                    total_pnl = 0.0

                    for status in swing_strategies:
                        symbol = status.get("pair", "UNKNOWN")
                        state = status.get("state", "UNKNOWN")
                        pnl = status.get("pnl", 0)
                        details = status.get("details", "")
                        total_pnl += pnl

                        pair_display = symbol.replace("-", "/")
                        sign = "+" if pnl >= 0 else ""
                        lines.append(f"<b>{pair_display}:</b> {state}")
                        if details:
                            lines.append(f"  <i>{html.escape(details)}</i>")
                        lines.append(f"  P&L: {sign}${pnl:.2f}")

                    lines.append("•••")
                    sign = "+" if total_pnl >= 0 else ""
                    lines.append(f"<b>Total P&L: {sign}${total_pnl:.2f}</b>")

                    update.message.reply_text("\n".join(lines), parse_mode="HTML")
                    return
            except Exception as e:
                logger.debug(f"Rust engine API unavailable: {e}")

            update.message.reply_text("Swing Engine not active or unavailable.", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /swing_status: {e}")
            update.message.reply_text(f"⚠️ Error getting swing status: {e}")
