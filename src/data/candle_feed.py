import os
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
        try:
            klines = self.client.get_klines(
                symbol=self.symbol,
                interval=self.interval,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"Failed to fetch candles for {self.symbol}: {e}")
            # Return empty DataFrame on failure
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Log warning if NaN values detected after coercion
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            nan_count = df[col].isna().sum()
            if nan_count > 0:
                logger.warning(f"{nan_count} NaN values detected in column '{col}' after coercion")

        return df[["open", "high", "low", "close", "volume"]]
