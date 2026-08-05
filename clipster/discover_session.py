"""Streaming session without a Tk Discover page.

Used on Android / headless: the phone UI drives search, queue and guest
playback through the remote API. A :class:`~clipster.player.DiscoverPlayer`
holds the playlist; local mpv is optional (guest play uses ``/stream/``).
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Set

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
        self.ensure_terms: Optional[Callable[[], bool]] = None
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
        """Mark the start of a Find-Similar run."""
        self._extend_requested = False
        self._tracks = []
        self.player.set_playlist([])
        self._selected = -1
        self._notify_queue_changed()

    def show_progress(self, current: int, total: int, title: str) -> None:
        """Log progress; there is no on-device status line for the PC page."""
        log.debug("Discover progress %s/%s: %s", current, total, title)

    def show_empty(self, key: str) -> None:
        """Clear the queue and log an empty-result key."""
        self._tracks = []
        self.player.set_playlist([])
        self._selected = -1
        self._busy = False
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

    def play_at(self, index: int) -> None:
        """Select ``index`` and start local audio when a backend exists.

        Guest devices play via ``/stream/``; local mpv/ffplay is best-effort.
        """
        if index < 0 or index >= len(self._tracks):
            return
        if self.ensure_terms is not None and not self.ensure_terms():
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

    def play_next(self) -> None:
        """Advance to the next track."""
        if self.player.next() is not None:
            self.play_at(self.player.index)
        elif self._tracks:
            nxt = min(self._selected + 1, len(self._tracks) - 1)
            if nxt != self._selected:
                self.play_at(nxt)

    def play_previous(self) -> None:
        """Go back one track."""
        if self.player.previous() is not None:
            self.play_at(self.player.index)
        elif self._tracks and self._selected > 0:
            self.play_at(self._selected - 1)

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
        try:
            self.player.stop()
        except Exception:  # pragma: no cover
            pass
