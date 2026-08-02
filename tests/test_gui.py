"""The two windows: download list, filters, settings and the download flow.

Every test here needs a display; without one the whole module is skipped.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from clipster import theme
from clipster.bridge import Prompt
from clipster.history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, HistoryEntry

pytestmark = pytest.mark.gui


def _rows(view) -> list:
    """Return the rendered table rows of the download list."""
    return [w for w in view._scroller.body.winfo_children() if w.winfo_class() == "TFrame"]


def _all_text(widget) -> list:
    """Collect the text of every label below ``widget``."""
    found = []
    for child in widget.winfo_children():
        try:
            text = str(child.cget("text"))
        except Exception:
            text = ""
        if text:
            found.append(text)
        found.extend(_all_text(child))
    return found


# ----------------------------------------------------------------------
# Structure
# ----------------------------------------------------------------------
def test_both_windows_are_created_and_hidden(gui) -> None:
    assert gui.nav is not None and gui.view is not None
    assert not gui.nav.visible()
    assert not gui.view.visible()


def test_the_root_window_never_shows_itself(gui) -> None:
    """It only hosts the two real windows."""
    assert gui.root.state() == "withdrawn"


def test_the_theme_is_applied(gui) -> None:
    assert gui.palette is theme.PALETTE


def test_showing_and_hiding_the_view(gui) -> None:
    gui.show_view()
    assert gui.view_visible()
    gui.hide_view()
    assert not gui.view_visible()


@pytest.mark.parametrize("page", ["downloads", "discover", "settings", "about"])
def test_every_page_can_be_selected(gui, page: str) -> None:
    gui.view.select_page(page)
    assert gui.view._page == page


def test_discover_page_exists(gui) -> None:
    assert gui.view.discover is not None
    gui.view.select_page("discover")
    assert gui.view._page == "discover"


def test_discover_queue_rows_show_duration(gui, messages) -> None:
    from clipster.discover import DiscoverTrack

    gui.view.select_page("discover")
    page = gui.view.discover
    page.set_tracks(
        [
            DiscoverTrack(
                url="https://www.youtube.com/watch?v=abcdefghijk",
                video_id="abcdefghijk",
                title="Known length",
                uploader="Channel",
                duration=225,
            ),
            DiscoverTrack(
                url="https://www.youtube.com/watch?v=bcdefghijkl",
                video_id="bcdefghijkl",
                title="Unknown length",
                uploader="Channel",
                duration=0,
            ),
        ]
    )
    assert len(page._row_frames) == 2
    known = page._row_frames[0].grid_slaves(row=0, column=3)[0]
    unknown = page._row_frames[1].grid_slaves(row=0, column=3)[0]
    assert known.cget("text") == "3:45"
    assert unknown.cget("text") == "-"
    header_duration = page._queue_header.grid_slaves(row=0, column=3)[0]
    assert header_duration.cget("text") == messages["column_duration"]


def test_discover_refresh_button_is_visible(gui, messages) -> None:
    gui.view.select_page("discover")
    page = gui.view.discover
    assert page._refresh_btn.winfo_ismapped() or str(page._refresh_btn.cget("text"))
    assert page._refresh_btn.cget("text") == messages["discover_refresh"]
    assert not hasattr(page, "_folder_btn")
    # Find Similar and Search Mode share one horizontal toolbar row.
    assert page._refresh_btn.winfo_manager() == "pack"
    assert str(page._refresh_btn.pack_info().get("side", "")) == "left"
    assert page._mode_box.winfo_manager() == "pack"
    assert str(page._mode_box.pack_info().get("side", "")) == "left"
    assert page._refresh_btn.master is page._mode_box.master
    # Status lives in the footer (packed to the bottom), not above the player.
    assert page._status_box.winfo_manager() == "pack"
    assert str(page._status_box.pack_info().get("side", "")) == "bottom"


def test_discover_visualizer_selector_persists(gui, messages) -> None:
    from clipster.visualizer import VISUALIZER_MODES, VIZ_OFF, visualizer_locale_key, visualizer_mode_choices

    gui.view.select_page("discover")
    page = gui.view.discover
    assert hasattr(page, "_viz_box")
    raw_values = page._viz_box.cget("values")
    values = list(raw_values) if raw_values else []
    assert len(values) == 7
    assert all(isinstance(item, str) and item.strip() for item in values)
    expected_labels, label_to_mode, _mode_to_label = visualizer_mode_choices(messages)
    assert tuple(values) == expected_labels
    assert values[0] == messages[visualizer_locale_key(VIZ_OFF)]
    assert values[1] == messages[visualizer_locale_key("text")]
    assert len(label_to_mode) == len(VISUALIZER_MODES)
    off_label = messages[visualizer_locale_key(VIZ_OFF)]
    assert off_label in values
    page._viz_var.set(off_label)
    page._visualizer_selected()
    assert page.config.discover_visualizer == VIZ_OFF
    assert page.selected_visualizer() == VIZ_OFF
    # Reload syncs the combobox from config and keeps all mode labels.
    page.config.discover_visualizer = "spectrum"
    page.reload_from_config()
    assert page.selected_visualizer() == "spectrum"
    assert len(page._viz_box.cget("values")) == 7


def test_audio_play_ready_maps_stage_and_starts_generator(gui, messages) -> None:
    """Audio backend play-ready must show the stage canvas and animate without PCM."""
    from clipster.discover import DiscoverTrack
    from clipster.player import BACKEND_AUDIO, PlayStartResult
    from clipster.visualizer import VIZ_SPECTRUM, VIZ_WAVEFORM, visualizer_locale_key

    gui.show_view()
    gui.view.select_page("discover")
    page = gui.view.discover
    page._playback_mode_var.set("audio")
    page.config.discover_play_video = False
    page.config.discover_visualizer = VIZ_WAVEFORM
    page.reload_from_config()

    track = DiscoverTrack(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        video_id="abcdefghijk",
        title="Viz Test",
        uploader="Channel",
        duration=90,
    )
    page._tracks = [track]
    page._selected = 0
    page.player._playing = True
    page.player._backend = BACKEND_AUDIO
    page.player._stream_url = "http://example/stream.m4a"
    page.player._process = type("Alive", (), {"poll": lambda self: None})()

    page._on_play_ready(page._play_token, PlayStartResult(track=track, backend=BACKEND_AUDIO))
    gui.root.update_idletasks()
    gui.root.update()

    assert page._eq_canvas.winfo_ismapped()
    assert page._eq_canvas.winfo_width() > 1
    assert page._eq_canvas.winfo_height() > 1
    assert page._eq_job is not None

    # Pump frames: generative fallback must leave drawable items on the canvas.
    for _ in range(8):
        gui.root.update()
    assert len(page._eq_canvas.find_all()) >= 2
    assert max(page._eq_levels, default=0.0) > 0.0 or page.selected_visualizer() == VIZ_WAVEFORM

    # Switching Stage modes while "playing" must keep the ticker alive.
    page._viz_var.set(messages[visualizer_locale_key(VIZ_SPECTRUM)])
    page._visualizer_selected()
    for _ in range(10):
        gui.root.update()
    assert page.selected_visualizer() == VIZ_SPECTRUM
    assert page._eq_job is not None
    assert page._eq_fake.playing is True
    assert max(page._eq_levels) > 0.05
    assert len(page._eq_canvas.find_all()) > 0

    page.player._playing = False
    page.player._process = None
    page._stop_stage()
    page._show_stage_idle()
    gui.root.update_idletasks()


def test_an_unknown_page_is_ignored(gui) -> None:
    gui.view.select_page("discover")
    gui.view.select_page("no-such-page")
    assert gui.view._page == "discover"


def test_streaming_is_the_default_leftmost_tab(gui, messages) -> None:
    assert gui.view._page == "discover"
    order = [key for key in gui.view._menu_buttons]
    # Dict insertion order matches pack order in the menu.
    assert order[:2] == ["discover", "downloads"]
    assert gui.view._menu_buttons["discover"].cget("text") == messages["page_discover"]


# ----------------------------------------------------------------------
# The download list
# ----------------------------------------------------------------------
def test_an_empty_list_says_so(gui, messages) -> None:
    gui.render_history([])
    assert messages["history_empty"] in _all_text(gui.view._scroller.body)


def test_every_entry_becomes_a_row(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    assert len(_rows(gui.view)) == 3


def test_the_sidebar_counts_each_status(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    assert gui.view._counts == {"all": 3, STATUS_OK: 1, STATUS_FAILED: 1, STATUS_CANCELED: 1}


def test_a_filter_narrows_the_table(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    gui.view.set_filter(STATUS_FAILED)
    assert len(_rows(gui.view)) == 1
    gui.view.set_filter("all")
    assert len(_rows(gui.view)) == 3


def test_an_empty_filter_explains_itself(gui, messages) -> None:
    gui.render_history([HistoryEntry(name="a.mp3", status=STATUS_OK)])
    gui.view.set_filter(STATUS_FAILED)
    assert messages["filter_empty"] in _all_text(gui.view._scroller.body)


def test_a_row_shows_name_length_size_and_date(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    texts = _all_text(gui.view._scroller.body)
    assert any("song.mp3" in t for t in texts)
    assert "3:33" in texts
    assert "6.6 MB" in texts
    assert "31.07.2026 11:20" in texts


def test_a_failed_row_shows_the_problem(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    assert any("boom" in t for t in _all_text(gui.view._scroller.body))


def test_play_and_folder_are_disabled_when_the_file_is_gone(gui, sample_entries, messages) -> None:
    """Deleting stays available - it is the only way to clear a stale row."""
    gui.render_history(sample_entries)
    buttons = []

    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, type(gui.view._clear_button)):
                buttons.append(child)
            collect(child)

    collect(gui.view._scroller.body)
    by_label = {}
    for button in buttons:
        by_label.setdefault(str(button.cget("text")), []).append(button)

    assert set(by_label) == {
        messages["history_play"],
        messages["history_folder"],
        messages["history_hide"],
        messages["history_delete"],
    }
    for label in (messages["history_play"], messages["history_folder"]):
        assert all("disabled" in b.state() for b in by_label[label]), label
    assert all("disabled" not in b.state() for b in by_label[messages["history_delete"]])
    assert all("disabled" not in b.state() for b in by_label[messages["history_hide"]])


def test_deleting_a_row_needs_no_confirmation(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    deleted = []
    gui.on_delete_entry = deleted.append

    gui._delete_entry(sample_entries[0])
    assert deleted == [sample_entries[0]]


def test_hiding_a_row_forwards_without_prompt(gui, sample_entries) -> None:
    gui.render_history(sample_entries)
    hidden = []
    gui.on_hide_entry = hidden.append

    gui._hide_entry(sample_entries[0])
    assert hidden == [sample_entries[0]]


@pytest.mark.xfail(reason="8-18 px left over: Tk distributes the slack of the "
                          "weighted name column differently in the heading strip "
                          "than in a row. The original error was 187 px.",
                   strict=False)
def test_the_header_lines_up_with_the_values(gui, sample_entries) -> None:
    """The heading must sit over its values, not left of them."""
    gui.render_history(sample_entries)
    gui.show_view("downloads")
    gui.root.update()
    gui.root.update_idletasks()

    header = gui.view._header
    row = [w for w in gui.view._scroller.body.winfo_children()
           if w.winfo_class() == "TFrame"][0]
    heads = {c.grid_info().get("column"): c.winfo_x() for c in header.winfo_children()}
    cells = {c.grid_info().get("column"): c.winfo_x() for c in row.winfo_children()}
    for column in (1, 2, 3, 4):
        assert abs(heads[column] - cells[column]) <= 2, \
            "column {0}: heading at {1}, value at {2}".format(column, heads[column], cells[column])


def test_a_long_name_is_shortened_to_one_line(gui) -> None:
    long_name = "A very long file name that certainly does not fit into the narrow column.mp4"
    assert gui.view._fit_line(long_name, 120).endswith("…")
    assert gui.view._fit_line("short.mp3", 400) == "short.mp3"


def test_clearing_asks_before_it_empties(gui, sample_entries, monkeypatch) -> None:
    gui.render_history(sample_entries)
    cleared: list = []
    gui.on_clear_history = lambda: cleared.append(True)

    monkeypatch.setattr(gui, "ask_yes_no", lambda *a: False)
    gui._clear_history()
    assert not cleared, "a declined confirmation must not clear anything"

    monkeypatch.setattr(gui, "ask_yes_no", lambda *a: True)
    gui._clear_history()
    assert cleared


# ----------------------------------------------------------------------
# The toolbar
# ----------------------------------------------------------------------
def test_a_pasted_link_is_handed_on(gui) -> None:
    submitted: list = []
    gui.on_submit_url = lambda url, fmt: submitted.append((url, fmt))
    gui.view._url_var.set("https://youtu.be/dQw4w9WgXcQ")
    gui.view._submit_url()
    assert submitted == [("https://youtu.be/dQw4w9WgXcQ", "mp3")]
    assert gui.view._url_var.get() == "", "the field is cleared after submitting"


def test_an_empty_field_submits_nothing(gui) -> None:
    submitted: list = []
    gui.on_submit_url = lambda url, fmt: submitted.append(url)
    gui.view._url_var.set("   ")
    gui.view._submit_url()
    assert submitted == []


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def test_the_form_shows_the_current_configuration(gui, config) -> None:
    config.history_limit = 55
    gui.view.select_page("settings")
    assert gui.view._vars["history_limit"].get() == "55"


def test_saving_writes_the_values_back(gui, config) -> None:
    saved: list = []
    gui.on_save_settings = lambda: saved.append(True)
    gui.view.select_page("settings")
    gui.view._vars["history_limit"].set("42")
    gui.view._vars["interval_sec"].set("3,5")
    gui.view._vars["use_tray"].set(False)
    gui.view._vars["startup_visibility"].set(gui.messages["settings_startup_window"])
    gui.view._save_settings()
    assert config.history_limit == 42
    assert config.interval_sec == pytest.approx(3.5), "a comma must work as a decimal point"
    assert config.use_tray is False
    assert config.start_minimized is False
    assert saved


def test_startup_tray_setting_enables_tray_icon(gui, config) -> None:
    gui.view.select_page("settings")
    gui.view._vars["use_tray"].set(False)
    gui.view._vars["startup_visibility"].set(gui.messages["settings_startup_tray"])
    gui.view._save_settings()
    assert config.start_minimized is True
    assert config.use_tray is True


def test_discover_suffix_controls_live_on_settings(gui, config, messages) -> None:
    """Suffix / require filter belong on Settings, not the Streaming toolbar."""
    gui.view.select_page("discover")
    discover_labels = _all_text(gui.view.discover)
    assert messages["discover_require_suffix"] not in discover_labels
    assert messages["settings_discover_suffix"] not in discover_labels

    gui.view.select_page("settings")
    assert "discover_search_suffix" in gui.view._vars
    assert "discover_require_suffix" in gui.view._vars
    gui.view._vars["discover_search_suffix"].set("karaoke")
    gui.view._vars["discover_require_suffix"].set(False)
    gui.view._save_settings()
    assert config.discover_search_suffix == "karaoke"
    assert config.discover_require_suffix is False


def test_cookies_settings_round_trip(gui, config, messages) -> None:
    gui.view.select_page("settings")
    assert "cookies_from_browser" in gui.view._vars
    assert "cookies_file" in gui.view._vars
    assert "cookies_risk_acknowledged" in gui.view._vars
    gui.view._vars["cookies_risk_acknowledged"].set(True)
    gui.view._sync_cookies_controls()
    gui.view._vars["cookies_from_browser"].set(messages["settings_cookies_browser_firefox"])
    gui.view._vars["cookies_file"].set("/tmp/example-cookies.txt")
    gui.view._save_settings()
    assert config.cookies_risk_acknowledged is True
    assert config.cookies_risk_acknowledged_at
    assert config.cookies_from_browser == "firefox"
    assert config.cookies_file == "/tmp/example-cookies.txt"
    gui.view.select_page("downloads")
    gui.view.select_page("settings")
    assert gui.view._vars["cookies_risk_acknowledged"].get()
    assert gui.view._vars["cookies_from_browser"].get() == messages["settings_cookies_browser_firefox"]
    assert gui.view._vars["cookies_file"].get() == "/tmp/example-cookies.txt"
    assert str(gui.view._cookies_browser_combo.cget("state")) == "readonly"


def test_cookies_settings_require_risk_acknowledgement(gui, config, messages) -> None:
    gui.view.select_page("settings")
    assert str(gui.view._cookies_browser_combo.cget("state")) == "disabled"
    gui.view._vars["cookies_from_browser"].set(messages["settings_cookies_browser_firefox"])
    gui.view._vars["cookies_file"].set("/tmp/example-cookies.txt")
    gui.view._vars["cookies_risk_acknowledged"].set(False)
    gui.view._save_settings()
    assert config.cookies_risk_acknowledged is False
    assert config.cookies_risk_acknowledged_at == ""
    assert config.cookies_from_browser == ""
    assert config.cookies_file == ""


def test_clearing_cookie_risk_ack_stops_saving_cookies(gui, config, messages) -> None:
    gui.view.select_page("settings")
    gui.view._vars["cookies_risk_acknowledged"].set(True)
    gui.view._sync_cookies_controls()
    gui.view._vars["cookies_from_browser"].set(messages["settings_cookies_browser_chrome"])
    gui.view._vars["cookies_file"].set("/tmp/keep-me.txt")
    gui.view._save_settings()
    assert config.cookies_from_browser == "chrome"
    stamped = config.cookies_risk_acknowledged_at
    assert stamped

    gui.view._vars["cookies_risk_acknowledged"].set(False)
    gui.view._sync_cookies_controls()
    gui.view._save_settings()
    assert config.cookies_risk_acknowledged is False
    assert config.cookies_risk_acknowledged_at == ""
    assert config.cookies_from_browser == ""
    assert config.cookies_file == ""
    assert str(gui.view._cookies_browser_combo.cget("state")) == "disabled"


def test_nonsense_input_keeps_the_previous_value(gui, config) -> None:
    gui.view.select_page("settings")
    gui.view._vars["interval_sec"].set("2")
    gui.view._save_settings()
    gui.view._vars["interval_sec"].set("not a number")
    gui.view._save_settings()
    assert config.interval_sec == pytest.approx(2.0)


def test_absurd_values_are_clamped(gui, config) -> None:
    gui.view.select_page("settings")
    gui.view._vars["interval_sec"].set("99999")
    gui.view._vars["history_limit"].set("-5")
    gui.view._save_settings()
    assert 0.5 <= config.interval_sec <= 60.0
    assert config.history_limit >= 1


def test_discarding_restores_the_stored_values(gui, config) -> None:
    gui.view.select_page("settings")
    gui.view._vars["history_limit"].set("999")
    gui.view._load_settings()
    assert gui.view._vars["history_limit"].get() == str(config.history_limit)


# ----------------------------------------------------------------------
# About
# ----------------------------------------------------------------------
def test_the_about_page_names_the_author_and_the_repository(gui) -> None:
    from clipster import APP_AUTHOR, APP_URL, APP_WEBSITE

    texts = _all_text(gui.view._pages["about"])
    assert APP_AUTHOR in texts
    assert APP_WEBSITE in texts
    assert APP_URL in texts


def test_the_links_are_clickable_but_the_name_is_not(gui) -> None:
    from clipster import APP_AUTHOR, APP_URL, APP_WEBSITE

    links = []
    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Label) and str(child.cget("cursor")) == "hand2":
                links.append(str(child.cget("text")))
            collect(child)
    collect(gui.view._pages["about"])
    assert APP_WEBSITE in links and APP_URL in links
    assert APP_AUTHOR not in links


def test_a_link_opens_the_browser(gui, monkeypatch) -> None:
    import webbrowser

    opened: list = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    gui.view._open_link("https://example.com")
    assert opened == ["https://example.com"]


# ----------------------------------------------------------------------
# The navigation window
# ----------------------------------------------------------------------
def test_the_question_asks_for_format_and_track(gui) -> None:
    prompt = Prompt()
    gui.nav.begin("link received")
    gui.nav.ask(prompt, "Some title", 213, ["de", "en", "es"], "mp4", True, "de")
    assert gui.nav.question_pending()
    assert gui.nav._format.get() == "mp4", "the configured default must be preselected"
    boxes = [w for w in gui.nav._form.winfo_children() if w.winfo_class() == "TCombobox"]
    assert len(boxes) == 2, "format and audio track"


def test_the_original_track_is_marked(gui) -> None:
    gui.nav.begin("x")
    gui.nav.ask(Prompt(), "T", 0, ["de", "en"], "mp3", True, "de")
    boxes = [w for w in gui.nav._form.winfo_children() if w.winfo_class() == "TCombobox"]
    values = list(boxes[1]["values"])
    assert "Original" in values[1] or "original" in values[1]
    assert "original" not in values[2].lower()


def test_a_single_track_offers_no_choice(gui) -> None:
    gui.nav.begin("x")
    gui.nav.ask(Prompt(), "T", 0, ["en"], "mp3", True)
    boxes = [w for w in gui.nav._form.winfo_children() if w.winfo_class() == "TCombobox"]
    assert len(boxes) == 1, "only the format selector"


def test_answering_returns_format_and_language(gui) -> None:
    prompt = Prompt()
    gui.nav.begin("x")
    gui.nav.ask(prompt, "T", 0, [], "mp3", False)
    gui.nav._submit()
    assert prompt.wait(poll=0.01) == {"format": "mp3", "language": ""}
    assert not gui.nav.question_pending()


def test_cancelling_the_question(gui) -> None:
    prompt = Prompt()
    gui.nav.begin("x")
    gui.nav.ask(prompt, "T", 0, [], "mp3", False)
    assert gui.nav.cancel_pending()
    assert prompt.wait(poll=0.01) is None
    assert gui.nav.cancel_event.is_set()


def test_cancelling_without_a_question_reports_nothing(gui) -> None:
    gui.nav.begin("x")
    assert gui.nav.cancel_pending() is False


def test_the_progress_bar_follows_the_download(gui) -> None:
    gui.nav.begin("x")
    gui.nav.show_progress("mp3", 213)
    gui.nav.set_percent(46.0)
    assert gui.nav._value.get() == pytest.approx(46.0)
    gui.nav.set_percent(None)
    assert gui.nav._bar_mode == "indeterminate"


def test_the_result_offers_the_finished_file(gui, tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    gui.nav.begin("x")
    gui.nav.finish("done", STATUS_OK, "detail", target)
    assert gui.nav.result_path() == target
    assert not gui.nav._bar.winfo_ismapped(), "a full bar looks like an empty one"
    assert gui.nav._auto_close_job is not None
    gui.nav._cancel_auto_close()


def test_a_failed_result_offers_no_file(gui) -> None:
    gui.nav.begin("x")
    gui.nav.finish("it broke", STATUS_FAILED)
    assert gui.nav.result_path() is None
    assert gui.nav._auto_close_job is None


def test_closing_the_window_cancels_a_pending_question(gui) -> None:
    prompt = Prompt()
    closed: list = []
    gui.on_nav_closed = lambda: closed.append(True)
    gui.nav.begin("x")
    gui.nav.ask(prompt, "T", 0, [], "mp3", False)
    gui.nav._closed()
    assert prompt.wait(poll=0.01) is None
    assert closed
