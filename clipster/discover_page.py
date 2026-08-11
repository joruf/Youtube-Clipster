"""The Discover page: related songs list plus Spotify-like playback controls."""

from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import playorder, theme
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
from .tooltip import attach as _attach_tooltip
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
_QUEUE_COL_ACTION = 36

#: Narrower than this and the channel column is dropped: the number, the title,
#: the length and both row buttons then still fit instead of being pushed out
#: of the visible area.
_QUEUE_NARROW = 420

#: Media-control glyphs for the Streaming player bar.
_ICON_PREV = "⏮"
_ICON_PLAY = "▶"
_ICON_PAUSE = "⏸"
_ICON_STOP = "⏹"
_ICON_NEXT = "⏭"
_ICON_SHUFFLE = "🔀"
_ICON_REPEAT = "🔁"
_ICON_REPEAT_ONE = "🔂"

#: Repeat modes, in the order the button cycles through them.  Re-exported from
#: :mod:`clipster.playorder`, which owns the rules; the names stay here because
#: the page is what everything else imports them from.
REPEAT_OFF = playorder.REPEAT_OFF
REPEAT_ALL = playorder.REPEAT_ALL
REPEAT_ONE = playorder.REPEAT_ONE
_REPEAT_ORDER = playorder.REPEAT_ORDER

#: What each repeat mode shows and says.
_REPEAT_LOOK = {
    REPEAT_OFF: (_ICON_REPEAT, "discover_repeat_off"),
    REPEAT_ALL: (_ICON_REPEAT, "discover_repeat_all"),
    REPEAT_ONE: (_ICON_REPEAT_ONE, "discover_repeat_one"),
}

#: Sleep timer choices in minutes; ``0`` is "keep playing".
_SLEEP_MINUTES = (0, 15, 30, 45, 60, 90, 120)


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
        #: Optional hide-from-queue (defaults to dislike when unset).
        self._on_hide: Optional[Callable[[DiscoverTrack], None]] = None
        #: Clear a stored like/dislike by video id.
        self._on_clear_vote: Optional[Callable[[str], None]] = None
        #: Play a voted track (enqueue if needed).
        self._on_play_vote: Optional[Callable[[str, str, str], None]] = None
        #: Fill the queue with the downloads that are already on disk.
        self.on_library: Optional[Callable[[], None]] = None
        #: Show the share code for one song (right-click on a queue row).
        self.on_share: Optional[Callable[[DiscoverTrack], None]] = None
        #: Optional gate: return ``False`` to block Streaming actions (terms declined).
        self.ensure_terms: Optional[Callable[[], bool]] = None
        #: Optional gate: return ``False`` when a track that is not on disk may
        #: not be streamed right now (mobile data, see :mod:`clipster.netmode`).
        self.allow_stream: Optional[Callable[[], bool]] = None
        #: Optional lookup of stored taste vote for a video id (``up`` / ``down``).
        self.vote_for: Optional[Callable[[str], Optional[str]]] = None
        #: Fired after the playlist changes so the app can persist it.
        self.on_queue_changed: Optional[Callable[[], None]] = None

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
        #: Shuffle, repeat and the shuffle bag - the same object the headless
        #: session uses, so both platforms answer "what plays next" alike.
        self._order = playorder.PlayOrder(
            shuffle=bool(getattr(config, "discover_shuffle", False)),
            repeat=str(getattr(config, "discover_repeat", REPEAT_OFF)),
        )
        #: Sleep timer: the Tk job that stops playback, and when it fires.
        self._sleep_job: Optional[str] = None
        self._sleep_ends_at = 0.0
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
        self._channel_labels: List[ttk.Label] = []
        self._channel_full: List[str] = []
        #: False once the queue got too narrow to keep the channel column.
        self._channel_visible = True
        self._header_channel: Optional[ttk.Label] = None
        #: Width of the two button columns; measured in :meth:`_build` so the
        #: header sits above the buttons on every theme, font size and DPI.
        self._col_action = _QUEUE_COL_ACTION
        #: Video ids that failed to start this session — skipped on auto-advance.
        self._unplayable_ids: set = set()

        self._build()
        self._sync_queue_visibility()

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

        # The other source: everything that is already on this machine.
        self._library_btn = ttk.Button(
            actions,
            text=self.messages["discover_library"],
            style="Row.TButton",
            command=self._library_clicked,
        )
        self._library_btn.pack(side="left", padx=(PAD_SMALL, 0))

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

        self._empty_hint = ttk.Label(
            self,
            text=self.messages.get("discover_search_hint", self.messages["discover_empty"]),
            style="Panel.Muted.TLabel",
            wraplength=720,
            justify="left",
        )
        # Packed only while the queue is empty (see _sync_queue_visibility).

        split = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        # Packed when tracks exist; hidden for the empty Streaming state.
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
        self._dislike_btn = dislike_btn
        like_btn = ttk.Button(
            controls,
            text=self.messages["discover_like"],
            style="Player.TButton",
            command=self.like_current,
            width=_PLAYER_BTN_WIDTH,
        )
        like_btn.pack(side="left", padx=(PAD_SMALL, 0))
        _attach_tooltip(like_btn, self.messages["discover_like"], background=tip_bg, foreground=tip_fg)
        self._like_btn = like_btn
        download_btn = ttk.Button(
            controls,
            text=self.messages["discover_download"],
            style="PlayerAccent.TButton",
            command=self.download_current,
        )
        download_btn.pack(side="right")

        # Own row on purpose: hung next to the transport buttons these three
        # would widen the player pane, and the Panedwindow would take that width
        # straight out of the queue beside it.
        modes = ttk.Frame(player, style="Panel.TFrame")
        modes.pack(fill="x", pady=(PAD_SMALL, 0))
        self._build_playback_modes(modes, tip_bg=tip_bg, tip_fg=tip_fg)

        queue = ttk.LabelFrame(
            split, text=self.messages["discover_queue"], style="Card.TLabelframe", padding=PAD_SMALL
        )

        # A Row.TButton asks for more than _QUEUE_COL_ACTION on most themes, and
        # grid would then widen the button columns of the rows only - leaving the
        # headings beside their buttons.  Measure one and let both agree on it.
        probe = ttk.Button(
            queue, text=self.messages["discover_hide_icon"], style="Row.TButton", width=3
        )
        # The column carries the button plus its left padding.
        self._col_action = max(_QUEUE_COL_ACTION, int(probe.winfo_reqwidth()) + PAD_SMALL)
        probe.destroy()

        # The rows live inside a scrolled canvas, so they are narrower than the
        # header by exactly the scrollbar.  Without the spacer on the right the
        # right-aligned headings would sit beside their values, not above them.
        header_wrap = ttk.Frame(queue, style="Panel.TFrame")
        header_wrap.pack(fill="x")
        self._queue_header = ttk.Frame(header_wrap, style="Panel.TFrame", padding=(PAD_SMALL, 4))
        self._queue_header.pack(side="left", fill="x", expand=True)
        self._header_gap = ttk.Frame(header_wrap, style="Panel.TFrame", width=0)
        self._header_gap.pack(side="right", fill="y")
        self._configure_queue_columns(self._queue_header)
        # width=1 everywhere: the header must take its column sizes from
        # _configure_queue_columns only, exactly like the rows below it, or a
        # long heading would shift the header out of line with the values.
        ttk.Label(
            self._queue_header, text=self.messages["discover_queue_col_num"],
            style="Panel.Muted.TLabel", width=1,
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            self._queue_header, text=self.messages["discover_queue_col_title"],
            style="Panel.Muted.TLabel", width=1,
        ).grid(row=0, column=1, sticky="ew", padx=(PAD_SMALL, 0))
        self._header_channel = ttk.Label(
            self._queue_header, text=self.messages["discover_queue_col_channel"],
            style="Panel.Muted.TLabel", width=1,
        )
        self._header_channel.grid(row=0, column=2, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Label(
            self._queue_header,
            text=self.messages["column_duration"],
            style="Panel.Muted.TLabel",
            anchor="e",
            width=1,
        ).grid(row=0, column=3, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Label(
            self._queue_header,
            text=self.messages["discover_queue_col_hide"],
            style="Panel.Muted.TLabel",
            anchor="e",
            width=1,
        ).grid(row=0, column=4, sticky="ew", padx=(PAD_SMALL, 0))
        ttk.Label(
            self._queue_header,
            text=self.messages["discover_queue_col_download"],
            style="Panel.Muted.TLabel",
            anchor="e",
            width=1,
        ).grid(row=0, column=5, sticky="ew", padx=(PAD_SMALL, 0))
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
        scrollbar.bind(
            "<Configure>",
            lambda event: self._header_gap.configure(width=max(0, int(event.width))),
            add="+",
        )
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

        votes = ttk.LabelFrame(
            self, text=self.messages["discover_votes"], style="Card.TLabelframe", padding=PAD_SMALL
        )
        self._votes_frame = votes
        self._votes_body = ttk.Frame(votes, style="Panel.TFrame")
        self._votes_body.pack(fill="both", expand=True)
        self._votes_empty = ttk.Label(
            votes, text=self.messages["discover_votes_empty"], style="Panel.Muted.TLabel"
        )
        self._vote_rows: List[ttk.Frame] = []

        self.reload_from_config()

    def _sync_queue_visibility(self) -> None:
        """Show only the toolbar + hint until songs are available to play."""
        # Keep the player/queue visible while a search is running so an empty
        # first Find-similar does not flash the idle hint before rows arrive.
        show_player = bool(self._tracks) or self._busy
        try:
            if show_player:
                if self._empty_hint.winfo_manager():
                    self._empty_hint.pack_forget()
                if self._split.winfo_manager() != "pack":
                    self._split.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD_SMALL))
            else:
                if self._split.winfo_manager():
                    self._split.pack_forget()
                if self._empty_hint.winfo_manager() != "pack":
                    self._empty_hint.pack(fill="x", padx=PAD, pady=(0, PAD))
            # Votes list stays available even with an empty queue.
            if self._vote_rows:
                if self._votes_frame.winfo_manager() != "pack":
                    self._votes_frame.pack(fill="x", padx=PAD, pady=(0, PAD))
            elif self._votes_frame.winfo_manager():
                self._votes_frame.pack_forget()
        except tk.TclError:
            pass

    def set_votes(self, entries: Sequence[Any]) -> None:
        """Render the persistent like/dislike list.

        :param entries: ``TasteEntry`` rows (newest first).
        """
        for child in list(self._votes_body.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass
        self._vote_rows = []
        tip_bg = self.palette.elevated
        tip_fg = self.palette.text
        for entry in entries:
            video_id = getattr(entry, "video_id", "") or ""
            if not video_id:
                continue
            vote = getattr(entry, "vote", "") or ""
            title = getattr(entry, "title", "") or video_id
            uploader = getattr(entry, "uploader", "") or ""
            row = ttk.Frame(self._votes_body, style="Panel.TFrame")
            row.pack(fill="x", pady=1)
            mark = "👍" if vote == "up" else "👎"
            ttk.Label(row, text=mark, style="Panel.Accent.TLabel" if vote == "up" else "Panel.Danger.TLabel", width=3).pack(
                side="left"
            )
            text = ttk.Frame(row, style="Panel.TFrame")
            text.pack(side="left", fill="x", expand=True, padx=(PAD_SMALL, 0))
            ttk.Label(text, text=_shorten(title, 80), style="Panel.TLabel").pack(anchor="w")
            if uploader:
                ttk.Label(text, text=_shorten(uploader, 60), style="Panel.Muted.TLabel").pack(anchor="w")
            clear_btn = ttk.Button(
                row,
                text="✕",
                style="Player.TButton",
                width=3,
                command=lambda vid=video_id: self._clear_vote_clicked(vid),
            )
            clear_btn.pack(side="right")
            _attach_tooltip(clear_btn, self.messages["discover_votes_clear"], background=tip_bg, foreground=tip_fg)
            play_btn = ttk.Button(
                row,
                text="▶",
                style="Player.TButton",
                width=3,
                command=lambda vid=video_id, t=title, u=uploader: self._play_vote_clicked(vid, t, u),
            )
            play_btn.pack(side="right", padx=(0, PAD_SMALL))
            _attach_tooltip(play_btn, self.messages["discover_votes_play"], background=tip_bg, foreground=tip_fg)
            self._vote_rows.append(row)
        if self._vote_rows:
            try:
                self._votes_empty.pack_forget()
            except tk.TclError:
                pass
        else:
            try:
                if self._votes_empty.winfo_manager() != "pack":
                    self._votes_empty.pack(fill="x", pady=PAD_SMALL)
            except tk.TclError:
                pass
        self._sync_queue_visibility()

    def _clear_vote_clicked(self, video_id: str) -> None:
        if self._on_clear_vote is not None:
            self._on_clear_vote(video_id)

    def _play_vote_clicked(self, video_id: str, title: str, uploader: str) -> None:
        if self.ensure_terms is not None and not self.ensure_terms():
            return
        if self._on_play_vote is not None:
            self._on_play_vote(video_id, title, uploader)

    def sync_vote_buttons(self) -> None:
        """Highlight like / dislike for the current track's stored vote."""
        vote = ""
        track = self.current_track()
        if track is not None and self.vote_for is not None:
            try:
                vote = self.vote_for(track.video_id) or ""
            except Exception:
                vote = ""
        like_style = "PlayerAccent.TButton" if vote == "up" else "Player.TButton"
        dislike_style = "PlayerAccent.TButton" if vote == "down" else "Player.TButton"
        try:
            self._like_btn.configure(style=like_style)
            self._dislike_btn.configure(style=dislike_style)
        except (tk.TclError, AttributeError):
            pass

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

    # ------------------------------------------------------------------
    # How the queue is played: shuffle, repeat, sleep timer
    # ------------------------------------------------------------------
    def _build_playback_modes(self, controls: tk.Misc, *, tip_bg: str, tip_fg: str) -> None:
        """Add the shuffle, repeat and sleep controls to the transport row.

        :param controls: The row that already holds play, stop and skip.
        :param tip_bg: Tooltip background colour.
        :param tip_fg: Tooltip text colour.
        :return: None
        """
        self._shuffle_btn = ttk.Button(
            controls,
            text=_ICON_SHUFFLE,
            style="Player.TButton",
            command=self.toggle_shuffle,
            width=_PLAYER_BTN_WIDTH,
        )
        self._shuffle_btn.pack(side="left")
        self._shuffle_tip = _attach_tooltip(
            self._shuffle_btn, "", background=tip_bg, foreground=tip_fg
        )
        self._repeat_btn = ttk.Button(
            controls,
            text=_ICON_REPEAT,
            style="Player.TButton",
            command=self.cycle_repeat,
            width=_PLAYER_BTN_WIDTH,
        )
        self._repeat_btn.pack(side="left", padx=(PAD_SMALL, 0))
        self._repeat_tip = _attach_tooltip(
            self._repeat_btn, "", background=tip_bg, foreground=tip_fg
        )

        self._sleep_var = tk.StringVar()
        self._sleep_labels = {
            self.messages["discover_sleep_off"] if minutes == 0
            else self.messages.format("discover_sleep_minutes", minutes=minutes): minutes
            for minutes in _SLEEP_MINUTES
        }
        sleep_box = ttk.Combobox(
            controls,
            state="readonly",
            textvariable=self._sleep_var,
            values=list(self._sleep_labels.keys()),
            width=12,
            font=self.fonts["body"],
        )
        sleep_box.current(0)
        sleep_box.pack(side="left", padx=(PAD, 0))
        sleep_box.bind("<<ComboboxSelected>>", lambda _e: self._sleep_selected())
        self._sleep_box = sleep_box
        _attach_tooltip(
            sleep_box, self.messages["discover_sleep_tip"], background=tip_bg, foreground=tip_fg
        )
        self._paint_playback_modes()

    def _paint_playback_modes(self) -> None:
        """Show which modes are on, on the buttons and in their tooltips."""
        self._shuffle_btn.configure(
            style="PlayerAccent.TButton" if self._order.shuffle else "Player.TButton"
        )
        self._shuffle_tip.set_text(
            self.messages["discover_shuffle_on" if self._order.shuffle
                          else "discover_shuffle_off"]
        )
        icon, message_key = _REPEAT_LOOK[self._order.repeat]
        self._repeat_btn.configure(
            text=icon,
            style="Player.TButton" if self._order.repeat == REPEAT_OFF
            else "PlayerAccent.TButton",
        )
        self._repeat_tip.set_text(self.messages[message_key])

    def set_shuffle(self, enabled: bool) -> None:
        """Set random order to a known value.

        Separate from :meth:`toggle_shuffle` because the setting can also arrive
        from somewhere else - the phone saving its settings - where there is no
        button press to announce and the configuration is already written.

        :param enabled: Whether to play in random order.
        :return: None
        """
        self._order.set_shuffle(enabled)
        self.config.discover_shuffle = self._order.shuffle
        self._paint_playback_modes()

    def toggle_shuffle(self) -> None:
        """Turn random order on or off.

        :return: None
        """
        self.set_shuffle(not self._order.shuffle)
        self.config.save()
        self.set_status(
            self.messages["discover_shuffle_on" if self._order.shuffle
                          else "discover_shuffle_off"],
            "info",
        )

    def set_repeat(self, mode: str) -> None:
        """Set the repeat mode to a known value.

        :param mode: ``off``, ``all`` or ``one``; anything else means ``off``.
        :return: None
        """
        self._order.set_repeat(mode)
        self.config.discover_repeat = self._order.repeat
        self._paint_playback_modes()

    def cycle_repeat(self) -> None:
        """Step through off, repeat all and repeat one.

        :return: None
        """
        self.set_repeat(self._order.cycle_repeat())
        self.config.save()
        self.set_status(self.messages[_REPEAT_LOOK[self._order.repeat][1]], "info")

    def next_index(self, *, automatic: bool) -> Optional[int]:
        """Return the row to play next, or ``None`` when the queue is through.

        The rules live in :class:`clipster.playorder.PlayOrder`, so the phone
        follows exactly the same ones.

        :param automatic: ``True`` when the track ended on its own.
        :return: The index to play, or ``None``.
        """
        return self._order.next_index(len(self._tracks), self._selected, automatic=automatic)

    def _reset_shuffle_bag(self) -> None:
        """Start a fresh random round, e.g. after the queue changed."""
        self._order.reset()

    # ------------------------------------------------------------------
    def _sleep_selected(self) -> None:
        """Start, restart or cancel the sleep timer from the selector."""
        minutes = self._sleep_labels.get(self._sleep_var.get(), 0)
        self.set_sleep_timer(minutes)

    def set_sleep_timer(self, minutes: int) -> None:
        """Stop playback after ``minutes``, or cancel a running timer.

        :param minutes: Minutes from now; ``0`` cancels.
        :return: None
        """
        self._cancel_sleep_timer()
        if minutes <= 0:
            self._sleep_ends_at = 0.0
            self.set_status(self.messages["discover_sleep_cancelled"], "info")
            return
        self._sleep_ends_at = time.monotonic() + minutes * 60
        self._sleep_job = self.after(minutes * 60 * 1000, self._sleep_reached)
        self.set_status(self.messages.format("discover_sleep_set", minutes=minutes), "info")

    def sleep_minutes_left(self) -> int:
        """Return the whole minutes left on the sleep timer, ``0`` when off."""
        if not self._sleep_ends_at:
            return 0
        return max(0, int((self._sleep_ends_at - time.monotonic()) // 60) + 1)

    def _cancel_sleep_timer(self) -> None:
        """Drop a pending sleep job without touching playback."""
        if self._sleep_job is not None:
            try:
                self.after_cancel(self._sleep_job)
            except tk.TclError:  # pragma: no cover - window going away
                pass
            self._sleep_job = None

    def _sleep_reached(self) -> None:
        """Stop playback because the sleep timer ran out."""
        self._sleep_job = None
        self._sleep_ends_at = 0.0
        try:
            self._sleep_box.current(0)
        except tk.TclError:  # pragma: no cover - window going away
            pass
        log.info("Sleep timer reached; stopping playback.")
        self.stop_playback()
        self.set_status(self.messages["discover_sleep_done"], "info")

    def _library_clicked(self) -> None:
        """Ask the application for the downloads that are already on disk."""
        if self._busy or self.on_library is None:
            return
        self.on_library()

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Enable or disable the refresh action while a search runs."""
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._refresh_btn.configure(state=state)
        self._library_btn.configure(state=state)
        self._sync_queue_visibility()
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

    def _notify_queue_changed(self) -> None:
        """Tell the application the playlist should be saved."""
        if self.on_queue_changed is not None:
            try:
                self.on_queue_changed()
            except Exception:
                log.debug("on_queue_changed failed", exc_info=True)

    def show_empty(self, message_key: str = "discover_empty") -> None:
        """Clear the list only when there is nothing worth keeping."""
        self.set_busy(False)
        self.set_loading(False)
        if self._tracks:
            # Find similar found nothing new — leave playback and the playlist alone.
            level = "warn" if message_key in ("discover_no_seeds", "discover_blocked") else "info"
            self.set_status(self.messages[message_key], level)
            self._sync_queue_visibility()
            return
        self._tracks = []
        self.player.set_playlist([])
        self._selected = -1
        self._render_rows()
        self._sync_queue_visibility()
        level = "warn" if message_key in ("discover_no_seeds", "discover_blocked") else "info"
        self.set_status(self.messages[message_key], level)
        self._now_title.configure(text=self.messages["discover_idle"])
        self._now_meta.configure(text="")
        self._up_next.configure(text="")
        self._show_stage_idle()
        self._set_play_icon(False)
        self.sync_vote_buttons()
        self._notify_queue_changed()

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
        was_empty = not self._tracks
        self._tracks = dedupe_tracks(tracks)
        self.player.set_playlist(self._tracks)
        self._selected = 0 if self._tracks else -1
        # A new queue means a new random round.
        self._reset_shuffle_bag()
        self._render_rows()
        self._sync_queue_visibility()
        if status:
            self.set_status(status, level)
        elif self._tracks:
            self.set_status(self.messages.format("discover_results", count=len(self._tracks)), "ok")
        else:
            self.set_status(self.messages["discover_empty"], "warn")
        if self._tracks:
            self._highlight(self._selected)
            if was_empty and not (self.player.playing or self.player.process_running):
                self.after(0, lambda: self.play_at(0))
        self._update_up_next()
        self.sync_vote_buttons()
        self._notify_queue_changed()

    def restore_tracks(
        self,
        tracks: List[DiscoverTrack],
        *,
        index: int = 0,
        status: str = "",
        level: str = "ok",
    ) -> None:
        """Load a previously saved playlist without auto-starting playback.

        :param tracks: Rows to show.
        :param index: Selected row.
        :param status: Status line text.
        :param level: Status colour level.
        """
        self.set_busy(False)
        self.set_loading(False)
        self._extend_requested = False
        self._resume_after_extend = False
        self._tracks = dedupe_tracks(tracks)
        self.player.set_playlist(self._tracks)
        if self._tracks:
            self._selected = max(0, min(int(index), len(self._tracks) - 1))
        else:
            self._selected = -1
        self._render_rows()
        self._sync_queue_visibility()
        if status:
            self.set_status(status, level)
        elif self._tracks:
            self.set_status(self.messages.format("discover_results", count=len(self._tracks)), "ok")
        else:
            self.set_status(self.messages["discover_empty"], "warn")
        if self._tracks:
            self._highlight(self._selected)
            track = self._tracks[self._selected]
            self._now_title.configure(text=_shorten(track.title, 120))
            self._now_meta.configure(text=track.uploader or "")
        else:
            self._now_title.configure(text=self.messages["discover_idle"])
            self._now_meta.configure(text="")
        self._update_up_next()
        self.sync_vote_buttons()
        self._notify_queue_changed()

    def begin_discover(self) -> None:
        """Mark a Find-Similar run as started without touching the playlist.

        Playback and existing rows stay put; new hits are appended by
        :meth:`append_tracks` as batches arrive.
        """
        self._extend_requested = False
        self._resume_after_extend = False
        self._sync_queue_visibility()

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
        self._sync_queue_visibility()
        # Auto-play when songs are ready and nothing is already playing.
        if self._tracks and not (self.player.playing or self.player.process_running):
            index = self._selected if self._selected >= 0 else 0
            self.after(0, lambda i=index: self.play_at(i))
        self._update_up_next()
        self.sync_vote_buttons()
        self._notify_queue_changed()

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
            self._sync_queue_visibility()
            if before == 0 and not (self.player.playing or self.player.process_running):
                self.after(0, lambda: self.play_at(0))
            elif self._resume_after_extend:
                self._resume_after_extend = False
                self.after(0, lambda index=before: self.play_at(index))
            elif self.player.playing or self.player.process_running:
                self._prefetch_upcoming()
            self._notify_queue_changed()
        if update_status:
            if status:
                self.set_status(status, level)
            else:
                self.set_status(self.messages.format("discover_results", count=len(self._tracks)), "ok")
        return len(fresh)

    def insert_tracks(self, position: int, tracks: List[DiscoverTrack]) -> int:
        """Put songs into the queue at ``position`` without stopping playback.

        Used when a track is picked from a search: it belongs right behind the
        one playing, not at the end of a long queue.

        :param position: Where to insert; clamped into the queue.
        :param tracks: The songs to insert.
        :return: How many were newly inserted.
        """
        fresh = dedupe_tracks(tracks, against=self._tracks)
        if not fresh:
            return 0
        where = max(0, min(int(position), len(self._tracks)))
        self._tracks[where:where] = fresh
        self.player.insert_tracks(where, fresh)
        if self._selected >= where:
            # The selection followed its track down the list.
            self._selected += len(fresh)
        self._render_rows()
        if 0 <= self._selected < len(self._tracks):
            self._highlight(self._selected)
        self._update_up_next()
        self._sync_queue_visibility()
        if self.player.playing or self.player.process_running:
            self._prefetch_upcoming()
        self._notify_queue_changed()
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
        frame.columnconfigure(
            2, minsize=_QUEUE_COL_CHANNEL if self._channel_visible else 0, weight=0
        )
        frame.columnconfigure(3, minsize=_QUEUE_COL_DURATION, weight=0)
        frame.columnconfigure(4, minsize=self._col_action, weight=0)
        frame.columnconfigure(5, minsize=self._col_action, weight=0)

    def _apply_queue_width(self, width: int) -> None:
        """Drop or restore the channel column for the available ``width``.

        The column minimum sizes add up to more than a narrow pane offers, and
        grid then pushes the row buttons past the right edge.  Giving up the
        channel keeps every control reachable.

        :param width: Currently available queue width in pixels.
        :return: None
        """
        wide = width <= 0 or width >= _QUEUE_NARROW
        if wide == self._channel_visible:
            return
        self._channel_visible = wide
        for frame in [self._queue_header] + list(self._row_frames):
            try:
                self._configure_queue_columns(frame)
            except tk.TclError:
                pass
        labels = list(self._channel_labels)
        if self._header_channel is not None:
            labels.append(self._header_channel)
        for label in labels:
            try:
                label.grid() if wide else label.grid_remove()
            except tk.TclError:
                pass

    def _on_queue_canvas_configure(self, event: tk.Event) -> None:
        """Keep the scroll body as wide as the canvas and refresh title ellipsis."""
        try:
            self._canvas.itemconfigure(self._window, width=event.width)
        except tk.TclError:
            pass
        self._apply_queue_width(int(event.width))
        self._refresh_title_ellipsis()

    def _fit_cell(self, label: ttk.Label, full: str) -> None:
        """Truncate ``full`` to what ``label`` is currently wide enough to show.

        :param label: A queue cell created by :meth:`_cell`.
        :param full: The untruncated text.
        :return: None
        """
        font = self.fonts.get("body")
        if font is None:
            return
        try:
            width = max(24, int(label.winfo_width()) - 4)
            label.configure(text=_fit_line(full, width, font))
        except (tk.TclError, TypeError, ValueError):
            pass

    def _cell(self, row: tk.Misc, full: str, style: str, anchor: str) -> ttk.Label:
        """Return a queue cell that never widens its grid column.

        A ``ttk.Label`` asks for as many pixels as its text needs, and ``grid``
        hands every column the widest request in it.  One long title therefore
        stretched the whole row past the canvas and pushed the later columns out
        of line with the rows above.  Pinning ``width`` to a single character
        makes the request constant, so the column geometry comes from
        :meth:`_configure_queue_columns` alone and every row lines up; the text
        is then shortened with an ellipsis to whatever space the cell got.

        :param row: The row frame the cell belongs to.
        :param full: The untruncated text.
        :param style: ttk style name.
        :param anchor: Text anchor inside the cell.
        :return: The new label.
        """
        label = ttk.Label(row, text=full, style=style, anchor=anchor, width=1)
        label.bind("<Configure>", lambda _e, lbl=label, txt=full: self._fit_cell(lbl, txt), add="+")
        return label

    def _refresh_title_ellipsis(self) -> None:
        """Truncate queue titles and channels to their current column width."""
        for label, full in zip(self._title_labels, self._title_full):
            self._fit_cell(label, full)
        for label, full in zip(self._channel_labels, self._channel_full):
            self._fit_cell(label, full)

    def _render_rows(self) -> None:
        """Rebuild the scrollable track rows under the fixed column header."""
        for child in self._body.winfo_children():
            child.destroy()
        self._row_frames = []
        self._title_labels = []
        self._title_full = []
        self._channel_labels = []
        self._channel_full = []
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
        # Single-click play on metadata cells only — not on Download (or other row actions).
        row.bind("<Button-1>", lambda _e, i=index: self.play_at(i))

        number = ttk.Label(
            row,
            text="{0}.".format(index + 1),
            style="Panel.Muted.TLabel",
            anchor="e",
            width=1,
        )
        number.grid(row=0, column=0, sticky="ew")
        number.bind("<Button-1>", lambda _e, i=index: self.play_at(i))

        full_title = " ".join((track.title or "").split())
        title = self._cell(row, full_title, "Panel.TLabel", "w")
        title.grid(row=0, column=1, sticky="ew", padx=(PAD_SMALL, 0))
        title.bind("<Button-1>", lambda _e, i=index: self.play_at(i))
        self._title_labels.append(title)
        self._title_full.append(full_title)

        channel = " ".join((track.uploader or track.seed_title or "").split())
        channel_label = self._cell(row, channel, "Panel.Muted.TLabel", "w")
        channel_label.grid(row=0, column=2, sticky="ew", padx=(PAD_SMALL, 0))
        if not self._channel_visible:
            # Rows arriving while the queue is narrow must not bring the column back.
            channel_label.grid_remove()
        channel_label.bind("<Button-1>", lambda _e, i=index: self.play_at(i))
        self._channel_labels.append(channel_label)
        self._channel_full.append(channel)

        duration_label = ttk.Label(
            row,
            text=format_duration(int(track.duration or 0)),
            style="Panel.Muted.TLabel",
            anchor="e",
            width=1,
        )
        duration_label.grid(row=0, column=3, sticky="ew", padx=(PAD_SMALL, 0))
        duration_label.bind("<Button-1>", lambda _e, i=index: self.play_at(i))

        hide_btn = ttk.Button(
            row,
            text=self.messages["discover_hide_icon"],
            style="Row.TButton",
            width=3,
            command=lambda t=track: self.hide_track(t),
        )
        hide_btn.grid(row=0, column=4, sticky="e", padx=(PAD_SMALL, 0))
        _attach_tooltip(
            hide_btn,
            self.messages["discover_hide"],
            background=self.palette.elevated,
            foreground=self.palette.text,
        )

        download_btn = ttk.Button(
            row,
            text=self.messages["discover_download_icon"],
            style="Row.TButton",
            width=3,
            command=lambda t=track: self._on_download(t),
        )
        download_btn.grid(row=0, column=5, sticky="e", padx=(PAD_SMALL, 0))
        _attach_tooltip(
            download_btn,
            watch_url(track),
            background=self.palette.elevated,
            foreground=self.palette.text,
        )

        self._row_frames.append(row)
        self._bind_wheel_tree(row)
        self._bind_share_tree(row, track)

    def _bind_share_tree(self, widget: tk.Misc, track: DiscoverTrack) -> None:
        """Offer the share code on right-click, anywhere in a queue row.

        The whole row reacts, including its labels: a right-click that lands
        between two words and does nothing would just look broken.

        :param widget: The row, whose descendants are bound as well.
        :param track: The song that row shows.
        :return: None
        """
        widget.bind("<Button-3>", lambda _event, t=track: self._share_track(t), add="+")
        for child in widget.winfo_children():
            self._bind_share_tree(child, track)

    def _share_track(self, track: DiscoverTrack) -> str:
        """Show the QR code for one queued song.

        :param track: The song to share.
        :return: ``break``, so no other handler also answers the click.
        """
        if self.on_share is not None and track.video_id:
            self.on_share(track)
        elif not track.video_id:
            # A file that was copied into the folder by hand has no YouTube id,
            # so there is nothing another Clipster could look up.
            self.set_status(self.messages["share_no_id"], "warn")
        return "break"

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
        self.sync_vote_buttons()
        self._notify_queue_changed()

    def _highlight(self, index: int) -> None:
        """Visually mark the active row title (number stays muted)."""
        for i, label in enumerate(self._title_labels):
            try:
                label.configure(style="Panel.Accent.TLabel" if i == index else "Panel.TLabel")
            except tk.TclError:
                pass

    def centre_on(self, index: int) -> None:
        """Scroll the queue so row ``index`` sits in the middle.

        Called when a new track starts, not on every refresh: in between the
        user has to be able to scroll around without the list snapping back.

        :param index: The row to centre.
        :return: None
        """
        if not (0 <= index < len(self._row_frames)):
            return
        row = self._row_frames[index]
        try:
            self._canvas.update_idletasks()
            area = self._canvas.bbox("all")
            if not area:
                return
            content = area[3] - area[1]
            visible = self._canvas.winfo_height()
            if content <= visible or content <= 0:
                return              # everything fits; there is nothing to scroll
            middle = row.winfo_y() + row.winfo_height() / 2.0
            top = middle - visible / 2.0
            self._canvas.yview_moveto(max(0.0, min(1.0, top / content)))
        except tk.TclError:  # pragma: no cover - window already gone
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

    def _may_play(self, track: DiscoverTrack) -> bool:
        """Return ``True`` when this track may be started right now.

        A file that is already on disk always may - the whole point of the
        mobile-data rule is that those keep playing.  Anything that would have
        to be fetched asks the application, which knows what the connection
        looks like; a refusal swaps the queue for the local library so the music
        does not simply stop.

        :param track: The track that is about to start.
        :return: Whether playback may go ahead.
        """
        if track.is_local or self.allow_stream is None or self.allow_stream():
            return True
        self.set_status(self.messages["playback_local_only_switch"], "warn")
        if self.on_library is not None:
            self.on_library()
        return False

    def play_at(self, index: int) -> None:
        """Start continuous playback of playlist index ``index``."""
        if index < 0 or index >= len(self._tracks):
            return
        if self.ensure_terms is not None and not self.ensure_terms():
            return
        if not self._may_play(self._tracks[index]):
            return
        self._cancel_playback_check()
        self._cancel_end_poll()
        self._play_token += 1
        token = self._play_token
        track = self._tracks[index]
        self._selected = index
        self._highlight(index)
        self.sync_vote_buttons()
        self._notify_queue_changed()
        # A new track: bring it back to the middle even if the user had scrolled
        # somewhere else while the previous one played.
        self.centre_on(index)
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
            if detail in ("no_player", "canceled", "invalid index", "empty playlist"):
                return
            failed = result.track
            if failed is None and 0 <= self._selected < len(self._tracks):
                failed = self._tracks[self._selected]
            if failed is not None and failed.video_id:
                self._unplayable_ids.add(failed.video_id)
                self._skip_to_next_playable()
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
        if 0 <= self._selected < len(self._tracks):
            failed = self._tracks[self._selected]
            if failed.video_id:
                self._unplayable_ids.add(failed.video_id)
                self._skip_to_next_playable()

    def _skip_to_next_playable(self) -> None:
        """Advance past tracks that already failed to start this session."""
        start = max(self._selected, -1) + 1
        for index in range(start, len(self._tracks)):
            track = self._tracks[index]
            if track.video_id and track.video_id in self._unplayable_ids:
                continue
            title = _shorten(track.title, 60) if track.title else "?"
            self.set_status(
                "{0} → {1}".format(self.messages["discover_playback_skip"], title),
                "warn",
            )
            self.after(200, lambda i=index: self.play_at(i))
            return

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
            nxt = self.next_index(automatic=True)
            if nxt is not None:
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
        """Skip to the next track, following shuffle and repeat."""
        nxt = self.next_index(automatic=False)
        if nxt is None:
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
        self.sync_vote_buttons()

    def dislike_current(self) -> None:
        """Thumbs-down the current track so similar songs are avoided."""
        track = self.current_track()
        if track is None:
            self.set_status(self.messages["discover_rate_need_track"], "warn")
            return
        self._on_dislike(track)
        self.sync_vote_buttons()

    def hide_track(self, track: DiscoverTrack) -> None:
        """Remove ``track`` from the queue and exclude it from Find similar."""
        if self._on_hide is not None:
            self._on_hide(track)
        else:
            self._on_dislike(track)
        self.sync_vote_buttons()

    def hide_at(self, index: int) -> None:
        """Hide the queue row at ``index``."""
        if index < 0 or index >= len(self._tracks):
            return
        self.hide_track(self._tracks[index])

    def hide_current(self) -> None:
        """Hide the track that is currently selected / playing."""
        track = self.current_track()
        if track is None:
            self.set_status(self.messages["discover_rate_need_track"], "warn")
            return
        self.hide_track(track)

    def download_at(self, index: int) -> None:
        """Download the track at ``index`` without changing playback."""
        if index < 0 or index >= len(self._tracks):
            return
        self._on_download(self._tracks[index])

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
        self._sync_queue_visibility()
        if self._tracks and 0 <= self._selected < len(self._tracks):
            self._highlight(self._selected)
        self._update_up_next()
        self.sync_vote_buttons()
        self._notify_queue_changed()
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
