"""Persistent list of everything that was downloaded.

Every finished, failed or canceled download becomes one :class:`HistoryEntry`
in a JSON file next to the configuration.  The main window renders that list,
so the user can see what worked, what did not and why.

The store is deliberately forgiving: a corrupted or partially written file is
reported and replaced instead of taking the program down.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .logging_setup import get_logger

log = get_logger(__name__)

#: Successful download.
STATUS_OK = "ok"
#: Download failed with an error.
STATUS_FAILED = "failed"
#: Download was canceled by the user.
STATUS_CANCELED = "canceled"


@dataclass
class HistoryEntry:
    """One line of the download list."""

    #: File name on disk, or the video title when nothing was written.
    name: str = ""
    #: Absolute path of the downloaded file (empty when it failed).
    path: str = ""
    #: Video title as reported by YouTube.
    title: str = ""
    #: The source URL.
    url: str = ""
    #: ``mp3`` or ``mp4``.
    media_format: str = ""
    #: :meth:`clipster.clip.ClipRange.key` when only a section was downloaded,
    #: empty for the whole video.  Older history files simply do not have it.
    section: str = ""
    #: Size in bytes, ``0`` when unknown.
    size: int = 0
    #: Length of the video in seconds, ``0`` when unknown.
    duration: int = 0
    #: ISO 8601 timestamp of when the download ended.
    finished_at: str = ""
    #: One of :data:`STATUS_OK`, :data:`STATUS_FAILED`, :data:`STATUS_CANCELED`.
    status: str = STATUS_OK
    #: Error classification (``bot``, ``unavailable``, ``metadata``, ``generic``).
    error_kind: str = ""
    #: Human readable problem description, empty on success.
    error: str = ""

    @property
    def succeeded(self) -> bool:
        """Return ``True`` when this entry represents a finished download."""
        return self.status == STATUS_OK

    def can_retry(self) -> bool:
        """Return ``True`` when this row can be sent through the pipeline again.

        Failed downloads keep the URL that was tried.  A canceled or finished
        row does not need this button: canceling was a choice, and a finished
        file is replayed with Play.

        :return: Whether a Retry action would have something to submit.
        """
        return self.status == STATUS_FAILED and bool(str(self.url or "").strip())

    def file_path(self) -> Optional[Path]:
        """Return the downloaded file, or ``None`` when it is gone."""
        if not self.path:
            return None
        candidate = Path(self.path)
        return candidate if candidate.exists() else None

    def identifier(self) -> str:
        """Return a short, stable id for this entry.

        Derived from the fields that make an entry unique rather than stored, so
        an existing history file needs no migration.  Callers outside the
        program - the remote interface - need a handle that survives a restart;
        the position in the list does not, because entries can be removed.

        :return: A 16 character hexadecimal id.
        """
        seed = "|".join((self.url, self.media_format, self.finished_at, self.name))
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def finished_datetime(self) -> Optional[datetime]:
        """Parse :attr:`finished_at` into a ``datetime``, or ``None``."""
        if not self.finished_at:
            return None
        try:
            return datetime.fromisoformat(self.finished_at)
        except ValueError:
            return None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HistoryEntry":
        """Build an entry from decoded JSON, ignoring unknown keys.

        :param data: One decoded JSON object.
        :return: The populated entry.
        """
        entry = cls()
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                continue
            default = getattr(entry, key)
            try:
                if isinstance(default, int) and not isinstance(default, bool):
                    setattr(entry, key, int(value))
                else:
                    setattr(entry, key, "" if value is None else str(value))
            except (TypeError, ValueError):
                continue
        return entry


class History:
    """Loads, appends to and saves the download list."""

    def __init__(self, path: Optional[Path] = None, limit: int = 200) -> None:
        """
        :param path: The JSON file; defaults to the per-user history file.
        :param limit: Maximum number of entries kept, newest first.
        """
        self.path = path or paths.history_file()
        self.limit = max(1, limit)
        self._entries: List[HistoryEntry] = []

    # ------------------------------------------------------------------
    @property
    def entries(self) -> List[HistoryEntry]:
        """Return the entries, newest first."""
        return list(self._entries)

    def __len__(self) -> int:
        """Return the number of stored entries."""
        return len(self._entries)

    def load(self) -> "History":
        """Read the file from disk; a missing or broken file yields an empty list.

        :return: ``self``, so the call can be chained.
        """
        self._entries = []
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Download history %s could not be read (%s) - starting a new one.", self.path, exc)
            return self
        items = raw.get("downloads") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            log.warning("Download history %s has an unexpected format - starting a new one.", self.path)
            return self
        for item in items:
            if isinstance(item, dict):
                self._entries.append(HistoryEntry.from_dict(item))
        del self._entries[self.limit :]
        return self

    def save(self) -> None:
        """Write the list back to disk, newest first."""
        payload = {"downloads": [asdict(entry) for entry in self._entries[: self.limit]]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            log.warning("Download history could not be saved: %s", exc)

    def add(self, entry: HistoryEntry) -> HistoryEntry:
        """Put ``entry`` at the top of the list and save.

        :param entry: The entry to record.
        :return: The stored entry.
        """
        if not entry.finished_at:
            entry.finished_at = datetime.now().isoformat(timespec="seconds")
        if not entry.name:
            entry.name = entry.title or entry.url
        if entry.path and not entry.size:
            try:
                entry.size = Path(entry.path).stat().st_size
            except OSError:
                entry.size = 0
        self._entries.insert(0, entry)
        del self._entries[self.limit :]
        self.save()
        return entry

    def find_download(self, url: str, media_format: str = "",
                      section: str = "") -> Optional[HistoryEntry]:
        """Return an earlier successful download of ``url`` whose file is still there.

        Used to avoid fetching and converting a video a second time when the
        same link is copied again.  The format has to match: the same video as
        MP3 and as MP4 are two different downloads - and so is a section of it,
        which is why a cut out piece never answers for the whole video.

        :param url: The canonical YouTube URL.
        :param media_format: ``mp3`` or ``mp4``; empty matches any format.
        :param section: The wanted :meth:`clipster.clip.ClipRange.key`; the
            default matches whole videos only.
        :return: The matching entry, or ``None``.
        """
        if not url:
            return None
        for entry in self._entries:
            if not entry.succeeded or entry.url != url:
                continue
            if media_format and entry.media_format != media_format:
                continue
            if entry.section != section:
                continue
            if entry.file_path() is not None:
                return entry
        return None

    def find_by_id(self, identifier: str) -> Optional[HistoryEntry]:
        """Return the entry with this :meth:`HistoryEntry.identifier`.

        :param identifier: The id handed out earlier.
        :return: The matching entry, or ``None`` when it is gone.
        """
        if not identifier:
            return None
        for entry in self._entries:
            if entry.identifier() == identifier:
                return entry
        return None

    def remove(self, entry: HistoryEntry) -> bool:
        """Drop one entry from the list and save.

        :param entry: The entry to remove; matched by identity first.
        :return: ``True`` when it was found.
        """
        for index, candidate in enumerate(self._entries):
            if candidate is entry or candidate == entry:
                del self._entries[index]
                self.save()
                return True
        return False

    def clear(self) -> None:
        """Drop every entry and save the empty list."""
        self._entries = []
        self.save()

    def remove_missing(self) -> int:
        """Drop successful entries whose file no longer exists.

        :return: The number of removed entries.
        """
        keep = [e for e in self._entries if not (e.succeeded and e.path and e.file_path() is None)]
        removed = len(self._entries) - len(keep)
        if removed:
            self._entries = keep
            self.save()
        return removed


def format_size(size: int) -> str:
    """Return a compact human readable file size.

    :param size: Size in bytes.
    :return: Something like ``4.7 MB``, or ``-`` when unknown.
    """
    if size <= 0:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return "{0:.0f} {1}".format(value, unit)
            return "{0:.1f} {1}".format(value, unit)
        value /= 1024.0
    return "{0:.1f} TB".format(value)  # pragma: no cover - unreachable


def format_duration(seconds: int) -> str:
    """Return a video length as ``M:SS`` or ``H:MM:SS``.

    :param seconds: Length in seconds.
    :return: The formatted length, or ``-`` when unknown.
    """
    if seconds <= 0:
        return "-"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{0}:{1:02d}:{2:02d}".format(hours, minutes, secs)
    return "{0}:{1:02d}".format(minutes, secs)


def format_timestamp(entry: HistoryEntry) -> str:
    """Return the finish time of ``entry`` as ``DD.MM.YYYY HH:MM``.

    :param entry: The entry to describe.
    :return: The formatted timestamp, or ``-`` when unknown.
    """
    moment = entry.finished_datetime()
    return moment.strftime("%d.%m.%Y %H:%M") if moment else "-"
