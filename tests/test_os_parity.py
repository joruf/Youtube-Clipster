"""Every platform gets the same window, built the same way.

A platform that quietly offers less is a bug, not a platform difference.  These
tests build the real windows with ``paths`` pretending to be each operating
system in turn and compare what came out - so a control can never go missing on
one platform while the developer only ever looks at another.

Only genuinely impossible things may differ, and those have to differ *visibly*:
a disabled control with an explanation, never a missing one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

pytestmark = pytest.mark.gui

#: The operating systems the program claims to support.
_PLATFORMS = ("linux", "windows", "macos", "termux")


def _build(platform: str, tmp_path: Path):
    """Build the GUI with ``paths`` reporting ``platform``.

    :param platform: One of :data:`_PLATFORMS`.
    :return: The ready :class:`~clipster.gui.Gui`.
    """
    from clipster import i18n, paths
    from clipster.config import Config
    from clipster.gui import Gui
    from clipster.terms import TERMS_APP_VERSION, TERMS_STREAMING_VERSION

    downloads = tmp_path / platform
    downloads.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.path = tmp_path / "{0}.json".format(platform)
    config.download_dir = str(downloads)
    config.use_tray = False
    config.ask_desktop_shortcut = False
    config.show_startup_notification = False
    config.language = "en"
    config.terms_app_version = TERMS_APP_VERSION
    config.terms_streaming_version = TERMS_STREAMING_VERSION

    gui = Gui(i18n.load("en"), config, downloads)
    gui.build_windows()
    gui.render_history([])
    return gui


@pytest.fixture()
def as_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Return a factory that builds the GUI as a named operating system."""
    from clipster import paths

    built: List[object] = []

    def factory(platform: str):
        monkeypatch.setattr(paths, "IS_WINDOWS", platform == "windows")
        monkeypatch.setattr(paths, "IS_MACOS", platform == "macos")
        monkeypatch.setattr(paths, "IS_LINUX", platform in ("linux", "termux"))
        monkeypatch.setattr(paths, "is_termux", lambda: platform == "termux")
        gui = _build(platform, tmp_path)
        built.append(gui)
        return gui

    try:
        yield factory
    finally:
        for gui in built:
            try:
                gui.destroy()
            except Exception:  # pragma: no cover - teardown must not fail a test
                pass


def _buttons(widget) -> List[str]:
    """Return the label of every button below ``widget``, in creation order."""
    found: List[str] = []
    for child in widget.winfo_children():
        if child.winfo_class() == "TButton":
            try:
                found.append(str(child.cget("text")))
            except Exception:  # pragma: no cover - defensive
                pass
        found.extend(_buttons(child))
    return found


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_the_update_button_exists_on_every_platform(as_platform, platform: str) -> None:
    """Reported missing on Windows; pinned here so it cannot go missing anywhere."""
    gui = as_platform(platform)
    gui.show_view("about")
    gui.root.update_idletasks()
    button = gui.view._update_button
    assert button.winfo_manager() == "pack", "no update button on {0}".format(platform)
    assert "disabled" not in button.state(), "update disabled on {0}".format(platform)
    assert str(button.cget("text")).strip(), "the update button has no label"


#: Pages every platform has to offer.
_SHARED_PAGES = ("discover", "downloads", "settings", "about")


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_every_page_exists_on_every_platform(as_platform, platform: str) -> None:
    gui = as_platform(platform)
    for page in _SHARED_PAGES:
        assert page in gui.view._pages, "{0} has no {1} page".format(platform, page)


@pytest.mark.parametrize("platform", ["linux", "windows", "macos"])
def test_the_desktop_platforms_all_offer_the_phone_page(as_platform, platform: str) -> None:
    """Remote control belongs on every desktop, not just the developer's."""
    gui = as_platform(platform)
    assert "phone" in gui.view._pages


def test_only_android_may_drop_the_phone_page(as_platform) -> None:
    """The one allowed difference, and it is deliberate.

    On Android the program *is* the phone, so a page for controlling a phone
    from elsewhere would point at itself.  Asserted rather than merely omitted,
    so this stays the single documented exception instead of becoming a habit.
    """
    gui = as_platform("termux")
    assert "phone" not in gui.view._pages
    assert "phone" not in gui.view._menu_buttons


def test_the_about_page_offers_the_same_controls_everywhere(as_platform) -> None:
    """Same buttons, same order - the page is built once, not per platform."""
    seen: Dict[str, List[str]] = {}
    for platform in _PLATFORMS:
        gui = as_platform(platform)
        gui.show_view("about")
        gui.root.update_idletasks()
        seen[platform] = _buttons(gui.view._pages["about"])
    reference = seen["linux"]
    assert reference, "the about page has no buttons at all"
    for platform, buttons in seen.items():
        assert buttons == reference, "{0} differs: {1} != {2}".format(
            platform, buttons, reference
        )


def test_the_download_rows_offer_the_same_actions_everywhere(as_platform) -> None:
    """Including the ones that remove a failed entry."""
    from clipster import viewwindow
    from clipster.history import STATUS_FAILED, HistoryEntry

    entry = HistoryEntry(
        name="broken.mp3", media_format="mp3", status=STATUS_FAILED,
        url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        error="boom", error_kind="forbidden", finished_at="2026-08-19T21:28:37",
    )
    for platform in _PLATFORMS:
        gui = as_platform(platform)
        gui.render_history([entry])
        gui.show_view("downloads")
        gui.root.update_idletasks()
        _separator, row, _entry = gui.view._row_items[0]
        frames = [w for w in row.winfo_children() if w.winfo_class() == "TFrame"]
        actions = [w for w in frames[-1].winfo_children() if w.winfo_class() == "TButton"]
        assert len(actions) == len(viewwindow._ROW_ACTIONS), (
            "{0} shows {1} row actions instead of {2}".format(
                platform, len(actions), len(viewwindow._ROW_ACTIONS)
            )
        )


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_the_window_title_names_the_version_on_every_platform(as_platform, platform: str) -> None:
    import clipster

    gui = as_platform(platform)
    assert clipster.APP_VERSION_FULL in gui.view.window.title()
    assert clipster.APP_VERSION_FULL in gui.root.title()


def test_the_phone_page_declares_its_update_button() -> None:
    """The Android shell renders this HTML, so this is Android's update button.

    Checked in the file rather than in a browser: there is no platform branch in
    the page, and this test is what keeps it that way.
    """
    from clipster import paths

    page = Path(paths.PROJECT_ROOT) / "clipster" / "web" / "index.html"
    markup = page.read_text(encoding="utf-8")
    assert 'id="update-button"' in markup
    assert 'id="about-version"' in markup
