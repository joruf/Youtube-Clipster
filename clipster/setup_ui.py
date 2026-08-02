"""Early setup splash shown while ``run.py`` checks or installs dependencies.

Without a visible window, double-clicking ``run.py`` looks frozen while packages
download.  This module opens a small Tk dialog as soon as a display is available.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from . import APP_TITLE
from .logging_setup import get_logger

log = get_logger(__name__)


class SetupSplash:
    """Non-blocking status window for the bootstrap dependency phase."""

    def __init__(self, *, title: str, heading: str, wait_hint: str) -> None:
        self._root = tk.Tk()
        self._root.title(title)
        self._root.resizable(False, False)
        try:
            self._root.attributes("-topmost", True)
        except tk.TclError:
            pass

        frame = ttk.Frame(self._root, padding=20)
        frame.pack(fill="both", expand=True)
        heading_label = ttk.Label(frame, text=heading)
        try:
            import tkinter.font as tkfont

            # Prefer the OS default face (Segoe UI on Windows) over a hard-coded
            # family that may be missing on Linux/macOS installers.
            base = tkfont.nametofont("TkDefaultFont")
            self._heading_font = base.copy()
            self._heading_font.configure(weight="bold", size=max(11, int(base.cget("size"))))
            heading_label.configure(font=self._heading_font)
        except (tk.TclError, TypeError, ValueError):
            self._heading_font = None
        heading_label.pack(anchor="w")
        self._status = ttk.Label(frame, text=wait_hint, wraplength=420, justify="left")
        self._status.pack(anchor="w", pady=(10, 8))
        self._bar = ttk.Progressbar(frame, mode="indeterminate", length=420)
        self._bar.pack(fill="x")
        self._bar.start(12)

        # Ignore the window chrome close button — bootstrap must finish and
        # destroy the splash itself (double-click / pythonw on Windows).
        self._root.protocol("WM_DELETE_WINDOW", lambda: None)

        self._root.update_idletasks()
        width = max(460, self._root.winfo_reqwidth())
        height = max(140, self._root.winfo_reqheight())
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self._root.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))
        self._root.update()

    @classmethod
    def try_open(cls, *, title: str, heading: str, wait_hint: str) -> Optional["SetupSplash"]:
        """Return a splash when a display is available, otherwise ``None``."""
        try:
            return cls(title=title, heading=heading, wait_hint=wait_hint)
        except Exception as exc:  # pragma: no cover - headless / missing Tk
            log.debug("Setup splash unavailable: %s", exc)
            return None

    def set_status(self, text: str) -> None:
        """Update the status line and keep the window responsive."""
        try:
            self._status.configure(text=text)
            self._root.update()
        except tk.TclError:
            pass

    def close(self) -> None:
        """Destroy the splash window."""
        try:
            self._bar.stop()
        except tk.TclError:
            pass
        try:
            self._root.destroy()
        except tk.TclError:
            pass


def open_setup_splash(messages) -> Optional[SetupSplash]:
    """Open the bootstrap splash using translated strings when possible."""
    title = messages.get("setup_title", APP_TITLE)
    heading = messages.get("setup_heading", "Setting up YouTube Clipster")
    wait_hint = messages.get("setup_please_wait", "Please wait — dependencies are being checked and installed.")
    return SetupSplash.try_open(title=title, heading=heading, wait_hint=wait_hint)
