"""What the mobile-data rule does to the program, not just to the table.

:mod:`tests.test_netmode` pins the decision itself down.  Here the question is
whether the rest of the program actually obeys it: the queue, the audio the
phone asks for, and the way the setting reaches the running page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipster import netmode
from clipster.discover import DiscoverTrack
from clipster.discover_session import HeadlessDiscoverSession


class _Messages(dict):
    """Just enough of :class:`clipster.i18n.Messages` for a status line."""

    def __getitem__(self, key: str) -> str:
        return key

    def format(self, key: str, **kwargs) -> str:
        return key


class _Config:
    """The settings the session and the rule read."""

    playback_on_mobile = netmode.MOBILE_LOCAL
    playback_local_only = False
    discover_extend_remaining = 3
    discover_mode = "related"


def _online(index: int = 0) -> DiscoverTrack:
    """Return a track that would have to be fetched."""
    return DiscoverTrack(url="https://youtu.be/x", video_id="aaaaaaaaaa{0}".format(index),
                         title="Online {0}".format(index))


def _local(tmp_path: Path, index: int = 0) -> DiscoverTrack:
    """Return a track that plays from a file on disk."""
    target = tmp_path / "song{0}.mp3".format(index)
    target.write_bytes(b"\0")
    return DiscoverTrack(url="", video_id="", title="Local {0}".format(index),
                         path=str(target))


@pytest.fixture()
def session() -> HeadlessDiscoverSession:
    """Return the Streaming session Android actually runs."""
    return HeadlessDiscoverSession(_Config(), _Messages())


@pytest.fixture()
def app(config, messages, monkeypatch):
    """Return a real application whose downloads never start."""
    from clipster.app import ClipsterApp

    instance = ClipsterApp(config, messages)
    monkeypatch.setattr(instance, "_handle_url", lambda *a, **k: None)
    try:
        yield instance
    finally:
        instance._cancel_auto_discover_job()
        instance.gui.destroy()


# ----------------------------------------------------------------------
# The queue on a metered connection
# ----------------------------------------------------------------------
def test_a_refused_stream_switches_the_queue_to_the_library(session, monkeypatch) -> None:
    """The music must keep playing from disk, not simply stop."""
    switched = []
    session.allow_stream = lambda: False
    session.on_library = lambda: switched.append(True)
    session._tracks = [_online()]
    session.player.set_playlist(session._tracks)

    session.play_at(0)
    assert switched == [True], "the library was never offered"
    assert session._selected == -1, "a refused track must not become the current one"


def test_a_local_track_plays_even_when_streaming_is_refused(session, tmp_path: Path,
                                                            monkeypatch) -> None:
    """This is the whole point: downloads keep working without a connection."""
    monkeypatch.setattr(session.player, "play_async",
                        lambda *a, **k: None)
    session.allow_stream = lambda: False
    session.on_library = lambda: pytest.fail("a local track must not trigger a switch")
    session._tracks = [_local(tmp_path)]
    session.player.set_playlist(session._tracks)

    session.play_at(0)
    assert session._selected == 0


def test_without_a_rule_wired_everything_plays(session, monkeypatch) -> None:
    """A desktop never wires the gate; nothing may change there."""
    monkeypatch.setattr(session.player, "play_async", lambda *a, **k: None)
    session._tracks = [_online()]
    session.player.set_playlist(session._tracks)

    session.play_at(0)
    assert session._selected == 0


def test_the_headless_session_can_take_a_whole_queue(session, tmp_path: Path) -> None:
    """The library fills the queue through set_tracks - which Android lacked."""
    assert hasattr(session, "set_tracks"), "the phone cannot show a library without this"
    session.set_tracks([_local(tmp_path, 0), _local(tmp_path, 1)])
    assert len(session._tracks) == 2
    assert session._selected == 0


def test_set_tracks_does_not_start_playing_on_the_backend(session, tmp_path: Path,
                                                          monkeypatch) -> None:
    """The browser owns playback on the phone; the backend must not join in."""
    monkeypatch.setattr(session.player, "play_async",
                        lambda *a, **k: pytest.fail("the backend started playing"))
    session.set_tracks([_local(tmp_path, 0)])


# ----------------------------------------------------------------------
# The application side
# ----------------------------------------------------------------------
@pytest.mark.gui
def test_the_connection_reaches_the_rule(app) -> None:
    app.config.playback_on_mobile = netmode.MOBILE_LOCAL
    app.config.playback_local_only = False

    app.set_connection_type("wifi")
    assert app.streaming_allowed() is True

    app.set_connection_type("cellular")
    assert app.streaming_allowed() is False


@pytest.mark.gui
def test_the_manual_switch_works_without_any_connection_report(app) -> None:
    app.config.playback_local_only = True
    assert app.streaming_allowed() is False


@pytest.mark.gui
def test_asking_is_remembered_for_the_connection_and_forgotten_after_it(app) -> None:
    app.config.playback_on_mobile = netmode.MOBILE_ASK
    app.config.playback_local_only = False
    app.set_connection_type("cellular")
    assert app.streaming_allowed() is False, "must ask before the first stream"

    app.allow_mobile_stream()
    assert app.streaming_allowed() is True, "the answer has to hold for this connection"

    app.set_connection_type("wifi")
    app.set_connection_type("cellular")
    assert app.streaming_allowed() is False, "a new connection is a new question"


@pytest.mark.gui
def test_the_audio_the_phone_asks_for_is_refused_too(app, monkeypatch) -> None:
    """A page left open must not keep pulling audio after leaving Wi-Fi."""
    app.config.playback_local_only = True
    url, headers = app.discover_remote_audio("aaaaaaaaaaa")
    assert url == "" and headers == {}


@pytest.mark.gui
def test_the_state_the_phone_polls_explains_the_rule(app) -> None:
    app.config.playback_on_mobile = netmode.MOBILE_LOCAL
    app.set_connection_type("cellular")
    state = app.playback_source_state()
    assert state["metered"] is True
    assert state["local_only"] is True
    assert state["mode"] == netmode.MOBILE_LOCAL
