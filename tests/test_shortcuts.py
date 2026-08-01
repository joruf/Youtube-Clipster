"""Desktop shortcuts, autostart entries and opening files - Windows included."""

from __future__ import annotations

import subprocess
from pathlib import Path, PureWindowsPath

import pytest

from clipster import paths, shortcuts


# ----------------------------------------------------------------------
# Quoting
# ----------------------------------------------------------------------
def test_a_path_with_an_apostrophe_cannot_break_the_powershell_script() -> None:
    r"""``C:\Users\O'Brien`` used to end the string and kill shortcut creation."""
    awkward = PureWindowsPath(r"C:\Users\O'Brien\Desktop\YouTube Clipster.lnk")
    quoted = shortcuts._ps_quote(awkward)
    assert "O''Brien" in quoted
    script = "$s = $w.CreateShortcut('{0}');".format(quoted)
    assert script.count("'") % 2 == 0, "unbalanced quotes would be a syntax error"


def test_ordinary_values_are_left_alone() -> None:
    assert shortcuts._ps_quote(r"C:\Tools\python.exe") == r"C:\Tools\python.exe"
    assert shortcuts._ps_quote(42) == "42"


def test_exec_values_are_double_quoted() -> None:
    assert shortcuts._quote("/a b/c") == '"/a b/c"'


def test_the_autostart_registry_value_quotes_both_parts() -> None:
    value = "{0} {1}".format(shortcuts._quote(r"C:\a b\pythonw.exe"),
                             shortcuts._quote(r"C:\c d\run.py"))
    assert value == '"C:\\a b\\pythonw.exe" "C:\\c d\\run.py"'


# ----------------------------------------------------------------------
# Console windows
# ----------------------------------------------------------------------
def test_no_console_flag_is_used_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    assert shortcuts._no_window() == {}


def test_child_processes_stay_invisible_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under pythonw every child would otherwise flash a console window."""
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    assert shortcuts._no_window() == {"creationflags": 0x08000000}


def test_the_explorer_command_quotes_only_the_path() -> None:
    r"""``explorer "/select,C:\..."`` is mis-parsed; the path alone must be quoted."""
    target = r"C:\Users\Jo Ruf\Downloads\Some Video.mp3"
    correct = 'explorer /select,"{0}"'.format(target)
    assert correct.startswith('explorer /select,"')
    wrong = subprocess.list2cmdline(["explorer", "/select,{0}".format(target)])
    assert wrong.startswith('explorer "/select,'), "this is what the list form produces"
    assert correct != wrong


# ----------------------------------------------------------------------
# Launch command and desktop entry
# ----------------------------------------------------------------------
def test_the_launch_command_points_at_the_entry_point() -> None:
    command = shortcuts.launch_command()
    assert len(command) == 2
    assert command[1].endswith("run.py")


def test_the_desktop_entry_is_complete() -> None:
    entry = shortcuts._desktop_entry_text()
    assert entry.startswith("[Desktop Entry]")
    for key in ("Type=Application", "Name=", "Exec=", "Icon=", "Terminal=false"):
        assert key in entry
    assert "run.py" in entry


def test_the_autostart_entry_carries_the_extra_key() -> None:
    assert "X-GNOME-Autostart-enabled=true" in shortcuts._desktop_entry_text(autostart=True)


def test_the_shortcut_name_matches_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    assert shortcuts.desktop_shortcut_path().suffix == ".lnk"
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    assert shortcuts.desktop_shortcut_path().suffix == ".desktop"


def test_windows_has_no_autostart_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """It uses the registry instead."""
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    assert shortcuts.autostart_path() is None


# ----------------------------------------------------------------------
# Opening files
# ----------------------------------------------------------------------
def test_opening_a_file_that_is_gone_reports_failure() -> None:
    assert shortcuts.open_path(Path("/nope/none.mp3")) is False


def test_revealing_inside_a_folder_that_is_gone_reports_failure() -> None:
    assert shortcuts.reveal_path(Path("/nope/deeper/none.mp3")) is False


def test_a_real_file_is_handed_to_the_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    launched: list = []
    monkeypatch.setattr(shortcuts.subprocess, "Popen", lambda *a, **k: launched.append(a) or None)
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    assert shortcuts.open_path(target) is True
    assert launched, "nothing was launched"


def test_the_configured_file_manager_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    launched: list = []
    monkeypatch.setattr(shortcuts.subprocess, "Popen", lambda *a, **k: launched.append(a[0]) or None)
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    shortcuts.reveal_path(target, file_manager="my-explorer")
    assert launched and launched[0][0] == "my-explorer"


def test_opening_a_folder_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args, **kwargs):
        raise OSError("no desktop here")

    monkeypatch.setattr(shortcuts.subprocess, "Popen", explode)
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    shortcuts.open_folder(tmp_path)  # must not raise
