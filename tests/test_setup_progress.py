"""Tests for bootstrap progress reporting and setup splash helpers.

The setup phase is the one part of the program that runs before anything is
installed and - started from ``run.bat`` - without a console.  Whatever happens
there has to reach the user through a window, otherwise a long first start looks
like a program that simply does not work.
"""

from __future__ import annotations

import io

import pytest

from clipster import cli, i18n, logging_setup, setup_ui
from clipster.installer import InstallReport, Step, bootstrap


def test_bootstrap_reports_progress(monkeypatch) -> None:
    messages = []

    monkeypatch.setattr("clipster.installer.check_python", lambda: __import__("clipster.installer", fromlist=["Step"]).Step(name="Python", ok=True))
    monkeypatch.setattr(
        "clipster.installer.check_tkinter",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="tkinter", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_venv",
        lambda recreate=False: __import__("clipster.installer", fromlist=["Step"]).Step(name="venv", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_ytdlp",
        lambda **_k: __import__("clipster.installer", fromlist=["Step"]).Step(name="yt-dlp", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_ffmpeg",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="FFmpeg", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_mpv",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="mpv", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_clipboard_tool",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="clipboard", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_tray_menu",
        lambda interpreter=None, auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(
            name="tray-menu", ok=True
        ),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_tray_support",
        lambda interpreter=None, auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(
            name="tray", ok=True
        ),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_js_runtime",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="js", ok=True),
    )
    monkeypatch.setattr("clipster.installer.paths.ensure_install_dir", lambda: None)
    monkeypatch.setattr("clipster.installer.paths.venv_python", lambda: __import__("pathlib").Path("/tmp/venv/bin/python"))

    report = bootstrap(on_progress=messages.append, use_venv=True)
    assert report.ok
    assert any("Python" in item for item in messages)
    assert any("yt-dlp" in item for item in messages)
    assert messages[-1].lower().startswith("dependency check finished")


def test_ensure_mpv_missing_does_not_fail_windows(monkeypatch) -> None:
    """Optional mpv must never block startup (Windows has no apt install)."""
    from clipster.installer import Step, ensure_mpv

    monkeypatch.setattr("clipster.installer.find_mpv", lambda: None)
    monkeypatch.setattr("clipster.installer.paths.IS_WINDOWS", True)
    monkeypatch.setattr("clipster.installer.paths.IS_LINUX", False)
    step = ensure_mpv(auto_install=True)
    assert isinstance(step, Step)
    assert step.ok is True
    assert "not installed" in step.detail.lower() or "mpv" in step.hint.lower()


def test_ensure_mpv_missing_does_not_fail_linux_without_install(monkeypatch) -> None:
    from clipster.installer import ensure_mpv

    monkeypatch.setattr("clipster.installer.find_mpv", lambda: None)
    monkeypatch.setattr("clipster.installer.paths.IS_WINDOWS", False)
    monkeypatch.setattr("clipster.installer.paths.IS_LINUX", True)
    step = ensure_mpv(auto_install=False)
    assert step.ok is True


def test_run_command_suppresses_console_on_windows(monkeypatch) -> None:
    """pip/venv children must not flash a console under pythonw.exe."""
    import subprocess

    from clipster import paths
    from clipster.installer import run_command

    captured = {}

    class FakeStdout:
        def __iter__(self):
            return iter(())

    class FakeProc:
        stdout = FakeStdout()
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr("clipster.installer.subprocess.Popen", fake_popen)
    # Also patch the helper so the flag is present even if the host OS is Linux.
    monkeypatch.setattr(
        "clipster.installer._no_window",
        lambda: {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)},
    )
    result = run_command(["echo", "ok"], echo=False, timeout=5.0)
    assert result.ok
    assert captured.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ----------------------------------------------------------------------
# The status line never breaks the window layout
# ----------------------------------------------------------------------
def test_a_short_status_is_left_alone() -> None:
    assert setup_ui.trim_status("Checking FFmpeg...") == "Checking FFmpeg..."


def test_whitespace_is_collapsed() -> None:
    assert setup_ui.trim_status("Checking\n  FFmpeg  ") == "Checking FFmpeg"


def test_a_long_status_is_ellipsised_in_the_middle() -> None:
    """A long install path must not be able to stretch the setup window."""
    text = setup_ui.trim_status("A" * 40 + "B" * 40 + "C" * 40, max_length=40)
    assert len(text) <= 40
    assert text.startswith("A") and text.endswith("C") and "..." in text


# ----------------------------------------------------------------------
# A failed setup has to be visible
# ----------------------------------------------------------------------
def _failed_report(count: int = 1) -> InstallReport:
    """Return a report with ``count`` failed steps."""
    report = InstallReport()
    for index in range(count):
        report.steps.append(
            Step(name="Component {0}".format(index), ok=False,
                 detail="not found", hint="install it manually")
        )
    return report


def test_the_summary_names_the_component_and_the_hint() -> None:
    text = cli.summarize_failures(_failed_report())
    assert "Component 0" in text
    assert "not found" in text
    assert "install it manually" in text


def test_a_long_list_of_failures_is_shortened() -> None:
    text = cli.summarize_failures(_failed_report(12), max_lines=3)
    assert "Component 2" in text
    assert "Component 11" not in text
    assert "9 more" in text


def test_a_missing_detail_still_says_something() -> None:
    report = InstallReport()
    report.steps.append(Step(name="FFmpeg", ok=False))
    assert "missing" in cli.summarize_failures(report)


def test_a_terminal_counts_as_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(cli.sys, "stderr", Tty())
    assert cli._console_is_visible() is True


def test_a_redirected_stream_is_not_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Linux desktop launcher sends stderr into the session log."""
    monkeypatch.setattr(cli.sys, "stderr", io.StringIO())
    assert cli._console_is_visible() is False


def test_pythonw_has_no_stream_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "stderr", None)
    assert cli._console_is_visible() is False


def test_without_a_console_the_failure_is_shown_in_a_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = []
    monkeypatch.setattr(cli, "_console_is_visible", lambda: False)
    monkeypatch.setattr(setup_ui, "show_setup_failure",
                        lambda title, text: shown.append((title, text)) or True)
    cli._report_failures(_failed_report(), i18n.load("en"))
    assert len(shown) == 1
    title, text = shown[0]
    assert "Component 0" in text
    assert title


def test_with_a_console_no_dialog_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_console_is_visible", lambda: True)
    monkeypatch.setattr(setup_ui, "show_setup_failure",
                        lambda title, text: pytest.fail("a terminal user does not need a modal"))
    cli._report_failures(_failed_report(), i18n.load("en"))


def test_the_dialog_texts_are_translated() -> None:
    for language in ("en", "de"):
        messages = i18n.load(language)
        assert messages["setup_failed_title"]
        assert messages["setup_failed_intro"]
        assert messages["setup_starting"]


# ----------------------------------------------------------------------
# The first start must not disappear into the tray
# ----------------------------------------------------------------------
def _parsed(*switches: str):
    """Return parsed arguments for the given switches."""
    return cli.build_parser().parse_args(list(switches))


def test_the_first_start_opens_the_window() -> None:
    result = cli._first_start_arguments([], _parsed(), first_start=True)
    assert result == ["--show-window"]


def test_later_starts_respect_the_configuration() -> None:
    assert cli._first_start_arguments([], _parsed(), first_start=False) == []


def test_an_explicit_wish_is_never_overruled() -> None:
    for switches in (("--no-window",), ("--show-window",), ("--check",)):
        arguments = list(switches)
        assert cli._first_start_arguments(arguments, _parsed(*switches), first_start=True) == arguments


# ----------------------------------------------------------------------
# Logging without a console
# ----------------------------------------------------------------------
def test_no_console_handler_without_a_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A StreamHandler on None would fail on every record under pythonw.exe."""
    import logging

    monkeypatch.setattr(logging_setup.sys, "stderr", None)
    logger = logging_setup.configure(log_to_file=False)
    try:
        assert not [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        logger.info("this must not raise")
    finally:
        logging_setup.configure(log_to_file=False)


def test_a_stream_still_gets_a_handler() -> None:
    import logging

    logger = logging_setup.configure(log_to_file=False)
    try:
        assert [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
    finally:
        logging_setup.configure(log_to_file=False)


# ----------------------------------------------------------------------
# The window itself (needs a display)
# ----------------------------------------------------------------------
@pytest.mark.gui
def test_the_splash_shows_the_logo_and_the_current_step() -> None:
    splash = setup_ui.SetupSplash.try_open(
        title="Setup", heading="Setting up", wait_hint="Please wait"
    )
    assert splash is not None
    try:
        assert splash._logo is not None, "the window must be recognisable as the program"
        splash.set_status("Checking FFmpeg...")
        assert splash._status.cget("text") == "Checking FFmpeg..."
        # The close button must not let the user abort a running installation.
        assert splash._root.protocol("WM_DELETE_WINDOW")
    finally:
        splash.close()


@pytest.mark.gui
def test_closing_twice_is_harmless() -> None:
    splash = setup_ui.SetupSplash.try_open(title="Setup", heading="H", wait_hint="W")
    assert splash is not None
    splash.close()
    splash.close()
    splash.set_status("gone")


@pytest.mark.gui
def test_the_progress_bar_uses_the_accent_colour() -> None:
    from tkinter import ttk

    from clipster.theme import PALETTE

    splash = setup_ui.SetupSplash.try_open(title="Setup", heading="H", wait_hint="W")
    assert splash is not None
    try:
        style = ttk.Style(splash._root)
        assert style.lookup("Setup.Horizontal.TProgressbar", "background") == PALETTE.accent
    finally:
        splash.close()


@pytest.mark.gui
def test_the_close_button_answers_instead_of_aborting() -> None:
    """A dead close button reads as a hung program - and it must not abort."""
    splash = setup_ui.SetupSplash.try_open(
        title="Setup", heading="H", wait_hint="W", busy_hint="still running"
    )
    assert splash is not None
    try:
        splash.set_status("Checking FFmpeg...")
        splash._refuse_close()
        assert splash._status.cget("text") == "still running"
        assert splash._root.winfo_exists(), "the installation must keep running"
    finally:
        splash.close()


# ----------------------------------------------------------------------
# The console echo survives a legacy code page
# ----------------------------------------------------------------------
def test_the_summary_stays_ascii() -> None:
    """A Windows console code page may not be able to encode a bullet."""
    text = cli.summarize_failures(_failed_report(2))
    text.encode("ascii")  # raises if a typographic character slipped in


def test_an_unencodable_status_is_still_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    written = []

    class LegacyConsole:
        def write(self, text: str) -> int:
            text.encode("cp437")  # raises for typographic characters
            written.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(cli.sys, "stderr", LegacyConsole())
    cli._print_progress("Wird gestartet… — jetzt")
    assert written, "the step must be reported even when characters are lost"
    assert "Wird gestartet" in "".join(written)
