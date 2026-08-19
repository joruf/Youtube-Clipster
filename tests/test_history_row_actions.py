"""Getting a failed download out of the list has to stay possible.

The bug these tests pin down: the five row actions were word buttons, and the
German labels alone asked for 684 px.  The table never got that much at the
default window size, so Tk clipped the row at the right edge - and the two
buttons that fell off were Hide and Delete, the only ways to clear a failed
attempt away.  A failed row has no file to play and no folder to open, so those
were the *only* actions it had left.

The assertions are about geometry and reachability, not wording, so translations
stay free to change.
"""

from __future__ import annotations

import tkinter as tk
from typing import List

import pytest

from clipster import i18n, viewwindow
from clipster.history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, HistoryEntry

pytestmark = pytest.mark.gui

#: A window narrower than anything the user can drag it to.
_TOO_NARROW = "700x600"


def _failed() -> HistoryEntry:
    """Return the row from the bug report: no file, only an error."""
    return HistoryEntry(
        name="EPLAN Scripting #1: Visual Studio & API Setup - Der perfekte Start",
        path="",
        title="EPLAN Scripting #1: Visual Studio & API Setup - Der perfekte Start",
        url="https://www.youtube.com/watch?v=IQU38vyzsIs",
        media_format="mp3",
        duration=576,
        status=STATUS_FAILED,
        error_kind="noformat",
        error="YouTube refused to hand out this stream (HTTP 403).",
        finished_at="2026-08-19T21:28:37",
    )


def _action_buttons(view, index: int = 0) -> List[tk.Widget]:
    """Return the action buttons of one rendered row, left to right.

    :param view: The view window.
    :param index: Which row to look at.
    :return: The buttons, in the order :data:`_ROW_ACTIONS` lists them.
    """
    _separator, row, _entry = view._row_items[index]
    frames = [w for w in row.winfo_children() if w.winfo_class() == "TFrame"]
    actions = frames[-1]
    return [w for w in actions.winfo_children() if w.winfo_class() == "TButton"]


def _settle(gui) -> None:
    """Let Tk finish laying the window out."""
    for _ in range(8):
        gui.root.update_idletasks()
        gui.root.update()


def test_a_failed_row_offers_every_action(gui) -> None:
    gui.render_history([_failed()])
    gui.show_view("downloads")
    _settle(gui)
    assert len(_action_buttons(gui.view)) == len(viewwindow._ROW_ACTIONS)


def test_hide_and_delete_stay_usable_without_a_file(gui) -> None:
    """A failed row has no file - and is exactly the row that must be removable."""
    gui.render_history([_failed()])
    gui.show_view("downloads")
    _settle(gui)
    buttons = dict(zip((a[0] for a in viewwindow._ROW_ACTIONS), _action_buttons(gui.view)))
    assert "disabled" not in buttons["_on_hide_entry"].state()
    assert "disabled" not in buttons["_on_delete_entry"].state()
    # Play and Folder have nothing to act on and say so by being greyed out.
    assert "disabled" in buttons["_on_play_entry"].state()
    assert "disabled" in buttons["_on_reveal_entry"].state()


def test_pressing_delete_on_a_failed_row_reaches_the_handler(gui) -> None:
    removed: List[str] = []
    gui.on_delete_entry = lambda entry: removed.append(entry.name)
    entry = _failed()
    gui.render_history([entry])
    gui.show_view("downloads")
    _settle(gui)
    buttons = dict(zip((a[0] for a in viewwindow._ROW_ACTIONS), _action_buttons(gui.view)))
    buttons["_on_delete_entry"].invoke()
    assert removed == [entry.name]


def test_pressing_hide_on_a_failed_row_reaches_the_handler(gui) -> None:
    hidden: List[str] = []
    gui.on_hide_entry = lambda entry: hidden.append(entry.name)
    entry = _failed()
    gui.render_history([entry])
    gui.show_view("downloads")
    _settle(gui)
    buttons = dict(zip((a[0] for a in viewwindow._ROW_ACTIONS), _action_buttons(gui.view)))
    buttons["_on_hide_entry"].invoke()
    assert hidden == [entry.name]


@pytest.mark.parametrize("language", ["en", "de"])
def test_no_action_is_clipped_at_the_minimum_window_width(config, downloads, language) -> None:
    """The regression itself: the last buttons must be inside the window.

    Checked in both languages, because the labels used to decide the width and a
    translation could bring the problem straight back.
    """
    from clipster.gui import Gui

    config.language = language
    gui = Gui(i18n.load(language), config, downloads)
    try:
        gui.build_windows()
        gui.render_history([_failed()])
        gui.show_view("downloads")
        minimum_width, minimum_height = gui.view.window.wm_minsize()
        gui.view.window.geometry("{0}x{1}".format(minimum_width, minimum_height))
        _settle(gui)
        window = gui.view.window
        right_edge = window.winfo_rootx() + window.winfo_width()
        for name, button in zip(
            (action[0] for action in viewwindow._ROW_ACTIONS), _action_buttons(gui.view)
        ):
            end = button.winfo_rootx() + button.winfo_width()
            assert end <= right_edge, "{0} is clipped in {1} ({2} > {3})".format(
                name, language, end, right_edge
            )
    finally:
        gui.destroy()


def test_the_window_cannot_be_made_narrower_than_the_table(gui) -> None:
    """Tk clips a row instead of growing the window, so the minimum has to hold."""
    view = gui.view
    minimum_width, _height = view.window.wm_minsize()
    needed = (
        viewwindow._COL_BADGE
        + viewwindow._COL_NAME_MIN
        + sum(view._col_widths.values())
        + view._actions_width
    )
    assert minimum_width > needed


def test_the_symbol_buttons_cost_far_less_than_labels_did(gui) -> None:
    """The point of symbols: a width that no translation can blow up.

    684 px was the measured German figure that caused the clipping; anything in
    that region means the labels are back.
    """
    assert gui.view._actions_width < 320


def test_every_symbol_explains_itself(gui) -> None:
    """A symbol nobody can read is not an improvement over a clipped label."""
    messages = i18n.load("en")
    for _handler, glyph, tip_key in viewwindow._ROW_ACTIONS:
        assert glyph.strip(), "an action without a symbol"
        assert messages[tip_key].strip() != tip_key, "{0} has no text".format(tip_key)


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_tooltips_exist_in_every_language(language: str) -> None:
    messages = i18n.load(language)
    for _handler, _glyph, tip_key in viewwindow._ROW_ACTIONS:
        assert messages[tip_key].strip() != tip_key, "{0} missing in {1}".format(tip_key, language)


def test_hide_and_delete_are_told_apart(gui) -> None:
    """One keeps the file and one does not; the symbols must not be the same."""
    glyphs = {handler: glyph for handler, glyph, _tip in viewwindow._ROW_ACTIONS}
    assert glyphs["_on_hide_entry"] != glyphs["_on_delete_entry"]
    messages = i18n.load("en")
    tips = {handler: messages[tip] for handler, _glyph, tip in viewwindow._ROW_ACTIONS}
    assert tips["_on_hide_entry"] != tips["_on_delete_entry"]


def test_a_canceled_row_can_also_be_cleared_away(gui) -> None:
    """Canceling was a choice, but the row still has to be removable."""
    entry = HistoryEntry(
        name="stopped.mp4", media_format="mp4", status=STATUS_CANCELED,
        finished_at="2026-07-29T21:44:30",
    )
    gui.render_history([entry])
    gui.show_view("downloads")
    _settle(gui)
    buttons = dict(zip((a[0] for a in viewwindow._ROW_ACTIONS), _action_buttons(gui.view)))
    assert "disabled" not in buttons["_on_delete_entry"].state()
    assert "disabled" not in buttons["_on_hide_entry"].state()


def test_a_finished_row_keeps_every_file_action(gui, downloads) -> None:
    target = downloads / "song.mp3"
    target.write_bytes(b"audio")
    entry = HistoryEntry(
        name="song.mp3", path=str(target), media_format="mp3", duration=213,
        size=5, status=STATUS_OK, finished_at="2026-07-31T11:20:05",
    )
    gui.render_history([entry])
    gui.show_view("downloads")
    _settle(gui)
    buttons = dict(zip((a[0] for a in viewwindow._ROW_ACTIONS), _action_buttons(gui.view)))
    assert "disabled" not in buttons["_on_play_entry"].state()
    assert "disabled" not in buttons["_on_reveal_entry"].state()
    # Nothing failed, so there is nothing to retry.
    assert "disabled" in buttons["_on_retry_entry"].state()
