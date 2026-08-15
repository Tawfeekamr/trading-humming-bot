#!/usr/bin/env python3
"""Emit the run manifest for a corrected-protocol walk-forward run.

Captures: commit SHA, data-end date, per-pair data hashes (from the
walk-forward reports), seeds, library versions, wall-clock timestamp.
Written to reports/run_manifest_<timestamp>.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy
import pandas
import sklearn
import torch
import stable_baselines3 as sb3


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=2,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> int:
    reports_dir = Path("reports")
    per_pair = {}
    for path in sorted(reports_dir.glob("rl_walk_forward_*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        meta = data.get("metadata", {})
        per_pair[path.stem.replace("rl_walk_forward_", "")] = {
            "data_end": meta.get("data_end"),
            "data_sha256": meta.get("data_sha256"),
            "data_bars": meta.get("data_bars"),
            "embargo_bars": meta.get("embargo_bars"),
            "train_bars": meta.get("train_bars"),
            "test_bars": meta.get("test_bars"),
            "step_bars": meta.get("step_bars"),
            "fold_specific_rf": meta.get("fold_specific_rf"),
            "source_commit": meta.get("source_commit"),
        }
    manifest = {
        "run_type": "corrected-protocol walk-forward (audit fix 2026-08-15)",
        "commit_sha": git_sha(),
        "wall_clock_utc": datetime.now(timezone.utc).isoformat(),
        "seeds": {"ppo": 42, "fold_rf": 42, "bootstrap": 42},
        "library_versions": {
            "numpy": numpy.__version__,
            "pandas": pandas.__version__,
            "scikit-learn": sklearn.__version__,
            "torch": torch.__version__,
            "stable-baselines3": sb3.__version__,
        },
        "pairs": per_pair,
    }
    out = reports_dir / f"run_manifest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
