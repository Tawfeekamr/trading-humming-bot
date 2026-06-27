# src/signals/paper_futures_connector.py
"""Paper (simulated) USDT-perpetual futures connector, priced off Gate.io.

Replaces BinanceFuturesConnector for the futures signal engine. Binance's
*testnet* trading endpoints reject every coin outside a ~30-symbol set
(`fapi POST /fapi/v1/leverage HTTP 400: -1121 Invalid symbol`), so the futures
engine traded nothing. This connector makes it a pure paper simulator:

  * Gate.io USDT-perp ``mark_price`` for pricing (807 contracts, incl. ICP/FET/INJ)
  * synthetic ``paper_fut_*`` order ids for open/close (no real exchange order)
  * no API keys, no auth, no real money

The engine already owns leveraged position state + P&L (futures_math,
signal_position, signal_risk), so this connector holds NO state — positions live
in the engine's position_mgr. It implements the same surface the engine calls on
BinanceFuturesConnector / FakeConn: get_price / set_leverage / set_margin_type /
open / close / get_position.
"""
import itertools
import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

GATE_PERP_TICKERS = "https://api.gateio.ws/api/v4/futures/usdt/tickers"


class PaperFuturesConnector:
    def __init__(self, default_leverage: int = 3):
        # default_leverage is accepted for interface parity with
        # BinanceFuturesConnector but is UNUSED — leverage lives in the engine's
        # risk math (signal_risk.get_budget_for_trade(leverage=...)), not here.
        self._default_leverage = default_leverage
        self._counter = itertools.count(1)

    @staticmethod
    def _contract(symbol: str) -> str:
        # "ICP-USDT" -> "ICP_USDT"  (Gate USDT-perp contract naming).
        return symbol.replace("-", "_")

    def get_price(self, symbol: str) -> float:
        """Gate.io USDT-perp mark_price (fallback last); 0.0 on any failure.

        Builds a ``urllib.request.Request`` for the GET (rather than passing a
        bare URL string) so the connector can attach headers and the engine can
        introspect the request via ``.full_url``. The contract segment is
        ``urllib.parse.quote``-escaped because ``symbol`` originates from
        Telegram signal parsing.
        """
        url = f"{GATE_PERP_TICKERS}?contract={urllib.parse.quote(self._contract(symbol))}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=5) as resp:
                data = json.loads(resp.read().decode())
            if data:
                row = data[0]
                mark = row.get("mark_price")
                if mark:
                    return float(mark)
                last = row.get("last")
                if last:
                    return float(last)
        except Exception as e:
            logger.warning(f"Paper futures price fetch failed for {symbol}: {e}")
        return 0.0

    def set_leverage(self, symbol, leverage):
        return {"msg": "paper"}

    def set_margin_type(self, symbol, margin_type="ISOLATED"):
        return {"msg": "paper"}

    def _next_id(self) -> str:
        return f"paper_fut_{next(self._counter)}"

    def open(self, symbol, side, qty, order_type="MARKET", price=None):
        oid = self._next_id()
        logger.info(f"[PAPER FUTURES] open {side} {symbol} qty={qty} -> {oid}")
        return {"orderId": oid, "status": "FILLED"}

    def close(self, symbol, side, qty):
        oid = self._next_id()
        logger.info(f"[PAPER FUTURES] close {side} {symbol} qty={qty} -> {oid}")
        return {"orderId": oid, "status": "FILLED"}

    def get_position(self, symbol):
        return None
