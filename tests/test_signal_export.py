"""Smoke tests for scripts/export_signal_dataset.py — the RL/ML dataset export."""
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import export_signal_dataset as exp  # noqa: E402


def _seed(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "signal_positions.json").write_text(json.dumps({
        "XLM-USDT": {
            "symbol": "XLM-USDT", "entry_price": 0.198, "stop_loss": 0.18,
            "take_profits": [0.21, 0.22], "signal_confidence": "high",
            "channel_name": "Binance Killers", "entry_timestamp": 1700000000.0,
            "tp1_hit": True, "amount": 50.0, "amount_closed": 16.5,
            "realized_pnl": 12.5, "is_closed": True, "exit_reason": "tp1",
            "raw_message": "sig",
        },
    }))
    conn = sqlite3.connect(data / "signal_journal.db")
    conn.execute(
        "CREATE TABLE signal_trades (id INTEGER, timestamp TEXT, symbol TEXT, "
        "action TEXT, entry_price REAL, realized_pnl REAL, exit_reason TEXT)"
    )
    conn.execute("INSERT INTO signal_trades VALUES (1,'2023-11-14T22:13:40+00:00','XLM-USDT','OPEN_LONG',0.198,0,'')")
    conn.execute("INSERT INTO signal_trades VALUES (2,'2023-11-14T23:00:00+00:00','XLM-USDT','CLOSE_TP1',0.21,12.5,'tp1')")
    conn.commit()
    conn.close()
    return data


def test_export_writes_positions_and_decisions_csv(tmp_path):
    data = _seed(tmp_path)
    out = tmp_path / "out"
    pos_csv, dec_csv = exp.export(str(data), str(out))
    assert os.path.exists(pos_csv)
    assert os.path.exists(dec_csv)

    rows = list(csv.DictReader(open(pos_csv)))
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "XLM-USDT"
    assert r["realized_pnl"] == "12.5"
    assert r["is_closed"] == "1"
    assert r["hold_seconds"] != ""  # derived from OPEN→CLOSE timestamps

    dec_rows = list(csv.DictReader(open(dec_csv)))
    assert len(dec_rows) == 2


def test_export_handles_missing_stores(tmp_path):
    # No data files at all — must not crash; writes empty (header-only) CSVs.
    empty = tmp_path / "empty"
    out = tmp_path / "out"
    pos_csv, dec_csv = exp.export(str(empty), str(out))
    assert os.path.exists(pos_csv) and os.path.exists(dec_csv)
