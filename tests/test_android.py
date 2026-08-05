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
def test_the_last_step_is_one_command() -> None:
    """adb may not enter Termux's storage, so Termux has to fetch it itself."""
    command = android.install_command()
    assert android.REMOTE_DIR in command
    assert android.BUNDLE_NAME in command
    assert "install-android.sh" in command
    assert "\n" not in command, "it has to be one line to paste"


def test_the_command_follows_a_different_target() -> None:
    command = android.install_command("other.tar.gz", "/sdcard/Documents")
    assert "/sdcard/Documents/other.tar.gz" in command


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


@pytest.mark.gui
def test_a_ready_phone_unlocks_the_transfer(dialog) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert "Pixel 7" in window._labels["device"].cget("text")
    assert str(window._start_button.cget("state")) == "normal"


@pytest.mark.gui
@pytest.mark.parametrize("dialog", ["List of devices attached\nAAA unauthorized"], indirect=True)
def test_an_unauthorised_phone_keeps_the_transfer_locked(dialog, messages) -> None:
    """The tap on the phone has to happen first, and it has to be said so."""
    window, _, root = dialog
    _settle(root, window)
    assert window._labels["device"].cget("text") == messages["android_device_unauthorised"]
    assert str(window._start_button.cget("state")) == "disabled"


@pytest.mark.gui
@pytest.mark.parametrize("dialog", ["List of devices attached"], indirect=True)
def test_no_phone_says_to_plug_one_in(dialog, messages) -> None:
    window, _, root = dialog
    _settle(root, window)
    assert window._labels["device"].cget("text") == messages["android_device_none"]
    assert str(window._start_button.cget("state")) == "disabled"


@pytest.mark.gui
def test_the_command_can_be_copied(dialog) -> None:
    window, copied, root = dialog
    _settle(root, window, 0.5)
    window._copy_command()
    assert copied == [android.install_command()]


@pytest.mark.gui
def test_the_window_shows_the_command_that_has_to_run_on_the_phone(dialog) -> None:
    window, _, root = dialog
    _settle(root, window, 0.5)
    shown = window._command.get("1.0", "end").strip()
    assert "install-android.sh" in shown
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
