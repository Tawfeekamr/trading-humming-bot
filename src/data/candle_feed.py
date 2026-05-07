import os
import pandas as pd
from binance.client import Client


class CandleFeed:
    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h",
                 testnet: bool = False):
        self.symbol = symbol
        self.interval = interval
        if testnet:
            api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
            api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
            self.client = Client(api_key, api_secret, testnet=True)
        else:
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")
            self.client = Client(api_key, api_secret)

    def fetch_candles(self, limit: int = 200) -> pd.DataFrame:
        klines = self.client.get_klines(
            symbol=self.symbol,
            interval=self.interval,
            limit=limit,
        )
        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["open", "high", "low", "close", "volume"]]
