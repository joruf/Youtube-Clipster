"""Persisted Streaming queue round-trips."""

from __future__ import annotations

from pathlib import Path

from clipster.discover import DiscoverTrack
from clipster.discover_queue import DiscoverQueueStore


def _track(n: int) -> DiscoverTrack:
    vid = "aaaaaaaaaa{0}".format(n)
    return DiscoverTrack(
        url="https://www.youtube.com/watch?v={0}".format(vid),
        video_id=vid,
        title="Song {0}".format(n),
        uploader="Artist {0}".format(n),
        duration=120 + n,
        seed_title="seed {0}".format(n),
    )


def test_queue_round_trip(tmp_path: Path) -> None:
    store = DiscoverQueueStore(path=tmp_path / "discover_queue.json")
    tracks = [_track(0), _track(1), _track(2)]
    store.save(tracks, index=1)
    loaded, index = store.load()
    assert index == 1
    assert [t.video_id for t in loaded] == [t.video_id for t in tracks]
    assert loaded[1].title == "Song 1"
    assert loaded[1].uploader == "Artist 1"
    assert loaded[1].duration == 121


def test_empty_queue_saves_cleanly(tmp_path: Path) -> None:
    store = DiscoverQueueStore(path=tmp_path / "discover_queue.json")
    store.save([], index=5)
    tracks, index = store.load()
    assert tracks == []
    assert index == -1


def test_broken_file_yields_empty(tmp_path: Path) -> None:
    path = tmp_path / "discover_queue.json"
    path.write_text("{not json", encoding="utf-8")
    store = DiscoverQueueStore(path=path)
    assert store.load() == ([], -1)


def test_index_is_clamped(tmp_path: Path) -> None:
    store = DiscoverQueueStore(path=tmp_path / "discover_queue.json")
    store.save([_track(0)], index=99)
    _, index = store.load()
    assert index == 0
