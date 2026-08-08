"""Cutting one section out of a video: the two time fields and what they cause.

Three things are pinned down here:

* what counts as a time and what is a typo (``1:75`` is a typo),
* that a section never lands on the file name of the full download,
* that the section reaches yt-dlp as a range instead of being silently dropped.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from clipster import clip
from clipster import downloader as module
from clipster.clip import ClipRange
from clipster.config import Config
from clipster.downloader import Downloader

CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ----------------------------------------------------------------------
# Reading a time
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, seconds",
    [
        ("90", 90.0),
        ("0", 0.0),
        ("1:30", 90.0),
        ("01:30", 90.0),
        ("1:02:03", 3723.0),
        ("1:30.5", 90.5),
        ("1:30,5", 90.5),
        (" 1:30 ", 90.0),
        ("1 : 30", 90.0),
    ],
)
def test_a_time_is_read_the_way_it_is_written(text: str, seconds: float) -> None:
    assert clip.parse_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "abc",
        "-5",
        "1:75",  # a typo, not 2:15
        "1:60:00",
        "1:2:3:4",
        "1e3",  # float() would take this - a time must not
        "inf",
        "nan",
        "12:",
    ],
)
def test_anything_that_is_not_a_time_is_refused(text: str) -> None:
    assert clip.parse_time(text) is None


@pytest.mark.parametrize(
    "seconds, text",
    [(0, "0:00"), (5, "0:05"), (90, "1:30"), (3723, "1:02:03"), (90.9, "1:30")],
)
def test_a_time_is_written_back_as_a_clock(seconds: float, text: str) -> None:
    assert clip.format_time(seconds) == text


# ----------------------------------------------------------------------
# Reading the two fields together
# ----------------------------------------------------------------------
def test_two_empty_fields_mean_the_whole_video() -> None:
    assert clip.parse_range("", "", 213) == (None, "")


def test_the_whole_video_spelled_out_is_still_the_whole_video() -> None:
    """Nothing to cut, so no section - the file keeps its plain name."""
    assert clip.parse_range("0:00", "3:33", 213) == (None, "")


def test_an_open_end_runs_to_the_end_of_the_video() -> None:
    section, error = clip.parse_range("1:00", "", 213)
    assert error == ""
    assert section == ClipRange(start=60.0, end=213.0)


def test_an_open_end_stays_open_when_the_length_is_unknown() -> None:
    section, error = clip.parse_range("1:00", "", 0)
    assert error == ""
    assert section == ClipRange(start=60.0, end=None)


def test_an_open_start_begins_at_the_beginning() -> None:
    section, error = clip.parse_range("", "1:00", 213)
    assert error == ""
    assert section == ClipRange(start=0.0, end=60.0)


def test_an_end_beyond_the_video_is_pulled_back_instead_of_refused() -> None:
    """Typing a generous end is a normal way to say "until it is over"."""
    section, error = clip.parse_range("1:00", "99:00", 213)
    assert error == ""
    assert section == ClipRange(start=60.0, end=213.0)


@pytest.mark.parametrize("start, end, key", [
    ("nope", "1:00", clip.ERROR_TIME),
    ("1:00", "nope", clip.ERROR_TIME),
    ("2:00", "1:00", clip.ERROR_ORDER),
    ("1:00", "1:00", clip.ERROR_ORDER),
    ("9:00", "", clip.ERROR_RANGE),
])
def test_input_that_cannot_work_is_reported_not_guessed(start: str, end: str, key: str) -> None:
    section, error = clip.parse_range(start, end, 213)
    assert section is None
    assert error == key


def test_every_error_key_has_a_translation() -> None:
    from clipster import i18n

    for language in ("en", "de"):
        messages = i18n.load(language)
        for key in (clip.ERROR_TIME, clip.ERROR_ORDER, clip.ERROR_RANGE):
            assert messages[key] != key, "{0} is missing in {1}".format(key, language)


# ----------------------------------------------------------------------
# What a section is called
# ----------------------------------------------------------------------
def test_the_section_carries_its_length_and_a_label() -> None:
    section = ClipRange(start=83.0, end=165.0)
    assert section.length == pytest.approx(82.0)
    assert section.label() == "1:23 - 2:45"
    assert ClipRange(start=83.0).length is None
    assert ClipRange(start=83.0).label() == "1:23"


def test_two_runs_of_the_same_piece_share_one_key() -> None:
    """The download list compares this, so it has to be stable."""
    assert ClipRange(start=83.0, end=165.0).key() == ClipRange(start=83, end=165).key()
    assert ClipRange(start=83.0, end=165.0).key() != ClipRange(start=83.0, end=166.0).key()
    assert ClipRange(start=83.0).key() != ClipRange(start=83.0, end=165.0).key()


def test_the_file_name_says_which_piece_it_is() -> None:
    section = ClipRange(start=83.0, end=165.0)
    assert clip.output_template("%(title)s.%(ext)s", section) == "%(title)s [1-23_2-45].%(ext)s"


def test_the_marker_never_contains_a_colon() -> None:
    """Windows refuses those, and the same name has to work on every platform."""
    for section in (ClipRange(start=3723.0, end=3800.0), ClipRange(start=5.0)):
        assert ":" not in clip.output_template("%(title)s.%(ext)s", section)


def test_a_template_without_an_extension_still_gets_the_marker() -> None:
    section = ClipRange(start=5.0, end=10.0)
    assert clip.output_template("%(title)s", section) == "%(title)s [0-05_0-10]"


def test_the_marker_lands_in_front_of_the_extension_of_a_longer_template() -> None:
    section = ClipRange(start=5.0, end=10.0)
    template = "%(uploader)s/%(title)s-%(id)s.%(ext)s"
    assert clip.output_template(template, section) == "%(uploader)s/%(title)s-%(id)s [0-05_0-10].%(ext)s"


# ----------------------------------------------------------------------
# What yt-dlp is asked for
# ----------------------------------------------------------------------
class _FakeYdl:
    """Stands in for ``yt_dlp.YoutubeDL`` and keeps the options it was given."""

    seen: List[Dict[str, Any]] = []

    def __init__(self, options: Dict[str, Any]) -> None:
        _FakeYdl.seen.append(options)

    def __enter__(self) -> "_FakeYdl":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def download(self, urls: List[str]) -> None:
        """A download that does nothing but succeed."""


@pytest.fixture()
def fake_ytdlp(monkeypatch: pytest.MonkeyPatch):
    """Replace yt-dlp with the recorder and return it."""
    _FakeYdl.seen = []
    monkeypatch.setattr(module, "_import_yt_dlp", lambda: _FakeYdl)
    return _FakeYdl


@pytest.fixture()
def downloader(config: Config, messages) -> Downloader:
    return Downloader(config, messages)


def test_the_section_reaches_yt_dlp_as_a_range(downloader: Downloader, fake_ytdlp) -> None:
    downloader.download(CANONICAL, "mp3", section=ClipRange(start=83.0, end=165.0))
    options = fake_ytdlp.seen[0]
    assert options["download_ranges"]({}, None) == [{"start_time": 83.0, "end_time": 165.0}]
    assert options["force_keyframes_at_cuts"] is True, "cut where the user asked, not at a keyframe"
    assert options["outtmpl"] == "%(title)s [1-23_2-45].%(ext)s"


def test_an_open_end_is_left_to_yt_dlp(downloader: Downloader, fake_ytdlp) -> None:
    """Without an end time yt-dlp cuts at the end of the video itself."""
    downloader.download(CANONICAL, "mp3", section=ClipRange(start=83.0))
    assert fake_ytdlp.seen[0]["download_ranges"]({}, None) == [{"start_time": 83.0}]


def test_a_whole_video_asks_for_no_range_at_all(downloader: Downloader, fake_ytdlp) -> None:
    downloader.download(CANONICAL, "mp3")
    options = fake_ytdlp.seen[0]
    assert "download_ranges" not in options
    assert "force_keyframes_at_cuts" not in options
    assert options["outtmpl"] == "%(title)s.%(ext)s"


def test_only_the_section_has_to_fit_on_the_disk(downloader: Downloader, fake_ytdlp,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """A ten second clip must not be refused because the whole video is too big."""
    monkeypatch.setattr(module, "free_space", lambda _target: 50_000_000)
    downloader.download(
        CANONICAL,
        "mp3",
        duration=600,
        estimated_size=400_000_000,
        section=ClipRange(start=0.0, end=10.0),
    )
    assert fake_ytdlp.seen, "the download must not be refused for lack of space"


def test_the_section_survives_a_retry_after_a_refused_stream(downloader: Downloader,
                                                             fake_ytdlp) -> None:
    """The 403 retries change the format - never what is being cut out."""
    section = ClipRange(start=83.0, end=165.0)
    downloader.download(CANONICAL, "mp3", section=section)
    base = fake_ytdlp.seen[0]
    for patch in downloader._forbidden_retries("mp3", ""):
        merged = module._merged_options(base, patch)
        assert merged["download_ranges"] is base["download_ranges"]
        assert merged["outtmpl"] == "%(title)s [1-23_2-45].%(ext)s"
