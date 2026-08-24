"""Where downloads land, and how Settings decides it.

The folder is chosen in Settings and defaults to the system Downloads directory
plus a ``clipster`` subfolder - the same shape Android always used.  Two things
follow from that subfolder and are pinned here: an explicit choice must never
have it appended, and the folder does not exist until something creates it, so
nothing may hand an unmade path to a file dialog or a file manager.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipster import paths
from clipster.config import Config


# ----------------------------------------------------------------------
# The default
# ----------------------------------------------------------------------
def test_the_default_is_the_system_folder_plus_a_subfolder(monkeypatch: pytest.MonkeyPatch,
                                                           tmp_path: Path) -> None:
    """Downloads stay together instead of mixing into everything else there."""
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    monkeypatch.setattr(paths, "system_download_dir", lambda: tmp_path / "Downloads")
    assert paths.default_download_dir() == tmp_path / "Downloads" / paths.DOWNLOAD_SUBFOLDER


def test_the_subfolder_is_named_clipster() -> None:
    """The name is in the config of every user, so it is not free to change."""
    assert paths.DOWNLOAD_SUBFOLDER == "clipster"


def test_the_default_is_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    assert paths.default_download_dir().is_absolute()


def test_an_empty_setting_means_the_default(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    monkeypatch.setattr(paths, "system_download_dir", lambda: tmp_path / "Downloads")
    config = Config()
    config.download_dir = ""
    assert config.resolved_download_dir() == paths.default_download_dir()


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_setting_counts_as_empty(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    config = Config()
    config.download_dir = blank
    assert config.resolved_download_dir() == paths.default_download_dir()


# ----------------------------------------------------------------------
# An explicit choice stays exactly what was chosen
# ----------------------------------------------------------------------
def test_a_chosen_folder_gets_no_subfolder_appended(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    """Picking a folder in Settings means that folder, not one inside it.

    The subfolder is a *default*, and quietly appending it to a deliberate
    choice would put the files somewhere the user did not point at.
    """
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    target = tmp_path / "somewhere else"
    config = Config()
    config.download_dir = str(target)
    assert config.resolved_download_dir() == target


def test_choosing_the_system_folder_itself_is_respected(monkeypatch: pytest.MonkeyPatch,
                                                        tmp_path: Path) -> None:
    """Someone who wants the bare Downloads folder has to be able to say so."""
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    system = tmp_path / "Downloads"
    monkeypatch.setattr(paths, "system_download_dir", lambda: system)
    config = Config()
    config.download_dir = str(system)
    assert config.resolved_download_dir() == system
    assert config.resolved_download_dir() != paths.default_download_dir()


def test_a_tilde_in_the_setting_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    config = Config()
    config.download_dir = "~/Music/clips"
    resolved = config.resolved_download_dir()
    assert resolved.is_absolute()
    assert "~" not in str(resolved)


# ----------------------------------------------------------------------
# Android keeps its shared folder
# ----------------------------------------------------------------------
def test_android_still_defaults_to_the_shared_phone_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Termux's private ``~/Downloads`` is invisible to any file manager."""
    monkeypatch.setattr(paths, "is_termux", lambda: True)
    assert paths.default_download_dir() == paths.ANDROID_PUBLIC_DOWNLOAD


def test_android_uses_the_same_subfolder_name() -> None:
    assert paths.ANDROID_PUBLIC_DOWNLOAD.name == paths.DOWNLOAD_SUBFOLDER


def test_every_platform_ends_in_the_same_subfolder(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    """Parity: the folder is named the same wherever Clipster runs."""
    for termux in (False, True):
        monkeypatch.setattr(paths, "is_termux", lambda t=termux: t)
        monkeypatch.setattr(paths, "system_download_dir", lambda: tmp_path / "Downloads")
        assert paths.default_download_dir().name == paths.DOWNLOAD_SUBFOLDER


# ----------------------------------------------------------------------
# The folder has to be created before anything shows it
# ----------------------------------------------------------------------
def test_the_folder_is_created_when_it_is_opened(config, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    """"Open folder" used to be safe only because the system folder exists."""
    from clipster import shortcuts
    from clipster.app import ClipsterApp
    from clipster import i18n

    target = tmp_path / "Downloads" / paths.DOWNLOAD_SUBFOLDER
    config.download_dir = str(target)
    opened = []
    monkeypatch.setattr(shortcuts, "open_folder", lambda path, manager: opened.append(path))

    app = ClipsterApp(config, i18n.load("en"))
    assert not target.exists(), "the fixture already created it"
    app._open_download_folder()
    assert target.is_dir(), "the folder was shown without being created"
    assert opened == [target]


def test_a_folder_that_cannot_be_created_does_not_raise(config, tmp_path: Path) -> None:
    """A bad path belongs in Settings; a crash keeps the user from getting there."""
    from clipster.app import ClipsterApp
    from clipster import i18n

    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    config.download_dir = str(blocker / "below")

    app = ClipsterApp(config, i18n.load("en"))
    assert app._ensure_download_dir() is False


def test_creating_an_existing_folder_is_not_an_error(config, tmp_path: Path) -> None:
    from clipster.app import ClipsterApp
    from clipster import i18n

    target = tmp_path / "already there"
    target.mkdir()
    config.download_dir = str(target)
    app = ClipsterApp(config, i18n.load("en"))
    assert app._ensure_download_dir() is True
    assert app._ensure_download_dir() is True


# ----------------------------------------------------------------------
# The Settings picker
# ----------------------------------------------------------------------
def test_the_picker_opens_at_the_nearest_existing_folder(tmp_path: Path) -> None:
    """A missing ``initialdir`` sends the dialog somewhere different per platform."""
    from clipster.viewwindow import _nearest_existing

    existing = tmp_path / "Downloads"
    existing.mkdir()
    assert _nearest_existing(existing / paths.DOWNLOAD_SUBFOLDER) == existing
    assert _nearest_existing(existing / "a" / "b" / "c") == existing
    assert _nearest_existing(existing) == existing


def test_the_picker_never_returns_a_missing_folder(tmp_path: Path) -> None:
    from clipster.viewwindow import _nearest_existing

    assert _nearest_existing(Path("/definitely/not/here/at/all")).is_dir()


# ----------------------------------------------------------------------
# The setting has to be on screen, not merely built
# ----------------------------------------------------------------------
def _settings_cards(view) -> dict:
    """Return every settings card by its heading.

    :param view: The view window.
    :return: ``{heading: widget}``.
    """
    found = {}

    def walk(widget) -> None:
        for child in widget.winfo_children():
            if child.winfo_class() == "TLabelframe":
                try:
                    found[str(child.cget("text"))] = child
                except Exception:  # pragma: no cover - defensive
                    pass
            walk(child)

    walk(view._pages["settings"])
    return found


def _download_dir_entry(view):
    """Return the entry bound to the download-dir variable, or ``None``."""
    wanted = str(view._vars["download_dir"])
    found = []

    def walk(widget) -> None:
        for child in widget.winfo_children():
            if child.winfo_class() == "TEntry":
                try:
                    if str(child.cget("textvariable")) == wanted:
                        found.append(child)
                except Exception:  # pragma: no cover - defensive
                    pass
            walk(child)

    walk(view._pages["settings"])
    return found[0] if found else None


def _settle(gui) -> None:
    for _ in range(10):
        gui.root.update_idletasks()
        gui.root.update()


@pytest.mark.gui
def test_no_settings_card_is_squeezed_to_nothing(gui) -> None:
    """The bug this pins down: pack gave the Streaming card the whole page.

    General and Behaviour - the download folder among them - ended up one pixel
    tall, so the setting existed and could not be seen or reached at all.
    """
    gui.show_view("settings")
    _settle(gui)
    cards = _settings_cards(gui.view)
    assert cards, "the settings page has no cards"
    for heading, card in cards.items():
        assert card.winfo_height() > 1, "{0} is {1} px tall".format(heading, card.winfo_height())
        assert card.winfo_width() > 1, "{0} is {1} px wide".format(heading, card.winfo_width())


@pytest.mark.gui
def test_the_download_folder_can_be_seen_and_typed_into(gui) -> None:
    gui.show_view("settings")
    _settle(gui)
    entry = _download_dir_entry(gui.view)
    assert entry is not None, "there is no download folder field"
    assert entry.winfo_ismapped(), "the field is built but not on screen"
    assert entry.winfo_width() > 1 and entry.winfo_height() > 1


@pytest.mark.gui
def test_the_resolved_folder_is_shown_next_to_the_field(gui) -> None:
    """Seeing the path matters as much as setting it - the default is implicit."""
    gui.show_view("settings")
    _settle(gui)
    label = gui.view._download_dir_resolved
    assert label.winfo_ismapped()
    assert str(gui.view.config.resolved_download_dir()) in str(label.cget("text"))


@pytest.mark.gui
def test_the_folder_can_be_reached_without_scrolling(gui) -> None:
    """It is the setting people open Settings for, so it comes before the rest."""
    gui.show_view("settings")
    _settle(gui)
    entry = _download_dir_entry(gui.view)
    window = gui.view.window
    top = window.winfo_rooty()
    assert top <= entry.winfo_rooty() <= top + window.winfo_height()


@pytest.mark.gui
def test_the_settings_page_scrolls(gui) -> None:
    """The form is taller than the window; without this the overflow is lost."""
    gui.show_view("settings")
    _settle(gui)
    scroller = gui.view._settings_scroller
    assert scroller.winfo_ismapped()
    assert scroller.body.winfo_height() > scroller.winfo_height(), (
        "the form fits, so this test no longer proves scrolling works"
    )


@pytest.mark.gui
def test_the_browse_button_sits_beside_the_field(gui) -> None:
    gui.show_view("settings")
    _settle(gui)
    entry = _download_dir_entry(gui.view)
    siblings = [
        child for child in entry.master.winfo_children() if child.winfo_class() == "TButton"
    ]
    assert len(siblings) == 1, "expected exactly one Browse button next to the field"
    assert siblings[0].winfo_ismapped()
