"""One version, visible everywhere, and never out of step with itself.

A build number is only worth having if every surface reports the same one.  The
Android APK carries its version in Gradle, the desktop windows in their title
bars and the phone page in its About section - three places that cannot import
each other, so they are checked against :mod:`clipster` here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import clipster

ROOT = Path(__file__).resolve().parent.parent
GRADLE = ROOT / "tools" / "android" / "launcher" / "app" / "build.gradle.kts"


def test_the_version_is_a_three_part_number() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", clipster.APP_VERSION), clipster.APP_VERSION


def test_the_build_number_counts_up() -> None:
    assert isinstance(clipster.APP_BUILD, int)
    assert clipster.APP_BUILD > 0


def test_the_full_version_names_both_parts() -> None:
    """``2.1.0 (4)`` - the format Fundus uses, so the projects read alike."""
    assert clipster.APP_VERSION_FULL == "{0} ({1})".format(
        clipster.APP_VERSION, clipster.APP_BUILD
    )


def test_every_title_carries_the_build() -> None:
    """A screenshot of any window has to say which build it came from."""
    for title in (clipster.APP_TITLE, clipster.APP_WINDOW_TITLE):
        assert clipster.APP_VERSION_FULL in title, title


def test_the_android_apk_reports_the_same_version() -> None:
    """An APK whose version differs from the program inside it misleads.

    Gradle cannot import Python, so the two are compared here rather than
    generated from one another.
    """
    source = GRADLE.read_text(encoding="utf-8")
    name = re.search(r'versionName\s*=\s*"([^"]+)"', source)
    code = re.search(r"versionCode\s*=\s*(\d+)", source)
    assert name is not None and code is not None, "the launcher declares no version"
    assert name.group(1) == clipster.APP_VERSION
    assert int(code.group(1)) == clipster.APP_BUILD


@pytest.mark.gui
def test_every_window_title_shows_the_version(gui) -> None:
    titles = [gui.root.title(), gui.view.window.title()]
    if gui.nav is not None:
        titles.append(gui.nav.window.title())
    for title in titles:
        assert clipster.APP_VERSION_FULL in title, title


@pytest.mark.gui
def test_the_about_page_shows_the_full_version(gui) -> None:
    gui.show_view("about")
    gui.root.update_idletasks()
    page = gui.view._pages["about"]
    texts = []

    def walk(widget) -> None:
        for child in widget.winfo_children():
            if child.winfo_class() in ("TLabel", "Label"):
                try:
                    texts.append(str(child.cget("text")))
                except Exception:  # pragma: no cover - defensive
                    pass
            walk(child)

    walk(page)
    assert any(clipster.APP_VERSION_FULL in text for text in texts), texts


def test_the_phone_page_is_told_the_full_version(config, messages) -> None:
    """The Android shell renders this page, so it is Android's version display."""
    from clipster.app import ClipsterApp

    app = ClipsterApp(config, messages)
    assert app.remote_about()["version"] == clipster.APP_VERSION_FULL


def test_the_command_line_prints_the_version_once(capsys) -> None:
    from clipster.cli import print_version

    print_version()
    printed = capsys.readouterr().out.strip()
    assert printed.count(clipster.APP_VERSION) == 1, printed
    assert clipster.APP_VERSION_FULL in printed
