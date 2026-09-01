from __future__ import annotations

import pandas as pd


def test_write_trace_csv_persists_required_per_bar_fields(tmp_path):
    from scripts.diagnose_exposure import TRACE_COLUMNS, write_trace_csv

    path = tmp_path / "ETHUSDT_trained_fold0.csv"
    rows = [
        {
            "timestamp": "2026-01-01T00:00:00",
            "pair": "ETHUSDT",
            "policy": "trained",
            "fold": 0,
            "seed": 42,
            "action_index": 1,
            "action_label": "grid_1.0",
            "position_value": 125.0,
            "equity": 1000.0,
            "position_value/equity": 0.125,
            "inventory_units": 0.5,
            "turnover_this_bar": 20.0,
            "grid_active": True,
            "grid_levels_crossed_this_bar": 2,
        }
    ]

    write_trace_csv(path, rows)

    frame = pd.read_csv(path)
    assert list(frame.columns) == TRACE_COLUMNS
    assert frame.to_dict("records") == [rows[0]]
