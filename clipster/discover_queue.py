"""Persist the Streaming queue across application restarts.

The last playlist (titles, ids, selection) is written to ``discover_queue.json``
beside the config so the next start can show the same songs again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths
from .discover import DiscoverTrack
from .logging_setup import get_logger

log = get_logger(__name__)

#: Cap how many queue rows are kept on disk.
_MAX_TRACKS = 200


def _track_to_dict(track: DiscoverTrack) -> Dict[str, Any]:
    """Serialize one Discover track for JSON."""
    return {
        "url": track.url or "",
        "video_id": track.video_id or "",
        "title": track.title or "",
        "uploader": track.uploader or "",
        "duration": int(track.duration or 0),
        "thumbnail": track.thumbnail or "",
        "seed_title": track.seed_title or "",
    }


def _track_from_dict(data: Dict[str, Any]) -> Optional[DiscoverTrack]:
    """Build a track from a JSON object, or ``None`` when unusable."""
    video_id = str(data.get("video_id") or "").strip()
    title = str(data.get("title") or "").strip()
    url = str(data.get("url") or "").strip()
    if not video_id and not url:
        return None
    if len(video_id) != 11 and url:
        # Prefer a real 11-char id; keep the row if the URL is the only handle.
        pass
    if not url and video_id:
        url = "https://www.youtube.com/watch?v={0}".format(video_id)
    if not title:
        title = video_id or url
    try:
        duration = max(0, int(data.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    return DiscoverTrack(
        url=url,
        video_id=video_id,
        title=title,
        uploader=str(data.get("uploader") or ""),
        duration=duration,
        thumbnail=str(data.get("thumbnail") or ""),
        seed_title=str(data.get("seed_title") or ""),
    )


class DiscoverQueueStore:
    """Load and save the Streaming playlist."""

    def __init__(self, path: Optional[Path] = None, limit: int = _MAX_TRACKS) -> None:
        """
        :param path: JSON file; defaults beside the active config.
        :param limit: Maximum tracks written to disk.
        """
        self.path = path or paths.discover_queue_file()
        self.limit = max(1, int(limit))

    def load(self) -> Tuple[List[DiscoverTrack], int]:
        """Return ``(tracks, selected_index)``; empty when nothing usable is stored.

        :return: Restored playlist and the selected row index (``-1`` when none).
        """
        if not self.path.is_file():
            return [], -1
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Discover queue %s could not be read (%s).", self.path, exc)
            return [], -1
        if not isinstance(raw, dict):
            return [], -1
        items = raw.get("tracks")
        if not isinstance(items, list):
            return [], -1
        tracks: List[DiscoverTrack] = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            track = _track_from_dict(item)
            if track is None:
                continue
            key = track.video_id or track.url
            if not key or key in seen:
                continue
            seen.add(key)
            tracks.append(track)
            if len(tracks) >= self.limit:
                break
        try:
            index = int(raw.get("index", -1))
        except (TypeError, ValueError):
            index = -1
        if tracks:
            index = max(-1, min(index, len(tracks) - 1))
        else:
            index = -1
        return tracks, index

    def save(self, tracks: List[DiscoverTrack], index: int = -1) -> None:
        """Write the current playlist to disk.

        :param tracks: Live queue rows.
        :param index: Selected / playing index.
        """
        rows = [_track_to_dict(track) for track in tracks[: self.limit] if track.video_id or track.url]
        if rows:
            index = max(-1, min(int(index), len(rows) - 1))
        else:
            index = -1
        payload = {"tracks": rows, "index": index}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            log.warning("Discover queue could not be saved: %s", exc)

    def clear(self) -> None:
        """Remove the on-disk queue file."""
        try:
            if self.path.is_file():
                self.path.unlink()
        except OSError as exc:
            log.warning("Discover queue could not be cleared: %s", exc)
