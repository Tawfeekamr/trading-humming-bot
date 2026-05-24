# tests/test_event_logger.py
import json
import time
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from src.logging.event_logger import EventLogger


class TestEventLogger:
    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "logs"
        EventLogger(log_dir=str(log_dir))
        assert log_dir.exists()

    def test_writes_jsonl_event(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        logger.log("test_event", key="value")
        logger.close()

        files = list(tmp_path.glob("events_*.jsonl"))
        assert len(files) >= 1
        with open(files[0]) as f:
            line = f.readline()
            data = json.loads(line)
            assert data["event"] == "test_event"
            assert data["key"] == "value"
            assert "ts" in data

    def test_event_has_timestamp(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        logger.log("ts_check")
        logger.close()

        files = list(tmp_path.glob("events_*.jsonl"))
        with open(files[0]) as f:
            data = json.loads(f.readline())
            ts = datetime.fromisoformat(data["ts"])
            assert ts.tzinfo is not None

    def test_multiple_events_written(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        for i in range(5):
            logger.log("batch", idx=i)
        logger.close()

        files = list(tmp_path.glob("events_*.jsonl"))
        with open(files[0]) as f:
            lines = f.readlines()
            assert len(lines) == 5

    def test_close_idempotent(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        logger.log("event1")
        logger.close()
        logger.close()  # Should not raise

    def test_default_str_serialization(self, tmp_path):
        """Non-serializable values should use str() fallback."""
        logger = EventLogger(log_dir=str(tmp_path))
        logger.log("test", obj={"nested": True}, dt=datetime.now(timezone.utc))
        logger.close()

        files = list(tmp_path.glob("events_*.jsonl"))
        with open(files[0]) as f:
            data = json.loads(f.readline())
            assert data["obj"]["nested"] is True


class TestDailyRotation:
    def test_date_change_opens_new_file(self, tmp_path):
        """When _current_date is stale, _get_file should open a new file for today."""
        logger = EventLogger(log_dir=str(tmp_path))
        logger.close()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Manually set stale date and no file handle
        logger._current_date = "2020-01-01"
        logger._file = None

        f = logger._get_file()
        assert logger._current_date == today
        assert f is not None

        # Write to the new file
        f.write('{"test":true}\n')
        logger.close()

        files = list(tmp_path.glob(f"events_{today}.jsonl"))
        assert len(files) == 1

    def test_file_naming_includes_date(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        logger.log("naming_test")
        logger.close()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        files = list(tmp_path.glob(f"events_{today}.jsonl"))
        assert len(files) == 1


class TestSizeRotation:
    def test_rotates_on_size_limit(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        logger._MAX_FILE_SIZE = 200  # Very small for testing

        for i in range(50):
            logger.log("filler", data="x" * 50)

        logger.close()
        files = list(tmp_path.glob("events_*.jsonl"))
        assert len(files) >= 2  # Should have rotated at least once

    def test_rotated_file_has_timestamp_suffix(self, tmp_path):
        logger = EventLogger(log_dir=str(tmp_path))
        logger._MAX_FILE_SIZE = 100

        for i in range(30):
            logger.log("filler", data="x" * 30)

        logger.close()
        # Check for files with _HHMMSS suffix pattern
        files = list(tmp_path.glob("events_*_??????.jsonl"))
        assert len(files) >= 1


class TestCleanup:
    def test_removes_old_log_files(self, tmp_path):
        # Create an old log file manually
        old_date = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d")
        old_file = tmp_path / f"events_{old_date}.jsonl"
        old_file.write_text('{"event":"old"}\n')

        logger = EventLogger(log_dir=str(tmp_path))
        logger.close()

        assert not old_file.exists()

    def test_keeps_recent_log_files(self, tmp_path):
        recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        recent_file = tmp_path / f"events_{recent_date}.jsonl"
        recent_file.write_text('{"event":"recent"}\n')

        logger = EventLogger(log_dir=str(tmp_path))
        logger.close()

        assert recent_file.exists()

    def test_cleanup_on_init(self, tmp_path):
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        old_file = tmp_path / f"events_{old_date}.jsonl"
        old_file.write_text('{"event":"old"}\n')

        # Creating the logger should trigger cleanup
        EventLogger(log_dir=str(tmp_path))
        assert not old_file.exists()

    def test_ignores_malformed_filenames(self, tmp_path):
        bad_file = tmp_path / "events_badfile.jsonl"
        bad_file.write_text('{"event":"bad"}\n')

        logger = EventLogger(log_dir=str(tmp_path))
        logger.close()
        # Should not crash, file stays (can't parse date)
        assert bad_file.exists()


class TestThreadSafety:
    def test_concurrent_writes(self, tmp_path):
        import threading

        logger = EventLogger(log_dir=str(tmp_path))
        errors = []

        def writer(thread_id):
            try:
                for i in range(20):
                    logger.log("concurrent", thread=thread_id, idx=i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logger.close()
        assert len(errors) == 0

        files = list(tmp_path.glob("events_*.jsonl"))
        total_lines = sum(len(f.read_text().strip().split('\n')) for f in files if f.read_text().strip())
        assert total_lines == 100  # 5 threads * 20 events
