"""Persistent thumbs-up / thumbs-down feedback for Streaming Discover.

Liked tracks become preferred seeds for “more like this”.  Disliked tracks
(and close title matches) are filtered out of future result lists.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from . import paths
from .discover import DiscoverTrack, titles_similar
from .history import STATUS_OK, HistoryEntry
from .logging_setup import get_logger

log = get_logger(__name__)

VOTE_UP = "up"
VOTE_DOWN = "down"

#: Cap how many taste votes are kept on disk.
_MAX_ENTRIES = 500


@dataclass
class TasteEntry:
    """One thumbs vote for a Discover / Streaming track."""

    video_id: str = ""
    title: str = ""
    uploader: str = ""
    url: str = ""
    vote: str = VOTE_UP
    voted_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TasteEntry":
        """Build an entry from decoded JSON, ignoring unknown keys."""
        entry = cls()
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                continue
            entry_default = getattr(entry, key)
            try:
                if isinstance(entry_default, int) and not isinstance(entry_default, bool):
                    setattr(entry, key, int(value))
                else:
                    setattr(entry, key, "" if value is None else str(value))
            except (TypeError, ValueError):
                continue
        if entry.vote not in (VOTE_UP, VOTE_DOWN):
            entry.vote = VOTE_UP
        return entry

    @classmethod
    def from_track(cls, track: DiscoverTrack, vote: str) -> "TasteEntry":
        """Create a vote row from a live Discover track."""
        return cls(
            video_id=track.video_id or "",
            title=track.title or "",
            uploader=track.uploader or "",
            url=track.url or "",
            vote=vote if vote in (VOTE_UP, VOTE_DOWN) else VOTE_UP,
            voted_at=datetime.now().isoformat(timespec="seconds"),
        )


class DiscoverTaste:
    """Loads and saves Streaming like / dislike votes."""

    def __init__(self, path: Optional[Path] = None, limit: int = _MAX_ENTRIES) -> None:
        """
        :param path: JSON file; defaults beside the active config.
        :param limit: Maximum votes kept, newest first.
        """
        self.path = path or paths.discover_taste_file()
        self.limit = max(1, int(limit))
        self._entries: List[TasteEntry] = []

    @property
    def entries(self) -> List[TasteEntry]:
        """Return all votes, newest first."""
        return list(self._entries)

    def load(self) -> "DiscoverTaste":
        """Read votes from disk; missing or broken files yield an empty store."""
        self._entries = []
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Discover taste %s could not be read (%s) - starting empty.", self.path, exc)
            return self
        items = raw.get("votes") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            log.warning("Discover taste %s has an unexpected format - starting empty.", self.path)
            return self
        for item in items:
            if isinstance(item, dict):
                self._entries.append(TasteEntry.from_dict(item))
        del self._entries[self.limit :]
        return self

    def save(self) -> None:
        """Write votes back to disk."""
        payload = {"votes": [asdict(entry) for entry in self._entries[: self.limit]]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            log.warning("Discover taste could not be saved: %s", exc)

    def vote_for(self, video_id: str) -> Optional[str]:
        """Return ``up`` / ``down`` for ``video_id``, or ``None``."""
        if not video_id:
            return None
        for entry in self._entries:
            if entry.video_id == video_id:
                return entry.vote
        return None

    def like(self, track: DiscoverTrack) -> TasteEntry:
        """Record a thumbs-up for ``track`` (replaces any prior vote)."""
        return self._record(track, VOTE_UP)

    def dislike(self, track: DiscoverTrack) -> TasteEntry:
        """Record a thumbs-down for ``track`` (replaces any prior vote)."""
        return self._record(track, VOTE_DOWN)

    def _record(self, track: DiscoverTrack, vote: str) -> TasteEntry:
        """Insert or replace the vote for ``track.video_id``."""
        entry = TasteEntry.from_track(track, vote)
        if entry.video_id:
            self._entries = [item for item in self._entries if item.video_id != entry.video_id]
        self._entries.insert(0, entry)
        del self._entries[self.limit :]
        self.save()
        return entry

    def liked_seeds(self) -> List[HistoryEntry]:
        """Return liked tracks as Discover seeds (newest first)."""
        seeds: List[HistoryEntry] = []
        seen: Set[str] = set()
        for entry in self._entries:
            if entry.vote != VOTE_UP:
                continue
            key = (entry.video_id or entry.url or entry.title).strip().lower()
            if not key or key in seen:
                continue
            if not entry.title.strip() and not entry.url.strip():
                continue
            seen.add(key)
            seeds.append(
                HistoryEntry(
                    name=entry.title or entry.video_id,
                    title=entry.title,
                    url=entry.url,
                    status=STATUS_OK,
                )
            )
        return seeds

    def excluded_ids(self) -> Set[str]:
        """Video ids that must not appear in Discover results again."""
        return {entry.video_id for entry in self._entries if entry.vote == VOTE_DOWN and entry.video_id}

    def disliked_titles(self) -> List[str]:
        """Titles from thumbs-down votes, for similarity filtering."""
        return [entry.title for entry in self._entries if entry.vote == VOTE_DOWN and entry.title.strip()]

    def is_blocked(self, track: DiscoverTrack) -> bool:
        """Return whether ``track`` should be skipped after a dislike."""
        if track.video_id and track.video_id in self.excluded_ids():
            return True
        for title in self.disliked_titles():
            if titles_similar(track.title, title):
                return True
        return False

    def filter_tracks(self, tracks: Sequence[DiscoverTrack]) -> List[DiscoverTrack]:
        """Drop disliked ids and near-duplicate disliked titles."""
        kept: List[DiscoverTrack] = []
        for track in tracks:
            if self.is_blocked(track):
                continue
            kept.append(track)
        return kept

    def merge_seeds(
        self,
        seeds: Sequence[HistoryEntry],
        *,
        prefer_liked: bool = True,
    ) -> List[HistoryEntry]:
        """Prepend liked seeds and drop seeds that match disliked titles/ids.

        :param seeds: History / folder seeds for this Discover run.
        :param prefer_liked: When ``True``, thumbs-up tracks lead the list.
        :return: Deduplicated seed list.
        """
        blocked_ids = self.excluded_ids()
        disliked = self.disliked_titles()

        def usable(entry: HistoryEntry) -> bool:
            video_id = ""
            url = (entry.url or "").strip()
            if "v=" in url:
                video_id = url.rsplit("v=", 1)[-1][:11]
            if video_id and video_id in blocked_ids:
                return False
            for title in disliked:
                if titles_similar(entry.title or entry.name, title):
                    return False
            return True

        merged: List[HistoryEntry] = []
        seen: Set[str] = set()

        def add_many(items: Iterable[HistoryEntry]) -> None:
            for entry in items:
                if not usable(entry):
                    continue
                key = (entry.url or entry.title or entry.name).strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(entry)

        if prefer_liked:
            add_many(self.liked_seeds())
        add_many(seeds)
        return merged
