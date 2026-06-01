"""Market data feed — polls the Rust engine API for klines and emits bars.

Polls the Rust engine's /api/v1/klines endpoint on a configurable interval
(default: 60s for 1m bars). This is simpler and more reliable than running
a separate WebSocket connection in Python, since the Rust engine already
manages WebSocket connections to exchanges.

Usage:
    feed = DataFeed(base_url="http://localhost:3030")
    feed.add_pair("BTC-USDT")
    async for bar in feed.bars():
        strategy_host.on_bar(bar)
"""
import asyncio
import logging
import time
from typing import AsyncIterator

import requests

logger = logging.getLogger(__name__)


class DataFeed:
    """Polls the Rust engine API for closed bars and yields them.

    Args:
        base_url: Rust engine API URL
        interval: Candlestick interval (e.g., "1m", "5m", "1h")
        poll_seconds: How often to poll for new bars
    """

    # Map interval to approximate poll frequency
    INTERVAL_SECONDS = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    def __init__(
        self,
        base_url: str = "http://localhost:3030",
        interval: str = "1m",
        poll_seconds: float | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._interval = interval
        self._poll_seconds = poll_seconds or min(
            self.INTERVAL_SECONDS.get(interval, 60), 60
        )
        self._pairs: list[str] = []
        self._last_bar_time: dict[str, int] = {}
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def add_pair(self, pair: str):
        """Add a trading pair to the feed (e.g., "BTC-USDT")."""
        if pair not in self._pairs:
            self._pairs.append(pair)

    @property
    def pairs(self) -> list[str]:
        return list(self._pairs)

    def _fetch_klines(self, symbol: str, limit: int = 2) -> list[dict]:
        """Fetch the latest klines from the Rust engine API.

        Returns the most recent closed bars. We fetch limit=2 so we can
        detect the newest closed bar by comparing timestamps.
        """
        try:
            resp = self._session.get(
                f"{self._base_url}/api/v1/klines",
                params={"symbol": symbol, "interval": self._interval, "limit": limit},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("Failed to fetch klines for %s: %s", symbol, e)
            return []

    def _check_new_bars(self) -> list[dict]:
        """Check all pairs for new closed bars since last poll.

        Returns a list of bar dicts with instrument_id added.
        """
        new_bars = []
        for pair in self._pairs:
            symbol = pair.replace("-", "")
            raw_bars = self._fetch_klines(symbol, limit=2)
            for raw in raw_bars:
                ts = raw.get("timestamp", 0)
                # Only emit bars we haven't seen yet
                if ts > self._last_bar_time.get(pair, 0):
                    self._last_bar_time[pair] = ts
                    bar = {
                        "instrument_id": pair,
                        "open": float(raw.get("open", 0)),
                        "high": float(raw.get("high", 0)),
                        "low": float(raw.get("low", 0)),
                        "close": float(raw.get("close", 0)),
                        "volume": float(raw.get("volume", 0)),
                        "timestamp": ts,
                    }
                    new_bars.append(bar)
        return new_bars

    async def bars(self) -> AsyncIterator[dict]:
        """Async generator that yields new bars as they close.

        Polls at the configured interval. Yields bar dicts suitable
        for passing directly to StrategyHost.on_bar().
        """
        logger.info(
            "DataFeed started: %d pairs, interval=%s, poll=%ds",
            len(self._pairs), self._interval, self._poll_seconds,
        )

        # Initial preload — seed last_bar_time so we don't replay history
        for pair in self._pairs:
            symbol = pair.replace("-", "")
            raw_bars = self._fetch_klines(symbol, limit=1)
            if raw_bars:
                self._last_bar_time[pair] = raw_bars[0].get("timestamp", 0)
                logger.info("Preloaded last bar for %s at ts=%d", pair, self._last_bar_time[pair])

        while True:
            try:
                new_bars = self._check_new_bars()
                for bar in new_bars:
                    logger.debug(
                        "New bar: %s close=%.2f ts=%d",
                        bar["instrument_id"], bar["close"], bar["timestamp"],
                    )
                    yield bar
            except Exception as e:
                logger.error("DataFeed poll error: %s", e)

            await asyncio.sleep(self._poll_seconds)
