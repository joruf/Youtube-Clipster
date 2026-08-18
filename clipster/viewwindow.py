"""The large window: the download list, the settings and the about page.

Layout follows a modern dark downloader: a menu row, a toolbar with a URL field
and a format selector, a sidebar with status filters and counts, and a table of
every download with name, length, size and date plus per-row actions.

Downloads never require this window - it is a viewer and a settings editor.  The
:class:`clipster.navwindow.NavWindow` handles the actual flow.

Every method must be called from the Tk main thread.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional, Tuple

from . import (
    APP_AUTHOR,
    APP_SHORT_NAME,
    APP_URL,
    APP_VERSION,
    APP_WEBSITE,
    dependencies,
    netmode,
    paths,
    theme,
    tooltip,
)
from .config import Config
from .discover import DiscoverTrack
from .discover_page import DiscoverPage
from .phone_page import PhonePage
from .scroller import Scroller as _Scroller
from .history import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_OK,
    HistoryEntry,
    format_duration,
    format_size,
    format_timestamp,
)
from .i18n import Messages
from .logging_setup import get_logger
from .theme import PAD, PAD_SMALL

log = get_logger(__name__)

#: Marks in front of a row, per status.
_MARKS = {STATUS_OK: "✓", STATUS_FAILED: "✕", STATUS_CANCELED: "–"}

#: Starting column widths of the table in pixels; the name column takes the
#: rest.  The user can drag the others, so these are defaults, not constants.
_COL_BADGE = 48
_COL_NAME_MIN = 150
_COL_DURATION = 62
_COL_SIZE = 78
_COL_DATE = 118
#: Gap between two row buttons.
_ROW_BUTTON_GAP = 4
#: How far a dragged column may be taken, in pixels.
_COL_MIN_WIDTH = 40
_COL_MAX_WIDTH = 320
#: Width of the grip between two column headings.
_GRIP_WIDTH = 6

#: The sidebar filters, as ``(key, message key)``.
_FILTERS = (
    ("all", "filter_all"),
    (STATUS_OK, "filter_ready"),
    (STATUS_FAILED, "filter_failed"),
    (STATUS_CANCELED, "filter_canceled"),
)

#: The table headings, as ``(sort key, message key, grid column, anchor)``.
#: The grid column is what ties a heading to its values and to its grip.
_COLUMNS = (
    ("name", "column_name", 1, "w"),
    ("duration", "column_duration", 2, "e"),
    ("size", "column_size", 3, "e"),
    ("date", "column_date", 4, "w"),
)

#: How each column is sorted.  Names read as text, everything else as a number
#: or a timestamp, so that 9 MB sorts below 10 MB instead of above it.
_SORT_KEYS = {
    "name": lambda entry: entry.name.casefold(),
    "duration": lambda entry: entry.duration,
    "size": lambda entry: entry.size,
    "date": lambda entry: entry.finished_at,
}

#: Which direction a column starts in when it is first clicked.  Text reads
#: best from A, numbers and dates from the largest - the newest download is the
#: one being looked for.
_SORT_DESCENDING_FIRST = {"name": False, "duration": True, "size": True, "date": True}

#: Appended to the active heading.
_SORT_MARKS = {False: " ▲", True: " ▼"}


def _shorten(text: str, limit: int = 120) -> str:
    """Return ``text`` truncated to ``limit`` characters with an ellipsis."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


class ViewWindow:
    """The download list, the settings editor and the about page."""

    def __init__(
        self,
        master: tk.Misc,
        messages: Messages,
        palette: theme.Palette,
        config: Config,
        icon: Optional[tk.PhotoImage],
        on_close: Callable[[], None],
        on_quit: Callable[[], None],
        on_play_entry: Callable[[HistoryEntry], None],
        on_reveal_entry: Callable[[HistoryEntry], None],
        on_retry_entry: Callable[[HistoryEntry], None],
        on_delete_entry: Callable[[HistoryEntry], None],
        on_hide_entry: Callable[[HistoryEntry], None],
        on_clear_history: Callable[[], None],
        on_open_folder: Callable[[], None],
        on_submit_url: Callable[[str, str], None],
        on_save_settings: Callable[[], None],
        on_check_updates: Callable[[], None],
        on_install_update: Callable[[], None],
        on_discover_refresh: Callable[[], None],
        on_discover_download: Callable[[DiscoverTrack], None],
        on_discover_extend: Callable[[DiscoverTrack], None],
        on_discover_like: Callable[[DiscoverTrack], None],
        on_discover_dislike: Callable[[DiscoverTrack], None],
        on_show_terms: Callable[[], None],
        on_phone_apply: Callable[[bool, str, int], dict],
        on_phone_new_token: Callable[[], dict],
        on_phone_state: Callable[[], dict],
    ) -> None:
        """
        :param master: The hidden Tk root.
        :param messages: The active translation table.
        :param palette: The colour scheme.
        :param config: The live configuration, edited in place by the settings page.
        :param icon: Window icon, or ``None``.
        :param on_close: Called when the window is closed.
        :param on_quit: Called by the quit button.
        :param on_play_entry: Play the file of a row in the default player.
        :param on_reveal_entry: Open the folder of a row.
        :param on_retry_entry: Start the same download again after a failure.
        :param on_delete_entry: Delete the file of a row and the row itself.
        :param on_hide_entry: Remove the row from the list but keep the file.
        :param on_clear_history: Empty the list.
        :param on_open_folder: Open the download folder.
        :param on_submit_url: Start a download for a pasted URL and format.
        :param on_save_settings: Persist the configuration after an edit.
        :param on_check_updates: Ask GitHub whether a newer version exists.
        :param on_install_update: Fetch the new version and restart.
        :param on_discover_refresh: Run a Discover search from history or local media.
        :param on_discover_download: Auto-download a Discover track with defaults.
        :param on_discover_extend: Top up the Discover list from the current track.
        :param on_discover_like: Thumbs-up a Streaming track.
        :param on_discover_dislike: Thumbs-down a Streaming track.
        :param on_phone_apply: Save the phone interface settings and restart it.
        :param on_phone_new_token: Replace the phone interface token.
        :param on_phone_state: Read the state of the phone interface.
        :param on_show_terms: Open the terms-of-use documents from About.
        """
        self.messages = messages
        self.palette = palette
        self.config = config
        self.fonts = theme.fonts()

        self._on_close = on_close
        self._on_quit = on_quit
        self._on_play_entry = on_play_entry
        #: Optional: play a row inside Clipster instead of handing it over to
        #: the system.  Bound to a double click on the row.
        self.on_play_here: Optional[Callable[[HistoryEntry], None]] = None
        #: Optional: show the QR code another Clipster can scan for this song.
        #: Bound to a right click on the row.
        self.on_share_entry: Optional[Callable[[HistoryEntry], None]] = None
        self._on_reveal_entry = on_reveal_entry
        self._on_retry_entry = on_retry_entry
        self._on_delete_entry = on_delete_entry
        self._on_hide_entry = on_hide_entry
        self._on_clear_history = on_clear_history
        self._on_open_folder = on_open_folder
        self._on_submit_url = on_submit_url
        self._on_save_settings = on_save_settings
        self._on_check_updates = on_check_updates
        self._on_install_update = on_install_update
        self._on_discover_refresh = on_discover_refresh
        self._on_discover_download = on_discover_download
        self._on_discover_extend = on_discover_extend
        self._on_discover_like = on_discover_like
        self._on_discover_dislike = on_discover_dislike
        self._on_show_terms = on_show_terms
        self._on_phone_apply = on_phone_apply
        self._on_phone_new_token = on_phone_new_token
        self._on_phone_state = on_phone_state
        #: Width the row action buttons need; measured, because labels differ
        #: by language and a fixed number would clip or waste space.
        self._actions_width = 0
        self._entries: List[HistoryEntry] = []
        self._filter = "all"
        #: Which column the table is sorted by, and in which direction.
        self._sort_key = "date"
        self._sort_desc = True
        #: Current width of the fixed columns, in pixels; changed by dragging.
        self._col_widths: Dict[int, int] = {
            2: _COL_DURATION,
            3: _COL_SIZE,
            4: _COL_DATE,
        }
        self._page = "discover"
        self._counts: Dict[str, int] = {}
        self._column_labels: Dict[str, ttk.Label] = {}
        self._grips: Dict[int, tk.Frame] = {}
        #: Set while a column grip is being dragged: ``(column, x, width)``.
        self._drag: Optional[Tuple[int, int, int]] = None
        self._filter_buttons: Dict[str, ttk.Button] = {}
        self._menu_buttons: Dict[str, ttk.Button] = {}
        self._pages: Dict[str, ttk.Frame] = {}
        self._vars: Dict[str, tk.Variable] = {}
        #: Visible table rows as ``(leading separator or None, row frame, entry)``.
        self._row_items: List[Tuple[Optional[ttk.Separator], ttk.Frame, HistoryEntry]] = []
        self.discover: Optional[DiscoverPage] = None
        self.phone: Optional[PhonePage] = None

        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.title(APP_SHORT_NAME)
        self.window.configure(background=palette.base)
        self.window.minsize(960, 620)
        self.window.geometry("1120x720")
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if icon is not None:
            try:
                self.window.iconphoto(False, icon)
            except tk.TclError:  # pragma: no cover
                pass

        self._build()
        self.select_page("discover")
        # Packing pages can remap a Toplevel on some window managers; stay hidden
        # until the application decides to show the window.
        self.window.withdraw()

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    @staticmethod
    def _menu_entries() -> Tuple[Tuple[str, str], ...]:
        """Return the pages this platform offers, as ``(key, label key)``.

        The pages themselves are identical everywhere; only this list differs,
        and only where a page would have nothing to do.  Remote hands the phone
        a link to *this* computer - on Android the program already runs on the
        phone, so there is nothing left to point at.

        :return: The menu entries in display order.
        """
        entries = [
            ("discover", "page_discover"),
            ("downloads", "page_downloads"),
            ("phone", "page_phone"),
            ("settings", "page_settings"),
            ("about", "page_about"),
        ]
        if paths.is_termux():
            entries = [item for item in entries if item[0] != "phone"]
        return tuple(entries)

    def _build(self) -> None:
        """Create the menu row and the pages."""
        menu = ttk.Frame(self.window, style="Toolbar.TFrame")
        menu.pack(fill="x")
        for key, label in self._menu_entries():
            button = ttk.Button(
                menu, text=self.messages[label], style="Menu.TButton", command=lambda k=key: self.select_page(k)
            )
            button.pack(side="left")
            self._menu_buttons[key] = button
        ttk.Separator(self.window, orient="horizontal").pack(fill="x")

        self._container = ttk.Frame(self.window, style="TFrame")
        self._container.pack(fill="both", expand=True)

        self._pages["downloads"] = self._build_downloads(self._container)
        self.discover = DiscoverPage(
            self._container,
            messages=self.messages,
            palette=self.palette,
            config=self.config,
            fonts=self.fonts,
            on_refresh=self._on_discover_refresh,
            on_download=self._on_discover_download,
            on_extend=self._on_discover_extend,
            on_like=self._on_discover_like,
            on_dislike=self._on_discover_dislike,
            on_mode_changed=self._discover_mode_changed,
        )
        self._pages["discover"] = self.discover
        if "phone" in self._menu_buttons:
            self.phone = PhonePage(
                self._container,
                messages=self.messages,
                palette=self.palette,
                config=self.config,
                fonts=self.fonts,
                on_apply=self._on_phone_apply,
                on_new_token=self._on_phone_new_token,
                on_state=self._on_phone_state,
                on_copy=self._copy_to_clipboard,
                on_firewall_hint=self._on_phone_firewall_hint,
            )
            self._pages["phone"] = self.phone
        self._pages["settings"] = self._build_settings(self._container)
        self._pages["about"] = self._build_about(self._container)

    def _copy_to_clipboard(self, text: str) -> None:
        """Put ``text`` on the clipboard.

        :param text: The address or command to copy.
        :return: None
        """
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
        except tk.TclError:  # pragma: no cover - no clipboard on this display
            log.debug("The clipboard could not be written")

    def _on_phone_firewall_hint(self, port: int):
        """Return the firewall description and command for ``port``.

        Imported here so the Phone page and the console wizard share one
        implementation instead of describing firewalls twice.

        :param port: The port that has to be reachable.
        :return: ``(description, command)``.
        """
        from .phonesetup import firewall_hint

        return firewall_hint(port)

    def select_page(self, key: str) -> None:
        """Show one of the pages.

        :param key: ``downloads``, ``discover``, ``settings`` or ``about``.
        :return: None
        """
        if key not in self._pages:
            return
        for name, page in self._pages.items():
            page.pack_forget()
            self._menu_buttons[name].configure(style="Menu.TButton")
        self._pages[key].pack(fill="both", expand=True)
        self._menu_buttons[key].configure(style="MenuSelected.TButton")
        self._page = key
        if key == "settings":
            self._load_settings()
        if key == "discover" and self.discover is not None:
            self.discover.reload_from_config()
        if self.phone is not None:
            # Polling costs nothing while nobody is looking at the page.
            if key == "phone":
                self.phone.start_polling()
            else:
                self.phone.stop_polling()

    @property
    def current_page(self) -> str:
        """Return the active page key (``downloads``, ``discover``, …)."""
        return self._page

    def _discover_mode_changed(self, mode: str) -> None:
        """Persist the Discover search mode chosen on the page."""
        self.config.discover_mode = mode
        self._on_save_settings()

    # ------------------------------------------------------------------
    # Downloads page
    # ------------------------------------------------------------------
    def _build_downloads(self, master: tk.Misc) -> ttk.Frame:
        """Create the toolbar, the sidebar and the table.

        :param master: The page container.
        :return: The page frame.
        """
        page = ttk.Frame(master, style="TFrame")

        toolbar = ttk.Frame(page, style="Toolbar.TFrame", padding=(PAD, PAD_SMALL))
        toolbar.pack(fill="x")
        self._url_var = tk.StringVar(value="")
        entry = ttk.Entry(toolbar, textvariable=self._url_var, font=self.fonts["body"])
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._submit_url())

        self._toolbar_format = tk.StringVar(value=self.messages["format_mp3"])
        format_box = ttk.Combobox(
            toolbar,
            state="readonly",
            width=18,
            textvariable=self._toolbar_format,
            values=[self.messages["format_mp3"], self.messages["format_mp4"]],
            font=self.fonts["body"],
        )
        format_box.pack(side="left", padx=(PAD_SMALL, PAD_SMALL))
        ttk.Button(
            toolbar, text=self.messages["toolbar_download"], style="Accent.TButton", command=self._submit_url
        ).pack(side="left")

        body = ttk.Frame(page, style="TFrame")
        body.pack(fill="both", expand=True)

        sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=(0, PAD_SMALL))
        sidebar.pack(side="left", fill="y")
        sidebar.configure(width=170)
        for key, label in _FILTERS:
            button = ttk.Button(
                sidebar,
                text=self.messages[label],
                style="Sidebar.TButton",
                width=20,
                command=lambda k=key: self.set_filter(k),
            )
            button.pack(fill="x", padx=PAD_SMALL, pady=1)
            self._filter_buttons[key] = button

        ttk.Separator(body, orient="vertical").pack(side="left", fill="y")

        table = ttk.Frame(body, style="TFrame", padding=(PAD, PAD_SMALL, PAD, 0))
        table.pack(side="left", fill="both", expand=True)

        self._actions_width = self._measure_actions(table)
        self._widen_for_headings(table)

        self._scroller = _Scroller(table, self.palette)
        self._scroller.pack(fill="both", expand=True)

        header = ttk.Frame(self._scroller.stack, style="Panel.TFrame")
        header.pack(side="top", fill="x")
        self._header = header
        self._configure_columns(header)
        # Empty grid columns are distributed differently from ones holding a
        # widget, which left the headings out of step with their values. These
        # two spacers give the heading strip the same structure as a row.
        # The badge is narrower than its column, so a thin spacer with the same
        # gap mirrors it; the action column has no gap and must claim its full
        # measured width.
        ttk.Frame(header, style="TFrame", width=1, height=1).grid(
            row=0, column=0, sticky="ew", padx=(0, PAD_SMALL)
        )
        ttk.Frame(header, style="TFrame", width=self._actions_width, height=1).grid(
            row=0, column=5, sticky="ew"
        )
        for sort_key, key, column, anchor in _COLUMNS:
            # width=1 keeps the heading from widening its column: the column
            # sizes come from the widths above, and the label just stretches
            # into whatever it gets. Otherwise a long heading such as "GRÖSSE"
            # would push the header out of step with the values below.
            label = ttk.Label(header, text="", style="Muted.TLabel", anchor=anchor, width=1)
            label.grid(row=0, column=column, sticky="ew", padx=(0, PAD_SMALL), pady=(0, 6))
            label.configure(cursor="hand2")
            label.bind("<Button-1>", lambda _e, k=sort_key: self.set_sort(k))
            tooltip.attach(
                label,
                self.messages["column_sort_tip"],
                background=self.palette.elevated,
                foreground=self.palette.text,
            )
            self._column_labels[sort_key] = label
        self._build_grips(header)
        self._paint_columns()
        ttk.Separator(self._scroller.stack, orient="horizontal").pack(side="top", fill="x")

        footer = ttk.Frame(page, style="Toolbar.TFrame", padding=(PAD, PAD_SMALL))
        footer.pack(fill="x")
        self._folder_label = ttk.Label(footer, text="", style="Panel.Muted.TLabel")
        self._folder_label.pack(side="left")
        ttk.Button(footer, text=self.messages["window_quit"], command=self._on_quit).pack(side="right")
        self._clear_button = ttk.Button(
            footer, text=self.messages["history_clear"], command=self._on_clear_history
        )
        self._clear_button.pack(side="right", padx=(0, PAD_SMALL))
        ttk.Button(footer, text=self.messages["window_open_folder"], command=self._on_open_folder).pack(
            side="right", padx=(0, PAD_SMALL)
        )
        return page

    def show_update_state(self, text: str, offer_install: bool, busy: bool = False) -> None:
        """Report the update situation on the about page.

        :param text: The line shown next to the button.
        :param offer_install: Turn the button into "install and restart".
        :param busy: Disable the button while something is running.
        :return: None
        """
        self._update_label.configure(text=text)
        self._update_button.configure(
            text=self.messages["update_install"] if offer_install else self.messages["update_check"],
            command=self._on_install_update if offer_install else self._on_check_updates,
            style="Accent.TButton" if offer_install else "Row.TButton",
        )
        try:
            self._update_button.state(["disabled"] if busy else ["!disabled"])
        except tk.TclError:  # pragma: no cover
            pass

    def _measure_actions(self, master: tk.Misc) -> int:
        """Return the width the row action buttons occupy, in pixels.

        Built once, measured and thrown away.  The header has no widget in that
        column, so the width has to be reserved explicitly - otherwise the
        weighted name column swallows the difference and every heading sits
        left of its values.  Measuring beats a constant, because "Abspielen" is
        wider than "Play".

        :param master: Any widget of the right window, used as a parent.
        :return: The required width including the gaps between the buttons.
        """
        probe = ttk.Frame(master, style="TFrame")
        for index, key in enumerate(
            ("history_retry", "history_play", "history_folder", "history_hide", "history_delete")
        ):
            ttk.Button(probe, text=self.messages[key], style="Row.TButton").pack(
                side="left", padx=(_ROW_BUTTON_GAP if index else 0, 0)
            )
        # The assembled frame is measured, not the sum of its parts: only a
        # layout pass knows the real geometry.
        probe.update_idletasks()
        width = probe.winfo_reqwidth()
        probe.destroy()
        return width

    def _widen_for_headings(self, master: tk.Misc) -> None:
        """Grow the starting widths until every heading is readable.

        The headings are clickable now, so a clipped one is worse than a few
        pixels of extra column: "GRÖSSE ▼" has to be legible, not "RÖSSE ▼".
        Measured rather than guessed, because the words differ per language and
        the sort mark only appears on the active column.

        :param master: Any widget of the right window, used as a parent.
        :return: None
        """
        probe = ttk.Label(master, style="Muted.TLabel")
        for _sort_key, message_key, column, _anchor in _COLUMNS:
            if column not in self._col_widths:
                continue
            probe.configure(text=self.messages[message_key].upper() + _SORT_MARKS[True])
            probe.update_idletasks()
            needed = probe.winfo_reqwidth() + PAD_SMALL
            self._col_widths[column] = max(self._col_widths[column], needed)
        probe.destroy()

    def _configure_columns(self, frame: tk.Misc) -> None:
        """Apply the shared column geometry to a header or row frame.

        :param frame: The frame whose columns are configured.
        :return: None
        """
        frame.columnconfigure(0, minsize=_COL_BADGE, weight=0)
        # Only the name column grows, so every other column stays aligned with
        # the header no matter how wide the window is - and so a column that is
        # dragged narrower hands its space to the names.
        frame.columnconfigure(1, weight=1, minsize=_COL_NAME_MIN)
        for column, width in self._col_widths.items():
            frame.columnconfigure(column, minsize=width, weight=0)
        frame.columnconfigure(5, minsize=self._actions_width, weight=0)

    # ------------------------------------------------------------------
    # Column widths
    # ------------------------------------------------------------------
    def _build_grips(self, header: tk.Misc) -> None:
        """Put a drag handle on the right edge of every fixed column.

        The handles are *placed*, not gridded: the header shares its grid with
        every row, and an extra cell there would take the two out of step.

        :param header: The heading strip.
        :return: None
        """
        for _sort_key, _message_key, column, _anchor in _COLUMNS:
            if column not in self._col_widths:
                continue  # The name column takes what is left; nothing to drag.
            grip = tk.Frame(
                header,
                background=self.palette.border,
                cursor="sb_h_double_arrow",
                width=_GRIP_WIDTH,
            )
            grip.bind("<Button-1>", lambda event, c=column: self._start_column_drag(event, c))
            grip.bind("<B1-Motion>", self._drag_column)
            grip.bind("<ButtonRelease-1>", self._end_column_drag)
            tooltip.attach(
                grip,
                self.messages["column_resize_tip"],
                background=self.palette.elevated,
                foreground=self.palette.text,
            )
            self._grips[column] = grip
        header.bind("<Configure>", lambda _event: self._place_grips(), add="+")

    def _place_grips(self) -> None:
        """Move every handle onto the right edge of its column."""
        header = getattr(self, "_header", None)
        if header is None:  # pragma: no cover - built together with the table
            return
        for column, grip in self._grips.items():
            try:
                bbox = header.grid_bbox(column, 0)
            except tk.TclError:  # pragma: no cover - window going away
                return
            if not bbox:
                continue
            left, _top, width, _height = bbox
            grip.place(x=left + width - _GRIP_WIDTH // 2, y=0,
                       width=_GRIP_WIDTH, relheight=1.0)
            grip.lift()

    def _start_column_drag(self, event: tk.Event, column: int) -> None:
        """Remember where a drag began.

        :param event: The button press on the handle.
        :param column: The grid column the handle belongs to.
        :return: None
        """
        self._drag = (column, int(event.x_root), self._col_widths[column])

    def _drag_column(self, event: tk.Event) -> None:
        """Resize the dragged column to follow the pointer.

        :param event: The motion event.
        :return: None
        """
        if self._drag is None:  # pragma: no cover - motion without a press
            return
        column, origin, width = self._drag
        self.set_column_width(column, width + int(event.x_root) - origin)

    def _end_column_drag(self, _event: tk.Event) -> None:
        """Finish a drag and settle the handles."""
        self._drag = None
        self._place_grips()

    def set_column_width(self, column: int, width: int) -> None:
        """Give one column a new width, within what the table can carry.

        :param column: The grid column, as listed in :data:`_COLUMNS`.
        :param width: The wanted width in pixels; clamped.
        :return: None
        """
        if column not in self._col_widths:
            return
        wanted = max(_COL_MIN_WIDTH, min(_COL_MAX_WIDTH, int(width)))
        if wanted == self._col_widths[column]:
            return
        self._col_widths[column] = wanted
        # Only one column changed, so the header and the rows that are already
        # mounted are nudged instead of rebuilt - a drag arrives many times a
        # second, and rebuilding the table on every pixel would crawl.
        frames: List[tk.Misc] = [self._header]
        frames.extend(row for _sep, row, _entry in self._row_items)
        for frame in frames:
            try:
                frame.columnconfigure(column, minsize=wanted)
            except tk.TclError:  # pragma: no cover - row destroyed meanwhile
                continue
        self._place_grips()

    # ------------------------------------------------------------------
    # Sorting and filtering
    # ------------------------------------------------------------------
    def set_sort(self, key: str) -> None:
        """Sort the table by one column, or turn its direction around.

        Clicking the column that is already active reverses it; a different
        column starts in the direction that is useful for its kind of value.

        :param key: A sort key from :data:`_COLUMNS`.
        :return: None
        """
        if key not in _SORT_KEYS:  # pragma: no cover - keys come from _COLUMNS
            return
        if key == self._sort_key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = _SORT_DESCENDING_FIRST[key]
        self._paint_columns()
        self._paint_rows()

    def _paint_columns(self) -> None:
        """Write the headings, marking the one the table is sorted by."""
        for sort_key, message_key, _column, _anchor in _COLUMNS:
            label = self._column_labels.get(sort_key)
            if label is None:  # pragma: no cover - built together with the table
                continue
            text = self.messages[message_key].upper()
            if sort_key == self._sort_key:
                text += _SORT_MARKS[self._sort_desc]
            label.configure(text=text)

    def set_filter(self, key: str) -> None:
        """Restrict the table to one status.

        :param key: ``all`` or one of the history statuses.
        :return: None
        """
        self._filter = key
        self._paint_filters()
        self._paint_rows()

    def _submit_url(self) -> None:
        """Hand the URL from the toolbar to the download pipeline."""
        url = self._url_var.get().strip()
        if not url:
            return
        media_format = "mp3" if self._toolbar_format.get() == self.messages["format_mp3"] else "mp4"
        self._url_var.set("")
        self._on_submit_url(url, media_format)

    def render(self, entries: List[HistoryEntry], download_dir: Path) -> None:
        """Redraw the table and the sidebar counts.

        Hide/delete of a single entry updates only that row so the list does not
        flash empty while every widget is rebuilt.

        :param entries: Every history entry, newest first.
        :param download_dir: Shown in the footer.
        :return: None
        """
        new_entries = list(entries)
        removed = self._single_removed_entry(self._entries, new_entries)
        self._entries = new_entries
        self._counts = {
            "all": len(self._entries),
            STATUS_OK: sum(1 for e in self._entries if e.status == STATUS_OK),
            STATUS_FAILED: sum(1 for e in self._entries if e.status == STATUS_FAILED),
            STATUS_CANCELED: sum(1 for e in self._entries if e.status == STATUS_CANCELED),
        }
        self._folder_label.configure(
            text="{0} {1}".format(self.messages["window_download_dir"], download_dir)
        )
        try:
            self._clear_button.state(["!disabled"] if self._entries else ["disabled"])
        except tk.TclError:  # pragma: no cover
            pass
        self._paint_filters()
        if removed is not None and self._remove_row(removed):
            return
        self._paint_rows()

    @staticmethod
    def _single_removed_entry(
        old: List[HistoryEntry], new: List[HistoryEntry]
    ) -> Optional[HistoryEntry]:
        """Return the one entry dropped from ``old`` when ``new`` is the rest.

        :param old: Previous history list.
        :param new: Updated history list.
        :return: The removed entry, or ``None`` when more than a single drop.
        """
        if len(old) != len(new) + 1:
            return None
        removed: Optional[HistoryEntry] = None
        oi = ni = 0
        while oi < len(old) and ni < len(new):
            if old[oi] is new[ni] or old[oi] == new[ni]:
                oi += 1
                ni += 1
                continue
            if removed is not None:
                return None
            removed = old[oi]
            oi += 1
        if oi < len(old):
            if removed is not None or oi != len(old) - 1:
                return None
            removed = old[oi]
        return removed

    def _paint_filters(self) -> None:
        """Update the sidebar labels, counts and selection."""
        for key, label in _FILTERS:
            button = self._filter_buttons.get(key)
            if button is None:  # pragma: no cover
                continue
            count = self._counts.get(key, 0)
            button.configure(
                text="{0}    {1}".format(self.messages[label], count),
                style="SidebarSelected.TButton" if key == self._filter else "Sidebar.TButton",
            )

    def _visible_entries(self) -> List[HistoryEntry]:
        """Return the entries matching the active filter, in the sorted order.

        The sort is stable, so rows that compare equal - two downloads of the
        same size, say - keep the order the history has them in.
        """
        if self._filter == "all":
            entries = list(self._entries)
        else:
            entries = [entry for entry in self._entries if entry.status == self._filter]
        return sorted(entries, key=_SORT_KEYS[self._sort_key], reverse=self._sort_desc)

    def _paint_rows(self) -> None:
        """Rebuild the table rows."""
        self._scroller.clear()
        self._row_items = []
        entries = self._visible_entries()
        if not entries:
            message = "history_empty" if not self._entries else "filter_empty"
            ttk.Label(
                self._scroller.body,
                text=self.messages[message],
                style="Panel.Muted.TLabel",
                wraplength=520,
                justify="left",
            ).pack(anchor="w", padx=PAD_SMALL, pady=PAD)
            return
        for index, entry in enumerate(entries):
            self._add_row(entry, index)
        self._scroller.to_top()

    def _remove_row(self, entry: HistoryEntry) -> bool:
        """Destroy the widgets for one visible entry; leave the others mounted.

        :param entry: The history entry that left the list.
        :return: ``True`` when the table was updated without a full rebuild.
        """
        index = next(
            (
                i
                for i, (_sep, _row, item) in enumerate(self._row_items)
                if item is entry or item == entry
            ),
            -1,
        )
        if index < 0:
            # Filtered out or never painted — sidebar counts already refreshed.
            return True

        sep, row, _item = self._row_items[index]
        for widget in (sep, row):
            if widget is None:
                continue
            try:
                widget.destroy()
            except tk.TclError:  # pragma: no cover
                pass
        del self._row_items[index]

        # The first visible row must not keep a leading separator.
        if index == 0 and self._row_items:
            first_sep, first_row, first_entry = self._row_items[0]
            if first_sep is not None:
                try:
                    first_sep.destroy()
                except tk.TclError:  # pragma: no cover
                    pass
                self._row_items[0] = (None, first_row, first_entry)

        if not self._row_items:
            message = "history_empty" if not self._entries else "filter_empty"
            ttk.Label(
                self._scroller.body,
                text=self.messages[message],
                style="Panel.Muted.TLabel",
                wraplength=520,
                justify="left",
            ).pack(anchor="w", padx=PAD_SMALL, pady=PAD)

        try:
            self._scroller._canvas.configure(scrollregion=self._scroller._canvas.bbox("all"))
        except tk.TclError:  # pragma: no cover
            pass
        return True

    def _add_row(self, entry: HistoryEntry, index: int) -> None:
        """Append one entry to the table.

        :param entry: The entry to render.
        :param index: Position, used for the separator.
        :return: None
        """
        separator: Optional[ttk.Separator] = None
        if index:
            separator = ttk.Separator(self._scroller.body, orient="horizontal")
            separator.pack(fill="x")

        row = ttk.Frame(self._scroller.body, style="Panel.TFrame", padding=(0, PAD_SMALL))
        row.pack(fill="x")
        self._configure_columns(row)

        colour = self.palette.status_colour(entry.status)

        badge = tk.Label(
            row,
            text=(entry.media_format or "?").upper(),
            background=self.palette.accent if entry.succeeded else self.palette.elevated,
            foreground=self.palette.on_accent if entry.succeeded else self.palette.muted,
            font=self.fonts["badge"],
            padx=6,
            pady=2,
            borderwidth=0,
        )
        badge.grid(row=0, column=0, sticky="nw", padx=(0, PAD_SMALL), pady=(1, 0))

        name_cell = ttk.Frame(row, style="Panel.TFrame")
        name_cell.grid(row=0, column=1, sticky="new", padx=(0, PAD_SMALL))
        title_line = ttk.Frame(name_cell, style="Panel.TFrame")
        title_line.pack(fill="x", anchor="w")
        tk.Label(
            title_line,
            text=_MARKS.get(entry.status, ""),
            background=self.palette.panel,
            foreground=colour,
            font=self.fonts["bold"],
        ).pack(side="left", anchor="n", padx=(0, 6))
        shown = _shorten(entry.name)
        name_label = ttk.Label(
            title_line,
            text=shown,
            style="Panel.Bold.TLabel",
            justify="left",
        )
        name_label.pack(side="left", anchor="nw", fill="x", expand=True)
        # A name that had to be cut short says nothing on its own, so the full
        # one is kept within reach. Names that fit get a silent tip, which
        # ``rewrap`` gives something to say as soon as the column is too narrow.
        name_tip = tooltip.attach(
            name_label,
            entry.name if shown != entry.name else "",
            background=self.palette.elevated,
            foreground=self.palette.text,
        )

        problem = self._problem_text(entry)
        problem_label: Optional[ttk.Label] = None
        if problem:
            style = "Panel.Danger.TLabel" if entry.status == STATUS_FAILED else "Panel.Warning.TLabel"
            problem_label = ttk.Label(name_cell, text=problem, style=style, justify="left")
            problem_label.pack(anchor="w", fill="x", padx=(18, 0), pady=(2, 0))

        # The name column is the only one that grows, so the wrap width has to
        # follow the cell instead of being a fixed number of pixels.  Names are
        # additionally clamped to two lines to keep the row heights even.
        def rewrap(
            event: tk.Event,
            first: ttk.Label = name_label,
            second: Optional[ttk.Label] = problem_label,
            full: str = entry.name,
            tip: tooltip.Tooltip = name_tip,
        ) -> None:
            """Keep the name length and the wrap width in sync with the cell."""
            width = max(120, event.width - 24)
            # The name stays on one line: measuring a single line is exact,
            # while predicting Tk's word wrapping never quite matches and left
            # the rows at uneven heights.  The status mark shares the line, so
            # its width is subtracted.
            fitted = self._fit_line(full, width - 26)
            first.configure(text=fitted)
            tip.set_text(full if fitted != full else "")
            if second is not None:
                second.configure(wraplength=width)

        name_cell.bind("<Configure>", rewrap)

        ttk.Label(
            row, text=format_duration(entry.duration), style="Panel.Muted.TLabel", anchor="ne"
        ).grid(row=0, column=2, sticky="new", padx=(0, PAD_SMALL))
        ttk.Label(
            row,
            text=format_size(entry.size) if entry.succeeded else "-",
            style="Panel.Muted.TLabel",
            anchor="ne",
        ).grid(row=0, column=3, sticky="new", padx=(0, PAD_SMALL))
        ttk.Label(row, text=format_timestamp(entry), style="Panel.Muted.TLabel", anchor="nw").grid(
            row=0, column=4, sticky="new", padx=(0, PAD_SMALL)
        )

        actions = ttk.Frame(row, style="Panel.TFrame")
        actions.grid(row=0, column=5, sticky="ne")
        available = entry.file_path() is not None

        retry_button = ttk.Button(
            actions, text=self.messages["history_retry"], style="Row.TButton",
            command=lambda e=entry: self._on_retry_entry(e),
        )
        retry_button.pack(side="left")
        play_button = ttk.Button(
            actions, text=self.messages["history_play"], style="Row.TButton",
            command=lambda e=entry: self._on_play_entry(e),
        )
        play_button.pack(side="left", padx=(_ROW_BUTTON_GAP, 0))
        folder_button = ttk.Button(
            actions, text=self.messages["history_folder"], style="Row.TButton",
            command=lambda e=entry: self._on_reveal_entry(e),
        )
        folder_button.pack(side="left", padx=(_ROW_BUTTON_GAP, 0))
        ttk.Button(
            actions,
            text=self.messages["history_hide"],
            style="Row.TButton",
            command=lambda e=entry: self._on_hide_entry(e),
        ).pack(side="left", padx=(_ROW_BUTTON_GAP, 0))
        # Deleting stays possible for entries whose file is already gone, so a
        # failed or stale row can be cleared away.
        ttk.Button(
            actions, text=self.messages["history_delete"], style="Row.TButton",
            command=lambda e=entry: self._on_delete_entry(e),
        ).pack(side="left", padx=(_ROW_BUTTON_GAP, 0))

        disabled = []
        if not entry.can_retry():
            disabled.append(retry_button)
        if not available:
            disabled.extend((play_button, folder_button))
        for button in disabled:
            try:
                button.state(["disabled"])
            except tk.TclError:  # pragma: no cover
                pass

        self._scroller.bind_wheel_tree(row)
        if available:
            # Double click plays the file in Clipster's own player; the Play
            # button keeps handing it to the system, which is what it says.
            self._bind_play_here(row, entry)
        self._row_items.append((separator, row, entry))

    def _bind_play_here(self, widget: tk.Misc, entry: HistoryEntry) -> None:
        """Let a double click anywhere on the row start it in the Streaming tab.

        The buttons are excluded - a double click on *Delete* must delete once,
        not delete and then play.

        :param widget: The row, walked down to its labels.
        :param entry: The entry the row shows.
        :return: None
        """
        if widget.winfo_class() in ("TButton", "Button"):
            return
        widget.bind("<Double-Button-1>", lambda _event, e=entry: self._play_here(e), add="+")
        widget.bind("<Button-3>", lambda _event, e=entry: self._share_entry(e), add="+")
        for child in widget.winfo_children():
            self._bind_play_here(child, entry)

    def _play_here(self, entry: HistoryEntry) -> None:
        """Hand one row to the in-app player.

        :param entry: The entry to play.
        :return: None
        """
        if self.on_play_here is not None:
            self.on_play_here(entry)

    def _share_entry(self, entry: HistoryEntry) -> str:
        """Show the share code for one download.

        :param entry: The entry the row shows.
        :return: ``break``, so no other handler also answers the click.
        """
        if self.on_share_entry is not None:
            self.on_share_entry(entry)
        return "break"

    def _fit_line(self, text: str, width: int) -> str:
        """Shorten ``text`` so that it fits on a single line of ``width`` pixels.

        :param text: The full file name.
        :param width: Available width in pixels.
        :return: The text, truncated with an ellipsis when necessary.
        """
        font = self.fonts["bold"]
        if width <= 0 or font.measure(text) <= width:
            return text
        budget = width - font.measure("…")
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if font.measure(text[:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def _problem_text(self, entry: HistoryEntry) -> str:
        """Return the problem line of a row, or an empty string.

        :param entry: The entry to describe.
        :return: The text shown below the name.
        """
        if entry.status == STATUS_CANCELED:
            return self.messages["history_status_canceled"]
        if entry.status == STATUS_FAILED:
            return self.messages.format("history_problem", details=entry.error or self.messages["error_title"])
        if entry.path and entry.file_path() is None:
            return self.messages["history_missing"]
        return ""

    # ------------------------------------------------------------------
    # Settings page
    # ------------------------------------------------------------------
    def _build_settings(self, master: tk.Misc) -> ttk.Frame:
        """Create the settings form.

        :param master: The page container.
        :return: The page frame.
        """
        page = ttk.Frame(master, style="TFrame", padding=PAD)
        ttk.Label(page, text=self.messages["page_settings"], style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text=self.messages["settings_intro"], style="Muted.TLabel").pack(
            anchor="w", pady=(2, PAD)
        )

        # The button row is packed before the cards so it keeps its space even
        # when the form is taller than the window.
        buttons = ttk.Frame(page, style="TFrame")
        buttons.pack(side="bottom", fill="x", pady=(PAD, 0))
        self._settings_status = ttk.Label(buttons, text="", style="Success.TLabel")
        self._settings_status.pack(side="left")
        ttk.Button(buttons, text=self.messages["settings_save"], style="Accent.TButton",
                   command=self._save_settings).pack(side="right")
        ttk.Button(buttons, text=self.messages["settings_reload"], command=self._load_settings).pack(
            side="right", padx=(0, PAD_SMALL)
        )

        columns = ttk.Frame(page, style="TFrame")
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=1, uniform="col")
        columns.columnconfigure(1, weight=1, uniform="col")

        left = ttk.LabelFrame(columns, text=self.messages["settings_general"], style="Card.TLabelframe",
                              padding=PAD)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD_SMALL))
        right = ttk.LabelFrame(columns, text=self.messages["settings_behaviour"], style="Card.TLabelframe",
                               padding=PAD)
        right.grid(row=0, column=1, sticky="nsew", padx=(PAD_SMALL, 0))

        discover = ttk.LabelFrame(page, text=self.messages["settings_discover"], style="Card.TLabelframe",
                                  padding=PAD)
        discover.pack(fill="x", pady=(PAD, 0), before=columns)

        self._vars["language"] = tk.StringVar()
        self._add_combo(left, "settings_language", "language", self._language_values())

        self._vars["default_format"] = tk.StringVar()
        self._add_combo(
            left, "settings_default_format", "default_format",
            [self.messages["format_mp3"], self.messages["format_mp4"]],
        )

        self._vars["download_dir"] = tk.StringVar()
        folder_row = ttk.Frame(left, style="Panel.TFrame")
        folder_row.pack(fill="x", pady=(PAD_SMALL, 0))
        # Android stores downloads behind a link, so it needs the longer wording
        # that names the folder a file manager actually shows.
        ttk.Label(
            folder_row,
            text=self.messages[
                "settings_download_dir_android" if paths.is_termux() else "settings_download_dir"
            ],
            style="Panel.Muted.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w")
        picker = ttk.Frame(left, style="Panel.TFrame")
        picker.pack(fill="x", pady=(2, 0))
        ttk.Entry(picker, textvariable=self._vars["download_dir"], font=self.fonts["body"]).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(picker, text=self.messages["settings_browse"], style="Row.TButton",
                   command=self._pick_folder).pack(side="left", padx=(PAD_SMALL, 0))
        # Where the files really land - the phone UI has always shown this, the
        # desktop did not, and an empty field or a link target is easy to misread.
        self._download_dir_resolved = ttk.Label(
            left, text="", style="Panel.Muted.TLabel", wraplength=520, justify="left"
        )
        self._download_dir_resolved.pack(anchor="w", pady=(2, 0))
        self._vars["download_dir"].trace_add("write", lambda *_: self._sync_resolved_download_dir())

        self._vars["file_manager"] = tk.StringVar()
        self._add_entry(left, "settings_file_manager", "file_manager")

        self._vars["interval_sec"] = tk.StringVar()
        self._add_entry(left, "settings_interval", "interval_sec", width=8)

        self._vars["history_limit"] = tk.StringVar()
        self._add_entry(left, "settings_history_limit", "history_limit", width=8)

        self._vars["startup_visibility"] = tk.StringVar()
        self._add_combo(
            right,
            "settings_startup",
            "startup_visibility",
            [self.messages["settings_startup_tray"], self.messages["settings_startup_window"]],
        )

        for key, label in (
            ("check_updates", "settings_check_updates"),
            ("parallel_downloads", "settings_parallel"),
            ("open_view_after_download", "settings_open_view"),
            ("open_folder_after_download", "settings_open_folder"),
            ("clear_clipboard_after_download", "settings_clear_clipboard"),
            ("ask_audio_language", "settings_ask_audio"),
            ("no_playlist", "settings_no_playlist"),
            ("restrict_filenames", "settings_restrict"),
            ("use_tray", "settings_use_tray"),
            ("show_startup_notification", "settings_startup_notification"),
            ("autostart", "settings_autostart"),
        ):
            self._vars[key] = tk.BooleanVar()
            ttk.Checkbutton(
                right, text=self.messages[label], variable=self._vars[key], style="TCheckbutton"
            ).pack(anchor="w", pady=2)

        ttk.Label(right, text=self.messages["settings_restart_note"], style="Panel.Muted.TLabel",
                  wraplength=360, justify="left").pack(anchor="w", pady=(PAD_SMALL, 0))

        self._vars["discover_search_suffix"] = tk.StringVar()
        self._add_entry(discover, "settings_discover_suffix", "discover_search_suffix")
        self._vars["discover_mode"] = tk.StringVar()
        self._add_combo(
            discover,
            "settings_discover_mode",
            "discover_mode",
            [
                self.messages["discover_mode_search"],
                self.messages["discover_mode_related"],
                self.messages["discover_mode_deezer"],
                self.messages["discover_mode_listenbrainz"],
            ],
        )
        self._vars["discover_max_results"] = tk.StringVar()
        self._add_entry(discover, "settings_discover_max", "discover_max_results", width=8)
        self._vars["discover_require_suffix"] = tk.BooleanVar()
        ttk.Checkbutton(
            discover,
            text=self.messages["settings_discover_require_suffix"],
            variable=self._vars["discover_require_suffix"],
            style="TCheckbutton",
        ).pack(anchor="w", pady=(PAD_SMALL, 0))

        self._vars["playback_on_mobile"] = tk.StringVar()
        self._add_combo(
            discover,
            "playback_mobile_label",
            "playback_on_mobile",
            [
                self.messages["playback_mobile_stream"],
                self.messages["playback_mobile_local"],
                self.messages["playback_mobile_ask"],
            ],
        )
        self._vars["playback_local_only"] = tk.BooleanVar()
        ttk.Checkbutton(
            discover,
            text=self.messages["playback_local_only_label"],
            variable=self._vars["playback_local_only"],
            style="TCheckbutton",
        ).pack(anchor="w", pady=(PAD_SMALL, 0))
        ttk.Label(
            discover,
            text=self.messages["playback_local_only_hint"],
            style="Panel.Muted.TLabel",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        ttk.Label(
            discover,
            text=self.messages["settings_cookies"],
            style="Panel.TLabel",
        ).pack(anchor="w", pady=(PAD, 0))
        ttk.Label(
            discover,
            text=self.messages["settings_cookies_hint"],
            style="Panel.Muted.TLabel",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(PAD_SMALL, 0))
        self._vars["cookies_risk_acknowledged"] = tk.BooleanVar()
        ttk.Checkbutton(
            discover,
            text=self.messages["settings_cookies_risk_ack"],
            variable=self._vars["cookies_risk_acknowledged"],
            style="TCheckbutton",
            command=self._sync_cookies_controls,
        ).pack(anchor="w", pady=(PAD_SMALL, 0))

        self._vars["cookies_from_browser"] = tk.StringVar()
        ttk.Label(
            discover, text=self.messages["settings_cookies_browser"], style="Panel.Muted.TLabel"
        ).pack(anchor="w", pady=(PAD_SMALL, 2))
        self._cookies_browser_combo = ttk.Combobox(
            discover,
            state="disabled",
            textvariable=self._vars["cookies_from_browser"],
            values=self._cookie_browser_labels(),
            font=self.fonts["body"],
        )
        self._cookies_browser_combo.pack(fill="x")

        self._vars["cookies_file"] = tk.StringVar()
        cookies_row = ttk.Frame(discover, style="Panel.TFrame")
        cookies_row.pack(fill="x", pady=(PAD_SMALL, 0))
        ttk.Label(cookies_row, text=self.messages["settings_cookies_file"], style="Panel.Muted.TLabel").pack(
            anchor="w"
        )
        cookies_entry_row = ttk.Frame(discover, style="Panel.TFrame")
        cookies_entry_row.pack(fill="x")
        self._cookies_file_entry = ttk.Entry(
            cookies_entry_row, textvariable=self._vars["cookies_file"], font=self.fonts["body"], state="disabled"
        )
        self._cookies_file_entry.pack(side="left", fill="x", expand=True)
        self._cookies_browse_btn = ttk.Button(
            cookies_entry_row,
            text=self.messages["settings_browse"],
            style="Row.TButton",
            command=self._pick_cookies_file,
            state="disabled",
        )
        self._cookies_browse_btn.pack(side="left", padx=(PAD_SMALL, 0))

        return page

    def _cookie_browser_labels(self) -> List[str]:
        """Return display labels for the cookies-from-browser combobox."""
        return [
            self.messages["settings_cookies_browser_off"],
            self.messages["settings_cookies_browser_firefox"],
            self.messages["settings_cookies_browser_chrome"],
            self.messages["settings_cookies_browser_chromium"],
            self.messages["settings_cookies_browser_brave"],
            self.messages["settings_cookies_browser_edge"],
        ]

    def _cookies_browser_key(self, label: str) -> str:
        """Map a cookies-browser display label to the stored config value."""
        mapping = {
            self.messages["settings_cookies_browser_firefox"]: "firefox",
            self.messages["settings_cookies_browser_chrome"]: "chrome",
            self.messages["settings_cookies_browser_chromium"]: "chromium",
            self.messages["settings_cookies_browser_brave"]: "brave",
            self.messages["settings_cookies_browser_edge"]: "edge",
        }
        return mapping.get(label, "")

    def _cookies_browser_label(self, key: str) -> str:
        """Map a stored cookies-browser value to a display label."""
        mapping = {
            "firefox": self.messages["settings_cookies_browser_firefox"],
            "chrome": self.messages["settings_cookies_browser_chrome"],
            "chromium": self.messages["settings_cookies_browser_chromium"],
            "brave": self.messages["settings_cookies_browser_brave"],
            "edge": self.messages["settings_cookies_browser_edge"],
        }
        return mapping.get((key or "").strip().lower(), self.messages["settings_cookies_browser_off"])

    def _sync_cookies_controls(self) -> None:
        """Enable browser/file cookie controls only after risk acknowledgement."""
        enabled = bool(self._vars["cookies_risk_acknowledged"].get())
        self._cookies_browser_combo.configure(state="readonly" if enabled else "disabled")
        self._cookies_file_entry.configure(state="normal" if enabled else "disabled")
        self._cookies_browse_btn.configure(state="normal" if enabled else "disabled")

    def _language_values(self) -> List[str]:
        """Return the selectable UI languages as display labels."""
        from . import i18n

        return [self.messages.language_label(code) for code in i18n.available_languages()]

    def _mobile_labels(self) -> Dict[str, str]:
        """Return the shown text for every ``playback_on_mobile`` value.

        :return: Mapping of stored value to translated label.
        """
        return {
            netmode.MOBILE_STREAM: self.messages["playback_mobile_stream"],
            netmode.MOBILE_LOCAL: self.messages["playback_mobile_local"],
            netmode.MOBILE_ASK: self.messages["playback_mobile_ask"],
        }

    def _add_combo(self, master: tk.Misc, label_key: str, var_key: str, values: List[str]) -> None:
        """Add a labelled read-only combobox to a settings card.

        :param master: The card frame.
        :param label_key: Message key of the label.
        :param var_key: Key in :attr:`_vars`.
        :param values: Selectable display values.
        :return: None
        """
        ttk.Label(master, text=self.messages[label_key], style="Panel.Muted.TLabel").pack(
            anchor="w", pady=(PAD_SMALL, 2)
        )
        ttk.Combobox(
            master, state="readonly", textvariable=self._vars[var_key], values=values, font=self.fonts["body"]
        ).pack(fill="x")

    def _add_entry(self, master: tk.Misc, label_key: str, var_key: str, width: int = 0) -> None:
        """Add a labelled text entry to a settings card.

        :param master: The card frame.
        :param label_key: Message key of the label.
        :param var_key: Key in :attr:`_vars`.
        :param width: Optional fixed width in characters.
        :return: None
        """
        ttk.Label(master, text=self.messages[label_key], style="Panel.Muted.TLabel").pack(
            anchor="w", pady=(PAD_SMALL, 2)
        )
        entry = ttk.Entry(master, textvariable=self._vars[var_key], font=self.fonts["body"])
        if width:
            entry.configure(width=width)
            entry.pack(anchor="w")
        else:
            entry.pack(fill="x")

    def _pick_folder(self) -> None:
        """Ask for the download directory."""
        current = self._vars["download_dir"].get().strip()
        chosen = filedialog.askdirectory(
            parent=self.window,
            title=self.messages["settings_download_dir"],
            initialdir=current or str(self.config.resolved_download_dir()),
        )
        if chosen:
            self._vars["download_dir"].set(chosen)

    def _pick_cookies_file(self) -> None:
        """Ask for a Netscape cookies.txt path (contents are never logged)."""
        if not bool(self._vars["cookies_risk_acknowledged"].get()):
            return
        current = self._vars["cookies_file"].get().strip()
        initial = str(Path(current).expanduser().parent) if current else str(Path.home())
        chosen = filedialog.askopenfilename(
            parent=self.window,
            title=self.messages["settings_cookies_file"],
            initialdir=initial,
            filetypes=[("Cookies", "*.txt"), ("All files", "*.*")],
        )
        if chosen:
            self._vars["cookies_file"].set(chosen)

    def _sync_resolved_download_dir(self) -> None:
        """Show which folder the current download-dir setting really means."""
        raw = self._vars["download_dir"].get().strip()
        try:
            if raw:
                target = Path(raw).expanduser()
                if paths.is_termux():
                    target = paths.android_writable_download_dir(target)
            else:
                target = self.config.resolved_download_dir()
            text = self.messages.format(
                "settings_download_dir_resolved", path=paths.friendly_download_path(target)
            )
        except (OSError, ValueError):
            text = ""
        try:
            self._download_dir_resolved.configure(text=text)
        except tk.TclError:
            pass

    def _load_settings(self) -> None:
        """Fill the form from the live configuration."""
        self._settings_status.configure(text="")
        self._vars["language"].set(self.messages.language_label(self.config.language))
        self._vars["default_format"].set(
            self.messages["format_mp4"] if self.config.default_format == "mp4" else self.messages["format_mp3"]
        )
        self._vars["download_dir"].set(self.config.download_dir)
        self._sync_resolved_download_dir()
        self._vars["file_manager"].set(self.config.file_manager)
        self._vars["interval_sec"].set("{0:g}".format(self.config.interval_sec))
        self._vars["history_limit"].set(str(self.config.history_limit))
        self._vars["discover_search_suffix"].set(self.config.discover_search_suffix)
        self._vars["discover_max_results"].set(str(self.config.discover_max_results))
        self._vars["discover_require_suffix"].set(bool(self.config.discover_require_suffix))
        self._vars["cookies_risk_acknowledged"].set(bool(self.config.cookies_risk_acknowledged))
        self._vars["cookies_from_browser"].set(self._cookies_browser_label(self.config.cookies_from_browser))
        self._vars["cookies_file"].set(self.config.cookies_file)
        self._sync_cookies_controls()
        mode_map = {
            "related": self.messages["discover_mode_related"],
            "deezer": self.messages["discover_mode_deezer"],
            "listenbrainz": self.messages["discover_mode_listenbrainz"],
            "search": self.messages["discover_mode_search"],
        }
        self._vars["discover_mode"].set(
            mode_map.get(self.config.discover_mode, self.messages["discover_mode_search"])
        )
        self._vars["playback_on_mobile"].set(
            self._mobile_labels()[netmode.normalize_mode(self.config.playback_on_mobile)]
        )
        self._vars["playback_local_only"].set(bool(self.config.playback_local_only))
        self._vars["startup_visibility"].set(
            self.messages["settings_startup_tray"]
            if self.config.start_minimized
            else self.messages["settings_startup_window"]
        )
        for key in (
            "check_updates",
            "parallel_downloads",
            "open_view_after_download",
            "open_folder_after_download",
            "clear_clipboard_after_download",
            "ask_audio_language",
            "no_playlist",
            "restrict_filenames",
            "use_tray",
            "show_startup_notification",
            "autostart",
        ):
            self._vars[key].set(bool(getattr(self.config, key)))

    def _save_settings(self) -> None:
        """Write the form back into the configuration and persist it."""
        from . import i18n

        labels = {self.messages.language_label(code): code for code in i18n.available_languages()}
        self.config.language = labels.get(self._vars["language"].get(), self.config.language)
        self.config.default_format = (
            "mp4" if self._vars["default_format"].get() == self.messages["format_mp4"] else "mp3"
        )
        self.config.download_dir = self._vars["download_dir"].get().strip()
        self.config.file_manager = self._vars["file_manager"].get().strip()
        self.config.interval_sec = _as_float(self._vars["interval_sec"].get(), self.config.interval_sec, 0.5, 60.0)
        self.config.history_limit = _as_int(self._vars["history_limit"].get(), self.config.history_limit, 1, 10000)
        self.config.discover_search_suffix = self._vars["discover_search_suffix"].get().strip()
        self.config.discover_require_suffix = bool(self._vars["discover_require_suffix"].get())
        acked = bool(self._vars["cookies_risk_acknowledged"].get())
        was_acked = bool(self.config.cookies_risk_acknowledged)
        self.config.cookies_risk_acknowledged = acked
        if acked:
            self.config.cookies_from_browser = self._cookies_browser_key(
                self._vars["cookies_from_browser"].get()
            )
            self.config.cookies_file = self._vars["cookies_file"].get().strip()
            if not was_acked or not self.config.cookies_risk_acknowledged_at:
                from .terms import utc_now_iso

                self.config.cookies_risk_acknowledged_at = utc_now_iso()
        else:
            # Without acknowledgement, refuse to persist cookie sources for yt-dlp.
            self.config.cookies_from_browser = ""
            self.config.cookies_file = ""
            self.config.cookies_risk_acknowledged_at = ""
        self.config.discover_max_results = _as_int(
            self._vars["discover_max_results"].get(), self.config.discover_max_results, 1, 200
        )
        selected_mode = self._vars["discover_mode"].get()
        if selected_mode == self.messages["discover_mode_related"]:
            self.config.discover_mode = "related"
        elif selected_mode == self.messages["discover_mode_deezer"]:
            self.config.discover_mode = "deezer"
        elif selected_mode == self.messages["discover_mode_listenbrainz"]:
            self.config.discover_mode = "listenbrainz"
        else:
            self.config.discover_mode = "search"
        chosen = self._vars["playback_on_mobile"].get()
        self.config.playback_on_mobile = next(
            (key for key, label in self._mobile_labels().items() if label == chosen),
            netmode.DEFAULT_MOBILE_MODE,
        )
        self.config.playback_local_only = bool(self._vars["playback_local_only"].get())
        start_in_tray = self._vars["startup_visibility"].get() == self.messages["settings_startup_tray"]
        self.config.start_minimized = start_in_tray
        for key in (
            "check_updates",
            "parallel_downloads",
            "open_view_after_download",
            "open_folder_after_download",
            "clear_clipboard_after_download",
            "ask_audio_language",
            "no_playlist",
            "restrict_filenames",
            "use_tray",
            "show_startup_notification",
            "autostart",
        ):
            setattr(self.config, key, bool(self._vars[key].get()))
        if start_in_tray:
            # Background startup needs a tray icon; otherwise the window is forced open.
            self.config.use_tray = True
            self._vars["use_tray"].set(True)

        self._on_save_settings()
        self._load_settings()
        if self.discover is not None:
            self.discover.reload_from_config()
        self._settings_status.configure(text=self.messages["settings_saved"])
        self.window.after(4000, lambda: self._settings_status.configure(text=""))

    # ------------------------------------------------------------------
    # About page
    # ------------------------------------------------------------------
    def _add_value(self, master: tk.Misc, text: str, link: str = "") -> None:
        """Add a value to an about card, as a clickable link when given a URL.

        :param master: The row frame.
        :param text: The text to show.
        :param link: URL opened on click; empty renders plain text.
        :return: None
        """
        if not link:
            ttk.Label(master, text=text, style="Panel.TLabel").pack(side="left")
            return
        label = tk.Label(
            master,
            text=text,
            background=self.palette.panel,
            foreground=self.palette.accent,
            font=self.fonts["body"],
            cursor="hand2",
            borderwidth=0,
        )
        label.pack(side="left")
        label.bind("<Button-1>", lambda _event, url=link: self._open_link(url))

    @staticmethod
    def _open_link(url: str) -> None:
        """Open ``url`` in the user's browser.

        :param url: The address to open.
        :return: None
        """
        import webbrowser

        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover - depends on the desktop
            log.warning("Could not open %s: %s", url, exc)

    def _build_about(self, master: tk.Misc) -> ttk.Frame:
        """Create the about page with version, paths and dependency status.

        :param master: The page container.
        :return: The page frame.
        """
        outer = ttk.Frame(master, style="TFrame", padding=PAD)
        # The licence keeps its place at the bottom, everything above scrolls -
        # the card stack is taller than the window in some languages.
        ttk.Label(outer, text=self.messages["about_license"], style="Muted.TLabel", wraplength=880,
                  justify="left").pack(side="bottom", anchor="w", pady=(PAD, 0))
        scroller = _Scroller(outer, self.palette)
        scroller.pack(fill="both", expand=True)
        page = ttk.Frame(scroller.body, style="TFrame")
        page.pack(fill="both", expand=True)

        ttk.Label(page, text=APP_SHORT_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Version {0}".format(APP_VERSION), style="Muted.TLabel").pack(anchor="w")

        update = ttk.Frame(page, style="TFrame")
        update.pack(fill="x", pady=(PAD_SMALL, 0))
        self._update_label = ttk.Label(update, text=self.messages["update_unknown"],
                                       style="Muted.TLabel")
        self._update_label.pack(side="left")
        self._update_button = ttk.Button(update, text=self.messages["update_check"],
                                         style="Row.TButton", command=self._on_check_updates)
        self._update_button.pack(side="left", padx=(PAD_SMALL, 0))
        ttk.Label(page, text=self.messages["about_text"], wraplength=760, justify="left").pack(
            anchor="w", pady=(PAD, 0)
        )
        terms = ttk.Frame(page, style="TFrame")
        terms.pack(fill="x", pady=(PAD_SMALL, 0))
        ttk.Label(
            terms,
            text=self.messages["about_terms_note"],
            wraplength=640,
            justify="left",
            style="Muted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            terms,
            text=self.messages["about_terms_button"],
            style="Row.TButton",
            command=self._on_show_terms,
        ).pack(side="left", padx=(PAD_SMALL, 0))

        author = ttk.LabelFrame(page, text=self.messages["about_author"], style="Card.TLabelframe", padding=PAD)
        author.pack(fill="x", pady=(PAD, 0))
        for label, value, link in (
            (self.messages["about_author_name"], APP_AUTHOR, ""),
            (self.messages["about_website"], APP_WEBSITE, APP_WEBSITE),
            (self.messages["about_repository"], APP_URL, APP_URL),
        ):
            line = ttk.Frame(author, style="Panel.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=label, style="Panel.Muted.TLabel", width=18, anchor="w").pack(side="left")
            self._add_value(line, value, link)

        card = ttk.LabelFrame(page, text=self.messages["about_paths"], style="Card.TLabelframe", padding=PAD)
        card.pack(fill="x", pady=(PAD, 0))
        for label, value in (
            (self.messages["about_config"], paths.config_file()),
            (self.messages["about_history"], paths.history_file()),
            (self.messages["about_log"], paths.log_file()),
            (self.messages["about_venv"], paths.venv_dir()),
        ):
            line = ttk.Frame(card, style="Panel.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=label, style="Panel.Muted.TLabel", width=18, anchor="w").pack(side="left")
            ttk.Label(line, text=str(value), style="Panel.TLabel").pack(side="left")

        deps = ttk.LabelFrame(page, text=self.messages["about_dependencies"], style="Card.TLabelframe",
                              padding=PAD)
        deps.pack(fill="x", pady=(PAD, 0))
        platform = dependencies.current_platform()
        for item in dependencies.pip_dependencies(platform) + dependencies.system_dependencies(platform):
            level = getattr(item, "level", dependencies.LEVEL_REQUIRED)
            name = getattr(item, "package", "") or getattr(item, "name", "")
            line = ttk.Frame(deps, style="Panel.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=name, style="Panel.Bold.TLabel", width=16, anchor="w").pack(side="left")
            ttk.Label(
                line,
                text=self.messages["about_required"] if level == dependencies.LEVEL_REQUIRED
                else self.messages["about_optional"],
                style="Panel.Muted.TLabel",
                width=12,
                anchor="w",
            ).pack(side="left")
            key = getattr(item, "feature_key", "")
            described = self.messages[key] if key else item.feature
            if described == key:  # no translation for this key
                described = item.feature
            ttk.Label(line, text=described, style="Panel.Muted.TLabel").pack(side="left")

        # The page container is the scroller's parent, not the inner frame -
        # select_page() shows and hides whatever is returned here.
        scroller.bind_wheel_tree(page)
        return outer

    # ------------------------------------------------------------------
    # Window state
    # ------------------------------------------------------------------
    def show(self, page: Optional[str] = None) -> None:
        """Show and raise the window.

        :param page: Optionally switch to a page first.
        :return: None
        """
        if page:
            self.select_page(page)
        elif self._page == "phone" and self.phone is not None:
            # Reopened on the Phone page: hide() stopped the refresh, so it has
            # to be picked up again or the status would be frozen.
            self.phone.start_polling()
        try:
            first = self.window.state() == "withdrawn"
            self.window.deiconify()
            if first:
                self.window.update_idletasks()
                width, height = self.window.winfo_width(), self.window.winfo_height()
                x = max(0, (self.window.winfo_screenwidth() - width) // 2)
                y = max(0, (self.window.winfo_screenheight() - height) // 3)
                self.window.geometry("+{0}+{1}".format(x, y))
            self.window.lift()
            self.window.focus_force()
        except tk.TclError:  # pragma: no cover
            pass

    def hide(self) -> None:
        """Hide the window."""
        if self.phone is not None:
            # Nobody is looking, and a pending refresh would keep firing.
            self.phone.stop_polling()
        try:
            self.window.withdraw()
        except tk.TclError:  # pragma: no cover
            pass

    def visible(self) -> bool:
        """Return ``True`` while the window is on screen."""
        try:
            return self.window.state() != "withdrawn"
        except tk.TclError:  # pragma: no cover
            return False

    def destroy(self) -> None:
        """Tear the window down."""
        if self.phone is not None:
            # Before the widgets go: a queued refresh would fire into a
            # destroyed interpreter, which Tk does not survive gracefully.
            self.phone.stop_polling()
        if self.discover is not None:
            self.discover.destroy_player()
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass


def _as_float(text: str, fallback: float, minimum: float, maximum: float) -> float:
    """Parse a float from a settings field, clamped into a sane range.

    :param text: The raw field content.
    :param fallback: Value used when the text is not a number.
    :param minimum: Lower bound.
    :param maximum: Upper bound.
    :return: The parsed value.
    """
    try:
        value = float(text.replace(",", "."))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, value))


def _as_int(text: str, fallback: int, minimum: int, maximum: int) -> int:
    """Parse an int from a settings field, clamped into a sane range.

    :param text: The raw field content.
    :param fallback: Value used when the text is not a number.
    :param minimum: Lower bound.
    :param maximum: Upper bound.
    :return: The parsed value.
    """
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, value))
