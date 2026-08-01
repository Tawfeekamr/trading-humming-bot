"""Atomic JSONL persistence for PPO shadow decisions."""
from __future__ import annotations

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

    A read-copy-write snapshot is protected by a process-local lock and is
    committed with ``os.replace``. The temporary file is created beside the
    journal so replacement remains atomic on the same filesystem.
    """
    if not isinstance(decision, ShadowRoutingDecision):
        raise TypeError("decision must be a ShadowRoutingDecision")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    with _lock_for(target):
        existing = target.read_bytes() if target.exists() else b""
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
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
