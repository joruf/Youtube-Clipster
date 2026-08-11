"""The rule that decides between streaming and the download folder.

A decision table with no I/O, so every case can simply be stated.
"""

from __future__ import annotations

import pytest

from clipster import netmode


class _Config:
    """The two settings the rule reads, and nothing else."""

    def __init__(self, mode: str = "stream", local_only: bool = False) -> None:
        self.playback_on_mobile = mode
        self.playback_local_only = local_only


# ----------------------------------------------------------------------
# Reading the settings
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value", ["stream", "local", "ask"])
def test_every_documented_mode_survives_normalising(value: str) -> None:
    assert netmode.normalize_mode(value) == value


@pytest.mark.parametrize("value", ["", None, "nonsense", 7, "STREAMING"])
def test_anything_else_falls_back_to_the_default(value) -> None:
    assert netmode.normalize_mode(value) == netmode.DEFAULT_MOBILE_MODE


def test_the_default_keeps_the_previous_behaviour() -> None:
    """Existing installations must not start refusing to play after an update."""
    assert netmode.DEFAULT_MOBILE_MODE == netmode.MOBILE_STREAM
    assert netmode.local_only(_Config(), "cellular") is False


def test_a_mode_is_read_case_insensitively() -> None:
    assert netmode.normalize_mode(" Local ") == netmode.MOBILE_LOCAL


# ----------------------------------------------------------------------
# Reading the connection
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value", ["cellular", "3g", "4g", "5g", "2g", "wimax", "CELLULAR"])
def test_a_mobile_connection_is_recognised(value: str) -> None:
    assert netmode.is_metered(value) is True


@pytest.mark.parametrize("value", ["wifi", "ethernet", "bluetooth", "wifi "])
def test_a_fixed_connection_is_not_metered(value: str) -> None:
    assert netmode.is_metered(value) is False


@pytest.mark.parametrize("value", ["", None, "unknown", "none"])
def test_an_unknown_connection_is_not_metered(value) -> None:
    """A desktop reports nothing; guessing "mobile" would stop its music."""
    assert netmode.is_metered(value) is False


# ----------------------------------------------------------------------
# The decision
# ----------------------------------------------------------------------
def test_wifi_streams_whatever_the_mobile_setting_says() -> None:
    for mode in netmode.MOBILE_MODES:
        assert netmode.local_only(_Config(mode), "wifi") is False
        assert netmode.should_ask(_Config(mode), "wifi") is False


def test_mobile_data_with_local_mode_stays_local() -> None:
    assert netmode.local_only(_Config("local"), "cellular") is True


def test_mobile_data_with_stream_mode_still_streams() -> None:
    assert netmode.local_only(_Config("stream"), "cellular") is False


def test_mobile_data_with_ask_mode_asks_rather_than_refusing() -> None:
    config = _Config("ask")
    assert netmode.local_only(config, "cellular") is False
    assert netmode.should_ask(config, "cellular") is True


def test_the_manual_switch_beats_everything() -> None:
    """Somebody who turned this on knows what their data plan looks like."""
    for connection in ("", "wifi", "cellular"):
        config = _Config("stream", local_only=True)
        assert netmode.local_only(config, connection) is True


def test_the_manual_switch_is_never_asked_about() -> None:
    config = _Config("ask", local_only=True)
    assert netmode.should_ask(config, "cellular") is False


def test_a_desktop_that_reports_nothing_is_never_restricted() -> None:
    for mode in netmode.MOBILE_MODES:
        assert netmode.local_only(_Config(mode), netmode.UNKNOWN) is False
