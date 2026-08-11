"""The order the queue is played in, as arithmetic.

:mod:`tests.test_playback_modes` drives the same rules through the Tk page.
Here they are exercised directly, including the cases a UI test cannot reach
comfortably - and the last test checks that both platforms really do share the
one implementation rather than each having their own.
"""

from __future__ import annotations

import random

import pytest

from clipster import playorder
from clipster.playorder import REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, PlayOrder


# ----------------------------------------------------------------------
# Reading the mode
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value", [REPEAT_OFF, REPEAT_ALL, REPEAT_ONE])
def test_every_documented_mode_survives_normalising(value: str) -> None:
    assert playorder.normalize_repeat(value) == value


@pytest.mark.parametrize("value", ["", None, "nonsense", 3, "OFF "])
def test_anything_else_means_off(value) -> None:
    assert playorder.normalize_repeat(value) in (REPEAT_OFF, "off")


def test_the_button_cycles_through_all_three() -> None:
    order = PlayOrder()
    assert order.repeat == REPEAT_OFF
    assert order.cycle_repeat() == REPEAT_ALL
    assert order.cycle_repeat() == REPEAT_ONE
    assert order.cycle_repeat() == REPEAT_OFF


# ----------------------------------------------------------------------
# In order
# ----------------------------------------------------------------------
def test_the_queue_plays_through_and_then_stops() -> None:
    order = PlayOrder()
    assert order.next_index(3, 0, automatic=True) == 1
    assert order.next_index(3, 1, automatic=True) == 2
    assert order.next_index(3, 2, automatic=True) is None


def test_repeat_all_starts_over() -> None:
    order = PlayOrder(repeat=REPEAT_ALL)
    assert order.next_index(3, 2, automatic=True) == 0


def test_repeat_one_returns_the_same_row() -> None:
    order = PlayOrder(repeat=REPEAT_ONE)
    assert order.next_index(3, 1, automatic=True) == 1


def test_pressing_next_never_repeats_the_same_song() -> None:
    """Repeat-one is about a song ending, not about the skip button."""
    order = PlayOrder(repeat=REPEAT_ONE)
    assert order.next_index(3, 1, automatic=False) == 2


def test_an_empty_queue_has_no_next() -> None:
    for mode in (REPEAT_OFF, REPEAT_ALL, REPEAT_ONE):
        assert PlayOrder(repeat=mode).next_index(0, -1, automatic=True) is None
        assert PlayOrder(shuffle=True, repeat=mode).next_index(0, -1, automatic=True) is None


def test_nothing_playing_yet_starts_at_the_top() -> None:
    assert PlayOrder().next_index(3, -1, automatic=False) == 0


# ----------------------------------------------------------------------
# Shuffled
# ----------------------------------------------------------------------
def test_shuffle_plays_every_row_before_repeating_one() -> None:
    random.seed(7)
    order = PlayOrder(shuffle=True)
    seen = []
    current = 0
    for _ in range(4):
        following = order.next_index(5, current, automatic=True)
        if following is None:
            break
        seen.append(following)
        current = following
    assert sorted(seen) == [1, 2, 3, 4], seen
    assert 0 not in seen, "the running song must not come back inside the round"


def test_shuffle_without_repeat_ends_after_one_round() -> None:
    random.seed(7)
    order = PlayOrder(shuffle=True)
    current = 0
    for _ in range(3):
        current = order.next_index(4, current, automatic=True)
        assert current is not None
    assert order.next_index(4, current, automatic=True) is None


def test_shuffle_with_repeat_all_starts_a_new_round() -> None:
    random.seed(7)
    order = PlayOrder(shuffle=True, repeat=REPEAT_ALL)
    current = 0
    for _ in range(3):
        current = order.next_index(4, current, automatic=True)
        assert current is not None
    assert order.next_index(4, current, automatic=True) is not None, "a new round has to start"


def test_a_shorter_queue_drops_rows_that_no_longer_exist() -> None:
    """Songs can be hidden while a round is in flight."""
    random.seed(7)
    order = PlayOrder(shuffle=True)
    order.next_index(10, 0, automatic=True)
    for _ in range(12):
        following = order.next_index(3, 0, automatic=True)
        assert following is None or following < 3


def test_a_single_track_repeats_only_when_asked_to() -> None:
    order = PlayOrder(shuffle=True)
    assert order.next_index(1, 0, automatic=True) is None
    order = PlayOrder(shuffle=True, repeat=REPEAT_ALL)
    assert order.next_index(1, 0, automatic=True) == 0


def test_a_new_queue_starts_a_new_round() -> None:
    random.seed(7)
    order = PlayOrder(shuffle=True)
    order.next_index(4, 0, automatic=True)
    order.reset()
    assert order._bag == [] and order._started is False


def test_turning_shuffle_on_starts_a_fresh_round() -> None:
    order = PlayOrder(shuffle=True)
    order.next_index(4, 0, automatic=True)
    order.set_shuffle(True)
    assert order._bag == []


# ----------------------------------------------------------------------
# One rule, both platforms
# ----------------------------------------------------------------------
def test_both_platforms_hold_the_shared_rule_rather_than_a_copy() -> None:
    """A second implementation is the thing this whole module exists to prevent."""
    from clipster.discover_session import HeadlessDiscoverSession

    class _Config:
        discover_shuffle = True
        discover_repeat = REPEAT_ALL
        discover_extend_remaining = 3
        discover_mode = "related"

    session = HeadlessDiscoverSession(_Config(), {})
    assert isinstance(session._order, PlayOrder)
    # The configuration reaches it, or the phone would start with the defaults.
    assert session._order.shuffle is True
    assert session._order.repeat == REPEAT_ALL


def test_the_two_platforms_produce_the_same_sequence() -> None:
    """Same modes, same queue, same seed - the answers must not differ at all."""
    from clipster.discover import DiscoverTrack
    from clipster.discover_session import HeadlessDiscoverSession

    class _Config:
        discover_shuffle = True
        discover_repeat = REPEAT_ALL
        discover_extend_remaining = 3
        discover_mode = "related"

    tracks = [DiscoverTrack(url="", video_id="{0:011d}".format(index), title="S{0}".format(index))
              for index in range(6)]

    def walk(step, steps: int = 10):
        """Collect what a next-index callable answers, following its own advice."""
        seen = []
        current = 0
        for _ in range(steps):
            current = step(current)
            seen.append(current)
            if current is None:
                break
            current = current
        return seen

    random.seed(99)
    session = HeadlessDiscoverSession(_Config(), {})
    session._tracks = list(tracks)

    def by_session(current):
        session._selected = current
        return session.next_index(automatic=True)

    from_session = walk(by_session)

    random.seed(99)
    bare = PlayOrder(shuffle=True, repeat=REPEAT_ALL)
    from_rule = walk(lambda current: bare.next_index(len(tracks), current, automatic=True))

    assert from_session == from_rule, "the phone diverged from the shared rule"


def test_the_headless_session_follows_shuffle_and_repeat() -> None:
    """Android drives this class, so a mode that stops here stops on the phone."""
    from clipster.discover import DiscoverTrack
    from clipster.discover_session import HeadlessDiscoverSession

    class _Config:
        discover_shuffle = False
        discover_repeat = REPEAT_OFF
        discover_extend_remaining = 3
        discover_mode = "related"

    session = HeadlessDiscoverSession(_Config(), {})
    session._tracks = [DiscoverTrack(url="", video_id="a" * 11, title="One"),
                       DiscoverTrack(url="", video_id="b" * 11, title="Two")]
    session._selected = 1

    assert session.next_index(automatic=True) is None, "off means stop at the end"
    session.set_repeat(REPEAT_ALL)
    assert session.next_index(automatic=True) == 0
    session.set_repeat(REPEAT_ONE)
    assert session.next_index(automatic=True) == 1
    assert session.next_index(automatic=False) is None, "skip is not a repeat"


def test_the_headless_sleep_timer_can_be_set_and_cancelled() -> None:
    from clipster.discover_session import HeadlessDiscoverSession

    class _Config:
        discover_shuffle = False
        discover_repeat = REPEAT_OFF
        discover_extend_remaining = 3
        discover_mode = "related"

    session = HeadlessDiscoverSession(_Config(), {})
    assert session.sleep_minutes_left() == 0
    session.set_sleep_timer(30)
    try:
        assert 29 <= session.sleep_minutes_left() <= 31
    finally:
        session.set_sleep_timer(0)
    assert session.sleep_minutes_left() == 0
