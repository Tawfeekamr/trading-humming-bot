"""Signal listener entrypoint — replaces the deleted src/trading_engine/runner.py.
Starts the SignalEngine (Telethon channel listener + DeepSeek parser) which opens
positions in signal_positions.json. The Rust engine manages exits (TP/SL/close).
Also runs the Telegram command handler (poll_once loop) for /pnl_all etc.
"""
import asyncio
import logging
import os
import threading
import time

import yaml
from dotenv import load_dotenv

from src.signals.signal_engine import SignalEngine

load_dotenv()

logger = logging.getLogger(__name__)


def load_config():
    with open("config/strategy.yaml") as f:
        return yaml.safe_load(f)


def _signal_order(side, symbol, amount, price=None):
    """Place a signal order via the Rust engine API after checking CapitalManager. Returns order_id."""
    try:
        import urllib.request, json

        rust_url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030")

        # CapitalManager check for BUY orders
        if side == "BUY":
            try:
                cap_url = f"{rust_url}/api/v1/capital"
                cap_resp = urllib.request.urlopen(cap_url, timeout=5)
                cap_data = json.loads(cap_resp.read())
                free_cap = float(cap_data.get("free_capital", 0.0))

                est_price = float(price) if price else float(_get_price(symbol) or 0.0)
                req_notional = float(amount) * est_price

                if req_notional > 0 and free_cap > 0 and req_notional > free_cap:
                    logger.warning(
                        f"Signal BUY rejected by CapitalManager: {symbol} notional ${req_notional:.2f} > free capital ${free_cap:.2f}"
                    )
                    return None
            except Exception as cap_err:
                logger.warning(f"CapitalManager pre-check warning for {symbol}: {cap_err}")

        url = rust_url + "/api/v1/order"
        body = json.dumps({
            "symbol": symbol.replace("-", ""),
            "side": side,
            "order_type": "Market",
            # signal_engine passes amount as Decimal; json.dumps can't serialize
            # Decimal, which silently broke every live signal buy (prod bug,
            # 2026-06-20). Coerce to float at this JSON boundary.
            "quantity": float(amount),
            "price": None,
            "time_in_force": None,
            "client_order_id": f"sig_{symbol.replace('-','_')}_{int(time.time())}",
            "reduce_only": side == "SELL",
        }).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        oid = data.get("orderId", "unknown")
        logger.info(f"Signal {side} placed: {symbol} qty={amount} -> {oid}")
        return oid
    except Exception as e:
        logger.error(f"Signal {side} FAILED for {symbol}: {e}")
        return None


def _get_price(symbol):
    """Get current price from Rust engine orderbook."""
    try:
        import urllib.request, json
        sym = symbol.replace("-", "")
        url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + f"/api/v1/orderbook?symbol={sym}&limit=1"
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if bids and asks:
            return (float(bids[0][0]) + float(asks[0][0])) / 2
        elif bids:
            return float(bids[0][0])
    except Exception:
        pass
    return 0.0


def _get_equity():
    """Get total mark-to-market portfolio equity from the Rust engine.

    Used for signal position sizing. The signal container ticks the engine with
    no connector, so without this sizing falls back to the static
    max_capital_usdt regardless of the real account equity. Returns 0 on failure
    — SignalEngine._get_equity then falls back to max_capital_usdt.
    """
    try:
        import urllib.request, json
        url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/capital"
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        return float(data.get("total_equity", 0))
    except Exception as e:
        logger.warning("Equity fetch failed: %s", e)
        return 0.0


def _telegram_send(message: str):
    """Push a signal alert to Telegram. Sync POST, mirrors _signal_order.

    Called from SignalEngine._notify for every trade event (entry, TP hits,
    close). Must never raise — a send failure must not crash the signal tick.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram creds missing — skipping signal alert")
        return
    try:
        import urllib.request, json
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("Telegram send failed: %s", e)


def _poll_commands(handler):
    """Background thread: poll Telegram for /commands every second."""
    while True:
        try:
            handler.poll_once()
        except Exception as e:
            logger.error(f"Command poll error: {e}")
        time.sleep(1)


def dispatch_cycle(spot_engine, futures_engine, connector):
    """Run ONE dispatch cycle of the shared signal loop.

    Processes at most one queued message per cycle so a slow DeepSeek call
    (2-5s) never starves position management (SL/TP checks). Multiple queued
    messages are drained one-per-second, interleaved with manage() calls.
    This guarantees position management fires at least every ~5s even under
    heavy signal load. Only the spot engine owns the listener; the futures
    engine is headless.

    A futures-engine error must never kill the spot engine, so the futures
    dispatch is wrapped in try/except + log.
    """
    # Drain at most one message from the spot engine's queue per cycle.
    msg = None
    if spot_engine._listener is not None:
        msg = spot_engine._listener.get_message()

    if msg is not None:
        try:
            spot_engine.process_one(msg, connector)
        except Exception as e:
            logger.error("Spot process_one error (manage still runs): %s", e)
        if futures_engine is not None:
            try:
                futures_engine.process_one(msg, connector)
            except Exception as e:
                logger.error("Futures process_one error (spot continues): %s", e)

    # Always manage positions — even when a message was just processed.
    spot_engine.manage(connector)
    if futures_engine is not None:
        try:
            futures_engine.manage(connector)
        except Exception as e:
            logger.error("Futures manage error (spot continues): %s", e)

def _build_futures_engine(signal_cfg: dict, fc: dict):
    """Build the headless PAPER futures engine, or None if disabled.

    Extracted from main() so the wiring (PaperFuturesConnector, no Binance keys)
    is unit-testable without starting the Telethon listener. Paper-only: Gate.io
    perp pricing, synthetic orders, no real money.
    """
    futures_enabled = (
        os.environ.get("SIGNAL_MODE") == "futures" or fc.get("enabled", False)
    )
    if not futures_enabled:
        return None
    from src.signals.paper_futures_connector import PaperFuturesConnector

    futures_connector = PaperFuturesConnector(default_leverage=fc.get("leverage", 3))
    return SignalEngine(
        config={**signal_cfg, **fc, "allow_shorts": True},
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=_telegram_send,
        buy_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("BUY", symbol, amount, price),
        sell_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("SELL", symbol, amount, price),
        get_price_fn=futures_connector.get_price,
        get_equity_fn=_get_equity,
        own_listener=False,
        state_suffix="_futures",
        futures_mode=True,
        futures_connector=futures_connector,
        leverage=fc.get("leverage", 3),
    )


async def main():
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    logger.info("Config loaded from config/strategy.yaml")

    # Start Telegram command handler in a background thread
    from types import SimpleNamespace
    from src.notifications.telegram_commands import TelegramCommandHandler

    handler = TelegramCommandHandler(
        journal=None,
        state_machine=SimpleNamespace(),
        circuit_breaker=SimpleNamespace(halted=False),
        position_guard=SimpleNamespace(),
        event_logger=SimpleNamespace(),
        strategy=SimpleNamespace(
            env=os.environ.get("ENV", "testnet"),
            capital_usdt=10000,
            base_asset="CRYPTO",
            grid_manager=SimpleNamespace(capital_usdt=10000),
            _base_capital=10000,
            _trend_statuses={},
            _trend_capital=10000,
            pairs={},
            grid_pnl={},
            get_indicators_snapshot=lambda: None,
            _get_usdt_balance=lambda: 0,
            _get_base_balance=lambda: 0,
        ),
    )
    handler.start()
    poll_thread = threading.Thread(target=_poll_commands, args=(handler,), daemon=True)
    poll_thread.start()
    logger.info("Telegram command handler polling started")

    # Start signal listener
    signal_cfg = config.get("signal_copy", {})
    if not signal_cfg.get("enabled", False):
        logger.info("Signal Copy Engine disabled — commands still running")
    else:
        from src.signals.signal_engine import SignalEngine

        # Spot engine OWNS the single Telethon listener. It is always built.
        spot_engine = SignalEngine(
            config=signal_cfg,
            btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
            telegram_send_fn=_telegram_send,
            buy_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("BUY", symbol, amount, price),
            sell_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("SELL", symbol, amount, price),
            get_price_fn=lambda symbol: _get_price(symbol),
            get_equity_fn=_get_equity,
            # own_listener defaults to True, state_suffix defaults to "".
        )

        # Futures engine is HEADLESS and PAPER: own_listener=False, namespaced
        # state, Gate.io perp pricing, synthetic orders, no real money. Built
        # from the config flag alone — no Binance keys required (the testnet
        # path returned -1121 and is retired).
        fc = config.get("signals_futures", {})
        futures_engine = _build_futures_engine(signal_cfg, fc)
        if futures_engine is not None:
            logger.info("Futures Signal Engine built (paper, Gate.io perp, state_suffix=_futures)")
        else:
            logger.info("Futures Signal Engine disabled (futures_enabled=%s) — spot-only",
                        os.environ.get("SIGNAL_MODE") == "futures" or fc.get("enabled", False))

        # Wire the live engines into the Telegram command handler so the
        # control commands (/signal_pause, /signal_resume, /signal_pnl,
        # /signal_inject, /signal_close) can drive the spot engine. Without
        # this they reply 'Signal engine not configured.'
        handler.attach_signal_engines(spot_engine, futures_engine)
        logger.info("Signal engines attached to Telegram command handler")

        # Only the spot engine owns/starts/stops the listener. The futures
        # engine never touches the listener — that's what removes the deploy
        # blocker (a second listener can't authenticate against Telethon).
        spot_engine.start_listener()
        logger.info("Signal Copy Engine started — listening to Telegram channels")

        # Spot orders route through the Rust engine API. The futures engine
        # holds its own connector internally (futures_connector), so the
        # `connector` passed to dispatch_cycle is the spot Rust-API connector:
        # None here means spot buys/sells use _signal_order (Rust HTTP), as
        # today; manage(connector) is connector-aware.
        spot_connector = None
        while True:
            try:
                dispatch_cycle(spot_engine, futures_engine, spot_connector)
            except Exception as e:
                logger.error("Signal dispatch error: %s", e)
            await asyncio.sleep(1)

    # If signal disabled, just keep the command thread alive
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
