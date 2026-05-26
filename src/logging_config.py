"""Structured JSON logging for production monitoring."""
import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": self.formatTime(record, self.default_time_format),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["error"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S")
        msg = f"{ts} [{record.levelname}] {record.module}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0]:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging(level: str = None) -> None:
    level = level or os.environ.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_dir = Path(os.environ.get("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # Console handler — JSON for structured output
    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter())

    # File handler — plain text, daily rotation, keep 30 days
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"bot_{today}.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(PlainFormatter())

    # Separate crash log — only ERROR and above, always there for quick debugging
    crash_handler = logging.handlers.RotatingFileHandler(
        log_dir / "crashes.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    crash_handler.setFormatter(PlainFormatter())
    crash_handler.setLevel(logging.ERROR)

    logging.root.handlers = [console, file_handler, crash_handler]
    logging.root.setLevel(log_level)
