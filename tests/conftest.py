"""Shared fixtures.

Two rules hold for the whole suite:

* No test may touch the real configuration, download list or downloads folder.
  The autouse :func:`isolated_home` fixture redirects the application data
  directory into a temporary folder for *every* test.
* No test may reach the network.  Everything that would talk to YouTube is
  either faked or marked ``network`` and deselected by default.
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipster import paths  # noqa: E402  - needs the path above


def _display_available() -> bool:
    """Return ``True`` when a Tk window can realistically be created."""
    if paths.IS_WINDOWS or paths.IS_MACOS:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _tkinter_available() -> bool:
    """Return ``True`` when tkinter can be imported at all."""
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


#: Reason shown when GUI tests are skipped, or an empty string when they run.
GUI_SKIP_REASON = (
    "" if _display_available() and _tkinter_available()
    else "no display or tkinter available"
)


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Skip GUI tests without a display and deselect network tests by default.

    :param config: The pytest configuration.
    :param items: The collected test items.
    :return: None
    """
    skip_gui = pytest.mark.skip(reason=GUI_SKIP_REASON)
    run_network = "network" in (config.getoption("-m") or "")
    skip_network = pytest.mark.skip(reason="needs the network; run with -m network")
    for item in items:
        if "gui" in item.keywords and GUI_SKIP_REASON:
            item.add_marker(skip_gui)
        if "network" in item.keywords and not run_network:
            item.add_marker(skip_network)


@pytest.fixture(autouse=True)
def collect_tk_garbage(request: pytest.FixtureRequest) -> Iterator[None]:
    """Finalise leftover Tk objects on the main thread after a GUI test.

    The suite builds and destroys many Tk roots, which the running program never
    does.  Widgets and variables left behind are freed whenever the garbage
    collector next runs - and when that happens on one of the web server's
    threads, ``Variable.__del__`` calls into Tk from the wrong thread and CPython
    aborts the whole process.  Collecting here keeps those finalisers on the main
    thread, where Tk is still valid.
    """
    yield
    if "gui" in request.keywords:
        gc.collect()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the application data directory at a temporary folder.

    Autouse on purpose: a single forgotten fixture would let a test write into
    the developer's real ``config.json`` or download list.

    :param tmp_path: pytest's per-test temporary directory.
    :param monkeypatch: pytest's environment patcher.
    :return: The temporary application data directory.
    """
    home = tmp_path / "appdata"
    home.mkdir()
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home))
    # A portable config.json next to the sources would win over the override.
    monkeypatch.setattr(paths, "PROJECT_ROOT", ROOT, raising=True)
    yield home


@pytest.fixture()
def downloads(tmp_path: Path) -> Path:
    """Return an empty directory that stands in for the download folder."""
    target = tmp_path / "downloads"
    target.mkdir()
    return target


@pytest.fixture()
def config(downloads: Path, isolated_home: Path):
    """Return a configuration that writes nowhere but the temporary folders."""
    from clipster.config import Config

    item = Config()
    item.path = isolated_home / "config.json"
    item.download_dir = str(downloads)
    item.use_tray = False
    item.ask_desktop_shortcut = False
    item.show_startup_notification = False
    item.open_folder_after_download = False
    item.clear_clipboard_after_download = False
    item.autostart = False
    return item


@pytest.fixture()
def messages():
    """Return the English translation table."""
    from clipster import i18n

    return i18n.load("en")


@pytest.fixture()
def gui(config, messages, downloads: Path) -> Iterator:
    """Build the real GUI with both windows and tear it down afterwards.

    :return: A ready :class:`clipster.gui.Gui` with its windows created.
    """
    from clipster.gui import Gui

    instance = Gui(messages, config, downloads)
    instance.build_windows()
    instance.render_history([])
    try:
        yield instance
    finally:
        instance.destroy()


@pytest.fixture()
def sample_entries():
    """Return one finished, one failed and one canceled history entry."""
    from clipster.history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, HistoryEntry

    return [
        HistoryEntry(name="song.mp3", media_format="mp3", duration=213, size=6_940_000,
                     status=STATUS_OK, finished_at="2026-07-31T11:20:05"),
        HistoryEntry(name="broken.mp4", media_format="mp4", status=STATUS_FAILED,
                     error="boom", error_kind="unavailable", finished_at="2026-07-30T09:00:00"),
        HistoryEntry(name="stopped.mp4", media_format="mp4", status=STATUS_CANCELED,
                     finished_at="2026-07-29T21:44:30"),
    ]
