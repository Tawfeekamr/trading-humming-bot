"""Aggregate runtime trade attribution and PPO shadow decisions.

The report intentionally reads only entry-time attribution persisted in the unified
journal.  A later regime-cache value is never used to fill an old trade's missing
metadata.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class ReportError(ValueError):
    """Raised when runtime inputs cannot produce a well-formed report."""


_CACHE_TTL_MS = 180_000
_REQUIRED_TRADE_COLUMNS = {"timestamp", "engine", "pair", "pnl"}
_REQUIRED_SHADOW_FIELDS = {
    "timestamp_ms",
    "pair",
    "action",
    "engine",
    "size_mult",
    "model_version",
    "model_sha256",
    "observation_age_ms",
    "mode",
}


def _iso_ms(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ReportError("timestamp cannot be boolean")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReportError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _number(value: Any, name: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool):
        raise ReportError(f"{name} must be numeric")
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ReportError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ReportError(f"{name} must be finite")
    if integer and number < 0:
        raise ReportError(f"{name} must be non-negative")
    return number


def _context(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReportError("trade context_json is not valid JSON") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReportError("trade context_json must be a JSON object")
    return value


def _read_trades(path: Path, since_ms: int | None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ReportError(f"cannot open journal: {exc}") from exc
    try:
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(trades)")
            }
        except sqlite3.Error as exc:
            raise ReportError(f"cannot inspect journal: {exc}") from exc
        missing = _REQUIRED_TRADE_COLUMNS - columns
        if missing:
            raise ReportError(f"journal is missing columns: {', '.join(sorted(missing))}")
        optional = {
            "id",
            "side",
            "entry_price",
            "exit_price",
            "quantity",
            "exit_reason",
            "duration_mins",
            "fees",
            "regime_at_entry",
            "context_json",
        }
        selected = sorted(_REQUIRED_TRADE_COLUMNS | (optional & columns))
        try:
            rows = connection.execute(
                f"SELECT {', '.join(selected)} FROM trades"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ReportError(f"cannot read journal: {exc}") from exc
    finally:
        connection.close()

    result: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(zip(selected, raw_row))
        timestamp_ms = _iso_ms(row["timestamp"])
        if timestamp_ms is None:
            raise ReportError("trade timestamp cannot be empty")
        if since_ms is not None and timestamp_ms < since_ms:
            continue
        pnl = float(_number(row["pnl"], "trade pnl"))
        context = _context(row.get("context_json"))
        # regime_at_entry is itself entry-time persisted metadata, so it is a
        # valid fallback for old rows that predate the JSON attribution object.
        if row.get("regime_at_entry") is not None and "regime_at_entry" not in context:
            context["regime_at_entry"] = row["regime_at_entry"]
        if "regime_confidence" in context and context["regime_confidence"] is not None:
            context["regime_confidence"] = float(
                _number(context["regime_confidence"], "regime_confidence")
            )
        if "ml_age_ms" in context and context["ml_age_ms"] is not None:
            context["ml_age_ms"] = int(_number(context["ml_age_ms"], "ml_age_ms", integer=True))
        result.append(
            {
                "id": int(row.get("id") or 0),
                "timestamp_ms": timestamp_ms,
                "timestamp": str(row["timestamp"]),
                "engine": str(row["engine"]),
                "pair": str(row["pair"]),
                "pnl": pnl,
                "fees": float(_number(row.get("fees") or 0.0, "trade fees")),
                "duration_mins": int(row.get("duration_mins") or 0),
                "context": context,
            }
        )
    return sorted(result, key=lambda row: (row["timestamp_ms"], row["id"], row["engine"], row["pair"]))


def _read_shadow(path: Path, since_ms: int | None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReportError(f"cannot read shadow journal: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ReportError(f"shadow journal line {line_number} is not JSON") from exc
        if not isinstance(value, dict) or not _REQUIRED_SHADOW_FIELDS <= value.keys():
            raise ReportError(f"shadow journal line {line_number} has an invalid schema")
        timestamp_ms = int(_number(value["timestamp_ms"], "shadow timestamp", integer=True))
        if since_ms is not None and timestamp_ms < since_ms:
            continue
        if value["mode"] != "shadow":
            raise ReportError(f"shadow journal line {line_number} is not shadow mode")
        action = int(_number(value["action"], "shadow action", integer=True))
        if action > 9:
            raise ReportError(f"shadow journal line {line_number} has an invalid action")
        age = int(_number(value["observation_age_ms"], "observation age", integer=True))
        size_mult = float(_number(value["size_mult"], "shadow size multiplier"))
        if not isinstance(value["pair"], str) or not value["pair"]:
            raise ReportError(f"shadow journal line {line_number} has no pair")
        if not isinstance(value["engine"], str) or not value["engine"]:
            raise ReportError(f"shadow journal line {line_number} has no engine")
        if not isinstance(value["model_version"], str) or not value["model_version"]:
            raise ReportError(f"shadow journal line {line_number} has no model version")
        if not isinstance(value["model_sha256"], str) or not value["model_sha256"]:
            raise ReportError(f"shadow journal line {line_number} has no model checksum")
        result.append(
            {
                **value,
                "timestamp_ms": timestamp_ms,
                "action": action,
                "size_mult": size_mult,
                "observation_age_ms": age,
            }
        )
    return sorted(result, key=lambda row: (row["timestamp_ms"], row["pair"], row["action"]))


def _metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    returns = [row["pnl"] for row in values]
    exposure = [1.0 if row["duration_mins"] > 0 else 0.0 for row in values]
    try:
        from src.ml.evaluation_report import summarize_returns

        result = summarize_returns(returns, exposure, fees=0.0, trade_count=len(values))
    except (ImportError, ValueError) as exc:
        raise ReportError(f"cannot calculate report metrics: {exc}") from exc
    result["fees"] = round(sum(row["fees"] for row in values), 12)
    result["slippage"] = 0.0
    return result


def _state_status(rows: list[dict[str, Any]], shadow: list[dict[str, Any]]) -> dict[str, Any]:
    cache_rows = [row for row in rows if row["context"].get("regime_at_entry") is not None]
    ages = [row["context"]["ml_age_ms"] for row in cache_rows if row["context"].get("ml_age_ms") is not None]
    versions = [row["context"]["regime_model_version"] for row in cache_rows if row["context"].get("regime_model_version")]
    reasons: set[str] = set()
    for row in rows:
        raw = row["context"].get("drift_reasons", [])
        if isinstance(raw, str):
            reasons.add(raw)
        elif isinstance(raw, list):
            reasons.update(str(item) for item in raw)
    cache_state = "missing" if not cache_rows else ("stale" if any(age > _CACHE_TTL_MS for age in ages) else "live")
    shadow_state = "missing" if not shadow else ("stale" if any(row["observation_age_ms"] > _CACHE_TTL_MS for row in shadow) else "shadow")
    model_state = "live" if versions else "missing"
    inconclusive = len(rows) < 100
    return {
        "cache": {
            "state": cache_state,
            "age_ms": max(ages) if ages else None,
            "model_version": versions[-1] if versions else None,
            "drift_reasons": sorted(reasons),
        },
        "model": {"state": model_state, "version": versions[-1] if versions else None},
        "shadow": {
            "state": shadow_state,
            "decision_age_ms": max((row["observation_age_ms"] for row in shadow), default=None),
            "model_version": shadow[-1]["model_version"] if shadow else None,
        },
        "evidence": {
            "state": "inconclusive" if inconclusive else "live",
            "trade_count": len(rows),
            "minimum_trades": 100,
        },
    }


def _join_shadow_attribution(rows: list[dict[str, Any]], shadow: list[dict[str, Any]]) -> None:
    """Attach an exact/near entry-time shadow decision to persisted context."""
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in shadow:
        by_pair[decision["pair"]].append(decision)
    for row in rows:
        context = row["context"]
        decision_timestamp = context.get("decision_timestamp")
        if decision_timestamp is None:
            continue
        try:
            decision_timestamp = int(decision_timestamp)
        except (TypeError, ValueError):
            raise ReportError("decision_timestamp must be an integer")
        candidates = by_pair.get(row["pair"], [])
        if not candidates:
            continue
        decision = min(candidates, key=lambda item: abs(item["timestamp_ms"] - decision_timestamp))
        if abs(decision["timestamp_ms"] - decision_timestamp) > 60_000:
            continue
        context.setdefault("router_mode", "shadow")
        context.setdefault("router_action", str(decision["action"]))
        context.setdefault("router_engine", decision["engine"])
        context.setdefault("router_size_mult", decision["size_mult"])
        context.setdefault("regime_model_version", decision["model_version"])


def build_report(
    db_path: str | Path,
    shadow_path: str | Path = "data/shadow_routing.jsonl",
    since: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic runtime report from journal and shadow JSONL."""
    since_ms = _iso_ms(since)
    rows = _read_trades(Path(db_path), since_ms)
    shadow = _read_shadow(Path(shadow_path), since_ms)
    _join_shadow_attribution(rows, shadow)

    by_engine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_slice: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    attribution_missing = 0
    for row in rows:
        context = row["context"]
        regime = context.get("regime_at_entry")
        if regime is None or regime == "":
            attribution_missing += 1
            regime = "missing"
        regime = str(regime).lower()
        by_engine[row["engine"]].append(row)
        by_regime[regime].append(row)
        by_slice[(row["engine"], regime)].append(row)

    metrics = _metrics(rows)
    metrics["by_engine"] = {key: _metrics(by_engine[key]) for key in sorted(by_engine)}
    metrics["by_regime"] = {key: _metrics(by_regime[key]) for key in sorted(by_regime)}
    slices = [
        {"engine": engine, "regime": regime, "metrics": _metrics(by_slice[(engine, regime)])}
        for engine, regime in sorted(by_slice)
    ]
    status = _state_status(rows, shadow)
    return {
        "metadata": {
            "report_type": "runtime_attribution",
            "schema_version": 1,
            "since": since,
            "ppo_active": False,
        },
        "metrics": metrics,
        "slices": slices,
        "ppo_active": False,
        "attribution_missing_count": attribution_missing,
        "shadow_decisions": len(shadow),
        "status": status,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="unified trades SQLite database")
    parser.add_argument("--since", default=None, help="inclusive ISO-8601 timestamp")
    parser.add_argument("--out", required=True, help="JSON report path")
    parser.add_argument(
        "--shadow",
        "--shadow-path",
        dest="shadow_path",
        default=os.environ.get("SHADOW_ROUTING_PATH", "data/shadow_routing.jsonl"),
        help="shadow routing JSONL path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(args.db, args.shadow_path, args.since)
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except (OSError, ReportError, sqlite3.Error, TypeError, ValueError) as exc:
        print(f"ml_report: malformed report input: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
