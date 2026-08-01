"""The application logic around the download: queue, duplicates, visibility.

The pipeline itself needs a Tk root, so the tests build a real application but
never let it reach the network: the downloader is replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipster.history import STATUS_OK, HistoryEntry

pytestmark = pytest.mark.gui

URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
URL_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


@pytest.fixture()
def app(config, messages, monkeypatch):
    """Return an application whose pipeline records instead of downloading.

    Only :meth:`ClipsterApp._handle_url` is replaced, so everything around it -
    the queue, the state flags, the navigation window - is the real thing.
    """
    from clipster.app import ClipsterApp

    instance = ClipsterApp(config, messages)
    started: list = []
    monkeypatch.setattr(instance, "_handle_url", started.append)
    instance.started = started  # type: ignore[attr-defined]
    try:
        yield instance
    finally:
        instance.gui.destroy()


# ----------------------------------------------------------------------
# The waiting list
# ----------------------------------------------------------------------
def test_the_first_link_starts_at_once(app) -> None:
    app._enqueue(URL_A, "")
    assert app.started == [URL_A]
    assert not app._queue


def test_a_link_copied_during_a_download_is_kept(app) -> None:
    """It used to be dropped silently."""
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "")
    assert app.started == [URL_A]
    assert [url for url, _, _ in app._queue] == [URL_B]


def test_the_same_link_is_not_queued_twice(app) -> None:
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "")
    app._enqueue(URL_B, "")
    assert len([url for url, _, _ in app._queue]) == 1


def test_the_waiting_list_has_an_upper_bound(app) -> None:
    app._enqueue(URL_A, "")
    for index in range(app.MAX_QUEUE + 5):
        app._enqueue("https://www.youtube.com/watch?v=q{0:010d}".format(index), "")
    assert len(app._queue) == app.MAX_QUEUE


def test_the_next_link_starts_when_the_previous_one_finished(app) -> None:
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "")
    app._busy = False
    app._start_next()
    assert app.started == [URL_A, URL_B]
    assert not app._queue


def test_nothing_starts_while_a_download_runs(app) -> None:
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "")
    app._start_next()
    assert app.started == [URL_A], "the running download must not be interrupted"


def test_the_chosen_format_travels_with_the_entry(app) -> None:
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "mp4")
    app._busy = False
    app._start_next()
    assert app._forced_format == "mp4"


def test_quitting_throws_the_waiting_links_away(app) -> None:
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "")
    app.request_quit()
    assert not app._queue


def test_nothing_starts_after_the_quit_request(app) -> None:
    app._enqueue(URL_A, "")
    app._queue.append((URL_B, "", False))
    app.request_quit()
    app._busy = False
    app._start_next()
    assert app.started == [URL_A]


# ----------------------------------------------------------------------
# Downloading the same video twice
# ----------------------------------------------------------------------
def test_an_earlier_download_is_found(app, tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                 media_format="mp3", status=STATUS_OK))
    assert app.history.find_download(URL_A, "mp3") is not None


def test_another_format_is_not_the_same_download(app, tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                 media_format="mp3", status=STATUS_OK))
    assert app.history.find_download(URL_A, "mp4") is None, "MP4 still has to be fetched"


def test_a_deleted_file_is_not_offered(app, tmp_path: Path) -> None:
    app.history.add(HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"), url=URL_A,
                                 media_format="mp3", status=STATUS_OK))
    assert app.history.find_download(URL_A, "mp3") is None


def test_a_failed_attempt_does_not_count(app) -> None:
    from clipster.history import STATUS_FAILED

    app.history.add(HistoryEntry(title="broken", url=URL_A, media_format="mp3",
                                 status=STATUS_FAILED))
    assert app.history.find_download(URL_A, "mp3") is None


def test_an_unknown_url_finds_nothing(app) -> None:
    assert app.history.find_download(URL_B, "mp3") is None
    assert app.history.find_download("", "mp3") is None


def test_the_existing_file_is_offered_instead_of_downloading(app, tmp_path: Path) -> None:
    from clipster.downloader import VideoInfo

    target = tmp_path / "song.mp3"
    target.write_bytes(b"x" * 100)
    entry = app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                         media_format="mp3", size=100, status=STATUS_OK))
    info = VideoInfo(url=URL_A, title="A song")
    app._offer_existing(app.gui.nav, info, entry, URL_A, "mp3")
    assert app.gui.nav.result_path() == target
    assert not app._busy, "the run is over, nothing was downloaded"


def test_download_again_starts_a_real_download(app, tmp_path: Path) -> None:
    from clipster.downloader import VideoInfo

    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    entry = app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                         media_format="mp3", status=STATUS_OK))
    app._offer_existing(app.gui.nav, VideoInfo(url=URL_A, title="A song"), entry, URL_A, "mp3")
    app.started.clear()
    # Press the "download again" button of the navigation window.
    for child in app.gui.nav._buttons.winfo_children():
        if str(child.cget("text")) == app.messages["nav_download_again"]:
            child.invoke()
            break
    else:
        pytest.fail("the download again button was not rendered")
    assert app.started == [URL_A]
    assert app._force_redownload is True


def test_the_force_flag_survives_the_waiting_list(app) -> None:
    """A deliberate re-download must not be answered with "already there"."""
    app._enqueue(URL_A, "")
    app._enqueue(URL_B, "mp3", force=True)
    app._busy = False
    app._start_next()
    assert app._force_redownload is True


def test_the_force_flag_is_cleared_afterwards(app) -> None:
    app._force_redownload = True
    app._busy = True
    app._finish_worker("status_done", title="x")
    assert app._force_redownload is False


def test_a_file_that_vanished_meanwhile_is_downloaded_again(app, tmp_path: Path) -> None:
    from clipster.downloader import VideoInfo

    entry = HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"), url=URL_A,
                         media_format="mp3", status=STATUS_OK)
    app._busy = False
    app.started.clear()
    app._offer_existing(app.gui.nav, VideoInfo(url=URL_A, title="t"), entry, URL_A, "mp3")
    assert app.started == [URL_A]


# ----------------------------------------------------------------------
# Startup visibility
# ----------------------------------------------------------------------
def test_with_a_tray_and_start_minimized_no_window_appears(app) -> None:
    app._tray_active = True
    app.config.start_minimized = True
    app._apply_initial_visibility()
    assert not app.gui.view_visible()


def test_with_a_tray_and_start_minimized_off_the_window_appears(app) -> None:
    app._tray_active = True
    app.config.start_minimized = False
    app._apply_initial_visibility()
    assert app.gui.view_visible()


def test_without_a_tray_the_window_always_appears(app) -> None:
    """It is the only way left to quit the program."""
    app._tray_active = False
    app.config.start_minimized = True
    app._apply_initial_visibility()
    assert app.gui.view_visible()


# ----------------------------------------------------------------------
# Audio track resolution
# ----------------------------------------------------------------------
def test_a_single_track_is_selected_explicitly(app) -> None:
    from clipster.downloader import AudioTrack, VideoInfo

    info = VideoInfo(url="u", title="t", audio_languages=["de"],
                     audio_tracks=[AudioTrack("de", False)])
    assert app._auto_language(info) == "de"


def test_with_several_tracks_the_original_wins(app) -> None:
    from clipster.downloader import AudioTrack, VideoInfo

    info = VideoInfo(url="u", title="t", audio_languages=["de", "en"],
                     audio_tracks=[AudioTrack("de", True), AudioTrack("en", False)])
    assert app._auto_language(info) == "de"


def test_without_any_track_information_nothing_is_forced(app) -> None:
    from clipster.downloader import VideoInfo

    assert app._auto_language(VideoInfo(url="u", title="t")) == ""


# ----------------------------------------------------------------------
# The toolbar
# ----------------------------------------------------------------------
def test_a_pasted_link_is_normalised_before_it_is_queued(app) -> None:
    app._submit_url("https://youtu.be/aaaaaaaaaaa?si=x", "mp3")
    assert app.started == [URL_A]


def test_something_that_is_not_a_link_is_rejected(app, monkeypatch) -> None:
    errors: list = []
    monkeypatch.setattr(app.gui, "show_error", lambda *a: errors.append(a))
    app._submit_url("https://example.com/video", "mp3")
    assert errors
    assert app.started == []


def test_a_second_link_from_the_toolbar_queues_instead_of_erroring(app) -> None:
    app._submit_url(URL_A, "mp3")
    app._submit_url(URL_B, "mp4")
    assert app.started == [URL_A]
    assert [url for url, _, _ in app._queue] == [URL_B]


def test_the_running_link_is_not_queued_again(app) -> None:
    """Copying the same link twice must not queue a pointless second run."""
    app._enqueue(URL_A, "")
    app._enqueue(URL_A, "")
    assert not app._queue


def test_but_a_deliberate_repeat_of_the_running_link_is_kept(app) -> None:
    app._enqueue(URL_A, "")
    app._enqueue(URL_A, "mp3", force=True)
    assert [url for url, _, _ in app._queue] == [URL_A]


def test_the_running_link_is_forgotten_when_the_run_ends(app) -> None:
    app._enqueue(URL_A, "")
    assert app._current_url == URL_A
    app._finish_worker("status_done", title="x")
    assert app._current_url == ""


def test_the_force_flag_does_not_leak_into_the_next_link(app) -> None:
    """A forced run must not make the following link skip its duplicate check."""
    app._enqueue(URL_A, "", force=True)
    assert app._force_redownload is True
    app._finish_worker("status_done", title="x")
    app._enqueue(URL_B, "")
    assert app._force_redownload is False
