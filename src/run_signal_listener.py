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

load_dotenv()

logger = logging.getLogger(__name__)


def load_config():
    with open("config/strategy.yaml") as f:
        return yaml.safe_load(f)


def _signal_order(side, symbol, amount, price):
    """Place a signal order via the Rust engine API. Returns a mock order_id."""
    try:
        import urllib.request, json
        url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030") + "/api/v1/order"
        body = json.dumps({
            "symbol": symbol.replace("-", ""),
            "side": side,
            "order_type": "Market",
            "quantity": amount,
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

        engine = SignalEngine(
            config=signal_cfg,
            btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
            telegram_send_fn=_telegram_send,
            buy_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("BUY", symbol, amount, price),
            sell_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("SELL", symbol, amount, price),
            get_price_fn=lambda symbol: _get_price(symbol),
        )
        engine.start_listener()
        logger.info("Signal Copy Engine started — listening to Telegram channels")

        while True:
            try:
                engine.tick()
            except Exception as e:
                logger.error("Signal tick error: %s", e)
            await asyncio.sleep(1)

    # If signal disabled, just keep the command thread alive
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
