"""Early setup window shown while ``run.py`` checks or installs dependencies.

Without a visible window the first start looks frozen: ``run.bat`` hands over to
``pythonw.exe``, so there is no console either, and the user is left staring at
nothing while yt-dlp and FFmpeg download.  This module opens a small dark window
- the same palette and logo as the application itself, so it is recognisable -
as soon as a display is available, and reports a failed setup in a dialog
instead of on an invisible stderr.

Only the standard library is used: at this point the virtual environment may not
exist yet, so nothing here may import a third-party package.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk
from typing import Optional

from . import APP_TITLE, paths
from .logging_setup import get_logger
from .theme import PALETTE, PAD

log = get_logger(__name__)

#: Longest status line shown; longer texts are cut so the window cannot grow.
MAX_STATUS_LENGTH = 96

#: Width of the logo on the splash, in pixels.
_LOGO_SIZE = 64


def trim_status(text: str, max_length: int = MAX_STATUS_LENGTH) -> str:
    """Shorten a status line so it always fits on one line.

    :param text: The raw status text.
    :param max_length: Maximum number of characters to keep.
    :return: The text, ellipsised in the middle when too long.
    """
    clean = " ".join(str(text).split())
    if len(clean) <= max_length:
        return clean
    keep = max(1, (max_length - 3) // 2)
    return "{0}...{1}".format(clean[:keep], clean[-keep:])


def _load_logo(widget: tk.Misc) -> Optional[tk.PhotoImage]:
    """Load the application logo, scaled down for the splash.

    Tk's own PNG reader is used because Pillow is not installed yet at this
    point.  ``subsample`` only divides by whole numbers, which is why the
    512 px source is reduced by a factor of eight.

    :param widget: Any widget, used to bind the image to the right interpreter.
    :return: The scaled image, or ``None`` when it cannot be loaded.
    """
    icon = paths.icon_file()
    if not icon.is_file():
        return None
    try:
        image = tk.PhotoImage(master=widget, file=str(icon))
    except tk.TclError as exc:  # pragma: no cover - Tk without PNG support
        log.debug("Setup logo could not be loaded: %s", exc)
        return None
    factor = max(1, image.width() // _LOGO_SIZE)
    if factor > 1:
        image = image.subsample(factor, factor)
    return image


class SetupSplash:
    """Non-blocking status window for the bootstrap dependency phase."""

    def __init__(self, *, title: str, heading: str, wait_hint: str, busy_hint: str = "") -> None:
        self._busy_hint = busy_hint or wait_hint
        self._root = tk.Tk()
        self._root.title(title)
        self._root.resizable(False, False)
        self._root.configure(background=PALETTE.base)
        try:
            self._root.attributes("-topmost", True)
        except tk.TclError:
            pass

        # Keep a reference: Tk drops images that are only held by a widget.
        self._logo = _load_logo(self._root)
        if self._logo is not None:
            try:
                self._root.iconphoto(True, self._logo)
            except tk.TclError:  # pragma: no cover - window manager without icons
                pass

        frame = tk.Frame(self._root, background=PALETTE.base, padx=PAD + 6, pady=PAD + 6)
        frame.pack(fill="both", expand=True)

        if self._logo is not None:
            tk.Label(frame, image=self._logo, background=PALETTE.base).grid(
                row=0, column=0, rowspan=3, sticky="n", padx=(0, PAD + 2)
            )

        base_font = tkfont.nametofont("TkDefaultFont")
        heading_font = base_font.copy()
        heading_font.configure(weight="bold", size=max(12, int(base_font.cget("size")) + 2))
        self._heading_font = heading_font

        tk.Label(
            frame,
            text=heading,
            font=heading_font,
            background=PALETTE.base,
            foreground=PALETTE.text,
            anchor="w",
        ).grid(row=0, column=1, sticky="we")

        tk.Label(
            frame,
            text=wait_hint,
            background=PALETTE.base,
            foreground=PALETTE.muted,
            wraplength=420,
            justify="left",
            anchor="w",
        ).grid(row=1, column=1, sticky="we", pady=(6, 12))

        style = ttk.Style(self._root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - exotic Tk build
            pass
        style.configure(
            "Setup.Horizontal.TProgressbar",
            troughcolor=PALETTE.elevated,
            background=PALETTE.accent,
            bordercolor=PALETTE.border,
            lightcolor=PALETTE.accent,
            darkcolor=PALETTE.accent,
            thickness=8,
        )
        self._bar = ttk.Progressbar(
            frame,
            mode="indeterminate",
            length=420,
            style="Setup.Horizontal.TProgressbar",
        )
        self._bar.grid(row=2, column=1, sticky="we")
        self._bar.start(12)

        self._status = tk.Label(
            frame,
            text=trim_status(wait_hint),
            background=PALETTE.base,
            foreground=PALETTE.muted,
            anchor="w",
        )
        self._status.grid(row=3, column=1, sticky="we", pady=(10, 0))

        # A half-finished installation must not be abortable through the window
        # chrome - but a close button that does nothing at all reads as a hung
        # program, so it answers instead.
        self._root.protocol("WM_DELETE_WINDOW", self._refuse_close)

        self._centre()
        self._root.update()

    def _refuse_close(self) -> None:
        """Answer a click on the window close button without stopping the setup."""
        self.set_status(self._busy_hint)

    def _centre(self) -> None:
        """Place the window in the upper third of the screen."""
        self._root.update_idletasks()
        width = max(520, self._root.winfo_reqwidth())
        height = max(150, self._root.winfo_reqheight())
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self._root.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))

    @classmethod
    def try_open(
        cls, *, title: str, heading: str, wait_hint: str, busy_hint: str = ""
    ) -> Optional["SetupSplash"]:
        """Return a splash when a display is available, otherwise ``None``.

        :param title: The window title.
        :param heading: The bold headline.
        :param wait_hint: The explanatory line below the headline.
        :param busy_hint: Shown when the close button is clicked.
        :return: The open splash or ``None``.
        """
        try:
            return cls(title=title, heading=heading, wait_hint=wait_hint, busy_hint=busy_hint)
        except Exception as exc:  # pragma: no cover - headless / missing Tk
            log.debug("Setup splash unavailable: %s", exc)
            return None

    def set_status(self, text: str) -> None:
        """Show what is being installed right now and keep the window alive.

        :param text: The status line from the installer.
        :return: None
        """
        try:
            self._status.configure(text=trim_status(text))
            self._root.update()
        except tk.TclError:  # pragma: no cover - window already gone
            pass

    def close(self) -> None:
        """Destroy the splash window."""
        try:
            self._bar.stop()
        except tk.TclError:  # pragma: no cover - window already gone
            pass
        try:
            self._root.destroy()
        except tk.TclError:  # pragma: no cover - window already gone
            pass


def open_setup_splash(messages) -> Optional[SetupSplash]:
    """Open the bootstrap splash using translated strings when possible.

    :param messages: The loaded :class:`~clipster.i18n.Messages` catalogue.
    :return: The open splash or ``None`` when no display is available.
    """
    title = messages.get("setup_title", APP_TITLE)
    heading = messages.get("setup_heading", "Setting up YouTube Clipster")
    wait_hint = messages.get(
        "setup_please_wait",
        "Please wait - dependencies are being checked and installed.",
    )
    busy_hint = messages.get("setup_still_running", "Please wait - the installation is still running.")
    return SetupSplash.try_open(title=title, heading=heading, wait_hint=wait_hint, busy_hint=busy_hint)


def show_setup_failure(title: str, text: str) -> bool:
    """Report an unfinished setup in a dialog.

    Used when the console is invisible (``pythonw.exe`` on Windows, a desktop
    launcher on Linux): without this the program would simply never appear.

    :param title: The dialog title.
    :param text: The prepared failure summary.
    :return: True when the dialog was shown.
    """
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title=title, message=text, parent=root)
        return True
    except Exception as exc:  # pragma: no cover - headless session
        log.debug("Setup failure dialog unavailable: %s", exc)
        return False
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:  # pragma: no cover - already gone
                pass
