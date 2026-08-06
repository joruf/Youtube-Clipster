"""The clipboard monitor and the download pipeline.

This is the Python version of the main event loop that used to live in
``linux/youtube-clipster.sh`` and in the ``:loop`` label of the Windows batch
file - now identical on every platform.

The program lives in the system tray.  Copying a YouTube link opens the small
:class:`clipster.navwindow.NavWindow`, which asks for format and audio track and
then shows the progress.  Every outcome is appended to the
:class:`clipster.history.History` that the large
:class:`clipster.viewwindow.ViewWindow` displays.
"""

from __future__ import annotations

import collections
import signal
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import APP_AUTHOR, APP_SHORT_NAME, APP_TITLE, APP_URL, APP_VERSION, APP_WEBSITE, paths, shortcuts
from .bridge import Prompt, TkBridge
from .clipboard import Clipboard
from .config import Config
from .downloader import (
    DownloadCanceled,
    Downloader,
    DownloadFailed,
    MetadataError,
    Progress,
    VideoInfo,
    cookies_are_configured,
    extract_youtube_url,
    user_facing_ytdlp_error,
)
from . import discover
from .discover import (
    DiscoverOutcome,
    DiscoverTrack,
    discover_tracks,
    resolve_discover_seeds,
    seed_from_track,
)
from .discover_taste import DiscoverTaste, VOTE_DOWN, VOTE_UP
from .discover_queue import DiscoverQueueStore
from .history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, History, HistoryEntry, format_size
from .i18n import Messages
from .logging_setup import get_logger
from .terms import (
    TERMS_APP_VERSION,
    TERMS_STREAMING_VERSION,
    accept_app_terms,
    accept_streaming_terms,
    app_terms_accepted,
    streaming_terms_accepted,
)
from .tray import TrayIcon
from . import updater

log = get_logger(__name__)

#: The download started right away.
SUBMIT_STARTED = "started"
#: Accepted, but waiting behind the running downloads.
SUBMIT_QUEUED = "queued"
#: This URL is downloading right now.
SUBMIT_RUNNING = "running"
#: This URL is already on the waiting list.
SUBMIT_WAITING = "waiting"
#: The waiting list is full, the link was dropped.
SUBMIT_FULL = "full"
#: The file exists already; nothing was started.
SUBMIT_EXISTS = "exists"
#: Not a YouTube link.
SUBMIT_INVALID = "invalid"
#: Not a format this program produces.
SUBMIT_FORMAT = "format"
#: The program is shutting down and takes nothing new.
SUBMIT_CLOSING = "closing"

#: The formats a submission may ask for.
MEDIA_FORMATS = ("mp3", "mp4")

#: How long a resolved audio URL is reused, in seconds. YouTube's links last
#: several hours; this stays well inside that.
REMOTE_AUDIO_TTL = 1800.0

#: Streaming commands a remote client may send.
DISCOVER_COMMANDS = (
    "refresh", "extend", "play", "toggle", "stop", "next", "previous",
    "like", "dislike", "hide", "download", "seek", "volume", "clear_vote",
)

#: Of those, the ones that reach YouTube and therefore need the Streaming terms.
#: Turning the volume down or stopping takes nothing new from anywhere.
DISCOVER_TERMS_COMMANDS = ("refresh", "extend", "play", "toggle", "next", "previous", "download")


@dataclass(frozen=True)
class SubmitResult:
    """What became of a download request that did not come from the clipboard.

    Returned to callers that owe somebody an answer - the remote interface has
    to turn this into an HTTP status, so "it was ignored" is not good enough.
    """

    #: One of the ``SUBMIT_*`` constants.
    state: str
    #: The canonical URL, empty when it could not be recognised.
    url: str = ""
    #: The matching history entry, set for :data:`SUBMIT_EXISTS`.
    entry_id: str = ""
    #: Number of links waiting, set for :data:`SUBMIT_QUEUED`.
    position: int = 0

    @property
    def accepted(self) -> bool:
        """Return ``True`` when a download was started or queued."""
        return self.state in (SUBMIT_STARTED, SUBMIT_QUEUED)


def internet_available(*, timeout: float = 1.0) -> bool:
    """Return whether a brief TCP probe to a public DNS host succeeds.

    Used to skip auto-Discover when the machine is clearly offline. Failures
    are treated as offline — callers must not raise.
    """
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((host, 53), timeout=timeout):
                return True
        except OSError:
            continue
    return False


class ClipsterApp:
    """Watches the clipboard and drives one download at a time.

    Links copied while a download runs are not dropped: they queue up and start
    one after another.
    """

    #: Upper bound for the waiting list, so a stuck download cannot pile up.
    MAX_QUEUE = 20

    def __init__(self, config: Config, messages: Messages, *, headless: bool = False,
                 accept_terms: bool = False) -> None:
        """
        :param config: The active user configuration.
        :param messages: The active translation table.
        :param headless: Run without windows and without a tray, driven by the
            remote interface. For a machine with no screen - a server, or Android
            through Termux.
        :param accept_terms: Confirm the terms of use without asking. Only ever
            passed from an explicit command line switch.
        """
        self.config = config
        self.messages = messages
        self.headless = bool(headless)
        if paths.ensure_android_download_dir(config):
            try:
                config.save()
            except Exception:
                log.debug("Could not save Android download_dir", exc_info=True)
        self.download_dir = config.resolved_download_dir()

        self.history = History(limit=config.history_limit).load()
        self.taste = DiscoverTaste().load()
        self.queue_store = DiscoverQueueStore()
        #: Tk ``after`` id for a debounced Streaming-queue save.
        self._queue_save_job: Optional[str] = None
        if self.headless:
            from .headless import HeadlessGui

            self.gui: Any = HeadlessGui(messages, config, self.download_dir,
                                        accept_terms=accept_terms)
        else:
            from .gui import Gui

            self.gui = Gui(messages, config, self.download_dir)
        self.bridge = TkBridge(self.gui.root)
        # No Tk fallback backend without Tk, and no clipboard watching either:
        # a headless machine has nobody copying links into it.
        self.clipboard = Clipboard(None if self.headless else self.gui.root)
        self.downloader = Downloader(config, messages)
        self.tray = TrayIcon(
            messages=messages,
            icon_path=paths.icon_file(),
            on_show=lambda: self.bridge.post(self._show_view),
            on_open_folder=lambda: self.bridge.post(self._open_download_folder),
            on_quit=lambda: self.bridge.post(self.request_quit),
        )

        self._last_seen = ""
        #: URLs whose worker thread is still running.
        self._active: Dict[str, threading.Thread] = {}
        #: Only one question may be on screen at a time - the window is single.
        self._asking = False
        self._quitting = False
        self._tray_active = False
        self._minimize_hint_shown = False
        self._cancel_events: Dict[str, threading.Event] = {}
        self._quit_event = threading.Event()
        self._forced_format = ""
        #: Set when the user asked for a download that already exists.
        self._force_redownload = False
        #: The run the navigation window currently belongs to.
        self._nav_owner = ""
        #: An update check or installation is in flight.
        self._checking_update = False
        #: Set once an update was installed, so the shutdown restarts us.
        self._restart_after_update = False
        #: Links copied while a download runs, processed one after another.
        self._queue: "collections.deque[tuple]" = collections.deque()
        #: Latest progress per running URL. Written by the download threads and
        #: read by the remote interface from its own thread, hence the lock.
        self._progress: Dict[str, Dict[str, Any]] = {}
        self._progress_lock = threading.Lock()
        #: The remote interface, created on demand in :meth:`run`.
        self._remote: Any = None
        #: Resolved audio URLs per video id, for playback on a remote device.
        self._audio_urls: Dict[str, Tuple[str, Dict[str, str], float]] = {}
        self._audio_lock = threading.Lock()
        #: Video ids a remote search has just offered. A device may play one of
        #: these before the queue has caught up - otherwise it would have to wait
        #: for a round trip, and the tap that started it would have expired.
        self._offered_ids: Dict[str, float] = {}

        self.gui.on_quit = self.request_quit
        self.gui.on_nav_closed = self._nav_closed
        self.gui.on_view_closed = self._view_closed
        self.gui.on_play_entry = self._play_entry
        self.gui.on_delete_entry = self._delete_entry
        self.gui.on_hide_entry = self._hide_entry
        self.gui.on_reveal_entry = self._reveal_entry
        self.gui.on_clear_history = self._clear_history
        self.gui.on_open_folder = self._open_download_folder
        self.gui.on_submit_url = self._submit_url
        self.gui.on_save_settings = self._save_settings
        self.gui.on_check_updates = self._check_updates
        self.gui.on_install_update = self._install_update
        self.gui.on_open_result = self._open_result
        self.gui.on_reveal_result = self._reveal_result
        self.gui.on_discover_refresh = self._discover_refresh
        self.gui.on_discover_download = self._discover_download
        self.gui.on_discover_extend = self._discover_extend
        self.gui.on_discover_like = self._discover_like
        self.gui.on_discover_dislike = self._discover_dislike
        self.gui.on_show_terms = self._show_terms
        self.gui.on_phone_apply = self.apply_remote_settings
        self.gui.on_phone_new_token = self.regenerate_remote_token
        self.gui.on_phone_state = self.remote_state
        self.gui.build_windows()
        if self.gui.view is not None and self.gui.view.discover is not None:
            self.gui.view.discover.player.set_options_provider(self.downloader._base_options)
            self.gui.view.discover.ensure_terms = self._ensure_streaming_terms
            self.gui.view.discover.vote_for = self.taste.vote_for
            self.gui.view.discover._on_hide = self._discover_hide
            self.gui.view.discover._on_clear_vote = self._discover_clear_vote
            self.gui.view.discover._on_play_vote = self._discover_play_vote
            self.gui.view.discover.on_queue_changed = self._schedule_queue_save
            self.gui.view.discover.set_votes(self.taste.entries)

        self._discover_cancel = threading.Event()
        self._discover_busy = False
        self._discover_extending = False
        #: Auto Find-Similar already ran (or was skipped) for this process.
        self._auto_discover_done = False
        #: Tk ``after`` id for the deferred auto Discover start, if any.
        self._auto_discover_job: Optional[str] = None
        #: Headless / Android Streaming backend (no Tk Discover page).
        self._headless_discover: Any = None
        if self.headless:
            from .discover_session import HeadlessDiscoverSession

            session = HeadlessDiscoverSession(config, messages)
            session.player.set_options_provider(self.downloader._base_options)
            session.ensure_terms = self._ensure_streaming_terms
            session._on_like = self._discover_like
            session._on_dislike = self._discover_dislike
            session._on_hide = self._discover_hide
            session._on_download = self._discover_download
            session._on_extend = self._discover_extend
            session.on_queue_changed = self._schedule_queue_save
            self._headless_discover = session
        #: Ignore tray "show" callbacks until startup visibility has been applied
        #: (some backends fire activate while the icon is created).
        self._tray_show_armed = False

    @property
    def _busy(self) -> bool:
        """Return ``True`` while the pipeline cannot take another link right now."""
        if self._asking:
            # The navigation window shows a question; a second one would replace it.
            return True
        return len(self._active) >= self._parallel_limit()

    def _parallel_limit(self) -> int:
        """Return how many downloads may run at the same time."""
        if not self.config.parallel_downloads:
            return 1
        return max(1, self.config.max_parallel_downloads)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def run(self) -> int:
        """Start the GUI event loop and monitor the clipboard until quit.

        :return: The process exit code.
        """
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._install_signal_handlers()
        self.bridge.start()
        self.gui.render_history(self.history.entries)

        if self.config.use_tray and not self.headless:
            self._tray_active = self.tray.start()
        if not self.headless:
            self._apply_initial_visibility()
            self._tray_show_armed = True
            # Ignore whatever is already in the clipboard at startup.
            self._last_seen = self.clipboard.read()

        log.info("%s", self.messages["separator"])
        log.info("%s", APP_TITLE)
        log.info("%s", self.messages["separator"])
        log.info("%s", self.messages["started"])
        log.info("Download folder: %s", self.download_dir)
        log.info("Download history: %s entries (%s)", len(self.history), self.history.path)
        if self.headless and not self.config.remote_enabled:
            # Without windows and without the remote interface there would be no
            # way to reach the program at all.
            log.warning("Headless without remote control - switch on \"remote_enabled\" "
                        "in %s, or run with --phone-setup once.", self.config.path)
        self.start_remote()

        self.gui.root.after(200, self._post_start)
        if not self.headless:
            self.gui.root.after(self.config.poll_interval_ms(), self._poll_clipboard)

        try:
            self.gui.root.mainloop()
        except KeyboardInterrupt:  # pragma: no cover - console interrupt
            log.info("Interrupted.")
        finally:
            self._shutdown()
        return 0

    # ------------------------------------------------------------------
    # The phone interface
    # ------------------------------------------------------------------
    def start_remote(self) -> bool:
        """Start the phone interface, if the user switched it on.

        Imported here and not at the top of the module: somebody who never turns
        this on should not pay for the import, and the program has to keep
        starting even if the interface cannot.

        :return: ``True`` when it is listening.
        """
        if not self.config.remote_enabled or self._remote is not None:
            return False
        from .webapi import RemoteApi
        from .webserver import LOOPBACK_ADDRESSES, RemoteServer, new_token

        if not self.config.remote_token:
            # Without a secret anybody on the network could start downloads.
            self.config.remote_token = new_token()
            self.config.save()
            log.info("A new token for remote control was generated.")
        server = RemoteServer(
            RemoteApi(self),
            token=self.config.remote_token,
            bind=self.config.remote_bind,
            port=self.config.remote_port,
        )
        if not server.start():
            return False
        self._remote = server
        # The complete address including the token: the token only lives in
        # config.json, so without this line nobody can work out what to type.
        log.info("Open this on your phone, tablet or other computer: %s", self.remote_url())
        if self.config.remote_bind in LOOPBACK_ADDRESSES:
            log.info('Only this machine can reach it - set "remote_bind" to '
                     '"0.0.0.0" in %s to let a phone in.', self.config.path)
        return True

    def stop_remote(self) -> None:
        """Stop the phone interface if it is running."""
        if self._remote is None:
            return
        self._remote.stop()
        self._remote = None
        log.info("Remote control was stopped.")

    # ------------------------------------------------------------------
    # Streaming, operated from the phone
    # ------------------------------------------------------------------
    def _discover_page(self) -> Any:
        """Return the GUI Discover page or the headless Streaming session."""
        if self.gui.view is not None and self.gui.view.discover is not None:
            return self.gui.view.discover
        return self._headless_discover

    def discover_remote_state(self) -> Dict[str, Any]:
        """Describe the Streaming page for a remote client.

        Read on the GUI thread because it touches the page and the player, but
        it changes nothing - the phone polls this while it is open.

        :return: Queue, playback position and status, ready to serialise.
        """
        if not self.bridge.on_gui_thread():
            return dict(self.bridge.call(self.discover_remote_state))
        page = self._discover_page()
        state: Dict[str, Any] = {
            "available": page is not None,
            "terms_accepted": streaming_terms_accepted(self.config),
            "tracks": [],
            "index": -1,
            "playing": False,
            "position": 0.0,
            "duration": 0.0,
            "can_seek": False,
            "busy": self._discover_busy,
            "extending": self._discover_extending,
            "mode": self.config.discover_mode,
            "level": 0.0,
            "volume": None,
            "volume_controllable": False,
            "search_delay_ms": max(200, int(self.config.remote_search_delay_ms)),
            "search_results": max(1, int(self.config.remote_search_results)),
        }
        if page is None:
            return state

        # Mind the shapes: tracks / index / playing / current are properties,
        # position / duration / can_seek / energy_level are methods.
        player = page.player
        state["tracks"] = [
            {
                "index": index,
                "video_id": track.video_id,
                "title": track.title,
                "uploader": track.uploader,
                "duration": track.duration,
                "seed_title": track.seed_title,
                "vote": self.taste.vote_for(track.video_id) or "",
            }
            for index, track in enumerate(player.tracks)
        ]
        state["index"] = player.index
        state["playing"] = player.playing
        try:
            state["position"] = float(player.position())
            state["duration"] = float(player.duration())
            state["can_seek"] = bool(player.can_seek())
            state["level"] = float(player.energy_level())
            state["volume"] = player.volume()
            state["volume_controllable"] = bool(player.volume_controllable())
        except Exception:  # pragma: no cover - a player without a live process
            log.debug("The player state could not be read", exc_info=True)
        current = player.current
        if current is None and 0 <= getattr(page, "_selected", -1) < len(player.tracks):
            current = player.tracks[page._selected]
        if current is not None:
            state["current"] = {
                "video_id": current.video_id,
                "title": current.title,
                "uploader": current.uploader,
                "duration": current.duration,
                "url": current.url,
                "vote": self.taste.vote_for(current.video_id) or "",
            }
            state["vote"] = state["current"]["vote"]
        else:
            state["vote"] = ""
        state["votes"] = [
            {
                "video_id": entry.video_id,
                "title": entry.title,
                "uploader": entry.uploader,
                "url": entry.url,
                "vote": entry.vote,
                "voted_at": entry.voted_at,
            }
            for entry in self.taste.entries
            if entry.video_id
        ]
        return state

    def discover_remote_audio(self, video_id: str) -> Tuple[str, Dict[str, str]]:
        """Resolve a browser-playable audio URL for one queued track.

        Runs on the caller's thread - the web server's - because resolving means
        asking YouTube, which must never happen on the GUI thread. Nothing here
        touches Tk: the queue is read through a marshalled snapshot.

        The result is cached, because these URLs stay valid for hours while
        resolving one costs a second or two that the phone would wait for on
        every single track.

        :param video_id: The video id from the queue.
        :return: ``(url, headers)``; the URL is empty when nothing was resolved.
            The headers have to be replayed by whoever fetches it - YouTube
            answers 403 to a request that arrives without them.
        """
        if not video_id or not streaming_terms_accepted(self.config):
            return "", {}
        now = time.monotonic()
        with self._audio_lock:
            cached = self._audio_urls.get(video_id)
            if cached is not None and now - cached[2] < REMOTE_AUDIO_TTL:
                return cached[0], dict(cached[1])

        with self._audio_lock:
            offered = video_id in self._offered_ids
        known = offered or any(item.get("video_id") == video_id
                               for item in self.discover_remote_state()["tracks"])
        if not known:
            # Only what is queued or what a search here just offered: otherwise
            # this would be an open resolver for any video id somebody sends.
            return "", {}
        watch = "https://www.youtube.com/watch?v={0}".format(video_id)
        try:
            from .player import BROWSER_AUDIO_FORMAT, resolve_stream

            url, headers = resolve_stream(watch, self.downloader._base_options(),
                                          format_selector=BROWSER_AUDIO_FORMAT)
        except Exception as exc:  # pragma: no cover - needs the network
            log.warning("No remote audio stream for %s: %s", video_id, exc)
            return "", {}
        with self._audio_lock:
            self._audio_urls[video_id] = (url, dict(headers), now)
        log.info("Resolved a remote audio stream for %s (%s headers)", video_id, len(headers))
        return url, dict(headers)

    def discover_remote_search(self, query: str) -> Dict[str, Any]:
        """Search YouTube for ``query`` on behalf of a remote device.

        Runs on the caller's thread - the web server's - because it reaches the
        network. Nothing is added to the queue yet: the device shows the results
        and the user picks one.

        :param query: What was typed on the device.
        :return: ``{"ok": bool, "error": str, "results": [...]}``.
        """
        text = " ".join(str(query or "").split())
        if not text:
            return {"ok": True, "error": "", "results": []}
        if not streaming_terms_accepted(self.config):
            return {"ok": False, "error": "terms_required", "results": []}
        try:
            found = discover.search_tracks(
                text,
                self.downloader._base_options(),
                limit=max(1, int(self.config.remote_search_results)),
            )
        except Exception as exc:  # pragma: no cover - needs the network
            log.warning("The remote search for %r failed: %s", text, exc)
            return {"ok": False, "error": str(exc), "results": []}
        log.info("Remote search for %r returned %s results.", text, len(found))
        now = time.monotonic()
        with self._audio_lock:
            for track in found:
                if track.video_id:
                    self._offered_ids[track.video_id] = now
            # Keep it from growing without bound over a long session.
            for stale in [key for key, when in self._offered_ids.items()
                          if now - when > REMOTE_AUDIO_TTL]:
                self._offered_ids.pop(stale, None)
        return {
            "ok": True,
            "error": "",
            "results": [
                {
                    "video_id": track.video_id,
                    "title": track.title,
                    "uploader": track.uploader,
                    "duration": track.duration,
                    "url": track.url,
                }
                for track in found
            ],
        }

    def discover_remote_enqueue(self, video_id: str, title: str = "", uploader: str = "",
                                duration: int = 0, play: bool = True) -> Dict[str, Any]:
        """Append a searched track to the queue and start it.

        Marshalled onto the GUI thread: it changes the playlist the Streaming
        page shows.

        :param video_id: The video id of the picked result.
        :param title: Its title, so the queue can show it without a second lookup.
        :param uploader: The channel name, when known.
        :param duration: Its length in seconds, when known.
        :param play: Start it right away instead of only queueing it.
        :return: ``{"ok": bool, "error": str, "state": {...}}``.
        """
        if not self.bridge.on_gui_thread():
            return dict(self.bridge.call(self.discover_remote_enqueue, video_id, title,
                                         uploader, duration, play))
        if not streaming_terms_accepted(self.config):
            return {"ok": False, "error": "terms_required", "state": self.discover_remote_state()}
        page = self._discover_page()
        if page is None:
            return {"ok": False, "error": "unavailable", "state": self.discover_remote_state()}
        if not video_id or len(str(video_id)) != 11:
            return {"ok": False, "error": "unknown_track", "state": self.discover_remote_state()}

        track = DiscoverTrack(
            url="https://www.youtube.com/watch?v={0}".format(video_id),
            video_id=str(video_id),
            title=str(title or video_id),
            uploader=str(uploader or ""),
            duration=max(0, int(duration or 0)),
        )
        existing = [item.video_id for item in page.player.tracks]
        if track.video_id in existing:
            # Already queued: play it where it is instead of adding it twice.
            position = existing.index(track.video_id)
        else:
            # Right behind what is playing, so a pick is heard next instead of
            # after everything else. Nothing played yet - the player's index is
            # still -1 - means it goes first.
            current = int(page.player.index)
            position = 0 if current < 0 else current + 1
            page.insert_tracks(position, [track])
        log.info("A remote device queued '%s' at position %s.", track.title, position + 1)
        if play:
            page.play_at(position)
        elif hasattr(page, "select_at"):
            # Guest devices play locally; still mark the row so like/dislike
            # and download know which track the phone is on.
            page.select_at(position)
        return {"ok": True, "error": "", "state": self.discover_remote_state()}

    def discover_remote_command(self, command: str, index: int = -1,
                               seconds: float = 0.0, video_id: str = "") -> Dict[str, Any]:
        """Run one Streaming command asked for by a remote client.

        Marshals itself onto the GUI thread like :meth:`submit_remote`.

        The Streaming terms are only *checked*, never asked for: the question is
        a modal dialog on the PC, so asking would block the phone's request until
        somebody walks over - and terms are not something to accept by remote
        control anyway.

        :param command: One of the commands in :data:`DISCOVER_COMMANDS`.
        :param index: Queue position, for ``play``.
        :param seconds: Target position, for ``seek``.
        :param video_id: Optional YouTube id (``clear_vote``, play helpers).
        :return: ``{"ok": bool, "error": str, "state": {...}}``.
        :raises RuntimeError: When the GUI bridge is no longer running.
        """
        if not self.bridge.on_gui_thread():
            return dict(self.bridge.call(
                self.discover_remote_command, command, index, seconds, video_id,
            ))
        if command not in DISCOVER_COMMANDS:
            return {"ok": False, "error": "unknown_command", "state": self.discover_remote_state()}
        page = self._discover_page()
        if page is None:
            return {"ok": False, "error": "unavailable", "state": self.discover_remote_state()}
        if command in DISCOVER_TERMS_COMMANDS and not streaming_terms_accepted(self.config):
            return {"ok": False, "error": "terms_required", "state": self.discover_remote_state()}

        log.info("Streaming command from a remote client: %s", command)
        try:
            self._run_discover_command(page, command, index, seconds, video_id=video_id)
        except Exception as exc:  # pragma: no cover - a page error must not kill the request
            log.exception("Streaming command %s failed", command)
            return {"ok": False, "error": str(exc), "state": self.discover_remote_state()}
        return {"ok": True, "error": "", "state": self.discover_remote_state()}

    def _run_discover_command(
        self,
        page: Any,
        command: str,
        index: int,
        seconds: float,
        *,
        video_id: str = "",
    ) -> None:
        """Carry out one Streaming command on the page.

        :param page: The Streaming page.
        :param command: The validated command name.
        :param index: Queue position, for ``play``.
        :param seconds: Target position, for ``seek``.
        :param video_id: Optional YouTube id for vote actions.
        :return: None
        """
        if command == "refresh":
            self._discover_refresh(require_terms=False)
        elif command == "extend":
            page.maybe_extend(reason="remote")
        elif command == "play":
            if 0 <= index < len(page.player.tracks):
                page.play_at(index)
            else:
                page.toggle_play()
        elif command == "toggle":
            page.toggle_play()
        elif command == "stop":
            page.stop_playback()
        elif command == "next":
            page.play_next()
        elif command == "previous":
            page.play_previous()
        elif command == "like":
            if 0 <= index < len(getattr(page, "_tracks", ())):
                if hasattr(page, "select_at"):
                    page.select_at(index)
            page.like_current()
            if hasattr(page, "sync_vote_buttons"):
                page.sync_vote_buttons()
            self._sync_votes_ui(page)
        elif command == "dislike":
            if 0 <= index < len(getattr(page, "_tracks", ())):
                if hasattr(page, "select_at"):
                    page.select_at(index)
            page.dislike_current()
            if hasattr(page, "sync_vote_buttons"):
                page.sync_vote_buttons()
            self._sync_votes_ui(page)
        elif command == "clear_vote":
            target = str(video_id or "").strip()
            if not target and 0 <= index < len(getattr(page, "_tracks", ())):
                target = page._tracks[index].video_id
            self._discover_clear_vote(target)
            if hasattr(page, "sync_vote_buttons"):
                page.sync_vote_buttons()
            self._sync_votes_ui(page)
        elif command == "hide":
            if 0 <= index < len(getattr(page, "_tracks", ())) and hasattr(page, "hide_at"):
                page.hide_at(index)
            elif hasattr(page, "hide_current"):
                page.hide_current()
            else:
                page.dislike_current()
            self._sync_votes_ui(page)
        elif command == "download":
            if 0 <= index < len(getattr(page, "_tracks", ())) and hasattr(page, "download_at"):
                page.download_at(index)
            else:
                page.download_current()
        elif command == "seek":
            page.player.seek(max(0.0, float(seconds)))
        elif command == "volume":
            # The same field carries the value: one number, one command.
            page.player.set_volume(int(seconds))

    def remote_state(self) -> Dict[str, Any]:
        """Describe the phone interface for the Phone page.

        Everything the page needs in one call, so it can be polled cheaply
        without reaching into the server.

        :return: Settings, whether it listens, the address and the last contact.
        """
        state: Dict[str, Any] = {
            "enabled": self.config.remote_enabled,
            "bind": self.config.remote_bind,
            "port": self.config.remote_port,
            "token": self.config.remote_token,
            "running": self._remote is not None and self._remote.running,
            "url": self.remote_url(),
            "contacts": 0,
            "last_contact": "",
        }
        if self._remote is not None:
            state["port"] = self._remote.port
            api = getattr(self._remote, "_api", None)
            if api is not None and hasattr(api, "contact_info"):
                state.update(api.contact_info())
        return state

    def apply_remote_settings(self, enabled: bool, bind: str, port: int) -> Dict[str, Any]:
        """Save the phone interface settings and restart it accordingly.

        :param enabled: Whether the interface should serve.
        :param bind: The interface to listen on.
        :param port: The TCP port to listen on.
        :return: The new :meth:`remote_state`.
        """
        from .webserver import new_token

        self.config.remote_enabled = bool(enabled)
        self.config.remote_bind = bind
        self.config.remote_port = int(port)
        if enabled and not self.config.remote_token:
            self.config.remote_token = new_token()
        self.config.save()

        # Always stop first: bind address and port may both have changed, and a
        # running server cannot be reconfigured in place.
        self.stop_remote()
        if self.config.remote_enabled:
            self.start_remote()
        return self.remote_state()

    def regenerate_remote_token(self) -> Dict[str, Any]:
        """Replace the token, which locks out every phone paired so far.

        :return: The new :meth:`remote_state`.
        """
        from .webserver import new_token

        self.config.remote_token = new_token()
        self.config.save()
        log.info("A new token for remote control was generated.")
        was_running = self._remote is not None
        self.stop_remote()
        if was_running or self.config.remote_enabled:
            self.start_remote()
        return self.remote_state()

    def remote_url(self) -> str:
        """Return the address a phone can open, token included.

        Meant for display on the PC - the log prints it, and the view window
        will show it as a QR code.  While the server is bound to loopback the
        network address is *not* returned: it would look inviting and then
        refuse every connection.

        :return: The URL, or an empty string while the interface is off.
        """
        if self._remote is None:
            return ""
        from .webserver import phone_url

        return phone_url(self.config.remote_bind, self._remote.port, self.config.remote_token)

    def _install_signal_handlers(self) -> None:
        """Quit cleanly on Ctrl+C and on SIGTERM."""
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            handler = getattr(signal, name, None)
            if handler is None:
                continue
            try:
                signal.signal(handler, self._on_signal)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass

    def _on_signal(self, signum: int, _frame: object) -> None:
        """Translate a POSIX signal into a normal quit request."""
        log.info("Signal %s received - shutting down.", signum)
        self.request_quit()

    def _apply_initial_visibility(self) -> None:
        """Decide whether the view window is shown at startup.

        Without a working tray icon the window is the only way to quit the
        program, so it is forced visible in that case.  Tray + start_minimized
        must not deiconify the Streaming/view window — only the tray icon
        (and an optional toast) belong on screen.
        """
        if self._tray_active:
            if self.config.start_minimized:
                self.gui.hide_view()
                log.info("Started in the system tray.")
                return
            self.gui.show_view()
            return

        if self.config.use_tray:
            log.warning("No system tray available - showing the view window instead.")
        elif self.config.start_minimized:
            log.warning("Without a tray icon the window is the only way to quit - showing it.")
        self.gui.show_view()

    def _keep_tray_start_hidden(self) -> None:
        """Re-hide the view after modal startup dialogs when tray-start is on."""
        if self._tray_active and self.config.start_minimized:
            self.gui.hide_view()

    def _post_start(self) -> None:
        """Run the one-off tasks that need a live event loop."""
        if not self._ensure_app_terms():
            return
        # Terms (or other modals) must not leave Streaming open behind them.
        self._keep_tray_start_hidden()
        # No OS balloon / toast for "started" — especially noisy when tray-minimized.
        self._sync_autostart()
        self._maybe_offer_desktop_shortcut()
        self._keep_tray_start_hidden()
        if self.config.check_updates and updater.due(self.config.update_check_hours):
            self._check_updates(announce=False)
        # Auto Find Similar only when Streaming terms were already accepted —
        # never force the Streaming terms modal at tray boot. Prefer the last
        # saved queue over starting a fresh search.
        if self._restore_discover_queue():
            self._auto_discover_done = True
        else:
            self._maybe_schedule_auto_discover()

    def _restore_discover_queue(self) -> bool:
        """Load the last Streaming playlist into the page when one was saved.

        :return: ``True`` when a non-empty queue was restored.
        """
        if not streaming_terms_accepted(self.config):
            return False
        page = self._discover_page()
        if page is None:
            return False
        tracks, index = self.queue_store.load()
        if not tracks:
            return False
        tracks = self.taste.filter_tracks(tracks)
        if not tracks:
            return False
        if index < 0 or index >= len(tracks):
            index = 0
        if hasattr(page, "restore_tracks"):
            page.restore_tracks(
                tracks,
                index=index,
                status=self.messages.format("discover_queue_restored", count=len(tracks)),
            )
        else:
            page.set_tracks(
                tracks,
                status=self.messages.format("discover_queue_restored", count=len(tracks)),
            )
            if hasattr(page, "select_at"):
                page.select_at(index)
        log.info("Restored Streaming queue with %s tracks (index %s).", len(tracks), index)
        return True

    def _schedule_queue_save(self) -> None:
        """Debounce writing the Streaming queue so batch updates do not thrash disk."""
        if self._quitting:
            self._save_discover_queue()
            return
        root = getattr(self.gui, "root", None)
        if root is None:
            self._save_discover_queue()
            return
        job = self._queue_save_job
        if job:
            try:
                root.after_cancel(job)
            except Exception:
                pass
        try:
            self._queue_save_job = root.after(400, self._save_discover_queue)
        except Exception:
            self._queue_save_job = None
            self._save_discover_queue()

    def _save_discover_queue(self) -> None:
        """Persist the current Streaming playlist."""
        self._queue_save_job = None
        page = self._discover_page()
        if page is None:
            return
        tracks = list(getattr(page, "_tracks", []) or [])
        index = int(getattr(page, "_selected", -1))
        self.queue_store.save(tracks, index)

    def _ensure_app_terms(self) -> bool:
        """Require acceptance of the general terms before normal startup continues."""
        if app_terms_accepted(self.config):
            return True
        accepted = self.gui.ask_terms_acceptance(
            title_key="terms_app_title",
            body_key="terms_app_body",
        )
        if not accepted:
            log.info("User declined the general terms of use.")
            self.request_quit()
            return False
        accept_app_terms(self.config)
        log.info("User accepted the general terms of use (v%s).", self.config.terms_app_version)
        return True

    def _ensure_streaming_terms(self) -> bool:
        """Require Streaming-specific terms before Discover actions run."""
        if streaming_terms_accepted(self.config):
            return True
        accepted = self.gui.ask_terms_acceptance(
            title_key="terms_streaming_title",
            body_key="terms_streaming_body",
        )
        if not accepted:
            log.info("User declined the Streaming terms of use.")
            page = self._discover_page()
            if page is not None:
                page.set_status(self.messages["terms_streaming_declined"], "warn")
            else:
                self.gui.toast(self.messages["terms_streaming_declined"])
            return False
        accept_streaming_terms(self.config)
        log.info(
            "User accepted the Streaming terms of use (v%s).",
            self.config.terms_streaming_version,
        )
        # First acceptance outside an in-flight refresh: queue auto Discover once.
        # Defer so a concurrent Find Similar can claim the slot first.
        try:
            self.gui.root.after(0, self._maybe_schedule_auto_discover)
        except Exception:
            pass
        return True

    def _maybe_schedule_auto_discover(self) -> None:
        """Schedule a one-shot auto Find Similar when Streaming terms are ready."""
        if self._auto_discover_done or self._discover_busy or self._quitting:
            return
        if not streaming_terms_accepted(self.config):
            return
        self._auto_discover_done = True
        try:
            self._auto_discover_job = self.gui.root.after(400, self._run_auto_discover)
        except Exception:
            self._auto_discover_done = False
            self._auto_discover_job = None

    def _cancel_auto_discover_job(self) -> None:
        """Drop a pending auto-Discover timer so teardown cannot fire it."""
        job = self._auto_discover_job
        self._auto_discover_job = None
        if not job:
            return
        try:
            self.gui.root.after_cancel(job)
        except Exception:
            pass

    def _run_auto_discover(self) -> None:
        """Start Discover in the background without forcing the view open."""
        self._auto_discover_job = None
        if self._quitting or self._discover_busy:
            return
        if not streaming_terms_accepted(self.config):
            return
        if not internet_available():
            log.info("Skipping auto Discover — no network.")
            return
        # Do not show the view when tray-minimized; refresh updates the page quietly.
        self._discover_refresh(require_terms=False)

    def _show_terms(self) -> None:
        """Open the combined terms documents from the About page."""
        self.gui.show_terms_document(
            title_key="terms_app_title",
            body_keys=("terms_app_body", "terms_streaming_body"),
        )

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def _check_updates(self, announce: bool = True) -> None:
        """Ask GitHub for the newest commit, without blocking the interface.

        :param announce: Show the result even when nothing is new; the startup
            check stays quiet unless there is something to report.
        :return: None
        """
        if self._checking_update:
            return
        self._checking_update = True
        self.gui.show_update_state(self.messages["update_checking"], False, busy=True)

        def work() -> None:
            """Talk to GitHub off the interface thread."""
            info = updater.check()
            self.bridge.post(self._update_checked, info, announce)

        threading.Thread(target=work, name="clipster-update-check", daemon=True).start()

    def _update_checked(self, info: "updater.UpdateInfo", announce: bool) -> None:
        """Show what the check found.

        :param info: The result of :func:`clipster.updater.check`.
        :param announce: Report "nothing new" and failures as well.
        :return: None
        """
        self._checking_update = False
        if not info.known:
            text = self.messages.format("update_failed", details=info.error or "?")
            self.gui.show_update_state(text, False)
            if announce:
                log.warning("%s", text)
            return
        if info.available:
            text = self.messages.format("update_available", summary=info.summary or info.remote)
            self.gui.show_update_state(text, True)
            log.info("%s", text)
            if not self.tray.notify(text):
                self.gui.toast(text)
            return
        text = self.messages.format("update_current", commit=info.remote)
        self.gui.show_update_state(text, False)
        if announce:
            log.info("%s", text)

    def _install_update(self) -> None:
        """Fetch the new version and restart, once the user confirmed."""
        if self._checking_update:
            return
        self._checking_update = True
        self.gui.show_update_state(self.messages["update_installing"], False, busy=True)

        def work() -> None:
            """Do the fetching off the interface thread."""
            ok, message = updater.apply()
            self.bridge.post(self._update_applied, ok, message)

        threading.Thread(target=work, name="clipster-update", daemon=True).start()

    def _update_applied(self, ok: bool, message: str) -> None:
        """Restart when the update worked, report the reason when it did not.

        :param ok: Whether the new version was fetched.
        :param message: Output of the update, for the log and the user.
        :return: None
        """
        self._checking_update = False
        if not ok:
            text = self.messages.format("update_error", details=message)
            log.error("%s", text)
            self.gui.show_update_state(text, True)
            self.gui.show_error(self.messages["error_title"], text)
            return
        log.info("Update installed: %s", message)
        self._restart_after_update = True
        self.request_quit()

    def _sync_autostart(self) -> None:
        """Make the autostart entry match the configuration."""
        try:
            if self.config.autostart != shortcuts.autostart_enabled():
                shortcuts.set_autostart(self.config.autostart)
        except Exception as exc:  # pragma: no cover - desktop specific
            log.debug("Autostart could not be synchronised: %s", exc)

    def _maybe_offer_desktop_shortcut(self) -> None:
        """Ask once whether a desktop launcher should be created."""
        if not self.config.ask_desktop_shortcut or shortcuts.desktop_shortcut_exists():
            return
        accepted = self.gui.ask_yes_no(self.messages["shortcut_title"], self.messages["shortcut_question"])
        self.config.ask_desktop_shortcut = False
        self.config.save()
        if not accepted:
            log.info("The user declined the desktop shortcut.")
            return
        try:
            created = shortcuts.create_desktop_shortcut()
        except OSError as exc:
            log.error("Desktop shortcut could not be created: %s", exc)
            self.gui.show_error(
                self.messages["shortcut_title"],
                self.messages.format("shortcut_failed", details=exc),
            )
            return
        self.gui.toast(self.messages.format("shortcut_created", path=created))

    def request_quit(self) -> None:
        """Ask the application to shut down (safe from any thread)."""
        if self._quitting:
            return
        self._quitting = True
        self._quit_event.set()
        self._discover_cancel.set()
        self._cancel_auto_discover_job()
        self._queue.clear()
        self._cancel_all()
        try:
            self.gui.root.quit()
        except Exception:  # pragma: no cover - interpreter already gone
            pass

    def _shutdown(self) -> None:
        """Release every resource after the event loop has ended."""
        self._quitting = True
        self._quit_event.set()
        self._discover_cancel.set()
        self._cancel_auto_discover_job()
        self._cancel_all()
        # Before the bridge stops: a request still in flight has to be able to
        # get its answer, rather than blocking on a bridge that is already gone.
        self.stop_remote()
        self._save_discover_queue()
        page = self._discover_page()
        if page is not None:
            page.destroy_player()
        for worker in list(self._active.values()):
            if worker.is_alive():
                worker.join(timeout=5.0)
        self.tray.stop()
        self.bridge.stop()
        self.gui.destroy()
        log.info("%s", self.messages["stopped"])
        if self._restart_after_update:
            # Last thing before the process ends: the windows and the lock are
            # gone, so the new instance can take over cleanly.
            updater.restart()

    # ------------------------------------------------------------------
    # Window callbacks
    # ------------------------------------------------------------------
    def _show_view(self) -> None:
        """Bring the view window up from the tray."""
        if not self._tray_show_armed:
            log.debug("Ignoring tray show before startup visibility is ready.")
            return
        self.gui.show_view()

    def _nav_closed(self) -> None:
        """The navigation window was closed or dismissed."""
        nav = self.gui.nav
        if nav is not None:
            nav.hide()
        if not self._tray_active and not self.gui.view_visible():
            # Nothing else is on screen and there is no tray icon to come back
            # to, so closing the last window has to end the program.
            self.request_quit()

    def _view_closed(self) -> None:
        """The view window was closed: hide it, or quit without a tray."""
        if not self._tray_active:
            self.request_quit()
            return
        self.gui.hide_view()
        log.debug("View window hidden - the program keeps running in the tray.")
        if not self._minimize_hint_shown:
            self._minimize_hint_shown = True
            # Point at whatever this backend actually supports: a click on the
            # icon when it reacts to one, the menu otherwise.
            key = "tray_minimized_click" if self.tray.has_default_action else "tray_minimized"
            if not self.tray.notify(self.messages[key]):
                self.gui.toast(self.messages[key])

    def _save_settings(self) -> None:
        """Persist the configuration edited in the settings page."""
        self.config.save()
        self.history.limit = max(1, self.config.history_limit)
        self.download_dir = self.config.resolved_download_dir()
        self.gui.download_dir = self.download_dir
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Download folder %s could not be created: %s", self.download_dir, exc)
        self._sync_autostart()
        self.gui.render_history(self.history.entries)
        log.info("Settings saved to %s", self.config.path)

    def _submit_url(self, url: str, media_format: str) -> None:
        """Start a download for a URL typed into the toolbar.

        :param url: The pasted URL.
        :param media_format: ``mp3`` or ``mp4`` from the toolbar selector.
        :return: None
        """
        target = extract_youtube_url(url)
        if not target:
            self.gui.show_error(self.messages["error_title"], self.messages["error_not_a_link"])
            return
        log.info("Download requested from the view window.")
        self._enqueue(target, media_format)

    def submit_remote(self, url: str, media_format: str, force: bool = False) -> SubmitResult:
        """Take a download request from outside the program.

        Everything below touches the navigation window and the shared
        ``_forced_format`` state, which only the GUI thread may do - so the call
        marshals itself instead of trusting every future caller to remember
        that.  Two requests arriving at once therefore cannot race.

        A remote caller is never asked anything, so the format has to come with
        the request; the audio track is resolved by :meth:`_auto_language`, the
        same way the view window toolbar does it.

        :param url: The URL as it arrived, not necessarily canonical.
        :param media_format: ``mp3`` or ``mp4``.
        :param force: Download again even when the file is already there.
        :return: What happened, ready to be turned into an answer.
        :raises RuntimeError: When the GUI bridge is no longer running.
        """
        if not self.bridge.on_gui_thread():
            return self.bridge.call(self.submit_remote, url, media_format, force)
        if self._quitting:
            return SubmitResult(SUBMIT_CLOSING)
        if media_format not in MEDIA_FORMATS:
            return SubmitResult(SUBMIT_FORMAT)
        target = extract_youtube_url(url)
        if not target:
            return SubmitResult(SUBMIT_INVALID)
        if not force:
            existing = self.history.find_download(target, media_format)
            if existing is not None:
                # Answered right away rather than starting a run that would end
                # in the "already downloaded" dialog on the desktop, which
                # nobody sitting at the phone can see.
                return SubmitResult(SUBMIT_EXISTS, url=target, entry_id=existing.identifier())
        log.info("Download requested remotely: %s as %s", target, media_format)
        state = self._enqueue(target, media_format, force=force)
        return SubmitResult(state, url=target, position=len(self._queue))

    def _discover_refresh(self, *, require_terms: bool = True) -> None:
        """Search for related songs on a worker thread.

        Seeds are resolved on the worker (history → likes → download folder →
        bounded disk scan) so a disk walk never freezes the UI.
        """
        if require_terms and not self._ensure_streaming_terms():
            return
        if self._discover_busy:
            return
        page = self._discover_page()
        if page is None:
            return
        self._auto_discover_done = True
        mode = page.selected_mode()
        self.config.discover_mode = mode
        self.config.save()
        self._discover_cancel.clear()
        self._discover_busy = True
        page.set_busy(True, self.messages["discover_loading"])
        page.begin_discover()
        exclude = set(self.taste.excluded_ids()) | page.video_ids()
        history_snapshot = list(self.history.entries)
        liked_snapshot = self.taste.liked_seeds()
        download_dir = self.download_dir
        min_seeds = self.config.discover_min_folder_seeds
        disk_scan = bool(self.config.discover_disk_scan_enabled)

        def on_progress(current: int, total: int, title: str) -> None:
            self.bridge.post(page.show_progress, current, total, title)

        def on_batch(tracks: List[DiscoverTrack]) -> None:
            self.bridge.post(self._discover_batch, list(tracks))

        def worker() -> None:
            try:
                seeds, _source = resolve_discover_seeds(
                    history_snapshot,
                    download_dir,
                    liked_entries=liked_snapshot,
                    min_folder_seeds=min_seeds,
                    disk_scan_enabled=disk_scan,
                )
                seeds = self.taste.merge_seeds(seeds, prefer_liked=False)
                if not seeds:
                    self.bridge.post(self._discover_no_seeds)
                    return
                outcome = discover_tracks(
                    seeds,
                    self.config,
                    mode=mode,
                    base_options=self.downloader._base_options(),
                    cancel_check=self._discover_cancel.is_set,
                    progress=on_progress,
                    on_batch=on_batch,
                    exclude_ids=exclude,
                )
            except Exception as exc:
                log.exception("Discover search failed")
                self.bridge.post(self._discover_failed, str(exc))
                return
            self.bridge.post(self._discover_ready, outcome)

        threading.Thread(target=worker, name="clipster-discover", daemon=True).start()

    def _discover_no_seeds(self) -> None:
        """Show the empty-seeds state after a background seed resolve."""
        self._discover_busy = False
        page = self._discover_page()
        if page is None:
            return
        # Never wipe a playlist that is already playing / queued.
        if getattr(page, "_tracks", None):
            if hasattr(page, "finish_discover"):
                page.finish_discover(status=self.messages["discover_no_seeds"], level="warn")
            else:
                page.set_busy(False)
                page.set_status(self.messages["discover_no_seeds"], "warn")
            return
        page.show_empty("discover_no_seeds")

    def _discover_batch(self, tracks: List[DiscoverTrack]) -> None:
        """Append tracks as soon as a seed batch arrives during Find Similar."""
        if not self._discover_busy:
            return
        page = self._discover_page()
        if page is None or not tracks:
            return
        filtered = self.taste.filter_tracks(tracks)
        if not filtered:
            return
        page.append_tracks(filtered, update_status=False)

    def _discover_ready(self, outcome: DiscoverOutcome) -> None:
        """Show Discover results and status on the UI thread."""
        self._discover_busy = False
        page = self._discover_page()
        if page is None:
            return
        outcome.tracks = self.taste.filter_tracks(outcome.tracks)
        # Keep rows already streamed in; only append anything still missing
        # (e.g. suffix-relaxed fallback that arrives at the end).
        known = page.video_ids()
        missing = [track for track in outcome.tracks if track.video_id not in known]
        if missing:
            page.append_tracks(missing, update_status=False)
        queue_count = len(page._tracks) if page._tracks else len(outcome.tracks)
        status, level = self._discover_status_text(outcome, queue_count=queue_count)
        page.finish_discover(status=status, level=level)

    def _discover_like(self, track: DiscoverTrack) -> None:
        """Toggle thumbs-up: second click clears; first click likes and extends."""
        page = self._discover_page()
        if self.taste.vote_for(track.video_id) == VOTE_UP:
            self.taste.clear_vote(track.video_id)
            if page is not None:
                page.set_status(self.messages["discover_vote_cleared"], "info")
                if hasattr(page, "sync_vote_buttons"):
                    page.sync_vote_buttons()
            self._sync_votes_ui(page)
            return
        self.taste.like(track)
        if page is not None:
            page.set_status(self.messages["discover_liked"], "ok")
            if hasattr(page, "sync_vote_buttons"):
                page.sync_vote_buttons()
        self._sync_votes_ui(page)
        if self._discover_busy or self._discover_extending:
            return
        self._discover_extend(track)

    def _discover_dislike(self, track: DiscoverTrack) -> None:
        """Toggle thumbs-down: second click clears; first click dislikes and drops."""
        if self.taste.vote_for(track.video_id) == VOTE_DOWN:
            self.taste.clear_vote(track.video_id)
            page = self._discover_page()
            if page is not None:
                page.set_status(self.messages["discover_vote_cleared"], "info")
                if hasattr(page, "sync_vote_buttons"):
                    page.sync_vote_buttons()
            self._sync_votes_ui(page)
            return
        self._exclude_and_drop(track, status_key="discover_disliked", play_if_current=True)
        self._sync_votes_ui(self._discover_page())

    def _discover_clear_vote(self, video_id: str) -> None:
        """Remove a stored like/dislike without changing the queue."""
        if not video_id:
            return
        if self.taste.clear_vote(video_id):
            page = self._discover_page()
            if page is not None:
                page.set_status(self.messages["discover_vote_cleared"], "info")
                if hasattr(page, "sync_vote_buttons"):
                    page.sync_vote_buttons()
            self._sync_votes_ui(page)

    def _sync_votes_ui(self, page: Any) -> None:
        """Refresh the desktop votes list when the page exposes one."""
        if page is not None and hasattr(page, "set_votes"):
            try:
                page.set_votes(self.taste.entries)
            except Exception:
                log.debug("Votes UI refresh failed", exc_info=True)

    def _discover_hide(self, track: DiscoverTrack) -> None:
        """Remove a queue row the user does not want — also blocked for Find similar."""
        self._exclude_and_drop(track, status_key="discover_hidden", play_if_current=True)
        self._sync_votes_ui(self._discover_page())

    def _discover_play_vote(self, video_id: str, title: str = "", uploader: str = "") -> None:
        """Play a voted track: reuse the queue row or enqueue it."""
        if not video_id:
            return
        page = self._discover_page()
        if page is None:
            return
        ids = [track.video_id for track in getattr(page, "_tracks", [])]
        if video_id in ids:
            page.play_at(ids.index(video_id))
            return
        entry = self.taste.entry_for(video_id)
        self.discover_remote_enqueue(
            video_id,
            title or (entry.title if entry else video_id),
            uploader or (entry.uploader if entry else ""),
            0,
            True,
        )

    def _exclude_and_drop(
        self,
        track: DiscoverTrack,
        *,
        status_key: str,
        play_if_current: bool,
    ) -> None:
        """Record a dislike, remove the track (and near-duplicates), optionally advance.

        :param track: Queue entry to drop.
        :param status_key: Locale key for the status line.
        :param play_if_current: When ``True``, start the next song if ``track`` was current.
        """
        self.taste.dislike(track)
        page = self._discover_page()
        if page is None:
            return
        was_current = False
        current = page.current_track() if hasattr(page, "current_track") else None
        if current is not None and current.video_id and current.video_id == track.video_id:
            was_current = True
        elif 0 <= getattr(page, "_selected", -1) < len(getattr(page, "_tracks", ())):
            selected = page._tracks[page._selected]
            if selected.video_id and selected.video_id == track.video_id:
                was_current = True
        # Drop near-duplicates first so play_next does not land on another dislike.
        for item in list(page._tracks):
            if item.video_id and item.video_id != track.video_id and self.taste.is_blocked(item):
                page.remove_track(item.video_id, play_next=False)
        page.remove_track(track.video_id, play_next=bool(play_if_current and was_current))
        page.set_status(self.messages[status_key], "info")
        if hasattr(page, "sync_vote_buttons"):
            page.sync_vote_buttons()

    def _discover_extend(self, track: DiscoverTrack) -> None:
        """Fetch more related songs from ``track`` and append them to the list."""
        if self._discover_busy or self._discover_extending:
            # Keep _extend_requested / resume-after-extend so a later finish can continue.
            return
        page = self._discover_page()
        if page is None:
            return
        if self.taste.vote_for(track.video_id) == VOTE_DOWN:
            page.mark_extend_idle()
            return
        mode = page.selected_mode()
        exclude = page.video_ids() | self.taste.excluded_ids()
        batch = max(1, int(self.config.discover_extend_count))
        self._discover_extending = True

        def worker() -> None:
            try:
                outcome = discover_tracks(
                    [seed_from_track(track)],
                    self.config,
                    mode=mode,
                    base_options=self.downloader._base_options(),
                    cancel_check=self._discover_cancel.is_set,
                    exclude_ids=exclude,
                    limit=batch,
                )
            except Exception as exc:
                log.exception("Discover extend failed")
                self.bridge.post(self._discover_extend_failed, str(exc))
                return
            self.bridge.post(self._discover_extend_ready, outcome)

        threading.Thread(target=worker, name="clipster-discover-extend", daemon=True).start()

    def _discover_extend_ready(self, outcome: DiscoverOutcome) -> None:
        """Append topped-up Discover tracks on the UI thread."""
        self._discover_extending = False
        page = self._discover_page()
        if page is None:
            return
        page.set_loading(False)
        outcome.tracks = self.taste.filter_tracks(outcome.tracks)
        if outcome.blocked and not outcome.tracks:
            if outcome.error_summary:
                log.warning("Discover extend blocked by YouTube: %s", outcome.error_summary)
            page.mark_extend_idle()
            page.set_status(self.messages["discover_blocked"], "error")
            return
        added = page.append_tracks(outcome.tracks)
        if added:
            page.set_status(
                self.messages.format(
                    "discover_extended",
                    added=added,
                    count=len(page._tracks),
                ),
                "ok",
            )
        elif outcome.blocked:
            page.set_status(self.messages["discover_blocked_partial"], "warn")
        else:
            page.mark_extend_idle()
            page.set_status(self.messages["discover_extend_empty"], "warn")

    def _discover_extend_failed(self, details: str) -> None:
        """Show a Discover top-up error on the UI thread."""
        self._discover_extending = False
        page = self._discover_page()
        if page is not None:
            page.mark_extend_idle()
            page.set_loading(False)
            page.set_status(
                user_facing_ytdlp_error(
                    details,
                    self.messages,
                    cookies_configured=cookies_are_configured(self.config),
                    context="discover",
                ),
                "error",
            )

    def _discover_status_text(
        self,
        outcome: DiscoverOutcome,
        *,
        queue_count: Optional[int] = None,
    ) -> Tuple[str, str]:
        """Build the Status-box message for a finished Discover run.

        :param outcome: Finished Discover search result.
        :param queue_count: Optional live queue size when batches already filled the list.
        """
        count = len(outcome.tracks) if queue_count is None else max(0, int(queue_count))
        has_tracks = count > 0 or bool(outcome.tracks)
        cookies_on = cookies_are_configured(self.config)
        if outcome.blocked and not has_tracks:
            if outcome.error_summary:
                log.warning("Discover blocked by YouTube: %s", outcome.error_summary)
            key = "discover_blocked_with_cookies" if cookies_on else "discover_blocked"
            return self.messages[key], "error"
        if outcome.blocked and has_tracks:
            base = self.messages.format("discover_results", count=count or len(outcome.tracks))
            return "{0} — {1}".format(base, self.messages["discover_blocked_partial"]), "warn"
        genre_note = ""
        if outcome.detected_genres:
            genre_note = self.messages.format(
                "discover_genres_detected",
                genres=", ".join(outcome.detected_genres),
            )
        if outcome.suffix_relaxed and has_tracks:
            base = self.messages.format("discover_results", count=count or len(outcome.tracks))
            ending = self.config.discover_search_suffix.strip() or "lyrics"
            note = self.messages.format("discover_suffix_relaxed", suffix=ending)
            parts = [base, note]
            if genre_note:
                parts.append(genre_note)
            return " — ".join(parts), "warn"
        if has_tracks:
            base = self.messages.format("discover_results", count=count or len(outcome.tracks))
            parts = [base]
            if genre_note:
                parts.append(genre_note)
            if outcome.genre_adapted:
                parts.append(self.messages["discover_genre_adapted"])
            return " — ".join(parts), "ok"
        if outcome.raw_hits and self.config.discover_require_suffix:
            ending = self.config.discover_search_suffix.strip() or "lyrics"
            return self.messages.format("discover_filtered_empty", suffix=ending, count=outcome.raw_hits), "warn"
        if outcome.warnings:
            detail = outcome.error_summary or outcome.warnings[0]
            return (
                user_facing_ytdlp_error(
                    detail,
                    self.messages,
                    cookies_configured=cookies_on,
                    context="discover",
                ),
                "error",
            )
        if outcome.canceled:
            return self.messages["discover_canceled"], "warn"
        return self.messages["discover_empty"], "warn"

    def _discover_failed(self, details: str) -> None:
        """Show a Discover error on the UI thread."""
        self._discover_busy = False
        page = self._discover_page()
        if page is not None:
            page.show_error(details)

    def _discover_download(self, track: DiscoverTrack) -> None:
        """Queue an automatic download with the configured defaults.

        Format and language dialogs are skipped: the default format from Settings
        is used and the original / best audio track is chosen automatically.
        """
        if not self._ensure_streaming_terms():
            return
        target = extract_youtube_url(track.url)
        if not target:
            self.gui.show_error(self.messages["error_title"], self.messages["error_not_a_link"])
            return
        media_format = self.config.default_format if self.config.default_format in ("mp3", "mp4") else "mp3"
        log.info("Discover auto-download: %s (%s)", track.title, media_format)
        # Stay on Streaming — do not jump to the Downloads tab.
        self.gui.show_view("discover")
        self._enqueue(target, media_format)

    # ------------------------------------------------------------------
    # The download list
    # ------------------------------------------------------------------
    def _record(self, entry: HistoryEntry) -> None:
        """Append one finished run to the history and refresh the table.

        :param entry: The entry to store.
        :return: None
        """
        self.history.add(entry)
        self.gui.render_history(self.history.entries)

    def _play_entry(self, entry: HistoryEntry) -> None:
        """Play the file of a table row with the system's default player."""
        target = entry.file_path()
        if target is None:
            self.gui.show_error(self.messages["error_title"], self.messages["history_missing"])
            self.gui.render_history(self.history.entries)
            return
        shortcuts.open_path(target)

    def _remove_entry_and_file(self, entry: HistoryEntry) -> str:
        """Delete the downloaded file and drop the row from the list.

        An entry whose file is already gone is simply removed - that is the only
        way to clear a failed attempt out of the list.

        :param entry: The entry to get rid of.
        :return: An error description, empty when it worked.
        """
        target = entry.file_path()
        if target is not None:
            try:
                target.unlink()
                log.info("Deleted %s", target)
            except OSError as exc:
                log.error("Could not delete %s: %s", target, exc)
                return str(exc)
        self.history.remove(entry)
        self.gui.render_history(self.history.entries)
        return ""

    def _delete_entry(self, entry: HistoryEntry) -> None:
        """Delete a row on request of the view window, reporting in a dialog.

        :param entry: The entry the user wants gone.
        :return: None
        """
        problem = self._remove_entry_and_file(entry)
        if problem:
            self.gui.show_error(
                self.messages["error_title"],
                self.messages.format("history_delete_failed", details=problem),
            )

    def delete_remote(self, entry: HistoryEntry) -> bool:
        """Delete a row on request of the remote interface.

        Marshals itself like :meth:`submit_remote`, and reports a failure as a
        return value instead of a dialog: nobody is sitting at the PC to close
        one, and the phone needs the answer.

        :param entry: The entry to get rid of.
        :return: ``True`` when the file and the row are gone.
        :raises RuntimeError: When the GUI bridge is no longer running.
        """
        if not self.bridge.on_gui_thread():
            return bool(self.bridge.call(self.delete_remote, entry))
        return not self._remove_entry_and_file(entry)

    def hide_remote(self, entry: HistoryEntry) -> bool:
        """Hide a download row on the phone without deleting the file.

        :param entry: The entry to remove from the list.
        :return: ``True`` when the row is gone.
        """
        if not self.bridge.on_gui_thread():
            return bool(self.bridge.call(self.hide_remote, entry))
        self.history.remove(entry)
        if self.gui.view is not None:
            self.gui.render_history(self.history.entries)
        return True

    def clear_history_remote(self) -> bool:
        """Clear the download list from the phone (files stay on disk).

        :return: ``True`` when the list was emptied.
        """
        if not self.bridge.on_gui_thread():
            return bool(self.bridge.call(self.clear_history_remote))
        self.history.clear()
        if self.gui.view is not None:
            self.gui.render_history(self.history.entries)
        log.info("Download list cleared remotely.")
        return True

    def _hide_entry(self, entry: HistoryEntry) -> None:
        """Remove ``entry`` from the Downloads list but keep the file on disk.

        :param entry: The row to hide.
        :return: None
        """
        self.history.remove(entry)
        self.gui.render_history(self.history.entries)

    def _reveal_entry(self, entry: HistoryEntry) -> None:
        """Open the folder of a table row and select the file."""
        target = entry.file_path()
        if target is None:
            self.gui.show_error(self.messages["error_title"], self.messages["history_missing"])
            self.gui.render_history(self.history.entries)
            return
        shortcuts.reveal_path(target, self.config.file_manager)

    def _open_result(self) -> None:
        """Open the file the navigation window just finished."""
        nav = self.gui.nav
        target = nav.result_path() if nav is not None else None
        if target is not None:
            shortcuts.open_path(target)

    def _reveal_result(self) -> None:
        """Open the folder of the file the navigation window just finished."""
        nav = self.gui.nav
        target = nav.result_path() if nav is not None else None
        if target is not None:
            shortcuts.reveal_path(target, self.config.file_manager)

    def _clear_history(self) -> None:
        """Empty the download list (the files stay where they are)."""
        self.history.clear()
        self.gui.render_history(self.history.entries)
        log.info("Download list cleared.")

    # ------------------------------------------------------------------
    # Clipboard monitoring
    # ------------------------------------------------------------------
    def _poll_clipboard(self) -> None:
        """Check the clipboard for a new YouTube link and reschedule itself."""
        if self._quitting:
            return
        # Watched even while a download runs, otherwise a link copied in the
        # meantime would be lost for good.
        current = self.clipboard.read()
        if current != self._last_seen:
            self._last_seen = current
            url = extract_youtube_url(current)
            if url:
                log.info("New YouTube link detected in the clipboard.")
                log.debug("Target URL: %s", url)
                self._enqueue(url, "")
        self.gui.root.after(self.config.poll_interval_ms(), self._poll_clipboard)

    def _enqueue(self, url: str, media_format: str, force: bool = False) -> str:
        """Start the download, or line it up behind the running one.

        :param url: The canonical YouTube URL.
        :param media_format: Forced format, empty to ask the user.
        :param force: Download again even when the file is already there.
        :return: One of the ``SUBMIT_*`` states, so callers that have to answer
            somebody - the remote interface - can say what actually happened.
        """
        if not self._busy:
            self._forced_format = media_format
            self._force_redownload = force
            self._start_worker(url)
            return SUBMIT_STARTED
        if url in self._active and not force:
            log.debug("%s is downloading right now - ignored.", url)
            return SUBMIT_RUNNING
        if any(queued == url for queued, _, _ in self._queue):
            log.debug("%s is already waiting - ignored.", url)
            return SUBMIT_WAITING
        if len(self._queue) >= self.MAX_QUEUE:
            log.warning("The waiting list is full (%s); %s was dropped.", self.MAX_QUEUE, url)
            return SUBMIT_FULL
        # The flag travels with the entry, otherwise a deliberate "download
        # again" would be met with "already downloaded" once its turn comes.
        self._queue.append((url, media_format, force))
        log.info("A download is running - %s links are now waiting.", len(self._queue))
        self._set_status(self.messages.format("status_queued", count=len(self._queue)))
        return SUBMIT_QUEUED

    def _start_next(self) -> None:
        """Take the next waiting link, if there is one."""
        if self._quitting or self._busy or not self._queue:
            return
        url, media_format, force = self._queue.popleft()
        log.info("Starting the next waiting download (%s left).", len(self._queue))
        self._forced_format = media_format
        self._force_redownload = force
        self._start_worker(url)

    def _start_worker(self, url: str) -> None:
        """Open the navigation window and hand the URL to the pipeline.

        :param url: The YouTube URL to process.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover - windows are built in __init__
            return
        self._asking = True
        self._nav_owner = url
        self._cancel_events[url] = threading.Event()
        self._set_status(self.messages["status_working"])
        nav.begin(self.messages["link_received"])
        worker = threading.Thread(target=self._handle_url, args=(url,),
                                  name="clipster-download", daemon=True)
        self._active[url] = worker
        worker.start()

    def _finish_worker(self, url: str, status_key: str, **kwargs: object) -> None:
        """Retire one finished run and let the next link in.

        :param url: The URL whose run ended.
        :param status_key: Translation key for the tray tooltip.
        :param kwargs: Placeholder values for the status text.
        :return: None
        """
        self._active.pop(url, None)
        self._cancel_events.pop(url, None)
        with self._progress_lock:
            self._progress.pop(url, None)
        if self._nav_owner == url:
            self._nav_owner = ""
            # A run that ends before its question was answered - a metadata
            # error, a cancel - would otherwise leave the lock set for good.
            self._asking = False
        self._forced_format = ""
        self._force_redownload = False
        # Re-sync so clearing the clipboard cannot re-trigger the same link.
        self._last_seen = self.clipboard.read()
        self._set_status(self.messages.format(status_key, **kwargs))
        if self._queue:
            # A short delay lets the user see the result before the next
            # question replaces it.
            self.gui.root.after(1200, self._start_next)

    def _cancel_all(self) -> None:
        """Signal every running download to stop."""
        for event in list(self._cancel_events.values()):
            event.set()

    def _cancel_of(self, url: str) -> threading.Event:
        """Return the cancel flag of one run, creating it when missing.

        :param url: The URL of the run.
        :return: Its cancel event.
        """
        return self._cancel_events.setdefault(url, threading.Event())

    def _nav_post(self, url: str, method: str, *args: object) -> None:
        """Call a navigation window method, but only for the owning run.

        :param url: The URL of the run that wants to draw.
        :param method: Name of the :class:`~clipster.navwindow.NavWindow` method.
        :param args: Arguments for that method.
        :return: None
        """
        nav = self.gui.nav
        if nav is None or not self._owns_nav(url):
            return
        self.bridge.post(getattr(nav, method), *args)

    def _owns_nav(self, url: str) -> bool:
        """Return ``True`` when this run may write to the navigation window.

        With several downloads in flight the single window belongs to the most
        recently started one; the others keep going and report into the list.

        :param url: The URL of the run.
        :return: Whether the window may be touched.
        """
        return self._nav_owner == url

    def _set_status(self, text: str) -> None:
        """Mirror the current status into the tray tooltip.

        :param text: The status text.
        :return: None
        """
        self.tray.set_tooltip("{0}\n{1}".format(APP_SHORT_NAME, text))

    # ------------------------------------------------------------------
    # Download pipeline (background thread)
    # ------------------------------------------------------------------
    def _handle_url(self, url: str) -> None:
        """Run metadata lookup, the question and the download for one URL.

        Executed in a worker thread; every GUI access goes through the bridge.

        :param url: The YouTube URL.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover
            return
        try:
            self._nav_post(url, "set_status", self.messages["fetching_metadata"])
            self._nav_post(url, "set_percent", None)

            try:
                info = self.downloader.fetch_info(url)
            except MetadataError as exc:
                self.bridge.post(self._question_answered)
                log.error("Video information could not be loaded: %s", exc)
                message = user_facing_ytdlp_error(
                    str(exc),
                    self.messages,
                    cookies_configured=cookies_are_configured(self.config),
                    context="metadata",
                )
                self._nav_post(url, "finish", message, STATUS_FAILED)
                self._store(url=url, status=STATUS_FAILED, error=message, error_kind="metadata")
                self.bridge.post(self._finish_worker, url, "status_failed")
                return

            if self._aborted(url):
                self._cancel_run(url, info)
                return

            answer = self._ask(nav, info)
            if not answer:
                log.warning("Download canceled by the user in the navigation window.")
                self._cancel_run(url, info)
                return
            media_format = answer.get("format") or "mp3"
            language = answer.get("language") or ""
            log.debug("Selected format: %s, audio track: %s", media_format, language or "best")

            existing = self.history.find_download(url, media_format)
            if existing is not None and not self._force_redownload:
                log.info("%s is already downloaded - offering the existing file.", existing.name)
                self.bridge.post(self._offer_existing, nav, info, existing, url, media_format)
                return

            self._run_download(url, info, media_format, language)
        except Exception:  # pragma: no cover - defensive, keeps the app alive
            log.exception("Unexpected error while processing the link")
            self._nav_post(url, "finish", self.messages["error_title"], STATUS_FAILED)
            self.bridge.post(self._finish_worker, url, "status_failed")

    def _record_progress(self, url: str, title: str, media_format: str, progress: Progress) -> None:
        """Remember the latest progress of one run for the remote interface.

        Called from the download thread; the remote interface reads the result
        from its own thread, so the dictionary is guarded.

        :param url: The URL being downloaded.
        :param title: The video title.
        :param media_format: ``mp3`` or ``mp4``.
        :param progress: The update just received from the downloader.
        :return: None
        """
        with self._progress_lock:
            self._progress[url] = {
                "url": url,
                "title": title,
                "format": media_format,
                "phase": progress.phase,
                "percent": progress.percent,
                "detail": progress.detail,
            }

    def remote_status(self) -> Dict[str, Any]:
        """Return what is happening right now, for the remote interface.

        Deliberately readable without the GUI bridge: the phone polls this every
        couple of seconds, and routing that through the Tk queue would put
        needless load on the event loop.

        :return: ``{"active": [...], "queued": int, "parallel": int}``
        """
        with self._progress_lock:
            active = [dict(item) for url, item in self._progress.items() if url in self._active]
        return {
            "active": active,
            "queued": len(self._queue),
            "parallel": self._parallel_limit(),
            # Headless (Android) is the only mode where the phone UI owns the
            # process lifecycle: Quit must tear the server down with it.
            "can_quit": bool(self.headless),
        }

    #: Settings the remote / Android UI may read and write.
    _REMOTE_SETTING_KEYS = (
        "language",
        "default_format",
        "download_dir",
        "history_limit",
        "parallel_downloads",
        "max_parallel_downloads",
        "no_playlist",
        "restrict_filenames",
        "ask_audio_language",
        "discover_search_suffix",
        "discover_mode",
        "discover_max_results",
        "discover_require_suffix",
        "cookies_risk_acknowledged",
        "cookies_from_browser",
        "cookies_file",
    )

    def remote_terms(self) -> Dict[str, Any]:
        """Return terms text and acceptance state for the phone / Android UI."""
        return {
            "app_accepted": app_terms_accepted(self.config),
            "streaming_accepted": streaming_terms_accepted(self.config),
            "app_version": TERMS_APP_VERSION,
            "streaming_version": TERMS_STREAMING_VERSION,
            "app_title": self.messages["terms_app_title"],
            "app_body": self.messages["terms_app_body"],
            "streaming_title": self.messages["terms_streaming_title"],
            "streaming_body": self.messages["terms_streaming_body"],
            "accept_label": self.messages.get("terms_accept", "Accept"),
            "decline_label": self.messages.get("terms_decline", "Decline"),
        }

    def accept_remote_terms(self, kind: str = "streaming") -> Dict[str, Any]:
        """Record terms acceptance from the phone UI (standalone Android).

        :param kind: ``streaming``, ``app``, or ``both``.
        :return: Updated :meth:`remote_terms` payload plus ``ok``.
        """
        if not self.bridge.on_gui_thread():
            return dict(self.bridge.call(self.accept_remote_terms, kind))
        which = str(kind or "streaming").strip().lower()
        if which in ("app", "both"):
            accept_app_terms(self.config)
            log.info("Remote client accepted the app terms (v%s).", self.config.terms_app_version)
        if which in ("streaming", "both"):
            accept_streaming_terms(self.config)
            log.info(
                "Remote client accepted the Streaming terms (v%s).",
                self.config.terms_streaming_version,
            )
            try:
                self.gui.root.after(0, self._maybe_schedule_auto_discover)
            except Exception:
                pass
        result = self.remote_terms()
        result["ok"] = True
        return result

    def remote_about(self) -> Dict[str, Any]:
        """Return About-page facts for the phone / Android UI."""
        return {
            "name": APP_SHORT_NAME,
            "version": APP_VERSION,
            "author": APP_AUTHOR,
            "website": APP_WEBSITE,
            "repository": APP_URL,
            "text": self.messages["about_text"],
            "license": self.messages["about_license"],
            "paths": {
                "config": str(paths.config_file()),
                "history": str(paths.history_file()),
                "log": str(paths.log_file()),
                "download_dir": paths.friendly_download_path(self.download_dir),
            },
        }

    def check_update_remote(self) -> Dict[str, Any]:
        """Ask GitHub for the newest commit on behalf of a remote device.

        Runs on the caller's thread - the web server's - because it reaches the
        network, which must never happen on the interface thread.  This is the
        same :mod:`clipster.updater` check the desktop About page runs; the
        phone only lacked a way to trigger it.

        :return: ``{"ok", "available", "local", "remote", "summary", "error"}``.
        """
        info = updater.check()
        text = (
            self.messages.format("update_available", summary=info.summary or info.remote)
            if info.available
            else self.messages.format("update_current", commit=info.remote)
        )
        if not info.known:
            text = self.messages.format("update_failed", details=info.error or "?")
        self.bridge.post(self.gui.show_update_state, text, bool(info.available))
        return {
            "ok": bool(info.known),
            "available": bool(info.available),
            "local": info.local,
            "remote": info.remote,
            "summary": info.summary,
            "error": info.error,
            "message": text,
        }

    def install_update_remote(self) -> Dict[str, Any]:
        """Fetch the newest version for a remote device and restart afterwards.

        Runs on the caller's thread for the same reason as
        :meth:`check_update_remote`.  The restart itself goes through the normal
        shutdown so the HTTP server, the downloads and the instance lock are
        released first - and it replays this process's own arguments, so the
        phone comes back headless rather than looking for a display.

        :return: ``{"ok": bool, "message": str}``.
        """
        ok, message = updater.apply()
        if not ok:
            text = self.messages.format("update_error", details=message)
            log.error("%s", text)
            self.bridge.post(self.gui.show_update_state, text, True)
            return {"ok": False, "message": text, "restarting": False}
        log.info("Update installed: %s", message)
        self.bridge.post(self._restart_for_remote_update)
        return {
            "ok": True,
            "message": self.messages["update_restarting"],
            "restarting": True,
        }

    def _restart_for_remote_update(self) -> None:
        """Shut down and come back up, after a remote device asked for it."""
        self._restart_after_update = True
        self.request_quit()

    def remote_settings(self) -> Dict[str, Any]:
        """Return the settings the phone UI may edit."""
        data: Dict[str, Any] = {}
        for key in self._REMOTE_SETTING_KEYS:
            data[key] = getattr(self.config, key)
        data["download_dir_resolved"] = paths.friendly_download_path(self.download_dir)
        data["languages"] = ["de", "en"]
        data["discover_modes"] = ["search", "related", "deezer", "listenbrainz"]
        return data

    def apply_app_settings(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a subset of settings from the phone UI and persist them.

        :param updates: Key/value pairs; unknown keys are ignored.
        :return: The settings after saving.
        """
        if not self.bridge.on_gui_thread():
            return dict(self.bridge.call(self.apply_app_settings, updates))
        config = self.config
        if "language" in updates:
            language = str(updates["language"] or "").strip().lower()
            if language in ("de", "en"):
                config.language = language
        if "default_format" in updates:
            fmt = str(updates["default_format"] or "").strip().lower()
            if fmt in MEDIA_FORMATS:
                config.default_format = fmt
        if "download_dir" in updates:
            raw = str(updates["download_dir"] or "").strip()
            if paths.is_termux():
                config.download_dir = paths.normalize_android_download_setting(raw)
            else:
                config.download_dir = raw
        if "history_limit" in updates:
            try:
                config.history_limit = max(1, int(updates["history_limit"]))
            except (TypeError, ValueError):
                pass
        if "parallel_downloads" in updates:
            config.parallel_downloads = bool(updates["parallel_downloads"])
        if "max_parallel_downloads" in updates:
            try:
                config.max_parallel_downloads = max(1, int(updates["max_parallel_downloads"]))
            except (TypeError, ValueError):
                pass
        if "no_playlist" in updates:
            config.no_playlist = bool(updates["no_playlist"])
        if "restrict_filenames" in updates:
            config.restrict_filenames = bool(updates["restrict_filenames"])
        if "ask_audio_language" in updates:
            config.ask_audio_language = bool(updates["ask_audio_language"])
        if "discover_search_suffix" in updates:
            config.discover_search_suffix = str(updates["discover_search_suffix"] or "")
        if "discover_mode" in updates:
            mode = str(updates["discover_mode"] or "").strip().lower()
            if mode in ("search", "related", "deezer", "listenbrainz"):
                config.discover_mode = mode
        if "discover_max_results" in updates:
            try:
                config.discover_max_results = max(1, int(updates["discover_max_results"]))
            except (TypeError, ValueError):
                pass
        if "discover_require_suffix" in updates:
            config.discover_require_suffix = bool(updates["discover_require_suffix"])
        if "cookies_risk_acknowledged" in updates:
            acknowledged = bool(updates["cookies_risk_acknowledged"])
            config.cookies_risk_acknowledged = acknowledged
            if acknowledged and not config.cookies_risk_acknowledged_at:
                from .terms import utc_now_iso
                config.cookies_risk_acknowledged_at = utc_now_iso()
            if not acknowledged:
                config.cookies_risk_acknowledged_at = ""
                config.cookies_from_browser = ""
                config.cookies_file = ""
        if "cookies_from_browser" in updates and config.cookies_risk_acknowledged:
            browser = str(updates["cookies_from_browser"] or "").strip().lower()
            if browser in ("", "firefox", "chrome", "chromium", "brave", "edge"):
                config.cookies_from_browser = browser
        if "cookies_file" in updates and config.cookies_risk_acknowledged:
            config.cookies_file = str(updates["cookies_file"] or "").strip()
        self._save_settings()
        return self.remote_settings()

    def _auto_language(self, info: VideoInfo) -> str:
        """Return the audio track to use without asking the user.

        With a single track that track is picked explicitly instead of leaving
        the choice to yt-dlp.  With several tracks the original one wins, which
        is what "always use the default language" means.

        :param info: The fetched video metadata.
        :return: A language code, or an empty string when nothing is known.
        """
        if len(info.audio_languages) == 1:
            return info.audio_languages[0]
        return info.original_language()

    def _ask(self, nav: Any, info: VideoInfo) -> Optional[Dict[str, str]]:
        """Ask for format and audio track, or skip when nothing to decide.

        The track question only appears when the video really offers several
        languages and the user asked to be prompted; otherwise the track is
        resolved by :meth:`_auto_language`.

        :param nav: The navigation window.
        :param info: The fetched video metadata.
        :return: ``{"format": ..., "language": ...}``, or ``None`` on cancel.
        """
        if self._forced_format:
            # Started from the view window toolbar, which already picked a format.
            self.bridge.post(self._question_answered)
            return {"format": self._forced_format, "language": self._auto_language(info)}
        prompt = Prompt()
        self.bridge.post(
            nav.ask,
            prompt,
            info.title,
            info.duration or 0,
            info.audio_languages,
            self.config.default_format,
            self.config.ask_audio_language,
            info.original_language(),
        )
        cancel = self._cancel_of(info.url)
        answer = prompt.wait(self._quit_event, cancel, nav.cancel_event)
        self.bridge.post(self._question_answered)
        if not isinstance(answer, dict):
            return None
        if not answer.get("language"):
            # No track was offered or "best available" was picked: resolve it
            # here so the download never relies on yt-dlp guessing.
            answer["language"] = self._auto_language(info)
        return answer

    def _offer_existing(self, nav: Any, info: VideoInfo, entry: HistoryEntry,
                        url: str, media_format: str) -> None:
        """Show the file that is already there instead of fetching it again.

        :param nav: The navigation window.
        :param info: The fetched video metadata.
        :param entry: The earlier download.
        :param url: The YouTube URL, for the "download again" button.
        :param media_format: The chosen format, likewise.
        :return: None
        """
        target = entry.file_path()
        if target is None:  # vanished between the lookup and now
            self._enqueue(url, media_format, force=True)
            return

        def again() -> None:
            """Start the same download once more, on the user's request."""
            nav.hide()
            self._enqueue(url, media_format, force=True)

        detail = "{0}  ·  {1}".format(entry.name, format_size(entry.size)) if entry.size else entry.name
        nav.already_downloaded(info.title, target, detail, again)
        self._finish_worker(url, "status_done", title=_trim(entry.name, 55))

    def _run_download(self, url: str, info: VideoInfo, media_format: str, language: str) -> None:
        """Download the video and report the outcome.

        :param url: The YouTube URL.
        :param info: The fetched video metadata.
        :param media_format: ``mp3`` or ``mp4``.
        :param language: Preferred audio language code, empty for best.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover
            return
        cancel_event = self._cancel_of(url)
        if self._owns_nav(url):
            self.bridge.call(nav.set_headline, info.title)
            self.bridge.call(nav.show_progress, media_format, info.duration or 0)

        def on_progress(progress: Progress) -> None:
            """Mirror a downloader progress update into the navigation window."""
            if self._owns_nav(url) and nav.cancel_event.is_set():
                cancel_event.set()
            self._nav_post(url, "set_status", self._phase_text(progress, media_format), progress.detail)
            self._nav_post(url, "set_percent", progress.percent)
            self._record_progress(url, info.title, media_format, progress)

        watcher = threading.Thread(
            target=_mirror_event,
            args=(nav.cancel_event if self._owns_nav(url) else threading.Event(), cancel_event),
            name="clipster-cancel-watch",
            daemon=True,
        )
        watcher.start()

        try:
            result: Optional[Path] = self.downloader.download(
                url=url,
                media_format=media_format,
                language=language,
                on_progress=on_progress,
                cancel_event=cancel_event,
                duration=info.duration,
                estimated_size=info.filesize,
            )
        except DownloadCanceled:
            cancel_event.set()
            self._cancel_run(url, info, media_format)
            return
        except DownloadFailed as exc:
            cancel_event.set()
            message = self._error_text(exc)
            log.error("Download failed: %s", _short_error(exc))
            self._nav_post(url, "finish", message, STATUS_FAILED)
            self._store(
                url=url,
                title=info.title,
                media_format=media_format,
                duration=info.duration or 0,
                status=STATUS_FAILED,
                error=message,
                error_kind=exc.kind,
            )
            self.bridge.post(self._finish_worker, url, "status_failed")
            return
        finally:
            cancel_event.set()

        name = result.name if result is not None else info.title
        size = _file_size(result)
        self._nav_post(
            url,
            "finish",
            self.messages["nav_done"],
            STATUS_OK,
            "{0}  ·  {1}".format(name, format_size(size)) if size else name,
            result,
        )
        self._store(
            url=url,
            title=info.title,
            name=name,
            path=str(result) if result is not None else "",
            media_format=media_format,
            size=size,
            duration=info.duration or 0,
            status=STATUS_OK,
        )

        if self.config.clear_clipboard_after_download:
            self.clipboard.clear()
        if self.config.open_folder_after_download:
            shortcuts.open_folder(self.download_dir, self.config.file_manager)
        if self.config.open_view_after_download:
            # Keep Streaming visible when the download was started from there.
            page = "downloads"
            if self.gui.view is not None and self.gui.view.current_page == "discover":
                page = "discover"
            self.bridge.post(self.gui.show_view, page)

        log.info("%s (%s)", self.messages["progress_finished"], name)
        self.bridge.post(self._finish_worker, url, "status_done", title=_trim(name, 55))

    def _question_answered(self) -> None:
        """Release the interactive lock so the next link may be picked up."""
        self._asking = False
        if self._queue:
            self.gui.root.after(200, self._start_next)

    def _cancel_run(self, url: str, info: Optional[VideoInfo] = None, media_format: str = "") -> None:
        """Record a canceled run and reset the state machine.

        :param url: The YouTube URL.
        :param info: The metadata, when it was already known.
        :param media_format: The chosen format, when it was already known.
        :return: None
        """
        self._nav_post(url, "finish", self.messages["progress_canceled"], STATUS_CANCELED)
        self._store(
            url=url,
            title=info.title if info is not None else "",
            media_format=media_format,
            duration=(info.duration or 0) if info is not None else 0,
            status=STATUS_CANCELED,
        )
        self.bridge.post(self._finish_worker, url, "status_canceled")

    def _store(self, **fields: object) -> None:
        """Queue one history entry for storage on the Tk thread.

        :param fields: Field values for :class:`~clipster.history.HistoryEntry`.
        :return: None
        """
        entry = HistoryEntry(**fields)  # type: ignore[arg-type]
        if not entry.name:
            entry.name = entry.title or entry.url
        self.bridge.post(self._record, entry)

    def _aborted(self, url: str) -> bool:
        """Return ``True`` when this run should stop (quit or cancel pressed).

        :param url: The URL of the run.
        :return: Whether it must not continue.
        """
        if self._quitting or self._quit_event.is_set():
            return True
        nav = self.gui.nav
        if nav is not None and self._owns_nav(url) and nav.cancel_event.is_set():
            return True
        return self._cancel_of(url).is_set()

    def _phase_text(self, progress: Progress, media_format: str) -> str:
        """Return the localized status text for a progress phase.

        :param progress: The reported progress.
        :param media_format: ``mp3`` or ``mp4``, used in the conversion text.
        :return: The status line for the navigation window.
        """
        if progress.phase == "downloading":
            return self.messages["progress_downloading"]
        if progress.phase == "converting":
            return self.messages.format("progress_converting", format=media_format.upper())
        if progress.phase == "merging":
            return self.messages["progress_merging"]
        if progress.phase == "finished":
            return self.messages["progress_finished"]
        if progress.phase == "preparing":
            return self.messages["progress_preparing"]
        return self.messages["progress_postprocessing"]

    def _error_text(self, error: DownloadFailed) -> str:
        """Return the localized message for a failed download.

        :param error: The raised error.
        :return: The message shown to the user and stored in the history.
        """
        return user_facing_ytdlp_error(
            str(error),
            self.messages,
            cookies_configured=cookies_are_configured(self.config),
            context="download",
        )

    def _open_download_folder(self) -> None:
        """Open the configured download folder in the file manager."""
        shortcuts.open_folder(self.download_dir, self.config.file_manager)


def _mirror_event(source: threading.Event, target: threading.Event) -> None:
    """Set ``target`` as soon as ``source`` is set.

    :param source: The event to watch (the window's cancel button).
    :param target: The event to set (the downloader's cancel flag).
    :return: None
    """
    while not target.is_set():
        if source.wait(timeout=0.2):
            target.set()
            return


def _file_size(target: Optional[Path]) -> int:
    """Return the size of ``target`` in bytes, or ``0`` when unknown.

    :param target: The downloaded file.
    :return: The size in bytes.
    """
    if target is None:
        return 0
    try:
        return target.stat().st_size
    except OSError:
        return 0


def _trim(text: str, limit: int) -> str:
    """Return ``text`` shortened to ``limit`` characters with an ellipsis.

    :param text: The text to shorten.
    :param limit: Maximum number of characters.
    :return: The shortened text.
    """
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _short_error(error: BaseException, limit: int = 300) -> str:
    """Return a compact single-line representation of an exception.

    :param error: The exception to describe.
    :param limit: Maximum number of characters.
    :return: The shortened message.
    """
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
