"""Signal listener entrypoint — replaces the deleted src/trading_engine/runner.py.
Starts the SignalEngine (Telethon channel listener + DeepSeek parser) which opens
positions in signal_positions.json. The Rust engine manages exits (TP/SL/close).
"""
import asyncio
import logging
import os

import yaml
from dotenv import load_dotenv

load_dotenv()  # Load .env (TELEGRAM_API_ID, TELEGRAM_API_HASH, DEEPSEEK_API_KEY, etc.)

logger = logging.getLogger(__name__)


def load_config():
    with open("config/strategy.yaml") as f:
        return yaml.safe_load(f)


async def main():
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    logger.info("Config loaded from config/strategy.yaml")

    signal_cfg = config.get("signal_copy", {})
    if not signal_cfg.get("enabled", False):
        logger.info("Signal Copy Engine disabled — exiting")
        return

    from src.signals.signal_engine import SignalEngine

    engine = SignalEngine(
        config=signal_cfg,
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=lambda msg: logger.info("Signal TG: %s", msg),
        # Order execution is handled by the Rust engine (reads signal_positions.json).
        # These fns are no-ops — the signal engine tracks positions; Rust places orders.
        buy_fn=lambda s, a, p, ot="MARKET": logger.info("Signal buy %s %s @ %s (Rust executes)", s, a, p),
        sell_fn=lambda s, a, p, ot="MARKET": logger.info("Signal sell %s %s @ %s (Rust executes)", s, a, p),
        get_price_fn=lambda s: 0.0,
    )
    engine.start_listener()
    logger.info("Signal Copy Engine started — listening to Telegram channels")

    while True:
        try:
            engine.tick()
        except Exception as e:
            logger.error("Signal tick error: %s", e)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
