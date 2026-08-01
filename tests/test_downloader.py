"""Link recognition, error classification, audio tracks and ffmpeg progress."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from clipster.config import Config
from clipster.downloader import (
    AudioTrack,
    DownloadFailed,
    Downloader,
    Progress,
    VideoInfo,
    _bytes_text,
    _clock,
    _discard,
    _estimated_size,
    _FfmpegProgressWatcher,
    _js_runtime,
    classify_error,
    extract_video_id,
    extract_youtube_url,
    free_space,
)

CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ----------------------------------------------------------------------
# Link recognition
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "http://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=AbCdEfGhIjKl",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/v/dQw4w9WgXcQ",
        "look at this https://youtu.be/dQw4w9WgXcQ isn't it great",
    ],
)
def test_every_link_flavour_becomes_the_canonical_url(text: str) -> None:
    assert extract_youtube_url(text) == CANONICAL


@pytest.mark.parametrize(
    "text",
    [
        # The query order must not matter; putting list/si/app before v used to break it.
        "https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?si=xyz&feature=share&v=dQw4w9WgXcQ",
    ],
)
def test_query_parameter_order_does_not_matter(text: str) -> None:
    assert extract_youtube_url(text) == CANONICAL


@pytest.mark.parametrize(
    "text",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&index=1",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM",
    ],
)
def test_playlist_parameters_are_dropped(text: str) -> None:
    """A video copied out of a playlist must download on its own."""
    assert extract_youtube_url(text) == CANONICAL


def test_the_same_video_in_two_forms_yields_one_url() -> None:
    short = extract_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    long = extract_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RD")
    assert short == long


@pytest.mark.parametrize(
    "text",
    ["https://example.com/watch?v=dQw4w9WgXcQ", "https://vimeo.com/123456789",
     "just some text", "", None],
)
def test_non_youtube_text_is_rejected(text) -> None:
    assert extract_youtube_url(text) is None


def test_video_id_is_extracted() -> None:
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://example.com/v=dQw4w9WgXcQ") is None


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "message,expected",
    [
        ("Sign in to confirm you are not a bot", "bot"),
        ("HTTP Error 429: Too Many Requests", "bot"),
        ("ERROR: Video unavailable", "unavailable"),
        ("This video is not available in your country", "unavailable"),
        ("OSError: [Errno 28] No space left on device", "diskfull"),
        ("ffmpeg: Error writing trailer: No space left on device", "diskfull"),
        ("Disk quota exceeded", "diskfull"),
        ("something else entirely", "generic"),
        ("", "generic"),
    ],
)
def test_error_messages_are_classified(message: str, expected: str) -> None:
    assert classify_error(message) == expected


def test_a_bare_ffmpeg_failure_is_not_called_a_full_disk() -> None:
    """ffmpeg often reports no reason - guessing "disk full" would mislead."""
    assert classify_error("ERROR: Postprocessing: audio conversion failed: Conversion failed!") == "generic"


def test_a_plain_write_error_is_not_called_a_full_disk() -> None:
    """It can just as well be a network problem."""
    assert classify_error("write error") == "generic"


# ----------------------------------------------------------------------
# Audio tracks
# ----------------------------------------------------------------------
def _formats(*pairs) -> dict:
    """Build a yt-dlp style info dict from ``(language, preference)`` pairs."""
    return {"formats": [{"acodec": "opus", "language": lang, "language_preference": pref}
                        for lang, pref in pairs]}


def test_the_original_track_is_listed_first_and_flagged() -> None:
    info = _formats(("en", -1), ("de", 10), ("es", -1))
    tracks = Downloader._audio_tracks(info)
    assert [t.code for t in tracks] == ["de", "en", "es"]
    assert tracks[0].original
    assert not any(t.original for t in tracks[1:])


def test_a_single_track_is_not_called_original() -> None:
    """With nothing to compare against there is no dub to distinguish."""
    assert Downloader._audio_tracks(_formats(("en", 10))) == [AudioTrack("en", False)]


def test_without_preference_data_tracks_stay_alphabetical() -> None:
    tracks = Downloader._audio_tracks(_formats(("en", -1), ("de", -1)))
    assert [(t.code, t.original) for t in tracks] == [("de", False), ("en", False)]


def test_video_only_and_undefined_streams_are_ignored() -> None:
    info = {"formats": [
        {"acodec": "none", "language": "fr", "language_preference": 10},
        {"acodec": "opus", "language": None},
        {"acodec": "opus", "language": "und", "language_preference": -1},
        {"acodec": "opus", "language": "de", "language_preference": -1},
    ]}
    assert [t.code for t in Downloader._audio_tracks(info)] == ["de"]


def test_missing_or_empty_formats_are_survivable() -> None:
    assert Downloader._audio_tracks({}) == []
    assert Downloader._audio_tracks({"formats": []}) == []
    assert Downloader._audio_tracks({"formats": [{"acodec": "none", "language": "de"}]}) == []


def test_video_info_reports_its_original_language() -> None:
    info = VideoInfo(url="u", title="t",
                     audio_tracks=[AudioTrack("de", True), AudioTrack("en", False)])
    assert info.original_language() == "de"
    assert VideoInfo(url="u", title="t").original_language() == ""


# ----------------------------------------------------------------------
# yt-dlp options
# ----------------------------------------------------------------------
def test_two_player_clients_are_requested(config: Config, messages) -> None:
    options = Downloader(config, messages)._base_options()
    assert options["extractor_args"]["youtube"]["player_client"] == ["default", "web_embedded"]


def test_the_javascript_runtime_is_passed_as_a_dict(config: Config, messages) -> None:
    """yt-dlp rejects a list; a wrong shape would break every download."""
    options = Downloader(config, messages)._base_options()
    runtime = _js_runtime()
    if runtime is None:
        assert "js_runtimes" not in options
    else:
        assert options["js_runtimes"] == {runtime: {}}


@pytest.mark.parametrize("media_format", ["mp3", "mp4"])
def test_a_language_adds_a_preferred_branch(config: Config, messages, media_format: str) -> None:
    selector = Downloader(config, messages)._format_selector(media_format, "de")
    assert "[language^=de]" in selector["format"]


def test_without_a_language_no_branch_is_duplicated(config: Config, messages) -> None:
    selector = Downloader(config, messages)._format_selector("mp4", "")
    assert selector["format"].count("bv*[ext=mp4]+ba[ext=m4a]") == 1
    assert "[language^=]" not in selector["format"]


def test_mp3_asks_for_the_audio_postprocessor(config: Config, messages) -> None:
    selector = Downloader(config, messages)._format_selector("mp3", "")
    assert selector["postprocessors"][0]["preferredcodec"] == "mp3"


# ----------------------------------------------------------------------
# Size estimation and the free space guard
# ----------------------------------------------------------------------
def test_the_size_estimate_prefers_the_exact_value() -> None:
    assert _estimated_size({"filesize": 1000, "filesize_approx": 2000}) == 1000
    assert _estimated_size({"filesize_approx": 2000}) == 2000
    assert _estimated_size({"formats": [{"filesize": 10}, {"filesize_approx": 90}]}) == 90
    assert _estimated_size({}) == 0
    assert _estimated_size({"filesize": "big", "formats": []}) == 0


def test_free_space_of_a_missing_path_is_zero() -> None:
    assert free_space(Path("/nope/nowhere")) == 0
    assert free_space(Path.home()) > 0


def test_a_download_that_cannot_fit_is_refused_before_it_starts(config: Config, messages) -> None:
    downloader = Downloader(config, messages)
    with pytest.raises(DownloadFailed) as raised:
        downloader.download(CANONICAL, "mp3", estimated_size=10 ** 15)
    assert raised.value.kind == "diskfull"
    assert "needed" in str(raised.value) and "free" in str(raised.value)


def test_a_small_download_passes_the_guard(config: Config, messages) -> None:
    """Only the guard is under test here, so the URL is deliberately invalid."""
    downloader = Downloader(config, messages)
    with pytest.raises(DownloadFailed) as raised:
        downloader.download("not-a-real-url", "mp3", estimated_size=1024)
    assert raised.value.kind != "diskfull"


def test_a_failed_download_removes_its_leftover(tmp_path: Path) -> None:
    leftover = tmp_path / "source.webm"
    leftover.write_bytes(b"x" * 2048)
    _discard(str(leftover))
    assert not leftover.exists()


def test_discarding_nothing_is_harmless(tmp_path: Path) -> None:
    _discard(None)
    _discard(str(tmp_path / "never-existed.webm"))


# ----------------------------------------------------------------------
# Conversion progress
# ----------------------------------------------------------------------
def test_clock_formatting() -> None:
    assert _clock(0) == "0:00"
    assert _clock(65) == "1:05"
    assert _clock(213) == "3:33"
    assert _clock(7325) == "2:02:05"


def test_byte_formatting() -> None:
    assert _bytes_text(0) == "0 B"
    assert _bytes_text(512) == "512 B"
    assert _bytes_text(150 * 1024 * 1024) == "150.0 MB"


class TestFfmpegProgress:
    """The watcher that turns ffmpeg's -progress file into percentages."""

    def test_the_arguments_point_at_the_progress_file(self, tmp_path: Path) -> None:
        target = tmp_path / "p.txt"
        watcher = _FfmpegProgressWatcher(target, 100, lambda p: None)
        assert watcher.ffmpeg_args() == ["-progress", str(target), "-nostats"]

    def test_not_available_yet_is_ignored(self, tmp_path: Path) -> None:
        """ffmpeg writes N/A before it has produced any output."""
        target = tmp_path / "p.txt"
        target.write_text("out_time_us=N/A\nout_time_ms=N/A\nprogress=continue\n")
        assert _FfmpegProgressWatcher(target, 100, lambda p: None)._read_position() is None

    def test_the_position_is_read_in_seconds(self, tmp_path: Path) -> None:
        """out_time_ms actually carries microseconds - both keys mean the same."""
        target = tmp_path / "p.txt"
        target.write_text("out_time_us=50000000\nout_time_ms=50000000\n")
        assert _FfmpegProgressWatcher(target, 100, lambda p: None)._read_position() == 50.0

    def test_the_newest_block_wins(self, tmp_path: Path) -> None:
        target = tmp_path / "p.txt"
        target.write_text("out_time_us=50000000\nprogress=continue\n"
                          "out_time_us=100000000\nprogress=continue\n")
        assert _FfmpegProgressWatcher(target, 200, lambda p: None)._read_position() == 100.0

    def test_a_missing_file_is_survivable(self, tmp_path: Path) -> None:
        assert _FfmpegProgressWatcher(tmp_path / "gone.txt", 10, lambda p: None)._read_position() is None

    def test_the_detail_line_shows_position_and_length(self, tmp_path: Path) -> None:
        watcher = _FfmpegProgressWatcher(tmp_path / "p.txt", 200, lambda p: None)
        assert watcher._detail(100.0) == "1:40 / 3:20"

    def test_without_a_known_length_no_percentage_is_invented(self, tmp_path: Path) -> None:
        watcher = _FfmpegProgressWatcher(tmp_path / "p.txt", None, lambda p: None)
        assert watcher.duration == 0
        assert watcher._detail(65.0) == "1:05"

    def test_it_emits_a_percentage_while_ffmpeg_writes(self, tmp_path: Path) -> None:
        target = tmp_path / "p.txt"
        seen: list = []
        watcher = _FfmpegProgressWatcher(target, 200, seen.append)
        watcher.INTERVAL = 0.02
        watcher.start("converting")
        try:
            assert seen == [], "start() truncates the file, so nothing is known yet"
            target.write_text("out_time_us=100000000\nprogress=continue\n")
            deadline = time.monotonic() + 3.0
            while not seen and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            watcher.stop()
        assert seen, "the watcher never reported anything"
        last: Progress = seen[-1]
        assert last.phase == "converting"
        assert last.percent == pytest.approx(50.0, abs=0.6)
        assert last.detail == "1:40 / 3:20"

    def test_starting_again_clears_the_previous_run(self, tmp_path: Path) -> None:
        """Two post-processors share one file; the second must start at zero."""
        target = tmp_path / "p.txt"
        target.write_text("out_time_us=999000000\n")
        watcher = _FfmpegProgressWatcher(target, 200, lambda p: None)
        watcher.start("merging")
        try:
            assert watcher._read_position() is None
        finally:
            watcher.stop()

    def test_cleanup_removes_the_file(self, tmp_path: Path) -> None:
        target = tmp_path / "p.txt"
        target.write_text("x")
        watcher = _FfmpegProgressWatcher(target, 10, lambda p: None)
        watcher.cleanup()
        assert not target.exists()

    def test_a_failing_callback_does_not_kill_the_thread(self, tmp_path: Path) -> None:
        target = tmp_path / "p.txt"
        calls: list = []

        def explode(progress: Progress) -> None:
            calls.append(progress)
            raise RuntimeError("the UI blew up")

        watcher = _FfmpegProgressWatcher(target, 200, explode)
        watcher.INTERVAL = 0.02
        watcher.start("converting")
        try:
            target.write_text("out_time_us=100000000\n")
            deadline = time.monotonic() + 3.0
            while len(calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            watcher.stop()
        assert len(calls) >= 2, "the watcher stopped after the first exception"


def test_the_download_signature_carries_duration_and_size() -> None:
    import inspect

    parameters = inspect.signature(Downloader.download).parameters
    assert "duration" in parameters
    assert "estimated_size" in parameters


def test_a_missing_yt_dlp_is_reported_clearly(config: Config, messages, monkeypatch) -> None:
    import clipster.downloader as module

    def refuse() -> None:
        raise module.MetadataError("yt-dlp is not installed. Run 'python3 run.py --update'")

    monkeypatch.setattr(module, "_import_yt_dlp", refuse)
    with pytest.raises(DownloadFailed) as raised:
        Downloader(config, messages).download(CANONICAL, "mp3")
    assert "yt-dlp is not installed" in str(raised.value)


@pytest.mark.network
def test_metadata_of_a_real_video(config: Config, messages) -> None:
    info = Downloader(config, messages).fetch_info(CANONICAL)
    assert info.title
    assert info.duration == 213
    assert info.audio_languages
