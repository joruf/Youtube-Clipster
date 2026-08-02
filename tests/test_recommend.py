"""Unit tests for free similarity providers used by Discover."""

from __future__ import annotations

from typing import Any, Dict, List

from clipster.recommend import (
    deezer_similar_queries,
    listenbrainz_similar_queries,
    similar_queries,
    split_artist_title,
)


def test_split_artist_title_common_forms() -> None:
    assert split_artist_title("Daft Punk - One More Time") == ("Daft Punk", "One More Time")
    assert split_artist_title("Just A Title") == ("", "Just A Title")


def test_deezer_similar_queries_builds_artist_titles(monkeypatch) -> None:
    calls: List[str] = []

    def fake_json(url: str, *, timeout: float = 20.0) -> Any:
        del timeout
        calls.append(url)
        if "/search?" in url:
            return {
                "data": [
                    {
                        "id": 1,
                        "title": "One More Time",
                        "artist": {"id": 27, "name": "Daft Punk"},
                    }
                ]
            }
        if "/related" in url:
            return {
                "data": [
                    {"id": 100, "name": "Justice"},
                    {"id": 101, "name": "Cassius"},
                ]
            }
        if "/top" in url:
            if "/100/" in url:
                return {
                    "data": [
                        {"title": "D.A.N.C.E.", "artist": {"id": 100, "name": "Justice"}},
                        {"title": "Genesis", "artist": {"id": 100, "name": "Justice"}},
                    ]
                }
            return {
                "data": [
                    {"title": "Cassius 1999", "artist": {"id": 101, "name": "Cassius"}},
                ]
            }
        return {"data": []}

    monkeypatch.setattr("clipster.recommend._http_json", fake_json)
    queries = deezer_similar_queries("Daft Punk - One More Time", limit=5)
    assert "Justice - D.A.N.C.E." in queries
    assert "Cassius - Cassius 1999" in queries
    assert any("/search?" in url for url in calls)


def test_listenbrainz_similar_queries_uses_labs(monkeypatch) -> None:
    def fake_json(url: str, *, timeout: float = 20.0) -> Any:
        del timeout
        if "musicbrainz.org" in url and "/artist/" in url:
            return {"artists": [{"id": "mbid-daft", "name": "Daft Punk"}]}
        if "similar-artists" in url:
            return [
                {"artist_mbid": "mbid-nile", "name": "Nile Rodgers", "score": 100},
                {"artist_mbid": "mbid-daft", "name": "Daft Punk", "score": 1},
            ]
        if "/search?" in url:
            return {"data": [{"title": "x", "artist": {"id": 55, "name": "Nile Rodgers"}}]}
        if "/top" in url:
            return {
                "data": [
                    {"title": "Good Times", "artist": {"id": 55, "name": "Nile Rodgers"}},
                ]
            }
        return {}

    monkeypatch.setattr("clipster.recommend._http_json", fake_json)
    queries = listenbrainz_similar_queries("Daft Punk - One More Time", limit=3)
    assert queries == ["Nile Rodgers - Good Times"]


def test_similar_queries_dispatcher(monkeypatch) -> None:
    monkeypatch.setattr("clipster.recommend.deezer_similar_queries", lambda *_a, **_k: ["A - B"])
    monkeypatch.setattr("clipster.recommend.listenbrainz_similar_queries", lambda *_a, **_k: ["C - D"])
    assert similar_queries("deezer", "seed") == ["A - B"]
    assert similar_queries("listenbrainz", "seed") == ["C - D"]
    assert similar_queries("unknown", "seed") == []


def test_discover_deezer_mode_searches_suggestions(monkeypatch) -> None:
    from clipster.config import Config
    from clipster.discover import MODE_DEEZER, discover_tracks
    from clipster.history import STATUS_OK, HistoryEntry

    extracts: List[str] = []

    def fake_extract(query: str, options: Dict[str, Any], *, allow_partial: bool = False) -> List[Dict[str, Any]]:
        del options, allow_partial
        extracts.append(query)
        video_id = "abcdefghijk" if "Justice" in query else "lmnopqrstuv"
        return [
            {
                "id": video_id,
                "title": "{0} lyrics".format(query.split(":", 1)[-1]),
                "webpage_url": "https://www.youtube.com/watch?v={0}".format(video_id),
            }
        ]

    monkeypatch.setattr(
        "clipster.discover.similar_queries",
        lambda provider, title, limit=8: ["Justice - D.A.N.C.E.", "Cassius - 1999"]
        if provider == MODE_DEEZER
        else [],
    )
    monkeypatch.setattr("clipster.discover._extract_flat", fake_extract)
    config = Config()
    config.discover_require_suffix = True
    config.discover_search_suffix = "lyrics"
    seeds = [
        HistoryEntry(
            title="Daft Punk - One More Time",
            url="https://www.youtube.com/watch?v=zzzzzzzzzzz",
            status=STATUS_OK,
        )
    ]
    outcome = discover_tracks(seeds, config, mode=MODE_DEEZER, limit=5)
    assert outcome.tracks
    assert any(item.startswith("ytsearch1:") for item in extracts)
