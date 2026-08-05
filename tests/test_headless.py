"""Running without a graphical interface.

Deliberately no ``gui`` marker: these tests must pass on a machine that has no
display at all, because that is the whole point of the mode - a server, a
Raspberry Pi, or Android through Termux.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from clipster.headless import HeadlessGui, HeadlessNav, HeadlessRoot


# ----------------------------------------------------------------------
# The event loop
# ----------------------------------------------------------------------
@pytest.fixture()
def loop():
    """Return a running headless root, stopped again afterwards."""
    root = HeadlessRoot()
    thread = threading.Thread(target=root.mainloop, daemon=True)
    thread.start()
    for _ in range(100):
        if root.loop_thread is not None:
            break
        time.sleep(0.01)
    try:
        yield root
    finally:
        root.quit()
        thread.join(timeout=3)


def _wait_for(condition, timeout: float = 3.0) -> bool:
    """Wait until ``condition`` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_scheduled_callback_runs(loop) -> None:
    seen: list = []
    loop.after(10, seen.append, "fired")
    assert _wait_for(lambda: seen == ["fired"]), seen


def test_callbacks_run_in_order(loop) -> None:
    seen: list = []
    loop.after(60, seen.append, "late")
    loop.after(10, seen.append, "early")
    assert _wait_for(lambda: len(seen) == 2), seen
    assert seen == ["early", "late"]


def test_a_cancelled_callback_never_runs(loop) -> None:
    seen: list = []
    handle = loop.after(50, seen.append, "no")
    loop.after_cancel(handle)
    time.sleep(0.3)
    assert seen == []


def test_cancelling_nonsense_is_harmless(loop) -> None:
    loop.after_cancel("")
    loop.after_cancel("not-a-handle")
    loop.after_cancel("after#nope")


def test_a_callback_without_a_function_only_returns_a_handle(loop) -> None:
    """Tk's after() allows that, and the code may rely on it."""
    assert loop.after(10) .startswith("after#")


def test_one_broken_callback_does_not_stop_the_loop(loop) -> None:
    seen: list = []

    def explode() -> None:
        raise RuntimeError("boom")

    loop.after(10, explode)
    loop.after(40, seen.append, "still running")
    assert _wait_for(lambda: seen == ["still running"]), seen


def test_callbacks_all_run_on_the_loop_thread(loop) -> None:
    """The application relies on that, exactly as it does with Tk."""
    threads: list = []
    for _ in range(5):
        loop.after(10, lambda: threads.append(threading.get_ident()))
    assert _wait_for(lambda: len(threads) == 5), threads
    assert set(threads) == {loop.loop_thread}


def test_quitting_ends_the_loop() -> None:
    root = HeadlessRoot()
    thread = threading.Thread(target=root.mainloop, daemon=True)
    thread.start()
    assert _wait_for(lambda: root.loop_thread is not None)
    root.quit()
    thread.join(timeout=3)
    assert not thread.is_alive()


# ----------------------------------------------------------------------
# The navigation window that is not drawn
# ----------------------------------------------------------------------
def test_the_nav_records_what_it_would_have_shown() -> None:
    nav = HeadlessNav()
    nav.begin("Link received")
    nav.set_headline("A Song")
    nav.show_progress("mp3", 200)
    nav.set_status("Converting", "2.1 MB/s")
    nav.set_percent(63.0)
    assert nav.headline == "A Song"
    assert nav.status == "Converting"
    assert nav.detail == "2.1 MB/s"
    assert nav.percent == 63.0
    nav.finish("Done", "ok")
    assert nav.phase == "ok"


def test_beginning_a_run_gives_it_a_fresh_cancel_event() -> None:
    """A cancel from the previous download must not abort the next one."""
    nav = HeadlessNav()
    first = nav.cancel_event
    first.set()
    nav.begin("Link received")
    assert nav.cancel_event is not first
    assert not nav.cancel_event.is_set()


def test_the_format_question_is_declined_at_once() -> None:
    """Nobody is there to answer, and the caller must not wait for one."""
    from clipster.bridge import Prompt

    prompt = Prompt()
    HeadlessNav().ask(prompt, "A Song", 200, [], "mp3", True, "en")
    assert prompt.answered is True
    assert prompt.wait() is None


def test_an_existing_file_is_noted() -> None:
    nav = HeadlessNav()
    nav.already_downloaded("A Song", Path("/tmp/a.mp3"), "already there", lambda: None)
    assert nav.result_path() == Path("/tmp/a.mp3")


def test_hiding_a_window_that_does_not_exist_is_harmless() -> None:
    HeadlessNav().hide()


# ----------------------------------------------------------------------
# The interface that shows nothing
# ----------------------------------------------------------------------
@pytest.fixture()
def gui(config, messages, tmp_path: Path):
    """Return a headless interface."""
    return HeadlessGui(messages, config, tmp_path)


def test_there_is_no_view_window(gui) -> None:
    """The application already handles that, in every place that asks."""
    assert gui.view is None
    assert gui.view_visible() is False
    gui.show_view()
    gui.hide_view()


def test_errors_and_notices_go_to_the_log(gui, caplog) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="clipster"):
        gui.show_error("Title", "Something went wrong")
        gui.toast("A notice")
        gui.show_update_state("An update is available", True)
    text = caplog.text
    assert "Something went wrong" in text
    assert "A notice" in text


def test_a_question_nobody_can_answer_is_a_no(gui) -> None:
    assert gui.ask_yes_no("Title", "Really?") is False


def test_the_terms_are_not_confirmed_on_the_users_behalf(gui) -> None:
    """A legal confirmation needs an explicit switch, not a convenience default."""
    assert gui.ask_terms_acceptance(title_key="terms_app_title") is False


def test_the_terms_switch_confirms_them(config, messages, tmp_path: Path) -> None:
    gui = HeadlessGui(messages, config, tmp_path, accept_terms=True)
    assert gui.ask_terms_acceptance(title_key="terms_app_title") is True


def test_the_history_is_kept_for_inspection(gui) -> None:
    from clipster.history import HistoryEntry

    gui.render_history([HistoryEntry(name="a.mp3"), HistoryEntry(name="b.mp3")])
    assert gui.status()["entries"] == 2


def test_destroying_stops_the_loop(gui) -> None:
    thread = threading.Thread(target=gui.root.mainloop, daemon=True)
    thread.start()
    assert _wait_for(lambda: gui.root.loop_thread is not None)
    gui.destroy()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_every_call_the_application_makes_is_answered(gui) -> None:
    """A missing method would only show up as a crash at runtime."""
    for name in ("build_windows", "destroy", "render_history", "show_error", "toast",
                 "show_update_state", "show_view", "hide_view", "view_visible",
                 "ask_yes_no", "ask_terms_acceptance", "show_terms_document"):
        assert callable(getattr(gui, name)), name
    for name in ("root", "nav", "view"):
        assert hasattr(gui, name), name
    for name in ("begin", "ask", "set_headline", "show_progress", "set_status",
                 "set_percent", "finish", "already_downloaded", "result_path", "hide",
                 "cancel_event"):
        assert hasattr(gui.nav, name), name


# ----------------------------------------------------------------------
# The switches that reach it
# ----------------------------------------------------------------------
def test_the_command_line_offers_the_mode() -> None:
    from clipster import cli

    arguments = cli.build_parser().parse_args(["--headless", "--accept-terms"])
    assert arguments.headless is True
    assert arguments.accept_terms is True


def test_a_first_headless_start_does_not_ask_for_a_window() -> None:
    from clipster import cli

    arguments = cli.build_parser().parse_args(["--headless"])
    assert cli._first_start_arguments(["--headless"], arguments, first_start=True) == ["--headless"]


def test_termux_is_recognised_by_its_own_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipster import paths

    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert paths.is_termux() is True
    monkeypatch.setenv("PREFIX", "/usr")
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    assert paths.is_termux() is True


def test_the_installer_knows_termux_packages() -> None:
    from clipster import installer

    pkg = [item for item in installer._PACKAGE_MANAGERS if item.name == "pkg"]
    assert pkg, "Termux has no entry"
    packages = pkg[0].packages
    assert packages["ffmpeg"] == "ffmpeg"
    assert packages["tk"] == "python-tkinter"
    # There is no X11 in Termux; the clipboard comes from termux-api.
    assert packages["xclip"] == "termux-api"


def test_termux_needs_no_sudo() -> None:
    """There is no root on Android, and pkg installs into the user's prefix."""
    from clipster import installer

    assert installer._privileged(["pkg", "install", "-y", "ffmpeg"])[0] == "pkg"


def test_freebsds_pkg_is_not_mistaken_for_termux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both are called pkg and they are not remotely the same thing."""
    from clipster import installer, paths

    monkeypatch.setattr(installer, "_manager_detected", False)
    monkeypatch.setattr(installer, "_detected_manager", None)
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/sbin/pkg" if name == "pkg" else None)
    assert installer.detect_package_manager() is None
