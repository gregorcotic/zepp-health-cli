"""Small Ubuntu-oriented operational helpers for Zepp synchronization."""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


class SyncLock:
    """Advisory process lock. Kernel release makes abandoned locks non-stale."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.handle: TextIO | None = None

    def acquire(self, nonblocking: bool = True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            fcntl.flock(self.handle.fileno(), flags)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()} acquired_at={datetime.now(timezone.utc).isoformat()}\n")
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "SyncLock":
        if not self.acquire(nonblocking=False):
            raise RuntimeError("lock acquisition failed")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def lock_is_held(path: str | Path) -> bool:
    """Return whether another process currently holds the advisory lock."""
    lock = SyncLock(path)
    if not lock.acquire(nonblocking=True):
        return True
    lock.release()
    return False
