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
        instance._cancel_auto_discover_job()
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
    app._finish_worker(URL_A, "status_done", title="x")
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
    app._finish_worker(URL_A, "status_done", title="x")
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
    app._enqueue(URL_A, "mp3")
    app._offer_existing(app.gui.nav, info, entry, URL_A, "mp3")
    assert app.gui.nav.result_path() == target
    assert URL_A not in app._active, "the run is over, nothing was downloaded"


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
    app._finish_worker(URL_A, "status_done", title="x")
    app._start_next()
    assert app._force_redownload is True


def test_the_force_flag_is_cleared_afterwards(app) -> None:
    app._enqueue(URL_A, "", force=True)
    assert app._force_redownload is True
    app._finish_worker(URL_A, "status_done", title="x")
    assert app._force_redownload is False


def test_a_file_that_vanished_meanwhile_is_downloaded_again(app, tmp_path: Path) -> None:
    from clipster.downloader import VideoInfo

    entry = HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"), url=URL_A,
                         media_format="mp3", status=STATUS_OK)
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


def test_post_start_keeps_view_hidden_in_tray_mode(app, monkeypatch) -> None:
    """Terms / startup dialogs must not leave Streaming open in tray-start mode."""
    from clipster.terms import accept_app_terms

    accept_app_terms(app.config)
    app._tray_active = True
    app._tray_show_armed = True
    app.config.start_minimized = True
    app.config.check_updates = False
    app.gui.show_view()
    assert app.gui.view_visible()
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)

    app._post_start()

    assert not app.gui.view_visible()


def test_post_start_never_notifies_started(app, monkeypatch) -> None:
    """Startup must not show an OS balloon or toast for the 'started' message."""
    from clipster.terms import accept_app_terms

    accept_app_terms(app.config)
    app.config.show_startup_notification = True  # even if the flag is on
    app.config.check_updates = False
    notified: list[str] = []
    toasted: list[str] = []
    monkeypatch.setattr(app.tray, "notify", lambda message: notified.append(message) or True)
    monkeypatch.setattr(app.gui, "toast", toasted.append)
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)

    app._post_start()

    assert notified == []
    assert toasted == []


def test_auto_discover_runs_when_online_and_streaming_terms_accepted(app, monkeypatch) -> None:
    from clipster.terms import accept_app_terms, accept_streaming_terms

    accept_app_terms(app.config)
    accept_streaming_terms(app.config)
    app.config.check_updates = False
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)
    monkeypatch.setattr("clipster.app.internet_available", lambda **_kwargs: True)
    refresh_calls: list[str] = []
    monkeypatch.setattr(app, "_discover_refresh", lambda **kwargs: refresh_calls.append("go"))

    # post_start only schedules; invoke the runner directly to avoid Tk after waits.
    app._post_start()
    assert app._auto_discover_done is True
    app._cancel_auto_discover_job()
    app._run_auto_discover()
    assert refresh_calls == ["go"]


def test_auto_discover_skipped_without_streaming_terms(app, monkeypatch) -> None:
    from clipster.terms import accept_app_terms

    accept_app_terms(app.config)
    app.config.terms_streaming_version = ""
    app.config.check_updates = False
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)
    monkeypatch.setattr("clipster.app.internet_available", lambda **_kwargs: True)
    refresh_calls: list[str] = []
    monkeypatch.setattr(app, "_discover_refresh", lambda **kwargs: refresh_calls.append("go"))

    app._post_start()
    app._run_auto_discover()
    assert refresh_calls == []
    assert app._auto_discover_done is False


def test_auto_discover_skipped_when_offline(app, monkeypatch) -> None:
    from clipster.terms import accept_app_terms, accept_streaming_terms

    accept_app_terms(app.config)
    accept_streaming_terms(app.config)
    monkeypatch.setattr("clipster.app.internet_available", lambda **_kwargs: False)
    refresh_calls: list[str] = []
    monkeypatch.setattr(app, "_discover_refresh", lambda **kwargs: refresh_calls.append("go"))

    app._auto_discover_done = True
    app._run_auto_discover()
    assert refresh_calls == []


def test_tray_show_ignored_before_armed(app) -> None:
    app._tray_show_armed = False
    app.gui.hide_view()
    app._show_view()
    assert not app.gui.view_visible()
    app._tray_show_armed = True
    app._show_view()
    assert app.gui.view_visible()


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


def test_discover_status_text_for_blocked(app) -> None:
    from clipster.discover import DiscoverOutcome

    outcome = DiscoverOutcome(blocked=True, error_summary="Sign in to confirm you're not a bot")
    text, level = app._discover_status_text(outcome)
    assert level == "error"
    assert "cookie" in text.lower() or "Settings" in text or "Einstellung" in text
    assert "Sign in to confirm" not in text
    assert "github.com" not in text


def test_discover_status_text_for_blocked_with_cookies(app) -> None:
    from clipster.discover import DiscoverOutcome

    app.config.cookies_risk_acknowledged = True
    app.config.cookies_from_browser = "firefox"
    outcome = DiscoverOutcome(blocked=True, error_summary="Sign in to confirm you're not a bot")
    text, level = app._discover_status_text(outcome)
    assert level == "error"
    assert text == app.messages["discover_blocked_with_cookies"]
    assert "Sign in to confirm" not in text


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
    assert URL_A in app._active
    app._finish_worker(URL_A, "status_done", title="x")
    assert URL_A not in app._active


def test_the_force_flag_does_not_leak_into_the_next_link(app) -> None:
    """A forced run must not make the following link skip its duplicate check."""
    app._enqueue(URL_A, "", force=True)
    assert app._force_redownload is True
    app._finish_worker(URL_A, "status_done", title="x")
    app._question_answered()
    app._enqueue(URL_B, "")
    assert app._force_redownload is False


# ----------------------------------------------------------------------
# One at a time, or several at once
# ----------------------------------------------------------------------
def test_sequentially_only_one_download_runs(app) -> None:
    app.config.parallel_downloads = False
    app._enqueue(URL_A, "")
    app._question_answered()          # the format question is out of the way
    app._enqueue(URL_B, "")
    assert app.started == [URL_A]
    assert [url for url, _, _ in app._queue] == [URL_B]


def test_in_parallel_the_second_link_starts_right_away(app) -> None:
    app.config.parallel_downloads = True
    app.config.max_parallel_downloads = 3
    app._enqueue(URL_A, "")
    app._question_answered()
    app._enqueue(URL_B, "")
    assert app.started == [URL_A, URL_B]
    assert not app._queue


def test_the_parallel_limit_is_respected(app) -> None:
    app.config.parallel_downloads = True
    app.config.max_parallel_downloads = 2
    for index, url in enumerate((URL_A, URL_B, "https://www.youtube.com/watch?v=ccccccccccc")):
        app._enqueue(url, "")
        app._question_answered()
    assert len(app.started) == 2
    assert len(app._queue) == 1


def test_a_question_blocks_a_second_one(app) -> None:
    """The navigation window is single; two questions would overwrite each other."""
    app.config.parallel_downloads = True
    app._enqueue(URL_A, "")           # leaves _asking set
    app._enqueue(URL_B, "")
    assert app.started == [URL_A]
    assert [url for url, _, _ in app._queue] == [URL_B]


def test_the_limit_is_one_while_parallel_is_off(app) -> None:
    app.config.parallel_downloads = False
    app.config.max_parallel_downloads = 5
    assert app._parallel_limit() == 1


def test_the_limit_never_drops_below_one(app) -> None:
    app.config.parallel_downloads = True
    app.config.max_parallel_downloads = 0
    assert app._parallel_limit() == 1


def test_the_window_belongs_to_the_newest_run(app) -> None:
    """Older runs keep going but must not draw over the newer question."""
    app.config.parallel_downloads = True
    app._enqueue(URL_A, "")
    app._question_answered()
    app._enqueue(URL_B, "")
    assert app._owns_nav(URL_B)
    assert not app._owns_nav(URL_A)


def test_quitting_cancels_every_running_download(app) -> None:
    app.config.parallel_downloads = True
    app._enqueue(URL_A, "")
    app._question_answered()
    app._enqueue(URL_B, "")
    app.request_quit()
    assert all(event.is_set() for event in app._cancel_events.values())


# ----------------------------------------------------------------------
# Deleting a row
# ----------------------------------------------------------------------
def test_deleting_removes_the_file_and_the_entry(app, tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    entry = app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                         media_format="mp3", status=STATUS_OK))
    app._delete_entry(entry)
    assert not target.exists()
    assert len(app.history) == 0


def test_deleting_an_entry_whose_file_is_gone_still_clears_the_row(app, tmp_path: Path) -> None:
    entry = app.history.add(HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"),
                                         url=URL_A, media_format="mp3", status=STATUS_OK))
    app._delete_entry(entry)
    assert len(app.history) == 0


def test_a_failed_entry_can_be_cleared_away(app) -> None:
    from clipster.history import STATUS_FAILED

    entry = app.history.add(HistoryEntry(title="broken", url=URL_A, status=STATUS_FAILED,
                                         error="boom"))
    app._delete_entry(entry)
    assert len(app.history) == 0


def test_a_file_that_cannot_be_deleted_keeps_its_row(app, tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    entry = app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                         status=STATUS_OK))
    errors = []
    monkeypatch.setattr(app.gui, "show_error", lambda *a: errors.append(a))
    monkeypatch.setattr(Path, "unlink", lambda self, **k: (_ for _ in ()).throw(OSError("busy")))
    app._delete_entry(entry)
    assert errors, "the user has to be told"
    assert len(app.history) == 1, "the row stays as long as the file does"


def test_hiding_removes_the_entry_but_keeps_the_file(app, tmp_path: Path) -> None:
    target = tmp_path / "keep.mp3"
    target.write_bytes(b"x")
    entry = app.history.add(
        HistoryEntry(name="keep.mp3", path=str(target), url=URL_A, media_format="mp3", status=STATUS_OK)
    )
    app._hide_entry(entry)
    assert target.exists()
    assert len(app.history) == 0


def test_playing_a_missing_file_reports_it(app, tmp_path: Path, monkeypatch) -> None:
    errors = []
    monkeypatch.setattr(app.gui, "show_error", lambda *a: errors.append(a))
    app._play_entry(HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"),
                                 status=STATUS_OK))
    assert errors


def test_playing_hands_the_file_to_the_desktop(app, tmp_path: Path, monkeypatch) -> None:
    from clipster import shortcuts

    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    opened = []
    monkeypatch.setattr(shortcuts, "open_path", lambda p: opened.append(p) or True)
    app._play_entry(HistoryEntry(name="song.mp3", path=str(target), status=STATUS_OK))
    assert opened == [target]


# ----------------------------------------------------------------------
# Requests from outside the program (the remote interface)
# ----------------------------------------------------------------------
def test_a_remote_request_starts_a_download(app) -> None:
    from clipster.app import SUBMIT_STARTED

    result = app.submit_remote(URL_A, "mp3")
    assert result.state == SUBMIT_STARTED
    assert result.accepted
    assert result.url == URL_A
    assert app.started == [URL_A]


def test_a_remote_request_never_asks_anything(app) -> None:
    """The phone cannot answer a dialog, so the format must already be decided."""
    app.submit_remote(URL_A, "mp4")
    assert app._forced_format == "mp4"


def test_a_remote_request_is_queued_behind_a_running_one(app) -> None:
    from clipster.app import SUBMIT_QUEUED

    app.submit_remote(URL_A, "mp3")
    result = app.submit_remote(URL_B, "mp3")
    assert result.state == SUBMIT_QUEUED
    assert result.accepted
    assert result.position == 1


def test_the_same_link_twice_is_reported_not_silently_dropped(app) -> None:
    from clipster.app import SUBMIT_RUNNING, SUBMIT_WAITING

    app.submit_remote(URL_A, "mp3")
    assert app.submit_remote(URL_A, "mp3").state == SUBMIT_RUNNING
    app.submit_remote(URL_B, "mp3")
    assert app.submit_remote(URL_B, "mp3").state == SUBMIT_WAITING


def test_a_full_waiting_list_is_reported(app) -> None:
    from clipster.app import SUBMIT_FULL

    app.submit_remote(URL_A, "mp3")
    for index in range(app.MAX_QUEUE):
        # A YouTube id is exactly 11 characters; a longer one is refused, which
        # would leave the waiting list half empty and this test meaningless.
        filler = "https://www.youtube.com/watch?v=q{0:010d}".format(index)
        assert app.submit_remote(filler, "mp3").accepted, filler
    result = app.submit_remote("https://www.youtube.com/watch?v=zzzzzzzzzzz", "mp3")
    assert result.state == SUBMIT_FULL
    assert not result.accepted


@pytest.mark.parametrize("bad", ["", "not a url", "https://example.com/watch?v=x", "ftp://x"])
def test_something_that_is_not_a_youtube_link_is_refused(app, bad: str) -> None:
    from clipster.app import SUBMIT_INVALID

    assert app.submit_remote(bad, "mp3").state == SUBMIT_INVALID
    assert app.started == []


@pytest.mark.parametrize("bad", ["", "wav", "MP3", "mp3; rm -rf /"])
def test_an_unknown_format_is_refused(app, bad: str) -> None:
    """An empty format would open the interactive question nobody can answer."""
    from clipster.app import SUBMIT_FORMAT

    assert app.submit_remote(URL_A, bad).state == SUBMIT_FORMAT
    assert app.started == []


def test_an_already_downloaded_file_is_offered_instead_of_fetched(app, tmp_path: Path) -> None:
    from clipster.app import SUBMIT_EXISTS

    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    entry = app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                        media_format="mp3", status=STATUS_OK))

    result = app.submit_remote(URL_A, "mp3")
    assert result.state == SUBMIT_EXISTS
    assert result.entry_id == entry.identifier()
    assert app.started == [], "nothing may be downloaded a second time"


def test_the_same_video_in_the_other_format_is_a_new_download(app, tmp_path: Path) -> None:
    from clipster.app import SUBMIT_STARTED

    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                 media_format="mp3", status=STATUS_OK))
    assert app.submit_remote(URL_A, "mp4").state == SUBMIT_STARTED


def test_force_downloads_an_existing_file_again(app, tmp_path: Path) -> None:
    from clipster.app import SUBMIT_STARTED

    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    app.history.add(HistoryEntry(name="song.mp3", path=str(target), url=URL_A,
                                 media_format="mp3", status=STATUS_OK))
    result = app.submit_remote(URL_A, "mp3", force=True)
    assert result.state == SUBMIT_STARTED
    assert app._force_redownload is True


def test_a_closing_program_takes_nothing_new(app) -> None:
    from clipster.app import SUBMIT_CLOSING

    app._quitting = True
    assert app.submit_remote(URL_A, "mp3").state == SUBMIT_CLOSING
    assert app.started == []


def test_a_request_from_another_thread_lands_on_the_gui_thread(app) -> None:
    """The web server answers in its own thread; touching Tk from there breaks it."""
    import threading

    from clipster.app import SUBMIT_STARTED

    app.bridge.start()
    seen: dict = {}

    def worker() -> None:
        try:
            seen["result"] = app.submit_remote(URL_A, "mp3")
            seen["gui_thread"] = False
        except Exception as exc:  # pragma: no cover - reported through the assert
            seen["error"] = exc

    original = app._enqueue

    def recording_enqueue(url, media_format, force=False):
        seen["ran_on_gui_thread"] = app.bridge.on_gui_thread()
        return original(url, media_format, force)

    app._enqueue = recording_enqueue  # type: ignore[assignment]
    thread = threading.Thread(target=worker)
    thread.start()
    # The bridge drains inside the Tk loop, so it has to be pumped here.
    deadline = 200
    while thread.is_alive() and deadline > 0:
        app.gui.root.update()
        deadline -= 1
    thread.join(timeout=5)

    assert "error" not in seen, seen.get("error")
    assert seen["result"].state == SUBMIT_STARTED
    assert seen["ran_on_gui_thread"] is True, "the pipeline must not be touched from a worker"


def test_a_request_after_shutdown_is_refused_not_hung(app) -> None:
    """A stopped bridge must raise instead of blocking the server thread forever."""
    import threading

    app.bridge.start()
    app.bridge.stop()
    outcome: dict = {}

    def worker() -> None:
        try:
            outcome["result"] = app.submit_remote(URL_A, "mp3")
        except RuntimeError as exc:
            outcome["error"] = str(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "the caller was left hanging"
    assert "bridge" in outcome.get("error", "").lower()


# ----------------------------------------------------------------------
# Starting and stopping the phone interface
# ----------------------------------------------------------------------
def test_the_phone_interface_stays_off_unless_asked(app) -> None:
    """It lets other devices start downloads, so it must never be on by accident."""
    assert app.config.remote_enabled is False
    assert app.start_remote() is False
    assert app._remote is None
    assert app.remote_url() == ""


def test_switching_it_on_starts_a_server(app) -> None:
    app.config.remote_enabled = True
    app.config.remote_bind = "127.0.0.1"
    app.config.remote_port = 0
    try:
        assert app.start_remote() is True
        assert app._remote.running
        assert app._remote.port > 0
    finally:
        app.stop_remote()
    assert app._remote is None


def test_a_token_is_generated_and_stored(app) -> None:
    """Without a secret anybody on the network could start downloads."""
    app.config.remote_enabled = True
    app.config.remote_bind = "127.0.0.1"
    app.config.remote_port = 0
    app.config.remote_token = ""
    try:
        assert app.start_remote()
        assert len(app.config.remote_token) >= 24
        from clipster.config import Config

        assert Config.load(app.config.path).remote_token == app.config.remote_token
    finally:
        app.stop_remote()


def test_starting_twice_keeps_the_first_server(app) -> None:
    app.config.remote_enabled = True
    app.config.remote_bind = "127.0.0.1"
    app.config.remote_port = 0
    try:
        assert app.start_remote()
        first = app._remote
        assert app.start_remote() is False
        assert app._remote is first
    finally:
        app.stop_remote()


def test_stopping_a_server_that_never_started_is_harmless(app) -> None:
    app.stop_remote()


def test_the_url_for_the_phone_carries_the_token(app) -> None:
    app.config.remote_enabled = True
    app.config.remote_bind = "0.0.0.0"
    app.config.remote_port = 0
    try:
        assert app.start_remote()
        url = app.remote_url()
        if not url:
            pytest.skip("this machine has no route to a network")
        assert url.startswith("http://")
        assert "token=" + app.config.remote_token in url
        assert str(app._remote.port) in url
        assert "0.0.0.0" not in url, "a phone cannot dial the bind address"
    finally:
        app.stop_remote()


def test_a_local_only_interface_does_not_advertise_a_network_address(app) -> None:
    """It would look inviting and then refuse every connection from the phone."""
    app.config.remote_enabled = True
    app.config.remote_bind = "127.0.0.1"
    app.config.remote_port = 0
    try:
        assert app.start_remote()
        assert app.remote_url().startswith("http://127.0.0.1:")
    finally:
        app.stop_remote()


# ----------------------------------------------------------------------
# Streaming, operated from the phone
# ----------------------------------------------------------------------
@pytest.fixture()
def streaming(app):
    """Return the Streaming page with its playback calls recorded."""
    page = app.gui.view.discover
    assert page is not None
    calls: list = []
    for name in ("play_at", "toggle_play", "stop_playback", "play_next", "play_previous",
                 "like_current", "dislike_current", "hide_current", "download_current",
                 "maybe_extend"):
        setattr(page, name, (lambda n: (lambda *a, **k: calls.append(n)))(name))
    seeks: list = []
    page.player.seek = lambda seconds: seeks.append(seconds) or True
    return page, calls, seeks


def _tracks(count: int = 3):
    """Return believable Streaming results."""
    from clipster.discover import DiscoverTrack

    # Real YouTube ids are exactly 11 characters, and the code checks that.
    return [DiscoverTrack(url="https://youtu.be/aaaaaaaaaa{0}".format(index),
                          video_id="aaaaaaaaaa{0}".format(index),
                          title="Song {0}".format(index),
                          uploader="Artist {0}".format(index), duration=180 + index)
            for index in range(count)]


def test_the_streaming_state_is_readable(app, streaming) -> None:
    state = app.discover_remote_state()
    assert state["available"] is True
    assert state["tracks"] == []
    assert state["index"] == -1


def test_the_queue_reaches_the_phone(app, streaming) -> None:
    page, _, _ = streaming
    page.player.set_playlist(_tracks(3))
    state = app.discover_remote_state()
    assert [item["title"] for item in state["tracks"]] == ["Song 0", "Song 1", "Song 2"]
    assert state["tracks"][0]["uploader"] == "Artist 0"
    assert state["tracks"][0]["duration"] == 180


def test_the_player_shape_is_read_correctly(app, streaming) -> None:
    """tracks/index/playing are properties, position/duration are methods.

    Calling a property (or reading a method) silently produced a broken state
    until an integration test hit it.
    """
    page, _, _ = streaming
    page.player.set_playlist(_tracks(2))
    state = app.discover_remote_state()
    assert isinstance(state["tracks"], list)
    assert isinstance(state["index"], int)
    assert isinstance(state["playing"], bool)
    assert isinstance(state["position"], float)
    assert isinstance(state["duration"], float)
    assert isinstance(state["can_seek"], bool)


@pytest.mark.parametrize("command,method", [
    ("toggle", "toggle_play"),
    ("stop", "stop_playback"),
    ("next", "play_next"),
    ("previous", "play_previous"),
    ("like", "like_current"),
    ("dislike", "dislike_current"),
    ("hide", "hide_current"),
    ("download", "download_current"),
    ("extend", "maybe_extend"),
])
def test_every_command_reaches_the_page(app, streaming, command: str, method: str) -> None:
    from clipster.terms import accept_streaming_terms

    page, calls, _ = streaming
    accept_streaming_terms(app.config)
    result = app.discover_remote_command(command)
    assert result["ok"] is True, result
    assert calls == [method], calls


def test_playing_a_queue_position(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, calls, _ = streaming
    accept_streaming_terms(app.config)
    page.player.set_playlist(_tracks(3))
    assert app.discover_remote_command("play", index=1)["ok"] is True
    assert calls == ["play_at"]


def test_an_impossible_position_toggles_instead(app, streaming) -> None:
    """Better than an error: the phone's queue may be one poll out of date."""
    from clipster.terms import accept_streaming_terms

    page, calls, _ = streaming
    accept_streaming_terms(app.config)
    page.player.set_playlist(_tracks(2))
    app.discover_remote_command("play", index=99)
    assert calls == ["toggle_play"]


def test_seeking_reaches_the_player(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    _, _, seeks = streaming
    accept_streaming_terms(app.config)
    assert app.discover_remote_command("seek", seconds=42.5)["ok"] is True
    assert seeks == [42.5]


def test_an_unknown_command_is_refused(app, streaming) -> None:
    _, calls, _ = streaming
    result = app.discover_remote_command("rm -rf /")
    assert result["ok"] is False
    assert result["error"] == "unknown_command"
    assert calls == []


def test_streaming_needs_its_terms_and_never_asks_by_remote(app, streaming, monkeypatch) -> None:
    """The question is a modal on the PC: asking would hang the phone's request."""
    _, calls, _ = streaming
    monkeypatch.setattr(app.gui, "ask_terms_acceptance",
                        lambda **kwargs: pytest.fail("a dialog was opened for a remote request"))
    for command in ("refresh", "play", "toggle", "next", "download", "extend"):
        result = app.discover_remote_command(command)
        assert result["ok"] is False, command
        assert result["error"] == "terms_required", (command, result)
    assert calls == [], calls


def test_stopping_works_without_the_terms(app, streaming) -> None:
    """Stopping something already playing takes nothing new from YouTube."""
    _, calls, _ = streaming
    assert app.discover_remote_command("stop")["ok"] is True
    assert calls == ["stop_playback"]


def test_the_answer_carries_the_new_state(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    accept_streaming_terms(app.config)
    result = app.discover_remote_command("stop")
    assert isinstance(result["state"], dict)
    assert "tracks" in result["state"]


# ----------------------------------------------------------------------
# Volume on the host
# ----------------------------------------------------------------------
def test_the_volume_is_reported(app, streaming) -> None:
    page, _, _ = streaming
    page.player.volume = lambda: 42
    page.player.volume_controllable = lambda: True
    state = app.discover_remote_state()
    assert state["volume"] == 42
    assert state["volume_controllable"] is True


def test_an_uncontrollable_backend_says_so(app, streaming) -> None:
    """ffplay takes its volume only at start; a slider would do nothing."""
    page, _, _ = streaming
    page.player.volume = lambda: None
    page.player.volume_controllable = lambda: False
    state = app.discover_remote_state()
    assert state["volume"] is None
    assert state["volume_controllable"] is False


def test_setting_the_volume_reaches_the_player(app, streaming) -> None:
    page, _, _ = streaming
    seen: list = []
    page.player.set_volume = lambda percent: seen.append(percent) or True
    assert app.discover_remote_command("volume", seconds=35)["ok"] is True
    assert seen == [35]


def test_the_volume_needs_no_streaming_terms(app, streaming) -> None:
    """Turning the sound down takes nothing new from YouTube."""
    page, _, _ = streaming
    seen: list = []
    page.player.set_volume = lambda percent: seen.append(percent) or True
    assert app.discover_remote_command("volume", seconds=10)["ok"] is True
    assert seen == [10]


@pytest.mark.parametrize("wanted,expected", [(-20, 0), (0, 0), (50, 50), (100, 100), (250, 100)])
def test_the_player_clamps_the_volume(wanted: int, expected: int) -> None:
    """Whatever arrives over the network, mpv only ever sees 0-100."""
    from clipster.player import DiscoverPlayer

    player = DiscoverPlayer()
    seen: list = []
    player._mpv_send = lambda command, expect_reply=True: seen.append(command) or True
    player.set_volume(wanted)
    assert seen == [["set_property", "volume", expected]]


# ----------------------------------------------------------------------
# Searching from a remote device
# ----------------------------------------------------------------------
def test_a_search_returns_results(app, streaming, monkeypatch) -> None:
    from clipster import app as app_module
    from clipster.terms import accept_streaming_terms

    accept_streaming_terms(app.config)
    monkeypatch.setattr(app_module.discover, "search_tracks",
                        lambda query, options=None, limit=12: _tracks(2))
    result = app.discover_remote_search("beatles")
    assert result["ok"] is True
    assert [item["title"] for item in result["results"]] == ["Song 0", "Song 1"]
    assert result["results"][0]["video_id"] == "aaaaaaaaaa0"


def test_an_empty_search_asks_youtube_nothing(app, streaming, monkeypatch) -> None:
    from clipster import app as app_module

    monkeypatch.setattr(app_module.discover, "search_tracks",
                        lambda *a, **k: pytest.fail("YouTube was asked for nothing"))
    for query in ("", "   ", None):
        assert app.discover_remote_search(query) == {"ok": True, "error": "", "results": []}


def test_a_search_needs_the_streaming_terms(app, streaming, monkeypatch) -> None:
    from clipster import app as app_module

    monkeypatch.setattr(app_module.discover, "search_tracks",
                        lambda *a, **k: pytest.fail("searched without consent"))
    result = app.discover_remote_search("beatles")
    assert result["ok"] is False
    assert result["error"] == "terms_required"


def test_a_failing_search_is_reported_not_raised(app, streaming, monkeypatch) -> None:
    from clipster import app as app_module
    from clipster.terms import accept_streaming_terms

    accept_streaming_terms(app.config)

    def explode(*args, **kwargs):
        raise RuntimeError("YouTube said no")

    monkeypatch.setattr(app_module.discover, "search_tracks", explode)
    result = app.discover_remote_search("beatles")
    assert result["ok"] is False
    assert "YouTube said no" in result["error"]


def test_the_search_delay_is_published(app, streaming) -> None:
    """The device has to know how long to wait after the last keystroke."""
    app.config.remote_search_delay_ms = 900
    state = app.discover_remote_state()
    assert state["search_delay_ms"] == 900


def test_an_absurd_search_delay_is_floored(app, streaming) -> None:
    app.config.remote_search_delay_ms = 0
    assert app.discover_remote_state()["search_delay_ms"] >= 200


# ----------------------------------------------------------------------
# Adding a search result to the queue
# ----------------------------------------------------------------------
def test_a_picked_result_is_queued_and_started(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, calls, _ = streaming
    accept_streaming_terms(app.config)
    result = app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", "Channel", 321)
    assert result["ok"] is True
    assert [item["title"] for item in result["state"]["tracks"]] == ["Picked"]
    assert calls == ["play_at"]


# ----------------------------------------------------------------------
# Where a picked result lands
# ----------------------------------------------------------------------
def test_a_pick_lands_right_after_the_playing_track(app, streaming) -> None:
    """Not at the end: a pick should be heard next, not after ten other songs."""
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.set_tracks(_tracks(4))
    page.player.set_playlist(page._tracks)
    page.player._index = 1                      # "Song 1" is playing
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", play=False)
    assert [item.title for item in page.player.tracks] == [
        "Song 0", "Song 1", "Picked", "Song 2", "Song 3"]


def test_a_pick_goes_first_while_nothing_has_played(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.set_tracks(_tracks(3))
    page.player.set_playlist(page._tracks)
    page.player._index = -1                     # nothing played yet
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", play=False)
    assert [item.title for item in page.player.tracks][0] == "Picked"


def test_a_pick_into_an_empty_queue_goes_first(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", play=False)
    assert [item.title for item in page.player.tracks] == ["Picked"]


def test_the_pick_is_the_one_that_gets_played(app, streaming) -> None:
    """The position handed to play_at has to be the inserted one."""
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.set_tracks(_tracks(4))
    page.player.set_playlist(page._tracks)
    page.player._index = 2
    started: list = []
    page.play_at = lambda index: started.append(index)
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked")
    assert started == [3]
    assert page.player.tracks[3].title == "Picked"


def test_the_running_track_keeps_playing_when_one_is_inserted(app, streaming) -> None:
    """set_playlist would stop the player; inserting must not."""
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.set_tracks(_tracks(3))
    page.player.set_playlist(page._tracks)
    page.player._index = 1
    stopped: list = []
    page.player.stop = lambda: stopped.append(True)
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", play=False)
    assert stopped == [], "the player was stopped"
    assert page.player.current.title == "Song 1", "it lost track of what is playing"


def test_an_already_queued_pick_is_played_where_it_is(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.set_tracks(_tracks(3))
    page.player.set_playlist(page._tracks)
    page.player._index = 0
    started: list = []
    page.play_at = lambda index: started.append(index)
    app.discover_remote_enqueue("aaaaaaaaaa2", "Song 2")
    assert len(page.player.tracks) == 3, "it must not be queued twice"
    assert started == [2]


def test_queueing_without_playing(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, calls, _ = streaming
    accept_streaming_terms(app.config)
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", play=False)
    assert calls == []
    assert [item.video_id for item in page.player.tracks] == ["bbbbbbbbbbb"]


def test_the_same_track_is_not_queued_twice(app, streaming) -> None:
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked", play=False)
    app.discover_remote_enqueue("bbbbbbbbbbb", "Picked again", play=False)
    assert [item.video_id for item in page.player.tracks] == ["bbbbbbbbbbb"]


@pytest.mark.parametrize("video_id", ["", "short", "waytoolongforanid", "../../etc/passwd", None])
def test_only_a_real_video_id_is_queued(app, streaming, video_id) -> None:
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    result = app.discover_remote_enqueue(video_id or "", "Nope")
    assert result["ok"] is False
    assert page.player.tracks == []


def test_queueing_needs_the_streaming_terms(app, streaming) -> None:
    page, calls, _ = streaming
    result = app.discover_remote_enqueue("bbbbbbbbbbb", "Picked")
    assert result["ok"] is False
    assert result["error"] == "terms_required"
    assert calls == []


# ----------------------------------------------------------------------
# The audio stream a device plays itself
# ----------------------------------------------------------------------
def test_only_a_queued_track_can_be_resolved(app, streaming) -> None:
    """Otherwise this would be an open resolver for any video id sent to it."""
    from clipster.terms import accept_streaming_terms

    accept_streaming_terms(app.config)
    assert app.discover_remote_audio("zzzzzzzzzzz") == ("", {})
    assert app.discover_remote_audio("") == ("", {})


def test_resolving_needs_the_streaming_terms(app, streaming, monkeypatch) -> None:
    page, _, _ = streaming
    page.player.set_playlist(_tracks(1))
    monkeypatch.setattr("clipster.player.resolve_stream",
                        lambda *a, **k: pytest.fail("resolved without consent"))
    assert app.discover_remote_audio("aaaaaaaaaa0") == ("", {})


def test_a_resolved_url_is_reused(app, streaming, monkeypatch) -> None:
    """Resolving costs a second or two; a phone must not pay it twice."""
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.player.set_playlist(_tracks(1))
    calls: list = []
    monkeypatch.setattr("clipster.player.resolve_stream",
                        lambda *a, **k: calls.append(1) or ("https://example.invalid/audio.m4a", {}))
    first = app.discover_remote_audio("aaaaaaaaaa0")
    second = app.discover_remote_audio("aaaaaaaaaa0")
    assert first == second == ("https://example.invalid/audio.m4a", {})
    assert len(calls) == 1


def test_the_browser_format_prefers_what_safari_can_play(app, streaming, monkeypatch) -> None:
    """Opus in WebM plays on Android but not on the iPhone."""
    from clipster.player import BROWSER_AUDIO_FORMAT
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.player.set_playlist(_tracks(1))
    seen: dict = {}

    def resolve(url, options=None, *, prefer_video=True, format_selector=""):
        seen["format"] = format_selector
        return "https://example.invalid/audio.m4a", {}

    monkeypatch.setattr("clipster.player.resolve_stream", resolve)
    app.discover_remote_audio("aaaaaaaaaa0")
    assert seen["format"] == BROWSER_AUDIO_FORMAT
    assert "m4a" in seen["format"]


# ----------------------------------------------------------------------
# The queue follows the playing track
# ----------------------------------------------------------------------
def _long_queue(count: int = 20):
    """Return distinctly named tracks - similar titles get deduplicated."""
    from clipster.discover import DiscoverTrack

    names = ["Bohemian Rhapsody", "Smells Like Teen Spirit", "Billie Jean", "Hotel California",
             "Stairway To Heaven", "Purple Haze", "Imagine", "Like A Rolling Stone",
             "Hey Jude", "Wish You Were Here", "London Calling", "Sweet Child O Mine",
             "Nothing Else Matters", "Under Pressure", "Riders On The Storm", "Paint It Black",
             "Comfortably Numb", "Whole Lotta Love", "Space Oddity", "Heroes"]
    return [DiscoverTrack(url="https://youtu.be/qqqqqqqqq{0:02d}".format(index),
                          video_id="qqqqqqqqq{0:02d}".format(index), title=name,
                          uploader="Artist {0}".format(index), duration=200 + index)
            for index, name in enumerate(names[:count])]


def test_centring_scrolls_the_queue(app, streaming) -> None:
    page, _, _ = streaming
    page.set_tracks(_long_queue())
    app.gui.root.update()
    page.centre_on(0)
    app.gui.root.update()
    at_top = page._canvas.yview()[0]
    page.centre_on(15)
    app.gui.root.update()
    assert page._canvas.yview()[0] > at_top, "the list did not follow the track"


def test_centring_puts_the_row_in_the_middle(app, streaming) -> None:
    page, _, _ = streaming
    page.set_tracks(_long_queue())
    app.gui.root.update()
    page.centre_on(12)
    app.gui.root.update()
    row = page._row_frames[12]
    middle = row.winfo_y() + row.winfo_height() / 2
    top, _ = page._canvas.yview()
    content = page._canvas.bbox("all")[3]
    view_middle = top * content + page._canvas.winfo_height() / 2
    assert abs(middle - view_middle) < 60, (middle, view_middle)


def test_centring_an_impossible_row_is_harmless(app, streaming) -> None:
    page, _, _ = streaming
    page.set_tracks(_long_queue(3))
    app.gui.root.update()
    page.centre_on(-1)
    page.centre_on(99)


def test_a_queue_that_fits_is_not_scrolled(app, streaming, monkeypatch) -> None:
    """With everything visible there is nothing to centre.

    The viewport height is forced: in a window that was never shown Tk reports
    one pixel, and then even two rows would look like they need scrolling.
    """
    page, _, _ = streaming
    page.set_tracks(_long_queue(3))
    app.gui.root.update()
    monkeypatch.setattr(page._canvas, "winfo_height", lambda: 5000)
    page.centre_on(2)
    app.gui.root.update()
    assert page._canvas.yview()[0] == 0.0


# ----------------------------------------------------------------------
# A searched track can be played before the queue caught up
# ----------------------------------------------------------------------
def test_a_searched_track_may_be_resolved_at_once(app, streaming, monkeypatch) -> None:
    """The tap that starts playback expires while the PC is still queueing."""
    from clipster import app as app_module
    from clipster.terms import accept_streaming_terms

    accept_streaming_terms(app.config)
    monkeypatch.setattr(app_module.discover, "search_tracks",
                        lambda query, options=None, limit=12: _tracks(1))
    monkeypatch.setattr("clipster.player.resolve_stream",
                        lambda *a, **k: ("https://example.invalid/a.m4a", {"User-Agent": "yt"}))
    # Not in the queue - only offered by the search.
    assert app.discover_remote_audio("aaaaaaaaaa0") == ("", {})
    app.discover_remote_search("anything")
    url, headers = app.discover_remote_audio("aaaaaaaaaa0")
    assert url == "https://example.invalid/a.m4a"
    assert headers == {"User-Agent": "yt"}


def test_an_id_no_search_ever_offered_stays_refused(app, streaming, monkeypatch) -> None:
    from clipster import app as app_module
    from clipster.terms import accept_streaming_terms

    accept_streaming_terms(app.config)
    monkeypatch.setattr(app_module.discover, "search_tracks",
                        lambda query, options=None, limit=12: _tracks(1))
    monkeypatch.setattr("clipster.player.resolve_stream",
                        lambda *a, **k: pytest.fail("resolved an id nobody offered"))
    app.discover_remote_search("anything")
    assert app.discover_remote_audio("zzzzzzzzzzz") == ("", {})


def test_the_resolved_headers_travel_with_the_url(app, streaming, monkeypatch) -> None:
    """YouTube hands out per-format headers; a relay has to replay them."""
    from clipster.terms import accept_streaming_terms

    page, _, _ = streaming
    accept_streaming_terms(app.config)
    page.player.set_playlist(_tracks(1))
    monkeypatch.setattr("clipster.player.resolve_stream",
                        lambda *a, **k: ("https://example.invalid/a.m4a",
                                         {"User-Agent": "Mozilla", "Accept": "*/*"}))
    url, headers = app.discover_remote_audio("aaaaaaaaaa0")
    assert headers["User-Agent"] == "Mozilla"
    # Cached with its headers, not just the URL.
    assert app.discover_remote_audio("aaaaaaaaaa0")[1]["Accept"] == "*/*"
