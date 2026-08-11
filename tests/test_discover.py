"""Unit tests for Discover search helpers."""

from __future__ import annotations

from pathlib import Path

from typing import Any, Dict, List

from clipster.config import Config
from clipster.discover import (
    MODE_RELATED,
    MODE_SEARCH,
    DiscoverExtractError,
    DiscoverTrack,
    _effective_search_settings,
    _search_query,
    _title_matches_suffix,
    _track_from_info,
    dedupe_tracks,
    detect_genres_in_text,
    discover_tracks,
    infer_library_genres,
    resolve_discover_seeds,
    seed_entries,
    seed_entries_from_disk,
    seed_entries_from_folder,
    title_from_media_filename,
    titles_similar,
    video_id_from_media_filename,
)
from clipster.history import STATUS_FAILED, STATUS_OK, HistoryEntry


def test_seed_entries_keep_successful_downloads_only() -> None:
    entries = [
        HistoryEntry(title="Song A", url="https://www.youtube.com/watch?v=aaaaaaaaaaa", status=STATUS_OK),
        HistoryEntry(title="Broken", url="https://www.youtube.com/watch?v=bbbbbbbbbbb", status=STATUS_FAILED),
        HistoryEntry(title="Song A", url="https://www.youtube.com/watch?v=aaaaaaaaaaa", status=STATUS_OK),
    ]
    seeds = seed_entries(entries)
    assert len(seeds) == 1
    assert seeds[0].title == "Song A"


def test_search_query_appends_suffix_once() -> None:
    assert _search_query("Hello World", "lyrics") == "Hello World lyrics"
    assert _search_query("Hello lyrics", "lyrics") == "Hello lyrics"
    assert _search_query("track.mp3", "lyrics") == "track lyrics"
    assert _search_query("Amelie Lens", "techno", ["techno"]) == "Amelie Lens techno"
    assert "techno" in _search_query("Random Track", "mix", ["techno"])


def test_infer_library_genres_from_titles_and_folders() -> None:
    seeds = [
        HistoryEntry(title="Amelie Lens - Live @ Awakenings", path="/music/Techno/amelie.mp3", status=STATUS_OK),
        HistoryEntry(title="Hard Techno Mix 2024", path="/music/Techno/mix.mp3", status=STATUS_OK),
        HistoryEntry(title="Some Pop Hit lyrics", path="/music/Pop/hit.mp3", status=STATUS_OK),
    ]
    genres = infer_library_genres(seeds)
    assert genres[0] == "techno"
    assert detect_genres_in_text("deep house set") == ["house"]
    suffix, require, adapted = _effective_search_settings("lyrics", True, ["techno"])
    assert suffix == "techno"
    assert require is False
    assert adapted is True


def test_title_suffix_filter() -> None:
    assert _title_matches_suffix("Song Lyrics", "lyrics", True)
    assert not _title_matches_suffix("Song Official", "lyrics", True)
    assert _title_matches_suffix("Song Official", "lyrics", False)


def test_track_from_info_builds_canonical_url() -> None:
    track = _track_from_info(
        {
            "id": "abcdefghijk",
            "title": "Demo Song lyrics",
            "uploader": "Channel",
            "duration": 120,
            "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk&list=RDx",
        },
        seed_title="Demo",
        suffix="lyrics",
        require_suffix=True,
    )
    assert isinstance(track, DiscoverTrack)
    assert track.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert track.video_id == "abcdefghijk"


def test_track_from_info_rejects_wrong_suffix() -> None:
    track = _track_from_info(
        {"id": "abcdefghijk", "title": "Demo Song", "webpage_url": "https://youtu.be/abcdefghijk"},
        seed_title="Demo",
        suffix="lyrics",
        require_suffix=True,
    )
    assert track is None


def test_discover_defaults() -> None:
    config = Config()
    assert config.discover_search_suffix == "lyrics"
    assert config.discover_require_suffix is True
    assert config.discover_mode == MODE_RELATED
    assert config.discover_min_folder_seeds == 5
    assert config.discover_disk_scan_enabled is True


def test_dedupe_tracks_collapses_lyrics_variants() -> None:
    def make(video_id: str, title: str) -> DiscoverTrack:
        return DiscoverTrack(
            url="https://www.youtube.com/watch?v={0}".format(video_id),
            video_id=video_id,
            title=title,
        )

    tracks = [
        make("aaaaaaaaaaa", "NOTSOBAD - Grounds for Hope (Lyrics / Visualizer)"),
        make("bbbbbbbbbbb", "NOTSOBAD - Grounds For Hope (lyrics)"),
        make("ccccccccccc", "HD Lyrics Video - Primal Fear: Vote Of No Confidence"),
        make("ddddddddddd", "Michael Jackson - They Don't Care About Us (Lyrics)"),
        make(
            "eeeeeeeeeee",
            "Michael Jackson - They Don't Care About Us (Lyrics Video) | BhaavNagar Lyrics",
        ),
    ]
    assert titles_similar(tracks[0].title, tracks[1].title)
    assert titles_similar(tracks[3].title, tracks[4].title)
    kept = dedupe_tracks(tracks)
    assert [track.video_id for track in kept] == ["aaaaaaaaaaa", "ccccccccccc", "ddddddddddd"]
    extra = make("fffffffff0", "NOTSOBAD - Grounds for Hope lyrics")
    assert dedupe_tracks([extra], against=kept) == []


def test_dedupe_keeps_every_file_even_when_the_names_look_alike() -> None:
    """Two files are two songs - an album would otherwise lose most of itself."""
    album = [
        DiscoverTrack(url="", video_id="", title="Nightfall Part 1", path="/music/a.mp3"),
        DiscoverTrack(url="", video_id="", title="Nightfall Part 2", path="/music/b.mp3"),
        DiscoverTrack(url="", video_id="", title="Nightfall Part 3", path="/music/c.mp3"),
    ]
    assert titles_similar(album[0].title, album[1].title), "the titles really are alike"
    assert len(dedupe_tracks(album)) == 3


def test_the_same_file_twice_is_still_one_song() -> None:
    same = DiscoverTrack(url="", video_id="", title="Song", path="/music/song.mp3")
    other = DiscoverTrack(url="", video_id="", title="Different name", path="/music/song.mp3")
    assert len(dedupe_tracks([same, other])) == 1


def test_title_from_media_filename_strips_id_and_extension() -> None:
    assert title_from_media_filename("Hello World [abcdefghijk].mp3") == "Hello World"
    assert title_from_media_filename("track.mp4") == "track"
    assert video_id_from_media_filename("Hello World [abcdefghijk].mp3") == "abcdefghijk"
    assert video_id_from_media_filename("track.mp3") == ""


def test_seed_entries_from_folder_use_media_names(tmp_path: Path) -> None:
    (tmp_path / "Song One.mp3").write_bytes(b"x")
    (tmp_path / "Song Two [zzzzzzzzzzz].m4a").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.mp3").write_bytes(b"x")
    seeds = seed_entries_from_folder(tmp_path)
    titles = {entry.title for entry in seeds}
    assert titles == {"Song One", "Song Two", "deep"}
    by_title = {entry.title: entry for entry in seeds}
    assert by_title["Song Two"].url.endswith("zzzzzzzzzzz")


def test_seed_entries_from_folder_is_bounded_and_skips_junk(tmp_path: Path) -> None:
    (tmp_path / "Keep.mp3").write_bytes(b"x")
    nested = tmp_path / "albums" / "one"
    nested.mkdir(parents=True)
    (nested / "Nested Track.mp3").write_bytes(b"x")
    junk = tmp_path / "node_modules"
    junk.mkdir()
    (junk / "secret.mp3").write_bytes(b"x")
    hidden = tmp_path / ".cache"
    hidden.mkdir()
    (hidden / "hidden.mp3").write_bytes(b"x")
    deep = tmp_path
    for level in range(6):
        deep = deep / "lvl{0}".format(level)
        deep.mkdir()
        (deep / "track{0}.mp3".format(level)).write_bytes(b"x")

    seeds = seed_entries_from_folder(tmp_path, max_depth=1, max_visited=200)
    titles = {entry.title for entry in seeds}
    assert "Keep" in titles
    assert "Nested Track" not in titles  # depth 2 under albums/one
    assert "track0" in titles
    assert "track1" not in titles
    assert "secret" not in titles
    assert "hidden" not in titles

    capped = seed_entries_from_folder(tmp_path, limit=2, max_depth=5, max_visited=500)
    assert len(capped) <= 2


def test_resolve_discover_seeds_history_and_likes_skip_folder_and_disk(tmp_path: Path) -> None:
    history = [
        HistoryEntry(title="History {0}".format(i), url="https://www.youtube.com/watch?v=aaaaaaaaaa{0}".format(i), status=STATUS_OK)
        for i in range(3)
    ]
    likes = [
        HistoryEntry(title="Liked {0}".format(i), url="https://www.youtube.com/watch?v=bbbbbbbbbb{0}".format(i), status=STATUS_OK)
        for i in range(2)
    ]
    for index in range(8):
        (tmp_path / "Folder {0}.mp3".format(index)).write_bytes(b"x")
    extra = tmp_path / "extra_music"
    extra.mkdir()
    (extra / "Disk Only.mp3").write_bytes(b"x")

    seeds, source = resolve_discover_seeds(
        history,
        tmp_path,
        liked_entries=likes,
        min_folder_seeds=5,
        disk_scan_enabled=True,
        disk_scan_roots=[extra],
    )
    assert source == "history"
    titles = {entry.title for entry in seeds}
    assert "History 0" in titles
    assert "Liked 0" in titles
    assert "Folder 0" not in titles
    assert "Disk Only" not in titles
    assert len(seeds) >= 5


def test_resolve_discover_seeds_uses_download_dir_when_sparse(tmp_path: Path) -> None:
    history = [
        HistoryEntry(title="Only One", url="https://www.youtube.com/watch?v=aaaaaaaaaaa", status=STATUS_OK),
    ]
    (tmp_path / "Folder Song.mp3").write_bytes(b"x")
    nested = tmp_path / "subfolder"
    nested.mkdir()
    for index in range(4):
        (nested / "More {0}.mp3".format(index)).write_bytes(b"x")

    seeds, source = resolve_discover_seeds(
        history,
        tmp_path,
        liked_entries=[],
        min_folder_seeds=5,
        disk_scan_enabled=False,
    )
    assert source == "download_dir"
    titles = {entry.title for entry in seeds}
    assert "Only One" in titles
    assert "Folder Song" in titles
    assert "More 0" in titles
    assert len(seeds) >= 5


def test_resolve_discover_seeds_falls_back_to_disk_scan(tmp_path: Path) -> None:
    download = tmp_path / "downloads"
    download.mkdir()
    music = tmp_path / "music"
    music.mkdir()
    nested = music / "albums" / "one"
    nested.mkdir(parents=True)
    (nested / "Deep Track.mp3").write_bytes(b"x")
    (music / "Top Track.flac").write_bytes(b"x")

    seeds, source = resolve_discover_seeds(
        [],
        download,
        liked_entries=[],
        min_folder_seeds=5,
        disk_scan_enabled=True,
        disk_scan_roots=[music],
    )
    assert source == "disk"
    titles = {entry.title for entry in seeds}
    assert "Deep Track" in titles
    assert "Top Track" in titles


def test_resolve_discover_seeds_disk_disabled_stays_sparse(tmp_path: Path) -> None:
    (tmp_path / "Only.mp3").write_bytes(b"x")
    seeds, source = resolve_discover_seeds(
        [],
        tmp_path,
        liked_entries=[],
        min_folder_seeds=5,
        disk_scan_enabled=False,
    )
    assert source == "download_dir"
    assert len(seeds) == 1


def test_seed_entries_from_disk_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    deep = root
    for level in range(6):
        deep = deep / "lvl{0}".format(level)
        deep.mkdir()
        (deep / "track{0}.mp3".format(level)).write_bytes(b"x")
    # max_depth=1: root (0) + lvl0 (1); deeper directories are not entered.
    seeds = seed_entries_from_disk(roots=[root], limit=40, max_depth=1, max_visited=50)
    titles = {entry.title for entry in seeds}
    assert "track0" in titles
    assert "track1" not in titles
    assert "track5" not in titles

    capped = seed_entries_from_disk(roots=[root], limit=2, max_depth=5, max_visited=500)
    assert len(capped) <= 2


def test_seed_entries_from_disk_skips_systemish_names(tmp_path: Path) -> None:
    root = tmp_path / "scan"
    root.mkdir()
    (root / "Keep.mp3").write_bytes(b"x")
    bad = root / "node_modules"
    bad.mkdir()
    (bad / "secret.mp3").write_bytes(b"x")
    hidden = root / ".cache"
    hidden.mkdir()
    (hidden / "hidden.mp3").write_bytes(b"x")
    seeds = seed_entries_from_disk(roots=[root], limit=10, max_depth=3)
    titles = {entry.title for entry in seeds}
    assert titles == {"Keep"}


def test_resolve_discover_seeds_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    seeds, source = resolve_discover_seeds(
        [],
        empty,
        liked_entries=[],
        min_folder_seeds=5,
        disk_scan_enabled=True,
        disk_scan_roots=[empty],
    )
    assert source == "none"
    assert seeds == []


def test_discover_tracks_relaxes_suffix_when_nothing_matches(monkeypatch) -> None:
    def fake_extract(query: str, options: Dict[str, Any], *, allow_partial: bool = False) -> List[Dict[str, Any]]:
        return [
            {
                "id": "abcdefghijk",
                "title": "Demo Song Official Video",
                "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
            }
        ]

    monkeypatch.setattr("clipster.discover._extract_flat", fake_extract)
    config = Config()
    config.discover_require_suffix = True
    config.discover_search_suffix = "lyrics"
    seeds = [HistoryEntry(title="Demo Song", url="https://www.youtube.com/watch?v=zzzzzzzzzzz", status=STATUS_OK)]
    outcome = discover_tracks(seeds, config, mode=MODE_SEARCH)
    assert outcome.suffix_relaxed is True
    assert len(outcome.tracks) == 1
    assert outcome.tracks[0].title == "Demo Song Official Video"
    assert outcome.raw_hits == 1


def test_discover_tracks_emits_batches_per_seed(monkeypatch) -> None:
    calls = []

    def fake_extract(query: str, options: Dict[str, Any], *, allow_partial: bool = False) -> List[Dict[str, Any]]:
        if "aaaaaaaaaaa" in query or "Seed A" in query:
            return [
                {
                    "id": "abcdefghijk",
                    "title": "First Hit lyrics",
                    "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
                }
            ]
        return [
            {
                "id": "lmnopqrstuv",
                "title": "Second Hit lyrics",
                "webpage_url": "https://www.youtube.com/watch?v=lmnopqrstuv",
            }
        ]

    monkeypatch.setattr("clipster.discover._extract_flat", fake_extract)
    config = Config()
    config.discover_require_suffix = True
    config.discover_results_per_seed = 2
    config.discover_max_results = 10
    seeds = [
        HistoryEntry(title="Seed A", url="https://www.youtube.com/watch?v=aaaaaaaaaaa", status=STATUS_OK),
        HistoryEntry(title="Seed B", url="https://www.youtube.com/watch?v=bbbbbbbbbbb", status=STATUS_OK),
    ]
    outcome = discover_tracks(
        seeds,
        config,
        mode=MODE_SEARCH,
        on_batch=lambda tracks: calls.append([track.video_id for track in tracks]),
    )
    assert len(calls) >= 2
    assert calls[0] == ["abcdefghijk"]
    assert "lmnopqrstuv" in calls[1]
    assert {track.video_id for track in outcome.tracks} == {"abcdefghijk", "lmnopqrstuv"}
    def fake_extract(query: str, options: Dict[str, Any], *, allow_partial: bool = False) -> List[Dict[str, Any]]:
        return [
            {
                "id": "abcdefghijk",
                "title": "Demo Song lyrics",
                "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
            },
            {
                "id": "lmnopqrstuv",
                "title": "Other Song lyrics",
                "webpage_url": "https://www.youtube.com/watch?v=lmnopqrstuv",
            },
        ]

    monkeypatch.setattr("clipster.discover._extract_flat", fake_extract)
    config = Config()
    config.discover_require_suffix = True
    seeds = [HistoryEntry(title="Seed", url="https://www.youtube.com/watch?v=zzzzzzzzzzz", status=STATUS_OK)]
    outcome = discover_tracks(seeds, config, mode=MODE_SEARCH, exclude_ids={"abcdefghijk"}, limit=5)
    assert [track.video_id for track in outcome.tracks] == ["lmnopqrstuv"]


def test_seed_from_track_copies_title_and_url() -> None:
    from clipster.discover import seed_from_track

    track = DiscoverTrack(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        video_id="abcdefghijk",
        title="Hello",
    )
    seed = seed_from_track(track)
    assert seed.title == "Hello"
    assert seed.url.endswith("abcdefghijk")


def test_discover_tracks_reports_bot_blocks(monkeypatch) -> None:
    def fake_extract(query: str, options: Dict[str, Any], *, allow_partial: bool = False) -> List[Dict[str, Any]]:
        raise DiscoverExtractError("Sign in to confirm you're not a bot", kind="bot")

    monkeypatch.setattr("clipster.discover._extract_flat", fake_extract)
    config = Config()
    seeds = [
        HistoryEntry(title="A", url="https://www.youtube.com/watch?v=aaaaaaaaaaa", status=STATUS_OK),
        HistoryEntry(title="B", url="https://www.youtube.com/watch?v=bbbbbbbbbbb", status=STATUS_OK),
    ]
    seen = []
    outcome = discover_tracks(
        seeds,
        config,
        mode=MODE_SEARCH,
        progress=lambda current, total, title: seen.append((current, total, title)),
    )
    assert outcome.blocked is True
    assert outcome.tracks == []
    assert len(seen) >= 1


def test_queue_title_fit_line_adds_ellipsis() -> None:
    from clipster.discover_page import _fit_line

    class FakeFont:
        def measure(self, text: str) -> int:
            return len(text) * 10

    font = FakeFont()
    assert _fit_line("short", 200, font) == "short"
    fitted = _fit_line("a very long title that will not fit", 80, font)
    assert fitted.endswith("…")
    assert font.measure(fitted) <= 80
