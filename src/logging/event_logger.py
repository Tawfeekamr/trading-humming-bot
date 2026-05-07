"""
event_logger.py — Structured event logging for the TA Grid Bot.
Writes one JSON object per line to daily-rotated files in logs/.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventLogger:
    def __init__(self, log_dir: str = "logs"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._file = None
        self._lock = threading.Lock()

    def _get_file(self) -> Any:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            path = self._log_dir / f"events_{today}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
            self._current_date = today
        return self._file

    def log(self, event_type: str, **kwargs) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **kwargs,
        }
        with self._lock:
            try:
                f = self._get_file()
                f.write(json.dumps(event, default=str) + "\n")
                f.flush()
            except Exception as e:
                logger.error(f"Event log write failed: {e}")

    def close(self) -> None:
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None
                self._current_date = ""
