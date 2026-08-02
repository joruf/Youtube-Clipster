"""The Discover page: related songs list plus Spotify-like playback controls."""

from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from . import theme
from .config import Config
from .discover import (
    MODE_DEEZER,
    MODE_LISTENBRAINZ,
    MODE_RELATED,
    MODE_SEARCH,
    DiscoverTrack,
    dedupe_tracks,
)
from .downloader import cookies_are_configured, user_facing_ytdlp_error
from .history import format_duration
from .i18n import Messages
from .logging_setup import get_logger
from .player import (
    BACKEND_AUDIO,
    BACKEND_FFPLAY,
    BACKEND_MPV,
    DiscoverPlayer,
    PlayStartResult,
    format_stream_rate,
    video_embed_available,
    watch_url,
)
from .spectrum import EQ_BAND_LABELS, EQ_BAR_COUNT, SPECTRUM_TWEEN_SEC, FakeSpectrum, SpectrumTween
from .theme import PAD, PAD_SMALL
from .visualizer import (
    DEFAULT_VISUALIZER,
    MOUNTAIN_BAR_COUNT,
    VIZ_COVER,
    VIZ_OFF,
    VIZ_PULSE,
    VIZ_SPECTRUM,
    VIZ_TEXT,
    VIZ_VISUALIZER,
    VIZ_WAVEFORM,
    VISUALIZER_MODES,
    downsample_waveform,
    generative_mountain_levels,
    generative_waveform,
    normalize_visualizer,
    visualizer_animates,
    visualizer_locale_key,
    visualizer_mode_choices,
)

log = get_logger(__name__)

_SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
#: Graphic-EQ / viz accent colour (classic red).
_EQ_COLOR = "#e74c3c"
#: Mountain visualizer fill (cyan) — visibly distinct from spectrum red bars.
_MOUNTAIN_COLOR = "#3ecfbf"
_MOUNTAIN_GLOW = "#1a6b63"
_EQ_BARS = EQ_BAR_COUNT
#: ~24 fps while an animated stage mode is active.
_EQ_TICK_MS = 42
_EQ_LABEL_PAD = 22
#: Minimum hit target for Streaming transport / reaction buttons (ttk width units).
_PLAYER_BTN_WIDTH = 4
_WAVEFORM_POINTS = 96
_QUEUE_COL_NUM = 36
_QUEUE_COL_CHANNEL = 140
_QUEUE_COL_DURATION = 56
_QUEUE_COL_ACTION = 48

#: Media-control glyphs for the Streaming player bar.
_ICON_PREV = "⏮"
_ICON_PLAY = "▶"
_ICON_PAUSE = "⏸"
_ICON_STOP = "⏹"
_ICON_NEXT = "⏭"


def _shorten(text: str, limit: int = 90) -> str:
    """Return ``text`` truncated with an ellipsis."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _fit_line(text: str, width: int, font) -> str:
    """Shorten ``text`` so it fits on one line of ``width`` pixels.

    :param text: Full label text.
    :param width: Available width in pixels.
    :param font: A Tk font object with ``measure``.
    :return: Possibly truncated text ending with ``…``.
    """
    clean = " ".join((text or "").split())
    if width <= 0 or font.measure(clean) <= width:
        return clean
    budget = width - font.measure("…")
    if budget <= 0:
        return "…"
    low, high = 0, len(clean)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(clean[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return clean[:low].rstrip() + "…"


def _attach_tooltip(widget: tk.Misc, text: str, *, background: str, foreground: str) -> None:
    """Show ``text`` in a small popup while the pointer rests on ``widget``.

    The popup is positioned so it stays inside the screen: when the widget sits
    near the right or bottom edge, the tip flips left and/or above instead of
    being clipped by the window border.
    """
    tip: Dict[str, Optional[tk.Toplevel]] = {"window": None}

    def hide(_event: Optional[tk.Event] = None) -> None:
        window = tip["window"]
        tip["window"] = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _screen_bounds(root: tk.Misc) -> tuple:
        try:
            left = int(root.winfo_vrootx())
            top = int(root.winfo_vrooty())
            width = int(root.winfo_vrootwidth())
            height = int(root.winfo_vrootheight())
            if width > 1 and height > 1:
                return left, top, left + width, top + height
        except (tk.TclError, TypeError, ValueError):
            pass
        try:
            width = int(root.winfo_screenwidth())
            height = int(root.winfo_screenheight())
            return 0, 0, width, height
        except (tk.TclError, TypeError, ValueError):
            return 0, 0, 1920, 1080

    def show(_event: Optional[tk.Event] = None) -> None:
        if tip["window"] is not None or not text:
            return
        try:
            anchor_x = int(widget.winfo_rootx())
            anchor_y = int(widget.winfo_rooty())
            anchor_w = int(widget.winfo_width())
            anchor_h = int(widget.winfo_height())
            root = widget.winfo_toplevel()
        except (tk.TclError, TypeError, ValueError):
            return
        screen_left, screen_top, screen_right, screen_bottom = _screen_bounds(root)
        margin = 8
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        window.configure(background=background)
        # Keep long URLs readable without forcing a huge horizontal popup.
        wrap = max(180, min(420, screen_right - screen_left - 2 * margin))
        label = tk.Label(
            window,
            text=text,
            background=background,
            foreground=foreground,
            justify="left",
            padx=8,
            pady=4,
            borderwidth=1,
            relief="solid",
            wraplength=wrap,
        )
        label.pack()
        window.update_idletasks()
        try:
            tip_w = int(window.winfo_reqwidth())
            tip_h = int(window.winfo_reqheight())
        except (tk.TclError, TypeError, ValueError):
            tip_w, tip_h = 200, 28

        # Prefer below the widget, right-aligned to its right edge when near the
        # right border so download-column tips stay fully visible.
        x = anchor_x
        y = anchor_y + anchor_h + 6
        if x + tip_w > screen_right - margin:
            x = anchor_x + anchor_w - tip_w
        if x < screen_left + margin:
            x = screen_left + margin
        if x + tip_w > screen_right - margin:
            x = max(screen_left + margin, screen_right - margin - tip_w)

        if y + tip_h > screen_bottom - margin:
            y = anchor_y - tip_h - 6
        if y < screen_top + margin:
            y = screen_top + margin
        if y + tip_h > screen_bottom - margin:
            y = max(screen_top + margin, screen_bottom - margin - tip_h)

        window.geometry("+{0}+{1}".format(int(x), int(y)))
        tip["window"] = window

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")


def _format_clock(seconds: float) -> str:
    """Return ``M:SS`` or ``H:MM:SS`` for a playback position or length."""
    total = int(max(0, round(float(seconds))))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{0}:{1:02d}:{2:02d}".format(hours, minutes, secs)
    return "{0}:{1:02d}".format(minutes, secs)


class DiscoverPage(ttk.Frame):
    """UI for searching related lyrics tracks and playing them in order."""

    def __init__(
        self,
        master: tk.Misc,
        messages: Messages,
        palette: theme.Palette,
        config: Config,
        fonts: dict,
        on_refresh: Callable[[], None],
        on_download: Callable[[DiscoverTrack], None],
        on_extend: Callable[[DiscoverTrack], None],
        on_like: Callable[[DiscoverTrack], None],
        on_dislike: Callable[[DiscoverTrack], None],
        on_mode_changed: Callable[[str], None],
    ) -> None:
        """
        :param master: Parent widget.
        :param messages: Translation table.
        :param palette: Colour scheme.
        :param config: Live configuration.
        :param fonts: Theme font map.
        :param on_refresh: Start a background Discover search.
        :param on_download: Auto-download the selected track with defaults.
        :param on_extend: Fetch more related songs from the given track.
        :param on_like: Thumbs-up — prefer similar songs next.
        :param on_dislike: Thumbs-down — avoid similar songs next.
        :param on_mode_changed: Persist the selected search mode.
        """
        super().__init__(master, style="TFrame")
        self.messages = messages
        self.palette = palette
        self.config = config
        self.fonts = fonts
        self._on_refresh = on_refresh
        self._on_download = on_download
        self._on_extend = on_extend
        self._on_like = on_like
        self._on_dislike = on_dislike
        self._on_mode_changed = on_mode_changed
        #: Optional gate: return ``False`` to block Streaming actions (terms declined).
        self.ensure_terms: Optional[Callable[[], bool]] = None

        self.player = DiscoverPlayer()
        self._tracks: List[DiscoverTrack] = []
        self._selected = -1
        self._row_frames: List[ttk.Frame] = []
        self._busy = False
        self._loading = False
        self._spinner_job: Optional[str] = None
        self._spinner_index = 0
        self._playback_check_job: Optional[str] = None
        self._end_poll_job: Optional[str] = None
        self._extend_requested = False
        self._play_token = 0
        #: When the queue runs out mid-session, continue once new tracks arrive.
        self._resume_after_extend = False
        self._seek_dragging = False
        self._eq_job: Optional[str] = None
        self._eq_levels = [0.0] * _EQ_BARS
        self._viz_levels = [0.0] * MOUNTAIN_BAR_COUNT
        self._eq_tween = SpectrumTween(SPECTRUM_TWEEN_SEC)
        self._eq_fake = FakeSpectrum(_EQ_BARS, seed=11)
        self._cover_photo: Optional[tk.PhotoImage] = None
        self._cover_image = None
        self._cover_job: Optional[str] = None
        self._cover_url = ""
        self._viz_mpv_tried = False
        self._title_labels: List[ttk.Label] = []
        self._title_full: List[str] = []

        self._build()

    def _build(self) -> None:
        """Create the toolbar, player panes and footer status."""
        # Footer first so pack(side=bottom) keeps status visible under the split.
        status_box = ttk.LabelFrame(
            self, text=self.messages["discover_status"], style="Card.TLabelframe", padding=PAD_SMALL
        )
        status_box.pack(side="bottom", fill="x", padx=PAD, pady=(0, PAD))
        status_row = ttk.Frame(status_box, style="Panel.TFrame")
        status_row.pack(fill="x")
        self._spinner = ttk.Label(status_row, text="", style="Panel.Accent.TLabel", width=2)
        self._spinner.pack(side="left", padx=(0, PAD_SMALL))
        self._status = ttk.Label(
            status_row,
            text=self.messages["discover_status_idle"],
            style="Panel.Muted.TLabel",
            wraplength=940,
            justify="left",
        )
        self._status.pack(side="left", fill="x", expand=True, anchor="w")
        self._load_bar = ttk.Progressbar(status_box, mode="indeterminate", length=200)
        # Packed only while loading so the idle Status box stays compact.
        self._status_box = status_box

        actions = ttk.Frame(self, style="Toolbar.TFrame", padding=(PAD, PAD))
        actions.pack(fill="x")
        self._refresh_btn = ttk.Button(
            actions,
            text=self.messages["discover_refresh"],
            style="Accent.TButton",
            command=self._on_refresh,
        )
        self._refresh_btn.pack(side="left")

        ttk.Label(actions, text=self.messages["discover_mode"], style="Panel.Muted.TLabel").pack(
            side="left", padx=(PAD, PAD_SMALL)
        )
        self._mode_var = tk.StringVar()
        self._mode_values = {
            self.messages["discover_mode_search"]: MODE_SEARCH,
            self.messages["discover_mode_related"]: MODE_RELATED,
            self.messages["discover_mode_deezer"]: MODE_DEEZER,
            self.messages["discover_mode_listenbrainz"]: MODE_LISTENBRAINZ,
        }
        self._mode_labels = {value: key for key, value in self._mode_values.items()}
        mode_box = ttk.Combobox(
            actions,
            state="readonly",
            textvariable=self._mode_var,
            values=list(self._mode_values.keys()),
            width=36,
            font=self.fonts["body"],
        )
        mode_box.pack(side="left")
        mode_box.bind("<<ComboboxSelected>>", lambda _e: self._mode_selected())
        self._mode_box = mode_box

        split = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        split.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD_SMALL))
        self._split = split

        player = ttk.LabelFrame(
            split, text=self.messages["discover_now_playing"], style="Card.TLabelframe", padding=PAD
        )
        self._player_pane = player

        mode_row = ttk.Frame(player, style="Panel.TFrame")
        mode_row.pack(fill="x", pady=(0, PAD_SMALL))
        mode_left = ttk.Frame(mode_row, style="Panel.TFrame")
        mode_left.pack(side="left")
        ttk.Label(mode_left, text=self.messages["discover_playback_mode"], style="Panel.Muted.TLabel").pack(
            side="left", padx=(0, PAD_SMALL)
        )
        self._playback_mode_var = tk.StringVar(
            value="video" if self.config.discover_play_video else "audio"
        )
        self._audio_radio = ttk.Radiobutton(
            mode_left,
            text=self.messages["discover_playback_mode_audio"],
            value="audio",
            variable=self._playback_mode_var,
            command=self._playback_mode_changed,
            style="TRadiobutton",
        )
        self._audio_radio.pack(side="left")
        self._video_radio = ttk.Radiobutton(
            mode_left,
            text=self.messages["discover_playback_mode_video"],
            value="video",
            variable=self._playback_mode_var,
            command=self._playback_mode_changed,
            style="TRadiobutton",
        )
        self._video_radio.pack(side="left", padx=(PAD_SMALL, 0))
        self._viz_label = ttk.Label(
            mode_left, text=self.messages["discover_viz_label"], style="Panel.Muted.TLabel"
        )
        self._viz_var = tk.StringVar()
        # Ordered string labels (never tuples / dict views) so the popdown Listbox fills.
        viz_labels, self._viz_values, self._viz_labels = visualizer_mode_choices(self.messages)
        self._viz_box = ttk.Combobox(
            mode_left,
            state="readonly",
            textvariable=self._viz_var,
            width=16,
            height=len(VISUALIZER_MODES),
        )
        # Assign values after construction: some Tk builds drop constructor `values=`
        # when combined with readonly + a later StringVar sync.
        self._viz_box.configure(values=viz_labels)
        self._viz_box.bind("<<ComboboxSelected>>", lambda _e: self._visualizer_selected())
        self._stream_rate = ttk.Label(
            mode_row,
            text=format_stream_rate(0.0),
            style="Panel.Muted.TLabel",
        )
        self._stream_rate.pack(side="right")
        _attach_tooltip(
            self._audio_radio,
            self.messages["discover_playback_mode_audio_tip"],
            background=self.palette.elevated,
            foreground=self.palette.text,
        )
        _attach_tooltip(
            self._video_radio,
            self.messages["discover_playback_mode_video_tip"],
            background=self.palette.elevated,
            foreground=self.palette.text,
        )
        # Stage controls are Audio-only; pack/hide from the current media mode.
        self._sync_stage_controls_visibility()

        self._video_host = tk.Frame(player, background="#0b0c0f", height=280, highlightthickness=0)
        self._video_host.pack(fill="both", expand=True, pady=(0, PAD_SMALL))
        self._video_host.pack_propagate(False)
        self._eq_canvas = tk.Canvas(
            self._video_host,
            background="#0b0c0f",
            highlightthickness=0,
            borderwidth=0,
        )
        self._video_placeholder = ttk.Label(
            self._video_host,
            text=self.messages["discover_video_placeholder"],
            style="Panel.Muted.TLabel",
            anchor="center",
            justify="center",
        )
        self._video_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self._video_host.bind("<Configure>", self._on_stage_configure, add="+")

        self._now_title = ttk.Label(
            player,
            text=self.messages["discover_idle"],
            style="Panel.TLabel",
            font=self.fonts.get("title", self.fonts["body"]),
            wraplength=360,
        )
        self._now_title.pack(anchor="w")
        self._now_meta = ttk.Label(player, text="", style="Panel.Muted.TLabel", wraplength=360)
        self._now_meta.pack(anchor="w", pady=(2, 0))
        self._up_next = ttk.Label(player, text="", style="Panel.Muted.TLabel", wraplength=360)
        self._up_next.pack(anchor="w", pady=(2, PAD_SMALL))

        seek_row = ttk.Frame(player, style="Panel.TFrame")
        seek_row.pack(fill="x", pady=(0, PAD_SMALL))
        self._time_elapsed = ttk.Label(seek_row, text="0:00", style="Panel.Muted.TLabel")
        self._time_elapsed.pack(side="left")
        self._seek_var = tk.DoubleVar(value=0.0)
        self._seek_scale = ttk.Scale(
            seek_row,
            from_=0,
            to=1,
            orient=tk.HORIZONTAL,
            variable=self._seek_var,
            command=self._on_seek_drag,
        )
        self._seek_scale.pack(side="left", fill="x", expand=True, padx=PAD_SMALL)
        self._time_total = ttk.Label(seek_row, text="-:--", style="Panel.Muted.TLabel")
        self._time_total.pack(side="left")
        self._seek_scale.bind("<ButtonPress-1>", self._on_seek_press, add="+")
        self._seek_scale.bind("<ButtonRelease-1>", self._on_seek_release, add="+")
        self._reset_seek_ui()

        controls = ttk.Frame(player, style="Panel.TFrame")
        controls.pack(fill="x")
        tip_bg = self.palette.elevated
        tip_fg = self.palette.text
        prev_btn = ttk.Button(
            controls,
            text=_ICON_PREV,
            style="Player.TButton",
            command=self.play_previous,
            width=_PLAYER_BTN_WIDTH,
        )
        prev_btn.pack(side="left")
        _attach_tooltip(prev_btn, self.messages["discover_prev"], background=tip_bg, foreground=tip_fg)
        self._play_btn = ttk.Button(
            controls,
            text=_ICON_PLAY,
            style="PlayerAccent.TButton",
            command=self.toggle_play,
            width=_PLAYER_BTN_WIDTH,
        )
        self._play_btn.pack(side="left", padx=PAD_SMALL)
        _attach_tooltip(self._play_btn, self.messages["discover_play"], background=tip_bg, foreground=tip_fg)
        stop_btn = ttk.Button(
            controls,
            text=_ICON_STOP,
            style="Player.TButton",
            command=self.stop_playback,
            width=_PLAYER_BTN_WIDTH,
        )
        stop_btn.pack(side="left", padx=(0, PAD_SMALL))
        _attach_tooltip(stop_btn, self.messages["discover_stop"], background=tip_bg, foreground=tip_fg)
        next_btn = ttk.Button(
            controls,
            text=_ICON_NEXT,
            style="Player.TButton",
            command=self.play_next,
            width=_PLAYER_BTN_WIDTH,
        )
        next_btn.pack(side="left")
        _attach_tooltip(next_btn, self.messages["discover_next"], background=tip_bg, foreground=tip_fg)
        dislike_btn = ttk.Button(
            controls,
            text=self.messages["discover_dislike"],
            style="Player.TButton",
            command=self.dislike_current,
            width=_PLAYER_BTN_WIDTH,
        )
        dislike_btn.pack(side="left", padx=(PAD_SMALL, 0))
        _attach_tooltip(dislike_btn, self.messages["discover_dislike"], background=tip_bg, foreground=tip_fg)
        like_btn = ttk.Button(
            controls,
            text=self.messages["discover_like"],
            style="Player.TButton",
            command=self.like_current,
            width=_PLAYER_BTN_WIDTH,
        )
        like_btn.pack(side="left", padx=(PAD_SMALL, 0))
        _attach_tooltip(like_btn, self.messages["discover_like"], background=tip_bg, foreground=tip_fg)
        download_btn = ttk.Button(
            controls,
            text=self.messages["discover_download"],
            style="PlayerAccent.TButton",
            command=self.download_current,
        )
        download_btn.pack(side="right")

        queue = ttk.LabelFrame(
            split, text=self.messages["discover_queue"], style="Card.TLabelframe", padding=PAD_SMALL
        )

        self._queue_header = ttk.Frame(queue, style="Panel.TFrame", padding=(PAD_SMALL, 4))
        self._queue_header.pack(fill="x")
        self._configure_queue_columns(self._queue_header)
        ttk.Label(
            self._queue_header, text=self.messages["discover_queue_col_num"], style="Panel.Muted.TLabel"
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            self._queue_header, text=self.messages["discover_queue_col_title"], style="Panel.Muted.TLabel"
        ).grid(row=0, column=1, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Label(
            self._queue_header, text=self.messages["discover_queue_col_channel"], style="Panel.Muted.TLabel"
        ).grid(row=0, column=2, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Label(
            self._queue_header,
            text=self.messages["column_duration"],
            style="Panel.Muted.TLabel",
            anchor="e",
        ).grid(row=0, column=3, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Label(
            self._queue_header,
            text=self.messages["discover_queue_col_download"],
            style="Panel.Muted.TLabel",
            anchor="e",
        ).grid(row=0, column=4, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Separator(queue, orient="horizontal").pack(fill="x", pady=(2, 0))

        list_wrap = ttk.Frame(queue, style="Panel.TFrame")
        list_wrap.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(list_wrap, background=self.palette.panel, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(list_wrap, orient="vertical", command=self._canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._body = ttk.Frame(self._canvas, style="Panel.TFrame")
        self._window = self._canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._body.bind("<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", self._on_queue_canvas_configure, add="+")
        self._bind_wheel(self._canvas)
        self._bind_wheel(self._body)
        self._bind_wheel(queue)
        self._bind_wheel(list_wrap)
        self._bind_wheel(self._queue_header)

        split.add(player, weight=3)
        split.add(queue, weight=3)
        try:
            split.pane(player, weight=3, minsize=320)
            split.pane(queue, weight=3, minsize=240)
        except tk.TclError:
            try:
                split.paneconfigure(player, minsize=320)
                split.paneconfigure(queue, minsize=240)
            except tk.TclError:
                pass
        player.bind("<Configure>", self._sync_player_wraplengths, add="+")

        self.reload_from_config()

    def _set_play_icon(self, playing: bool) -> None:
        """Show the play or pause glyph on the main transport button."""
        try:
            self._play_btn.configure(text=_ICON_PAUSE if playing else _ICON_PLAY)
        except tk.TclError:
            pass

    def _sync_player_wraplengths(self, _event: Optional[tk.Event] = None) -> None:
        """Keep Now Playing text wrapping to the current pane width."""
        try:
            width = max(120, int(self._player_pane.winfo_width()) - 2 * PAD)
        except (tk.TclError, TypeError, ValueError):
            return
        for label in (self._now_title, self._now_meta, self._up_next):
            try:
                label.configure(wraplength=width)
            except tk.TclError:
                pass

    def reload_from_config(self) -> None:
        """Sync mode/visualizer widgets from the live configuration."""
        mode = self.config.discover_mode if self.config.discover_mode in self._mode_labels else MODE_SEARCH
        self._mode_var.set(self._mode_labels.get(mode, self.messages["discover_mode_search"]))
        self._playback_mode_var.set("video" if self.config.discover_play_video else "audio")
        viz_labels, self._viz_values, self._viz_labels = visualizer_mode_choices(self.messages)
        try:
            self._viz_box.configure(values=viz_labels)
        except tk.TclError:
            pass
        viz = normalize_visualizer(getattr(self.config, "discover_visualizer", DEFAULT_VISUALIZER))
        self._viz_var.set(self._viz_labels.get(viz, self.messages[visualizer_locale_key(DEFAULT_VISUALIZER)]))
        self.player.set_visualizer_mode(viz)
        self._sync_stage_controls_visibility()

    def selected_mode(self) -> str:
        """Return the currently selected Discover mode key."""
        return self._mode_values.get(self._mode_var.get(), MODE_SEARCH)

    def selected_visualizer(self) -> str:
        """Return the currently selected stage visualizer mode id."""
        return self._viz_values.get(self._viz_var.get(), DEFAULT_VISUALIZER)

    def wants_video(self) -> bool:
        """Return ``True`` when the user selected in-tab video playback."""
        return self._playback_mode_var.get() == "video"

    def _sync_stage_controls_visibility(self) -> None:
        """Show Stage label/combobox only when Audio media mode is selected."""
        show = not self.wants_video()
        try:
            if show:
                if self._viz_label.winfo_manager() != "pack":
                    self._viz_label.pack(side="left", padx=(PAD, PAD_SMALL))
                if self._viz_box.winfo_manager() != "pack":
                    self._viz_box.pack(side="left")
            else:
                if self._viz_label.winfo_manager():
                    self._viz_label.pack_forget()
                if self._viz_box.winfo_manager():
                    self._viz_box.pack_forget()
                # Video mode has no stage animation — free the redraw timer / mpv viz.
                self._stop_stage()
        except tk.TclError:
            pass

    def _mode_selected(self) -> None:
        """Persist the mode combobox choice."""
        self._on_mode_changed(self.selected_mode())

    def _visualizer_selected(self) -> None:
        """Persist the stage visualizer choice and refresh the canvas."""
        mode = self.selected_visualizer()
        changed = normalize_visualizer(self.config.discover_visualizer) != mode
        if changed:
            self.config.discover_visualizer = mode
            try:
                self.config.save()
            except Exception:
                log.debug("Could not save discover_visualizer", exc_info=True)
            self._viz_mpv_tried = False
        self.player.set_visualizer_mode(mode)
        # While audio is playing, keep the stage canvas mapped for the new mode.
        if self.player.playing and not self.wants_video():
            if not self._eq_canvas.winfo_ismapped():
                self._show_stage_audio()
                return
        self._apply_stage_for_mode(restart_animation=True)

    def _playback_mode_changed(self) -> None:
        """Persist Video/Audio choice and restart the current track if needed."""
        want_video = self.wants_video()
        self._sync_stage_controls_visibility()
        if bool(self.config.discover_play_video) == want_video:
            return
        self.config.discover_play_video = want_video
        try:
            self.config.save()
        except Exception:
            log.debug("Could not save discover_play_video", exc_info=True)
        self.player.clear_stream_cache()
        # Drop the equalizer immediately so Video mode does not keep showing it.
        self._stop_equalizer()
        self._set_stage_placeholder(self.messages["discover_video_placeholder"])
        if want_video:
            self._show_stage_idle()
        if self.player.playing or self.player.process_running or self.player.can_seek():
            if 0 <= self._selected < len(self._tracks):
                self.play_at(self._selected)
            return
        self._show_stage_idle()

    def set_status(self, text: str, level: str = "info") -> None:
        """Update the Discover status / info line.

        :param text: Message shown in the Status box.
        :param level: ``info``, ``ok``, ``warn`` or ``error``.
        """
        styles = {
            "info": "Panel.Muted.TLabel",
            "ok": "Panel.Success.TLabel",
            "warn": "Panel.Warning.TLabel",
            "error": "Panel.Danger.TLabel",
        }
        self._status.configure(
            text=text or self.messages["discover_status_idle"],
            style=styles.get(level, "Panel.Muted.TLabel"),
        )

    def set_loading(self, loading: bool, message: str = "") -> None:
        """Show or hide the loading spinner and indeterminate progress bar.

        :param loading: ``True`` while online work or playback startup runs.
        :param message: Optional status text shown next to the spinner.
        """
        self._loading = loading
        if loading:
            if message:
                self.set_status(message, "info")
            if self._load_bar.winfo_manager() != "pack":
                self._load_bar.pack(fill="x", pady=(PAD_SMALL, 0))
            try:
                self._load_bar.start(12)
            except tk.TclError:
                pass
            self._spin_once()
            return
        if self._spinner_job is not None:
            try:
                self.after_cancel(self._spinner_job)
            except tk.TclError:
                pass
            self._spinner_job = None
        self._spinner.configure(text="")
        try:
            self._load_bar.stop()
        except tk.TclError:
            pass
        if self._load_bar.winfo_manager() == "pack":
            self._load_bar.pack_forget()

    def _spin_once(self) -> None:
        """Advance the Unicode spinner while loading."""
        if not self._loading:
            self._spinner.configure(text="")
            return
        self._spinner.configure(text=_SPINNER_FRAMES[self._spinner_index % len(_SPINNER_FRAMES)])
        self._spinner_index += 1
        self._spinner_job = self.after(120, self._spin_once)

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Enable or disable the refresh action while a search runs."""
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._refresh_btn.configure(state=state)
        if busy:
            self.set_loading(True, message or self.messages["discover_loading"])
        elif not self._playback_check_job:
            self.set_loading(False)
            if message:
                self.set_status(message, "info")

    def show_progress(self, current: int, total: int, title: str) -> None:
        """Show live online-search progress for one seed title."""
        self.set_loading(
            True,
            self.messages.format(
                "discover_searching_seed",
                current=current,
                total=total,
                title=_shorten(title, 70),
            ),
        )

    def show_error(self, details: str) -> None:
        """Show a search failure message (bot / wiki dumps sanitized)."""
        self.set_busy(False)
        self.set_loading(False)
        text = user_facing_ytdlp_error(
            details,
            self.messages,
            cookies_configured=cookies_are_configured(self.config),
            context="discover",
        )
        self.set_status(text, "error")

    def show_empty(self, message_key: str = "discover_empty") -> None:
        """Clear the list and show an empty-state message."""
        self.set_busy(False)
        self.set_loading(False)
        self._tracks = []
        self.player.set_playlist([])
        self._render_rows()
        level = "warn" if message_key in ("discover_no_seeds", "discover_blocked") else "info"
        self.set_status(self.messages[message_key], level)
        self._now_title.configure(text=self.messages["discover_idle"])
        self._now_meta.configure(text="")
        self._up_next.configure(text="")
        self._show_stage_idle()
        self._set_play_icon(False)

    def set_tracks(self, tracks: List[DiscoverTrack], status: str = "", level: str = "ok") -> None:
        """Replace the result list and playlist.

        :param tracks: Discover hits to show.
        :param status: Optional status text; defaults to a result count.
        :param level: Status colour level.
        """
        self.set_busy(False)
        self.set_loading(False)
        self._extend_requested = False
        self._resume_after_extend = False
        self._tracks = dedupe_tracks(tracks)
        self.player.set_playlist(self._tracks)
        self._selected = 0 if self._tracks else -1
        self._render_rows()
        if status:
            self.set_status(status, level)
        elif self._tracks:
            self.set_status(self.messages.format("discover_results", count=len(self._tracks)), "ok")
        else:
            self.set_status(self.messages["discover_empty"], "warn")
        if self._tracks:
            self._highlight(self._selected)
        self._update_up_next()

    def begin_discover(self) -> None:
        """Clear the queue for a new Find-Similar run while search stays busy."""
        self._extend_requested = False
        self._resume_after_extend = False
        self._tracks = []
        self.player.set_playlist([])
        self._selected = -1
        self._render_rows()
        self._now_title.configure(text=self.messages["discover_idle"])
        self._now_meta.configure(text="")
        self._up_next.configure(text="")
        self._show_stage_idle()
        self._set_play_icon(False)

    def finish_discover(self, status: str = "", level: str = "ok") -> None:
        """End the Find-Similar busy state and show the final status text."""
        self.set_busy(False)
        self.set_loading(False)
        self._extend_requested = False
        if status:
            self.set_status(status, level)
        elif self._tracks:
            self.set_status(self.messages.format("discover_results", count=len(self._tracks)), "ok")
        else:
            self.set_status(self.messages["discover_empty"], "warn")
        if self._tracks and self._selected < 0:
            self._selected = 0
            self._highlight(0)
        self._update_up_next()

    def append_tracks(
        self,
        tracks: List[DiscoverTrack],
        status: str = "",
        level: str = "ok",
        *,
        update_status: bool = True,
    ) -> int:
        """Append more songs without resetting the current selection.

        :param tracks: Extra Discover hits.
        :param status: Optional status text.
        :param level: Status colour level.
        :param update_status: When ``False``, keep the current status / progress text.
        :return: How many tracks were newly added.
        """
        self._extend_requested = False
        before = len(self._tracks)
        fresh = dedupe_tracks(tracks, against=self._tracks)
        if fresh:
            self._tracks.extend(fresh)
            self.player.append_tracks(fresh)
            # Append only — do not destroy existing rows (avoids queue flicker).
            self._append_rows(fresh, start_index=before)
            if before == 0 and self._selected < 0:
                self._selected = 0
            if 0 <= self._selected < len(self._tracks):
                self._highlight(self._selected)
            self._update_up_next()
            if self._resume_after_extend:
                self._resume_after_extend = False
                self.after(0, lambda index=before: self.play_at(index))
            elif self.player.playing or self.player.process_running:
                self._prefetch_upcoming()
        if update_status:
            if status:
                self.set_status(status, level)
            else:
                self.set_status(self.messages.format("discover_results", count=len(self._tracks)), "ok")
        return len(fresh)

    def video_ids(self) -> set:
        """Return video ids currently in the Discover list."""
        return {track.video_id for track in self._tracks if track.video_id}

    def maybe_extend(self, reason: str = "play") -> None:
        """Ask for more songs when the remaining list is getting short.

        :param reason: ``play`` or ``download`` - only used for status wording.
        """
        del reason
        if self._busy or self._extend_requested or not self._tracks:
            return
        remaining = len(self._tracks) - max(self._selected, 0) - 1
        threshold = max(1, int(self.config.discover_extend_remaining))
        if remaining > threshold:
            return
        seed = None
        if 0 <= self._selected < len(self._tracks):
            seed = self._tracks[self._selected]
        elif self._tracks:
            seed = self._tracks[-1]
        if seed is None:
            return
        self._extend_requested = True
        self.set_loading(True, self.messages["discover_extending"])
        self._on_extend(seed)

    def mark_extend_idle(self) -> None:
        """Clear the in-flight extend flag when a top-up finishes or fails."""
        self._extend_requested = False
        if self._resume_after_extend:
            self._resume_after_extend = False
            self._set_play_icon(False)
            self.set_loading(False)

    def _embed_wid(self) -> Optional[int]:
        """Return the video host window id for embedding, if available."""
        try:
            self._video_host.update_idletasks()
            return int(self._video_host.winfo_id())
        except (tk.TclError, TypeError, ValueError):
            return None

    def _prefetch_upcoming(self) -> None:
        """Warm the stream URL for the next track while the current one plays."""
        nxt = self._selected + 1
        if nxt >= len(self._tracks):
            return
        prefer_video = self.wants_video() and video_embed_available()
        self.player.prefetch(nxt, prefer_video=prefer_video)

    def _on_stage_configure(self, _event: Optional[tk.Event] = None) -> None:
        """Redraw the stage visualization when the host size changes."""
        if self._eq_canvas.winfo_ismapped():
            self._draw_stage()

    def _show_stage_idle(self) -> None:
        """Show the idle placeholder (no video / no stage animation)."""
        self._stop_stage()
        try:
            self._eq_canvas.place_forget()
        except tk.TclError:
            pass
        self._show_video_placeholder(True)

    def _show_stage_video(self) -> None:
        """Hide overlays so an embedded video player can fill the stage."""
        self._stop_stage()
        try:
            self._eq_canvas.place_forget()
        except tk.TclError:
            pass
        self._show_video_placeholder(False)

    def _show_stage_audio(self) -> None:
        """Show the audio-stage canvas for the configured visualizer mode."""
        self._show_video_placeholder(False)
        try:
            self._eq_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
            # Force geometry so the first paint is not skipped (1x1 canvas).
            self._eq_canvas.update_idletasks()
        except tk.TclError:
            return
        self._apply_stage_for_mode(restart_animation=True)

    def _show_stage_equalizer(self) -> None:
        """Compatibility alias for audio-stage display."""
        self._show_stage_audio()

    def _pcm_signal_alive(self) -> bool:
        """Return ``True`` when live PCM analysis currently has audible energy."""
        if not self.player.pcm_analysis_active():
            return False
        return (
            self.player.rms_level() > 0.01
            or self.player.energy_level() > 0.01
            or self.player.peak_level() > 0.01
            or max((abs(v) for v in self.player.waveform_samples()), default=0.0) > 0.01
            or max(self.player.spectrum_levels(), default=0.0) > 0.01
        )

    def _apply_stage_for_mode(self, *, restart_animation: bool = False) -> None:
        """Configure analyser / animation for the current visualizer mode."""
        mode = self.selected_visualizer()
        # Treat "playing" as the playback flag so generative fallbacks still move
        # even if the process probe briefly races during startup.
        playing = bool(self.player.playing)
        if mode == VIZ_VISUALIZER and playing and not self._viz_mpv_tried:
            self._viz_mpv_tried = True
            if not self.player.start_mpv_visualizer():
                self.set_status(self.messages["discover_viz_mpv_fallback"], "info")
        elif mode != VIZ_VISUALIZER:
            self.player.stop_mpv_visualizer()
            self._viz_mpv_tried = False

        if mode == VIZ_COVER:
            self._ensure_cover_image()

        if not self._eq_canvas.winfo_ismapped():
            # Idle / video stage — do not start a hidden animation loop.
            if mode in (VIZ_OFF, VIZ_TEXT) or not visualizer_animates(mode):
                self._stop_stage_loop()
            return

        if mode in (VIZ_OFF, VIZ_TEXT) or not visualizer_animates(mode):
            self._stop_stage_loop()
            if mode == VIZ_OFF:
                self._eq_fake.reset()
                self._eq_tween.reset()
                self._eq_levels = [0.0] * _EQ_BARS
                self._viz_levels = [0.0] * MOUNTAIN_BAR_COUNT
            self._draw_stage()
            return

        if restart_animation or self._eq_job is None:
            self._eq_fake.set_playing(playing)
            if mode == VIZ_SPECTRUM and self._pcm_signal_alive():
                self._eq_tween.reset(self.player.spectrum_levels())
                self._eq_levels = self._eq_tween.current()
            self._tick_stage()

    def _stop_stage(self) -> None:
        """Stop animation, clear bars, and close secondary mpv visualizer."""
        self._stop_stage_loop()
        self._eq_fake.reset()
        self._eq_tween.reset()
        self._eq_levels = [0.0] * _EQ_BARS
        self._viz_levels = [0.0] * MOUNTAIN_BAR_COUNT
        self.player.stop_mpv_visualizer()
        self._viz_mpv_tried = False

    def _stop_stage_loop(self) -> None:
        """Cancel the stage redraw timer only."""
        if self._eq_job is not None:
            try:
                self.after_cancel(self._eq_job)
            except tk.TclError:
                pass
            self._eq_job = None

    def _stop_equalizer(self) -> None:
        """Compatibility alias used by older call sites."""
        self._stop_stage()

    def _settle_equalizer(self) -> None:
        """Keep the stage visible while animated modes calm after pause."""
        self._eq_fake.set_playing(False)
        mode = self.selected_visualizer()
        if mode in (VIZ_OFF, VIZ_TEXT, VIZ_COVER):
            self._draw_stage()
            return
        if self._eq_job is None and self._eq_canvas.winfo_ismapped():
            self._tick_stage()

    def _tick_stage(self) -> None:
        """Advance the active visualization and schedule the next frame."""
        self._eq_job = None
        mode = self.selected_visualizer()
        playing = bool(self.player.playing)
        keep = False

        if mode == VIZ_SPECTRUM:
            levels = self.player.spectrum_levels()
            if self._pcm_signal_alive() and max(levels, default=0.0) > 0.01:
                self._eq_tween.set_target(levels)
                self._eq_levels = self._eq_tween.current()
            else:
                self._eq_levels = self._eq_fake.tick(playing=playing)
            keep = playing or max(self._eq_levels, default=0.0) > 0.01
        elif mode == VIZ_VISUALIZER:
            # Distinct generative mountain — never the same FakeSpectrum EQ bars.
            envelope = self._eq_fake.tick(playing=playing)
            energy = max(envelope, default=0.0)
            if energy < 0.05 and playing:
                energy = 0.55
            self._viz_levels = generative_mountain_levels(
                MOUNTAIN_BAR_COUNT,
                energy=energy,
                phase=time.monotonic(),
            )
            keep = playing or max(self._viz_levels, default=0.0) > 0.01
        elif mode in (VIZ_WAVEFORM, VIZ_PULSE):
            if self._pcm_signal_alive():
                # Keep fake envelope in sync so fallback can resume seamlessly.
                self._eq_fake.set_playing(playing)
                keep = True
            else:
                self._eq_levels = self._eq_fake.tick(playing=playing)
                keep = playing or max(self._eq_levels, default=0.0) > 0.01
        elif mode == VIZ_COVER:
            keep = playing  # optional light motion while playing
        else:
            keep = False

        self._draw_stage()
        if keep and visualizer_animates(mode):
            self._eq_job = self.after(_EQ_TICK_MS, self._tick_stage)

    def _tick_equalizer(self) -> None:
        """Compatibility alias."""
        self._tick_stage()

    def _draw_stage(self) -> None:
        """Paint the active visualizer mode into the stage canvas."""
        canvas = self._eq_canvas
        try:
            width = max(1, int(canvas.winfo_width()))
            height = max(1, int(canvas.winfo_height()))
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            # Geometry not ready yet — try again on the next tick / Configure.
            return
        canvas.delete("all")
        mode = self.selected_visualizer()
        if mode == VIZ_OFF:
            return
        if mode == VIZ_TEXT:
            track = self.current_track()
            title = (track.title if track else "") or self.messages["discover_idle"]
            channel = track.uploader if track else ""
            canvas.create_text(
                width // 2,
                height // 2 - (10 if channel else 0),
                text=_shorten(title, 48),
                fill=self.palette.text,
                font=self.fonts.get("heading", self.fonts["body"]),
                anchor="center",
            )
            if channel:
                canvas.create_text(
                    width // 2,
                    height // 2 + 18,
                    text=_shorten(channel, 40),
                    fill=self.palette.muted,
                    font=self.fonts.get("small", self.fonts["body"]),
                    anchor="center",
                )
            return
        if mode == VIZ_COVER:
            self._draw_cover(canvas, width, height)
            return
        if mode == VIZ_WAVEFORM:
            self._draw_waveform(canvas, width, height)
            return
        if mode == VIZ_PULSE:
            self._draw_pulse(canvas, width, height)
            return
        if mode == VIZ_VISUALIZER:
            self._draw_mountain(canvas, width, height, self._viz_levels)
            return
        # spectrum: real PCM Goertzel bars (FakeSpectrum fallback when no PCM)
        self._draw_bars(canvas, width, height, self._eq_levels)

    def _draw_equalizer(self) -> None:
        """Compatibility alias."""
        self._draw_stage()

    def _draw_bars(self, canvas: tk.Canvas, width: int, height: int, levels: List[float]) -> None:
        """Paint coloured frequency bars with kHz labels."""
        label_pad = _EQ_LABEL_PAD if height > 60 else 0
        gap = max(3, width // (_EQ_BARS * 10))
        bar_width = max(4, (width - gap * (_EQ_BARS + 1)) // _EQ_BARS)
        track = "#2a3140"
        label_color = self.palette.muted
        usable = max(8, height - label_pad - 12)
        radius = max(2, min(6, bar_width // 2))
        for index, level in enumerate(levels):
            colour = _EQ_COLOR
            label = EQ_BAND_LABELS[index] if index < len(EQ_BAND_LABELS) else ""
            x0 = gap + index * (bar_width + gap)
            x1 = x0 + bar_width
            bar_h = max(2, int(usable * max(0.02, level))) if level > 0.01 else 2
            y1 = height - label_pad - 4
            y0 = y1 - bar_h
            canvas.create_rectangle(x0, 8, x1, y1, fill=track, outline="")
            body_top = min(y1 - 1, y0 + radius)
            if body_top < y1:
                canvas.create_rectangle(x0, body_top, x1, y1, fill=colour, outline="")
            if bar_h > radius:
                canvas.create_oval(x0, y0, x1, y0 + 2 * radius, fill=colour, outline="")
            else:
                canvas.create_rectangle(x0, y0, x1, y1, fill=colour, outline="")
            if label_pad:
                canvas.create_text(
                    (x0 + x1) // 2,
                    height - 8,
                    text=label,
                    fill=label_color,
                    font=self.fonts.get("small", self.fonts["body"]),
                    anchor="s",
                )

    def _draw_waveform(self, canvas: tk.Canvas, width: int, height: int) -> None:
        """Paint an oscilloscope polyline from PCM, or a generative fallback."""
        if self._pcm_signal_alive():
            samples = downsample_waveform(self.player.waveform_samples(), _WAVEFORM_POINTS)
        else:
            energy = max(self._eq_levels, default=0.0)
            if energy < 0.05 and self.player.playing:
                energy = 0.45
            samples = generative_waveform(_WAVEFORM_POINTS, energy=energy, phase=time.monotonic())
        mid = height // 2
        amp = max(8, height // 2 - 16)
        points = []
        for index, value in enumerate(samples):
            x = int(index * (width - 1) / max(1, len(samples) - 1))
            y = int(mid - float(value) * amp)
            points.extend((x, y))
        canvas.create_line(0, mid, width, mid, fill="#2a3140", width=1)
        if len(points) >= 4:
            canvas.create_line(*points, fill=_EQ_COLOR, width=2, smooth=True)

    def _draw_mountain(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        levels: List[float],
    ) -> None:
        """Paint a dense mirrored mountain silhouette (visualizer mode)."""
        if not levels:
            return
        mid = height // 2
        amp = max(10, mid - 10)
        count = len(levels)
        # Upper silhouette polygon (filled mountain).
        top_points: List[int] = [0, mid]
        for index, level in enumerate(levels):
            x = int(index * (width - 1) / max(1, count - 1))
            y = int(mid - max(0.0, min(1.0, float(level))) * amp)
            top_points.extend((x, y))
        top_points.extend((width, mid))
        canvas.create_polygon(*top_points, fill=_MOUNTAIN_GLOW, outline="")
        # Dense thin bars for motion detail (no kHz labels — not spectrum).
        gap = max(1, width // (count * 8))
        bar_w = max(2, (width - gap * (count + 1)) // count)
        for index, level in enumerate(levels):
            h = max(1, int(amp * max(0.0, min(1.0, float(level)))))
            if h < 2:
                continue
            x0 = gap + index * (bar_w + gap)
            x1 = x0 + bar_w
            canvas.create_rectangle(x0, mid - h, x1, mid, fill=_MOUNTAIN_COLOR, outline="")
            # Mirrored reflection below the midline.
            canvas.create_rectangle(
                x0,
                mid,
                x1,
                mid + max(1, h // 2),
                fill=_MOUNTAIN_GLOW,
                outline="",
            )

    def _draw_pulse(self, canvas: tk.Canvas, width: int, height: int) -> None:
        """Paint a beat / energy ring in the stage centre."""
        if self._pcm_signal_alive():
            energy = self.player.energy_level()
        else:
            energy = max(self._eq_levels, default=0.0)
            if energy < 0.05 and self.player.playing:
                energy = 0.25 + 0.2 * abs(math.sin(time.monotonic() * 4.0))
        cx, cy = width // 2, height // 2
        base = max(18, min(width, height) // 6)
        radius = int(base + (min(width, height) // 3) * max(0.0, min(1.0, energy)))
        for scale, fill in ((1.35, "#5a1f1c"), (1.15, "#8e2c24"), (1.0, _EQ_COLOR)):
            r = int(radius * scale)
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=fill, width=3)

    def _draw_cover(self, canvas: tk.Canvas, width: int, height: int) -> None:
        """Paint the track thumbnail, or a title placeholder when missing."""
        del width, height
        photo = self._cover_photo
        if photo is not None:
            canvas.create_image(
                int(canvas.winfo_width()) // 2,
                int(canvas.winfo_height()) // 2,
                image=photo,
                anchor="center",
            )
            return
        track = self.current_track()
        title = (track.title if track else "") or self.messages["discover_idle"]
        canvas.create_text(
            int(canvas.winfo_width()) // 2,
            int(canvas.winfo_height()) // 2,
            text=_shorten(title, 42),
            fill=self.palette.muted,
            font=self.fonts.get("heading", self.fonts["body"]),
            anchor="center",
        )

    def _ensure_cover_image(self) -> None:
        """Load the current track thumbnail into ``_cover_photo`` (async)."""
        track = self.current_track()
        url = (track.thumbnail if track else "") or ""
        if url == self._cover_url and self._cover_photo is not None:
            return
        self._cover_url = url
        self._cover_photo = None
        self._cover_image = None
        if not url:
            return
        token = url

        def worker() -> None:
            photo = None
            image = None
            try:
                from io import BytesIO
                from urllib.request import Request, urlopen

                from PIL import Image, ImageTk

                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=8) as response:
                    raw = response.read()
                image = Image.open(BytesIO(raw)).convert("RGB")
                # Fit inside a typical stage; exact canvas size may differ.
                image.thumbnail((420, 420))
                # PhotoImage must be created on the UI thread.
            except Exception:
                log.debug("Cover download failed for %s", url, exc_info=True)
                image = None

            def apply() -> None:
                nonlocal photo
                if self._cover_url != token:
                    return
                if image is None:
                    self._cover_photo = None
                    self._cover_image = None
                    self._draw_stage()
                    return
                try:
                    from PIL import ImageTk

                    photo = ImageTk.PhotoImage(image)
                except Exception:
                    photo = None
                self._cover_photo = photo
                self._cover_image = image
                if self._eq_canvas.winfo_ismapped():
                    self._draw_stage()

            try:
                self.after(0, apply)
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="clipster-cover", daemon=True).start()

    def _configure_queue_columns(self, frame: tk.Misc) -> None:
        """Apply the shared queue column geometry to a header or row frame."""
        frame.columnconfigure(0, minsize=_QUEUE_COL_NUM, weight=0)
        frame.columnconfigure(1, weight=1, minsize=80)
        frame.columnconfigure(2, minsize=_QUEUE_COL_CHANNEL, weight=0)
        frame.columnconfigure(3, minsize=_QUEUE_COL_DURATION, weight=0)
        frame.columnconfigure(4, minsize=_QUEUE_COL_ACTION, weight=0)

    def _on_queue_canvas_configure(self, event: tk.Event) -> None:
        """Keep the scroll body as wide as the canvas and refresh title ellipsis."""
        try:
            self._canvas.itemconfigure(self._window, width=event.width)
        except tk.TclError:
            pass
        self._refresh_title_ellipsis()

    def _refresh_title_ellipsis(self) -> None:
        """Truncate queue titles to the current title-column width."""
        font = self.fonts.get("body")
        if font is None:
            return
        for label, full in zip(self._title_labels, self._title_full):
            try:
                width = max(24, int(label.winfo_width()) - 4)
            except (tk.TclError, TypeError, ValueError):
                continue
            try:
                label.configure(text=_fit_line(full, width, font))
            except tk.TclError:
                pass

    def _render_rows(self) -> None:
        """Rebuild the scrollable track rows under the fixed column header."""
        for child in self._body.winfo_children():
            child.destroy()
        self._row_frames = []
        self._title_labels = []
        self._title_full = []
        if not self._tracks:
            ttk.Label(self._body, text=self.messages["discover_empty"], style="Panel.Muted.TLabel").pack(
                anchor="w", padx=PAD, pady=PAD
            )
            return
        self._append_rows(self._tracks, start_index=0)

    def _append_rows(self, tracks: List[DiscoverTrack], *, start_index: int) -> None:
        """Add ``tracks`` as new queue rows starting at ``start_index``.

        Existing rows stay mounted so incremental Discover updates do not flicker.
        """
        if start_index == 0 and not self._row_frames:
            # Drop the empty-state label when the first real rows arrive.
            for child in list(self._body.winfo_children()):
                try:
                    child.destroy()
                except tk.TclError:
                    pass
        for offset, track in enumerate(tracks):
            self._append_row(start_index + offset, track)
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except tk.TclError:
            pass

    def _append_row(self, index: int, track: DiscoverTrack) -> None:
        """Create one queue row for ``track`` at visual index ``index``."""
        row = ttk.Frame(self._body, style="Panel.TFrame", padding=(PAD_SMALL, 4))
        row.pack(fill="x", pady=1)
        self._configure_queue_columns(row)
        row.bind("<Button-1>", lambda _e, i=index: self.select_at(i))
        row.bind("<Double-Button-1>", lambda _e, i=index: self.play_at(i))

        number = ttk.Label(
            row,
            text="{0}.".format(index + 1),
            style="Panel.Muted.TLabel",
            anchor="e",
        )
        number.grid(row=0, column=0, sticky="ew")
        number.bind("<Button-1>", lambda _e, i=index: self.select_at(i))
        number.bind("<Double-Button-1>", lambda _e, i=index: self.play_at(i))

        full_title = " ".join((track.title or "").split())
        title = ttk.Label(row, text=full_title, style="Panel.TLabel", anchor="w")
        title.grid(row=0, column=1, sticky="ew", padx=(PAD_SMALL, 0))
        title.bind("<Button-1>", lambda _e, i=index: self.select_at(i))
        title.bind("<Double-Button-1>", lambda _e, i=index: self.play_at(i))
        title.bind(
            "<Configure>",
            lambda e, lbl=title, full=full_title: lbl.configure(
                text=_fit_line(full, max(24, int(e.width) - 4), self.fonts["body"])
            ),
            add="+",
        )
        self._title_labels.append(title)
        self._title_full.append(full_title)

        channel = " ".join((track.uploader or track.seed_title or "").split())
        channel_label = ttk.Label(
            row,
            text=_fit_line(channel, _QUEUE_COL_CHANNEL - 8, self.fonts["body"]) if channel else "",
            style="Panel.Muted.TLabel",
            anchor="w",
        )
        channel_label.grid(row=0, column=2, sticky="ew", padx=(PAD_SMALL, 0))
        channel_label.bind("<Button-1>", lambda _e, i=index: self.select_at(i))
        channel_label.bind("<Double-Button-1>", lambda _e, i=index: self.play_at(i))

        duration_label = ttk.Label(
            row,
            text=format_duration(int(track.duration or 0)),
            style="Panel.Muted.TLabel",
            anchor="e",
        )
        duration_label.grid(row=0, column=3, sticky="ew", padx=(PAD_SMALL, 0))
        duration_label.bind("<Button-1>", lambda _e, i=index: self.select_at(i))
        duration_label.bind("<Double-Button-1>", lambda _e, i=index: self.play_at(i))

        download_btn = ttk.Button(
            row,
            text=self.messages["discover_download_icon"],
            style="Row.TButton",
            width=3,
            command=lambda t=track: self._on_download(t),
        )
        download_btn.grid(row=0, column=4, sticky="e", padx=(PAD_SMALL, 0))
        _attach_tooltip(
            download_btn,
            watch_url(track),
            background=self.palette.elevated,
            foreground=self.palette.text,
        )

        self._row_frames.append(row)
        self._bind_wheel_tree(row)

    def _bind_wheel(self, widget: tk.Misc) -> None:
        """Attach mouse-wheel / two-finger scroll for Linux, macOS and Windows."""
        widget.bind(
            "<MouseWheel>",
            lambda e: self._queue_scroll(-1 if getattr(e, "delta", 0) > 0 else 1),
            add="+",
        )
        widget.bind("<Button-4>", lambda _e: self._queue_scroll(-1), add="+")
        widget.bind("<Button-5>", lambda _e: self._queue_scroll(1), add="+")

    def _bind_wheel_tree(self, widget: tk.Misc) -> None:
        """Bind scroll on ``widget`` and every descendant (labels, buttons)."""
        self._bind_wheel(widget)
        for child in widget.winfo_children():
            self._bind_wheel_tree(child)

    def _queue_scroll(self, direction: int) -> str:
        """Scroll the Streaming queue by a few units.

        :param direction: ``-1`` up, ``1`` down.
        :return: ``break`` so the event is not also handled elsewhere.
        """
        self._canvas.yview_scroll(direction * 3, "units")
        return "break"

    def select_at(self, index: int) -> None:
        """Mark a queue row as selected without starting playback."""
        if index < 0 or index >= len(self._tracks):
            return
        self._selected = index
        self._highlight(index)
        self._update_up_next()

    def _highlight(self, index: int) -> None:
        """Visually mark the active row title (number stays muted)."""
        for i, label in enumerate(self._title_labels):
            try:
                label.configure(style="Panel.Accent.TLabel" if i == index else "Panel.TLabel")
            except tk.TclError:
                pass

    def _update_up_next(self) -> None:
        """Show the upcoming queue title under Now Playing."""
        nxt = self._selected + 1
        if 0 <= nxt < len(self._tracks):
            self._up_next.configure(
                text=self.messages.format("discover_up_next", title=_shorten(self._tracks[nxt].title, 80))
            )
        elif self._tracks:
            self._up_next.configure(text=self.messages["discover_up_next_loading"])
        else:
            self._up_next.configure(text="")

    def _reset_seek_ui(self, *, duration: float = 0.0, position: float = 0.0) -> None:
        """Reset the scrubber and time labels for a track or idle state."""
        self._seek_dragging = False
        total = max(0.0, float(duration))
        pos = max(0.0, float(position))
        if total > 0:
            pos = min(pos, total)
            try:
                self._seek_scale.configure(to=total, state="normal")
            except tk.TclError:
                pass
            self._seek_var.set(pos)
            self._time_total.configure(text=_format_clock(total))
        else:
            try:
                self._seek_scale.configure(to=1, state="disabled")
            except tk.TclError:
                pass
            self._seek_var.set(0.0)
            self._time_total.configure(text="-:--")
        self._time_elapsed.configure(text=_format_clock(pos))

    def _refresh_seek_ui(self) -> None:
        """Sync scrubber labels from the live player position."""
        if self._seek_dragging:
            return
        duration = self.player.duration()
        position = self.player.position()
        if duration > 0:
            try:
                current_to = float(self._seek_scale.cget("to"))
            except (tk.TclError, TypeError, ValueError):
                current_to = 0.0
            if abs(current_to - duration) > 0.5:
                try:
                    self._seek_scale.configure(to=duration, state="normal")
                except tk.TclError:
                    pass
                self._time_total.configure(text=_format_clock(duration))
            self._seek_var.set(min(position, duration))
        else:
            self._seek_var.set(0.0)
        self._time_elapsed.configure(text=_format_clock(position))

    def _on_seek_press(self, _event: Optional[tk.Event] = None) -> None:
        """Mark that the user is dragging the scrubber."""
        self._seek_dragging = True

    def _on_seek_drag(self, _value: Optional[str] = None) -> None:
        """Update the elapsed label while the scrubber moves."""
        try:
            self._time_elapsed.configure(text=_format_clock(float(self._seek_var.get())))
        except (tk.TclError, TypeError, ValueError):
            pass

    def _on_seek_release(self, _event: Optional[tk.Event] = None) -> None:
        """Jump playback to the scrubber position when the user releases."""
        self._seek_dragging = False
        try:
            target = float(self._seek_var.get())
        except (tk.TclError, TypeError, ValueError):
            return
        if not self.player.can_seek():
            self._refresh_seek_ui()
            return
        if self.player.seek(target):
            self._set_play_icon(True)
            if self.player.backend in (BACKEND_MPV, BACKEND_FFPLAY):
                self._show_stage_video()
            elif self.wants_video():
                self._set_stage_placeholder(self.messages["discover_playback_video_fallback"])
                self._show_stage_idle()
            else:
                self._show_stage_equalizer()
            self._cancel_end_poll()
            self._end_poll_job = self.after(400, self._poll_track_end)
        self._refresh_seek_ui()

    def play_at(self, index: int) -> None:
        """Start continuous playback of playlist index ``index``."""
        if index < 0 or index >= len(self._tracks):
            return
        if self.ensure_terms is not None and not self.ensure_terms():
            return
        self._cancel_playback_check()
        self._cancel_end_poll()
        self._play_token += 1
        token = self._play_token
        track = self._tracks[index]
        self._selected = index
        self._highlight(index)
        self._now_title.configure(text=_shorten(track.title, 120))
        meta = track.uploader
        if track.seed_title:
            meta = "{0}  ·  seed: {1}".format(meta, _shorten(track.seed_title, 40)).strip(" ·")
        self._now_meta.configure(text=meta)
        self._reset_seek_ui(duration=float(track.duration or 0), position=0.0)
        self._update_up_next()
        self._set_play_icon(True)
        # Clear the previous stage (equalizer/video) while the stream resolves.
        self._stop_equalizer()
        self._set_stage_placeholder(self.messages["discover_playback_loading"])
        self._show_stage_idle()
        self.set_loading(True, self.messages["discover_playback_loading"])
        self.maybe_extend("play")
        # Resolve the next stream while the current one starts.
        self._prefetch_upcoming()
        prefer_video = self.wants_video()
        embed_wid = self._embed_wid() if prefer_video else None

        def on_done(result: PlayStartResult) -> None:
            self.after(0, lambda: self._on_play_ready(token, result))

        self.player.play_async(
            index,
            on_done=on_done,
            embed_wid=embed_wid,
            prefer_video=prefer_video,
        )

    def _on_play_ready(self, token: int, result: PlayStartResult) -> None:
        """Handle async stream start on the UI thread."""
        if token != self._play_token:
            return
        self.set_loading(False)
        if result.track is None or result.backend == "":
            self._set_stage_placeholder(self.messages["discover_video_placeholder"])
            self._show_stage_idle()
            detail = result.error or "?"
            if detail == "no_player":
                self.set_status(self.messages["discover_playback_need_mpv"], "error")
            else:
                # Log keeps the raw yt-dlp text; status never dumps wiki URLs.
                log.warning("Streaming start failed: %s", detail)
                self.set_status(
                    user_facing_ytdlp_error(
                        detail,
                        self.messages,
                        cookies_configured=cookies_are_configured(self.config),
                        context="playback",
                    ),
                    "error",
                )
            self._set_play_icon(False)
            self._reset_seek_ui()
            return
        if result.backend in (BACKEND_MPV, BACKEND_FFPLAY):
            self._set_stage_placeholder(self.messages["discover_video_placeholder"])
            self._show_stage_video()
            self.set_status(self.messages["discover_playback_streaming"], "ok")
            self._refresh_seek_ui()
            self._refresh_stream_rate()
            self._prefetch_upcoming()
            self._end_poll_job = self.after(400, self._poll_track_end)
            return
        if result.backend == BACKEND_AUDIO:
            if self.wants_video() or result.error == "video_embed_unavailable":
                # Video was requested but only audio could start — do not keep the EQ.
                self._set_stage_placeholder(self.messages["discover_playback_video_fallback"])
                self._show_stage_idle()
                self.set_status(self.messages["discover_playback_video_fallback"], "warn")
            else:
                self._set_stage_placeholder(self.messages["discover_video_placeholder"])
                self._show_stage_equalizer()
                self.set_status(self.messages["discover_playback_audio"], "ok")
            self._refresh_seek_ui()
            self._refresh_stream_rate()
            self._prefetch_upcoming()
            self._end_poll_job = self.after(400, self._poll_track_end)
            return
        self._set_stage_placeholder(self.messages["discover_video_placeholder"])
        self._show_stage_idle()
        self.set_status(self.messages["discover_playback_failed"], "error")
        self._set_play_icon(False)
        self._reset_seek_ui()

    def _set_stage_placeholder(self, text: str) -> None:
        """Update the text shown on the idle / fallback video stage."""
        try:
            self._video_placeholder.configure(text=text)
        except tk.TclError:
            pass

    def _show_video_placeholder(self, visible: bool) -> None:
        """Show or hide the text overlay on the video host."""
        try:
            if visible:
                self._video_placeholder.place(relx=0.5, rely=0.5, anchor="center")
            else:
                self._video_placeholder.place_forget()
        except tk.TclError:
            pass

    def _cancel_playback_check(self) -> None:
        """Cancel a pending playback startup poll."""
        if self._playback_check_job is not None:
            try:
                self.after_cancel(self._playback_check_job)
            except tk.TclError:
                pass
            self._playback_check_job = None

    def _cancel_end_poll(self) -> None:
        """Cancel the auto-next process watcher."""
        if self._end_poll_job is not None:
            try:
                self.after_cancel(self._end_poll_job)
            except tk.TclError:
                pass
            self._end_poll_job = None

    def _poll_track_end(self) -> None:
        """Advance to the next song when the local player finishes."""
        self._end_poll_job = None
        self._refresh_seek_ui()
        self._refresh_stream_rate()
        if self.player.track_finished():
            nxt = self._selected + 1
            if nxt < len(self._tracks):
                self.play_at(nxt)
            else:
                self._resume_after_extend = True
                self.maybe_extend("play")
                if self._extend_requested:
                    self.set_loading(True, self.messages["discover_extending"])
                    self.set_status(self.messages["discover_queue_ended"], "info")
                    self._set_play_icon(True)
                else:
                    self._resume_after_extend = False
                    self._set_play_icon(False)
                    self.set_status(self.messages["discover_queue_ended"], "info")
                    self._stop_equalizer()
                    self._refresh_stream_rate()
                    self._reset_seek_ui(
                        duration=self.player.duration(),
                        position=self.player.duration() or 0.0,
                    )
            return
        if self.player.playing and self.player.backend in (BACKEND_MPV, BACKEND_FFPLAY, BACKEND_AUDIO):
            self._end_poll_job = self.after(400, self._poll_track_end)

    def _refresh_stream_rate(self) -> None:
        """Update the live KB/s label from the player."""
        try:
            self._stream_rate.configure(text=format_stream_rate(self.player.stream_rate_bps()))
        except tk.TclError:
            pass

    def toggle_play(self) -> None:
        """Play or pause the current track."""
        if self.player.playing or self.player.process_running:
            self._cancel_playback_check()
            self._cancel_end_poll()
            self._play_token += 1
            self.player.pause()
            self.set_loading(False)
            self._settle_equalizer()
            self._refresh_seek_ui()
            self._refresh_stream_rate()
            self._set_play_icon(False)
            self.set_status(self.messages["discover_playback_paused"], "info")
            return
        if self.ensure_terms is not None and not self.ensure_terms():
            return
        if self.player.can_seek() and self._selected == self.player.index:
            if self.player.seek(self.player.position()):
                self._set_play_icon(True)
                if self.player.backend in (BACKEND_MPV, BACKEND_FFPLAY):
                    self._show_stage_video()
                    self.set_status(self.messages["discover_playback_streaming"], "ok")
                elif self.wants_video():
                    self._set_stage_placeholder(self.messages["discover_playback_video_fallback"])
                    self._show_stage_idle()
                    self.set_status(self.messages["discover_playback_video_fallback"], "warn")
                else:
                    self._show_stage_equalizer()
                    self.set_status(self.messages["discover_playback_audio"], "ok")
                self._cancel_end_poll()
                self._end_poll_job = self.after(400, self._poll_track_end)
                self._refresh_seek_ui()
                return
        if self._selected < 0 and self._tracks:
            self.play_at(0)
            return
        self.play_at(self._selected if self._selected >= 0 else 0)

    def stop_playback(self) -> None:
        """Stop playback completely (no auto-next, stays on the current song)."""
        self._cancel_playback_check()
        self._cancel_end_poll()
        self._play_token += 1
        self._resume_after_extend = False
        self.set_loading(False)
        self.player.stop()
        self._show_stage_idle()
        self._refresh_stream_rate()
        self._reset_seek_ui(
            duration=float(self._tracks[self._selected].duration or 0)
            if 0 <= self._selected < len(self._tracks)
            else 0.0,
            position=0.0,
        )
        self._set_play_icon(False)
        self.set_status(self.messages["discover_playback_stopped"], "info")

    def play_next(self) -> None:
        """Skip to the next track."""
        nxt = self._selected + 1
        if nxt >= len(self._tracks):
            self.maybe_extend("play")
            self.set_status(self.messages["discover_up_next_loading"], "info")
            return
        self.play_at(nxt)

    def play_previous(self) -> None:
        """Skip to the previous track."""
        if not self._tracks:
            return
        self.play_at(max(0, self._selected - 1))

    def current_track(self) -> Optional[DiscoverTrack]:
        """Return the selected / playing track, or ``None``."""
        track = self.player.current
        if track is None and 0 <= self._selected < len(self._tracks):
            track = self._tracks[self._selected]
        return track

    def like_current(self) -> None:
        """Thumbs-up the current track so similar songs are preferred."""
        track = self.current_track()
        if track is None:
            self.set_status(self.messages["discover_rate_need_track"], "warn")
            return
        self._on_like(track)

    def dislike_current(self) -> None:
        """Thumbs-down the current track so similar songs are avoided."""
        track = self.current_track()
        if track is None:
            self.set_status(self.messages["discover_rate_need_track"], "warn")
            return
        self._on_dislike(track)

    def remove_track(self, video_id: str, *, play_next: bool = True) -> bool:
        """Remove a track from the queue after a dislike.

        :param video_id: YouTube id to drop.
        :param play_next: When ``True``, advance playback after removal.
        :return: ``True`` when a track was removed.
        """
        if not video_id:
            return False
        index = next((i for i, track in enumerate(self._tracks) if track.video_id == video_id), -1)
        if index < 0:
            return False
        was_playing = index == self._selected and (self.player.playing or self.player.process_running)
        del self._tracks[index]
        self.player.set_playlist(self._tracks)
        if self._selected > index:
            self._selected -= 1
        elif self._selected == index:
            self._selected = min(index, len(self._tracks) - 1) if self._tracks else -1
        self._render_rows()
        if self._tracks and 0 <= self._selected < len(self._tracks):
            self._highlight(self._selected)
        self._update_up_next()
        if play_next and was_playing:
            if 0 <= self._selected < len(self._tracks):
                self.play_at(self._selected)
            else:
                self._cancel_playback_check()
                self._cancel_end_poll()
                self._play_token += 1
                self.player.stop()
                self._show_stage_idle()
                self._now_title.configure(text=self.messages["discover_idle"])
                self._now_meta.configure(text="")
                self._reset_seek_ui()
                self._set_play_icon(False)
        elif not self._tracks:
            self._selected = -1
            self._now_title.configure(text=self.messages["discover_idle"])
            self._now_meta.configure(text="")
            self._reset_seek_ui()
            self._set_play_icon(False)
        return True

    def download_current(self) -> None:
        """Download the track that is currently selected / playing."""
        track = self.current_track()
        if track is not None:
            self._on_download(track)
            self.maybe_extend("download")

    def destroy_player(self) -> None:
        """Stop external playback when the window goes away."""
        self._cancel_playback_check()
        self._cancel_end_poll()
        self._stop_equalizer()
        self._play_token += 1
        self.set_loading(False)
        self.player.stop()
