"""User configuration, stored as JSON.

The configuration replaces ``linux/config.cfg`` and the ``set "..."`` block of
the old Windows batch file with a single file that both platforms share.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict

from . import paths
from .logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class Config:
    """All user-tunable settings of YouTube Clipster."""

    #: UI language; ``de`` or ``en`` (any file in ``clipster/locales``).
    language: str = "en"
    #: Target directory for downloads; empty means "the OS download folder".
    download_dir: str = ""
    #: Clipboard polling interval in seconds.
    interval_sec: float = 2.0
    #: Show a short notification window on startup.
    show_startup_notification: bool = True
    #: Format preselected in the navigation window (``mp3`` or ``mp4``).
    default_format: str = "mp3"
    #: Open the view window automatically once a download finished.
    open_view_after_download: bool = False
    #: Maximum number of entries kept in the download list.
    history_limit: int = 100
    #: Look for a newer version on GitHub when the program starts.
    check_updates: bool = True
    #: Run several downloads at the same time instead of one after another.
    parallel_downloads: bool = False
    #: Upper bound while :attr:`parallel_downloads` is on.
    max_parallel_downloads: int = 3
    #: Place an icon in the system tray (needs pystray).
    use_tray: bool = True
    #: Start without showing the status window (only when the tray works).
    start_minimized: bool = True
    #: Open the download folder in the file manager after a finished download.
    open_folder_after_download: bool = False
    #: Explicit file manager command; empty means the OS default handler.
    file_manager: str = ""
    #: Empty the clipboard after a successful download.
    clear_clipboard_after_download: bool = True
    #: Ask for the preferred audio track when a video offers several languages.
    ask_audio_language: bool = True
    #: Never download a whole playlist, even when the URL contains a list id.
    no_playlist: bool = True
    #: Restrict file names to ASCII (``--restrict-filenames`` of the old script).
    restrict_filenames: bool = False
    #: yt-dlp output template.
    output_template: str = "%(title)s.%(ext)s"
    #: Custom HTTP user agent; empty means the yt-dlp default.
    user_agent: str = ""
    #: Ask once whether a desktop shortcut should be created.
    ask_desktop_shortcut: bool = True
    #: Start YouTube Clipster automatically when the user logs in.
    autostart: bool = False
    #: Hours between two yt-dlp update checks; ``0`` checks on every start.
    update_check_hours: int = 24
    #: Console log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
    log_level: str = "INFO"

    #: Path this configuration was loaded from; not serialised.
    path: Path = field(default_factory=paths.config_file, compare=False, repr=False)

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    def resolved_download_dir(self) -> Path:
        """Return the effective download directory as an absolute path."""
        if self.download_dir.strip():
            return Path(self.download_dir).expanduser()
        return paths.default_download_dir()

    def poll_interval_ms(self) -> int:
        """Return the clipboard polling interval in milliseconds (>= 250 ms)."""
        return max(250, int(self.interval_sec * 1000))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Path) -> "Config":
        """Build a configuration from raw JSON data.

        Unknown keys are ignored and values with a wrong type fall back to the
        default, so a hand-edited file can never crash the application.

        :param data: The decoded JSON object.
        :param path: The file the data came from.
        :return: The populated configuration.
        """
        config = cls(path=path)
        for item in fields(cls):
            if item.name == "path" or item.name not in data:
                continue
            value = data[item.name]
            default = getattr(config, item.name)
            try:
                if isinstance(default, bool):
                    coerced: Any = bool(value)
                elif isinstance(default, float):
                    coerced = float(value)
                elif isinstance(default, int):
                    coerced = int(value)
                else:
                    coerced = "" if value is None else str(value)
            except (TypeError, ValueError):
                log.warning("Ignoring invalid value for '%s' in %s", item.name, path)
                continue
            setattr(config, item.name, coerced)
        return config

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load the configuration, creating it with defaults when missing.

        :param path: Optional explicit configuration file.
        :return: The loaded (or freshly created) configuration.
        """
        target = Path(path).expanduser() if path else paths.config_file()
        if not target.is_file():
            config = cls(path=target)
            config.save()
            log.info("Created default configuration at %s", target)
            return config
        try:
            with target.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            log.error("Could not read %s (%s) - using defaults", target, exc)
            return cls(path=target)
        if not isinstance(data, dict):
            log.error("%s does not contain a JSON object - using defaults", target)
            return cls(path=target)
        return cls.from_dict(data, target)

    def to_dict(self) -> Dict[str, Any]:
        """Return the serialisable representation of this configuration."""
        data = asdict(self)
        data.pop("path", None)
        return data

    def save(self) -> None:
        """Write the configuration back to disk (atomically)."""
        target = self.path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            temp.replace(target)
        except OSError as exc:
            log.error("Could not write configuration %s: %s", target, exc)


def load_state() -> Dict[str, Any]:
    """Return the machine-managed state (update timestamps and similar)."""
    target = paths.state_file()
    if not target.is_file():
        return {}
    try:
        with target.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: Dict[str, Any]) -> None:
    """Persist the machine-managed state.

    :param state: The complete state mapping to write.
    :return: None
    """
    target = paths.state_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        temp.replace(target)
    except OSError as exc:
        log.debug("Could not write state file %s: %s", target, exc)
