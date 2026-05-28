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

        # Fetch available Binance pairs on init
        self._available_pairs: set[str] = set()
        self._last_pair_refresh = 0

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

    def get_status(self) -> dict:
        risk_status = self._risk.get_status()
        positions = self._position_mgr.get_open_positions()
        return {
            "state": self.state.value,
            "audit_mode": self._audit_mode,
            "open_positions": len(positions),
            "risk": risk_status,
        }

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

        # Parse with GLM
        signal = self._parser.parse(text)
        logger.info(f"Signal parsed: action={signal.action.value}, pair={signal.pair}, "
                     f"reasoning={signal.parse_reasoning[:80]}")

        # Log raw message for audit
        self._journal.log_raw_message(
            channel_id=msg.get("channel_id", 0),
            channel_name=channel_name,
            message_id=msg.get("message_id", 0),
            text=text,
            parsed_action=signal.action.value,
            parsed_pair=signal.pair or "",
            parse_reasoning=signal.parse_reasoning,
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

        # Validate
        valid, reason = self._validator.validate(signal)
        if not valid:
            logger.info(f"Signal rejected ({channel_name}): {reason}")
            self._notify(f"Signal rejected: {signal.pair} — {reason}")
            self._log_audit_trade(signal, channel_name, "rejected", 0, reason)
            return

        # BTC correlation gate
        btc_regime, _, _ = self._get_btc_regime()
        if btc_regime == "DANGER" and self._config.get("use_btc_correlation_gate", True):
            logger.info(f"Signal blocked by BTC DANGER: {signal.pair}")
            self._notify(f"Signal blocked (BTC DANGER): {signal.pair}")
            self._log_audit_trade(signal, channel_name, "blocked_btc", 0, "btc_danger")
            return

        # Risk checks
        if not self._risk.can_trade():
            logger.info("Signal blocked by risk guard")
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
        """Place a real buy order via Hummingbot connector."""
        entry = signal.entry_high or signal.entry_low or 0
        if entry <= 0:
            logger.warning(f"Signal has no valid entry price: {signal.pair}")
            return

        # Get current equity for position sizing
        current_price = self._get_current_price(connector, signal.pair)
        equity = self._get_equity(connector)
        logger.info(f"Signal execution: pair={signal.pair} entry={entry} "
                     f"current_price={current_price} equity={equity}")

        # Calculate position size from risk guard
        usdt_amount = self._risk.get_budget_for_trade(signal, equity)
        if usdt_amount <= 0:
            logger.warning(f"Signal budget is 0 for {signal.pair} (equity={equity})")
            return

        # Use entry price (or current market if no entry zone)
        buy_price = entry if signal.entry_high else current_price
        if buy_price <= 0:
            return

        amount = usdt_amount / buy_price

        # Round amount to 6 decimal places
        amount = round(amount, 6)
        if amount <= 0:
            return

        # Place buy order via strategy callback
        order_id = None
        if self._buy_fn:
            try:
                order_id = self._buy_fn(
                    symbol=signal.pair,
                    amount=Decimal(str(amount)),
                    price=Decimal(str(buy_price)),
                )
            except Exception as e:
                logger.error(f"Signal buy failed for {signal.pair}: {e}")
                return

        if order_id:
            self._position_mgr.open_position(
                symbol=signal.pair,
                entry_price=buy_price,
                amount=amount,
                stop_loss=signal.stop_loss or buy_price * 0.95,
                take_profits=signal.take_profits,
                signal_confidence=signal.confidence.value,
                raw_message=signal.raw_message,
                channel_name=channel_name,
                order_id=str(order_id),
            )
            self._risk.record_trade_opened()
            self._log_audit_trade(signal, channel_name, "OPEN_LONG", buy_price, "live_entry")
            self._notify(
                f"[LIVE] Signal entered: {signal.pair}\n"
                f"Entry: ${buy_price:,.2f}\n"
                f"Amount: {amount:.6f} (${usdt_amount:.2f})\n"
                f"SL: ${signal.stop_loss:,.2f}\n"
                f"TPs: {', '.join(f'${tp:,.2f}' for tp in signal.take_profits)}\n"
                f"Confidence: {signal.confidence.value}\n"
                f"Channel: {channel_name}"
            )
            logger.info(f"[LIVE] Signal entered: {signal.pair} @ ${buy_price:,.2f} "
                        f"amount={amount:.6f} order={order_id}")
        else:
            logger.warning(f"Signal buy returned no order ID for {signal.pair}")
            self._log_audit_trade(signal, channel_name, "buy_failed", buy_price, "no_order_id")

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
                if not self._audit_mode:
                    self._execute_close(pos, pos.take_profits[0], "tp1")
                self._position_mgr.partial_close(pos.symbol, pos.tp1_close_pct, pos.take_profits[0], "tp1")
                self._position_mgr.update_stop_loss(pos.symbol, pos.entry_price)
                self._notify(f"[{'AUDIT' if self._audit_mode else 'LIVE'}] TP1 hit: {pos.symbol} @ ${pos.take_profits[0]:,.2f}, SL → breakeven")
                self._log_position_trade(pos, pos.take_profits[0], "tp1")

            # TP2 hit
            if not pos.tp2_hit and len(pos.take_profits) >= 2 and current_price >= pos.take_profits[1]:
                pos.tp2_hit = True
                if not self._audit_mode:
                    self._execute_close(pos, pos.take_profits[1], "tp2")
                self._position_mgr.partial_close(pos.symbol, pos.tp2_close_pct, pos.take_profits[1], "tp2")
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
        """Get current price from connector."""
        try:
            if self._get_price_fn:
                return self._get_price_fn(symbol)
            if connector is None:
                return 0
            price_obj = connector.get_mid_price(symbol)
            if price_obj:
                return float(price_obj)
        except Exception:
            pass
        return 0

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

    def _execute_close(self, pos: SignalPosition, price: float, reason: str):
        """Place a sell order to close a signal position."""
        if not self._sell_fn or pos.remaining_amount <= 0:
            return

        try:
            amount_to_sell = round(pos.remaining_amount, 6)
            if amount_to_sell <= 0:
                return
            order_id = self._sell_fn(
                symbol=pos.symbol,
                amount=Decimal(str(amount_to_sell)),
                price=Decimal(str(price)),
            )
            if order_id:
                logger.info(f"Signal close order placed: {pos.symbol} {amount_to_sell} @ ${price:,.2f} ({reason})")
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
            is_audit=1 if self._audit_mode else 0,
        ))

    def _notify(self, message: str):
        """Send Telegram notification."""
        if self._telegram_send:
            try:
                self._telegram_send(message)
            except Exception as e:
                logger.error(f"Signal notify failed: {e}")
