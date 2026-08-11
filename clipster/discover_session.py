"""Streaming session without a Tk Discover page.

Used on Android / headless: the phone UI drives search, queue and guest
playback through the remote API. A :class:`~clipster.player.DiscoverPlayer`
holds the playlist; local mpv is optional (guest play uses ``/stream/``).

Shuffle, repeat and the sleep timer are the same rules the desktop follows -
:class:`~clipster.playorder.PlayOrder` decides what comes next here too, and the
sleep timer is a plain thread because there is no Tk event loop to hang it on.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, List, Optional, Set

from . import playorder
from .discover import DiscoverTrack, dedupe_tracks
from .logging_setup import get_logger
from .player import DiscoverPlayer

log = get_logger(__name__)


class HeadlessDiscoverSession:
    """Minimal stand-in for :class:`~clipster.discover_page.DiscoverPage`."""

    def __init__(self, config: Any, messages: Any) -> None:
        """
        :param config: Live configuration (mode, extend thresholds).
        :param messages: Translation table (status strings, unused on device).
        """
        self.config = config
        self.messages = messages
        self.player = DiscoverPlayer()
        self._tracks: List[DiscoverTrack] = []
        self._selected = -1
        self._busy = False
        self._extend_requested = False
        #: Shuffle, repeat and the shuffle bag - the same class the Tk page uses.
        self._order = playorder.PlayOrder(
            shuffle=bool(getattr(config, "discover_shuffle", False)),
            repeat=str(getattr(config, "discover_repeat", playorder.REPEAT_OFF)),
        )
        #: Sleep timer: a plain timer thread, since there is no Tk loop here.
        self._sleep_timer: Optional[threading.Timer] = None
        self._sleep_ends_at = 0.0
        self.ensure_terms: Optional[Callable[[], bool]] = None
        #: Return ``False`` when a track that is not on disk may not be fetched.
        self.allow_stream: Optional[Callable[[], bool]] = None
        #: Fill the queue with the downloads that are already on disk.
        self.on_library: Optional[Callable[[], None]] = None
        self._on_like: Optional[Callable[[DiscoverTrack], None]] = None
        self._on_dislike: Optional[Callable[[DiscoverTrack], None]] = None
        self._on_hide: Optional[Callable[[DiscoverTrack], None]] = None
        self._on_download: Optional[Callable[[DiscoverTrack], None]] = None
        self._on_extend: Optional[Callable[[DiscoverTrack], None]] = None
        #: Fired after the playlist changes so the app can persist it.
        self.on_queue_changed: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Surface used by ClipsterApp remote / discover helpers
    # ------------------------------------------------------------------
    def _notify_queue_changed(self) -> None:
        """Tell the application the playlist should be saved."""
        if self.on_queue_changed is not None:
            try:
                self.on_queue_changed()
            except Exception:
                log.debug("on_queue_changed failed", exc_info=True)

    def selected_mode(self) -> str:
        """Return the configured Discover mode."""
        return str(self.config.discover_mode or "related")

    def video_ids(self) -> Set[str]:
        """Return video ids currently in the queue."""
        return {track.video_id for track in self._tracks if track.video_id}

    def set_busy(self, busy: bool, _text: str = "") -> None:
        """Record whether a Find-Similar run is in flight."""
        self._busy = bool(busy)

    def begin_discover(self) -> None:
        """Mark the start of a Find-Similar run without clearing the playlist."""
        self._extend_requested = False

    def show_progress(self, current: int, total: int, title: str) -> None:
        """Log progress; there is no on-device status line for the PC page."""
        log.debug("Discover progress %s/%s: %s", current, total, title)

    def show_empty(self, key: str) -> None:
        """Clear the queue only when it is already empty; otherwise keep it."""
        self._busy = False
        if self._tracks:
            log.info("Discover empty (%s) — keeping %s queued tracks", key, len(self._tracks))
            self._notify_queue_changed()
            return
        self._tracks = []
        self.player.set_playlist([])
        self._selected = -1
        self._notify_queue_changed()
        log.info("Discover empty: %s", key)

    def finish_discover(self, status: str = "", level: str = "ok") -> None:
        """End a Find-Similar run; guest playback is started by the web UI."""
        del status, level
        self._busy = False
        self._extend_requested = False
        if self._tracks and self._selected < 0:
            self._selected = 0
        self._notify_queue_changed()

    def set_status(self, text: str, _kind: str = "info") -> None:
        """Log a status line."""
        if text:
            log.info("%s", text)

    def set_loading(self, *_args: Any, **_kwargs: Any) -> None:
        """No loading indicator without a window."""

    def select_at(self, index: int) -> None:
        """Mark a queue index as current without starting local playback."""
        if 0 <= index < len(self._tracks):
            self._selected = index
            self._notify_queue_changed()

    def set_tracks(self, tracks: List[DiscoverTrack], status: str = "", level: str = "ok") -> None:
        """Replace the queue, as :meth:`clipster.discover_page.DiscoverPage.set_tracks` does.

        The page counterpart also starts playing when the queue was empty; here
        it must not.  On the phone the audio comes out of the browser, and the
        browser decides when to start - a track begun on this side would play on
        whatever speaker the PC has instead.

        :param tracks: The songs to put in the queue.
        :param status: Optional status text, logged rather than shown.
        :param level: Status level, kept for signature compatibility.
        :return: None
        """
        del level
        self._busy = False
        self._extend_requested = False
        self._tracks = dedupe_tracks(tracks)
        self.player.set_playlist(self._tracks)
        self._selected = 0 if self._tracks else -1
        # A new queue means a new random round.
        self._order.reset()
        if status:
            log.info("%s", status)
        self._notify_queue_changed()

    def restore_tracks(
        self,
        tracks: List[DiscoverTrack],
        *,
        index: int = 0,
        status: str = "",
        level: str = "ok",
    ) -> None:
        """Load a saved playlist without starting local playback."""
        del status, level
        self._tracks = dedupe_tracks(tracks)
        self.player.set_playlist(self._tracks)
        if self._tracks:
            self._selected = max(0, min(int(index), len(self._tracks) - 1))
        else:
            self._selected = -1
        self._order.reset()
        self._notify_queue_changed()

    def append_tracks(self, tracks: List[DiscoverTrack], update_status: bool = True) -> int:
        """Append songs to the queue.

        :param tracks: Candidates to add.
        :param update_status: Kept for DiscoverPage compatibility.
        :return: How many were newly appended.
        """
        del update_status
        fresh = dedupe_tracks(tracks, against=self._tracks)
        if not fresh:
            return 0
        self._tracks.extend(fresh)
        self.player.append_tracks(fresh)
        if self._selected < 0 and self._tracks:
            self._selected = 0
        self._notify_queue_changed()
        return len(fresh)

    def insert_tracks(self, position: int, tracks: List[DiscoverTrack]) -> int:
        """Insert songs at ``position`` without stopping playback."""
        fresh = dedupe_tracks(tracks, against=self._tracks)
        if not fresh:
            return 0
        where = max(0, min(int(position), len(self._tracks)))
        self._tracks[where:where] = fresh
        self.player.insert_tracks(where, fresh)
        if self._selected >= where:
            self._selected += len(fresh)
        self._notify_queue_changed()
        return len(fresh)

    def _may_play(self, track: DiscoverTrack) -> bool:
        """Return ``True`` when this track may be started right now.

        Same rule as the Tk page keeps: a file on disk always plays, anything
        that would have to be fetched asks the application first, and a refusal
        swaps the queue for the local library instead of stopping the music.

        :param track: The track that is about to start.
        :return: Whether playback may go ahead.
        """
        if track.is_local or self.allow_stream is None or self.allow_stream():
            return True
        log.info("Streaming is not allowed on this connection - switching to the library.")
        if self.on_library is not None:
            self.on_library()
        return False

    def play_at(self, index: int) -> None:
        """Select ``index`` and start local audio when a backend exists.

        Guest devices play via ``/stream/``; local mpv/ffplay is best-effort.
        """
        if index < 0 or index >= len(self._tracks):
            return
        if self.ensure_terms is not None and not self.ensure_terms():
            return
        if not self._may_play(self._tracks[index]):
            return
        self._selected = index
        self._notify_queue_changed()
        self.maybe_extend("play")

        def _done(result: Any) -> None:
            if getattr(result, "error", None):
                log.debug("Headless play_at skipped local audio: %s", result.error)

        try:
            self.player.play_async(index, on_done=_done, prefer_video=False)
        except Exception as exc:  # pragma: no cover - missing player binaries
            log.debug("Headless play_at failed: %s", exc)

    def toggle_play(self) -> None:
        """Play or pause the current track on the local backend."""
        if self.player.playing or self.player.process_running:
            self.player.pause()
            return
        if self.ensure_terms is not None and not self.ensure_terms():
            return
        index = self._selected if self._selected >= 0 else self.player.index
        if index < 0 and self._tracks:
            index = 0
        if index >= 0:
            self.play_at(index)

    def stop_playback(self) -> None:
        """Stop local playback."""
        self.player.stop()

    def next_index(self, *, automatic: bool) -> Optional[int]:
        """Return the row to play next, or ``None`` when the queue is through.

        Same rules as the desktop, from the same place: shuffle draws from a bag
        and repeat-one only repeats a song that ended by itself.

        :param automatic: ``True`` when the song ended on its own.
        :return: The index to play, or ``None``.
        """
        return self._order.next_index(len(self._tracks), self._selected, automatic=automatic)

    def play_next(self) -> None:
        """Advance to the next track, honouring shuffle and repeat."""
        following = self.next_index(automatic=False)
        if following is None:
            return
        self.play_at(following)

    def play_previous(self) -> None:
        """Go back one track."""
        if self._tracks and self._selected > 0:
            self.play_at(self._selected - 1)

    # ------------------------------------------------------------------
    # How the queue is played
    # ------------------------------------------------------------------
    def set_shuffle(self, enabled: bool) -> None:
        """Turn random order on or off.

        :param enabled: Whether to play in random order.
        :return: None
        """
        self._order.set_shuffle(enabled)
        self.config.discover_shuffle = self._order.shuffle

    def toggle_shuffle(self) -> None:
        """Flip random order, as the desktop button does."""
        self.set_shuffle(not self._order.shuffle)

    def set_repeat(self, mode: str) -> None:
        """Set the repeat mode.

        :param mode: ``off``, ``all`` or ``one``.
        :return: None
        """
        self._order.set_repeat(mode)
        self.config.discover_repeat = self._order.repeat

    def cycle_repeat(self) -> None:
        """Step through off, repeat all and repeat one."""
        self.set_repeat(self._order.cycle_repeat())

    def set_sleep_timer(self, minutes: int) -> None:
        """Stop playback after ``minutes``, or cancel a running timer.

        :param minutes: Minutes from now; ``0`` cancels.
        :return: None
        """
        self._cancel_sleep_timer()
        if minutes <= 0:
            self._sleep_ends_at = 0.0
            log.info("Sleep timer off.")
            return
        self._sleep_ends_at = time.monotonic() + minutes * 60
        self._sleep_timer = threading.Timer(minutes * 60, self._sleep_reached)
        self._sleep_timer.daemon = True
        self._sleep_timer.start()
        log.info("Sleep timer set: playback stops in %s minutes.", minutes)

    def sleep_minutes_left(self) -> int:
        """Return the whole minutes left on the sleep timer, ``0`` when off."""
        if not self._sleep_ends_at:
            return 0
        return max(0, int((self._sleep_ends_at - time.monotonic()) // 60) + 1)

    def _cancel_sleep_timer(self) -> None:
        """Drop a pending sleep timer without touching playback."""
        if self._sleep_timer is not None:
            self._sleep_timer.cancel()
            self._sleep_timer = None

    def _sleep_reached(self) -> None:
        """Stop playback because the sleep timer ran out."""
        self._sleep_timer = None
        self._sleep_ends_at = 0.0
        log.info("Sleep timer reached - stopping playback.")
        try:
            self.player.stop()
        except Exception:  # pragma: no cover - a dying backend must not raise here
            log.debug("Stopping on the sleep timer failed", exc_info=True)

    def current_track(self) -> Optional[DiscoverTrack]:
        """Return the track under the playhead, if any."""
        track = self.player.current
        if track is None and 0 <= self._selected < len(self._tracks):
            return self._tracks[self._selected]
        return track

    def like_current(self) -> None:
        """Record a like when a callback is wired."""
        track = self.current_track()
        if track is not None and self._on_like is not None:
            self._on_like(track)

    def dislike_current(self) -> None:
        """Record a dislike when a callback is wired."""
        track = self.current_track()
        if track is not None and self._on_dislike is not None:
            self._on_dislike(track)

    def hide_at(self, index: int) -> None:
        """Remove the track at ``index`` and exclude it from Find similar."""
        if index < 0 or index >= len(self._tracks):
            return
        track = self._tracks[index]
        if self._on_hide is not None:
            self._on_hide(track)
        elif self._on_dislike is not None:
            self._on_dislike(track)

    def hide_current(self) -> None:
        """Hide the current track when a callback is wired."""
        track = self.current_track()
        if track is None:
            return
        if self._on_hide is not None:
            self._on_hide(track)
        elif self._on_dislike is not None:
            self._on_dislike(track)

    def download_current(self) -> None:
        """Start a download when a callback is wired."""
        track = self.current_track()
        if track is not None and self._on_download is not None:
            self._on_download(track)

    def download_at(self, index: int) -> None:
        """Download the track at ``index`` without changing selection."""
        if 0 <= index < len(self._tracks) and self._on_download is not None:
            self._on_download(self._tracks[index])

    def maybe_extend(self, reason: str = "play") -> None:
        """Ask the application to fetch more related songs when the queue is short."""
        del reason
        if self._busy or self._extend_requested or not self._tracks:
            return
        remaining = len(self._tracks) - max(self._selected, 0) - 1
        threshold = max(1, int(getattr(self.config, "discover_extend_remaining", 3)))
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
        if self._on_extend is not None:
            self._on_extend(seed)

    def mark_extend_idle(self) -> None:
        """Clear the in-flight extend flag when a top-up finishes or fails."""
        self._extend_requested = False

    def remove_track(self, video_id: str, *, play_next: bool = True) -> bool:
        """Remove a track from the queue after a dislike."""
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
        if play_next and was_playing:
            if 0 <= self._selected < len(self._tracks):
                self.play_at(self._selected)
            else:
                self.player.stop()
                self._notify_queue_changed()
        else:
            self._notify_queue_changed()
        return True

    def destroy_player(self) -> None:
        """Stop playback on shutdown."""
        self._cancel_sleep_timer()
        try:
            self.player.stop()
        except Exception:  # pragma: no cover
            pass
