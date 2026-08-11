"""How the Streaming queue is played: shuffle, repeat and the sleep timer.

The rules live in :meth:`clipster.discover_page.DiscoverPage.next_index`, which
both the skip button and the auto-advance at the end of a track go through - so
a mode can never apply to one of them and not the other.
"""

from __future__ import annotations

import pytest

from clipster.discover import DiscoverTrack
from clipster.discover_page import REPEAT_ALL, REPEAT_OFF, REPEAT_ONE

pytestmark = pytest.mark.gui


def _tracks(count: int = 3):
    """Return a queue of believable tracks."""
    return [
        DiscoverTrack(url="https://youtu.be/aaaaaaaaaa{0}".format(index),
                      video_id="aaaaaaaaaa{0}".format(index),
                      title="Song {0}".format(index))
        for index in range(count)
    ]


@pytest.fixture()
def page(gui, monkeypatch):
    """Return the Streaming page with a queue and without real playback."""
    item = gui.view.discover
    assert item is not None
    monkeypatch.setattr(item, "play_at", lambda index: setattr(item, "_selected", index))
    item._tracks = _tracks()
    item.player.set_playlist(item._tracks)
    item._selected = 0
    item._reset_shuffle_bag()
    return item


# ----------------------------------------------------------------------
# In order
# ----------------------------------------------------------------------
def test_the_queue_plays_in_order_and_then_stops(page) -> None:
    assert page.next_index(automatic=True) == 1
    page._selected = 2
    assert page.next_index(automatic=True) is None


def test_repeat_all_starts_the_queue_over(page) -> None:
    page.set_repeat(REPEAT_ALL)
    page._selected = 2
    assert page.next_index(automatic=True) == 0


def test_repeat_one_plays_the_same_song_again(page) -> None:
    page.set_repeat(REPEAT_ONE)
    page._selected = 1
    assert page.next_index(automatic=True) == 1


def test_pressing_next_never_repeats_the_same_song(page) -> None:
    """Repeat-one is for a song that ended, not for a deliberate skip."""
    page.set_repeat(REPEAT_ONE)
    page._selected = 1
    assert page.next_index(automatic=False) == 2


# ----------------------------------------------------------------------
# Shuffle
# ----------------------------------------------------------------------
def test_shuffle_plays_every_song_before_repeating_one(page) -> None:
    page.set_shuffle(True)
    page._tracks = _tracks(6)
    page.player.set_playlist(page._tracks)
    page._selected = 0
    page._reset_shuffle_bag()

    drawn = []
    for _ in range(5):
        index = page.next_index(automatic=True)
        assert index is not None
        drawn.append(index)
        page._selected = index
    assert sorted(drawn) == [1, 2, 3, 4, 5], "each of the others exactly once"


def test_shuffle_does_not_hand_back_the_running_song(page) -> None:
    page.set_shuffle(True)
    page._selected = 1
    for _ in range(2):
        assert page.next_index(automatic=True) != 1


def test_shuffle_without_repeat_ends_after_one_round(page) -> None:
    page.set_shuffle(True)
    page._selected = 0
    for _ in range(len(page._tracks) - 1):
        index = page.next_index(automatic=True)
        assert index is not None
        page._selected = index
    assert page.next_index(automatic=True) is None


def test_shuffle_with_repeat_all_starts_a_new_round(page) -> None:
    page.set_shuffle(True)
    page.set_repeat(REPEAT_ALL)
    page._selected = 0
    for _ in range(len(page._tracks) - 1):
        page._selected = page.next_index(automatic=True)
    assert page.next_index(automatic=True) is not None, "it keeps going"


def test_a_new_queue_starts_a_new_random_round(page) -> None:
    page.set_shuffle(True)
    page._selected = 0
    page.next_index(automatic=True)
    page.set_tracks(_tracks(4))
    assert page._order._bag == [] and page._order._started is False


def test_a_single_track_repeats_only_when_asked_to(page) -> None:
    page.set_shuffle(True)
    page._tracks = _tracks(1)
    page.player.set_playlist(page._tracks)
    page._selected = 0
    page._reset_shuffle_bag()
    assert page.next_index(automatic=True) is None
    page.set_repeat(REPEAT_ALL)
    page._reset_shuffle_bag()
    assert page.next_index(automatic=True) == 0


# ----------------------------------------------------------------------
# The buttons
# ----------------------------------------------------------------------
def test_shuffle_is_remembered_for_the_next_start(page) -> None:
    assert page.config.discover_shuffle is False
    page.toggle_shuffle()
    assert page._order.shuffle is True
    assert page.config.discover_shuffle is True


def test_the_repeat_button_steps_through_its_three_modes(page) -> None:
    assert page._order.repeat == REPEAT_OFF
    page.cycle_repeat()
    assert page._order.repeat == REPEAT_ALL
    page.cycle_repeat()
    assert page._order.repeat == REPEAT_ONE
    page.cycle_repeat()
    assert page._order.repeat == REPEAT_OFF
    assert page.config.discover_repeat == REPEAT_OFF


def test_the_active_mode_is_visible_on_its_button(page) -> None:
    page.toggle_shuffle()
    assert "Accent" in str(page._shuffle_btn.cget("style"))
    page.toggle_shuffle()
    assert "Accent" not in str(page._shuffle_btn.cget("style"))


# ----------------------------------------------------------------------
# The sleep timer
# ----------------------------------------------------------------------
def test_the_sleep_timer_counts_down(page) -> None:
    page.set_sleep_timer(30)
    assert page.sleep_minutes_left() == 30
    assert page._sleep_job is not None


def test_the_sleep_timer_can_be_switched_off(page) -> None:
    page.set_sleep_timer(30)
    page.set_sleep_timer(0)
    assert page.sleep_minutes_left() == 0
    assert page._sleep_job is None


def test_reaching_the_sleep_timer_stops_playback(page, monkeypatch) -> None:
    stopped: list = []
    monkeypatch.setattr(page, "stop_playback", lambda: stopped.append(True))
    page.set_sleep_timer(15)
    page._sleep_reached()
    assert stopped == [True]
    assert page.sleep_minutes_left() == 0, "the timer does not fire twice"
