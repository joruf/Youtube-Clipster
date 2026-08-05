"""Unit tests for Streaming thumbs-up / thumbs-down taste."""

from __future__ import annotations

from pathlib import Path

from clipster.discover import DiscoverTrack, titles_similar
from clipster.discover_taste import (
    VOTE_DOWN,
    VOTE_UP,
    DiscoverTaste,
)
from clipster.history import HistoryEntry, STATUS_OK


def _track(video_id: str, title: str, uploader: str = "Chan") -> DiscoverTrack:
    return DiscoverTrack(
        url="https://www.youtube.com/watch?v={0}".format(video_id),
        video_id=video_id,
        title=title,
        uploader=uploader,
    )


def test_titles_similar_ignores_lyrics_boilerplate() -> None:
    assert titles_similar("Hello World lyrics", "Hello World Official Video")
    assert not titles_similar("Hello World", "Goodbye Moon")


def test_like_and_dislike_persist(tmp_path: Path) -> None:
    path = tmp_path / "taste.json"
    taste = DiscoverTaste(path=path).load()
    liked = _track("aaaaaaaaaaa", "Song A lyrics")
    hated = _track("bbbbbbbbbbb", "Song B lyrics")
    taste.like(liked)
    taste.dislike(hated)
    assert taste.vote_for("aaaaaaaaaaa") == VOTE_UP
    assert taste.vote_for("bbbbbbbbbbb") == VOTE_DOWN

    again = DiscoverTaste(path=path).load()
    assert again.vote_for("aaaaaaaaaaa") == VOTE_UP
    assert "bbbbbbbbbbb" in again.excluded_ids()
    seeds = again.liked_seeds()
    assert len(seeds) == 1
    assert seeds[0].title == "Song A lyrics"


def test_filter_tracks_blocks_id_and_similar_title(tmp_path: Path) -> None:
    taste = DiscoverTaste(path=tmp_path / "taste.json").load()
    taste.dislike(_track("bbbbbbbbbbb", "Neon Dreams lyrics"))
    kept = taste.filter_tracks(
        [
            _track("bbbbbbbbbbb", "Neon Dreams lyrics"),
            _track("ccccccccccc", "Neon Dreams Official Audio"),
            _track("ddddddddddd", "Other Song lyrics"),
        ]
    )
    assert [track.video_id for track in kept] == ["ddddddddddd"]


def test_merge_seeds_prefers_liked_and_skips_disliked(tmp_path: Path) -> None:
    taste = DiscoverTaste(path=tmp_path / "taste.json").load()
    taste.like(_track("aaaaaaaaaaa", "Liked Song"))
    taste.dislike(_track("bbbbbbbbbbb", "Bad Song"))
    seeds = [
        HistoryEntry(title="Bad Song lyrics", url="https://www.youtube.com/watch?v=bbbbbbbbbbb", status=STATUS_OK),
        HistoryEntry(title="Folder Song", url="", status=STATUS_OK),
    ]
    merged = taste.merge_seeds(seeds)
    titles = [entry.title for entry in merged]
    assert titles[0] == "Liked Song"
    assert "Bad Song lyrics" not in titles
    assert "Folder Song" in titles


def test_replacing_vote_updates_side(tmp_path: Path) -> None:
    taste = DiscoverTaste(path=tmp_path / "taste.json").load()
    track = _track("aaaaaaaaaaa", "Flip Me")
    taste.like(track)
    taste.dislike(track)
    assert taste.vote_for("aaaaaaaaaaa") == VOTE_DOWN
    assert taste.liked_seeds() == []


def test_clear_vote_removes_entry(tmp_path: Path) -> None:
    path = tmp_path / "taste.json"
    taste = DiscoverTaste(path=path).load()
    track = _track("aaaaaaaaaaa", "Toggle Me")
    taste.like(track)
    assert taste.clear_vote("aaaaaaaaaaa") is True
    assert taste.vote_for("aaaaaaaaaaa") is None
    again = DiscoverTaste(path=path).load()
    assert again.vote_for("aaaaaaaaaaa") is None
    assert again.clear_vote("missingxxxxx") is False
