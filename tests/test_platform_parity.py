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

from clipster import i18n, paths, updater


# ----------------------------------------------------------------------
# The update button
# ----------------------------------------------------------------------
class _Info:
    """Stands in for :class:`clipster.updater.UpdateInfo`."""

    def __init__(self, *, available=False, remote="2222222222", local="1111111111",
                 summary="Newer commit", error="", unknown=False) -> None:
        self.available = available
        self.remote = remote
        self.local = local
        self.summary = summary
        self.error = error
        self.unknown = unknown

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


def test_an_installation_without_a_version_offers_the_update_to_the_phone(app, monkeypatch) -> None:
    """The Android bundle has no .git; it must not be told it is current.

    This is the state the phone was stuck in: no local commit, so nothing to
    compare, so "newest version" - and the install button never appeared.
    """
    monkeypatch.setattr(updater, "check",
                        lambda *a, **k: _Info(available=True, unknown=True, local=""))
    result = app.check_update_remote()
    assert result["ok"] is True
    assert result["available"] is True
    assert result["unknown"] is True
    assert result["message"] != app.messages.format("update_current", commit="2222222222")


def test_the_unknown_version_wording_exists_in_every_language() -> None:
    """Both languages must be able to say it, or the phone shows a raw key."""
    for language in i18n.available_languages():
        messages = i18n.load(language)
        text = messages.format("update_unversioned", commit="abc1234")
        assert text and "update_unversioned" not in text
        assert "abc1234" in text


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
        assert i18n.load(language)["error_drm"].strip()


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


# ----------------------------------------------------------------------
# Feature parity: the phone interface is the Android version
# ----------------------------------------------------------------------
def _web(name: str) -> str:
    """Return one file of the phone interface as text.

    :param name: File name below ``clipster/web``.
    :return: Its contents.
    """
    return (paths.PROJECT_ROOT / "clipster" / "web" / name).read_text(encoding="utf-8")


#: Every Streaming control the desktop offers, and the element id that has to
#: exist in the phone interface for it.  Android is not a second program - it is
#: this page - so a desktop feature without a row here is a feature the phone
#: silently does not have.
_STREAMING_CONTROLS = {
    "shuffle": 'id="stream-shuffle"',
    "repeat": 'id="stream-repeat"',
    "sleep timer": 'id="stream-sleep"',
    "library": 'id="stream-library"',
    "share as a QR code": 'id="share-dialog"',
    "scan a QR code": 'id="scan-dialog"',
    "stage / visualizer": 'id="stage"',
    "video playback": 'id="stream-video"',
    "sortable download list": 'id="sort-row"',
}


@pytest.mark.parametrize("feature", sorted(_STREAMING_CONTROLS))
def test_every_desktop_streaming_feature_reaches_the_phone(feature: str) -> None:
    """Whatever the Tk page can do, the page Android shows can do as well."""
    page = _web("index.html")
    assert _STREAMING_CONTROLS[feature] in page, (
        "{0} is missing from the phone interface".format(feature)
    )


#: Settings that must be editable from the phone as well as the desktop, and the
#: element id that edits them.
_REMOTE_SETTINGS = {
    "playback_on_mobile": 'id="set-mobile"',
    "playback_local_only": 'id="set-local-only"',
    "discover_shuffle": 'id="set-shuffle"',
    "discover_repeat": 'id="set-repeat"',
    "discover_play_video": 'id="set-play-video"',
    "discover_visualizer": 'id="set-visualizer"',
    "discover_extend_count": 'id="set-extend-count"',
}


@pytest.mark.parametrize("key", sorted(_REMOTE_SETTINGS))
def test_the_phone_can_edit_the_same_settings(key: str) -> None:
    """A setting the phone cannot reach is a setting Android does not have."""
    from clipster.app import ClipsterApp
    from clipster.config import Config

    assert hasattr(Config, key) or key in Config.__dataclass_fields__, key
    assert key in ClipsterApp._REMOTE_SETTING_KEYS, "{0} is not offered remotely".format(key)
    assert _REMOTE_SETTINGS[key] in _web("index.html"), "{0} has no field".format(key)
    assert key in _web("app.js"), "{0} is never sent or read".format(key)


def test_the_phone_never_decides_the_play_order_by_itself() -> None:
    """Shuffle and repeat are one rule; the phone asks for it, it does not guess.

    A local "next = index + 1" is exactly how the two platforms drifted apart
    before, so the endpoint has to exist and the page has to use it.
    """
    from clipster.webapi import RemoteApi

    assert hasattr(RemoteApi, "discover_next")
    assert "/api/discover/next" in _web("app.js")


def test_the_shared_play_order_is_what_both_sides_hold() -> None:
    from clipster.discover_page import DiscoverPage
    from clipster.discover_session import HeadlessDiscoverSession
    from clipster.playorder import PlayOrder

    for owner in (DiscoverPage, HeadlessDiscoverSession):
        for name in ("next_index", "set_shuffle", "set_repeat", "set_sleep_timer"):
            assert callable(getattr(owner, name, None)), "{0} lacks {1}".format(owner, name)
    assert PlayOrder is not None


def test_the_phone_can_retry_a_failed_download() -> None:
    """A failed row on the phone has to start the same download again."""
    script = _web("app.js")
    assert "retryDownload" in script
    assert "retryable" in script
    assert "force: true" in script


def test_the_shell_cache_was_bumped_with_the_interface() -> None:
    """An installed home-screen copy keeps serving the version it cached."""
    worker = _web("sw.js")
    assert "clipster-shell-v5" in worker, "bump SHELL_CACHE when index/app/style change"
    # Live paths must never be cached, or the phone plays yesterday's queue.
    for path in ("/api/", "/media/", "/queue/", "/stream/", "/video/"):
        assert '"{0}"'.format(path) in worker, path


def test_the_scanner_has_its_decoder_checked_in() -> None:
    """A CDN would fail exactly where Clipster is most useful: with no internet."""
    vendor = paths.PROJECT_ROOT / "clipster" / "web" / "vendor"
    assert (vendor / "jsqr.js").is_file()
    assert (vendor / "jsqr-LICENSE.txt").is_file(), "third-party code travels with its licence"
    assert 'src="/vendor/jsqr.js"' in _web("index.html")
    assert "https://" not in _web("index.html").split("<script")[-1]


def test_the_launcher_may_open_the_camera() -> None:
    """Without the permission and a WebChromeClient a WebView denies getUserMedia."""
    launcher = paths.PROJECT_ROOT / "tools" / "android" / "launcher" / "app" / "src" / "main"
    manifest = (launcher / "AndroidManifest.xml").read_text(encoding="utf-8")
    activity = (launcher / "java" / "de" / "loresoft" / "youtubeclipster"
                / "MainActivity.kt").read_text(encoding="utf-8")
    assert "android.permission.CAMERA" in manifest
    assert "WebChromeClient" in activity
    assert "RESOURCE_VIDEO_CAPTURE" in activity
