"""What the phone interface may ask for, as plain data.

This module knows nothing about HTTP: every method takes decoded arguments and
returns a status code plus a JSON-ready dictionary.  :mod:`clipster.webserver`
does the transport, this does the work - which keeps both testable on their own.

Requests arrive on the web server's own threads.  Anything that changes the
download pipeline goes through :meth:`clipster.app.ClipsterApp.submit_remote`,
which marshals itself onto the GUI thread; reading the history or the progress
snapshot needs no marshalling and must not use it, because the phone polls.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .app import (
    SUBMIT_CLOSING,
    SUBMIT_EXISTS,
    SUBMIT_FORMAT,
    SUBMIT_FULL,
    SUBMIT_INVALID,
    SUBMIT_RUNNING,
    SUBMIT_WAITING,
)
from .downloader import extract_video_id, share_url
from .history import HistoryEntry
from .logging_setup import get_logger
from .qrview import qr_svg

log = get_logger(__name__)

#: HTTP status for a refused Streaming command.
_DISCOVER_STATUS = {
    "unknown_command": 400,
    # The terms question is a dialog on the PC, so this is not something the
    # phone can resolve by retrying - it needs a person at the machine.
    "terms_required": 403,
    "unknown_track": 400,
    "unavailable": 503,
    "closing": 503,
}

#: HTTP status for every submission outcome.  Anything not listed was accepted.
_SUBMIT_STATUS = {
    SUBMIT_INVALID: 400,
    SUBMIT_FORMAT: 400,
    SUBMIT_EXISTS: 200,
    SUBMIT_RUNNING: 409,
    SUBMIT_WAITING: 409,
    SUBMIT_FULL: 503,
    SUBMIT_CLOSING: 503,
}


def entry_to_dict(entry: HistoryEntry) -> Dict[str, Any]:
    """Describe one download for the phone.

    The stored path is deliberately left out: the phone reaches a file through
    ``/media/<id>``, and telling it where the file lives on the PC would only
    invite somebody to ask for that path directly.

    :param entry: The history entry to describe.
    :return: A JSON-ready dictionary.
    """
    return {
        "id": entry.identifier(),
        "name": entry.name,
        "title": entry.title,
        "url": entry.url,
        "format": entry.media_format,
        "size": entry.size,
        "duration": entry.duration,
        "finished_at": entry.finished_at,
        "status": entry.status,
        "error_kind": entry.error_kind,
        "error": entry.error,
        "playable": entry.file_path() is not None,
    }


class RemoteApi:
    """The endpoints of the phone interface, independent of HTTP."""

    def __init__(self, app: Any) -> None:
        """
        :param app: The running :class:`clipster.app.ClipsterApp`.
        """
        self._app = app
        self._lock = threading.Lock()
        self._contacts = 0
        self._last_contact = ""

    # ------------------------------------------------------------------
    # Contact tracking - so the phone page can say "a phone is talking to us"
    # ------------------------------------------------------------------
    def _record_contact(self) -> None:
        """Remember that a device just asked for something.

        Called from the server's request threads, hence the lock.
        """
        stamp = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._contacts += 1
            self._last_contact = stamp

    def contact_info(self) -> Dict[str, Any]:
        """Return how often and when a device last got through.

        :return: ``{"contacts": int, "last_contact": str}``
        """
        with self._lock:
            return {"contacts": self._contacts, "last_contact": self._last_contact}

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def downloads(self) -> Tuple[int, Dict[str, Any]]:
        """Return the download list, newest first.

        :return: ``(200, {"downloads": [...]})``
        """
        self._record_contact()
        entries: List[HistoryEntry] = self._app.history.entries
        return 200, {"downloads": [entry_to_dict(entry) for entry in entries]}

    def status(self, connection: str = "") -> Tuple[int, Dict[str, Any]]:
        """Return what is downloading right now.

        The device reports its own connection here rather than through a call
        of its own: it polls this while the page is open anyway, so the backend
        learns about a walk out of Wi-Fi within one poll and without extra
        traffic.  Only the device on the connection can know this - the PC
        cannot see which network a phone is holding.

        :param connection: What ``navigator.connection`` reported, if anything.
        :return: ``(200, {"active": [...], "queued": int, "parallel": int, ...})``
        """
        self._record_contact()
        if connection:
            self._app.set_connection_type(connection)
        return 200, self._app.remote_status()

    def quit(self) -> Tuple[int, Dict[str, Any]]:
        """Ask the application to shut down (server, downloads, wake lock).

        Used by the Android launcher when the user taps Quit / Beenden.

        :return: ``(200, {"ok": True})`` once the quit was requested.
        """
        self._record_contact()
        try:
            self._app.request_quit()
        except Exception as exc:  # pragma: no cover - must still answer the phone
            log.debug("Remote quit refused: %s", exc)
            return 503, {"ok": False, "error": "closing"}
        return 200, {"ok": True}

    def about(self) -> Tuple[int, Dict[str, Any]]:
        """Return About-page data for the phone UI."""
        self._record_contact()
        return 200, self._app.remote_about()

    def terms(self) -> Tuple[int, Dict[str, Any]]:
        """Return terms text and acceptance flags for the phone UI."""
        self._record_contact()
        return 200, self._app.remote_terms()

    def accept_terms(self, kind: str = "streaming") -> Tuple[int, Dict[str, Any]]:
        """Accept terms from the phone UI (standalone Android).

        :param kind: ``streaming``, ``app``, or ``both``.
        """
        self._record_contact()
        try:
            return 200, self._app.accept_remote_terms(kind)
        except RuntimeError as exc:
            log.debug("Remote terms refused: %s", exc)
            return 503, {"ok": False, "error": "closing"}

    def update_check(self) -> Tuple[int, Dict[str, Any]]:
        """Look for a newer version on GitHub, for the phone UI.

        Reaches the network, so it runs on the calling web-server thread just
        like :meth:`discover_search` does.

        :return: ``(200, {...})``, or ``(503, ...)`` while shutting down.
        """
        self._record_contact()
        try:
            return 200, self._app.check_update_remote()
        except Exception as exc:  # pragma: no cover - needs the network
            log.debug("Remote update check failed: %s", exc)
            return 200, {"ok": False, "available": False, "error": str(exc)}

    def update_install(self) -> Tuple[int, Dict[str, Any]]:
        """Fetch the newest version and restart, for the phone UI.

        :return: ``(200, {...})``, or ``(503, ...)`` while shutting down.
        """
        self._record_contact()
        try:
            return 200, self._app.install_update_remote()
        except RuntimeError as exc:
            log.debug("Remote update refused: %s", exc)
            return 503, {"ok": False, "error": "closing"}
        except Exception as exc:  # pragma: no cover - needs the network
            log.debug("Remote update failed: %s", exc)
            return 200, {"ok": False, "message": str(exc)}

    def settings(self) -> Tuple[int, Dict[str, Any]]:
        """Return editable settings for the phone UI."""
        self._record_contact()
        return 200, self._app.remote_settings()

    def save_settings(self, updates: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Persist settings from the phone UI.

        :param updates: Key/value pairs to apply.
        :return: ``(200, settings)`` after saving.
        """
        self._record_contact()
        try:
            return 200, self._app.apply_app_settings(updates if isinstance(updates, dict) else {})
        except RuntimeError as exc:
            log.debug("Remote settings refused: %s", exc)
            return 503, {"error": "closing"}

    def discover(self) -> Tuple[int, Dict[str, Any]]:
        """Return the Streaming queue and what is playing.

        :return: ``(200, state)``, or ``(503, ...)`` while the program shuts down.
        """
        self._record_contact()
        try:
            return 200, self._app.discover_remote_state()
        except RuntimeError as exc:
            log.debug("Streaming state refused: %s", exc)
            return 503, {"available": False, "error": "closing"}

    def discover_command(self, command: str, index: int = -1,
                         seconds: float = 0.0, video_id: str = "") -> Tuple[int, Dict[str, Any]]:
        """Run one Streaming command for the phone.

        :param command: The command name.
        :param index: Queue position, for ``play``.
        :param seconds: Target position, for ``seek``.
        :param video_id: Optional YouTube id for vote actions.
        :return: The HTTP status and the result including the new state.
        """
        self._record_contact()
        try:
            result = self._app.discover_remote_command(command, index, seconds, video_id)
        except RuntimeError as exc:
            log.debug("Streaming command refused: %s", exc)
            return 503, {"ok": False, "error": "closing"}
        if result.get("ok"):
            return 200, result
        return _DISCOVER_STATUS.get(str(result.get("error")), 400), result

    def discover_search(self, query: str) -> Tuple[int, Dict[str, Any]]:
        """Search YouTube for a term typed on the device.

        :param query: The search term.
        :return: The HTTP status and the results.
        """
        self._record_contact()
        try:
            result = self._app.discover_remote_search(query)
        except RuntimeError as exc:
            log.debug("Streaming search refused: %s", exc)
            return 503, {"ok": False, "error": "closing", "results": []}
        if result.get("ok"):
            return 200, result
        return _DISCOVER_STATUS.get(str(result.get("error")), 502), result

    def discover_enqueue(self, track: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Add a searched track to the queue and start it.

        :param track: ``{"video_id", "title", "uploader", "duration", "play"}``.
        :return: The HTTP status and the result including the new state.
        """
        self._record_contact()
        try:
            result = self._app.discover_remote_enqueue(
                str(track.get("video_id") or ""),
                str(track.get("title") or ""),
                str(track.get("uploader") or ""),
                int(track.get("duration") or 0),
                bool(track.get("play", True)),
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            log.debug("Streaming enqueue refused: %s", exc)
            return 503, {"ok": False, "error": "closing"}
        if result.get("ok"):
            return 200, result
        return _DISCOVER_STATUS.get(str(result.get("error")), 400), result

    def share_code(self, video_id: str) -> Tuple[int, str]:
        """Return a QR code for one song, as SVG.

        Only a video id is accepted, and the URL around it is built here: this
        is a share button, not a service that turns arbitrary text into codes.

        :param video_id: The eleven character YouTube id.
        :return: ``(status, svg)``; the SVG is empty when nothing was produced.
        """
        self._record_contact()
        url = share_url(video_id)
        if not url:
            return 404, ""
        svg = qr_svg(url)
        if svg is None:
            # The optional qrcode package is missing; say so rather than
            # serving a broken image the page cannot explain.
            log.info("No QR code for %s: the qrcode package is not installed.", video_id)
            return 503, ""
        return 200, svg

    def scan(self, text: str) -> Tuple[int, Dict[str, Any]]:
        """Take what a camera read and put the song in the queue.

        The scanned text is parsed with the same
        :func:`clipster.downloader.extract_video_id` the clipboard watcher uses,
        so a link that works when copied also works when scanned - and the
        pattern is not maintained twice, once here and once in JavaScript.

        :param text: Whatever the scanner decoded.
        :return: The HTTP status and the result including the new state.
        """
        self._record_contact()
        video_id = extract_video_id(str(text or ""))
        if not video_id:
            return 400, {"ok": False, "error": "not_a_youtube_link"}
        try:
            # Queued, not started: a song somebody just shared should not cut
            # off whatever is currently playing.
            result = self._app.discover_remote_enqueue(video_id, "", "", 0, False)
        except (RuntimeError, TypeError, ValueError) as exc:
            log.debug("Scan enqueue refused: %s", exc)
            return 503, {"ok": False, "error": "closing"}
        result["video_id"] = video_id
        if result.get("ok"):
            return 200, result
        return _DISCOVER_STATUS.get(str(result.get("error")), 400), result

    def streaming_allowed(self) -> bool:
        """Return ``True`` when tracks that are not on disk may be fetched.

        :return: Whether the current connection allows streaming.
        """
        try:
            return bool(self._app.streaming_allowed())
        except Exception:  # pragma: no cover - a shutting-down app must not 500
            log.debug("The playback rule could not be read", exc_info=True)
            return True

    def discover_next(self, index: int, automatic: bool = True) -> Tuple[int, Dict[str, Any]]:
        """Ask which queue row the device should play next.

        :param index: The row the device is on.
        :param automatic: ``True`` when the song ended by itself.
        :return: The HTTP status and ``{"ok", "index"}``.
        """
        self._record_contact()
        try:
            return 200, self._app.discover_remote_next(index, automatic)
        except RuntimeError as exc:
            log.debug("Streaming next refused: %s", exc)
            return 503, {"ok": False, "index": -1, "error": "closing"}

    def discover_audio(self, video_id: str) -> Tuple[str, Dict[str, str]]:
        """Resolve the audio stream of a queued track for playback on a device.

        :param video_id: The video id from the queue.
        :return: ``(url, headers)``; the URL is empty when nothing was resolved.
        """
        self._record_contact()
        try:
            url, headers = self._app.discover_remote_audio(video_id)
        except RuntimeError as exc:
            log.debug("Streaming audio refused: %s", exc)
            return "", {}
        return str(url or ""), dict(headers or {})

    def discover_video(self, video_id: str) -> Tuple[str, Dict[str, str]]:
        """Resolve the video stream of a queued track for playback on a device.

        :param video_id: The video id from the queue.
        :return: ``(url, headers)``; the URL is empty when nothing was resolved.
        """
        self._record_contact()
        try:
            url, headers = self._app.discover_remote_video(video_id)
        except RuntimeError as exc:
            log.debug("Streaming video refused: %s", exc)
            return "", {}
        return str(url or ""), dict(headers or {})

    def media(self, entry_id: str) -> Optional[Path]:
        """Resolve a download id to the file on disk.

        The id is looked up in the history and the stored path is used; no part
        of the request ever reaches the file system, so a crafted id cannot
        escape the download folder.

        :param entry_id: The id from :meth:`HistoryEntry.identifier`.
        :return: The existing file, or ``None``.
        """
        self._record_contact()
        entry = self._app.history.find_by_id(entry_id)
        if entry is None:
            return None
        return entry.file_path()

    def queue_media(self, position: str) -> Optional[Path]:
        """Resolve a queue position to the file it plays from.

        The Streaming queue can hold songs that are already on disk - the
        library.  Those must play on the phone without touching the network,
        which is the whole point of the mobile-data rule, and ``/stream/`` can
        only serve things that come from YouTube.

        Like :meth:`media`, no part of the request reaches the file system: the
        position picks a track the queue already holds and the path stored on it
        is used, so nothing can be talked into serving an arbitrary file.

        :param position: The queue index, as it arrived in the path.
        :return: The existing file, or ``None``.
        """
        self._record_contact()
        try:
            index = int(position)
        except (TypeError, ValueError):
            return None
        try:
            return self._app.queue_track_path(index)
        except RuntimeError as exc:
            log.debug("Queue media refused: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Changing
    # ------------------------------------------------------------------
    def submit(self, url: str, media_format: str, force: bool = False) -> Tuple[int, Dict[str, Any]]:
        """Hand a link to the download pipeline.

        :param url: The URL as the phone sent it.
        :param media_format: ``mp3`` or ``mp4``.
        :param force: Download again even when the file is already there.
        :return: The HTTP status and a body describing the outcome.
        """
        self._record_contact()
        try:
            result = self._app.submit_remote(url, media_format, force)
        except RuntimeError as exc:
            # The GUI bridge stopped: the program is on its way out.
            log.debug("Remote submission refused: %s", exc)
            return 503, {"state": SUBMIT_CLOSING, "accepted": False}
        body: Dict[str, Any] = {
            "state": result.state,
            "accepted": result.accepted,
            "url": result.url,
            "position": result.position,
        }
        if result.entry_id:
            body["id"] = result.entry_id
            entry = self._app.history.find_by_id(result.entry_id)
            if entry is not None:
                body["entry"] = entry_to_dict(entry)
        status = _SUBMIT_STATUS.get(result.state, 202)
        return status, body

    def delete(self, entry_id: str) -> Tuple[int, Dict[str, Any]]:
        """Delete a downloaded file and its list entry.

        :param entry_id: The id from :meth:`HistoryEntry.identifier`.
        :return: The HTTP status and a short body.
        """
        self._record_contact()
        entry = self._app.history.find_by_id(entry_id)
        if entry is None:
            return 404, {"deleted": False}
        try:
            deleted = self._app.delete_remote(entry)
        except RuntimeError as exc:
            log.debug("Remote deletion refused: %s", exc)
            return 503, {"deleted": False}
        if not deleted:
            return 500, {"deleted": False, "id": entry_id}
        return 200, {"deleted": True, "id": entry_id}

    def hide(self, entry_id: str) -> Tuple[int, Dict[str, Any]]:
        """Remove a list row but keep the file on disk.

        :param entry_id: The id from :meth:`HistoryEntry.identifier`.
        :return: The HTTP status and a short body.
        """
        self._record_contact()
        entry = self._app.history.find_by_id(entry_id)
        if entry is None:
            return 404, {"hidden": False}
        try:
            hidden = self._app.hide_remote(entry)
        except RuntimeError as exc:
            log.debug("Remote hide refused: %s", exc)
            return 503, {"hidden": False}
        return 200 if hidden else 500, {"hidden": bool(hidden), "id": entry_id}

    def clear_history(self) -> Tuple[int, Dict[str, Any]]:
        """Empty the download list; files stay on disk."""
        self._record_contact()
        try:
            cleared = self._app.clear_history_remote()
        except RuntimeError as exc:
            log.debug("Remote clear refused: %s", exc)
            return 503, {"cleared": False}
        return 200, {"cleared": bool(cleared)}
