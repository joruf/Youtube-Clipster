"""The downloads that are already on disk, as something the player can play.

Clipster downloads *and* plays; these tests pin down that the two halves meet.
What is on disk has to reach the queue with its title and length intact, and it
has to play from the file instead of being resolved against YouTube again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipster.discover import DiscoverTrack
from clipster.history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, HistoryEntry
from clipster.library import library_tracks
from clipster.player import local_source

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _file(folder: Path, name: str, *, mtime: float = 0.0) -> Path:
    """Create a file and optionally date it, returning the path."""
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    if mtime:
        import os

        os.utime(target, (mtime, mtime))
    return target


# ----------------------------------------------------------------------
# What lands in the library
# ----------------------------------------------------------------------
def test_a_finished_download_keeps_its_title_length_and_link(downloads: Path) -> None:
    target = _file(downloads, "song.mp3")
    entry = HistoryEntry(name="song.mp3", path=str(target), title="A Song", url=URL,
                         duration=213, status=STATUS_OK)
    track = library_tracks(downloads, [entry])[0]
    assert track.title == "A Song"
    assert track.duration == 213
    assert track.path == str(target)
    assert track.video_id == "dQw4w9WgXcQ", "likes and find-similar still need the id"
    assert track.is_local


def test_a_file_the_list_never_saw_is_still_playable(downloads: Path) -> None:
    """Copied in by hand, or downloaded before the list was kept."""
    _file(downloads, "stray.mp3")
    tracks = library_tracks(downloads, [])
    assert [track.title for track in tracks] == ["stray"]
    assert tracks[0].video_id == ""


def test_a_download_is_not_listed_twice(downloads: Path) -> None:
    target = _file(downloads, "song.mp3")
    entry = HistoryEntry(name="song.mp3", path=str(target), title="A Song",
                         status=STATUS_OK)
    tracks = library_tracks(downloads, [entry])
    assert len(tracks) == 1, "the folder scan must not repeat what the list has"


def test_a_deleted_file_is_not_offered(downloads: Path) -> None:
    entry = HistoryEntry(name="gone.mp3", path=str(downloads / "gone.mp3"),
                         title="Gone", status=STATUS_OK)
    assert library_tracks(downloads, [entry]) == []


@pytest.mark.parametrize("status", [STATUS_FAILED, STATUS_CANCELED])
def test_an_attempt_that_never_finished_lends_no_title(downloads: Path, status: str) -> None:
    """Whatever is on disk plays - but a row that failed says nothing about it."""
    target = _file(downloads, "half.mp3")
    entry = HistoryEntry(name="half.mp3", path=str(target), title="Should Not Be Used",
                         duration=999, status=status)
    tracks = library_tracks(downloads, [entry])
    assert [(track.title, track.duration) for track in tracks] == [("half", 0)]


def test_only_media_files_count(downloads: Path) -> None:
    _file(downloads, "notes.txt")
    _file(downloads, "cover.jpg")
    _file(downloads, "song.opus")
    assert [track.title for track in library_tracks(downloads, [])] == ["song"]


def test_sub_folders_are_searched(downloads: Path) -> None:
    _file(downloads, "albums/best of/track.m4a")
    assert [track.title for track in library_tracks(downloads, [])] == ["track"]


def test_the_newest_file_comes_first(downloads: Path) -> None:
    _file(downloads, "old.mp3", mtime=1_000_000)
    _file(downloads, "new.mp3", mtime=2_000_000)
    assert [track.title for track in library_tracks(downloads, [])] == ["new", "old"]


def test_the_download_list_keeps_its_own_order_in_front(downloads: Path) -> None:
    """List entries carry titles and lengths, so they lead - newest first."""
    known = _file(downloads, "known.mp3", mtime=1_000_000)
    _file(downloads, "stray.mp3", mtime=2_000_000)
    entry = HistoryEntry(name="known.mp3", path=str(known), title="Known", status=STATUS_OK)
    assert [track.title for track in library_tracks(downloads, [entry])] == ["Known", "stray"]


def test_the_list_is_bounded(downloads: Path) -> None:
    for index in range(12):
        _file(downloads, "track{0}.mp3".format(index))
    assert len(library_tracks(downloads, [], limit=5)) == 5


def test_a_folder_that_is_not_there_yields_nothing(tmp_path: Path) -> None:
    assert library_tracks(tmp_path / "nope", []) == []


# ----------------------------------------------------------------------
# How such a track is played
# ----------------------------------------------------------------------
def test_a_local_track_plays_from_the_file(downloads: Path) -> None:
    target = _file(downloads, "song.mp3")
    track = DiscoverTrack(url=URL, video_id="dQw4w9WgXcQ", title="A Song", path=str(target))
    assert local_source(track) == str(target)


def test_a_track_without_a_file_goes_the_online_way(downloads: Path) -> None:
    online = DiscoverTrack(url=URL, video_id="dQw4w9WgXcQ", title="A Song")
    assert local_source(online) is None


def test_a_file_that_vanished_falls_back_to_the_stream(downloads: Path) -> None:
    """The track still plays - it is only resolved against YouTube again."""
    track = DiscoverTrack(url=URL, video_id="dQw4w9WgXcQ", title="A Song",
                          path=str(downloads / "deleted.mp3"))
    assert local_source(track) is None


def test_a_local_track_is_never_prefetched(downloads: Path, monkeypatch) -> None:
    """Warming up a stream for a file that is already there is pure waste."""
    from clipster import player as module

    target = _file(downloads, "song.mp3")
    instance = module.DiscoverPlayer()
    instance.set_playlist([DiscoverTrack(url=URL, video_id="x", title="A", path=str(target))])
    calls: list = []
    monkeypatch.setattr(module, "resolve_stream_url", lambda *a, **k: calls.append(a))
    instance.prefetch(0)
    assert calls == []


# ----------------------------------------------------------------------
# Surviving a restart
# ----------------------------------------------------------------------
def test_a_local_track_survives_the_saved_queue(tmp_path: Path, downloads: Path) -> None:
    from clipster.discover_queue import DiscoverQueueStore

    target = _file(downloads, "song.mp3")
    store = DiscoverQueueStore(path=tmp_path / "queue.json")
    store.save([DiscoverTrack(url="", video_id="", title="song", path=str(target))], 0)
    tracks, index = DiscoverQueueStore(path=tmp_path / "queue.json").load()
    assert index == 0
    assert [(track.title, track.path) for track in tracks] == [("song", str(target))]
