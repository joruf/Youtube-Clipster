"""Handing the program to a phone over USB.

No phone and no adb are needed: a fake adb on PATH produces the same output the
real one does, which is the only way to exercise every state a device can be in.
"""

from __future__ import annotations

import os
import stat
import tarfile
from pathlib import Path

import pytest

from clipster import android


# ----------------------------------------------------------------------
# Reading what adb says
# ----------------------------------------------------------------------
def test_a_ready_phone_is_recognised() -> None:
    found = android.parse_devices(
        "List of devices attached\n"
        "R58M12ABCDE            device usb:1-3 product:a52q model:SM_A525F device:a52q\n"
    )
    assert len(found) == 1
    assert found[0].serial == "R58M12ABCDE"
    assert found[0].ready is True
    assert found[0].model == "SM A525F"
    assert "SM A525F" in found[0].describe()


def test_a_phone_waiting_for_the_prompt_is_recognised() -> None:
    """This is the tap on the phone that people miss."""
    found = android.parse_devices("List of devices attached\nR58M12ABCDE  unauthorized usb:1-3\n")
    assert found[0].needs_confirmation is True
    assert found[0].ready is False


def test_an_offline_phone_is_recognised() -> None:
    found = android.parse_devices("List of devices attached\nR58M12ABCDE offline\n")
    assert found[0].state == android.STATE_OFFLINE
    assert found[0].ready is False


def test_noise_and_emptiness_are_ignored() -> None:
    assert android.parse_devices("") == []
    assert android.parse_devices("List of devices attached\n\n") == []
    assert android.parse_devices("* daemon started successfully\n") == []
    assert android.parse_devices("garbage-without-a-state\n") == []


def test_several_phones_are_all_listed() -> None:
    found = android.parse_devices(
        "List of devices attached\n"
        "AAA device model:One\n"
        "BBB unauthorized\n"
    )
    assert [device.serial for device in found] == ["AAA", "BBB"]


# ----------------------------------------------------------------------
# What the situation means
# ----------------------------------------------------------------------
@pytest.fixture()
def fake_adb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put a fake adb on PATH whose output the test decides."""
    directory = tmp_path / "bin"
    directory.mkdir()
    script = directory / "adb"

    def program(devices_output: str = "", push_lines: str = "", exit_code: int = 0) -> Path:
        script.write_text(
            "#!/usr/bin/env bash\n"
            "case \"$*\" in\n"
            "  *devices*) cat <<'OUT'\n{0}\nOUT\n    ;;\n"
            "  *run-as*) exit 1 ;;\n"
            "  *push*) cat <<'OUT'\n{1}\nOUT\n    exit {2} ;;\n"
            "  *monkey*) echo 'Events injected: 1'; exit {2} ;;\n"
            "esac\n".format(devices_output, push_lines, exit_code),
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
        return script

    return program


def test_without_adb_that_is_what_it_says(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android.shutil, "which", lambda name: None)
    monkeypatch.setattr(android.paths, "IS_WINDOWS", False)
    assert android.adb_path() is None
    assert android.summarise([])[0] == "no_adb"
    assert android.devices() == []


def test_nothing_plugged_in(fake_adb) -> None:
    fake_adb("List of devices attached")
    assert android.devices() == []
    assert android.summarise([])[0] == "none"


def test_a_ready_phone_is_the_one_returned(fake_adb) -> None:
    fake_adb("List of devices attached\nAAA unauthorized\nBBB device model:Pixel_7")
    found = android.devices()
    state, device = android.summarise(found)
    assert state == "ready"
    assert device is not None and device.serial == "BBB"


def test_an_unauthorised_phone_is_reported_as_such(fake_adb) -> None:
    fake_adb("List of devices attached\nAAA unauthorized")
    state, device = android.summarise(android.devices())
    assert state == "unauthorised"
    assert device is None


def test_an_offline_phone_is_reported_as_such(fake_adb) -> None:
    fake_adb("List of devices attached\nAAA offline")
    assert android.summarise(android.devices())[0] == "offline"


# ----------------------------------------------------------------------
# The bundle
# ----------------------------------------------------------------------
@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    """Return a miniature checkout with the things that must not travel."""
    root = tmp_path / "youtube-clipster"
    (root / "clipster").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "clipster" / "__pycache__").mkdir()
    (root / "run.py").write_text("entry\n", encoding="utf-8")
    (root / "clipster" / "app.py").write_text("code\n", encoding="utf-8")
    (root / "clipster" / "__pycache__" / "app.pyc").write_bytes(b"\0")
    (root / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (root / "venv" / "bin" / "python").write_text("binary\n", encoding="utf-8")
    (root / "config.json").write_text('{"remote_token": "secret"}\n', encoding="utf-8")
    (root / "history.json").write_text("[]\n", encoding="utf-8")
    return root


def test_the_bundle_carries_the_program(checkout: Path, tmp_path: Path) -> None:
    archive = android.make_bundle(checkout, tmp_path / "bundle.tar.gz")
    with tarfile.open(archive) as opened:
        names = opened.getnames()
    assert "youtube-clipster/run.py" in names
    assert "youtube-clipster/clipster/app.py" in names


def test_the_token_never_travels(checkout: Path, tmp_path: Path) -> None:
    """The configuration holds the remote control token of *this* machine."""
    archive = android.make_bundle(checkout, tmp_path / "bundle.tar.gz")
    with tarfile.open(archive) as opened:
        names = opened.getnames()
    assert not [name for name in names if name.endswith("config.json")]
    assert not [name for name in names if name.endswith("history.json")]
    assert b"secret" not in archive.read_bytes()


@pytest.mark.parametrize("unwanted", [".git", "venv", "__pycache__"])
def test_build_leftovers_stay_behind(checkout: Path, tmp_path: Path, unwanted: str) -> None:
    archive = android.make_bundle(checkout, tmp_path / "bundle.tar.gz")
    with tarfile.open(archive) as opened:
        names = opened.getnames()
    assert not [name for name in names if unwanted in name.split("/")], unwanted


def test_the_progress_counts_every_file(checkout: Path, tmp_path: Path) -> None:
    seen: list = []
    android.make_bundle(checkout, tmp_path / "bundle.tar.gz",
                        on_progress=lambda done, total: seen.append((done, total)))
    assert seen, "no progress at all"
    assert seen[-1][0] == seen[-1][1], seen[-1]
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)


# ----------------------------------------------------------------------
# The transfer
# ----------------------------------------------------------------------
def test_the_transfer_reports_its_progress(fake_adb, tmp_path: Path) -> None:
    fake_adb(push_lines="[  0%] /sdcard/Download/x\n[ 45%] /sdcard/Download/x\n"
                        "[100%] /sdcard/Download/x\n1 file pushed.")
    bundle = tmp_path / android.BUNDLE_NAME
    bundle.write_bytes(b"data")
    percentages: list = []
    ok, message = android.push(bundle, on_progress=percentages.append)
    assert ok, message
    assert 45 in percentages
    assert percentages[-1] == 100
    assert message.endswith(android.BUNDLE_NAME)


def test_a_failed_transfer_says_why(fake_adb, tmp_path: Path) -> None:
    fake_adb(push_lines="adb: error: failed to copy: No space left on device", exit_code=1)
    bundle = tmp_path / android.BUNDLE_NAME
    bundle.write_bytes(b"data")
    ok, message = android.push(bundle)
    assert not ok
    assert "No space left" in message


def test_a_missing_archive_is_refused(fake_adb, tmp_path: Path) -> None:
    fake_adb(push_lines="")
    ok, message = android.push(tmp_path / "not-there.tar.gz")
    assert not ok
    assert "missing" in message


def test_without_adb_the_transfer_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(android, "adb_path", lambda: None)
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(b"x")
    ok, message = android.push(bundle)
    assert not ok
    assert "adb" in message


def test_termux_can_be_brought_to_the_front(fake_adb) -> None:
    fake_adb()
    assert android.open_termux() is True


def test_without_adb_termux_cannot_be_started(monkeypatch) -> None:
    monkeypatch.setattr(android, "adb_path", lambda: None)
    assert android.open_termux() is False


# ----------------------------------------------------------------------
# The step that cannot be done from here
# ----------------------------------------------------------------------
def test_the_bootstrap_script_unpacks_and_installs() -> None:
    """adb may not enter Termux's storage, so Termux has to fetch it itself."""
    body = android.setup_script_body()
    assert android.REMOTE_DIR in body
    assert android.BUNDLE_NAME in body
    assert "install-android.sh" in body
    assert "--accept-terms" in body


def test_the_home_bootstrap_script_avoids_sdcard() -> None:
    body = android.setup_script_body(in_home=True)
    assert "/sdcard" not in body
    assert "termux-setup-storage" not in body
    assert "$HOME/$BUNDLE" in body
    assert "--accept-terms" in body


def test_the_launch_command_is_short_and_typeable() -> None:
    command = android.launch_command()
    assert android.SETUP_SCRIPT_NAME in command
    assert android.typeable(command)
    assert "&&" not in command
    assert ";" not in command
    assert len(command) < 80


def test_the_home_launch_command_is_short_and_typeable() -> None:
    command = android.launch_command(in_home=True)
    assert command.startswith("bash ~/")
    assert android.SETUP_SCRIPT_NAME in command
    assert android.typeable(command)
    assert len(command) < 80
    assert "/sdcard" not in command

def test_the_fallback_one_liner_still_mentions_the_archive() -> None:
    command = android.install_command("other.tar.gz", "/sdcard/Documents")
    assert "/sdcard/Documents/other.tar.gz" in command
    assert "--accept-terms" in command


def test_write_setup_script_creates_the_file(tmp_path: Path) -> None:
    target = tmp_path / android.SETUP_SCRIPT_NAME
    written = android.write_setup_script(target, accept_terms=True)
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert text.startswith("#!")
    assert "--accept-terms" in text


def test_setup_script_can_omit_accept_terms() -> None:
    assert "--accept-terms" not in android.setup_script_body(accept_terms=False)


# ----------------------------------------------------------------------
# The window in the program
# ----------------------------------------------------------------------
@pytest.fixture()
def dialog(fake_adb, messages, request):
    """Return the wizard with a fake adb behind it."""
    import tkinter as tk

    from clipster import theme
    from clipster.android_dialog import AndroidDialog

    fake_adb(getattr(request, "param", "List of devices attached\nAAA device model:Pixel_7"))
    root = tk.Tk()
    theme.apply(root)
    copied: list = []
    window = AndroidDialog(root, messages, theme.PALETTE, theme.fonts(),
                           on_copy=copied.append)
    try:
        yield window, copied, root
    finally:
        window.close()
        root.destroy()


def _settle(root, dialog_window, seconds: float = 2.0) -> None:
    """Pump Tk until the background scan has been shown."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)


def _goto_step(window, step: str) -> None:
    """Jump the wizard to *step* without running side effects (for GUI tests)."""
    from clipster.android_dialog import WIZARD_STEPS

    idx = WIZARD_STEPS.index(step)
    window._completed = set(WIZARD_STEPS[:idx])
    window._current_step = step
    window._terms_accepted = True
    window._termux_ready = True
    window._refresh_wizard()


@pytest.mark.gui
def test_wizard_shows_one_page_at_a_time(dialog, messages) -> None:
    """Future steps must not sit empty under the current page."""
    from clipster.android_dialog import WIZARD_STEPS

    window, _, root = dialog
    _settle(root, window)
    assert window._current_step == "prepare"
    assert "1" in window._progress_label.cget("text")
    for step, card in window._step_cards.items():
        manager = card.winfo_manager()
        if step == "prepare":
            assert manager == "pack"
        else:
            assert manager == ""
    assert str(window._back_button.cget("state")) == "disabled"
    window._adb_ready = True
    window._device_ready = True
    window._complete_current()
    _settle(root, window)
    assert window._current_step == "terms"
    assert window._step_cards["prepare"].winfo_manager() == ""
    assert window._step_cards["terms"].winfo_manager() == "pack"
    assert str(window._back_button.cget("state")) == "normal"
    window._go_back()
    _settle(root, window)
    assert window._current_step == "prepare"
    assert window._step_cards["prepare"].winfo_manager() == "pack"
    assert WIZARD_STEPS == ("prepare", "terms", "termux", "setup", "done")
    assert messages["android_wizard_back"]
    assert messages["android_wizard_prepare_item_usb"]


@pytest.mark.gui
def test_a_ready_phone_unlocks_the_transfer(dialog) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert "Pixel 7" in window._labels["device"].cget("text")
    _goto_step(window, "setup")
    assert str(window._start_button.cget("state")) == "normal"


@pytest.mark.gui
@pytest.mark.parametrize("dialog", ["List of devices attached\nAAA unauthorized"], indirect=True)
def test_an_unauthorised_phone_keeps_the_transfer_locked(dialog, messages) -> None:
    """The tap on the phone has to happen first, and it has to be said so."""
    window, _, root = dialog
    _settle(root, window)
    assert window._labels["device"].cget("text") == messages["android_device_unauthorised"]
    _goto_step(window, "setup")
    assert str(window._start_button.cget("state")) == "disabled"


@pytest.mark.gui
@pytest.mark.parametrize("dialog", ["List of devices attached"], indirect=True)
def test_no_phone_says_to_plug_one_in(dialog, messages) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert window._labels["device"].cget("text") == messages["android_device_none"]
    _goto_step(window, "setup")
    assert str(window._start_button.cget("state")) == "disabled"


@pytest.mark.gui
def test_the_command_can_be_copied(dialog) -> None:
    window, copied, root = dialog
    _settle(root, window, 0.5)
    window._copy_command()
    assert copied == [android.launch_command()]


@pytest.mark.gui
def test_the_window_shows_the_command_that_has_to_run_on_the_phone(dialog) -> None:
    window, _, root = dialog
    _settle(root, window, 0.5)
    shown = window._command.get("1.0", "end").strip()
    assert android.SETUP_SCRIPT_NAME in shown
    assert android.REMOTE_DIR in shown


@pytest.mark.gui
def test_workers_never_touch_tk_directly(dialog) -> None:
    """Calling after() off the Tk thread raises "main thread is not in main loop"."""
    window, _, root = dialog
    source = Path(android.__file__).with_name("android_dialog.py").read_text(encoding="utf-8")
    worker_part = source.split("def _post", 1)[0]
    # Inside the worker functions only _post may be used.
    for chunk in worker_part.split("def work() -> None:")[1:]:
        body = chunk.split("\n        threading.Thread", 1)[0]
        assert "self.window.after" not in body, body[:200]
        assert "_set(" not in body or "_post(" in body


@pytest.mark.gui
def test_closing_stops_both_timers(dialog) -> None:
    window, _, root = dialog
    _settle(root, window, 0.4)
    window.close()
    assert window._watch_job is None
    assert window._pump_job is None
    # A second close must not raise either.
    window.close()


@pytest.mark.gui
def test_a_result_arriving_after_the_close_is_dropped(dialog) -> None:
    window, _, root = dialog
    window.close()
    window._post(window._set, "device", "too late")
    assert window._results.empty(), "a queued result would run against dead widgets"


# ----------------------------------------------------------------------
# Installing adb, only after asking
# ----------------------------------------------------------------------
def test_the_plan_names_the_distribution_package(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipster import installer

    monkeypatch.setattr(android.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])           # apt-get
    kind, command = android.adb_install_plan()
    assert kind == "package"
    assert command == "apt-get install -y adb"


def test_without_a_package_manager_there_is_nothing_to_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipster import installer

    monkeypatch.setattr(android.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(installer, "detect_package_manager", lambda: None)
    assert android.adb_install_plan() == ("manual", "")


def test_on_windows_the_plan_is_winget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android.paths, "IS_WINDOWS", True)
    monkeypatch.setattr(android.shutil, "which",
                        lambda name: "C:\\winget.exe" if name == "winget" else None)
    kind, command = android.adb_install_plan()
    assert kind == "winget"
    assert android.WINGET_PACKAGE in command


def test_without_winget_windows_has_no_automatic_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android.paths, "IS_WINDOWS", True)
    monkeypatch.setattr(android.shutil, "which", lambda name: None)
    assert android.adb_install_plan() == ("manual", "")


def test_googles_terms_are_never_accepted_on_the_users_behalf(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows route pulls Google's SDK tools; that licence needs a real yes."""
    from clipster import installer

    monkeypatch.setattr(android.paths, "IS_WINDOWS", True)
    monkeypatch.setattr(android.shutil, "which",
                        lambda name: "C:\\winget.exe" if name == "winget" else None)
    monkeypatch.setattr(installer, "run_command",
                        lambda *a, **k: pytest.fail("winget ran without the licence accepted"))

    ok, message = android.install_adb(accept_licence=False)

    assert not ok
    assert "terms" in message.lower()


def test_with_the_terms_accepted_winget_is_told_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipster import installer

    seen: list = []
    monkeypatch.setattr(android.paths, "IS_WINDOWS", True)
    monkeypatch.setattr(android.shutil, "which",
                        lambda name: "C:\\winget.exe" if name == "winget" else None)
    monkeypatch.setattr(android, "adb_path", lambda: "C:\\adb.exe")

    def fake_run(command, **kwargs):
        seen.append(list(command))
        return installer.CommandResult(returncode=0, output="Successfully installed")

    monkeypatch.setattr(installer, "run_command", fake_run)

    ok, message = android.install_adb(accept_licence=True)

    assert ok and message == "C:\\adb.exe"
    argv, = seen
    assert "--accept-package-agreements" in argv
    assert android.WINGET_PACKAGE in argv
    # Nothing may wait for a keypress in a window with no console.
    assert "--disable-interactivity" in argv


def test_the_terms_can_be_read_before_agreeing() -> None:
    assert android.SDK_TERMS_URL.startswith("https://")


def test_on_linux_the_distribution_package_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipster import installer

    calls: list = []
    monkeypatch.setattr(android.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])
    monkeypatch.setattr(android, "adb_path", lambda: "/usr/bin/adb")

    def fake_install(keys, **kwargs):
        calls.append((list(keys), kwargs))
        return installer.CommandResult(returncode=0, output="")

    monkeypatch.setattr(installer, "install_system_packages", fake_install)

    ok, message = android.install_adb()

    assert ok and message == "/usr/bin/adb"
    (keys, kwargs), = calls
    assert keys == ["adb"]
    # The window has no terminal for a sudo prompt, and it already asked.
    assert kwargs["graphical"] is True
    assert kwargs["confirm"](["adb"], "irrelevant") is True


def test_a_failed_install_says_what_went_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipster import installer

    monkeypatch.setattr(android.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])
    monkeypatch.setattr(installer, "install_system_packages",
                        lambda keys, **kw: installer.CommandResult(
                            returncode=100, output="E: Unable to locate package adb"))

    ok, message = android.install_adb()

    assert not ok
    assert "Unable to locate package" in message


def test_freshly_installed_windows_tools_are_found(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    """winget puts them on the PATH of new processes, not of this one."""
    installed = (tmp_path / "Microsoft" / "WinGet" / "Packages"
                 / "Google.PlatformTools_Microsoft.Winget.Source" / "platform-tools")
    installed.mkdir(parents=True)
    (installed / "adb.exe").write_text("binary")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    found = android._winget_locations()

    assert [path.name for path in found] == ["adb.exe"]


def test_nothing_is_looked_for_without_a_windows_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert android._winget_locations() == []


@pytest.fixture()
def dialog_without_adb(messages, monkeypatch: pytest.MonkeyPatch):
    """Return the wizard on a system where adb is missing but installable."""
    import tkinter as tk

    from clipster import theme
    from clipster.android_dialog import AndroidDialog

    monkeypatch.setattr(android, "adb_path", lambda: None)
    monkeypatch.setattr(android, "adb_install_plan",
                        lambda: ("package", "apt-get install -y adb"))
    root = tk.Tk()
    theme.apply(root)
    window = AndroidDialog(root, messages, theme.PALETTE, theme.fonts(),
                           on_copy=lambda text: None)
    try:
        yield window, root
    finally:
        window.close()
        root.destroy()


@pytest.mark.gui
def test_the_install_button_appears_when_adb_is_missing(dialog_without_adb, messages) -> None:
    window, root = dialog_without_adb
    _settle(root, window)
    assert window._labels["adb"].cget("text") == messages["android_adb_missing"]
    assert window._adb_row.winfo_manager() == "pack", "the offer to install it is not shown"


@pytest.mark.gui
def test_the_install_button_stays_away_when_adb_is_there(dialog) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert window._adb_row.winfo_manager() == "", "offered to install what is already there"


@pytest.mark.gui
def test_nothing_is_offered_when_nothing_could_install_it(messages,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    from clipster import theme
    from clipster.android_dialog import AndroidDialog

    monkeypatch.setattr(android, "adb_path", lambda: None)
    monkeypatch.setattr(android, "adb_install_plan", lambda: ("manual", ""))
    root = tk.Tk()
    theme.apply(root)
    window = AndroidDialog(root, messages, theme.PALETTE, theme.fonts(), on_copy=lambda t: None)
    try:
        _settle(root, window)
        assert window._adb_row.winfo_manager() == "", "offered a button that cannot work"
    finally:
        window.close()
        root.destroy()


@pytest.mark.gui
def test_a_no_installs_nothing(dialog_without_adb, messages,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter.messagebox

    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(android, "install_adb",
                        lambda **kwargs: pytest.fail("installed despite a no"))

    window._install_adb()
    _settle(root, window, 0.5)

    assert window._adb_hint.cget("text") == messages["android_adb_declined"]


@pytest.mark.gui
def test_the_question_shows_what_will_be_run(dialog_without_adb,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Nobody should have to guess what a button does to their system."""
    import tkinter.messagebox

    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    asked: list = []

    def fake_ask(title, question, **kwargs):
        asked.append(question)
        return False

    monkeypatch.setattr(tkinter.messagebox, "askyesno", fake_ask)
    window._install_adb()

    question, = asked
    assert "apt-get install -y adb" in question


@pytest.mark.gui
def test_a_yes_installs_and_reports_the_result(dialog_without_adb, messages,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """After a real install adb is there - and the rescan has to see that."""
    import tkinter.messagebox

    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    present: list = []
    monkeypatch.setattr(android, "adb_path", lambda: "/usr/bin/adb" if present else None)
    monkeypatch.setattr(android, "devices", lambda: [])
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(android, "install_adb",
                        lambda **kwargs: (present.append(True), (True, "/usr/bin/adb"))[1])

    window._install_adb()
    _settle(root, window, 1.5)

    assert "/usr/bin/adb" in window._labels["adb"].cget("text")
    assert window._adb_row.winfo_manager() == "", "still offering to install it"


@pytest.mark.gui
def test_a_failed_install_keeps_the_button_and_says_why(dialog_without_adb,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason has to survive the rescan that follows, which found no adb."""
    import tkinter.messagebox

    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(android, "install_adb", lambda **kwargs: (False, "E: no such package"))

    window._install_adb()
    # Long enough for the periodic watch to fire at least once on top of it.
    _settle(root, window, 2.5)

    assert "no such package" in window._labels["adb"].cget("text")
    assert window._adb_row.winfo_manager() == "pack", "no way left to try again"


@pytest.mark.gui
def test_the_licence_is_named_before_it_is_accepted(dialog_without_adb,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows a yes accepts Google's terms, so the question has to say so."""
    import tkinter.messagebox

    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    monkeypatch.setattr(android, "adb_install_plan",
                        lambda: ("winget", "winget install --exact --id Google.PlatformTools"))
    asked: list = []
    accepted: list = []

    def fake_ask(title, question, **kwargs):
        asked.append(question)
        return True

    monkeypatch.setattr(tkinter.messagebox, "askyesno", fake_ask)
    monkeypatch.setattr(android, "install_adb",
                        lambda **kwargs: accepted.append(kwargs["accept_licence"]) or (True, "adb"))

    window._install_adb()
    _settle(root, window, 1.0)

    question, = asked
    assert android.SDK_TERMS_URL in question, "the terms were not shown"
    assert accepted == [True]


@pytest.mark.gui
def test_a_distribution_package_needs_no_licence_flag(dialog_without_adb,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The distribution already redistributes adb; nothing extra is agreed to."""
    import tkinter.messagebox

    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    accepted: list = []
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(android, "install_adb",
                        lambda **kwargs: accepted.append(kwargs["accept_licence"]) or (True, "adb"))

    window._install_adb()
    _settle(root, window, 1.0)

    assert accepted == [False]


@pytest.mark.gui
def test_the_package_managers_output_is_shown_while_it_runs(dialog_without_adb,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    window, root = dialog_without_adb
    _settle(root, window, 0.5)
    window._adb_output("Setting up android-libbase (34.0.0) ...")
    assert "android-libbase" in window._adb_hint.cget("text")


@pytest.mark.gui
def test_a_long_output_line_stays_one_row(dialog_without_adb) -> None:
    window, root = dialog_without_adb
    window._adb_output("x" * 400)
    assert len(window._adb_hint.cget("text")) <= 90


# ----------------------------------------------------------------------
# Typing the last command into Termux, instead of the user doing it
# ----------------------------------------------------------------------
@pytest.fixture()
def recording_adb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put an adb on PATH that records every call and fakes the foreground app."""
    directory = tmp_path / "bin"
    directory.mkdir()
    log_file = tmp_path / "calls.log"
    script = directory / "adb"

    def program(focus: str = "com.termux/com.termux.app.TermuxActivity",
                exit_code: int = 0) -> Path:
        script.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> '{log}'\n"
            "case \"$*\" in\n"
            "  *dumpsys*) echo '  mCurrentFocus=Window{{aaa u0 {focus}}}';;\n"
            "  *devices*) echo 'List of devices attached'; echo 'AAA device model:Pixel_7';;\n"
            "  *) exit {code} ;;\n"
            "esac\n".format(log=log_file, focus=focus, code=exit_code),
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
        return log_file

    return program


def test_the_focused_app_is_recognised(recording_adb) -> None:
    recording_adb()
    assert android.foreground_app() == "com.termux"


def test_another_app_in_front_is_reported_as_itself(recording_adb) -> None:
    recording_adb(focus="com.whatsapp/com.whatsapp.HomeActivity")
    assert android.foreground_app() == "com.whatsapp"


def test_an_unreadable_answer_means_unknown(recording_adb) -> None:
    recording_adb(focus="")
    assert android.foreground_app() == ""


def test_nothing_is_typed_into_another_app(recording_adb) -> None:
    """The whole point of the check: keystrokes go wherever the focus is."""
    log_file = recording_adb(focus="com.whatsapp/com.whatsapp.HomeActivity")

    ok, reason = android.run_on_phone("echo hello", focus_pause=0, open_timeout=0.4)

    assert not ok
    assert reason == "termux_not_open"
    calls = log_file.read_text(encoding="utf-8")
    assert "input text" not in calls, "typed a command into a foreign app"


def test_with_termux_in_front_the_command_is_typed_and_entered(recording_adb) -> None:
    log_file = recording_adb()

    ok, reason = android.run_on_phone(android.launch_command(), focus_pause=0)

    assert ok and reason == ""
    calls = log_file.read_text(encoding="utf-8").replace("%s", " ")
    assert "input text" in calls
    assert android.SETUP_SCRIPT_NAME in calls
    assert "keyevent 66" in calls, "the command was typed but never run"


def test_the_command_is_reported_step_by_step(recording_adb) -> None:
    recording_adb()
    seen: list = []
    android.run_on_phone("echo hello", on_status=seen.append, focus_pause=0)
    assert seen == ["opening", "typing"]


def test_spaces_survive_the_way_to_the_phone(recording_adb) -> None:
    """input text reads %s as a space; a raw space would split the command."""
    log_file = recording_adb()
    android.type_text("pkg install -y tar")
    calls = log_file.read_text(encoding="utf-8")
    assert "pkg%sinstall%s-y%star" in calls


def test_the_real_launch_command_can_be_typed() -> None:
    """If this ever fails the command grew a character that cannot be sent."""
    assert android.typeable(android.launch_command())


@pytest.mark.parametrize("text", ["it's", "100%", "two\nlines", ""])
def test_what_cannot_be_typed_safely_is_refused(text: str) -> None:
    """A half-typed shell command is worse than none at all."""
    assert not android.typeable(text)


def test_an_untypeable_command_never_reaches_the_phone(recording_adb) -> None:
    log_file = recording_adb()
    ok, reason = android.run_on_phone("echo 'quoted'")
    assert not ok and reason == "untypeable"
    assert not log_file.exists() or "input text" not in log_file.read_text(encoding="utf-8")


def test_without_adb_nothing_can_be_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android, "adb_path", lambda: None)
    assert android.type_text("echo hello") is False
    assert android.press_enter() is False
    assert android.foreground_app() == ""


def test_waiting_gives_up_rather_than_hanging(recording_adb) -> None:
    recording_adb(focus="com.android.launcher/com.android.launcher.Launcher")
    assert android.wait_for_termux(timeout=0.3, poll=0.1) is False


def test_play_store_termux_is_recognised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android, "termux_version_name", lambda serial="": "googleplay.2026.06.21")
    assert android.termux_is_play_store() is True


def test_github_termux_is_not_flagged_as_play_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android, "termux_version_name", lambda serial="": "0.118.3")
    assert android.termux_is_play_store() is False


def test_verification_failure_is_recognised() -> None:
    assert android.is_verification_failure(
        "Failure [INSTALL_FAILED_VERIFICATION_FAILURE: Install not allowed]"
    )
    assert android.is_verification_failure("INSTALL_CANCELED_BY_USER")
    assert not android.is_verification_failure("Success")


def test_transfer_pushes_archive_and_script(fake_adb, tmp_path: Path) -> None:
    fake_adb(push_lines="[ 100%] /sdcard/Download/x")
    bundle = tmp_path / android.BUNDLE_NAME
    script = tmp_path / android.SETUP_SCRIPT_NAME
    bundle.write_bytes(b"archive")
    android.write_setup_script(script)
    ok, message, in_home = android.transfer(bundle, script)
    assert ok
    assert android.SETUP_SCRIPT_NAME in message
    assert in_home is False


def test_transfer_into_termux_home_when_run_as_works(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "bin"
    directory.mkdir()
    script = directory / "adb"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *run-as*cp*) exit 0 ;;\n"
        "  *run-as*) echo ok; exit 0 ;;\n"
        "  *push*) echo '[100%] staged'; exit 0 ;;\n"
        "  *rm*) exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
    bundle = tmp_path / android.BUNDLE_NAME
    setup = tmp_path / android.SETUP_SCRIPT_NAME
    bundle.write_bytes(b"archive")
    android.write_setup_script(setup, in_home=True)
    assert android.termux_run_as_available()
    ok, message, in_home = android.transfer(bundle, setup)
    assert ok and in_home
    assert android.TERMUX_HOME in message
    assert android.SETUP_SCRIPT_NAME in message


def test_install_apk_reports_success(fake_adb, tmp_path: Path) -> None:
    """Reuse the fake adb; extend it to accept install."""
    fake_adb()
    directory = Path(android.adb_path()).parent
    script = directory / "adb"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *install*) echo Success; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    ok, message = android.install_apk(apk)
    assert ok
    assert "Success" in message


@pytest.mark.gui
def test_the_run_button_waits_for_a_ready_phone(dialog) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert str(window._run_button.cget("state")) == "normal"


@pytest.mark.gui
@pytest.mark.parametrize("dialog", ["List of devices attached"], indirect=True)
def test_without_a_phone_nothing_can_be_typed(dialog) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert str(window._run_button.cget("state")) == "disabled"


@pytest.mark.gui
def test_the_window_reports_a_typed_command(dialog, messages,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter.messagebox

    window, _, root = dialog
    _settle(root, window)
    window._terms_accepted = True
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(android, "run_on_phone", lambda *a, **k: (True, ""))
    monkeypatch.setattr(android, "write_setup_script", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(android, "push", lambda *a, **k: (True, "ok"))

    window._run_on_phone()
    _settle(root, window, 1.0)

    assert window._labels["finish"].cget("text") == messages["android_run_started"]


@pytest.mark.gui
def test_a_refusal_to_type_names_the_reason(dialog, messages,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """"Termux did not come up" and "adb refused" need different answers."""
    import tkinter.messagebox

    window, _, root = dialog
    _settle(root, window)
    window._terms_accepted = True
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(android, "run_on_phone", lambda *a, **k: (False, "termux_not_open"))
    monkeypatch.setattr(android, "write_setup_script", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(android, "push", lambda *a, **k: (True, "ok"))

    window._run_on_phone()
    _settle(root, window, 1.0)

    assert window._labels["finish"].cget("text") == messages["android_run_failed_termux_not_open"]


@pytest.mark.gui
def test_an_unknown_reason_still_says_something_useful(dialog, messages,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter.messagebox

    window, _, root = dialog
    _settle(root, window)
    window._terms_accepted = True
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(android, "run_on_phone", lambda *a, **k: (False, "something-new"))
    monkeypatch.setattr(android, "write_setup_script", lambda *a, **k: Path("/tmp/x"))
    monkeypatch.setattr(android, "push", lambda *a, **k: (True, "ok"))

    window._run_on_phone()
    _settle(root, window, 1.0)

    assert window._labels["finish"].cget("text") == messages["android_run_failed"]


def test_every_reason_the_typing_can_fail_with_has_a_message() -> None:
    """A new reason would otherwise silently fall back to the vague message."""
    import re

    from clipster import android_dialog

    source = Path(android.__file__).read_text(encoding="utf-8")
    body = source.split("def run_on_phone", 1)[1].split("\ndef ", 1)[0]
    reasons = set(re.findall(r'return False, "([a-z_]+)"', body))

    assert reasons, "no failure reasons found - did run_on_phone change shape?"
    assert reasons <= set(android_dialog.RUN_FAILURES), \
        "no message for: {0}".format(sorted(reasons - set(android_dialog.RUN_FAILURES)))


def test_every_status_the_typing_reports_has_a_message() -> None:
    import re

    from clipster import android_dialog

    source = Path(android.__file__).read_text(encoding="utf-8")
    body = source.split("def run_on_phone", 1)[1].split("\ndef ", 1)[0]
    statuses = set(re.findall(r'on_status\("([a-z_]+)"\)', body))

    assert statuses == set(android_dialog.RUN_STATUS), \
        "status keys and messages drifted: {0}".format(
            statuses ^ set(android_dialog.RUN_STATUS))


# ----------------------------------------------------------------------
# Clipster launcher APK
# ----------------------------------------------------------------------
def test_launcher_apk_path_is_under_tools_android() -> None:
    path = android.launcher_apk_path()
    assert path.name == "clipster-launcher.apk"
    assert path.parent.name == "android"
    assert "tools" in path.parts


def test_install_clipster_launcher_fails_when_apk_missing(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android, "launcher_apk_path", lambda: tmp_path / "missing.apk")
    ok, message = android.install_clipster_launcher()
    assert ok is False
    assert "missing" in message.lower()


def test_termux_phone_url_parses_config(fake_adb) -> None:
    config = (
        '{"remote_bind": "127.0.0.1", "remote_port": 8765, '
        '"remote_token": "abc123token"}'
    )
    script = fake_adb("List of devices attached\nAAA device")
    body = script.read_text(encoding="utf-8")
    script.write_text(
        body.replace(
            "  *run-as*) exit 1 ;;\n",
            "  *run-as*) cat <<'OUT'\n{0}\nOUT\n    ;;\n".format(config),
        ),
        encoding="utf-8",
    )
    url = android.termux_phone_url()
    assert url == "http://127.0.0.1:8765/?token=abc123token"


def test_termux_phone_url_empty_when_config_unreadable(fake_adb) -> None:
    fake_adb("List of devices attached\nAAA device")
    assert android.termux_phone_url() == ""

