# src/signals/binance_futures_connector.py
"""Binance USDT-M futures (fapi) REST connector. HMAC-signed, testnet by default.
Errors raise RuntimeError with the fapi body so the engine can notify.
"""
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TESTNET_BASE = "https://testnet.binancefuture.com"
LIVE_BASE = "https://fapi.binance.com"


class BinanceFuturesConnector:
    def __init__(self, api_key, api_secret, testnet=True, default_leverage=3):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base = TESTNET_BASE if testnet else LIVE_BASE
        self._default_leverage = default_leverage

    def _sign(self, params):
        query = urllib.parse.urlencode(params)
        sig = hmac.new(self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _request(self, method, path, params):
        params = dict(params)
        params.setdefault("timestamp", int(time.time() * 1000))
        params.setdefault("recvWindow", 5000)
        url = f"{self._base}{path}?{self._sign(params)}"
        req = urllib.request.Request(url, method=method,
                                     headers={"X-MBX-APIKEY": self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"fapi {method} {path} HTTP {e.code}: {body}")

    def _post(self, path, params): return self._request("POST", path, params)
    def _get(self, path, params):  return self._request("GET", path, params)

    def set_leverage(self, symbol, leverage):
        return self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    def set_margin_type(self, symbol, margin_type="ISOLATED"):
        try:
            return self._post("/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type})
        except RuntimeError as e:
            if "No need to change margin type" in str(e) or "-4046" in str(e):
                return {"msg": "no change needed"}
            raise
