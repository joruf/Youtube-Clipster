"""Find thematically related YouTube songs from downloads and local media.

Two strategies are supported:

* ``search`` - run a YouTube search for each seed title
* ``related`` - pull related videos for each seed URL (falls back to search)

Seeds are collected automatically in priority order (history → likes → download
folder → bounded disk scan) until enough titles exist for a Discover search.
Genre cues in titles and folder names (techno, pop, metal, …) steer the search
queries and ranking.  For instrumental genres a ``lyrics`` ending is replaced
automatically so results stay on-topic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .config import Config
from .downloader import _import_yt_dlp, classify_error, extract_youtube_url
from .history import STATUS_OK, HistoryEntry
from .logging_setup import get_logger
from . import paths
from .recommend import similar_queries

log = get_logger(__name__)

#: Modes accepted by :func:`discover_tracks`.
MODE_SEARCH = "search"
MODE_RELATED = "related"
MODE_DEEZER = "deezer"
MODE_LISTENBRAINZ = "listenbrainz"
DISCOVER_MODES = frozenset({MODE_SEARCH, MODE_RELATED, MODE_DEEZER, MODE_LISTENBRAINZ})

#: Stop after this many consecutive bot / rate-limit failures.
_MAX_CONSECUTIVE_BLOCKS = 2

#: Progress callback: ``(seed_index_1based, seed_total, seed_title)``.
DiscoverProgress = Callable[[int, int, str], None]

#: Batch callback: newly found tracks after each seed / genre query.
DiscoverBatch = Callable[[List["DiscoverTrack"]], None]

#: File name extensions treated as seed media when scanning a folder.
_MEDIA_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
        ".mp4",
        ".m4v",
        ".webm",
        ".mkv",
        ".avi",
        ".mov",
    }
)

#: yt-dlp often appends `` [videoId]`` before the extension.
_ID_IN_NAME_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]\s*$")

#: Cap how many files from a folder / disk scan become Discover seeds.
_MAX_FOLDER_SEEDS = 40

#: Default recursion depth for the bounded disk scan (Music / Downloads trees).
_DISK_SCAN_MAX_DEPTH = 3

#: Stop walking after this many filesystem entries during a disk scan.
_DISK_SCAN_MAX_VISITED = 2000

#: Directory names skipped during a disk scan (case-insensitive basename match).
_DISK_SCAN_SKIP_NAMES = frozenset(
    {
        "proc",
        "sys",
        "dev",
        "run",
        "windows",
        "system32",
        "system volume information",
        "$recycle.bin",
        "recycle.bin",
        "recycler",
        "node_modules",
        ".git",
        ".svn",
        ".hg",
        "__pycache__",
        "cache",
        "caches",
        "tmp",
        "temp",
        "appdata",
        "application data",
        "library",  # macOS ~/Library
    }
)

#: Boilerplate words ignored when comparing song titles for duplicates.
_TITLE_NOISE_WORDS = frozenset(
    {
        "lyrics",
        "lyric",
        "official",
        "video",
        "audio",
        "music",
        "hd",
        "hq",
        "4k",
        "mv",
        "visualizer",
        "visualiser",
        "topic",
        "remastered",
        "remaster",
        "live",
        "cover",
        "karaoke",
        "instrumental",
        "full",
        "version",
        "explicit",
        "clean",
        "feat",
        "ft",
        "vs",
        "and",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "with",
        "for",
        "from",
        "song",
        "track",
        "original",
        "mix",
        "remix",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


@dataclass(frozen=True)
class DiscoverTrack:
    """One playable Discover result."""

    #: Canonical watch URL.
    url: str
    #: Video id.
    video_id: str
    #: Display title.
    title: str
    #: Channel / uploader name when known.
    uploader: str = ""
    #: Length in seconds when known.
    duration: int = 0
    #: Thumbnail URL when known.
    thumbnail: str = ""
    #: Title of the history entry that triggered this result.
    seed_title: str = ""
    #: Absolute path of a file that is already on disk.  Set for tracks that
    #: come from the download folder: those play straight from there instead of
    #: being resolved against YouTube again.
    path: str = ""

    @property
    def is_local(self) -> bool:
        """Return ``True`` when this track plays from a file on disk."""
        return bool(self.path)


def seed_from_track(track: DiscoverTrack) -> HistoryEntry:
    """Build a history-shaped seed from one Discover result.

    :param track: A track the user is listening to or downloading.
    :return: A seed usable by :func:`discover_tracks`.
    """
    return HistoryEntry(
        name=track.title,
        title=track.title,
        url=track.url,
        status=STATUS_OK,
    )


def _prepare_title(title: str) -> str:
    """Strip channel suffixes and noise-only brackets before tokenizing."""
    text = " ".join((title or "").split())
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    # Drop "(lyrics)", "[Official Video]", etc. when the inside is only noise.
    def _paren_keep(match: re.Match) -> str:
        inner = match.group(0)[1:-1]
        words = [w.lower() for w in _WORD_RE.findall(inner)]
        if words and all(w in _TITLE_NOISE_WORDS or len(w) < 3 for w in words):
            return " "
        return match.group(0)

    text = _PAREN_RE.sub(_paren_keep, text)
    # Drop a leading "HD Lyrics Video - ..." style prefix with no real content.
    if " - " in text:
        left, right = text.split(" - ", 1)
        left_words = [w.lower() for w in _WORD_RE.findall(left)]
        if left_words and all(w in _TITLE_NOISE_WORDS or len(w) < 3 for w in left_words):
            text = right
    return text


def title_tokens(title: str) -> Set[str]:
    """Return significant lowercase words from a song title."""
    tokens: Set[str] = set()
    for word in _WORD_RE.findall(_prepare_title(title)):
        lower = word.lower()
        if len(lower) < 3 or lower in _TITLE_NOISE_WORDS:
            continue
        tokens.add(lower)
    return tokens


def titles_similar(left: str, right: str, *, threshold: float = 0.72) -> bool:
    """Return whether two titles look like the same / very similar song."""
    a = title_tokens(left)
    b = title_tokens(right)
    if not a or not b:
        return False
    if a == b:
        return True
    overlap = len(a & b)
    if overlap == 0:
        return False
    # Prefer the smaller set so "Song" vs "Song lyrics remix" still matches.
    return (overlap / float(min(len(a), len(b)))) >= threshold


def dedupe_tracks(
    tracks: Sequence[DiscoverTrack],
    *,
    against: Optional[Sequence[DiscoverTrack]] = None,
) -> List[DiscoverTrack]:
    """Drop tracks that share a video id or a near-identical title.

    :param tracks: Candidates, kept in order (first wins).
    :param against: Optional already-accepted tracks to also compare against.
    :return: Deduplicated list.
    """
    kept: List[DiscoverTrack] = list(against or ())
    seen_ids: Set[str] = {track.video_id for track in kept if track.video_id}
    fresh: List[DiscoverTrack] = []
    for track in tracks:
        if track.video_id and track.video_id in seen_ids:
            continue
        if any(titles_similar(track.title, existing.title) for existing in kept):
            continue
        if any(titles_similar(track.title, existing.title) for existing in fresh):
            continue
        fresh.append(track)
        if track.video_id:
            seen_ids.add(track.video_id)
        kept.append(track)
    return fresh


@dataclass
class DiscoverOutcome:
    """Result of a Discover run, including status for the UI."""

    #: Tracks to show (may be empty).
    tracks: List[DiscoverTrack] = field(default_factory=list)
    #: How many seed titles were queried.
    seeds_tried: int = 0
    #: Hits returned by YouTube before the suffix filter.
    raw_hits: int = 0
    #: True when YouTube bot / rate-limit blocking was detected.
    blocked: bool = False
    #: True when the suffix filter removed every hit and was relaxed.
    suffix_relaxed: bool = False
    #: Short technical detail for logs / advanced status.
    error_summary: str = ""
    #: Human-oriented warning lines collected during the run.
    warnings: List[str] = field(default_factory=list)
    #: True when the user canceled mid-search.
    canceled: bool = False
    #: Dominant genres inferred from the seed library (e.g. techno, pop).
    detected_genres: List[str] = field(default_factory=list)
    #: True when an instrumental genre replaced a ``lyrics`` search ending.
    genre_adapted: bool = False


#: Genres where a ``lyrics`` search ending usually finds the wrong results.
_INSTRUMENTAL_GENRES = frozenset(
    {
        "techno",
        "house",
        "trance",
        "drum and bass",
        "dubstep",
        "edm",
        "ambient",
        "hardstyle",
        "hardcore",
        "garage",
        "jungle",
        "breakbeat",
        "electro",
        "minimal",
        "synthwave",
        "industrial",
    }
)

#: Canonical genre -> marker phrases (longer phrases first).
_GENRE_MARKERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("drum and bass", ("drum and bass", "drum & bass", "dnb", "liquid dnb")),
    ("techno", ("hard techno", "industrial techno", "detroit techno", "schranz", "techno")),
    ("house", ("deep house", "tech house", "afro house", "progressive house", "house music", "house")),
    ("trance", ("psytrance", "progressive trance", "uplifting trance", "trance")),
    ("dubstep", ("dubstep", "riddim")),
    ("hardstyle", ("hardstyle", "rawstyle")),
    ("hardcore", ("gabber", "frenchcore", "hardcore")),
    ("edm", ("big room", "future rave", "edm")),
    ("ambient", ("ambient", "downtempo", "chillout")),
    ("synthwave", ("synthwave", "retrowave", "outrun")),
    ("electro", ("electro house", "electro")),
    ("minimal", ("minimal techno", "minimal")),
    ("jungle", ("jungle")),
    ("garage", ("uk garage", "garage")),
    ("breakbeat", ("breakbeat", "breaks")),
    ("industrial", ("industrial")),
    ("hip hop", ("hip hop", "hip-hop", "rap music", "trap music", "rap")),
    ("r&b", ("r&b", "rnb", "rhythm and blues")),
    ("pop", ("synth pop", "synthpop", "indie pop", "k-pop", "kpop", "pop music", "pop")),
    ("rock", ("indie rock", "alternative rock", "hard rock", "punk rock", "rock")),
    ("metal", ("death metal", "black metal", "heavy metal", "metalcore", "metal")),
    ("punk", ("punk rock", "punk")),
    ("jazz", ("jazz")),
    ("blues", ("blues")),
    ("classical", ("classical", "orchestra", "symphony")),
    ("country", ("country music", "country")),
    ("reggae", ("reggae", "dancehall")),
    ("soul", ("soul music", "soul")),
    ("funk", ("funk")),
    ("disco", ("disco")),
    ("folk", ("folk music", "indie folk", "folk")),
    ("latin", ("reggaeton", "latin pop", "salsa", "latin")),
)


def _genre_pattern_hits(text: str, patterns: Sequence[str]) -> bool:
    """Return whether ``text`` contains any genre marker as a whole token/phrase."""
    lower = " {0} ".format((text or "").lower())
    for pattern in patterns:
        needle = pattern.lower()
        if " " in needle or "&" in needle or "-" in needle:
            if needle in lower:
                return True
            continue
        if re.search(r"(?<![a-z0-9]){0}(?![a-z0-9])".format(re.escape(needle)), lower):
            return True
    return False


def detect_genres_in_text(text: str) -> List[str]:
    """Return canonical genre names found in a single title / path string."""
    hits: List[str] = []
    for genre, patterns in _GENRE_MARKERS:
        if _genre_pattern_hits(text, patterns):
            hits.append(genre)
    return hits


def infer_library_genres(
    seeds: Sequence[HistoryEntry],
    *,
    limit: int = 3,
) -> List[str]:
    """Infer dominant genres from seed titles, names and folder paths.

    :param seeds: Discover seed entries (history or folder scans).
    :param limit: Maximum genres to return, strongest first.
    :return: Canonical genre labels such as ``techno`` or ``pop``.
    """
    scores: Dict[str, int] = {}
    for entry in seeds:
        blobs = [
            entry.title or "",
            entry.name or "",
            entry.path or "",
        ]
        # Folder names often encode the genre (…/Techno/track.mp3).
        if entry.path:
            try:
                blobs.extend(Path(entry.path).parts[-3:])
            except Exception:
                pass
        for blob in blobs:
            for genre in detect_genres_in_text(blob):
                # Folder/path hits weigh a bit more than a single title token.
                weight = 3 if entry.path and genre in (entry.path or "").lower() else 1
                scores[genre] = scores.get(genre, 0) + weight
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _score in ranked[: max(1, int(limit))] if _score > 0]


def _effective_search_settings(
    config_suffix: str,
    require_suffix: bool,
    genres: Sequence[str],
) -> Tuple[str, bool, bool]:
    """Adapt lyrics-oriented settings when the library is instrumental.

    :return: ``(suffix, require_suffix, genre_adapted)``.
    """
    ending = _normalize_suffix(config_suffix)
    primary = genres[0] if genres else ""
    if primary and primary in _INSTRUMENTAL_GENRES and ending.lower() == "lyrics":
        # "lyrics" pulls karaoke/pop lyric videos; use the genre instead.
        return primary, False, True
    return ending, bool(require_suffix), False


def _search_query(title: str, suffix: str, genres: Sequence[str] = ()) -> str:
    """Build the YouTube search string for one seed title."""
    clean = re.sub(r"\s+", " ", (title or "").strip())
    # Drop a trailing extension leftover from file names.
    clean = re.sub(r"\.(mp3|mp4|m4a|webm|mkv)$", "", clean, flags=re.IGNORECASE).strip()
    ending = _normalize_suffix(suffix)
    parts = [clean]
    if ending and ending.lower() not in clean.lower():
        parts.append(ending)
    for genre in genres[:2]:
        if genre and genre.lower() not in clean.lower() and genre.lower() != ending.lower():
            parts.append(genre)
            break
    return " ".join(part for part in parts if part).strip()


def _genre_relevance(track: DiscoverTrack, genres: Sequence[str]) -> int:
    """Score how well ``track`` matches the inferred genres (higher is better)."""
    if not genres:
        return 0
    blob = "{0} {1}".format(track.title, track.uploader)
    score = 0
    for index, genre in enumerate(genres):
        weight = max(1, 3 - index)
        patterns = dict(_GENRE_MARKERS).get(genre, (genre,))
        if _genre_pattern_hits(blob, patterns):
            score += 10 * weight
    return score


def _sort_by_genre(tracks: List[DiscoverTrack], genres: Sequence[str]) -> List[DiscoverTrack]:
    """Stable-sort tracks so genre matches float to the front."""
    if not genres or not tracks:
        return tracks
    decorated = list(enumerate(tracks))
    decorated.sort(key=lambda item: (-_genre_relevance(item[1], genres), item[0]))
    return [track for _index, track in decorated]


class DiscoverExtractError(RuntimeError):
    """Raised when a single YouTube search / related lookup fails."""

    def __init__(self, message: str, *, kind: str = "generic") -> None:
        super().__init__(message)
        self.kind = kind


def seed_entries(entries: Sequence[HistoryEntry]) -> List[HistoryEntry]:
    """Return finished downloads that can seed a Discover search.

    :param entries: The full download history.
    :return: Successful entries that still have a title or URL.
    """
    seeds: List[HistoryEntry] = []
    seen: Set[str] = set()
    for entry in entries:
        if entry.status != STATUS_OK:
            continue
        key = (entry.url or entry.title or "").strip().lower()
        if not key or key in seen:
            continue
        if not entry.title.strip() and not entry.url.strip():
            continue
        seen.add(key)
        seeds.append(entry)
    return seeds


def title_from_media_filename(name: str) -> str:
    """Turn a media file name into a YouTube search title.

    Strips the extension and an optional trailing ``[videoId]`` marker that
    yt-dlp sometimes writes into the file name.

    :param name: File name or stem.
    :return: Cleaned title text, or empty when nothing usable remains.
    """
    stem = Path(name).stem.strip()
    if not stem:
        return ""
    stem = _ID_IN_NAME_RE.sub("", stem).strip()
    stem = re.sub(r"\s+", " ", stem)
    return stem


def video_id_from_media_filename(name: str) -> str:
    """Return an 11-character YouTube id embedded in ``name``, if any."""
    match = _ID_IN_NAME_RE.search(Path(name).stem.strip())
    return match.group(1) if match else ""


def seed_entries_from_folder(
    folder: Path,
    *,
    limit: int = _MAX_FOLDER_SEEDS,
    max_depth: int = _DISK_SCAN_MAX_DEPTH,
    max_visited: int = _DISK_SCAN_MAX_VISITED,
) -> List[HistoryEntry]:
    """Build synthetic seed entries from audio/video files under ``folder``.

    Walks nested directories with the same depth / visit caps as the bounded
    disk scan. Hidden and system-ish subdirectories are skipped. Newer files
    are preferred.

    :param folder: Directory that holds songs the user likes (e.g. Downloads).
    :param limit: Maximum number of seeds to return.
    :param max_depth: Maximum directory depth relative to ``folder`` (0 = root only).
    :param max_visited: Abort after this many files/dirs inspected.
    :return: History-shaped seeds usable by :func:`discover_tracks`.
    """
    root = Path(folder).expanduser()
    if not root.is_dir():
        return []
    return seed_entries_from_disk(
        roots=[root],
        limit=limit,
        max_depth=max_depth,
        max_visited=max_visited,
    )


def _seeds_from_media_files(
    files: Sequence[Path],
    *,
    limit: int,
    seen: Optional[Set[str]] = None,
) -> List[HistoryEntry]:
    """Turn media paths into deduplicated HistoryEntry seeds."""
    seeds: List[HistoryEntry] = []
    used = seen if seen is not None else set()
    for path in files:
        title = title_from_media_filename(path.name)
        if not title:
            continue
        key = title.lower()
        if key in used:
            continue
        used.add(key)
        video_id = video_id_from_media_filename(path.name)
        url = "https://www.youtube.com/watch?v={0}".format(video_id) if video_id else ""
        seeds.append(
            HistoryEntry(
                name=path.name,
                path=str(path),
                title=title,
                url=url,
                status=STATUS_OK,
            )
        )
        if len(seeds) >= max(1, int(limit)):
            break
    return seeds


def _disk_scan_should_skip_dir(path: Path) -> bool:
    """Return whether ``path`` should be skipped during a bounded disk scan."""
    name = path.name
    if not name or name in (".", ".."):
        return True
    # Hidden directories (``.cache``, …) — keep the walk away from huge trees.
    if name.startswith("."):
        return True
    lower = name.lower()
    if lower in _DISK_SCAN_SKIP_NAMES:
        return True
    # Absolute system roots that must never be walked.
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    lowered = resolved.lower()
    for banned in ("/proc", "/sys", "/dev", "/run"):
        if lowered == banned or lowered.startswith(banned + os.sep):
            return True
    if "\\windows\\" in lowered or lowered.endswith("\\windows"):
        return True
    if "$recycle.bin" in lowered or "\\recycler\\" in lowered:
        return True
    return False


def default_disk_scan_roots(
    download_dir: Optional[Path] = None,
) -> List[Path]:
    """Return common music / download locations for a bounded disk scan.

    Scope (intentionally narrow — never the whole filesystem):

    * the configured Clipster download directory
    * the OS Music folder (XDG / Windows Known Folder / ``~/Music`` when present)
    * ``~/Downloads`` when distinct from the download dir

    Callers must still apply depth / file caps via :func:`seed_entries_from_disk`.
    """
    roots: List[Path] = []
    seen: Set[str] = set()

    def add(candidate: Optional[Path]) -> None:
        if candidate is None:
            return
        try:
            path = Path(candidate).expanduser().resolve()
        except OSError:
            path = Path(candidate).expanduser()
        if not path.is_dir():
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    add(download_dir)
    add(paths.default_music_dir())
    add(paths.default_download_dir())
    add(Path.home() / "Music")
    return roots


def seed_entries_from_disk(
    *,
    roots: Optional[Sequence[Path]] = None,
    download_dir: Optional[Path] = None,
    limit: int = _MAX_FOLDER_SEEDS,
    max_depth: int = _DISK_SCAN_MAX_DEPTH,
    max_visited: int = _DISK_SCAN_MAX_VISITED,
    seen_titles: Optional[Set[str]] = None,
) -> List[HistoryEntry]:
    """Collect media seeds from a few common folders with hard bounds.

    Walks only ``roots`` (see :func:`default_disk_scan_roots`), skips system and
    hidden directories, stops at ``max_depth`` and ``max_visited`` entries, and
    returns at most ``limit`` seeds (newest files preferred).

    Safe to call from a worker thread — it only touches the local filesystem.

    :param roots: Explicit roots for tests; defaults to common music/download dirs.
    :param download_dir: Included in the default root list when ``roots`` is omitted.
    :param limit: Maximum seeds to return.
    :param max_depth: Maximum directory depth relative to each root (0 = root only).
    :param max_visited: Abort after this many files/dirs inspected across all roots.
    :param seen_titles: Titles already collected by earlier seed stages (skipped).
    :return: History-shaped seeds usable by :func:`discover_tracks`.
    """
    walk_roots = list(roots) if roots is not None else default_disk_scan_roots(download_dir)
    if not walk_roots:
        return []
    depth_limit = max(0, int(max_depth))
    visit_limit = max(1, int(max_visited))
    visited = 0
    found: List[Tuple[float, Path]] = []
    for root in walk_roots:
        if visited >= visit_limit:
            break
        root_path = Path(root).expanduser()
        if not root_path.is_dir() or _disk_scan_should_skip_dir(root_path):
            continue
        stack: List[Tuple[Path, int]] = [(root_path, 0)]
        while stack and visited < visit_limit:
            current, depth = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError as exc:
                log.debug("Discover disk scan skipped %s: %s", current, exc)
                continue
            for entry in entries:
                if visited >= visit_limit:
                    break
                visited += 1
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if depth >= depth_limit:
                            continue
                        if _disk_scan_should_skip_dir(entry):
                            continue
                        stack.append((entry, depth + 1))
                        continue
                    if not entry.is_file():
                        continue
                    if entry.suffix.lower() not in _MEDIA_EXTENSIONS:
                        continue
                    mtime = entry.stat().st_mtime if entry.exists() else 0.0
                    found.append((mtime, entry))
                except OSError:
                    continue
    found.sort(key=lambda item: item[0], reverse=True)
    return _seeds_from_media_files(
        [path for _mtime, path in found],
        limit=limit,
        seen=seen_titles,
    )


def _merge_seed_lists(*groups: Sequence[HistoryEntry]) -> List[HistoryEntry]:
    """Concatenate seed groups, keeping first occurrence of each title/url key."""
    merged: List[HistoryEntry] = []
    seen: Set[str] = set()
    for group in groups:
        for entry in group:
            key = (entry.url or entry.title or entry.name or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def resolve_discover_seeds(
    history_entries: Sequence[HistoryEntry],
    download_dir: Path,
    *,
    liked_entries: Optional[Sequence[HistoryEntry]] = None,
    min_folder_seeds: int = 5,
    disk_scan_enabled: bool = True,
    disk_scan_roots: Optional[Sequence[Path]] = None,
    max_disk_seeds: int = _MAX_FOLDER_SEEDS,
) -> Tuple[List[HistoryEntry], str]:
    """Pick Discover seeds in priority order until enough titles exist.

    Collection order (stop early once ``min_folder_seeds`` are available):

    1. Clipster download history (finished entries)
    2. Liked tracks from Discover taste
    3. Media files in the configured download directory (bounded recursive scan)
    4. Bounded disk scan of common Music / Downloads locations

    Steps 3–4 run only when earlier stages still fall short of the threshold.
    Disk scanning is intended for a worker thread (see
    :func:`seed_entries_from_disk`).

    :param history_entries: Current download list.
    :param download_dir: Configured download folder.
    :param liked_entries: Optional thumbs-up seeds from :class:`DiscoverTaste`.
    :param min_folder_seeds: Stop collecting once this many seeds exist.
    :param disk_scan_enabled: When ``False``, skip stage 4 entirely.
    :param disk_scan_roots: Optional override roots for tests.
    :param max_disk_seeds: Cap for stage-4 seeds.
    :return: ``(seeds, source)`` where ``source`` is ``history``, ``likes``,
        ``download_dir``, ``disk``, or ``none``.
    """
    threshold = max(1, int(min_folder_seeds))
    history_seeds = seed_entries(history_entries)
    like_seeds = list(liked_entries or ())
    seeds = _merge_seed_lists(history_seeds, like_seeds)
    if len(seeds) >= threshold:
        if history_seeds and like_seeds:
            source = "history"
        elif like_seeds and not history_seeds:
            source = "likes"
        else:
            source = "history"
        return seeds, source

    folder_seeds = seed_entries_from_folder(download_dir)
    seeds = _merge_seed_lists(seeds, folder_seeds)
    if len(seeds) >= threshold:
        return seeds, "download_dir"

    if disk_scan_enabled:
        seen_titles = {
            (entry.title or entry.name or "").strip().lower()
            for entry in seeds
            if (entry.title or entry.name or "").strip()
        }
        disk_seeds = seed_entries_from_disk(
            roots=disk_scan_roots,
            download_dir=download_dir,
            limit=max(1, int(max_disk_seeds)),
            seen_titles=seen_titles,
        )
        seeds = _merge_seed_lists(seeds, disk_seeds)
        if seeds:
            return seeds, ("disk" if disk_seeds else ("download_dir" if folder_seeds else "history"))

    if seeds:
        if folder_seeds:
            return seeds, "download_dir"
        if like_seeds and not history_seeds:
            return seeds, "likes"
        return seeds, "history"
    return [], "none"


def _normalize_suffix(suffix: str) -> str:
    """Return a cleaned suffix without a leading asterisk or spaces."""
    return suffix.strip().lstrip("*").strip()


def _title_matches_suffix(title: str, suffix: str, required: bool) -> bool:
    """Return whether ``title`` satisfies the optional suffix rule."""
    needle = _normalize_suffix(suffix)
    if not required or not needle:
        return True
    return needle.lower() in (title or "").lower()


def _video_id_of(url: str, info: Optional[Dict[str, Any]] = None) -> str:
    """Return a video id from metadata or from the URL."""
    if info:
        candidate = info.get("id")
        if isinstance(candidate, str) and len(candidate) == 11:
            return candidate
    extracted = extract_youtube_url(url or "")
    if extracted:
        return extracted.rsplit("v=", 1)[-1]
    return ""


def _track_from_info(
    info: Dict[str, Any],
    *,
    seed_title: str,
    suffix: str,
    require_suffix: bool,
) -> Optional[DiscoverTrack]:
    """Build a :class:`DiscoverTrack` from one yt-dlp info dict, or ``None``."""
    title = str(info.get("title") or "").strip()
    if not title or not _title_matches_suffix(title, suffix, require_suffix):
        return None
    url = str(info.get("webpage_url") or info.get("url") or "").strip()
    if not url.startswith("http"):
        video_id = str(info.get("id") or "")
        if len(video_id) == 11:
            url = "https://www.youtube.com/watch?v={0}".format(video_id)
    canonical = extract_youtube_url(url)
    if not canonical:
        return None
    video_id = _video_id_of(canonical, info)
    duration = info.get("duration")
    duration_i = int(duration) if isinstance(duration, (int, float)) else 0
    thumb = ""
    raw_thumb = info.get("thumbnail")
    if isinstance(raw_thumb, str) and raw_thumb.startswith("http"):
        thumb = raw_thumb
    thumbnails = info.get("thumbnails")
    if not thumb and isinstance(thumbnails, list) and thumbnails:
        last = thumbnails[-1]
        if isinstance(last, dict) and isinstance(last.get("url"), str):
            thumb = str(last["url"])
    return DiscoverTrack(
        url=canonical,
        video_id=video_id,
        title=title,
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        duration=duration_i,
        thumbnail=thumb,
        seed_title=seed_title,
    )


def _extract_flat(query: str, options: Dict[str, Any], *, allow_partial: bool = False) -> List[Dict[str, Any]]:
    """Run a flat yt-dlp extraction and return entry dicts.

    :param query: yt-dlp URL or ``ytsearchN:...`` query.
    :param options: Base yt-dlp options.
    :param allow_partial: When ``True``, tolerate missing entries (related mixes).
    :raises DiscoverExtractError: When YouTube returns nothing usable / blocks.
    """
    youtube_dl = _import_yt_dlp()
    opts = dict(options)
    opts["skip_download"] = True
    opts["quiet"] = True
    opts["no_warnings"] = True
    opts["extract_flat"] = "in_playlist"
    opts["ignoreerrors"] = bool(allow_partial)
    opts["noplaylist"] = False
    try:
        with youtube_dl(opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        raise DiscoverExtractError(message, kind=classify_error(message)) from exc
    if not isinstance(info, dict):
        raise DiscoverExtractError("YouTube returned no data", kind="generic")
    entries = info.get("entries")
    if isinstance(entries, list):
        rows = [entry for entry in entries if isinstance(entry, dict)]
        if rows:
            return rows
        if allow_partial:
            return []
        raise DiscoverExtractError("YouTube returned an empty result list", kind="generic")
    return [info]


def search_tracks(
    query: str,
    base_options: Optional[Dict[str, Any]] = None,
    *,
    limit: int = 12,
) -> List[DiscoverTrack]:
    """Search YouTube for ``query`` and return the results as tracks.

    A plain search, unlike :func:`discover_tracks`: no seeds, no genre sorting
    and no suffix filter - whoever typed the term meant exactly that term.

    :param query: What the user typed.
    :param base_options: yt-dlp options already prepared by the downloader.
    :param limit: Maximum number of results.
    :return: The results in the order YouTube gave them.
    :raises DiscoverExtractError: When YouTube returns nothing usable.
    """
    text = " ".join(str(query or "").split())
    if not text:
        return []
    count = max(1, min(int(limit), 25))
    rows = _extract_flat("ytsearch{0}:{1}".format(count, text), dict(base_options or {}))
    tracks: List[DiscoverTrack] = []
    seen: Set[str] = set()
    for row in rows:
        track = _track_from_info(row, seed_title="", suffix="", require_suffix=False)
        if track is None or track.video_id in seen:
            continue
        seen.add(track.video_id)
        tracks.append(track)
        if len(tracks) >= count:
            break
    return tracks


def discover_tracks(
    seeds: Sequence[HistoryEntry],
    config: Config,
    *,
    mode: Optional[str] = None,
    base_options: Optional[Dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress: Optional[DiscoverProgress] = None,
    on_batch: Optional[DiscoverBatch] = None,
    exclude_ids: Optional[Set[str]] = None,
    limit: Optional[int] = None,
) -> DiscoverOutcome:
    """Return related tracks for ``seeds`` according to the Discover settings.

    :param seeds: Finished history entries used as starting points.
    :param config: Live user configuration.
    :param mode: Override for :attr:`Config.discover_mode`.
    :param base_options: yt-dlp options already prepared by the downloader.
    :param cancel_check: Optional callback; when it returns ``True`` the search stops.
    :param progress: Optional UI callback for per-seed progress.
    :param on_batch: Optional UI callback with newly found tracks after each seed.
    :param exclude_ids: Video ids already in the playlist (skipped).
    :param limit: Optional cap for this run; defaults to :attr:`Config.discover_max_results`.
    :return: Tracks plus status details for the Discover page.
    """
    chosen_mode = (mode or config.discover_mode or MODE_SEARCH).strip().lower()
    if chosen_mode not in DISCOVER_MODES:
        chosen_mode = MODE_SEARCH
    genres = infer_library_genres(seeds)
    suffix, require_suffix, genre_adapted = _effective_search_settings(
        config.discover_search_suffix,
        bool(config.discover_require_suffix),
        genres,
    )
    # Similarity providers already return themed titles; a forced "lyrics"
    # suffix often filters away good matches.
    if chosen_mode in (MODE_DEEZER, MODE_LISTENBRAINZ) and require_suffix:
        require_suffix = False
    per_seed = max(1, int(config.discover_results_per_seed))
    max_results = max(1, int(limit if limit is not None else config.discover_max_results))
    options = dict(base_options or {})

    found: List[DiscoverTrack] = []
    alternates: List[DiscoverTrack] = []
    seen_ids: Set[str] = {item for item in (exclude_ids or set()) if item}
    alt_ids: Set[str] = set(seen_ids)
    seed_ids = {_video_id_of(entry.url) for entry in seeds if entry.url}
    seed_ids.discard("")
    seen_ids |= seed_ids

    outcome = DiscoverOutcome(detected_genres=list(genres), genre_adapted=genre_adapted)
    consecutive_blocks = 0
    seed_list = list(seeds)
    # Extra genre-only searches keep the queue on-topic (techno → techno, …).
    genre_queries = ["{0} mix".format(genre) for genre in genres[:2]]
    total = len(seed_list) + len(genre_queries)

    def _emit_batch(new_tracks: List[DiscoverTrack]) -> None:
        if on_batch is None or not new_tracks:
            return
        try:
            on_batch(list(new_tracks))
        except Exception:  # pragma: no cover - UI callback must not abort search
            log.debug("Discover batch callback failed", exc_info=True)

    def _consume_batch(batch: List[Dict[str, Any]], seed_title: str) -> None:
        nonlocal found
        outcome.raw_hits += len(batch)
        before = len(found)
        added = 0
        for info in batch:
            strict = _track_from_info(
                info, seed_title=seed_title, suffix=suffix, require_suffix=require_suffix
            )
            relaxed = None
            if require_suffix and strict is None:
                relaxed = _track_from_info(
                    info, seed_title=seed_title, suffix=suffix, require_suffix=False
                )
            track = strict
            if track is None:
                if (
                    relaxed is not None
                    and relaxed.video_id not in alt_ids
                    and relaxed.video_id not in seed_ids
                    and not any(titles_similar(relaxed.title, existing.title) for existing in alternates)
                    and not any(titles_similar(relaxed.title, existing.title) for existing in found)
                ):
                    alt_ids.add(relaxed.video_id)
                    alternates.append(relaxed)
                continue
            if track.video_id in seen_ids or track.video_id in seed_ids:
                continue
            if any(titles_similar(track.title, existing.title) for existing in found):
                continue
            seen_ids.add(track.video_id)
            found.append(track)
            added += 1
            if added >= per_seed or len(found) >= max_results:
                break
        _emit_batch(found[before:])

    for index, seed in enumerate(seed_list, start=1):
        if cancel_check is not None and cancel_check():
            outcome.canceled = True
            break
        if len(found) >= max_results:
            break
        seed_title = seed.title.strip() or seed.name.strip() or seed.url
        outcome.seeds_tried += 1
        if progress is not None:
            try:
                progress(index, total, seed_title)
            except Exception:  # pragma: no cover - UI callback must not abort search
                log.debug("Discover progress callback failed", exc_info=True)

        batch: List[Dict[str, Any]] = []
        try:
            if chosen_mode == MODE_RELATED:
                video_id = _video_id_of(seed.url)
                if video_id:
                    mix = "https://www.youtube.com/watch?v={0}&list=RD{0}".format(video_id)
                    batch = _extract_flat(mix, options, allow_partial=True)
                if len(batch) <= 1:
                    query = _search_query(seed_title, suffix, genres)
                    if query:
                        search_opts = dict(options)
                        search_opts["noplaylist"] = True
                        batch = _extract_flat(
                            "ytsearch{0}:{1}".format(per_seed * 2, query),
                            search_opts,
                            allow_partial=False,
                        )
            elif chosen_mode in (MODE_DEEZER, MODE_LISTENBRAINZ):
                suggestions = similar_queries(chosen_mode, seed_title, limit=max(per_seed, 6))
                if not suggestions:
                    outcome.warnings.append(
                        "{0}: no similar songs from {1}".format(seed_title, chosen_mode)
                    )
                    # Fall back to plain title search so the queue still fills.
                    query = _search_query(seed_title, suffix, genres)
                    if query:
                        search_opts = dict(options)
                        search_opts["noplaylist"] = True
                        batch = _extract_flat(
                            "ytsearch{0}:{1}".format(per_seed, query),
                            search_opts,
                            allow_partial=False,
                        )
                else:
                    search_opts = dict(options)
                    search_opts["noplaylist"] = True
                    for suggestion in suggestions:
                        if len(found) >= max_results:
                            break
                        query = _search_query(suggestion, suffix if require_suffix else "", genres)
                        if not query:
                            continue
                        try:
                            hits = _extract_flat(
                                "ytsearch1:{0}".format(query),
                                search_opts,
                                allow_partial=False,
                            )
                        except DiscoverExtractError:
                            raise
                        except Exception as exc:
                            log.debug("Similarity ytsearch failed for %r: %s", query, exc)
                            continue
                        _consume_batch(hits, seed_title)
                    consecutive_blocks = 0
                    continue
            else:
                query = _search_query(seed_title, suffix, genres)
                if not query:
                    continue
                search_opts = dict(options)
                search_opts["noplaylist"] = True
                batch = _extract_flat(
                    "ytsearch{0}:{1}".format(per_seed, query),
                    search_opts,
                    allow_partial=False,
                )
            consecutive_blocks = 0
        except DiscoverExtractError as exc:
            log.warning("Discover search failed for '%s': %s", seed_title, exc)
            outcome.warnings.append("{0}: {1}".format(seed_title, str(exc)))
            if not outcome.error_summary:
                outcome.error_summary = str(exc)
            if exc.kind == "bot":
                outcome.blocked = True
                consecutive_blocks += 1
                if consecutive_blocks >= _MAX_CONSECUTIVE_BLOCKS:
                    break
            continue
        except Exception as exc:
            log.warning("Discover search failed for '%s': %s", seed_title, exc)
            message = str(exc).strip() or type(exc).__name__
            outcome.warnings.append("{0}: {1}".format(seed_title, message))
            if not outcome.error_summary:
                outcome.error_summary = message
            if classify_error(message) == "bot":
                outcome.blocked = True
                consecutive_blocks += 1
                if consecutive_blocks >= _MAX_CONSECUTIVE_BLOCKS:
                    break
            continue

        _consume_batch(batch, seed_title)

    for offset, genre_query in enumerate(genre_queries, start=1):
        if outcome.canceled or len(found) >= max_results:
            break
        if cancel_check is not None and cancel_check():
            outcome.canceled = True
            break
        if progress is not None:
            try:
                progress(len(seed_list) + offset, total, genre_query)
            except Exception:  # pragma: no cover
                log.debug("Discover progress callback failed", exc_info=True)
        try:
            search_opts = dict(options)
            search_opts["noplaylist"] = True
            batch = _extract_flat(
                "ytsearch{0}:{1}".format(max(per_seed, 8), genre_query),
                search_opts,
                allow_partial=False,
            )
            consecutive_blocks = 0
        except Exception as exc:
            log.warning("Discover genre search failed for '%s': %s", genre_query, exc)
            message = str(exc).strip() or type(exc).__name__
            outcome.warnings.append("{0}: {1}".format(genre_query, message))
            if classify_error(message) == "bot":
                outcome.blocked = True
            continue
        _consume_batch(batch, genre_query)

    if not found and alternates:
        found = alternates[:max_results]
        outcome.suffix_relaxed = True
        _emit_batch(found)

    found = _sort_by_genre(dedupe_tracks(found), genres)
    outcome.tracks = found[:max_results]
    log.info(
        "Discover (%s) returned %s tracks from %s seeds genres=%s (raw=%s blocked=%s relaxed=%s)",
        chosen_mode,
        len(outcome.tracks),
        outcome.seeds_tried,
        ",".join(genres) or "-",
        outcome.raw_hits,
        outcome.blocked,
        outcome.suffix_relaxed,
    )
    return outcome
