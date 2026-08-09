"""The downloads that are already on disk, as tracks the player can play.

Clipster downloads and Clipster plays - but until now those were two separate
halves: the Streaming tab only ever played from YouTube, while a finished
download could only be handed to the system's default player.  This module is
the bridge.  It collects what is on disk into :class:`~clipster.discover.DiscoverTrack`
objects with their :attr:`~clipster.discover.DiscoverTrack.path` filled in, which
:func:`clipster.player.local_source` then plays straight from the file.

Two sources are merged, the download list first:

* history entries that finished and whose file is still there - those bring the
  title, the length and the original URL along, so likes and "find similar"
  keep working on a track that plays from disk;
* everything else in the download folder, for files that were copied in by hand
  or downloaded before the list was kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .discover import _MEDIA_EXTENSIONS, DiscoverTrack
from .downloader import extract_video_id
from .history import STATUS_OK, HistoryEntry
from .logging_setup import get_logger

log = get_logger(__name__)

#: How many files the folder scan looks at before it gives up.  A download
#: folder is normally small; this only stops a pathological one from freezing
#: the worker.
_SCAN_LIMIT = 5000


def _track_from_entry(entry: HistoryEntry) -> Optional[DiscoverTrack]:
    """Turn a finished download into a playable track.

    :param entry: One row of the download list.
    :return: The track, or ``None`` when the file is gone or not playable.
    """
    target = entry.file_path()
    if target is None or target.suffix.lower() not in _MEDIA_EXTENSIONS:
        return None
    title = entry.title or target.stem
    return DiscoverTrack(
        url=entry.url,
        video_id=extract_video_id(entry.url) or "",
        title=title,
        duration=max(0, int(entry.duration or 0)),
        path=str(target),
    )


def _track_from_file(path: Path) -> DiscoverTrack:
    """Turn a file the download list knows nothing about into a track.

    :param path: The media file.
    :return: A track that plays from that path.
    """
    return DiscoverTrack(url="", video_id="", title=path.stem, path=str(path))


def library_tracks(
    download_dir: Path,
    history_entries: Sequence[HistoryEntry] = (),
    *,
    limit: int = 500,
) -> List[DiscoverTrack]:
    """Collect everything playable that is already on this machine.

    The download list comes first and in its own order - newest first - because
    those entries carry titles and lengths.  Files the list does not know are
    appended, newest file first.

    :param download_dir: The folder downloads are written to.
    :param history_entries: The download list, newest first.
    :param limit: Upper bound on the number of tracks returned.
    :return: The tracks, ready for the Streaming queue.
    """
    tracks: List[DiscoverTrack] = []
    seen: Dict[str, bool] = {}

    for entry in history_entries:
        if entry.status != STATUS_OK:
            continue
        track = _track_from_entry(entry)
        if track is None or track.path in seen:
            continue
        seen[track.path] = True
        tracks.append(track)
        if len(tracks) >= limit:
            return tracks

    for path in _scan_folder(download_dir):
        key = str(path)
        if key in seen:
            continue
        seen[key] = True
        tracks.append(_track_from_file(path))
        if len(tracks) >= limit:
            break
    return tracks


def _scan_folder(download_dir: Path) -> List[Path]:
    """Return the media files in ``download_dir``, newest first.

    Only the folder itself and its sub-folders are looked at - this is the place
    the program writes to, not a search of the whole disk.

    :param download_dir: The folder to scan.
    :return: The media files found, newest modification first.
    """
    root = Path(download_dir).expanduser()
    if not root.is_dir():
        return []
    found: List[tuple] = []
    seen = 0
    try:
        candidates = sorted(root.rglob("*"))
    except OSError as exc:  # pragma: no cover - unreadable folder
        log.debug("Library scan of %s failed: %s", root, exc)
        return []
    for candidate in candidates:
        if seen >= _SCAN_LIMIT:
            log.debug("Library scan stopped at %s files in %s", _SCAN_LIMIT, root)
            break
        seen += 1
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.suffix.lower() not in _MEDIA_EXTENSIONS:
                continue
            found.append((candidate.stat().st_mtime, candidate))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in found]
