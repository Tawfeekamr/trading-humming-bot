#!/usr/bin/env python3
# src/ml/regime_pusher.py
"""Always-on sidecar that keeps the Rust engine's regime cache live.

The Rust engine consumes ML regime labels via ``data/regime_cache.json``
(3-min TTL) to gate grid/mean-reversion, but nothing produces them — so the
cache goes stale and every lookup returns ``None`` (strategies fall back to
their own ADX/CHOP TA gates). This module closes that gap: every
``REGIME_INTERVAL_SEC`` (default 180s = the TTL) it fetches fresh 1h bars for
each pair, computes the same 14 ``MARKET_FEATURE_COLS`` the reproducible RF
classifier was trained on (see :mod:`src.ml.train_regime`), predicts the
regime + confidence, and pushes all pairs in one ``POST /api/v1/regime``.

Reuses the trading-signal-listener image (sklearn + pandas + pandas_ta +
requests). No torch. No Rust changes — only the existing HTTP endpoints.

Run:  ``python -m src.ml.regime_pusher``
Env:  RUST_ENGINE_URL (default http://rust-bot:3030),
      REGIME_PAIRS (comma-separated, default ETH-USDT,BNB-USDT,DOGE-USDT,XRP-USDT),
      REGIME_INTERVAL_SEC (default 180),
      MODEL_DIR (default models).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import pandas as pd
import requests

from src.data.feature_contract import MARKET_FEATURE_COLS, assert_market_feature_contract
from src.data.feature_engineering import calculate_technical_features
from src.ml.regime_classifier import RegimeClassifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("regime_pusher")

# Engine bar cache size (trading-engine-core/src/bar_cache.rs MAX_BARS_PER_PAIR).
# Requesting exactly this many 1h bars guarantees the response is served from
# the 1h cache (the /api/v1/klines connector fallback defaults to 1m — a silent
# interval mismatch we must avoid, since the classifier was trained on 1h bars).
KLINE_LIMIT = 500
KLINE_INTERVAL = "1h"

REGIME_NAMES = {0: "ranging", 1: "trending", 2: "danger"}


def model_path_for(pair: str, model_dir: str) -> str:
    """``ETH-USDT`` → ``models/regime_ETH-USDT_clean.pkl`` (reproducible models only)."""
    return os.path.join(model_dir, f"regime_{pair}_clean.pkl")


def _declared_feature_contract_ok(clf: RegimeClassifier) -> bool:
    """True when a model's embedded feature manifest is absent or matches."""
    columns = getattr(clf, "feature_columns", None)
    if columns is None:
        return True
    try:
        assert_market_feature_contract(columns)
    except ValueError as exc:
        log.error("Model feature contract rejected: %s", exc)
        return False
    return True


def load_models(pairs: list[str], model_dir: str) -> dict[str, RegimeClassifier]:
    """Load one reproducible RF classifier per pair that has a model file.

    Pairs without a model are skipped (logged) — they keep regime=None on the
    engine, i.e. the existing TA-fallback behaviour. No regression.
    """
    models: dict[str, RegimeClassifier] = {}
    for pair in pairs:
        path = model_path_for(pair, model_dir)
        if not os.path.exists(path):
            log.warning("No clean model for %s (expected %s) — skipping, TA fallback stays)", pair, path)
            continue
        clf = RegimeClassifier(model_path=path, model_type="random_forest")
        clf.load_model()
        if not _declared_feature_contract_ok(clf):
            continue
        models[pair] = clf
        log.info("Loaded model for %s from %s", pair, path)
    return models


def compute_regime(df: pd.DataFrame, clf: RegimeClassifier) -> Optional[tuple[int, float]]:
    """Compute (regime, confidence) for the latest bar.

    Pure w.r.t. (df, clf) — unit-testable. Replicates the training feature
    path exactly: ``calculate_technical_features`` (which drops warmup NaNs)
    then select ``MARKET_FEATURE_COLS`` in order, last row. No ffill — matching
    ``train_regime.py``.

    Returns None if no NaN-free row survives (degenerate / too-short input).

    ``calculate_technical_features`` can raise on very short input (pandas_ta's
    ``ta.adx`` returns None below its warmup) — we catch that here so one pair's
    bad data returns None (→ skipped) instead of killing the whole cycle.
    Production always passes ≥500 bars, so this is purely defensive.
    """
    if not _declared_feature_contract_ok(clf):
        return None
    try:
        feats = calculate_technical_features(df.copy())
    except Exception:
        return None
    if feats is None or feats.empty:
        return None
    row = feats[MARKET_FEATURE_COLS].iloc[-1:]
    if row.empty or row.isna().any().any():
        return None
    probs = clf.predict_proba_full(row)  # {int(class): float(prob)}
    regime = max(probs, key=probs.get)
    confidence = float(probs[regime])
    return int(regime), confidence


def fetch_klines(session: requests.Session, base_url: str, pair: str) -> Optional[pd.DataFrame]:
    """GET /api/v1/klines for one pair → OHLCV DataFrame, or None on failure."""
    url = f"{base_url}/api/v1/klines"
    params = {"symbol": pair, "interval": KLINE_INTERVAL, "limit": KLINE_LIMIT}
    try:
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        bars = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.error("klines fetch failed for %s: %s", pair, exc)
        return None
    if not bars:
        log.warning("Empty klines response for %s", pair)
        return None
    df = pd.DataFrame(bars)
    # Engine Bar serde: {open, high, low, close, volume, timestamp(i64 ms)}.
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
    df = df.set_index("timestamp").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def collect_regime(
    models: dict[str, RegimeClassifier], session: requests.Session, base_url: str
) -> list[dict]:
    """Fetch bars + predict for every loaded pair → list of RegimeUpdate dicts."""
    updates: list[dict] = []
    for pair, clf in models.items():
        df = fetch_klines(session, base_url, pair)
        if df is None or len(df) < 60:  # need > warmup (~50) for a NaN-free last row
            log.warning("Insufficient bars for %s (got %s) — skipping", pair, len(df) if df is not None else 0)
            continue
        result = compute_regime(df, clf)
        if result is None:
            log.warning("No usable feature row for %s — skipping", pair)
            continue
        regime, confidence = result
        updates.append({"pair": pair, "regime": regime, "confidence": confidence})
        log.info(
            "%s → regime=%s(%s) confidence=%.3f",
            pair, regime, REGIME_NAMES.get(regime, "?"), confidence,
        )
    return updates


def push_regime(session: requests.Session, base_url: str, updates: list[dict]) -> bool:
    """POST all updates to /api/v1/regime. Returns True on success."""
    if not updates:
        log.warning("No regime updates to push this cycle")
        return False
    try:
        resp = session.post(f"{base_url}/api/v1/regime", json=updates, timeout=15)
        resp.raise_for_status()
        log.info("Pushed %d regime updates → %s", len(updates), resp.text.strip())
        return True
    except requests.RequestException as exc:
        log.error("regime POST failed: %s", exc)
        return False


def main() -> int:
    base_url = os.environ.get("RUST_ENGINE_URL", "http://rust-bot:3030").rstrip("/")
    pairs = [p.strip() for p in os.environ.get(
        "REGIME_PAIRS", "ETH-USDT,BNB-USDT,DOGE-USDT,XRP-USDT").split(",") if p.strip()]
    interval = int(os.environ.get("REGIME_INTERVAL_SEC", "180"))
    model_dir = os.environ.get("MODEL_DIR", "models")

    log.info("Starting regime pusher: base=%s pairs=%s interval=%ss model_dir=%s",
             base_url, pairs, interval, model_dir)
    models = load_models(pairs, model_dir)
    if not models:
        log.error("No models loaded — nothing to push. Exiting.")
        return 1

    session = requests.Session()
    log.info("Entering push loop")
    while True:
        try:
            updates = collect_regime(models, session, base_url)
            push_regime(session, base_url, updates)
        except Exception:  # never let the loop die
            log.exception("Cycle failed (continuing after sleep)")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
