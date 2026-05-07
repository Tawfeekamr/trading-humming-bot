import json
import logging
import asyncio
from typing import Callable, Optional
import websockets

logger = logging.getLogger(__name__)


class WebSocketFeed:
    BINANCE_WS = "wss://stream.binance.com:9443/ws"
    BINANCE_WS_TESTNET = "wss://testnet.binance.vision/ws"

    def __init__(self, symbol: str = "btcusdt", testnet: bool = False,
                 on_price_update: Optional[Callable[[float], None]] = None):
        self.symbol = symbol.lower()
        self.testnet = testnet
        self.on_price_update = on_price_update
        self._running = False
        self._latest_price: float = 0.0

    @property
    def latest_price(self) -> float:
        return self._latest_price

    async def start(self) -> None:
        base = self.BINANCE_WS_TESTNET if self.testnet else self.BINANCE_WS
        stream = f"{base}/{self.symbol}@ticker"
        self._running = True
        async with websockets.connect(stream) as ws:
            logger.info(f"WebSocket connected: {stream}")
            while self._running:
                msg = await ws.recv()
                data = json.loads(msg)
                price = float(data["c"])
                self._latest_price = price
                if self.on_price_update:
                    self.on_price_update(price)

    def stop(self) -> None:
        self._running = False
