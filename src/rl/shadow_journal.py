"""Atomic JSONL persistence for PPO shadow decisions."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from pathlib import Path

from src.rl.shadow_schema import ShadowRoutingDecision


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def log_decision(path: str, decision: ShadowRoutingDecision) -> None:
    """Append one decision as a complete line using an atomic replace.

    A process-local lock avoids duplicate work in threads, while a companion
    lock file and POSIX ``flock`` serialize the complete read-copy-replace
    transaction across processes. The temporary file is created beside the
    journal so replacement remains atomic on the same filesystem.
    """
    if not isinstance(decision, ShadowRoutingDecision):
        raise TypeError("decision must be a ShadowRoutingDecision")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    lock_path = target.with_name(f".{target.name}.lock")
    with _lock_for(target):
        with lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                existing = target.read_bytes() if target.exists() else b""
                temp_name: str | None = None
                fd, temp_name = tempfile.mkstemp(
                    prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
                )
                try:
                    with os.fdopen(fd, "wb") as temp:
                        temp.write(existing)
                        temp.write(line.encode("utf-8"))
                        temp.flush()
                        os.fsync(temp.fileno())
                    os.replace(temp_name, target)
                    try:
                        dir_fd = os.open(target.parent, os.O_DIRECTORY)
                    except (AttributeError, OSError):
                        dir_fd = None
                    if dir_fd is not None:
                        try:
                            os.fsync(dir_fd)
                        finally:
                            os.close(dir_fd)
                finally:
                    if temp_name is not None:
                        try:
                            os.unlink(temp_name)
                        except FileNotFoundError:
                            pass
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
