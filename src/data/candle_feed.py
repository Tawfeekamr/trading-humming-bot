import os
import time
import logging
import pandas as pd
from binance.client import Client

logger = logging.getLogger(__name__)


class CandleFeed:
    def __init__(self, symbol: str = "SOLUSDT", interval: str = "1h",
                 testnet: bool = False):
        self.symbol = symbol
        self.interval = interval
        # Always use real Binance for candle data (public endpoint, no keys needed)
        self.client = Client("", "")

    def fetch_candles(self, limit: int = 200) -> pd.DataFrame:
        klines = None
        for attempt in range(2):
            try:
                klines = self.client.get_klines(
                    symbol=self.symbol,
                    interval=self.interval,
                    limit=limit,
                )
                break
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"Candle fetch retry for {self.symbol}: {e}")
                    time.sleep(2)
                else:
                    logger.error(f"Failed to fetch candles for {self.symbol} after retry: {e}")
                    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        if not klines:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Validate: reject if last close is 0 or NaN
        last_close = df["close"].iloc[-1]
        if pd.isna(last_close) or last_close <= 0:
            logger.warning(f"Invalid candle data for {self.symbol}: last_close={last_close}")
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        return df[["open", "high", "low", "close", "volume"]]
