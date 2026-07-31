"""Desktop shortcuts and autostart entries for Linux and Windows.

Replaces ``check_desktop_launcher`` from ``linux/lib/installer.sh`` and the
``:check_autostart`` registry block of the old Windows batch file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import APP_SHORT_NAME, paths
from .logging_setup import get_logger

log = get_logger(__name__)

_ENTRY_NAME = "youtube-clipster"
_REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REGISTRY_VALUE = "YouTubeClipster"
_COMMENT = "Download YouTube videos and audio by copying a link"


def launch_command(gui: bool = True) -> List[str]:
    """Return the command that starts YouTube Clipster.

    The managed virtual environment interpreter is preferred so that shortcuts
    keep working even when the system Python changes.

    :param gui: Use ``pythonw.exe`` on Windows (no console window).
    :return: The argument vector, interpreter first.
    """
    interpreter = paths.venv_python(gui=gui)
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    return [str(interpreter), str(paths.bootstrap_script())]


def _quote(value: str) -> str:
    """Return ``value`` wrapped in double quotes for Exec= / shortcut targets."""
    return '"{0}"'.format(value)


def _ps_quote(value: object) -> str:
    r"""Escape a value for a single quoted PowerShell string.

    PowerShell ends a single quoted string at the first ``'``, so a path such as
    ``C:\Users\O'Brien\Desktop`` would break the generated script.  Doubling the
    quote is the documented escape.

    :param value: The value to embed.
    :return: The escaped text, without the surrounding quotes.
    """
    return str(value).replace("'", "''")


def _no_window() -> Dict[str, int]:
    """Return the ``subprocess`` keywords that suppress a console window.

    Started from the desktop shortcut the program runs under ``pythonw.exe``,
    which has no console; every child process would otherwise flash a black
    window for a moment.

    :return: Keyword arguments for ``subprocess``; empty off Windows.
    """
    if not paths.IS_WINDOWS:
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


# ----------------------------------------------------------------------
# Linux (freedesktop.org)
# ----------------------------------------------------------------------
def _desktop_entry_text(autostart: bool = False) -> str:
    """Build the contents of a ``.desktop`` file.

    :param autostart: Add the autostart specific keys.
    :return: The complete file content.
    """
    command = launch_command(gui=False)
    exec_line = " ".join(_quote(part) for part in command)
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        "Name={0}".format(APP_SHORT_NAME),
        "Comment={0}".format(_COMMENT),
        "Exec={0}".format(exec_line),
        "Icon={0}".format(paths.icon_file()),
        "Terminal=false",
        "Categories=Network;AudioVideo;Utility;",
        "StartupNotify=false",
    ]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def _write_desktop_entry(target: Path, autostart: bool = False) -> Path:
    """Write and mark a ``.desktop`` file as executable and trusted.

    :param target: Destination path.
    :param autostart: Add the autostart specific keys.
    :return: The written path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_desktop_entry_text(autostart=autostart), encoding="utf-8")
    target.chmod(0o755)
    # GNOME and Cinnamon refuse to run launchers that are not marked as trusted.
    try:
        subprocess.run(
            ["gio", "set", str(target), "metadata::trusted", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return target


# ----------------------------------------------------------------------
# Windows
# ----------------------------------------------------------------------
def _powershell(script: str) -> bool:
    """Run a short PowerShell snippet.

    :param script: The PowerShell source to execute.
    :return: ``True`` when PowerShell exited successfully.
    """
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
            **_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("PowerShell call failed: %s", exc)
        return False
    if completed.returncode != 0:
        log.debug("PowerShell error: %s", completed.stderr.decode("utf-8", errors="replace").strip())
    return completed.returncode == 0


def _create_windows_shortcut(target: Path) -> bool:
    """Create a ``.lnk`` file through the Windows Script Host.

    :param target: The destination ``.lnk`` path.
    :return: ``True`` on success.
    """
    command = launch_command(gui=True)
    icon = paths.windows_icon_file()
    icon_line = "$s.IconLocation = '{0}';".format(_ps_quote(icon)) if icon.is_file() else ""
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        "$s = $w.CreateShortcut('{lnk}'); "
        "$s.TargetPath = '{exe}'; "
        "$s.Arguments = '\"{script}\"'; "
        "$s.WorkingDirectory = '{cwd}'; "
        "$s.Description = '{desc}'; "
        "{icon}"
        "$s.Save()"
    ).format(
        lnk=_ps_quote(target),
        exe=_ps_quote(command[0]),
        script=_ps_quote(command[1]),
        cwd=_ps_quote(paths.PROJECT_ROOT),
        desc=_ps_quote(_COMMENT),
        icon=icon_line,
    )
    return _powershell(script)


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def desktop_shortcut_path() -> Path:
    """Return the path the desktop shortcut has (or would have)."""
    if paths.IS_WINDOWS:
        return paths.desktop_dir() / "{0}.lnk".format(APP_SHORT_NAME)
    return paths.desktop_dir() / "{0}.desktop".format(_ENTRY_NAME)


def desktop_shortcut_exists() -> bool:
    """Return ``True`` when a desktop shortcut is already present."""
    return desktop_shortcut_path().exists()


def create_desktop_shortcut() -> Path:
    """Create the desktop shortcut for the current platform.

    :return: The path of the created shortcut.
    :raises OSError: When the shortcut could not be written.
    """
    target = desktop_shortcut_path()
    if paths.IS_WINDOWS:
        if not _create_windows_shortcut(target):
            raise OSError("Windows Script Host could not create {0}".format(target))
        log.info("Desktop shortcut created: %s", target)
        return target
    _write_desktop_entry(target)
    log.info("Desktop shortcut created: %s", target)
    return target


def autostart_path() -> Optional[Path]:
    """Return the autostart file path on Linux/macOS, ``None`` on Windows."""
    if paths.IS_WINDOWS:
        return None
    return paths.autostart_dir() / "{0}.desktop".format(_ENTRY_NAME)


def autostart_enabled() -> bool:
    """Return ``True`` when YouTube Clipster starts automatically at login."""
    if paths.IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_RUN_KEY) as key:
                winreg.QueryValueEx(key, _REGISTRY_VALUE)
            return True
        except (OSError, ImportError):
            return False
    target = autostart_path()
    return bool(target and target.is_file())


def set_autostart(enabled: bool) -> bool:
    """Enable or disable the login autostart.

    :param enabled: ``True`` installs the entry, ``False`` removes it.
    :return: ``True`` when the requested state was reached.
    """
    if paths.IS_WINDOWS:
        return _set_autostart_windows(enabled)
    return _set_autostart_linux(enabled)


def _set_autostart_windows(enabled: bool) -> bool:
    """Write or delete the HKCU ``Run`` registry value."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - not Windows
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REGISTRY_RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
            if enabled:
                command = launch_command(gui=True)
                value = "{0} {1}".format(_quote(command[0]), _quote(command[1]))
                winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_SZ, value)
                log.info("Autostart enabled (registry): %s", value)
            else:
                try:
                    winreg.DeleteValue(key, _REGISTRY_VALUE)
                    log.info("Autostart disabled (registry).")
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log.error("Autostart could not be changed: %s", exc)
        return False


def _set_autostart_linux(enabled: bool) -> bool:
    """Write or delete the freedesktop autostart entry."""
    target = autostart_path()
    if target is None:  # pragma: no cover - Windows handled above
        return False
    try:
        if enabled:
            _write_desktop_entry(target, autostart=True)
            log.info("Autostart enabled: %s", target)
        elif target.exists():
            os.remove(target)
            log.info("Autostart disabled: %s", target)
        return True
    except OSError as exc:
        log.error("Autostart could not be changed: %s", exc)
        return False


def open_folder(folder: Path, file_manager: str = "") -> None:
    """Open ``folder`` in the platform file manager.

    :param folder: The directory to reveal.
    :param file_manager: Optional explicit command (e.g. ``nemo``).
    :return: None
    """
    try:
        if file_manager.strip():
            subprocess.Popen([file_manager.strip(), str(folder)], **_no_window())
            return
        if paths.IS_WINDOWS:
            os.startfile(str(folder))  # type: ignore[attr-defined]  # noqa: S606 - user's own folder
        elif paths.IS_MACOS:
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Download folder could not be opened: %s", exc)


def open_path(target: Path) -> bool:
    """Play or open ``target`` with the user's default application.

    :param target: The downloaded file.
    :return: ``True`` when a handler was launched.
    """
    if not target.exists():
        log.warning("Cannot open %s - the file no longer exists.", target)
        return False
    try:
        if paths.IS_WINDOWS:
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606 - user's own file
        elif paths.IS_MACOS:
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s could not be opened: %s", target, exc)
        return False


#: File managers that can preselect a file, and the flag that does it.
_SELECT_FLAGS = (
    ("nautilus", "--select"),
    ("nemo", ""),
    ("dolphin", "--select"),
    ("thunar", ""),
    ("caja", "--select"),
    ("pcmanfm", ""),
)


def reveal_path(target: Path, file_manager: str = "") -> bool:
    """Open the folder containing ``target`` and select the file if possible.

    Falls back to simply opening the parent directory when the file manager
    cannot preselect an entry.

    :param target: The downloaded file.
    :param file_manager: Optional explicit command (e.g. ``nemo``).
    :return: ``True`` when a file manager was launched.
    """
    folder = target.parent if target.name else target
    if not folder.is_dir():
        log.warning("Cannot reveal %s - the folder no longer exists.", folder)
        return False

    explicit = file_manager.strip()
    try:
        if explicit:
            subprocess.Popen([explicit, str(target if target.exists() else folder)], **_no_window())
            return True
        if paths.IS_WINDOWS:
            if target.exists():
                # The command line has to read  explorer /select,"C:\dir\file" .
                # Passing it as a list makes Python quote the whole switch, which
                # explorer then fails to parse. It also exits non-zero on success.
                subprocess.Popen('explorer /select,"{0}"'.format(target), **_no_window())
            else:
                os.startfile(str(folder))  # type: ignore[attr-defined]  # noqa: S606 - user's own folder
            return True
        if paths.IS_MACOS:
            subprocess.Popen(["open", "-R", str(target)] if target.exists() else ["open", str(folder)])
            return True

        if target.exists():
            for command, flag in _SELECT_FLAGS:
                if shutil.which(command) is None:
                    continue
                arguments = [command] + ([flag] if flag else []) + [str(target)]
                subprocess.Popen(arguments)
                return True
        subprocess.Popen(["xdg-open", str(folder)])
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s could not be revealed: %s", target, exc)
        return False
