"""The program running *on* the phone, not talking to one.

Android is a Linux that reports itself as Linux, so every difference has to be
asked for explicitly through :func:`clipster.paths.is_termux`.  These tests pin
down which differences are deliberate - and, just as importantly, that nothing
else changes.  Without them a refactor can quietly turn the Android build into
a second code base.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipster import paths


@pytest.fixture()
def on_android(monkeypatch: pytest.MonkeyPatch):
    """Make the whole program believe it runs inside Termux."""
    monkeypatch.setattr(paths, "is_termux", lambda: True)
    return paths


@pytest.fixture()
def on_desktop(monkeypatch: pytest.MonkeyPatch):
    """Make the whole program believe it runs on a normal computer."""
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    return paths


# ----------------------------------------------------------------------
# Deliberate differences
# ----------------------------------------------------------------------
def test_android_hides_the_remote_page(on_android) -> None:
    """The phone cannot control itself remotely - the page has no purpose."""
    from clipster.viewwindow import ViewWindow

    keys = [key for key, _label in ViewWindow._menu_entries()]
    assert "phone" not in keys


def test_the_desktop_keeps_the_remote_page(on_desktop) -> None:
    from clipster.viewwindow import ViewWindow

    keys = [key for key, _label in ViewWindow._menu_entries()]
    assert "phone" in keys


def test_only_the_remote_page_differs(on_android, on_desktop, monkeypatch) -> None:
    """Every other page must exist on both, in the same order."""
    from clipster.viewwindow import ViewWindow

    monkeypatch.setattr(paths, "is_termux", lambda: False)
    desktop = [key for key, _ in ViewWindow._menu_entries()]
    monkeypatch.setattr(paths, "is_termux", lambda: True)
    android = [key for key, _ in ViewWindow._menu_entries()]
    assert [key for key in desktop if key != "phone"] == android


@pytest.mark.gui
def test_the_window_still_works_without_the_remote_page(config, messages, downloads, monkeypatch) -> None:
    """Dropping a page must not break navigation or the pages that remain."""
    monkeypatch.setattr(paths, "is_termux", lambda: True)
    from clipster.gui import Gui

    gui = Gui(messages, config, downloads)
    gui.build_windows()
    gui.render_history([])
    try:
        assert gui.view.phone is None
        for page in ("downloads", "discover", "settings", "about"):
            gui.view.select_page(page)
            assert gui.view.current_page == page
        # An unknown page must be ignored, not crash.
        gui.view.select_page("phone")
        assert gui.view.current_page == "about"
    finally:
        gui.destroy()


# ----------------------------------------------------------------------
# The download folder
# ----------------------------------------------------------------------
def test_android_maps_the_public_folder_to_a_writable_one(on_android, config) -> None:
    """/storage/emulated/0/Download is not writable; the link in ~ is."""
    config.download_dir = "/storage/emulated/0/Download/clipster"
    resolved = config.resolved_download_dir()
    assert isinstance(resolved, Path)
    assert resolved.is_absolute()


def test_the_desktop_takes_the_folder_as_typed(on_desktop, config, tmp_path: Path) -> None:
    config.download_dir = str(tmp_path / "music")
    assert config.resolved_download_dir() == tmp_path / "music"


def test_a_public_path_is_shown_the_way_a_file_manager_names_it() -> None:
    """The user looks for the folder in the Android file manager, not in ~."""
    shown = paths.friendly_download_path("/data/data/com.termux/files/home/storage/downloads/clipster")
    assert shown == "/storage/emulated/0/Download/clipster"


def test_a_normal_path_is_left_alone(tmp_path: Path) -> None:
    assert paths.friendly_download_path(str(tmp_path)) == str(tmp_path)


def test_an_empty_path_still_names_something() -> None:
    assert paths.friendly_download_path("").strip()


# ----------------------------------------------------------------------
# Everything that must NOT differ
# ----------------------------------------------------------------------
@pytest.mark.parametrize("termux", [False, True])
def test_the_error_classification_is_the_same_everywhere(termux: bool, monkeypatch) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: termux)
    from clipster.downloader import classify_error

    assert classify_error("HTTP Error 403: Forbidden") == "forbidden"
    assert classify_error("This video is DRM protected") == "drm"
    assert classify_error("Requested format is not available") == "noformat"
    assert classify_error("Video unavailable") == "unavailable"
    assert classify_error("No space left on device") == "diskfull"


@pytest.mark.parametrize("termux", [False, True])
def test_the_retry_plan_is_the_same_everywhere(termux: bool, monkeypatch, config, messages) -> None:
    """A 403 on the phone has to be handled exactly as on the desktop."""
    monkeypatch.setattr(paths, "is_termux", lambda: termux)
    from clipster.downloader import Downloader

    variants = Downloader(config, messages)._forbidden_retries("mp3", "")
    assert len(variants) >= 2
    assert "format" in variants[0]


@pytest.mark.parametrize("termux", [False, True])
def test_the_update_check_is_offered_on_every_platform(termux: bool, monkeypatch) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: termux)
    from clipster import updater

    assert callable(updater.check)
    assert callable(updater.apply)
    assert updater.repository_slug().count("/") == 1


@pytest.mark.parametrize("termux", [False, True])
def test_the_web_interface_always_offers_the_update_routes(termux: bool, monkeypatch) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: termux)
    from clipster.webapi import RemoteApi

    assert hasattr(RemoteApi, "update_check")
    assert hasattr(RemoteApi, "update_install")


def test_the_phone_interface_ships_the_update_button() -> None:
    """The button is part of the served page, not something added by hand."""
    web = Path(__file__).resolve().parent.parent / "clipster" / "web"
    page = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    assert 'id="update-button"' in page
    assert 'id="update-state"' in page
    assert "/api/update" in script
    assert "installUpdate" in script


def test_the_service_worker_caches_the_page_under_a_version() -> None:
    """A home-screen copy keeps the shell; without a version it keeps it forever."""
    web = Path(__file__).resolve().parent.parent / "clipster" / "web"
    worker = (web / "sw.js").read_text(encoding="utf-8")
    assert re.search(r'SHELL_CACHE\s*=\s*"clipster-shell-v\d+"', worker)
    for asset in ("/index.html", "/app.js", "/style.css"):
        assert asset in worker, "{0} is not in the cached shell".format(asset)
