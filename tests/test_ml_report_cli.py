from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ml_report import ReportError, build_report


def _db(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE trades ("
        "id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, engine TEXT NOT NULL, "
        "pair TEXT NOT NULL, side TEXT, entry_price REAL, exit_price REAL, "
        "quantity REAL, pnl REAL NOT NULL, exit_reason TEXT, duration_mins INTEGER, "
        "fees REAL, regime_at_entry TEXT, context_json TEXT)"
    )
    connection.executemany(
        "INSERT INTO trades "
        "(id,timestamp,engine,pair,side,entry_price,exit_price,quantity,pnl,"
        "exit_reason,duration_mins,fees,regime_at_entry,context_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()


def test_empty_runtime_report_is_shadow_only(tmp_path):
    report = build_report(
        db_path=tmp_path / "trades.db", shadow_path=tmp_path / "shadow.jsonl"
    )

    assert report["ppo_active"] is False
    assert report["attribution_missing_count"] == 0
    assert report["shadow_decisions"] == 0
    assert report["metrics"]["trade_count"] == 0
    assert report["status"]["cache"]["state"] == "missing"
    assert report["status"]["shadow"]["state"] == "missing"


def test_report_counts_missing_entry_attribution_without_cache_inference(tmp_path):
    db_path = tmp_path / "trades.db"
    _db(
        db_path,
        [
            (
                1,
                "2026-08-01T00:00:00+00:00",
                "trend",
                "ETH-USDT",
                "BUY",
                100.0,
                101.0,
                1.0,
                1.0,
                "take_profit",
                30,
                0.2,
                None,
                None,
            )
        ],
    )
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1_756_656_000_000,
                "pair": "ETH-USDT",
                "action": 4,
                "engine": "trend",
                "size_mult": 1.0,
                "model_version": "ppo-v1",
                "model_sha256": "abc",
                "observation_age_ms": 0,
                "mode": "shadow",
            }
        )
        + "\n"
    )

    report = build_report(db_path=db_path, shadow_path=shadow_path)

    assert report["attribution_missing_count"] == 1
    assert report["slices"][0]["regime"] == "missing"
    assert report["slices"][0]["metrics"]["trade_count"] == 1
    assert report["slices"][0]["regime"] != "trending"
    assert report["shadow_decisions"] == 1


def test_report_aggregates_engine_and_regime_deterministically(tmp_path):
    db_path = tmp_path / "trades.db"
    context = json.dumps(
        {
            "regime_at_entry": "trending",
            "regime_confidence": 0.9,
            "regime_model_version": "rf-v1",
            "ml_gate_decision": "allowed",
            "decision_timestamp": 1_756_656_000_000,
            "ml_age_ms": 100,
        }
    )
    _db(
        db_path,
        [
            (2, "2026-08-01T00:02:00+00:00", "trend", "ETH-USDT", "BUY", 100, 102, 1, 2, "tp", 30, 0.2, None, context),
            (1, "2026-08-01T00:01:00+00:00", "trend", "ETH-USDT", "BUY", 100, 99, 1, -1, "sl", 20, 0.2, None, context),
        ],
    )

    first = build_report(db_path=db_path, shadow_path=tmp_path / "missing.jsonl")
    second = build_report(db_path=db_path, shadow_path=tmp_path / "missing.jsonl")

    assert first == second
    assert first["metrics"]["trade_count"] == 2
    assert first["metrics"]["net_pnl"] == 1.0
    assert first["metrics"]["by_engine"]["trend"]["trade_count"] == 2
    assert first["metrics"]["by_regime"]["trending"]["trade_count"] == 2
    assert first["status"]["cache"]["model_version"] == "rf-v1"
    assert first["status"]["cache"]["state"] == "live"


def test_cli_writes_report_and_rejects_malformed_shadow(tmp_path):
    db_path = tmp_path / "trades.db"
    out_path = tmp_path / "report.json"
    bad_shadow = tmp_path / "bad.jsonl"
    bad_shadow.write_text("not-json\n")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/ml_report.py",

            "--db",
            str(db_path),
            "--since",
            "2026-08-01T00:00:00+00:00",
            "--out",
            str(out_path),
            "--shadow",
            str(bad_shadow),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not out_path.exists()
def test_report_joins_shadow_decision_to_entry_timestamp_without_filling_regime(tmp_path):
    db_path = tmp_path / "trades.db"
    context = json.dumps(
        {
            "decision_timestamp": 1_756_656_000_000,
            "regime_at_entry": "ranging",
        }
    )
    _db(
        db_path,
        [
            (1, "2026-08-01T00:01:00+00:00", "grid", "ETH-USDT", "BUY", 100, 101, 1, 1, "tp", 10, 0.1, None, context),
        ],
    )
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1_756_656_000_000,
                "pair": "ETHUSDT",
                "action": 1,
                "engine": "grid",
                "size_mult": 1.0,
                "model_version": "ppo-v1",
                "model_sha256": "abc",
                "observation_age_ms": 10,
                "mode": "shadow",
            }
        )
        + "\n"
    )

    report = build_report(db_path=db_path, shadow_path=shadow_path)

    assert report["attribution_missing_count"] == 0
    assert report["shadow_decisions"] == 1
    assert report["metrics"]["by_regime"]["ranging"]["trade_count"] == 1
    assert report["shadow_attributed_trades"] == 1
    assert report["status"]["cache"]["state"] == "inconclusive"


def test_shadow_model_version_does_not_become_regime_model_provenance(tmp_path):
    db_path = tmp_path / "trades.db"
    context = json.dumps({"regime_at_entry": "trending", "decision_timestamp": 1_756_656_000_000, "ml_age_ms": 10})
    _db(
        db_path,
        [(1, "2026-08-01T00:01:00+00:00", "trend", "ETH-USDT", "BUY", 100, 101, 1, 1, "tp", 10, 0.1, None, context)],
    )
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1_756_656_000_000,
                "pair": "ETHUSDT",
                "action": 1,
                "engine": "grid",
                "size_mult": 1.0,
                "model_version": "ppo-v9",
                "model_sha256": "ppo-sha",
                "observation_age_ms": 10,
                "mode": "shadow",
            }
        )
        + "\n"
    )

    report = build_report(db_path=db_path, shadow_path=shadow_path)

    assert report["status"]["cache"]["model_version"] is None
    assert report["status"]["model"]["version"] is None
    assert report["status"]["shadow"]["model_version"] == "ppo-v9"


def test_existing_database_without_trades_table_is_malformed(tmp_path):
    db_path = tmp_path / "bad.db"
    sqlite3.connect(db_path).execute("CREATE TABLE other (id INTEGER)")

    with pytest.raises(ReportError):
        build_report(db_path=db_path, shadow_path=tmp_path / "shadow.jsonl")

def test_shadow_reader_rejects_action_engine_mismatch(tmp_path):
    db_path = tmp_path / "missing.db"
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1_756_656_000_000,
                "pair": "ETHUSDT",
                "action": 1,
                "engine": "trend",
                "size_mult": 1.0,
                "model_version": "ppo-v1",
                "model_sha256": "ppo-sha",
                "observation_age_ms": 0,
                "mode": "shadow",
            }
        ) + "\n"
    )
    with pytest.raises(ReportError, match="size multiplier outside action map"):
        build_report(db_path=db_path, shadow_path=shadow_path)


def test_runtime_evidence_requires_each_engine_regime_slice_to_reach_floor():
    from scripts.ml_report import _state_status

    rows = [
        {"engine": "grid", "context": {"regime_at_entry": "ranging"}}
        for _ in range(100)
    ] + [{"engine": "grid", "context": {"regime_at_entry": "trending"}}]
    status = _state_status(rows, [])
    assert status["evidence"]["state"] == "inconclusive"
    assert status["evidence"]["insufficient_slices"] == ["grid:trending"]
