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

import hashlib
import json
import logging
import os
import pickle
import time
from collections import defaultdict, deque
from typing import Optional
import pandas as pd
import requests

from src.data.feature_contract import MARKET_FEATURE_COLS, assert_market_feature_contract
from src.ml.drift import evaluate_drift
from src.ml.model_metadata import (
    canonical_feature_contract_hash,
    read_metadata,
)
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
DRIFT_WINDOW_MS = 24 * 60 * 60 * 1000
DRIFT_CACHE_TTL_MS = 180_000
DRIFT_WINDOW_MAX_EVENTS = 2_000


class RegimeDriftMonitor:
    """Bounded, deterministic 24-hour prediction windows keyed by pair."""

    def __init__(self, window_ms: int = DRIFT_WINDOW_MS, max_events: int = DRIFT_WINDOW_MAX_EVENTS):
        self.window_ms = int(window_ms)
        self._windows = defaultdict(lambda: deque(maxlen=max_events))
        self._last_seen: dict[str, int] = {}
        self._metadata: dict[str, dict] = {}

    def observe(self, pair: str, regime: int, confidence: float, timestamp_ms: int, metadata: dict | None = None) -> None:
        timestamp_ms = int(timestamp_ms)
        window = self._windows[pair]
        cutoff = timestamp_ms - self.window_ms
        while window and window[0][0] < cutoff:
            window.popleft()
        window.append((timestamp_ms, int(regime), float(confidence)))
        self._last_seen[pair] = timestamp_ms
        if metadata is not None:
            self._metadata[pair] = dict(metadata)

    def collect_drift_report(self, now_ms: int | None = None) -> dict[str, dict]:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        report: dict[str, dict] = {}
        feature_hash = canonical_feature_contract_hash(MARKET_FEATURE_COLS)
        for pair in sorted(self._windows):
            window = self._windows[pair]
            cutoff = now - self.window_ms
            while window and window[0][0] < cutoff:
                window.popleft()
            metadata = self._metadata.get(pair, {})
            training = metadata.get("class_distribution", {})
            counts = {0: 0, 1: 0, 2: 0}
            confidences = []
            for _, regime, confidence in window:
                counts[regime] = counts.get(regime, 0) + 1
                confidences.append(confidence)
            total = sum(counts.values())
            live = {key: (value / total if total else 0.0) for key, value in sorted(counts.items())}
            confidence_24h = sum(confidences) / len(confidences) if confidences else None
            last_seen = self._last_seen.get(pair, 0)
            age_ms = max(0, now - last_seen) if last_seen else self.window_ms + 1
            feature_match = metadata.get("feature_contract_hash") == feature_hash
            reasons = evaluate_drift(
                training,
                live,
                confidence_24h,
                age_ms,
                DRIFT_CACHE_TTL_MS,
                feature_contract_match=feature_match,
            )
            item = {
                "pair": pair,
                "training_distribution": dict(training),
                "live_distribution": live,
                "confidence_24h": confidence_24h,
                "age_ms": age_ms,
                "reasons": reasons,
            }
            report[pair] = item
            if reasons:
                log.warning("regime_drift pair=%s reasons=%s report=%s", pair, reasons, item)
        return report


_DRIFT_MONITOR = RegimeDriftMonitor()


def collect_drift_report(models: dict[str, RegimeClassifier] | None = None, now_ms: int | None = None) -> dict[str, dict]:
    """Return deterministic drift reports without disabling predictions."""
    if models:
        for pair, clf in sorted(models.items()):
            metadata = getattr(clf, "metadata", None)
            if metadata is not None:
                _DRIFT_MONITOR._metadata[pair] = dict(metadata)
    return _DRIFT_MONITOR.collect_drift_report(now_ms=now_ms)
KLINE_LIMIT = 500
KLINE_INTERVAL = "1h"
REGIME_NAMES = {0: "ranging", 1: "trending", 2: "danger"}


def model_path_for(pair: str, model_dir: str) -> str:
    """``ETH-USDT`` → ``models/regime_ETH-USDT_clean.pkl`` (reproducible models only)."""
    return os.path.join(model_dir, f"regime_{pair}_clean.pkl")

def model_metadata(clf: RegimeClassifier, path: str) -> dict[str, str | None]:
    """Return deterministic model provenance for a regime update.

    The artifact digest is deliberately computed from the bytes on disk rather
    than from a mutable classifier object.  Feature provenance prefers a model
    supplied hash and otherwise hashes the canonical ordered feature manifest.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"model artifact not found: {path}")
    digest = hashlib.sha256()
    with open(path, "rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    version = getattr(clf, "model_version", None)
    if version is None:
        version = getattr(clf, "version", None)
    if version is None:
        try:
            with open(path, "rb") as artifact:
                serialized = pickle.load(artifact)
            if isinstance(serialized, dict):
                version = serialized.get("version")
        except (EOFError, OSError, pickle.PickleError, ValueError, ImportError):
            version = None
    feature_hash = getattr(clf, "feature_contract_hash", None)
    if feature_hash is None:
        columns = getattr(clf, "feature_columns", None)
        if columns is not None:
            canonical = json.dumps(list(columns), separators=(",", ":"), ensure_ascii=True)
            feature_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "model_version": None if version is None else str(version),
        "artifact_sha256": digest.hexdigest(),
        "feature_contract_hash": None if feature_hash is None else str(feature_hash),
    }


def build_regime_update(
    pair: str,
    regime: int,
    confidence: float,
    metadata: dict[str, str | None],
    timestamp_ms: int,
) -> dict:
    """Build the API payload without changing prediction semantics."""
    return {
        "pair": pair,
        "regime": int(regime),
        "confidence": float(confidence),
        "timestamp": int(timestamp_ms),
        "model_version": metadata.get("model_version"),
        "artifact_sha256": metadata.get("artifact_sha256"),
        "feature_contract_hash": metadata.get("feature_contract_hash"),
    }



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
        try:
            manifest = read_metadata(path)
            clf = RegimeClassifier(model_path=path, model_type="random_forest")
            clf.load_model()
            clf.metadata = manifest
        except (OSError, ValueError, EOFError, pickle.PickleError, ImportError) as exc:
            log.error("Model rejected for %s: %s — skipping, TA fallback stays", pair, exc)
            continue
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
        from src.data.feature_engineering import calculate_technical_features
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
        try:
            metadata = model_metadata(clf, getattr(clf, "model_path", model_path_for(pair, "")))
        except FileNotFoundError as exc:
            log.error("Model metadata unavailable for %s: %s — skipping", pair, exc)
            continue
        regime, confidence = result
        timestamp_ms = int(time.time() * 1000)
        updates.append(build_regime_update(pair, regime, confidence, metadata, timestamp_ms))
        _DRIFT_MONITOR.observe(pair, regime, confidence, timestamp_ms, getattr(clf, "metadata", None))
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
