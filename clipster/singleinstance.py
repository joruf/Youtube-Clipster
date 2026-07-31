"""Single-instance guard.

POSIX uses an advisory ``flock`` on the lock file, Windows a named mutex.  Both
are released by the operating system when the process dies, so a crashed
instance can never leave a stale lock behind.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from . import paths
from .logging_setup import get_logger

log = get_logger(__name__)

_MUTEX_NAME = "Local\\LoresoftYouTubeClipster"
_ERROR_ALREADY_EXISTS = 183


class AlreadyRunning(RuntimeError):
    """Raised when another instance already holds the lock."""

    def __init__(self, pid: Optional[int]) -> None:
        """
        :param pid: PID of the running instance, if it could be determined.
        """
        self.pid = pid
        super().__init__("Another instance is running" + ("" if pid is None else " (PID {0})".format(pid)))


class SingleInstance:
    """Context manager that guarantees only one running YouTube Clipster."""

    def __init__(self, lock_path: Optional[Path] = None) -> None:
        """
        :param lock_path: Optional explicit lock file; defaults to the install dir.
        """
        self.lock_path = lock_path or paths.lock_file()
        self._handle: Any = None
        self._mutex: Any = None

    # ------------------------------------------------------------------
    def _read_pid(self) -> Optional[int]:
        """Return the PID stored in the lock file, if readable."""
        try:
            content = self.lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            return int(content)
        except ValueError:
            return None

    def _write_pid(self) -> None:
        """Store the current PID in the lock file for diagnostics."""
        try:
            self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            log.debug("Could not write PID into %s: %s", self.lock_path, exc)

    # ------------------------------------------------------------------
    def acquire(self) -> None:
        """Take the lock.

        :raises AlreadyRunning: When another instance is already running.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if paths.IS_WINDOWS:
            self._acquire_windows()
        else:
            self._acquire_posix()
        self._write_pid()
        log.debug("Instance lock acquired (%s, PID %s)", self.lock_path, os.getpid())

    def _acquire_posix(self) -> None:
        """Acquire an exclusive, non-blocking ``flock`` on the lock file."""
        import fcntl

        pid_before = self._read_pid()
        handle = open(self.lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AlreadyRunning(pid_before)
        self._handle = handle

    def _acquire_windows(self) -> None:
        """Create a named mutex; failure means a second instance."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        last_error = ctypes.get_last_error()
        if not handle:
            log.warning("Could not create the instance mutex (error %s)", last_error)
            return
        if last_error == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise AlreadyRunning(self._read_pid())
        self._mutex = handle

    # ------------------------------------------------------------------
    def release(self) -> None:
        """Release the lock and remove the lock file."""
        if self._handle is not None:
            try:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            except Exception:  # pragma: no cover - best effort during shutdown
                pass
            try:
                self._handle.close()
            except Exception:  # pragma: no cover
                pass
            self._handle = None
        if self._mutex is not None:
            try:
                import ctypes

                ctypes.WinDLL("kernel32").CloseHandle(self._mutex)  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                pass
            self._mutex = None
        try:
            self.lock_path.unlink()
        except OSError:
            pass
        log.debug("Instance lock released.")

    # ------------------------------------------------------------------
    def __enter__(self) -> "SingleInstance":
        """Acquire the lock when entering the ``with`` block."""
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Release the lock when leaving the ``with`` block."""
        self.release()
