"""Platform paths, including the Windows ones simulated from here."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clipster import paths


@pytest.fixture()
def as_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend to run on Windows with a plausible environment."""
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr(paths, "IS_LINUX", False)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Jo\AppData\Local")
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)


@pytest.fixture()
def as_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pretend to run on Linux with a clean XDG environment."""
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_LINUX", True)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)


def test_the_environment_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "elsewhere"))
    assert paths.install_dir() == tmp_path / "elsewhere"


def test_linux_uses_the_xdg_data_directory(as_linux, tmp_path: Path) -> None:
    assert paths.install_dir() == tmp_path / "xdg" / "YoutubeClipster"


def test_windows_uses_local_appdata(as_windows) -> None:
    assert str(paths.install_dir()).endswith("YoutubeClipster")
    assert "AppData" in str(paths.install_dir())


def test_the_override_also_wins_on_windows(as_windows, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, r"D:\clipster")
    assert str(paths.install_dir()) == r"D:\clipster"


def test_the_windows_interpreters(as_windows) -> None:
    console = str(paths.venv_python()).replace("\\", "/")
    gui = str(paths.venv_python(gui=True)).replace("\\", "/")
    assert console.endswith("Scripts/python.exe")
    assert gui.endswith("Scripts/pythonw.exe"), "the shortcut must not open a console"
    assert console != gui


def test_the_posix_interpreter(as_linux) -> None:
    assert str(paths.venv_python()).endswith("bin/python")
    assert paths.venv_python() == paths.venv_python(gui=True), "no separate GUI binary here"


def test_the_bundled_ffmpeg_is_an_exe_on_windows(as_windows) -> None:
    assert str(paths.bundled_ffmpeg_exe()).endswith("ffmpeg.exe")
    assert str(paths.bundled_ffplay_exe()).endswith("ffplay.exe")
    assert str(paths.bundled_mpv_exe()).endswith("mpv.exe")


def test_the_download_list_lives_next_to_the_configuration() -> None:
    assert paths.history_file().parent == paths.config_file().parent
    assert paths.history_file().name == "history.json"


def test_the_entry_point_and_the_icons_exist() -> None:
    assert paths.bootstrap_script().name == "run.py"
    assert paths.bootstrap_script().is_file()
    assert paths.icon_file().is_file()
    assert paths.windows_icon_file().suffix == ".ico"
    assert paths.windows_icon_file().is_file()


def test_the_install_directory_is_created_on_demand() -> None:
    target = paths.ensure_install_dir()
    assert target.is_dir()


def test_a_desktop_directory_is_always_returned() -> None:
    assert paths.desktop_dir().exists()


def test_the_download_folder_is_absolute() -> None:
    assert paths.default_download_dir().is_absolute()


def test_default_music_dir_is_absolute_or_missing() -> None:
    music = paths.default_music_dir()
    if music is not None:
        assert music.is_absolute()
        assert music.is_dir()


def test_running_in_the_managed_environment_is_detectable() -> None:
    assert isinstance(paths.running_in_managed_venv(), bool)


def test_every_path_helper_stays_inside_the_install_directory() -> None:
    """A stray absolute path would write into the user's home by accident."""
    root = paths.install_dir()
    for candidate in (paths.venv_dir(), paths.ffmpeg_dir(), paths.lock_file(),
                      paths.log_file(), paths.state_file()):
        assert str(candidate).startswith(str(root)), candidate


def test_the_configuration_is_isolated_during_tests() -> None:
    """Guards the autouse fixture itself - a regression here would be nasty."""
    assert str(paths.config_file()).startswith(os.environ[paths.HOME_ENV_VAR])
