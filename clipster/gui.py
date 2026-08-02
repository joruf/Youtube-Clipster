"""Owner of the Tk interpreter and of the two application windows.

The Tk root itself is never shown.  It only hosts:

* :class:`clipster.navwindow.NavWindow` - the small window that appears when a
  link is copied and drives one download from A to Z, and
* :class:`clipster.viewwindow.ViewWindow` - the large window with the download
  list, the settings and the about page.

Keeping the root hidden means both windows can be opened and closed
independently while the program keeps running in the system tray.

Every method must be called from the Tk main thread - worker threads go through
:class:`clipster.bridge.TkBridge`.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, List, Optional

from . import APP_SHORT_NAME, paths, theme
from .config import Config
from .history import HistoryEntry
from .i18n import Messages
from .logging_setup import get_logger
from .navwindow import NavWindow
from .theme import PAD
from .viewwindow import ViewWindow

log = get_logger(__name__)


class Gui:
    """Creates the Tk root, applies the dark theme and owns both windows."""

    def __init__(self, messages: Messages, config: Config, download_dir: Path) -> None:
        """
        :param messages: The active translation table.
        :param config: The live configuration (the settings page edits it).
        :param download_dir: Directory shown in the view window footer.
        """
        self.messages = messages
        self.config = config
        self.download_dir = download_dir

        #: Set by the application before :meth:`build_windows`.
        self.on_quit: Optional[Callable[[], None]] = None
        self.on_nav_closed: Optional[Callable[[], None]] = None
        self.on_view_closed: Optional[Callable[[], None]] = None
        self.on_play_entry: Optional[Callable[[HistoryEntry], None]] = None
        self.on_reveal_entry: Optional[Callable[[HistoryEntry], None]] = None
        self.on_delete_entry: Optional[Callable[[HistoryEntry], None]] = None
        self.on_clear_history: Optional[Callable[[], None]] = None
        self.on_open_folder: Optional[Callable[[], None]] = None
        self.on_submit_url: Optional[Callable[[str, str], None]] = None
        self.on_save_settings: Optional[Callable[[], None]] = None
        self.on_check_updates: Optional[Callable[[], None]] = None
        self.on_install_update: Optional[Callable[[], None]] = None
        self.on_open_result: Optional[Callable[[], None]] = None
        self.on_reveal_result: Optional[Callable[[], None]] = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_SHORT_NAME)
        self.palette = theme.apply(self.root)
        self.fonts = theme.fonts()
        self._icon = self._load_icon()

        self.nav: Optional[NavWindow] = None
        self.view: Optional[ViewWindow] = None

    # ------------------------------------------------------------------
    def _load_icon(self) -> Optional[tk.PhotoImage]:
        """Load the window icon, tolerating any platform quirk.

        :return: The icon, or ``None`` when it cannot be read.
        """
        icon_ico = paths.windows_icon_file()
        if paths.IS_WINDOWS and icon_ico.is_file():
            try:
                self.root.iconbitmap(default=str(icon_ico))
            except tk.TclError:  # pragma: no cover - old Tk
                pass
        icon_png = paths.icon_file()
        if not icon_png.is_file():
            return None
        try:
            image = tk.PhotoImage(file=str(icon_png))
        except tk.TclError:
            log.debug("Icon %s could not be loaded.", icon_png)
            return None
        try:
            self.root.iconphoto(True, image)
        except tk.TclError:  # pragma: no cover
            pass
        return image

    def build_windows(self) -> None:
        """Create both windows; call after the callbacks have been assigned."""
        self.nav = NavWindow(
            master=self.root,
            messages=self.messages,
            palette=self.palette,
            icon=self._icon,
            on_close=self._nav_closed,
            on_open_file=self._open_result,
            on_open_folder=self._reveal_result,
        )
        self.view = ViewWindow(
            master=self.root,
            messages=self.messages,
            palette=self.palette,
            config=self.config,
            icon=self._icon,
            on_close=self._view_closed,
            on_quit=self._quit,
            on_play_entry=self._play_entry,
            on_reveal_entry=self._reveal_entry,
            on_delete_entry=self._delete_entry,
            on_clear_history=self._clear_history,
            on_open_folder=self._open_folder,
            on_submit_url=self._submit_url,
            on_save_settings=self._save_settings,
            on_check_updates=self._check_updates,
            on_install_update=self._install_update,
        )

    # ------------------------------------------------------------------
    # Callback plumbing
    # ------------------------------------------------------------------
    def _quit(self) -> None:
        """Forward the quit request."""
        if self.on_quit is not None:
            self.on_quit()
        else:  # pragma: no cover - defensive
            self.root.quit()

    def _nav_closed(self) -> None:
        """Forward "the navigation window was closed"."""
        if self.on_nav_closed is not None:
            self.on_nav_closed()

    def _view_closed(self) -> None:
        """Forward "the view window was closed"."""
        if self.on_view_closed is not None:
            self.on_view_closed()

    def _play_entry(self, entry: HistoryEntry) -> None:
        """Forward the "play" button of a table row."""
        if self.on_play_entry is not None:
            self.on_play_entry(entry)

    def _delete_entry(self, entry: HistoryEntry) -> None:
        """Ask for confirmation, then forward the "delete" button of a row.

        Deleting removes the file from the disk, so it is never done silently.
        """
        question = self.messages.format("history_delete_confirm", name=entry.name)
        if not self.ask_yes_no(self.messages["history_delete"], question):
            return
        if self.on_delete_entry is not None:
            self.on_delete_entry(entry)

    def _reveal_entry(self, entry: HistoryEntry) -> None:
        """Forward the "folder" button of a table row."""
        if self.on_reveal_entry is not None:
            self.on_reveal_entry(entry)

    def _clear_history(self) -> None:
        """Ask for confirmation, then forward the clear request."""
        if not self.ask_yes_no(self.messages["history_clear"], self.messages["history_clear_confirm"]):
            return
        if self.on_clear_history is not None:
            self.on_clear_history()

    def _open_folder(self) -> None:
        """Forward the "open download folder" button."""
        if self.on_open_folder is not None:
            self.on_open_folder()

    def _submit_url(self, url: str, media_format: str) -> None:
        """Forward a URL pasted into the toolbar."""
        if self.on_submit_url is not None:
            self.on_submit_url(url, media_format)

    def _save_settings(self) -> None:
        """Forward the settings save request."""
        if self.on_save_settings is not None:
            self.on_save_settings()

    def _check_updates(self) -> None:
        """Forward the "look for a new version" button."""
        if self.on_check_updates is not None:
            self.on_check_updates()

    def _install_update(self) -> None:
        """Ask for confirmation, then forward the install request."""
        if not self.ask_yes_no(self.messages["update_install"],
                               self.messages["update_confirm"]):
            return
        if self.on_install_update is not None:
            self.on_install_update()

    def show_update_state(self, text: str, offer_install: bool, busy: bool = False) -> None:
        """Pass the update situation on to the about page.

        :param text: The line shown next to the button.
        :param offer_install: Turn the button into "install and restart".
        :param busy: Disable the button while something is running.
        :return: None
        """
        if self.view is not None:
            self.view.show_update_state(text, offer_install, busy)

    def _open_result(self) -> None:
        """Forward the "open file" button of the navigation window."""
        if self.on_open_result is not None:
            self.on_open_result()

    def _reveal_result(self) -> None:
        """Forward the "folder" button of the navigation window."""
        if self.on_reveal_result is not None:
            self.on_reveal_result()

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------
    def show_view(self, page: Optional[str] = None) -> None:
        """Show the large window.

        :param page: Optionally switch to ``downloads``, ``settings`` or ``about``.
        :return: None
        """
        if self.view is not None:
            self.view.show(page)

    def hide_view(self) -> None:
        """Hide the large window."""
        if self.view is not None:
            self.view.hide()

    def view_visible(self) -> bool:
        """Return ``True`` while the large window is on screen."""
        return self.view is not None and self.view.visible()

    def render_history(self, entries: List[HistoryEntry]) -> None:
        """Redraw the download table.

        :param entries: Every history entry, newest first.
        :return: None
        """
        if self.view is not None:
            self.view.render(entries, self.download_dir)

    # ------------------------------------------------------------------
    # Message boxes and notifications
    # ------------------------------------------------------------------
    def ask_yes_no(self, title: str, question: str) -> bool:
        """Show a yes/no question.

        :param title: The window title.
        :param question: The question text.
        :return: ``True`` when the user confirmed.
        """
        return bool(messagebox.askyesno(title=title, message=question, parent=self._dialog_parent()))

    def show_error(self, title: str, text: str) -> None:
        """Show a modal error box.

        :param title: The window title.
        :param text: The error message.
        :return: None
        """
        messagebox.showerror(title=title, message=text, parent=self._dialog_parent())

    def show_info(self, title: str, text: str) -> None:
        """Show a modal information box.

        :param title: The window title.
        :param text: The message.
        :return: None
        """
        messagebox.showinfo(title=title, message=text, parent=self._dialog_parent())

    def _dialog_parent(self) -> tk.Misc:
        """Return the best visible window to parent a message box on."""
        for window in (self.view, self.nav):
            if window is not None and window.visible():
                return window.window
        return self.root

    def toast(self, text: str, duration_ms: int = 4000) -> None:
        """Show a small notification window that closes itself.

        :param text: The notification text.
        :param duration_ms: Lifetime in milliseconds.
        :return: None
        """
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title(APP_SHORT_NAME)
        window.configure(background=self.palette.base)
        window.resizable(False, False)
        window.overrideredirect(True)
        window.attributes("-topmost", True)

        frame = ttk.Frame(window, style="Panel.TFrame", padding=PAD)
        frame.pack(fill="both", expand=True)
        tk.Frame(frame, background=self.palette.accent, width=3).pack(side="left", fill="y", padx=(0, PAD))
        ttk.Label(frame, text=text, style="Panel.TLabel", wraplength=320, justify="left").pack(
            side="left", anchor="w"
        )

        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        x = max(0, window.winfo_screenwidth() - width - 40)
        y = max(0, window.winfo_screenheight() - height - 80)
        window.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))
        window.deiconify()
        window.after(duration_ms, lambda: _safe_destroy(window))

    def destroy(self) -> None:
        """Tear down both windows and the Tk interpreter."""
        for window in (self.nav, self.view):
            if window is not None:
                window.destroy()
        try:
            self.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass


def _safe_destroy(window: tk.Misc) -> None:
    """Destroy a window, ignoring the case where it is already gone."""
    try:
        window.destroy()
    except tk.TclError:  # pragma: no cover
        pass


def show_startup_error(title: str, text: str) -> None:
    """Show an error before the windows exist (single instance, setup).

    Falls back to the console when no display is available.

    :param title: The window title.
    :param text: The error message.
    :return: None
    """
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title=title, message=text)
        root.destroy()
    except Exception:  # pragma: no cover - headless session
        log.error("%s: %s", title, text)
