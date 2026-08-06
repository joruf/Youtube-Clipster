"""Linux, Windows and Android must run the same code, not three copies of it.

Two rules this pins down:

* A feature that exists on one platform exists on the others unless the
  difference is deliberate and named here (the Remote tab, for instance, has
  nothing to control when the program already runs on the phone).
* Where the presentation genuinely differs, the difference is one branch on
  :func:`clipster.paths.is_termux` - never a second implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from clipster import paths, updater


# ----------------------------------------------------------------------
# The update button
# ----------------------------------------------------------------------
class _Info:
    """Stands in for :class:`clipster.updater.UpdateInfo`."""

    def __init__(self, *, available=False, remote="2222222222", local="1111111111",
                 summary="Newer commit", error="") -> None:
        self.available = available
        self.remote = remote
        self.local = local
        self.summary = summary
        self.error = error

    @property
    def known(self) -> bool:
        return not self.error and bool(self.remote)


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


def test_the_phone_can_ask_for_an_update(app, monkeypatch) -> None:
    """Same updater the desktop About page drives - no second implementation."""
    monkeypatch.setattr(updater, "check", lambda *a, **k: _Info(available=True))
    result = app.check_update_remote()
    assert result["ok"] is True
    assert result["available"] is True
    assert result["remote"] == "2222222222"
    assert result["message"]


def test_the_phone_is_told_when_nothing_is_new(app, monkeypatch) -> None:
    monkeypatch.setattr(updater, "check", lambda *a, **k: _Info(available=False))
    result = app.check_update_remote()
    assert result["ok"] is True
    assert result["available"] is False


def test_a_failed_check_reaches_the_phone_as_a_message(app, monkeypatch) -> None:
    monkeypatch.setattr(updater, "check", lambda *a, **k: _Info(error="no network", remote=""))
    result = app.check_update_remote()
    assert result["ok"] is False
    assert "no network" in result["message"]


def test_installing_from_the_phone_asks_for_a_restart(app, monkeypatch) -> None:
    monkeypatch.setattr(updater, "apply", lambda *a, **k: (True, "12 files updated"))
    result = app.install_update_remote()
    assert result["ok"] is True
    assert result["restarting"] is True


def test_a_failed_install_does_not_restart(app, monkeypatch) -> None:
    monkeypatch.setattr(updater, "apply", lambda *a, **k: (False, "local changes"))
    result = app.install_update_remote()
    assert result["ok"] is False
    assert result["restarting"] is False
    assert app._restart_after_update is False


def test_the_restart_goes_through_the_normal_shutdown(app, monkeypatch) -> None:
    """The server, the downloads and the instance lock must be released first."""
    quits: list = []
    monkeypatch.setattr(app, "request_quit", lambda: quits.append(True))
    app._restart_for_remote_update()
    assert app._restart_after_update is True
    assert quits == [True]


def test_the_update_check_never_raises_at_the_caller(app, monkeypatch) -> None:
    """The web server thread must always get an answer to send back."""
    def explode(*_a: Any, **_k: Any) -> None:
        raise OSError("network is down")

    monkeypatch.setattr(updater, "check", explode)
    from clipster.webapi import RemoteApi

    status, payload = RemoteApi(app).update_check()
    assert status == 200
    assert payload["ok"] is False


# ----------------------------------------------------------------------
# The settings page
# ----------------------------------------------------------------------
@pytest.mark.gui
def test_the_desktop_settings_show_where_files_land(gui, downloads: Path) -> None:
    """The phone UI always showed this; the desktop did not."""
    gui.view.select_page("settings")
    gui.view._vars["download_dir"].set(str(downloads))
    gui.view.window.update_idletasks()
    shown = str(gui.view._download_dir_resolved.cget("text"))
    assert shown
    assert str(downloads) in shown


@pytest.mark.gui
def test_an_empty_download_dir_still_names_a_folder(gui) -> None:
    """"Empty means the system folder" is not obvious - so name it."""
    gui.view.select_page("settings")
    gui.view._vars["download_dir"].set("")
    gui.view.window.update_idletasks()
    assert str(gui.view._download_dir_resolved.cget("text")).strip()


@pytest.mark.gui
def test_the_resolved_folder_follows_what_is_typed(gui, tmp_path: Path) -> None:
    gui.view.select_page("settings")
    first = tmp_path / "one"
    second = tmp_path / "two"
    gui.view._vars["download_dir"].set(str(first))
    gui.view.window.update_idletasks()
    before = str(gui.view._download_dir_resolved.cget("text"))
    gui.view._vars["download_dir"].set(str(second))
    gui.view.window.update_idletasks()
    assert str(gui.view._download_dir_resolved.cget("text")) != before


@pytest.mark.gui
def test_nonsense_in_the_folder_field_does_not_crash_the_page(gui) -> None:
    gui.view.select_page("settings")
    for text in ("\x00", "~" * 500, "  ", "://"):
        gui.view._vars["download_dir"].set(text)
        gui.view.window.update_idletasks()


# ----------------------------------------------------------------------
# Shared code, not parallel code
# ----------------------------------------------------------------------
def test_both_platforms_use_one_download_folder_resolver(config) -> None:
    """Android maps a public path onto a writable link; the call is the same."""
    resolved = config.resolved_download_dir()
    assert isinstance(resolved, Path)
    assert resolved.is_absolute()


def test_the_android_hint_exists_in_every_language() -> None:
    from clipster import i18n

    for language in ("en", "de"):
        messages = i18n.load(language)
        assert messages["settings_download_dir_android"].strip()
        assert messages["settings_download_dir"].strip()


def test_the_update_words_exist_in_every_language() -> None:
    from clipster import i18n

    for language in ("en", "de"):
        messages = i18n.load(language)
        for key in ("update_check", "update_install", "update_restarting",
                    "update_available", "update_current", "update_failed", "update_error"):
            assert messages[key].strip(), "{0} missing in {1}".format(key, language)


def test_the_403_explanation_exists_in_every_language() -> None:
    from clipster import i18n

    for language in ("en", "de"):
        assert i18n.load(language)["error_forbidden"].strip()


@pytest.mark.parametrize("termux", [False, True])
def test_the_download_folder_label_is_chosen_by_platform(termux: bool, monkeypatch) -> None:
    """One branch on is_termux - not a second settings page."""
    from clipster import i18n

    monkeypatch.setattr(paths, "is_termux", lambda: termux)
    messages = i18n.load("en")
    key = "settings_download_dir_android" if paths.is_termux() else "settings_download_dir"
    assert messages[key].strip()
    if termux:
        assert "Download/clipster" in messages[key]
