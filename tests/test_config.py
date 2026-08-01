"""Configuration defaults, persistence and tolerance for hand editing."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from clipster import paths
from clipster.config import Config


def test_a_missing_file_is_created_with_defaults(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    config = Config.load(target)
    assert target.is_file()
    assert config.language == "en"


def test_every_value_survives_a_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    config = Config.load(target)
    config.language = "de"
    config.interval_sec = 3.5
    config.history_limit = 42
    config.default_format = "mp4"
    config.open_view_after_download = True
    config.download_dir = "/tmp/somewhere"
    config.save()

    again = Config.load(target)
    assert again.language == "de"
    assert again.interval_sec == pytest.approx(3.5)
    assert again.history_limit == 42
    assert again.default_format == "mp4"
    assert again.open_view_after_download is True
    assert again.download_dir == "/tmp/somewhere"


def test_the_download_folder_is_not_opened_by_default() -> None:
    assert Config().open_folder_after_download is False


def test_defaults_that_the_interface_relies_on() -> None:
    config = Config()
    assert config.default_format == "mp3"
    assert config.history_limit == 100
    assert config.use_tray is True
    assert config.start_minimized is True
    assert config.ask_audio_language is True
    assert config.no_playlist is True


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("interval_sec", "nonsense", 2.0),
        ("history_limit", "many", 100),
        ("interval_sec", None, 2.0),
        ("use_tray", 1, True),
        ("language", None, ""),
    ],
)
def test_unusable_values_fall_back_to_the_default(tmp_path: Path, key, value, expected) -> None:
    config = Config.from_dict({key: value}, tmp_path / "config.json")
    assert getattr(config, key) == expected


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    config = Config.from_dict({"who_is_this": 5, "language": "de"}, tmp_path / "config.json")
    assert config.language == "de"
    assert not hasattr(config, "who_is_this")


@pytest.mark.parametrize("content", ["{ broken", "[]", '"a string"'])
def test_a_damaged_file_yields_defaults(tmp_path: Path, content: str) -> None:
    target = tmp_path / "config.json"
    target.write_text(content, encoding="utf-8")
    assert Config.load(target).language == "en"


def test_the_polling_interval_never_gets_absurdly_small() -> None:
    config = Config()
    config.interval_sec = 0.001
    assert config.poll_interval_ms() == 250


def test_an_empty_download_folder_means_the_system_folder() -> None:
    config = Config()
    config.download_dir = ""
    assert config.resolved_download_dir() == paths.default_download_dir()


def test_a_configured_download_folder_wins(tmp_path: Path) -> None:
    config = Config()
    config.download_dir = str(tmp_path)
    assert config.resolved_download_dir() == tmp_path


def test_a_tilde_in_the_download_folder_is_expanded() -> None:
    config = Config()
    config.download_dir = "~/Videos"
    assert config.resolved_download_dir() == Path.home() / "Videos"


def test_the_example_file_documents_every_setting() -> None:
    """A new setting is easy to add and just as easy to forget documenting."""
    fields = {f.name for f in dataclasses.fields(Config) if f.name != "path"}
    root = Path(__file__).resolve().parent.parent
    documented = {k for k in json.loads((root / "config.example.json").read_text())
                  if not k.startswith("_")}
    assert fields == documented


def test_the_path_itself_is_never_written(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    Config.load(target).save()
    assert "path" not in json.loads(target.read_text())
