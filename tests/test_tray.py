"""The system tray icon and its backend selection."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from clipster import paths, tray


def _icon(messages) -> tray.TrayIcon:
    """Return a tray icon that was never started."""
    return tray.TrayIcon(messages, paths.icon_file(), lambda: None, lambda: None, lambda: None)


# ----------------------------------------------------------------------
# Safety of the inactive icon
# ----------------------------------------------------------------------
def test_an_unstarted_icon_claims_nothing(messages) -> None:
    icon = _icon(messages)
    assert icon.active is False
    assert icon.backend == ""


def test_every_call_on_an_inactive_icon_is_harmless(messages) -> None:
    """The application uses these unconditionally, tray or not."""
    icon = _icon(messages)
    assert icon.notify("hello") is False
    icon.set_tooltip("anything")
    icon.stop()


def test_capabilities_default_to_optimistic(messages) -> None:
    icon = _icon(messages)
    assert icon.has_menu is True
    assert icon.has_default_action is True


def test_a_menu_action_never_escapes_as_an_exception(messages) -> None:
    """A failing callback must not kill the pystray thread."""
    def explode() -> None:
        raise RuntimeError("boom")

    icon = tray.TrayIcon(messages, paths.icon_file(), explode, explode, explode)
    icon._handle_show()
    icon._handle_open_folder()
    icon._handle_quit()


# ----------------------------------------------------------------------
# Icon image
# ----------------------------------------------------------------------
def test_the_application_icon_is_scaled(messages) -> None:
    pytest.importorskip("PIL")
    image = tray._load_image(paths.icon_file())
    assert max(image.size) == tray.ICON_SIZE


def test_a_missing_icon_falls_back_to_a_drawn_one() -> None:
    pytest.importorskip("PIL")
    image = tray._load_image(Path("/nope/missing.png"))
    assert image.size == (tray.ICON_SIZE, tray.ICON_SIZE)


def test_the_placeholder_is_square() -> None:
    pytest.importorskip("PIL")
    assert tray._placeholder_image().size == (tray.ICON_SIZE, tray.ICON_SIZE)


# ----------------------------------------------------------------------
# Backend preference
# ----------------------------------------------------------------------
def test_gtk_is_preferred_because_it_can_do_both(monkeypatch: pytest.MonkeyPatch) -> None:
    """AppIndicator shows a menu but ignores clicks; X11 the other way round."""
    monkeypatch.setattr(paths, "IS_LINUX", True)
    monkeypatch.delenv("PYSTRAY_BACKEND", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    assert tray._preferred_backends()[0] == "gtk"


def test_gnome_gets_appindicator_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """GNOME's shell does not show the status icon GTK relies on."""
    monkeypatch.setattr(paths, "IS_LINUX", True)
    monkeypatch.delenv("PYSTRAY_BACKEND", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert tray._preferred_backends()[0] == "appindicator"


def test_a_pinned_backend_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "IS_LINUX", True)
    monkeypatch.setenv("PYSTRAY_BACKEND", "xorg")
    assert tray._preferred_backends() == ()


def test_other_platforms_keep_their_single_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "IS_LINUX", False)
    monkeypatch.delenv("PYSTRAY_BACKEND", raising=False)
    assert tray._preferred_backends() == ()


def test_every_candidate_is_a_real_pystray_backend() -> None:
    assert set(tray._BACKEND_PREFERENCE) <= {"gtk", "appindicator", "xorg", "win32", "darwin"}


def test_forgetting_pystray_clears_the_module_cache() -> None:
    sys.modules["pystray"] = object()          # type: ignore[assignment]
    sys.modules["pystray._fake"] = object()    # type: ignore[assignment]
    tray._forget_pystray()
    assert "pystray" not in sys.modules
    assert "pystray._fake" not in sys.modules


@pytest.mark.gui
def test_the_icon_really_starts_and_offers_a_quit_entry(messages) -> None:
    """The whole point of the tray: a way to open the window and to quit."""
    pytest.importorskip("pystray")
    hits: list = []
    icon = tray.TrayIcon(messages, paths.icon_file(),
                         on_show=lambda: hits.append("show"),
                         on_open_folder=lambda: hits.append("folder"),
                         on_quit=lambda: hits.append("quit"))
    if not icon.start(timeout=8.0):
        pytest.skip("no usable tray backend in this session")
    try:
        assert icon.active
        assert icon.backend
        entries = [str(item.text) for item in icon._icon.menu] if icon.has_menu else []
        if icon.has_menu:
            assert messages["window_quit"] in entries
            assert messages["tray_show"] in entries
        assert icon.has_menu or icon.has_default_action, \
            "a tray icon that neither opens a menu nor reacts to a click is useless"
        icon._handle_quit()
        assert hits == ["quit"]
    finally:
        icon.stop()
