"""
signal_engine.py — Orchestrator for the Signal Copy Trading Engine.

Receives messages from ChannelListener, parses via GLM, validates,
and executes. Supports audit mode (paper trade) for measuring signal quality.
"""

import logging
import os
import time
import json
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Callable

from .channel_listener import ChannelListener
from .signal_parser import SignalParser, ParsedSignal, SignalAction, SignalConfidence
from .signal_validator import SignalValidator
from .signal_risk import SignalRiskGuard
from .signal_position import SignalPositionManager, SignalPosition
from .signal_journal import SignalJournal, SignalTrade

logger = logging.getLogger(__name__)


class SignalEngineState(Enum):
    LISTENING = "LISTENING"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


class SignalEngine:
    def __init__(self, config: dict, btc_regime_fn: Callable,
                 telegram_send_fn: Optional[Callable] = None,
                 buy_fn: Optional[Callable] = None,
                 sell_fn: Optional[Callable] = None,
                 get_price_fn: Optional[Callable] = None):
        self._config = config
        self._get_btc_regime = btc_regime_fn
        self._telegram_send = telegram_send_fn
        self._buy_fn = buy_fn
        self._sell_fn = sell_fn
        self._get_price_fn = get_price_fn

        self._enabled = config.get("enabled", False)
        self._audit_mode = config.get("audit_mode", True)
        self._manual_pause = False
        # Per-key notification cooldown: stops a persistent condition (broken buy
        # path, active risk-guard cooldown, saturated positions) from alerting on
        # every incoming signal. Trade events (entry/TP/close) bypass this.
        self._notify_cooldowns: dict[str, float] = {}
        self._notify_cooldown_seconds = float(config.get("notify_cooldown_seconds", 1800))
        # Persisted message_id dedup: a restart must not re-execute old channel
        # signals (the listener's queue file replays consumed messages on restart).
        self._seen_signal_ids: set[int] = set()
        self._seen_signal_ids_path = "data/seen_signal_ids.json"
        self._seen_signal_ids_max = 2000
        self._load_seen_signal_ids()
        self._state = SignalEngineState.LISTENING

        # Sub-components
        channel_ids = [int(c.strip()) for c in
                       os.environ.get("SIGNAL_CHANNEL_IDS", "").split(",") if c.strip()]
        self._listener = ChannelListener(
            api_id=int(os.environ.get("TELEGRAM_API_ID", "0")),
            api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
            channel_ids=channel_ids,
            session_name=config.get("session_name", "signal_listener"),
        )
        self._parser = SignalParser(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            model=config.get("ai_model", "deepseek-chat"),
        )
        self._validator = SignalValidator(config)
        self._risk = SignalRiskGuard(config)
        self._position_mgr = SignalPositionManager(config)
        self._journal = SignalJournal()

        # Fetch available Gate.io pairs on init
        self._available_pairs: set[str] = set()
        self._last_pair_refresh = 0
        self._refresh_available_pairs()  # Fetch immediately on startup

        logger.info(f"Signal Engine initialized: "
                     f"enabled={self._enabled}, audit={self._audit_mode}, "
                     f"channels={len(channel_ids)}")

    @property
    def state(self) -> SignalEngineState:
        if not self._enabled:
            return SignalEngineState.DISABLED
        if self._manual_pause:
            return SignalEngineState.PAUSED
        return self._state

    def start_listener(self):
        """Start Telethon listener in background thread."""
        if self._enabled:
            self._listener.start()

    def stop_listener(self):
        self._listener.stop()

    def tick(self, connector=None):
        """Called from on_tick(). Processes queued messages and manages positions."""
        if not self._enabled or self._manual_pause:
            self._write_status()
            return

        # Refresh available pairs every hour
        if time.time() - self._last_pair_refresh > 3600:
            self._refresh_available_pairs()

        # Process queued messages
        while True:
            msg = self._listener.get_message()
            if msg is None:
                break
            self._process_message(msg, connector)

        # Manage open positions
        if connector:
            self._manage_positions(connector)

        # Write status to shared file for Rust engine to read
        self._write_status()

    def get_status(self) -> dict:
        # Sync with Rust before reporting — catches positions closed by Rust engine
        self._sync_closed_from_rust()
        risk_status = self._risk.get_status()
        positions = self._position_mgr.get_open_positions()
        return {
            "state": self.state.value,
            "audit_mode": self._audit_mode,
            "open_positions": len(positions),
            "risk": risk_status,
        }

    def _sync_closed_from_rust(self):
        """Mark Python positions as closed if Rust engine has closed them."""
        try:
            with open("data/signal_positions.json", "r") as f:
                rust_data = json.load(f)
            rust_open = {sym for sym, p in rust_data.items() if not p.get("is_closed", False)}
            for pos in self._position_mgr.get_open_positions():
                if pos.symbol not in rust_open:
                    logger.info(f"Syncing closed position from Rust: {pos.symbol}")
                    self._position_mgr.close_position(pos.symbol, pos.entry_price, "rust_sync")
        except Exception:
            pass  # File may not exist yet

    def _write_status(self):
        """Write current status to shared JSON file for Rust engine to read."""
        try:
            self._sync_closed_from_rust()
            positions = self._position_mgr.get_open_positions()
            risk_status = self._risk.get_status()

            # Merge latest state from Rust-managed signal_positions.json
            # (Rust tracks TP/SL hits in real-time; Python may have stale in-memory state)
            rust_positions = {}
            try:
                with open("data/signal_positions.json", "r") as f:
                    rust_data = json.load(f)
                    for sym, pdata in rust_data.items():
                        if not pdata.get("is_closed", False):
                            rust_positions[sym] = pdata
            except Exception:
                pass

            pos_list = []
            for p in positions:
                # Use Rust's TP/SL state if available (more up-to-date)
                rp = rust_positions.get(p.symbol, {})
                pos_list.append({
                    "symbol": p.symbol,
                    "entry_price": p.entry_price,
                    "amount": p.amount,
                    "stop_loss": rp.get("stop_loss", p.stop_loss),
                    "take_profits": p.take_profits,
                    "channel_name": getattr(p, 'channel_name', ''),
                    "entry_time": str(getattr(p, 'entry_time', '')),
                    "tp1_hit": rp.get("tp1_hit", p.tp1_hit),
                    "tp2_hit": rp.get("tp2_hit", p.tp2_hit),
                    "tp3_hit": rp.get("tp3_hit", p.tp3_hit),
                })
            status = {
                "state": self.state.value,
                "audit_mode": self._audit_mode,
                "open_positions": len(positions),
                "positions": pos_list,
                "trades_today": risk_status.get("trades_today", 0),
                "max_trades": risk_status.get("max_trades", 10),
                "daily_pnl": risk_status.get("daily_pnl", 0.0),
                "halted": risk_status.get("halted", False),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open("data/signal_status.json", "w") as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to write signal status: {e}")

    def pause(self):
        self._manual_pause = True
        logger.info("Signal engine paused")

    def resume(self):
        self._manual_pause = False
        logger.info("Signal engine resumed")

    def inject_signal(self, text: str, connector) -> ParsedSignal:
        """Manually inject a signal message. Returns parsed signal."""
        signal = self._parser.parse(text)
        msg = {"text": text, "channel_name": "manual_inject", "channel_id": 0, "message_id": 0}
        self._process_message(msg, connector)
        return signal

    def manual_close(self, symbol: str) -> Optional[str]:
        """Manually close a signal position. Returns reason or None."""
        pos = self._position_mgr.get_position(symbol)
        if not pos:
            return None
        pos.is_closed = True
        pos.exit_reason = "manual"
        return "manual"

    def _process_message(self, msg: dict, connector):
        """Full pipeline: parse → validate → execute."""
        text = msg["text"]
        channel_name = msg.get("channel_name", "unknown")

        # Skip already-processed signals (survives restarts via the persisted set;
        # edits share the original message_id, so always let them through).
        msg_id = msg.get("message_id", 0)
        if msg_id and not text.startswith("[EDIT]"):
            if msg_id in self._seen_signal_ids:
                logger.debug(f"Skipping already-processed signal (msg {msg_id})")
                return
            self._seen_signal_ids.add(msg_id)
            self._save_seen_signal_ids()

        # Parse with GLM
        signal = self._parser.parse(text)
        if signal.action == SignalAction.NOT_A_SIGNAL:
            logger.debug(f"[{channel_name}] Not a signal: {signal.parse_reasoning[:60]}")
        else:
            logger.info(f"[{channel_name}] {signal.action.value} {signal.pair or '?'} "
                        f"| entry={signal.entry_low}-{signal.entry_high} "
                        f"| SL={signal.stop_loss} | TPs={signal.take_profits} "
                        f"| score={signal.quality_score}/10")

        # Log raw message for audit
        self._journal.log_raw_message(
            channel_id=msg.get("channel_id", 0),
            channel_name=channel_name,
            message_id=msg.get("message_id", 0),
            text=text,
            parsed_action=signal.action.value,
            parsed_pair=signal.pair or "",
            parse_reasoning=signal.parse_reasoning,
            quality_score=signal.quality_score,
            quality_reason=signal.quality_reason,
        )

        if signal.action == SignalAction.NOT_A_SIGNAL:
            return

        # Handle CLOSE signals
        if signal.action == SignalAction.CLOSE:
            self._handle_close(signal, channel_name)
            return

        # Handle UPDATE signals
        if signal.action == SignalAction.UPDATE_SL:
            if signal.pair and signal.stop_loss:
                self._position_mgr.update_stop_loss(signal.pair, signal.stop_loss)
                logger.info(f"Signal SL updated: {signal.pair} → ${signal.stop_loss:,.2f}")
            return

        if signal.action != SignalAction.OPEN_LONG:
            return

        # Fill missing stop-loss using ATR-based default
        if signal.stop_loss is None:
            self._fill_default_sl(signal, connector)

        # Validate
        valid, reason = self._validator.validate(signal)
        if not valid:
            logger.info(f"Signal rejected ({channel_name}): {reason}")
            self._notify_dedupe(f"rejected:{signal.pair}", f"🚫 Signal rejected: {signal.pair} — {reason}")
            self._log_audit_trade(signal, channel_name, "rejected", 0, reason)
            return

        # BTC correlation gate
        btc_regime, _, _ = self._get_btc_regime()
        if btc_regime == "DANGER" and self._config.get("use_btc_correlation_gate", True):
            logger.info(f"Signal blocked by BTC DANGER: {signal.pair}")
            self._notify_dedupe("blocked_btc", f"🚫 Signal blocked (BTC DANGER): {signal.pair}")
            self._log_audit_trade(signal, channel_name, "blocked_btc", 0, "btc_danger")
            return

        # Risk checks
        if not self._risk.can_trade():
            logger.info("Signal blocked by risk guard")
            self._notify_dedupe(f"blocked_risk:{signal.pair}", f"🚫 Signal blocked (risk guard): {signal.pair}")
            self._log_audit_trade(signal, channel_name, "blocked_risk", 0, "risk_limit")
            return

        # Execute (or simulate in audit mode)
        if self._audit_mode:
            self._simulate_entry(signal, channel_name)
        else:
            self._execute_entry(signal, channel_name, connector)

    def _simulate_entry(self, signal: ParsedSignal, channel_name: str):
        """Paper trade: log the signal as if we entered."""
        entry = signal.entry_high or signal.entry_low or 0
        if entry <= 0:
            return

        self._position_mgr.open_position(
            symbol=signal.pair,
            entry_price=entry,
            amount=100,  # Simulated amount
            stop_loss=signal.stop_loss or entry * 0.95,
            take_profits=signal.take_profits,
            signal_confidence=signal.confidence.value,
            raw_message=signal.raw_message,
            channel_name=channel_name,
        )
        self._risk.record_trade_opened()

        self._log_audit_trade(signal, channel_name, "OPEN_LONG", entry, "audit_entry")
        self._notify(
            f"[AUDIT] Signal entered: {signal.pair}\n"
            f"Entry: ${entry:,.2f}\n"
            f"SL: ${signal.stop_loss:,.2f}\n"
            f"TPs: {', '.join(f'${tp:,.2f}' for tp in signal.take_profits)}\n"
            f"Channel: {channel_name}"
        )
        logger.info(f"[AUDIT] Signal entered: {signal.pair} @ ${entry:,.2f} from {channel_name}")

    def _execute_entry(self, signal: ParsedSignal, channel_name: str, connector):
        """Place a real spot buy order via the execution adapter (MARKET).

        Order of operations is deliberate: validate duplicate / max-positions
        BEFORE placing any order, so a rejected signal never leaves an untracked
        exchange position. Entry is MARKET at the live fill price, so a non-fill
        can never create a phantom tracked position.
        """
        entry = signal.entry_high or signal.entry_low or 0
        if entry <= 0:
            logger.warning(f"Signal has no valid entry price: {signal.pair}")
            self._notify_dedupe(f"no_entry:{signal.pair}", f"🚫 Signal rejected (no entry price): {signal.pair}")
            return

        # Pre-flight guards — abort before any order is placed
        if self._position_mgr.has_open_position(signal.pair):
            logger.warning(f"Signal skipped — position already open for {signal.pair}")
            self._log_audit_trade(signal, channel_name, "skipped_duplicate", entry, "duplicate_symbol")
            self._notify_dedupe(f"skipped_duplicate:{signal.pair}", f"🚫 Signal skipped (already open): {signal.pair}")
            return
        if len(self._position_mgr.get_open_positions()) >= self._position_mgr.max_positions:
            logger.warning(f"Signal skipped — max positions ({self._position_mgr.max_positions}) reached")
            self._log_audit_trade(signal, channel_name, "skipped_max_positions", entry, "max_positions")
            self._notify_dedupe("skipped_max_positions", f"🚫 Signal skipped (max positions reached): {signal.pair}")
            return

        # Entry-zone gate: only enter while live price is inside the signal's
        # [entry_low, entry_high] zone. Buying above the zone (a stale signal can
        # already be above tp1) means Rust's TP logic instantly "hits" tp1 at a
        # loss and closes the position on arrival.
        entry_low = signal.entry_low or signal.entry_high or entry
        entry_high = signal.entry_high or signal.entry_low or entry
        current_price = self._get_current_price(connector, signal.pair)
        if current_price <= 0:
            current_price = entry_high  # price feed unavailable — assume zone top
        if current_price > entry_high:
            logger.info(f"Signal skipped — above entry zone: {signal.pair} "
                        f"zone={entry_low}-{entry_high} now={current_price}")
            self._notify_dedupe(f"above_zone:{signal.pair}",
                                f"🚫 Signal skipped (above entry zone): {signal.pair} "
                                f"— zone ${entry_low}-{entry_high}, now ${current_price}")
            self._log_audit_trade(signal, channel_name, "skipped_above_zone", current_price, "above_entry_zone")
            return
        if current_price < entry_low:
            logger.info(f"Signal skipped — below entry zone: {signal.pair} "
                        f"zone={entry_low}-{entry_high} now={current_price}")
            self._notify_dedupe(f"below_zone:{signal.pair}",
                                f"🚫 Signal skipped (below entry zone): {signal.pair} "
                                f"— zone ${entry_low}-{entry_high}, now ${current_price}")
            self._log_audit_trade(signal, channel_name, "skipped_below_zone", current_price, "below_entry_zone")
            return

        fill_price = current_price
        equity = self._get_equity(connector)
        logger.info(f"Signal execution: pair={signal.pair} entry_zone={entry_low}-{entry_high} "
                     f"fill_price={fill_price} equity={equity}")

        # Calculate position size from risk guard
        usdt_amount = self._risk.get_budget_for_trade(signal, equity)
        if usdt_amount <= 0:
            logger.warning(f"Signal budget is 0 for {signal.pair} (equity={equity})")
            self._notify_dedupe(f"no_budget:{signal.pair}", f"🚫 Signal skipped (no budget): {signal.pair} (equity=${equity:.0f})")
            return

        if fill_price <= 0:
            return

        amount = round(usdt_amount / fill_price, 6)
        if amount <= 0:
            return

        # Place MARKET buy via strategy callback
        order_id = None
        if self._buy_fn:
            try:
                order_id = self._buy_fn(
                    symbol=signal.pair,
                    amount=Decimal(str(amount)),
                    price=Decimal(str(fill_price)),
                    order_type="MARKET",
                )
            except Exception as e:
                logger.error(f"Signal buy failed for {signal.pair}: {e}")
                self._notify_dedupe(f"buy_error:{signal.pair}", f"🚫 Signal entry FAILED: {signal.pair} — {e}")
                return

        if order_id:
            self._position_mgr.open_position(
                symbol=signal.pair,
                entry_price=fill_price,
                amount=amount,
                stop_loss=signal.stop_loss or fill_price * 0.95,
                take_profits=signal.take_profits,
                signal_confidence=signal.confidence.value,
                raw_message=signal.raw_message,
                channel_name=channel_name,
            )
            self._risk.record_trade_opened()
            self._log_audit_trade(signal, channel_name, "OPEN_LONG", fill_price, "live_entry")
            self._notify(
                f"[LIVE] Signal entered: {signal.pair}\n"
                f"Entry: ${fill_price:,.2f}\n"
                f"Amount: {amount:.6f} (${usdt_amount:.2f})\n"
                f"SL: ${signal.stop_loss:,.2f}\n"
                f"TPs: {', '.join(f'${tp:,.2f}' for tp in signal.take_profits)}\n"
                f"Confidence: {signal.confidence.value}\n"
                f"Channel: {channel_name}"
            )
            logger.info(f"[LIVE] Signal entered: {signal.pair} @ ${fill_price:,.2f} "
                        f"amount={amount:.6f} order={order_id}")
        else:
            logger.warning(f"Signal buy returned no order ID for {signal.pair}")
            self._log_audit_trade(signal, channel_name, "buy_failed", fill_price, "no_order_id")
            self._notify_dedupe(f"no_order_id:{signal.pair}", f"🚫 Signal entry failed (no order ID): {signal.pair}")

    def _manage_positions(self, connector):
        """Check all signal positions for SL/TP hits."""
        for pos in self._position_mgr.get_open_positions():
            current_price = self._get_current_price(connector, pos.symbol)
            if current_price <= 0:
                continue

            # Stop-loss check
            if current_price <= pos.stop_loss:
                if not self._audit_mode:
                    self._execute_close(pos, current_price, "stop_loss")
                pnl = self._position_mgr.close_position(pos.symbol, current_price, "stop_loss")
                self._record_close(pos, current_price, "stop_loss", pnl)
                continue

            # TP1 hit
            if not pos.tp1_hit and len(pos.take_profits) >= 1 and current_price >= pos.take_profits[0]:
                pos.tp1_hit = True
                tp1_slice = pos.remaining_amount * pos.tp1_close_pct
                if not self._audit_mode:
                    self._execute_close(pos, pos.take_profits[0], "tp1", amount=tp1_slice)
                _, tp1_pnl = self._position_mgr.partial_close(pos.symbol, pos.tp1_close_pct, pos.take_profits[0], "tp1")
                self._risk.record_trade_closed(tp1_pnl)
                self._position_mgr.update_stop_loss(pos.symbol, pos.entry_price)
                self._notify(f"[{'AUDIT' if self._audit_mode else 'LIVE'}] TP1 hit: {pos.symbol} @ ${pos.take_profits[0]:,.2f}, SL → breakeven")
                self._log_position_trade(pos, pos.take_profits[0], "tp1")

            # TP2 hit
            if not pos.tp2_hit and len(pos.take_profits) >= 2 and current_price >= pos.take_profits[1]:
                pos.tp2_hit = True
                tp2_slice = pos.remaining_amount * pos.tp2_close_pct
                if not self._audit_mode:
                    self._execute_close(pos, pos.take_profits[1], "tp2", amount=tp2_slice)
                _, tp2_pnl = self._position_mgr.partial_close(pos.symbol, pos.tp2_close_pct, pos.take_profits[1], "tp2")
                self._risk.record_trade_closed(tp2_pnl)
                self._position_mgr.update_stop_loss(pos.symbol, pos.take_profits[0])
                self._notify(f"[{'AUDIT' if self._audit_mode else 'LIVE'}] TP2 hit: {pos.symbol} @ ${pos.take_profits[1]:,.2f}")
                self._log_position_trade(pos, pos.take_profits[1], "tp2")

            # TP3 hit
            if not pos.tp3_hit and len(pos.take_profits) >= 3 and current_price >= pos.take_profits[2]:
                pos.tp3_hit = True
                if not self._audit_mode:
                    self._execute_close(pos, pos.take_profits[2], "tp3")
                pnl = self._position_mgr.close_position(pos.symbol, pos.take_profits[2], "tp3")
                self._record_close(pos, pos.take_profits[2], "tp3", pnl)

    def _handle_close(self, signal: ParsedSignal, channel_name: str):
        """Handle CLOSE signal from trader."""
        if not signal.pair:
            return
        pos = self._position_mgr.get_position(signal.pair)
        if not pos:
            return

        close_price = signal.entry_low or pos.entry_price
        if not self._audit_mode:
            self._execute_close(pos, close_price, "trader_close")

        pnl = self._position_mgr.close_position(signal.pair, close_price, "trader_close")
        self._record_close(pos, close_price, "trader_close", pnl)
        self._notify(f"Signal closed by trader: {signal.pair}")

    def _get_current_price(self, connector, symbol: str) -> float:
        """Get current price from connector, with Gate.io REST API fallback."""
        try:
            if self._get_price_fn:
                price = self._get_price_fn(symbol)
                if price > 0:
                    return price
            if connector is not None:
                price_obj = connector.get_mid_price(symbol)
                if price_obj:
                    return float(price_obj)
        except Exception:
            pass
        # Fallback: fetch from Gate.io REST API for any unregistered pair
        try:
            gate_pair = symbol.replace("-", "_")
            url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={gate_pair}"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if data and data[0].get("last"):
                    return float(data[0]["last"])
        except Exception as e:
            logger.debug(f"Gate.io price fallback failed for {symbol}: {e}")
        return 0

    def _fill_default_sl(self, signal: ParsedSignal, connector):
        """Fill missing stop-loss using ATR-based default.

        When a signal has no explicit SL, compute ATR from recent candles
        and set SL = entry - (ATR × multiplier) for LONG positions.
        Entry defaults to current market price if not specified.
        """
        if not signal.pair or signal.stop_loss is not None:
            return

        # Resolve entry price: use signal entry or current market price
        entry = signal.entry_high or signal.entry_low
        if not entry or entry <= 0:
            entry = self._get_current_price(connector, signal.pair)
        if entry <= 0:
            logger.warning(f"Cannot compute default SL for {signal.pair}: no entry/market price")
            return

        # Fetch recent candles from Gate.io to compute ATR
        atr = self._fetch_atr(signal.pair)
        if atr <= 0:
            logger.warning(f"Cannot compute default SL for {signal.pair}: ATR unavailable")
            return

        multiplier = self._config.get("default_sl_atr_multiplier", 2.0)
        sl_distance = atr * multiplier

        # Compute SL (below entry for LONG)
        default_sl = entry - sl_distance

        # Cap at max_sl_distance_pct
        max_sl_pct = self._config.get("max_sl_distance_pct", 10.0) / 100.0
        max_sl_distance = entry * max_sl_pct
        if sl_distance > max_sl_distance:
            default_sl = entry - max_sl_distance
            logger.info(f"Default SL capped at {max_sl_pct*100:.0f}% for {signal.pair}")

        # Round to reasonable precision
        if default_sl > 1:
            default_sl = round(default_sl, 2)
        else:
            default_sl = round(default_sl, 6)

        signal.stop_loss = default_sl

        # Also set entry if it was missing
        if not signal.entry_low and not signal.entry_high:
            signal.entry_low = entry
            signal.entry_high = entry
            signal.is_market_entry = True

        logger.info(f"Default SL set for {signal.pair}: ${default_sl:,.4f} "
                     f"(ATR={atr:.4f} × {multiplier}, entry=${entry:,.4f})")
        self._notify(
            f"📐 <b>Default SL</b>: {signal.pair}\n"
            f"Entry: ${entry:,.4f} (market)\n"
            f"SL: ${default_sl:,.4f} (ATR×{multiplier})\n"
            f"Distance: {(entry - default_sl) / entry * 100:.1f}%"
        )

    def _fetch_atr(self, pair: str) -> float:
        """Fetch recent candles from Gate.io and compute ATR(14)."""
        try:
            gate_pair = pair.replace("-", "_")
            url = f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={gate_pair}&interval=1h&limit=15"
            req = urllib.request.Request(url, headers={"User-Agent": "signal-engine"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if not data or len(data) < 14:
                return 0.0
            # Gate.io candlestick format: [timestamp, volume, close, high, low, amount]
            # Compute ATR(14) using simple average of true ranges
            true_ranges = []
            for i in range(1, len(data)):
                high = float(data[i][3])
                low = float(data[i][4])
                prev_close = float(data[i - 1][2])
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                true_ranges.append(tr)
            if not true_ranges:
                return 0.0
            # Use last 14 TRs for ATR
            atr = sum(true_ranges[-14:]) / len(true_ranges[-14:])
            return atr
        except Exception as e:
            logger.debug(f"ATR fetch failed for {pair}: {e}")
            return 0.0

    def _get_equity(self, connector) -> float:
        """Get total account equity from connector, with config fallback."""
        try:
            if connector is not None:
                bal = connector.get_balance("USDT")
                if bal and float(bal.total_balance) > 0:
                    return float(bal.total_balance)
        except Exception:
            pass
        # Fallback: use max_capital_usdt as total equity for position sizing
        fallback = self._config.get("max_capital_usdt", 1000)
        logger.info(f"Signal equity fallback: using ${fallback} (connector balance unavailable)")
        return float(fallback)

    def _execute_close(self, pos: SignalPosition, price: float, reason: str,
                       amount: Optional[float] = None):
        """Place a MARKET sell to close (part of) a signal position.

        MARKET guarantees the exit fills — a LIMIT sell at the stop/TP price can
        fail to fill during fast moves, leaving the position open while the
        tracker believes it's closed.

        amount: slice to sell; defaults to the full remaining position (used for
        full closes — stop_loss, tp3, trader_close). Partial TP closes pass only
        their slice so the exchange sell matches the booked accounting.
        """
        if not self._sell_fn or pos.remaining_amount <= 0:
            return

        sell_amount = amount if amount is not None else pos.remaining_amount
        try:
            amount_to_sell = round(sell_amount, 6)
            if amount_to_sell <= 0:
                return
            order_id = self._sell_fn(
                symbol=pos.symbol,
                amount=Decimal(str(amount_to_sell)),
                price=Decimal(str(price)),
                order_type="MARKET",
            )
            if order_id:
                logger.info(f"Signal close order placed: {pos.symbol} {amount_to_sell} "
                            f"@ ${price:,.2f} ({reason}, MARKET)")
        except Exception as e:
            logger.error(f"Signal close failed for {pos.symbol}: {e}")

    def _refresh_available_pairs(self):
        """Fetch available Gate.io USDT pairs."""
        try:
            url = "https://api.gateio.ws/api/v4/spot/tickers"
            req = urllib.request.Request(url, headers={"User-Agent": "signal-engine"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            self._available_pairs = {
                t["currency_pair"].replace("_", "-")
                for t in data
                if t["currency_pair"].endswith("_USDT")
            }
            self._validator.set_available_pairs(self._available_pairs)
            self._last_pair_refresh = time.time()
            logger.info(f"Refreshed {len(self._available_pairs)} Gate.io pairs")
        except Exception as e:
            logger.warning(f"Failed to refresh pairs: {e}")

    def _record_close(self, pos: SignalPosition, price: float, reason: str, pnl: Optional[float]):
        """Record a closed position in the journal."""
        if pnl is not None:
            self._risk.record_trade_closed(pnl)
        self._log_position_trade(pos, price, reason)
        self._notify(
            f"[{'AUDIT' if self._audit_mode else 'LIVE'}] Closed: {pos.symbol} "
            f"({reason}) @ ${price:,.2f}, PnL: ${pnl or 0:.2f}"
        )

    def _log_audit_trade(self, signal: ParsedSignal, channel_name: str,
                         action: str, price: float, reason: str):
        self._journal.log_trade(SignalTrade(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=signal.pair or "",
            channel_name=channel_name,
            action=action,
            entry_price=signal.entry_high or signal.entry_low or 0,
            current_price=price,
            quantity=0,
            realized_pnl=0,
            exit_reason=reason,
            signal_confidence=signal.confidence.value,
            stop_loss=signal.stop_loss or 0,
            take_profits=str(signal.take_profits),
            tp1_hit=0, tp2_hit=0, tp3_hit=0,
            raw_message=signal.raw_message[:500],
            parse_reasoning=signal.parse_reasoning,
            is_audit=1 if self._audit_mode else 0,
        ))

    def _log_position_trade(self, pos: SignalPosition, price: float, reason: str):
        self._journal.log_trade(SignalTrade(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=pos.symbol,
            channel_name=pos.channel_name,
            action=f"CLOSE_{reason}",
            entry_price=pos.entry_price,
            current_price=price,
            quantity=pos.amount,
            realized_pnl=pos.realized_pnl,
            exit_reason=reason,
            signal_confidence=pos.signal_confidence,
            stop_loss=pos.stop_loss,
            take_profits=str(pos.take_profits),
            tp1_hit=int(pos.tp1_hit),
            tp2_hit=int(pos.tp2_hit),
            tp3_hit=int(pos.tp3_hit),
            raw_message=pos.raw_message[:500],
            parse_reasoning="",
            is_audit=1 if self._audit_mode else 0,
        ))

    def _notify(self, message: str):
        """Send Telegram notification."""
        if self._telegram_send:
            try:
                self._telegram_send(message)
            except Exception as e:
                logger.error(f"Signal notify failed: {e}")

    def _notify_dedupe(self, key: str, message: str):
        """Send a notification, suppressing repeats of `key` within the cooldown.

        Prevents Telegram spam when a persistent condition would otherwise alert
        on every incoming signal (e.g. a failing buy path, an active risk-guard
        block, max-positions saturated). Time-sensitive trade events (entry / TP
        / close) use _notify directly so they always fire.
        """
        now = time.time()
        if now - self._notify_cooldowns.get(key, 0.0) < self._notify_cooldown_seconds:
            return
        self._notify_cooldowns[key] = now
        self._notify(message)

    def _load_seen_signal_ids(self):
        """Load persisted seen message_ids so restarts skip already-traded signals."""
        try:
            with open(self._seen_signal_ids_path) as f:
                ids = json.load(f)
            self._seen_signal_ids = {int(i) for i in ids if isinstance(i, (int, float))}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._seen_signal_ids = set()

    def _save_seen_signal_ids(self):
        """Persist the seen set (bounded) so dedup survives restarts."""
        try:
            if len(self._seen_signal_ids) > self._seen_signal_ids_max:
                self._seen_signal_ids = set(list(self._seen_signal_ids)[-self._seen_signal_ids_max:])
            with open(self._seen_signal_ids_path, "w") as f:
                json.dump(sorted(self._seen_signal_ids), f)
        except OSError as e:
            logger.warning(f"Could not persist seen signal ids: {e}")
