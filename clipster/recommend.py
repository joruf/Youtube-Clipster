"""Free music-similarity helpers for Discover search modes.

Providers return suggested ``Artist - Title`` queries.  Clipster then resolves
them on YouTube with yt-dlp.  No paid accounts are required:

* **Deezer** — public JSON API (search + related artists + top tracks)
* **ListenBrainz** — Labs similar-artists (via MusicBrainz artist id), then
  Deezer tops for those artists
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .logging_setup import get_logger

log = get_logger(__name__)

#: Polite identity for MusicBrainz / ListenBrainz / Deezer.
_USER_AGENT = "YoutubeClipster/1.0 (https://github.com/joruf/youtube-clipster)"
_DEEZER = "https://api.deezer.com"
_MUSICBRAINZ = "https://musicbrainz.org/ws/2"
_LB_LABS = "https://labs.api.listenbrainz.org"
#: Stable-enough ListenBrainz similar-artists algorithm string.
_LB_SIMILAR_ARTISTS_ALGO = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_"
    "limit_100_filter_True_skip_30"
)

PROVIDER_DEEZER = "deezer"
PROVIDER_LISTENBRAINZ = "listenbrainz"


def split_artist_title(seed_title: str) -> Tuple[str, str]:
    """Best-effort split of ``Artist - Title`` (or return empty artist)."""
    text = re.sub(r"\s+", " ", (seed_title or "").strip())
    text = re.sub(r"\.(mp3|mp4|m4a|webm|mkv)$", "", text, flags=re.IGNORECASE).strip()
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    for sep in (" - ", " – ", " — ", " ~ "):
        if sep in text:
            left, right = text.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return "", text


def _http_json(url: str, *, timeout: float = 20.0) -> Any:
    """GET ``url`` and decode JSON."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed API hosts
        raw = response.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _deezer_search_track(query: str) -> Optional[Dict[str, Any]]:
    """Return the first Deezer track hit for ``query``, or ``None``."""
    clean = (query or "").strip()
    if not clean:
        return None
    url = "{0}/search?q={1}&limit=1".format(_DEEZER, urllib.parse.quote(clean))
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("Deezer search failed for %r: %s", clean, exc)
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    return first if isinstance(first, dict) else None


def _deezer_related_artists(artist_id: int, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Return related Deezer artists for ``artist_id``."""
    url = "{0}/artist/{1}/related?limit={2}".format(_DEEZER, int(artist_id), max(1, limit))
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("Deezer related artists failed: %s", exc)
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _deezer_artist_top(artist_id: int, *, limit: int = 3) -> List[Dict[str, Any]]:
    """Return top tracks for a Deezer artist."""
    url = "{0}/artist/{1}/top?limit={2}".format(_DEEZER, int(artist_id), max(1, limit))
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("Deezer artist top failed: %s", exc)
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _format_track(artist: str, title: str) -> str:
    """Return a YouTube-friendly ``Artist - Title`` query."""
    artist = re.sub(r"\s+", " ", (artist or "").strip())
    title = re.sub(r"\s+", " ", (title or "").strip())
    if artist and title:
        return "{0} - {1}".format(artist, title)
    return artist or title


def _queries_from_deezer_tracks(tracks: Sequence[Dict[str, Any]]) -> List[str]:
    """Map Deezer track dicts to unique query strings."""
    seen: set[str] = set()
    out: List[str] = []
    for track in tracks:
        title = str(track.get("title") or "").strip()
        artist_obj = track.get("artist") if isinstance(track.get("artist"), dict) else {}
        artist = str(artist_obj.get("name") or "").strip()
        query = _format_track(artist, title)
        key = query.lower()
        if not query or key in seen:
            continue
        seen.add(key)
        out.append(query)
    return out


def deezer_similar_queries(seed_title: str, *, limit: int = 8) -> List[str]:
    """Suggest thematically related songs via Deezer related artists.

    :param seed_title: Seed song title (optionally ``Artist - Title``).
    :param limit: Maximum number of suggestions.
    :return: Query strings for YouTube search.
    """
    limit = max(1, int(limit))
    hit = _deezer_search_track(seed_title)
    if hit is None:
        artist, title = split_artist_title(seed_title)
        if artist and title:
            hit = _deezer_search_track("{0} {1}".format(artist, title))
    if hit is None:
        return []

    artist_obj = hit.get("artist") if isinstance(hit.get("artist"), dict) else {}
    try:
        artist_id = int(artist_obj.get("id") or 0)
    except (TypeError, ValueError):
        artist_id = 0
    if artist_id <= 0:
        return []

    related = _deezer_related_artists(artist_id, limit=max(4, min(10, limit)))
    collected: List[Dict[str, Any]] = []
    per_artist = 2 if limit >= 6 else 1
    for item in related:
        try:
            related_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if related_id <= 0:
            continue
        collected.extend(_deezer_artist_top(related_id, limit=per_artist))
        if len(collected) >= limit * 2:
            break
    queries = _queries_from_deezer_tracks(collected)
    # Drop exact seed match when possible.
    seed_key = _format_track(
        str(artist_obj.get("name") or ""),
        str(hit.get("title") or ""),
    ).lower()
    queries = [q for q in queries if q.lower() != seed_key]
    return queries[:limit]


def _musicbrainz_artist_mbid(artist: str, title: str = "") -> Optional[str]:
    """Resolve a MusicBrainz artist MBID from names."""
    if artist.strip():
        query = 'artist:"{0}"'.format(artist.replace('"', ""))
    elif title.strip():
        query = 'recording:"{0}"'.format(title.replace('"', ""))
    else:
        return None
    url = "{0}/artist/?query={1}&fmt=json&limit=1".format(
        _MUSICBRAINZ, urllib.parse.quote(query)
    )
    try:
        payload = _http_json(url, timeout=25.0)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("MusicBrainz artist lookup failed: %s", exc)
        return None
    artists = payload.get("artists") if isinstance(payload, dict) else None
    if not isinstance(artists, list) or not artists:
        # Fall back: search recording then take artist-credit.
        if not title.strip() and artist.strip():
            return None
        rec_q = 'recording:"{0}"'.format((title or artist).replace('"', ""))
        if artist.strip() and title.strip():
            rec_q = 'recording:"{0}" AND artist:"{1}"'.format(
                title.replace('"', ""), artist.replace('"', "")
            )
        rec_url = "{0}/recording/?query={1}&fmt=json&limit=1".format(
            _MUSICBRAINZ, urllib.parse.quote(rec_q)
        )
        try:
            rec_payload = _http_json(rec_url, timeout=25.0)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return None
        recordings = rec_payload.get("recordings") if isinstance(rec_payload, dict) else None
        if not isinstance(recordings, list) or not recordings:
            return None
        credits = recordings[0].get("artist-credit") if isinstance(recordings[0], dict) else None
        if isinstance(credits, list) and credits:
            artist_block = credits[0].get("artist") if isinstance(credits[0], dict) else None
            if isinstance(artist_block, dict) and artist_block.get("id"):
                return str(artist_block["id"])
        return None
    first = artists[0]
    if isinstance(first, dict) and first.get("id"):
        return str(first["id"])
    return None


def _listenbrainz_similar_artist_names(artist_mbid: str, *, limit: int = 8) -> List[str]:
    """Return similar artist names from ListenBrainz Labs."""
    url = "{0}/similar-artists/json?artist_mbids={1}&algorithm={2}".format(
        _LB_LABS,
        urllib.parse.quote(artist_mbid),
        urllib.parse.quote(_LB_SIMILAR_ARTISTS_ALGO),
    )
    try:
        payload = _http_json(url, timeout=30.0)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("ListenBrainz similar artists failed: %s", exc)
        return []
    if not isinstance(payload, list):
        return []
    names: List[str] = []
    seen: set[str] = set()
    ranked = sorted(
        (item for item in payload if isinstance(item, dict)),
        key=lambda item: float(item.get("score") or 0.0),
        reverse=True,
    )
    for item in ranked:
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        if str(item.get("artist_mbid") or "") == artist_mbid:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def listenbrainz_similar_queries(seed_title: str, *, limit: int = 8) -> List[str]:
    """Suggest songs via ListenBrainz similar artists + Deezer top tracks.

    :param seed_title: Seed song title.
    :param limit: Maximum number of suggestions.
    :return: Query strings for YouTube search.
    """
    limit = max(1, int(limit))
    artist, title = split_artist_title(seed_title)
    if not artist:
        # Prefer Deezer metadata to recover an artist name.
        hit = _deezer_search_track(seed_title)
        if hit is not None:
            artist_obj = hit.get("artist") if isinstance(hit.get("artist"), dict) else {}
            artist = str(artist_obj.get("name") or "").strip()
            if not title:
                title = str(hit.get("title") or "").strip()
    mbid = _musicbrainz_artist_mbid(artist, title)
    if not mbid:
        return []
    names = _listenbrainz_similar_artist_names(mbid, limit=max(4, min(12, limit)))
    collected: List[Dict[str, Any]] = []
    for name in names:
        hit = _deezer_search_track(name)
        if hit is None:
            continue
        artist_obj = hit.get("artist") if isinstance(hit.get("artist"), dict) else {}
        try:
            artist_id = int(artist_obj.get("id") or 0)
        except (TypeError, ValueError):
            artist_id = 0
        if artist_id <= 0:
            # Use the hit itself as a soft suggestion.
            collected.append(hit)
            continue
        collected.extend(_deezer_artist_top(artist_id, limit=2))
        if len(collected) >= limit * 2:
            break
    return _queries_from_deezer_tracks(collected)[:limit]


def similar_queries(provider: str, seed_title: str, *, limit: int = 8) -> List[str]:
    """Dispatch to a free similarity provider.

    :param provider: ``deezer`` or ``listenbrainz``.
    :param seed_title: Seed song title.
    :param limit: Maximum suggestions.
    :return: Query strings (may be empty when the provider finds nothing).
    """
    key = (provider or "").strip().lower()
    if key == PROVIDER_DEEZER:
        return deezer_similar_queries(seed_title, limit=limit)
    if key == PROVIDER_LISTENBRAINZ:
        return listenbrainz_similar_queries(seed_title, limit=limit)
    return []
