"""The persistent download list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipster.history import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_OK,
    History,
    HistoryEntry,
    format_duration,
    format_size,
    format_timestamp,
)


# ----------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "size,expected",
    [(0, "-"), (-5, "-"), (512, "512 B"), (2048, "2.0 KB"),
     (5 * 1024 ** 2, "5.0 MB"), (3 * 1024 ** 3, "3.0 GB")],
)
def test_size_formatting(size: int, expected: str) -> None:
    assert format_size(size) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "-"), (-1, "-"), (59, "0:59"), (213, "3:33"), (3600, "1:00:00"), (7325, "2:02:05")],
)
def test_duration_formatting(seconds: int, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_timestamp_formatting() -> None:
    assert format_timestamp(HistoryEntry(finished_at="2026-07-31T11:20:05")) == "31.07.2026 11:20"
    assert format_timestamp(HistoryEntry()) == "-"
    assert format_timestamp(HistoryEntry(finished_at="not a date")) == "-"


# ----------------------------------------------------------------------
# Entries
# ----------------------------------------------------------------------
def test_a_finished_entry_knows_its_file(tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x" * 16)
    entry = HistoryEntry(name="song.mp3", path=str(target), status=STATUS_OK)
    assert entry.succeeded
    assert entry.file_path() == target


def test_an_entry_whose_file_vanished_reports_none(tmp_path: Path) -> None:
    entry = HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"), status=STATUS_OK)
    assert entry.file_path() is None


def test_a_failed_entry_has_no_file() -> None:
    entry = HistoryEntry(title="broken", status=STATUS_FAILED, error="boom")
    assert not entry.succeeded
    assert entry.file_path() is None


# ----------------------------------------------------------------------
# The store
# ----------------------------------------------------------------------
def test_a_new_store_is_empty(tmp_path: Path) -> None:
    store = History(path=tmp_path / "history.json").load()
    assert len(store) == 0
    assert store.entries == []


def test_adding_fills_in_size_name_and_timestamp(tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x" * 4096)
    store = History(path=tmp_path / "history.json").load()
    entry = store.add(HistoryEntry(path=str(target), title="A song", media_format="mp3",
                                   status=STATUS_OK))
    assert entry.size == 4096
    assert entry.name == "A song", "the title stands in when no file name was given"
    assert entry.finished_datetime() is not None


def test_newest_entries_come_first(tmp_path: Path) -> None:
    store = History(path=tmp_path / "history.json").load()
    for title in ("first", "second", "third"):
        store.add(HistoryEntry(title=title, status=STATUS_OK))
    assert [e.title for e in store.entries] == ["third", "second", "first"]


def test_the_limit_is_enforced(tmp_path: Path) -> None:
    store = History(path=tmp_path / "history.json", limit=3).load()
    for index in range(6):
        store.add(HistoryEntry(title=str(index), status=STATUS_OK))
    assert len(store) == 3
    assert [e.title for e in store.entries] == ["5", "4", "3"]


def test_entries_survive_a_reload(tmp_path: Path) -> None:
    target = tmp_path / "history.json"
    store = History(path=target).load()
    store.add(HistoryEntry(title="Broken", status=STATUS_FAILED, error="boom",
                           error_kind="bot", media_format="mp4", duration=90))
    reloaded = History(path=target).load()
    assert len(reloaded) == 1
    entry = reloaded.entries[0]
    assert (entry.title, entry.status, entry.error_kind, entry.duration) == ("Broken", STATUS_FAILED, "bot", 90)


def test_clearing_empties_the_file(tmp_path: Path) -> None:
    target = tmp_path / "history.json"
    store = History(path=target).load()
    store.add(HistoryEntry(title="x", status=STATUS_OK))
    store.clear()
    assert len(store) == 0
    assert json.loads(target.read_text())["downloads"] == []


def test_remove_missing_drops_vanished_files(tmp_path: Path) -> None:
    present = tmp_path / "here.mp3"
    present.write_bytes(b"x")
    store = History(path=tmp_path / "history.json").load()
    store.add(HistoryEntry(name="here.mp3", path=str(present), status=STATUS_OK))
    store.add(HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"), status=STATUS_OK))
    store.add(HistoryEntry(title="failed", status=STATUS_FAILED))
    assert store.remove_missing() == 1
    # Newest first, so the failed entry added last leads.
    assert [e.name for e in store.entries] == ["failed", "here.mp3"]


def test_remove_missing_keeps_canceled_entries(tmp_path: Path) -> None:
    """They never had a file, so there is nothing to miss."""
    store = History(path=tmp_path / "history.json").load()
    store.add(HistoryEntry(title="stopped", status=STATUS_CANCELED))
    assert store.remove_missing() == 0
    assert len(store) == 1


# ----------------------------------------------------------------------
# Broken files must never take the program down
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "content",
    ["{ not json", '{"downloads": "nope"}', "[]", "null", "42"],
)
def test_a_damaged_file_yields_an_empty_list(tmp_path: Path, content: str) -> None:
    target = tmp_path / "history.json"
    target.write_text(content, encoding="utf-8")
    assert len(History(path=target).load()) == 0


def test_unknown_keys_are_ignored_and_types_coerced(tmp_path: Path) -> None:
    target = tmp_path / "history.json"
    target.write_text('[{"name": "a", "duration": "7", "succeeded": true, "bogus": 1}]',
                      encoding="utf-8")
    entry = History(path=target).load().entries[0]
    assert entry.name == "a"
    assert entry.duration == 7, "numbers arrive as strings from hand-edited files"


def test_a_plain_list_is_accepted_as_well(tmp_path: Path) -> None:
    """Older files stored the entries without the wrapping object."""
    target = tmp_path / "history.json"
    target.write_text('[{"name": "a"}, {"name": "b"}]', encoding="utf-8")
    assert [e.name for e in History(path=target).load().entries] == ["a", "b"]


def test_saving_into_an_unwritable_place_does_not_raise(tmp_path: Path) -> None:
    store = History(path=Path("/proc/definitely/not/writable/history.json"))
    store.add(HistoryEntry(title="x", status=STATUS_OK))  # must not raise
