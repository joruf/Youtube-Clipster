"""What happens when YouTube hands out a stream and then refuses to serve it.

A track can play in the Streaming tab and still fail to download with
``HTTP Error 403 Forbidden``.  The two take different routes: playback asks for
``bestaudio[ext=m4a]`` and only resolves a URL, while the download asks for
plain ``ba`` - usually the WebM/Opus stream - and fetches it itself.  YouTube
signs those through different player responses, and the one the download picked
is sometimes refused at transfer time even though the metadata came back fine.

These tests pin the retry down: on a 403 or a DRM wall the download tries
again with the format playback already proves works, then with other player
clients, and it never retries a deleted video or a bot check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from clipster import downloader as module
from clipster.config import Config
from clipster.downloader import (
    DownloadCanceled,
    DownloadFailed,
    Downloader,
    _describe_patch,
    _merged_options,
    classify_error,
)

CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

FORBIDDEN = (
    "ERROR: unable to download video data: HTTP Error 403: Forbidden"
)

DRM = "ERROR: [youtube] eNvUS-6PTbs: This video is DRM protected"
NO_FORMAT = "ERROR: [youtube] eNvUS-6PTbs: Requested format is not available"


class _FakeYdl:
    """Stands in for ``yt_dlp.YoutubeDL`` and records every attempt."""

    #: Filled by the factory below: one entry per constructed instance.
    seen: List[Dict[str, Any]] = []
    #: Errors to raise, one per attempt; ``None`` means "this one works".
    script: List[Any] = []

    def __init__(self, options: Dict[str, Any]) -> None:
        self.options = options
        _FakeYdl.seen.append(options)

    def __enter__(self) -> "_FakeYdl":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def download(self, urls: List[str]) -> None:
        index = len(_FakeYdl.seen) - 1
        problem = _FakeYdl.script[index] if index < len(_FakeYdl.script) else None
        if problem is not None:
            raise problem


@pytest.fixture()
def fake_ytdlp(monkeypatch: pytest.MonkeyPatch):
    """Replace yt-dlp with the recorder and return it."""
    _FakeYdl.seen = []
    _FakeYdl.script = []
    monkeypatch.setattr(module, "_import_yt_dlp", lambda: _FakeYdl)
    return _FakeYdl


@pytest.fixture()
def downloader(config: Config, messages) -> Downloader:
    return Downloader(config, messages)


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------
@pytest.mark.parametrize("message", [
    FORBIDDEN,
    "ERROR: unable to download video, data: HTTP Error 403 Forbidden",
    "HTTP Error 403: Forbidden",
    "fragment 1 not found, unable to continue: 403 forbidden",
])
def test_a_refused_transfer_is_recognised(message: str) -> None:
    assert classify_error(message) == "forbidden"


def test_a_bot_check_still_wins_over_the_status_code() -> None:
    """"Sign in to confirm" is actionable; a bare 403 is not."""
    assert classify_error(
        "HTTP Error 403: Forbidden. Sign in to confirm you're not a bot"
    ) == "bot"


def test_an_unrelated_error_is_still_generic() -> None:
    assert classify_error("Postprocessing: Conversion failed!") == "generic"


def test_the_user_sees_a_403_explained(messages) -> None:
    text = module.user_facing_ytdlp_error(FORBIDDEN, messages, context="download")
    assert text == messages["error_forbidden"]
    assert "403" in text


def test_a_drm_wall_is_recognised() -> None:
    assert classify_error(DRM) == "drm"
    assert classify_error("ERROR: [youtube] abc: This video is DRM protected") == "drm"


def test_a_client_without_formats_is_recognised() -> None:
    assert classify_error(NO_FORMAT) == "noformat"


def test_the_user_sees_drm_explained(messages) -> None:
    text = module.user_facing_ytdlp_error(DRM, messages, context="download")
    assert text == messages["error_drm"]
    assert "DRM" in text


# ----------------------------------------------------------------------
# Option merging
# ----------------------------------------------------------------------
def test_merging_replaces_a_plain_value() -> None:
    merged = _merged_options({"format": "ba", "quiet": True}, {"format": "bv"})
    assert merged["format"] == "bv"
    assert merged["quiet"] is True


def test_merging_keeps_unrelated_extractor_args() -> None:
    base = {"extractor_args": {"youtube": {"player_client": ["default"], "lang": ["de"]}}}
    merged = _merged_options(base, {"extractor_args": {"youtube": {"player_client": ["tv"]}}})
    assert merged["extractor_args"]["youtube"]["player_client"] == ["tv"]
    assert merged["extractor_args"]["youtube"]["lang"] == ["de"]


def test_merging_never_writes_back_into_the_original() -> None:
    base = {"extractor_args": {"youtube": {"player_client": ["default"]}}}
    _merged_options(base, {"extractor_args": {"youtube": {"player_client": ["ios"]}}})
    assert base["extractor_args"]["youtube"]["player_client"] == ["default"]


def test_a_patch_describes_itself_for_the_log() -> None:
    base = {"format": "ba"}
    assert "format" in _describe_patch({"format": "ba[ext=m4a]"}, base)
    assert "tv" in _describe_patch(
        {"format": "ba", "extractor_args": {"youtube": {"player_client": ["tv"]}}}, base
    )


# ----------------------------------------------------------------------
# The retry itself
# ----------------------------------------------------------------------
def test_a_working_download_is_tried_exactly_once(downloader, fake_ytdlp) -> None:
    """No retry may run when nothing went wrong."""
    fake_ytdlp.script = [None]
    downloader.download(CANONICAL, "mp3")
    assert len(fake_ytdlp.seen) == 1


def test_a_403_is_retried_with_the_format_that_plays(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [Exception(FORBIDDEN), None]
    downloader.download(CANONICAL, "mp3")
    assert len(fake_ytdlp.seen) == 2
    assert "ext=m4a" in fake_ytdlp.seen[1]["format"]


def test_the_first_attempt_keeps_the_configured_format(downloader, fake_ytdlp) -> None:
    """The retry must not change what a working download would have fetched."""
    fake_ytdlp.script = [None]
    downloader.download(CANONICAL, "mp3")
    assert fake_ytdlp.seen[0]["format"] == "ba/best"


def test_a_stubborn_403_moves_on_to_other_player_clients(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [Exception(FORBIDDEN), Exception(FORBIDDEN), None]
    downloader.download(CANONICAL, "mp3")
    assert len(fake_ytdlp.seen) == 3
    clients = fake_ytdlp.seen[2]["extractor_args"]["youtube"]["player_client"]
    assert clients == [module._RETRY_PLAYER_CLIENTS[0]]


@pytest.mark.parametrize("media_format", ["mp3", "mp4"])
def test_every_retry_is_a_different_attempt(downloader, fake_ytdlp, media_format: str) -> None:
    """A retry that changes nothing would just fail the same way."""
    fake_ytdlp.script = [Exception(FORBIDDEN)] * 10
    with pytest.raises(DownloadFailed):
        downloader.download(CANONICAL, media_format)
    signatures = [
        (
            attempt.get("format"),
            tuple(((attempt.get("extractor_args") or {}).get("youtube") or {})
                  .get("player_client") or ()),
        )
        for attempt in fake_ytdlp.seen
    ]
    assert len(signatures) == len(set(signatures))


def test_the_retries_run_out_and_the_403_is_reported(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [Exception(FORBIDDEN)] * 10
    with pytest.raises(DownloadFailed) as raised:
        downloader.download(CANONICAL, "mp3")
    assert raised.value.kind == "forbidden"


def test_a_drm_wall_is_retried_with_another_player_client(downloader, fake_ytdlp) -> None:
    """The TV-client experiment reports DRM; the next client often still works."""
    fake_ytdlp.script = [Exception(FORBIDDEN), Exception(DRM), None]
    downloader.download(CANONICAL, "mp3")
    assert len(fake_ytdlp.seen) == 3
    clients = fake_ytdlp.seen[2]["extractor_args"]["youtube"]["player_client"]
    assert clients == [module._RETRY_PLAYER_CLIENTS[0]]
    assert "tv" not in clients


def test_the_retries_run_out_and_the_drm_is_reported(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [Exception(DRM)] * 10
    with pytest.raises(DownloadFailed) as raised:
        downloader.download(CANONICAL, "mp3")
    assert raised.value.kind == "drm"


def test_a_client_without_formats_moves_on(downloader, fake_ytdlp) -> None:
    """ios-without-PO-token used to abort the chain as a generic error."""
    fake_ytdlp.script = [Exception(FORBIDDEN), Exception(FORBIDDEN), Exception(NO_FORMAT), None]
    downloader.download(CANONICAL, "mp3")
    assert len(fake_ytdlp.seen) == 4
    clients = fake_ytdlp.seen[3]["extractor_args"]["youtube"]["player_client"]
    assert clients == [module._RETRY_PLAYER_CLIENTS[1]]


def test_an_unavailable_video_is_never_retried(downloader, fake_ytdlp) -> None:
    """Retrying a deleted video wastes the user's time and YouTube's patience."""
    fake_ytdlp.script = [Exception("ERROR: Video unavailable")]
    with pytest.raises(DownloadFailed) as raised:
        downloader.download(CANONICAL, "mp3")
    assert raised.value.kind == "unavailable"
    assert len(fake_ytdlp.seen) == 1


def test_a_bot_check_is_never_retried(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [Exception("ERROR: Sign in to confirm you're not a bot")]
    with pytest.raises(DownloadFailed) as raised:
        downloader.download(CANONICAL, "mp3")
    assert raised.value.kind == "bot"
    assert len(fake_ytdlp.seen) == 1


def test_a_cancelled_download_is_never_retried(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [DownloadCanceled()]
    with pytest.raises(DownloadCanceled):
        downloader.download(CANONICAL, "mp3")
    assert len(fake_ytdlp.seen) == 1


def test_cancelling_during_a_403_retry_stops_it(downloader, fake_ytdlp) -> None:
    import threading

    stop = threading.Event()
    stop.set()
    fake_ytdlp.script = [Exception(FORBIDDEN)] * 5
    with pytest.raises(DownloadCanceled):
        downloader.download(CANONICAL, "mp3", cancel_event=stop)
    assert len(fake_ytdlp.seen) == 1


def test_mp4_downloads_retry_with_a_progressive_stream(downloader, fake_ytdlp) -> None:
    """One already muxed file - the kind the Streaming tab plays without trouble."""
    fake_ytdlp.script = [Exception(FORBIDDEN), None]
    downloader.download(CANONICAL, "mp4")
    assert len(fake_ytdlp.seen) == 2
    retried = fake_ytdlp.seen[1]["format"]
    assert retried != fake_ytdlp.seen[0]["format"]
    assert retried.startswith("b[ext=mp4]")


def test_a_language_choice_survives_the_retry(downloader, fake_ytdlp) -> None:
    fake_ytdlp.script = [Exception(FORBIDDEN), None]
    downloader.download(CANONICAL, "mp3", language="de")
    assert "language^=de" in fake_ytdlp.seen[1]["format"]


def test_the_retry_keeps_the_output_settings(downloader, fake_ytdlp, downloads: Path) -> None:
    """A retry must land in the same folder with the same naming rules."""
    fake_ytdlp.script = [Exception(FORBIDDEN), None]
    downloader.download(CANONICAL, "mp3")
    first, second = fake_ytdlp.seen
    assert second["paths"] == first["paths"]
    assert second["outtmpl"] == first["outtmpl"]
    assert second["postprocessors"] == first["postprocessors"]


def test_the_retry_list_is_ordered_format_first(downloader) -> None:
    variants = downloader._forbidden_retries("mp3", "")
    assert "format" in variants[0]
    assert all("extractor_args" in item for item in variants[1:])
    assert all(
        "tv" not in item["extractor_args"]["youtube"]["player_client"]
        and "ios" not in item["extractor_args"]["youtube"]["player_client"]
        for item in variants[1:]
    )
