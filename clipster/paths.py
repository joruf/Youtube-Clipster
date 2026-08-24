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


def is_termux() -> bool:
    """Return whether this runs inside Termux on Android.

    Android reports itself as Linux, so nothing else would tell the difference -
    and it matters: Termux has its own package manager, needs no ``sudo``, and
    has neither a system tray nor an X server.

    :return: ``True`` inside Termux.
    """
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    return bool(os.environ.get("TERMUX_VERSION")) or Path("/data/data/com.termux").exists()


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


def deno_dir() -> Path:
    """Return the directory of the privately installed Deno runtime."""
    return install_dir() / "deno"


def bundled_deno_exe() -> Path:
    """Return the path the privately installed ``deno`` executable would have.

    yt-dlp needs a JavaScript engine to solve YouTube's signature and ``n``
    challenges.  Distributions rarely ship a new enough one - Ubuntu's
    ``quickjs`` predates the minimum by years - so Clipster keeps its own next
    to the FFmpeg it already downloads.
    """
    return deno_dir() / ("deno.exe" if IS_WINDOWS else "deno")


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


def discover_queue_file() -> Path:
    """Return the JSON file holding the last Streaming playlist.

    Stored beside the active configuration so a restart can restore the queue.
    """
    return config_file().with_name("discover_queue.json")


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


def web_root() -> Path:
    """Return the directory holding the files of the phone interface.

    These ship with the sources, next to the code that serves them.
    """
    return Path(__file__).resolve().parent / "web"


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


#: Folder created inside the system Downloads directory.  Every platform uses
#: it: Android always did, and the desktop dropping its files straight into
#: ``~/Downloads`` mixed them in with everything a browser ever saved.
DOWNLOAD_SUBFOLDER = "clipster"


def system_download_dir() -> Path:
    """Return the operating system's own Downloads folder.

    The plain folder, without Clipster's subfolder - what a file dialog would
    open at, and the base :func:`default_download_dir` builds on.

    :return: The user's Downloads directory.
    """
    if IS_WINDOWS:
        folder = _windows_shell_folder("{374DE290-123F-4565-9164-39C4925E467B}")
        if folder is not None:
            return folder
    elif IS_LINUX:
        folder = _xdg_user_dir("XDG_DOWNLOAD_DIR")
        if folder is not None:
            return folder
    return Path.home() / "Downloads"


def default_download_dir() -> Path:
    """Return the folder downloads land in when Settings names none.

    Always the system Downloads folder plus :data:`DOWNLOAD_SUBFOLDER`, so the
    files stay together and apart from everything else that folder collects.
    Android resolves to the *shared* phone Downloads tree rather than Termux's
    private ``~/Downloads``, which no file manager would show.

    :return: The default download directory.
    """
    if is_termux():
        return android_public_download_dir()
    return system_download_dir() / DOWNLOAD_SUBFOLDER


#: Public Android path shown in Settings / About and stored in config.
ANDROID_PUBLIC_DOWNLOAD = Path("/storage/emulated/0/Download") / DOWNLOAD_SUBFOLDER


def android_public_download_dir() -> Path:
    """Return the phone's shared ``Download/clipster`` path (for config / UI)."""
    return ANDROID_PUBLIC_DOWNLOAD


def android_download_dir() -> Path:
    """Return a writable shared ``Download/clipster`` path for Termux.

    Prefers the Termux shared-storage link (``~/storage/downloads``), which is
    what actually works for I/O after ``termux-setup-storage``. The public
    ``/storage/emulated/0/Download/clipster`` form is used for display and
    config; :func:`android_writable_download_dir` maps between them.

    :return: Absolute path under the phone's public Download folder.
    """
    link_root = _android_storage_link_root()
    link = link_root / DOWNLOAD_SUBFOLDER
    if link_root.is_symlink() or _path_present(link_root):
        return link
    for path in (
        ANDROID_PUBLIC_DOWNLOAD,
        Path("/sdcard/Download") / DOWNLOAD_SUBFOLDER,
    ):
        if _path_present(path.parent):
            return path
    return link


def _android_storage_link_root() -> Path:
    """Return Termux ``~/storage/downloads`` (symlink to public Download)."""
    return Path.home() / "storage" / "downloads"


def _path_present(path: Path) -> bool:
    """Return whether ``path`` exists or is a symlink (even if the target is blocked)."""
    try:
        return path.is_symlink() or path.exists()
    except OSError:
        return False


def android_writable_download_dir(configured: Path | str) -> Path:
    """Map a public ``Download/...`` config path to the Termux-writable link.

    Termux often cannot create ``/storage/emulated/0/...`` directly, but can
    write the same place through ``~/storage/downloads/...``.

    :param configured: Path from config or the UI.
    :return: Path to use for mkdir / yt-dlp output.
    """
    path = Path(str(configured or "")).expanduser()
    if not is_termux():
        return path
    text = str(path)
    link_root = _android_storage_link_root()
    public_roots = (
        "/storage/emulated/0/Download",
        "/sdcard/Download",
        "/storage/self/primary/Download",
    )
    for root in public_roots:
        if text == root or text.startswith(root + "/"):
            rest = text[len(root) :].lstrip("/") or DOWNLOAD_SUBFOLDER
            if _path_present(link_root):
                return link_root / rest
            return Path(root) / rest
    return path


def friendly_download_path(path: Path | str) -> str:
    """Return a path that file managers recognise (public Download/...).

    :param path: Internal or public download directory.
    :return: Display string for About / Settings.
    """
    text = str(path or "").strip()
    if not text:
        return str(ANDROID_PUBLIC_DOWNLOAD)
    link_root = str(_android_storage_link_root())
    normalized = str(Path(text).expanduser())
    if normalized == link_root or normalized.startswith(link_root + os.sep):
        rest = normalized[len(link_root) :].lstrip("/") or DOWNLOAD_SUBFOLDER
        return str(Path("/storage/emulated/0/Download") / rest)
    # /data/data/.../home/storage/downloads/...
    marker = "/home/storage/downloads"
    if marker in normalized.replace("\\", "/"):
        rest = normalized.split(marker, 1)[-1].lstrip("/") or DOWNLOAD_SUBFOLDER
        return str(Path("/storage/emulated/0/Download") / rest)
    try:
        resolved = str(Path(normalized).resolve())
        if "/Download/" in resolved or resolved.endswith("/Download"):
            return resolved
    except OSError:
        pass
    return normalized


def is_private_termux_download_dir(path: Path | str) -> bool:
    """Return whether ``path`` is Termux's private home Downloads (not shared).

    :param path: Candidate download directory.
    :return: ``True`` when files would stay inside the Termux app sandbox.
    """
    text = str(path or "").strip()
    if not text:
        return False
    resolved = str(Path(text).expanduser())
    # Shared storage (public or Termux link) is never "private".
    if resolved.startswith("/sdcard/") or "/storage/emulated/" in resolved:
        return False
    if "/home/storage/downloads" in resolved.replace("\\", "/"):
        return False
    if resolved == str(_android_storage_link_root()) or resolved.startswith(
        str(_android_storage_link_root()) + os.sep
    ):
        return False
    home_downloads = str(Path.home() / "Downloads")
    if resolved == home_downloads or resolved.startswith(home_downloads + os.sep):
        return True
    return "/data/data/com.termux/files/home/Downloads" in resolved


def is_termux_link_download_dir(path: Path | str) -> bool:
    """Return whether ``path`` is the Termux ``~/storage/downloads`` form."""
    text = str(Path(str(path or "")).expanduser())
    link = str(_android_storage_link_root())
    return text == link or text.startswith(link + os.sep) or "/home/storage/downloads" in text.replace(
        "\\", "/"
    )


def normalize_android_download_setting(value: str) -> str:
    """Turn a user-entered download folder into a stored absolute path.

    Accepts short forms such as ``Download/clipster`` or ``~/storage/downloads/clipster``.

    :param value: Text from Settings.
    :return: Absolute path string (may be empty when input is empty).
    """
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.replace("\\", "/").lstrip("./")
    aliases = {
        "download/clipster": str(ANDROID_PUBLIC_DOWNLOAD),
        "downloads/clipster": str(ANDROID_PUBLIC_DOWNLOAD),
        "download": str(ANDROID_PUBLIC_DOWNLOAD.parent),
        "downloads": str(ANDROID_PUBLIC_DOWNLOAD.parent),
    }
    if lowered.lower() in aliases:
        return aliases[lowered.lower()]
    if lowered.lower().startswith("download/"):
        return str(Path("/storage/emulated/0") / lowered)
    if lowered.lower().startswith("downloads/"):
        rest = lowered.split("/", 1)[1]
        return str(Path("/storage/emulated/0/Download") / rest)
    expanded = Path(text).expanduser()
    if is_termux_link_download_dir(expanded):
        return friendly_download_path(expanded)
    return str(expanded)


def ensure_android_download_dir(config: object) -> bool:
    """Point an Android config at shared ``Download/clipster`` when needed.

    Rewrites private Termux ``~/Downloads`` and the opaque
    ``~/storage/downloads/clipster`` form to the public path that Android file
    managers show. Actual writes still go through the Termux storage link via
    :func:`android_writable_download_dir`.

    :param config: Live config object with a ``download_dir`` attribute.
    :return: ``True`` when ``config.download_dir`` was changed.
    """
    if not is_termux():
        return False
    wanted = str(android_public_download_dir())
    current = str(getattr(config, "download_dir", "") or "").strip()
    if (
        not current
        or is_private_termux_download_dir(current)
        or is_termux_link_download_dir(current)
    ):
        if current == wanted:
            return False
        setattr(config, "download_dir", wanted)
        return True
    return False


def default_music_dir() -> Path | None:
    """Return the user's Music folder when it exists, else ``None``.

    Honours XDG ``XDG_MUSIC_DIR`` on Linux and the Windows Known Folder for
    Music. Falls back to ``~/Music`` (or common localised names) only when that
    directory already exists — never invents a new Music tree.
    """
    if IS_WINDOWS:
        folder = _windows_shell_folder("{4BD8D571-6D19-48D3-BE97-422220080E43}")
        if folder is not None:
            return folder
    elif IS_LINUX:
        folder = _xdg_user_dir("XDG_MUSIC_DIR")
        if folder is not None:
            return folder
    for name in ("Music", "Musik", "Musique", "Música", "Musica"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    return None


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
