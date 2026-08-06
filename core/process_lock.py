# -*- coding: utf-8 -*-
"""Khóa tiến trình liên nền tảng, tự nhả khi process thoát/crash."""

from __future__ import annotations

import os
from typing import Optional


class ProcessLock:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._handle: Optional[object] = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
            handle.seek(0)
            self._handle = handle
            return True
        except (OSError, IOError):
            handle.close()
            return False

    def release(self):
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        finally:
            handle.close()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Process lock is already held: {self.path}")
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.release()

