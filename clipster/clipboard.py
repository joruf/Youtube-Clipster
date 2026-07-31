"""Cross-platform clipboard access without third-party dependencies.

Backends, in order of preference:

* Windows - the Win32 clipboard API through :mod:`ctypes`
* Wayland  - ``wl-paste`` / ``wl-copy``
* X11      - ``xclip`` or ``xsel``
* macOS    - ``pbpaste`` / ``pbcopy``
* fallback - the clipboard of the running Tk interpreter
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, List, Optional

from . import paths
from .logging_setup import get_logger

log = get_logger(__name__)

_COMMAND_TIMEOUT = 5.0


def _which(name: str) -> Optional[str]:
    """Return the absolute path of an executable, or ``None``."""
    return shutil.which(name)


def _run(command: List[str], stdin_devnull: bool = False) -> Optional[str]:
    """Run a helper process and return its stdout.

    :param command: The argument vector to execute.
    :param stdin_devnull: Feed an empty stdin instead of inheriting it.
    :return: The captured stdout, or ``None`` when the call failed.
    """
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL if stdin_devnull else None,
            timeout=_COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Clipboard helper %s failed: %s", command[0], exc)
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------
# Windows backend
# ----------------------------------------------------------------------
def _win_open_clipboard(user32: Any, attempts: int = 10) -> bool:
    """Try to open the Windows clipboard, which other apps may hold briefly."""
    import time

    for _ in range(attempts):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _win_read() -> str:
    """Read Unicode text from the Windows clipboard."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    cf_unicodetext = 13
    if not _win_open_clipboard(user32):
        return ""
    try:
        if not user32.IsClipboardFormatAvailable(cf_unicodetext):
            return ""
        handle = user32.GetClipboardData(cf_unicodetext)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.c_wchar_p(pointer).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win_clear() -> bool:
    """Empty the Windows clipboard."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL

    if not _win_open_clipboard(user32):
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


class Clipboard:
    """Reads and clears the system clipboard using the best available backend."""

    def __init__(self, tk_widget: Any = None) -> None:
        """
        :param tk_widget: Optional Tk widget used as last-resort backend.
        """
        self._tk = tk_widget
        self._backend = self._detect_backend()
        self._read_failures = 0
        log.debug("Clipboard backend: %s", self._backend)

    @property
    def backend(self) -> str:
        """Return the name of the selected backend."""
        return self._backend

    def _detect_backend(self) -> str:
        """Pick the backend that fits the current OS and session type."""
        if paths.IS_WINDOWS:
            return "win32"
        if paths.IS_MACOS and _which("pbpaste"):
            return "pbcopy"
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        if wayland and _which("wl-paste"):
            return "wl-clipboard"
        if _which("xclip"):
            return "xclip"
        if _which("xsel"):
            return "xsel"
        if _which("wl-paste"):
            return "wl-clipboard"
        return "tk"

    def read(self) -> str:
        """Return the clipboard content as text (empty string when unavailable)."""
        try:
            text = self._read_raw()
        except Exception as exc:  # pragma: no cover - depends on desktop session
            self._read_failures += 1
            if self._read_failures <= 3:
                log.warning("Clipboard could not be read (%s): %s", self._backend, exc)
            return ""
        self._read_failures = 0
        return text or ""

    def _read_raw(self) -> str:
        """Dispatch the read to the selected backend."""
        if self._backend == "win32":
            return _win_read()
        if self._backend == "wl-clipboard":
            return _run(["wl-paste", "--no-newline", "--type", "text/plain"], stdin_devnull=True) or ""
        if self._backend == "xclip":
            return _run(["xclip", "-selection", "clipboard", "-o"], stdin_devnull=True) or ""
        if self._backend == "xsel":
            return _run(["xsel", "--clipboard", "--output"], stdin_devnull=True) or ""
        if self._backend == "pbcopy":
            return _run(["pbpaste"], stdin_devnull=True) or ""
        return self._tk_read()

    def _tk_read(self) -> str:
        """Read via the Tk interpreter (works on every platform Tk supports)."""
        if self._tk is None:
            return ""
        try:
            return str(self._tk.clipboard_get())
        except Exception:
            # Tk raises TclError for an empty clipboard or non-text content.
            return ""

    def clear(self) -> None:
        """Empty the clipboard so the same link is not detected twice."""
        try:
            if self._backend == "win32":
                _win_clear()
            elif self._backend == "wl-clipboard":
                if _which("wl-copy"):
                    _run(["wl-copy", "--clear"], stdin_devnull=True)
            elif self._backend == "xclip":
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=b"",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=_COMMAND_TIMEOUT,
                    check=False,
                )
            elif self._backend == "xsel":
                _run(["xsel", "--clipboard", "--clear"], stdin_devnull=True)
            elif self._backend == "pbcopy":
                subprocess.run(
                    ["pbcopy"],
                    input=b"",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=_COMMAND_TIMEOUT,
                    check=False,
                )
            elif self._tk is not None:
                self._tk.clipboard_clear()
        except Exception as exc:  # pragma: no cover - depends on desktop session
            log.debug("Clipboard could not be cleared: %s", exc)
