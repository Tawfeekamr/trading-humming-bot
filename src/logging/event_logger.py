"""
event_logger.py — Structured event logging for the TA Grid Bot.
Writes one JSON object per line to daily-rotated files in logs/.
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventLogger:
    _MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB per event file
    _MAX_LOG_AGE_DAYS = 14

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._file = None
        self._lock = threading.Lock()
        self._cleanup_old_logs()

    def _cleanup_old_logs(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._MAX_LOG_AGE_DAYS)
        removed = 0
        for path in self._log_dir.glob("events_*.jsonl"):
            try:
                date_str = path.stem.replace("events_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    path.unlink()
                    removed += 1
            except (ValueError, OSError):
                continue
        if removed:
            logger.info(f"Cleaned up {removed} old event log files (>{self._MAX_LOG_AGE_DAYS}d)")
        self._cleanup_hummingbot_logs()

    def _cleanup_hummingbot_logs(self) -> None:
        _MAX_HB_LOG_SIZE = 50 * 1024 * 1024  # 50MB
        removed = 0
        truncated = 0
        for path in self._log_dir.glob("logs_*.log.*"):
            try:
                if path.stat().st_size > _MAX_HB_LOG_SIZE:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        for path in self._log_dir.glob("logs_*.log"):
            try:
                if path.stat().st_size > _MAX_HB_LOG_SIZE:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    lines = content.splitlines()
                    keep = lines[-5000:]
                    path.write_text("\n".join(keep) + "\n", encoding="utf-8")
                    truncated += 1
            except OSError:
                continue
        if removed or truncated:
            logger.info(f"Hummingbot log cleanup: removed {removed} rotated files, truncated {truncated} active logs")

    def _get_file(self) -> Any:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            path = self._log_dir / f"events_{today}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
            self._current_date = today
            self._cleanup_old_logs()
        # Rotate if file exceeds size limit
        if self._file and self._file.tell() > self._MAX_FILE_SIZE:
            self._file.close()
            ts = datetime.now(timezone.utc).strftime("%H%M%S")
            path = self._log_dir / f"events_{today}_{ts}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
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
