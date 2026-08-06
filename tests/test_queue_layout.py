"""The Streaming queue must stay a straight grid at any window width.

The bug these tests pin down: a ``ttk.Label`` asks for as many pixels as its
text needs, and ``grid`` sizes a column to the widest request in it.  One long
title therefore stretched its row far past the visible area and moved the
channel, length and button columns out of line with every other row.  Nothing
was ever shortened with an ellipsis because the label always got the width it
had asked for.

Every assertion here is about geometry, not about wording, so translations stay
free to change.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from clipster.discover import DiscoverTrack
from clipster.discover_page import _QUEUE_NARROW, _fit_line

SHORT_TITLE = "Short one"
LONG_TITLE = (
    "Sia - The Greatest (Lyrics) [Official Extended Ultra Deluxe Remastered "
    "Anniversary Edition featuring a very long tail that never fits anywhere]"
)
LONG_CHANNEL = "A Channel With A Really Long Name That Will Not Fit Either"


def _tracks() -> list:
    """Return three rows: short, very long, short again."""
    return [
        DiscoverTrack(
            url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
            video_id="aaaaaaaaaaa", title=SHORT_TITLE, uploader="Chan", duration=100,
        ),
        DiscoverTrack(
            url="https://www.youtube.com/watch?v=bbbbbbbbbbb",
            video_id="bbbbbbbbbbb", title=LONG_TITLE, uploader=LONG_CHANNEL, duration=225,
        ),
        DiscoverTrack(
            url="https://www.youtube.com/watch?v=ccccccccccc",
            video_id="ccccccccccc", title="Another short", uploader="Chan", duration=100,
        ),
    ]


def _columns(row: tk.Misc) -> dict:
    """Return ``{column: (x, width)}`` for one queue row."""
    found = {}
    for child in row.grid_slaves():
        info = child.grid_info()
        found[int(info["column"])] = (child.winfo_x(), child.winfo_width())
    return found


# ----------------------------------------------------------------------
# The text shortener itself - no display needed
# ----------------------------------------------------------------------
class _Font:
    """A font whose every character is exactly ten pixels wide."""

    @staticmethod
    def measure(text: str) -> int:
        return 10 * len(text)


def test_a_short_line_is_left_alone() -> None:
    assert _fit_line("hello", 500, _Font()) == "hello"


def test_a_long_line_is_cut_with_an_ellipsis() -> None:
    result = _fit_line("abcdefghij", 50, _Font())
    assert result.endswith("…")
    assert _Font.measure(result) <= 50


def test_whitespace_is_collapsed_before_measuring() -> None:
    assert _fit_line("  a \n b  ", 500, _Font()) == "a b"


def test_no_room_at_all_still_returns_something_drawable() -> None:
    assert _fit_line("abcdefghij", 5, _Font()) == "…"


def test_an_empty_title_survives() -> None:
    assert _fit_line("", 100, _Font()) == ""


# ----------------------------------------------------------------------
# The rendered grid
# ----------------------------------------------------------------------
pytestmark_gui = pytest.mark.gui


@pytest.mark.gui
def test_a_long_title_does_not_widen_its_row(gui) -> None:
    """The row asks for the same width whatever the title says.

    This is the root cause: as long as the requested widths match, grid gives
    every row the same column geometry and nothing can be pushed to the right.
    """
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.update_idletasks()

    widths = {row.winfo_reqwidth() for row in page._row_frames}
    assert len(widths) == 1, "rows disagree on their requested width: {0}".format(widths)


@pytest.mark.gui
def test_a_long_title_does_not_widen_the_title_cell(gui) -> None:
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.update_idletasks()

    requested = {label.winfo_reqwidth() for label in page._title_labels}
    assert len(requested) == 1, "title cells disagree: {0}".format(requested)


@pytest.mark.gui
def test_a_long_channel_does_not_widen_its_cell(gui) -> None:
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.update_idletasks()

    requested = {label.winfo_reqwidth() for label in page._channel_labels}
    assert len(requested) == 1, "channel cells disagree: {0}".format(requested)


@pytest.mark.gui
@pytest.mark.parametrize("window_width", [1600, 1200, 900, 700])
def test_every_row_uses_the_same_columns(gui, window_width: int) -> None:
    """Entries stand directly below one another at any window width."""
    gui.show_view()
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.geometry("{0}x700".format(window_width))
    for _ in range(6):
        gui.view.window.update_idletasks()
        gui.view.window.update()

    layouts = [_columns(row) for row in page._row_frames]
    first = layouts[0]
    for index, other in enumerate(layouts[1:], start=1):
        assert other == first, "row {0} is out of line at {1} px".format(index, window_width)


@pytest.mark.gui
@pytest.mark.parametrize("window_width", [1600, 1200, 900, 700])
def test_a_row_never_reaches_past_the_visible_area(gui, window_width: int) -> None:
    """No column may be pushed beyond the right edge of the queue."""
    gui.show_view()
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.geometry("{0}x700".format(window_width))
    for _ in range(6):
        gui.view.window.update_idletasks()
        gui.view.window.update()

    visible = page._canvas.winfo_width()
    for index, row in enumerate(page._row_frames):
        assert row.winfo_reqwidth() <= visible, (
            "row {0} needs {1} px but only {2} px are visible".format(
                index, row.winfo_reqwidth(), visible
            )
        )
        for column, (x, width) in _columns(row).items():
            assert x + width <= visible + 1, (
                "column {0} of row {1} ends at {2} px, past {3} px".format(
                    column, index, x + width, visible
                )
            )


@pytest.mark.gui
def test_a_title_that_does_not_fit_ends_in_an_ellipsis(gui) -> None:
    gui.show_view()
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.geometry("1000x700")
    for _ in range(6):
        gui.view.window.update_idletasks()
        gui.view.window.update()

    long_label = page._title_labels[1]
    shown = str(long_label.cget("text"))
    assert shown != LONG_TITLE
    assert shown.endswith("…")
    assert LONG_TITLE.startswith(shown[:-1].rstrip())


@pytest.mark.gui
def test_a_title_that_fits_is_shown_in_full(gui) -> None:
    gui.show_view()
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.geometry("1600x700")
    for _ in range(6):
        gui.view.window.update_idletasks()
        gui.view.window.update()

    assert str(page._title_labels[0].cget("text")) == SHORT_TITLE


@pytest.mark.gui
def test_the_full_title_is_kept_for_re_measuring(gui) -> None:
    """Widening the window must bring the cut-off text back."""
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    assert page._title_full[1] == LONG_TITLE


@pytest.mark.gui
def test_a_narrow_queue_drops_the_channel_column(gui) -> None:
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())

    page._apply_queue_width(_QUEUE_NARROW - 1)
    assert page._channel_visible is False
    for label in page._channel_labels:
        assert not label.grid_info(), "channel cell still occupies a grid slot"

    page._apply_queue_width(_QUEUE_NARROW + 200)
    assert page._channel_visible is True
    for label in page._channel_labels:
        assert label.grid_info(), "channel cell did not come back"


@pytest.mark.gui
def test_rows_added_while_narrow_keep_the_channel_hidden(gui) -> None:
    """Incremental Discover results must not undo the narrow layout."""
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks()[:1])
    page._apply_queue_width(_QUEUE_NARROW - 1)

    page.append_tracks(_tracks()[1:])
    assert page._channel_visible is False
    for label in page._channel_labels:
        assert not label.grid_info()


@pytest.mark.gui
def test_the_header_and_the_rows_share_one_column_layout(gui) -> None:
    gui.show_view()
    gui.view.select_page("discover")
    page = gui.view.discover
    page.restore_tracks(_tracks())
    gui.view.window.geometry("1400x700")
    for _ in range(6):
        gui.view.window.update_idletasks()
        gui.view.window.update()

    header = _columns(page._queue_header)
    row = _columns(page._row_frames[0])
    assert set(header) == set(row)
    for column in header:
        assert abs(header[column][0] - row[column][0]) <= 2, (
            "header column {0} is {1} px off".format(column, header[column][0] - row[column][0])
        )
