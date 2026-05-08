import json
import logging
import asyncio
import math
from typing import Callable, Optional
import websockets

logger = logging.getLogger(__name__)


class WebSocketFeed:
    BINANCE_WS = "wss://stream.binance.com:9443/ws"
    BINANCE_WS_TESTNET = "wss://testnet.binance.vision/ws"

    # BTC sanity bounds: 0 < price < 1,000,000
    MIN_PRICE = 0
    MAX_PRICE = 1_000_000

    def __init__(self, symbol: str = "btcusdt", testnet: bool = False,
                 on_price_update: Optional[Callable[[float], None]] = None,
                 max_retries: int = 50):
        self.symbol = symbol.lower()
        self.testnet = testnet
        self.on_price_update = on_price_update
        self._running = False
        self._latest_price: float = 0.0
        self._max_retries = max_retries
        self._consecutive_failures = 0

    @property
    def latest_price(self) -> float:
        return self._latest_price

    def _validate_price(self, data: dict) -> Optional[float]:
        """Validate price from WebSocket message.

        Returns None if price is invalid, otherwise returns validated price.
        """
        # Check if "c" key exists
        if "c" not in data:
            logger.warning(f"WebSocket message missing 'c' key: {data}")
            return None

        try:
            price = float(data["c"])
        except (ValueError, TypeError):
            logger.warning(f"WebSocket price conversion failed: {data.get('c')}")
            return None

        # Check for NaN or Infinity
        if not math.isfinite(price):
            logger.warning(f"WebSocket price is not finite: {price}")
            return None

        # Check price is positive and within BTC sanity range
        if not (self.MIN_PRICE < price < self.MAX_PRICE):
            logger.warning(f"WebSocket price out of valid range [{self.MIN_PRICE}, {self.MAX_PRICE}]: {price}")
            return None

        return price

    async def start(self) -> None:
        base = self.BINANCE_WS_TESTNET if self.testnet else self.BINANCE_WS
        stream = f"{base}/{self.symbol}@ticker"
        self._running = True
        retry_delay = 1
        while self._running:
            # Check max retries
            if self._consecutive_failures >= self._max_retries:
                logger.error(f"WebSocket max retries ({self._max_retries}) exceeded. Stopping.")
                self._running = False
                break

            try:
                async with websockets.connect(stream) as ws:
                    logger.info(f"WebSocket connected: {stream}")
                    retry_delay = 1
                    self._consecutive_failures = 0  # Reset on successful connection
                    while self._running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        price = self._validate_price(data)
                        if price is not None:
                            self._latest_price = price
                            if self.on_price_update:
                                self.on_price_update(price)
            except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
                if not self._running:
                    break
                self._consecutive_failures += 1
                logger.warning(f"WebSocket disconnected: {e}. Reconnecting in {retry_delay}s... (failure {self._consecutive_failures}/{self._max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            except Exception as e:
                if not self._running:
                    break
                self._consecutive_failures += 1
                logger.error(f"WebSocket error: {e} (failure {self._consecutive_failures}/{self._max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def stop(self) -> None:
        self._running = False
