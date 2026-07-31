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
from typing import Callable, Dict, List, Optional

from . import APP_SHORT_NAME, APP_VERSION, dependencies, paths, theme
from .config import Config
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

#: Fixed column widths of the table in pixels; the name column takes the rest.
_COL_BADGE = 48
_COL_NAME_MIN = 150
_COL_DURATION = 62
_COL_SIZE = 78
_COL_DATE = 118
#: Width reserved for the scrollbar so header and rows stay aligned; matches
#: the width configured for Vertical.TScrollbar in clipster.theme.
_SCROLLBAR = 11

#: The sidebar filters, as ``(key, message key)``.
_FILTERS = (
    ("all", "filter_all"),
    (STATUS_OK, "filter_ready"),
    (STATUS_FAILED, "filter_failed"),
    (STATUS_CANCELED, "filter_canceled"),
)


def _shorten(text: str, limit: int = 120) -> str:
    """Return ``text`` truncated to ``limit`` characters with an ellipsis."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


class _Scroller(ttk.Frame):
    """A vertically scrollable container for the table rows."""

    def __init__(self, master: tk.Misc, palette: theme.Palette) -> None:
        """
        :param master: The parent widget.
        :param palette: The colour scheme.
        """
        super().__init__(master, style="Panel.TFrame")
        self._canvas = tk.Canvas(
            self, background=palette.panel, highlightthickness=0, borderwidth=0, takefocus=0
        )
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")

        #: Rows are added to this frame.
        self.body = ttk.Frame(self._canvas, style="Panel.TFrame")
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(self._window, width=e.width))
        self.bind_wheel(self)
        self.bind_wheel(self.body)
        self.bind_wheel(self._canvas)

    def bind_wheel(self, widget: tk.Misc) -> None:
        """Attach mouse wheel scrolling for every platform.

        :param widget: The widget to bind.
        :return: None
        """
        widget.bind("<MouseWheel>", lambda e: self._scroll(-1 if getattr(e, "delta", 0) > 0 else 1), add="+")
        widget.bind("<Button-4>", lambda _e: self._scroll(-1), add="+")
        widget.bind("<Button-5>", lambda _e: self._scroll(1), add="+")

    def bind_wheel_tree(self, widget: tk.Misc) -> None:
        """Bind the wheel on a widget and every descendant.

        :param widget: The root of the subtree.
        :return: None
        """
        self.bind_wheel(widget)
        for child in widget.winfo_children():
            self.bind_wheel_tree(child)

    def _scroll(self, direction: int) -> None:
        """Scroll by three rows.

        :param direction: ``-1`` up, ``1`` down.
        :return: None
        """
        self._canvas.yview_scroll(direction * 3, "units")

    def clear(self) -> None:
        """Remove every row."""
        for child in self.body.winfo_children():
            child.destroy()

    def to_top(self) -> None:
        """Scroll back to the first row."""
        self._canvas.yview_moveto(0.0)


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
        on_open_entry: Callable[[HistoryEntry], None],
        on_reveal_entry: Callable[[HistoryEntry], None],
        on_clear_history: Callable[[], None],
        on_open_folder: Callable[[], None],
        on_submit_url: Callable[[str, str], None],
        on_save_settings: Callable[[], None],
    ) -> None:
        """
        :param master: The hidden Tk root.
        :param messages: The active translation table.
        :param palette: The colour scheme.
        :param config: The live configuration, edited in place by the settings page.
        :param icon: Window icon, or ``None``.
        :param on_close: Called when the window is closed.
        :param on_quit: Called by the quit button.
        :param on_open_entry: Play/open the file of a row.
        :param on_reveal_entry: Open the folder of a row.
        :param on_clear_history: Empty the list.
        :param on_open_folder: Open the download folder.
        :param on_submit_url: Start a download for a pasted URL and format.
        :param on_save_settings: Persist the configuration after an edit.
        """
        self.messages = messages
        self.palette = palette
        self.config = config
        self.fonts = theme.fonts()

        self._on_close = on_close
        self._on_quit = on_quit
        self._on_open_entry = on_open_entry
        self._on_reveal_entry = on_reveal_entry
        self._on_clear_history = on_clear_history
        self._on_open_folder = on_open_folder
        self._on_submit_url = on_submit_url
        self._on_save_settings = on_save_settings

        self._entries: List[HistoryEntry] = []
        self._filter = "all"
        self._page = "downloads"
        self._counts: Dict[str, int] = {}
        self._filter_buttons: Dict[str, ttk.Button] = {}
        self._menu_buttons: Dict[str, ttk.Button] = {}
        self._pages: Dict[str, ttk.Frame] = {}
        self._vars: Dict[str, tk.Variable] = {}

        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.title(APP_SHORT_NAME)
        self.window.configure(background=palette.base)
        self.window.minsize(880, 620)
        self.window.geometry("1020x720")
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if icon is not None:
            try:
                self.window.iconphoto(False, icon)
            except tk.TclError:  # pragma: no cover
                pass

        self._build()
        self.select_page("downloads")

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    def _build(self) -> None:
        """Create the menu row and the three pages."""
        menu = ttk.Frame(self.window, style="Toolbar.TFrame")
        menu.pack(fill="x")
        for key, label in (("downloads", "page_downloads"), ("settings", "page_settings"), ("about", "page_about")):
            button = ttk.Button(
                menu, text=self.messages[label], style="Menu.TButton", command=lambda k=key: self.select_page(k)
            )
            button.pack(side="left")
            self._menu_buttons[key] = button
        ttk.Separator(self.window, orient="horizontal").pack(fill="x")

        self._container = ttk.Frame(self.window, style="TFrame")
        self._container.pack(fill="both", expand=True)

        self._pages["downloads"] = self._build_downloads(self._container)
        self._pages["settings"] = self._build_settings(self._container)
        self._pages["about"] = self._build_about(self._container)

    def select_page(self, key: str) -> None:
        """Show one of the three pages.

        :param key: ``downloads``, ``settings`` or ``about``.
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

        header = ttk.Frame(table, style="TFrame")
        header.pack(fill="x", padx=(0, _SCROLLBAR))
        self._configure_columns(header)
        for column, key, anchor in (
            (1, "column_name", "w"),
            (2, "column_duration", "e"),
            (3, "column_size", "e"),
            (4, "column_date", "w"),
        ):
            ttk.Label(header, text=self.messages[key].upper(), style="Muted.TLabel", anchor=anchor).grid(
                row=0, column=column, sticky="ew", padx=(0, PAD_SMALL), pady=(0, 6)
            )
        ttk.Separator(table, orient="horizontal").pack(fill="x")

        self._scroller = _Scroller(table, self.palette)
        self._scroller.pack(fill="both", expand=True)

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

    @staticmethod
    def _configure_columns(frame: tk.Misc) -> None:
        """Apply the shared column geometry to a header or row frame.

        :param frame: The frame whose columns are configured.
        :return: None
        """
        frame.columnconfigure(0, minsize=_COL_BADGE, weight=0)
        # Only the name column grows, so every other column stays aligned with
        # the header no matter how wide the window is.
        frame.columnconfigure(1, weight=1, minsize=_COL_NAME_MIN)
        frame.columnconfigure(2, minsize=_COL_DURATION, weight=0)
        frame.columnconfigure(3, minsize=_COL_SIZE, weight=0)
        frame.columnconfigure(4, minsize=_COL_DATE, weight=0)
        # The action column takes exactly what its two buttons need.
        frame.columnconfigure(5, weight=0)

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

        :param entries: Every history entry, newest first.
        :param download_dir: Shown in the footer.
        :return: None
        """
        self._entries = list(entries)
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
        self._paint_rows()

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
        """Return the entries matching the active filter."""
        if self._filter == "all":
            return self._entries
        return [entry for entry in self._entries if entry.status == self._filter]

    def _paint_rows(self) -> None:
        """Rebuild the table rows."""
        self._scroller.clear()
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

    def _add_row(self, entry: HistoryEntry, index: int) -> None:
        """Append one entry to the table.

        :param entry: The entry to render.
        :param index: Position, used for the separator.
        :return: None
        """
        if index:
            ttk.Separator(self._scroller.body, orient="horizontal").pack(fill="x")

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
        name_label = ttk.Label(
            title_line,
            text=_shorten(entry.name),
            style="Panel.Bold.TLabel",
            justify="left",
        )
        name_label.pack(side="left", anchor="nw", fill="x", expand=True)

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
        ) -> None:
            """Keep the name length and the wrap width in sync with the cell."""
            width = max(120, event.width - 24)
            # The name stays on one line: measuring a single line is exact,
            # while predicting Tk's word wrapping never quite matches and left
            # the rows at uneven heights.  The status mark shares the line, so
            # its width is subtracted.
            first.configure(text=self._fit_line(full, width - 26))
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
        open_button = ttk.Button(
            actions,
            text=self.messages["history_open"],
            style="Row.TButton",
            width=8,
            command=lambda e=entry: self._on_open_entry(e),
        )
        open_button.pack(side="left")
        folder_button = ttk.Button(
            actions,
            text=self.messages["history_folder"],
            style="Row.TButton",
            width=8,
            command=lambda e=entry: self._on_reveal_entry(e),
        )
        folder_button.pack(side="left", padx=(6, 0))
        if not available:
            for button in (open_button, folder_button):
                try:
                    button.state(["disabled"])
                except tk.TclError:  # pragma: no cover
                    pass

        self._scroller.bind_wheel_tree(row)

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
        ttk.Label(folder_row, text=self.messages["settings_download_dir"], style="Panel.Muted.TLabel").pack(
            anchor="w"
        )
        picker = ttk.Frame(left, style="Panel.TFrame")
        picker.pack(fill="x", pady=(2, 0))
        ttk.Entry(picker, textvariable=self._vars["download_dir"], font=self.fonts["body"]).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(picker, text=self.messages["settings_browse"], style="Row.TButton",
                   command=self._pick_folder).pack(side="left", padx=(PAD_SMALL, 0))

        self._vars["file_manager"] = tk.StringVar()
        self._add_entry(left, "settings_file_manager", "file_manager")

        self._vars["interval_sec"] = tk.StringVar()
        self._add_entry(left, "settings_interval", "interval_sec", width=8)

        self._vars["history_limit"] = tk.StringVar()
        self._add_entry(left, "settings_history_limit", "history_limit", width=8)

        for key, label in (
            ("open_view_after_download", "settings_open_view"),
            ("open_folder_after_download", "settings_open_folder"),
            ("clear_clipboard_after_download", "settings_clear_clipboard"),
            ("ask_audio_language", "settings_ask_audio"),
            ("no_playlist", "settings_no_playlist"),
            ("restrict_filenames", "settings_restrict"),
            ("use_tray", "settings_use_tray"),
            ("start_minimized", "settings_start_minimized"),
            ("show_startup_notification", "settings_startup_notification"),
            ("autostart", "settings_autostart"),
        ):
            self._vars[key] = tk.BooleanVar()
            ttk.Checkbutton(
                right, text=self.messages[label], variable=self._vars[key], style="TCheckbutton"
            ).pack(anchor="w", pady=2)

        ttk.Label(right, text=self.messages["settings_restart_note"], style="Panel.Muted.TLabel",
                  wraplength=360, justify="left").pack(anchor="w", pady=(PAD_SMALL, 0))

        return page

    def _language_values(self) -> List[str]:
        """Return the selectable UI languages as display labels."""
        from . import i18n

        return [self.messages.language_label(code) for code in i18n.available_languages()]

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

    def _load_settings(self) -> None:
        """Fill the form from the live configuration."""
        self._settings_status.configure(text="")
        self._vars["language"].set(self.messages.language_label(self.config.language))
        self._vars["default_format"].set(
            self.messages["format_mp4"] if self.config.default_format == "mp4" else self.messages["format_mp3"]
        )
        self._vars["download_dir"].set(self.config.download_dir)
        self._vars["file_manager"].set(self.config.file_manager)
        self._vars["interval_sec"].set("{0:g}".format(self.config.interval_sec))
        self._vars["history_limit"].set(str(self.config.history_limit))
        for key in (
            "open_view_after_download",
            "open_folder_after_download",
            "clear_clipboard_after_download",
            "ask_audio_language",
            "no_playlist",
            "restrict_filenames",
            "use_tray",
            "start_minimized",
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
        for key in (
            "open_view_after_download",
            "open_folder_after_download",
            "clear_clipboard_after_download",
            "ask_audio_language",
            "no_playlist",
            "restrict_filenames",
            "use_tray",
            "start_minimized",
            "show_startup_notification",
            "autostart",
        ):
            setattr(self.config, key, bool(self._vars[key].get()))

        self._on_save_settings()
        self._load_settings()
        self._settings_status.configure(text=self.messages["settings_saved"])
        self.window.after(4000, lambda: self._settings_status.configure(text=""))

    # ------------------------------------------------------------------
    # About page
    # ------------------------------------------------------------------
    def _build_about(self, master: tk.Misc) -> ttk.Frame:
        """Create the about page with version, paths and dependency status.

        :param master: The page container.
        :return: The page frame.
        """
        page = ttk.Frame(master, style="TFrame", padding=PAD)
        ttk.Label(page, text=APP_SHORT_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Version {0}".format(APP_VERSION), style="Muted.TLabel").pack(anchor="w")
        ttk.Label(page, text=self.messages["about_text"], wraplength=760, justify="left").pack(
            anchor="w", pady=(PAD, 0)
        )

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

        ttk.Label(page, text=self.messages["about_license"], style="Muted.TLabel", wraplength=880,
                  justify="left").pack(side="bottom", anchor="w", pady=(PAD, 0))

        deps = ttk.LabelFrame(page, text=self.messages["about_dependencies"], style="Card.TLabelframe",
                              padding=PAD)
        deps.pack(fill="both", expand=True, pady=(PAD, 0))
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

        return page

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
