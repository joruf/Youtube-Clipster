"""Platform detection and all filesystem locations used by YouTube Clipster.

Everything the application writes lives below :func:`install_dir` so that the
checkout itself stays clean and the same code works on Linux, Windows and macOS.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

#: Root of the checkout (the directory containing ``run.py``).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Environment variable that relocates the whole application data directory.
HOME_ENV_VAR = "YOUTUBE_CLIPSTER_HOME"


def install_dir() -> Path:
    """Return the writable application data directory for the current user."""
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "YoutubeClipster"
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "YoutubeClipster"
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "YoutubeClipster"


def venv_dir() -> Path:
    """Return the directory of the managed virtual environment."""
    return install_dir() / "venv"


def venv_python(gui: bool = False) -> Path:
    """Return the interpreter of the managed virtual environment.

    ``gui=True`` returns ``pythonw.exe`` on Windows, which starts without a
    console window; on every other platform both variants are identical.
    """
    if IS_WINDOWS:
        return venv_dir() / "Scripts" / ("pythonw.exe" if gui else "python.exe")
    return venv_dir() / "bin" / "python"


def running_in_managed_venv() -> bool:
    """Return ``True`` when the current interpreter is the managed venv."""
    try:
        return Path(sys.prefix).resolve() == venv_dir().resolve()
    except OSError:
        return False


def ffmpeg_dir() -> Path:
    """Return the directory of the privately installed FFmpeg build."""
    return install_dir() / "ffmpeg"


def bundled_ffmpeg_bin() -> Path:
    """Return the ``bin`` directory of the privately installed FFmpeg build."""
    return ffmpeg_dir() / "bin"


def bundled_ffmpeg_exe() -> Path:
    """Return the path the privately installed ``ffmpeg`` executable would have."""
    return bundled_ffmpeg_bin() / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")


def bundled_ffplay_exe() -> Path:
    """Return the path the privately installed ``ffplay`` executable would have."""
    return bundled_ffmpeg_bin() / ("ffplay.exe" if IS_WINDOWS else "ffplay")


def bundled_mpv_exe() -> Path:
    """Return a privately installed ``mpv`` path if we ever ship one beside FFmpeg."""
    return bundled_ffmpeg_bin() / ("mpv.exe" if IS_WINDOWS else "mpv")


def bootstrap_script() -> Path:
    """Return the path of the bootstrap launcher ``run.py``."""
    return PROJECT_ROOT / "run.py"


def lock_file() -> Path:
    """Return the single-instance lock file."""
    return install_dir() / "youtube-clipster.lock"


def log_file() -> Path:
    """Return the rotating log file."""
    return install_dir() / "youtube-clipster.log"


def state_file() -> Path:
    """Return the machine-managed state file (update timestamps, ...)."""
    return install_dir() / "state.json"


def config_file() -> Path:
    """Return the active configuration file.

    A ``config.json`` next to the sources wins (portable / per-checkout setup),
    otherwise the per-user file inside :func:`install_dir` is used.
    """
    portable = PROJECT_ROOT / "config.json"
    if portable.is_file():
        return portable
    return install_dir() / "config.json"


def history_file() -> Path:
    """Return the JSON file holding the download history.

    It always lives beside the active configuration file, so a portable
    checkout keeps its own history.
    """
    return config_file().with_name("history.json")


def discover_taste_file() -> Path:
    """Return the JSON file holding Streaming like / dislike votes.

    Stored beside the active configuration, like the download history.
    """
    return config_file().with_name("discover_taste.json")


#: Locations searched for the application icon, most specific first.
_ICON_CANDIDATES = (
    ("assets", "icons", "youtube-clipster.png"),
    ("assets", "youtube-clipster.png"),
    ("linux", "youtube-clipster-linux.png"),
    ("windows", "youtube-clipster-windows.png"),
)


def icon_file() -> Path:
    """Return the application icon (PNG, readable by Tk 8.6+).

    The first existing candidate wins; when none exists the canonical path is
    returned so callers can report a helpful location.
    """
    canonical = PROJECT_ROOT.joinpath(*_ICON_CANDIDATES[0])
    for parts in _ICON_CANDIDATES:
        candidate = PROJECT_ROOT.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return canonical


def windows_icon_file() -> Path:
    """Return the Windows shortcut icon; may not exist."""
    return PROJECT_ROOT / "assets" / "icons" / "youtube-clipster.ico"


def _xdg_user_dir(key: str) -> Path | None:
    """Resolve a single entry of the freedesktop ``user-dirs.dirs`` file."""
    base = os.environ.get("XDG_CONFIG_HOME")
    config = (Path(base) if base else Path.home() / ".config") / "user-dirs.dirs"
    try:
        content = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in content.splitlines():
        line = raw.strip()
        if not line.startswith(key + "="):
            continue
        value = line.split("=", 1)[1].strip().strip('"')
        value = value.replace("$HOME", str(Path.home()))
        candidate = Path(value)
        if candidate.is_dir():
            return candidate
    return None


def _windows_shell_folder(guid: str) -> Path | None:
    """Resolve a Windows known folder through the registry."""
    try:
        import winreg
    except ImportError:
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, guid)
    except OSError:
        return None
    expanded = Path(os.path.expandvars(value))
    return expanded if expanded.is_dir() else None


def default_download_dir() -> Path:
    """Return the user's download folder, honouring OS specific settings."""
    if IS_WINDOWS:
        folder = _windows_shell_folder("{374DE290-123F-4565-9164-39C4925E467B}")
        if folder is not None:
            return folder
    elif IS_LINUX:
        folder = _xdg_user_dir("XDG_DOWNLOAD_DIR")
        if folder is not None:
            return folder
    return Path.home() / "Downloads"


def desktop_dir() -> Path:
    """Return the user's desktop folder, falling back to the home directory."""
    if IS_WINDOWS:
        folder = _windows_shell_folder("Desktop")
        if folder is not None:
            return folder
    elif IS_LINUX:
        folder = _xdg_user_dir("XDG_DESKTOP_DIR")
        if folder is not None:
            return folder
    for name in ("Desktop", "Schreibtisch", "Bureau", "Escritorio"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    return Path.home()


def autostart_dir() -> Path:
    """Return the freedesktop autostart directory (Linux only)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "autostart"


def ensure_install_dir() -> Path:
    """Create and return :func:`install_dir`."""
    target = install_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target
