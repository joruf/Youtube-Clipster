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
from typing import Callable, List, Optional, Sequence

from . import APP_SHORT_NAME, APP_WINDOW_TITLE, i18n, paths, theme
from .config import Config
from .discover import DiscoverTrack
from .history import HistoryEntry
from .i18n import Messages
from .logging_setup import get_logger
from .navwindow import NavWindow
from .qrview import draw_qr
from .theme import PAD, PAD_SMALL
from .viewwindow import ViewWindow

log = get_logger(__name__)

#: Edge length of the QR code in the share dialog, in pixels.  Big enough that
#: another phone reads it off the screen without anyone leaning in.
_SHARE_QR_SIZE = 260


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
        self.on_retry_entry: Optional[Callable[[HistoryEntry], None]] = None
        self.on_delete_entry: Optional[Callable[[HistoryEntry], None]] = None
        self.on_hide_entry: Optional[Callable[[HistoryEntry], None]] = None
        self.on_clear_history: Optional[Callable[[], None]] = None
        self.on_open_folder: Optional[Callable[[], None]] = None
        self.on_submit_url: Optional[Callable[[str, str], None]] = None
        self.on_save_settings: Optional[Callable[[], None]] = None
        self.on_check_updates: Optional[Callable[[], None]] = None
        self.on_install_update: Optional[Callable[[], None]] = None
        self.on_open_result: Optional[Callable[[], None]] = None
        self.on_reveal_result: Optional[Callable[[], None]] = None
        self.on_discover_refresh: Optional[Callable[[], None]] = None
        self.on_discover_download: Optional[Callable[[DiscoverTrack], None]] = None
        self.on_discover_extend: Optional[Callable[[DiscoverTrack], None]] = None
        self.on_discover_like: Optional[Callable[[DiscoverTrack], None]] = None
        self.on_discover_dislike: Optional[Callable[[DiscoverTrack], None]] = None
        self.on_phone_apply: Optional[Callable[[bool, str, int], dict]] = None
        self.on_phone_new_token: Optional[Callable[[], dict]] = None
        self.on_phone_state: Optional[Callable[[], dict]] = None
        self.on_show_terms: Optional[Callable[[], None]] = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_WINDOW_TITLE)
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
            on_retry_entry=self._retry_entry,
            on_delete_entry=self._delete_entry,
            on_hide_entry=self._hide_entry,
            on_clear_history=self._clear_history,
            on_open_folder=self._open_folder,
            on_submit_url=self._submit_url,
            on_save_settings=self._save_settings,
            on_check_updates=self._check_updates,
            on_install_update=self._install_update,
            on_discover_refresh=self._discover_refresh,
            on_discover_download=self._discover_download,
            on_discover_extend=self._discover_extend,
            on_discover_like=self._discover_like,
            on_discover_dislike=self._discover_dislike,
            on_show_terms=self._show_terms,
            on_phone_apply=self._phone_apply,
            on_phone_new_token=self._phone_new_token,
            on_phone_state=self._phone_state,
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

    def _retry_entry(self, entry: HistoryEntry) -> None:
        """Forward the "retry" button of a failed table row."""
        if self.on_retry_entry is not None:
            self.on_retry_entry(entry)

    def _delete_entry(self, entry: HistoryEntry) -> None:
        """Forward the "delete" button of a table row (file + list, no prompt)."""
        if self.on_delete_entry is not None:
            self.on_delete_entry(entry)

    def _hide_entry(self, entry: HistoryEntry) -> None:
        """Forward the "hide" button: drop the row, keep the file on disk."""
        if self.on_hide_entry is not None:
            self.on_hide_entry(entry)

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
        if self.on_install_update is None:
            return
        if not self.ask_yes_no(self.messages["update_install"], self.messages["update_confirm"]):
            return
        self.on_install_update()

    def _discover_refresh(self) -> None:
        """Forward the Discover refresh button."""
        if self.on_discover_refresh is not None:
            self.on_discover_refresh()

    def _discover_extend(self, track: DiscoverTrack) -> None:
        """Forward a request to top up the Discover playlist."""
        if self.on_discover_extend is not None:
            self.on_discover_extend(track)

    def _discover_like(self, track: DiscoverTrack) -> None:
        """Forward a Streaming thumbs-up."""
        if self.on_discover_like is not None:
            self.on_discover_like(track)

    def _discover_dislike(self, track: DiscoverTrack) -> None:
        """Forward a Streaming thumbs-down."""
        if self.on_discover_dislike is not None:
            self.on_discover_dislike(track)

    def _discover_download(self, track: DiscoverTrack) -> None:
        """Forward a Discover auto-download request."""
        if self.on_discover_download is not None:
            self.on_discover_download(track)

    def _phone_apply(self, enabled: bool, bind: str, port: int) -> dict:
        """Forward the phone interface settings to the application.

        :param enabled: Whether the interface should serve.
        :param bind: The interface to listen on.
        :param port: The TCP port to listen on.
        :return: The new state, or an empty dictionary without a handler.
        """
        if self.on_phone_apply is None:
            return {}
        return self.on_phone_apply(enabled, bind, port)

    def _phone_new_token(self) -> dict:
        """Ask the application for a new phone interface token.

        :return: The new state, or an empty dictionary without a handler.
        """
        if self.on_phone_new_token is None:
            return {}
        return self.on_phone_new_token()

    def _phone_state(self) -> dict:
        """Read the state of the phone interface.

        :return: The state, or an empty dictionary without a handler.
        """
        if self.on_phone_state is None:
            return {}
        return self.on_phone_state()

    def _show_terms(self) -> None:
        """Forward the About-page request to show the terms documents."""
        if self.on_show_terms is not None:
            self.on_show_terms()

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

        :param page: Optionally switch to ``discover``, ``downloads``,
            ``settings`` or ``about``.
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

    def ask_terms_acceptance(
        self,
        title_key: str,
        body_key: str,
        checkbox_key: str = "terms_checkbox",
        accept_key: str = "terms_accept",
        decline_key: str = "terms_decline",
    ) -> bool:
        """Show versioned terms with a required checkbox before Accept.

        The dialog can switch between English and German display text without
        changing ``config.language``.  The acceptance checkbox state is kept.

        :param title_key: Locale key for the dialog title.
        :param body_key: Locale key for the scrollable terms body.
        :param checkbox_key: Locale key for the agreement checkbox.
        :param accept_key: Locale key for the Accept button.
        :param decline_key: Locale key for the Decline button.
        :return: ``True`` when the user checked the box and accepted.
        """
        result = {"accepted": False}
        parent = self._dialog_parent()
        dialog = tk.Toplevel(parent)
        dialog.withdraw()
        dialog.transient(parent)
        dialog.configure(background=self.palette.base)
        dialog.resizable(True, True)

        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=PAD)
        frame.pack(fill="both", expand=True)

        title_label = ttk.Label(frame, text="", style="Title.TLabel")
        title_label.pack(anchor="w")
        lang_row = self._pack_terms_language_row(frame)

        text = self._build_terms_text_widget(frame)
        agreed = tk.BooleanVar(value=False)
        accept_btn = ttk.Button(frame, text="", style="Accent.TButton", state="disabled")

        def _sync_accept(*_args: object) -> None:
            accept_btn.configure(state="normal" if agreed.get() else "disabled")

        checkbox = ttk.Checkbutton(
            frame,
            text="",
            variable=agreed,
            command=_sync_accept,
            style="TCheckbutton",
        )
        checkbox.pack(anchor="w", pady=(PAD, 0))

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(PAD, 0))
        decline_btn = ttk.Button(buttons, text="", style="Row.TButton")

        def accept() -> None:
            if not agreed.get():
                return
            result["accepted"] = True
            dialog.destroy()

        def decline() -> None:
            result["accepted"] = False
            dialog.destroy()

        accept_btn.configure(command=accept)
        decline_btn.configure(command=decline)
        accept_btn.pack(side="right")
        decline_btn.pack(side="right", padx=(0, PAD))

        def apply_language(code: str) -> None:
            msgs = i18n.load(code)
            title = msgs[title_key]
            body = msgs[body_key]
            dialog.title(title)
            title_label.configure(text=title)
            self._set_terms_text(text, body)
            checkbox.configure(text=msgs[checkbox_key])
            accept_btn.configure(text=msgs[accept_key])
            decline_btn.configure(text=msgs[decline_key])
            self._refresh_terms_language_labels(lang_row, msgs)
            _sync_accept()

        lang_var = self._bind_terms_language_row(lang_row, apply_language)
        apply_language(lang_var.get())

        dialog._terms_text = text  # type: ignore[attr-defined]
        dialog._terms_lang_var = lang_var  # type: ignore[attr-defined]
        dialog._terms_agreed = agreed  # type: ignore[attr-defined]
        dialog._terms_apply_language = apply_language  # type: ignore[attr-defined]

        dialog.protocol("WM_DELETE_WINDOW", decline)
        dialog.update_idletasks()
        width = max(560, dialog.winfo_reqwidth())
        height = max(420, dialog.winfo_reqheight())
        dialog.geometry("{0}x{1}".format(width, height))
        dialog.deiconify()
        dialog.grab_set()
        dialog.focus_set()
        dialog.wait_window()
        return bool(result["accepted"])

    def show_terms_document(
        self,
        title_key: str,
        body_keys: Sequence[str],
        close_key: str = "terms_close",
    ) -> None:
        """Show a read-only scrollable terms document (About page).

        Supports switching the displayed language without changing
        ``config.language``.

        :param title_key: Locale key for the dialog title.
        :param body_keys: Locale keys joined with blank lines into the body.
        :param close_key: Locale key for the Close button.
        :return: None
        """
        parent = self._dialog_parent()
        dialog = tk.Toplevel(parent)
        dialog.withdraw()
        dialog.transient(parent)
        dialog.configure(background=self.palette.base)
        dialog.resizable(True, True)

        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=PAD)
        frame.pack(fill="both", expand=True)
        title_label = ttk.Label(frame, text="", style="Title.TLabel")
        title_label.pack(anchor="w")
        lang_row = self._pack_terms_language_row(frame)

        text = self._build_terms_text_widget(frame)
        close_btn = ttk.Button(frame, text="", style="Accent.TButton", command=dialog.destroy)
        close_btn.pack(anchor="e", pady=(PAD, 0))

        def apply_language(code: str) -> None:
            msgs = i18n.load(code)
            title = msgs[title_key]
            body = "\n\n".join(msgs[key] for key in body_keys)
            dialog.title(title)
            title_label.configure(text=title)
            self._set_terms_text(text, body)
            close_btn.configure(text=msgs[close_key])
            self._refresh_terms_language_labels(lang_row, msgs)

        lang_var = self._bind_terms_language_row(lang_row, apply_language)
        apply_language(lang_var.get())

        dialog._terms_text = text  # type: ignore[attr-defined]
        dialog._terms_lang_var = lang_var  # type: ignore[attr-defined]
        dialog._terms_apply_language = apply_language  # type: ignore[attr-defined]

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        dialog.geometry("{0}x{1}".format(max(560, dialog.winfo_reqwidth()), max(420, dialog.winfo_reqheight())))
        dialog.deiconify()
        dialog.grab_set()
        dialog.wait_window()

    def _terms_initial_language(self) -> str:
        """Return the UI language to use when a terms dialog opens."""
        code = (self.messages.language or i18n.DEFAULT_LANGUAGE).lower()
        return code if code in ("en", "de") else i18n.DEFAULT_LANGUAGE

    def _pack_terms_language_row(self, frame: ttk.Frame) -> ttk.Frame:
        """Create the English/Deutsch toggle row (widgets bound later)."""
        row = ttk.Frame(frame, style="Panel.TFrame")
        row.pack(anchor="w", pady=(PAD_SMALL, 0))
        return row

    def _bind_terms_language_row(
        self,
        row: ttk.Frame,
        apply_language: Callable[[str], None],
    ) -> tk.StringVar:
        """Fill the language row and wire it to ``apply_language``.

        :param row: Empty language toggle row.
        :param apply_language: Called with ``en`` or ``de`` when the user toggles.
        :return: The language StringVar (initialised from the current UI language).
        """
        lang_var = tk.StringVar(value=self._terms_initial_language())
        buttons: List[ttk.Radiobutton] = []

        def on_toggle() -> None:
            apply_language(lang_var.get())

        for code, key in (("en", "terms_lang_en"), ("de", "terms_lang_de")):
            button = ttk.Radiobutton(
                row,
                text=self.messages[key],
                value=code,
                variable=lang_var,
                command=on_toggle,
                style="TRadiobutton",
            )
            button.pack(side="left", padx=(0, PAD))
            buttons.append(button)
        row._terms_lang_buttons = buttons  # type: ignore[attr-defined]
        return lang_var

    def _refresh_terms_language_labels(self, row: ttk.Frame, msgs: Messages) -> None:
        """Update English/Deutsch control captions for the active display locale."""
        buttons = getattr(row, "_terms_lang_buttons", None)
        if not buttons:
            return
        for button, key in zip(buttons, ("terms_lang_en", "terms_lang_de")):
            button.configure(text=msgs[key])

    def _build_terms_text_widget(self, frame: ttk.Frame) -> tk.Text:
        """Create the scrollable terms body Text widget."""
        text_wrap = ttk.Frame(frame, style="Panel.TFrame")
        text_wrap.pack(fill="both", expand=True, pady=(PAD, 0))
        scroll = ttk.Scrollbar(text_wrap)
        scroll.pack(side="right", fill="y")
        text = tk.Text(
            text_wrap,
            wrap="word",
            height=18,
            width=72,
            yscrollcommand=scroll.set,
            background=self.palette.panel,
            foreground=self.palette.text,
            insertbackground=self.palette.text,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            padx=8,
            pady=8,
        )
        text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=text.yview)
        return text

    @staticmethod
    def _set_terms_text(text: tk.Text, body: str) -> None:
        """Replace the scrollable terms body without touching other dialog state."""
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", body)
        text.configure(state="disabled")

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

    def show_share_code(self, url: str, title: str = "") -> None:
        """Show a QR code somebody else can scan to get this song.

        The code carries a plain YouTube link, so any camera app reads it;
        Clipster's own scanner is only what puts it straight into a queue.

        :param url: The link to encode.
        :param title: Song title, shown above the code.
        :return: None
        """
        parent = self._dialog_parent()
        dialog = tk.Toplevel(parent)
        dialog.withdraw()
        dialog.transient(parent)
        dialog.title(self.messages["share_title"])
        dialog.configure(background=self.palette.base)
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=PAD)
        frame.pack(fill="both", expand=True)
        if title:
            ttk.Label(frame, text=title, style="Title.TLabel", wraplength=320,
                      justify="left").pack(anchor="w")

        canvas = tk.Canvas(frame, highlightthickness=0, background=self.palette.base,
                           width=_SHARE_QR_SIZE, height=_SHARE_QR_SIZE)
        canvas.pack(pady=(PAD, PAD))
        drawn = draw_qr(canvas, url, _SHARE_QR_SIZE, dark=self.palette.base, light="#ffffff")

        ttk.Label(
            frame,
            text=self.messages["share_hint"] if drawn else self.messages["share_missing"],
            style="Panel.Muted.TLabel",
            wraplength=320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(frame, text=url, style="Panel.Muted.TLabel", wraplength=320,
                  justify="left").pack(anchor="w", pady=(PAD_SMALL, 0))

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(PAD, 0))

        def copy_link() -> None:
            """Put the link on the clipboard, for people without a camera to hand."""
            try:
                dialog.clipboard_clear()
                dialog.clipboard_append(url)
            except tk.TclError:  # pragma: no cover - no clipboard on this display
                log.debug("The share link could not be copied", exc_info=True)

        ttk.Button(buttons, text=self.messages["share_copy"], style="Row.TButton",
                   command=copy_link).pack(side="left")
        ttk.Button(buttons, text=self.messages["share_close"], style="Accent.TButton",
                   command=dialog.destroy).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        dialog.deiconify()
        dialog.grab_set()
        dialog.focus_set()

    def toast(self, text: str, duration_ms: int = 4000) -> None:
        """Show a small notification window that closes itself.

        :param text: The notification text.
        :param duration_ms: Lifetime in milliseconds.
        :return: None
        """
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title(APP_WINDOW_TITLE)
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
