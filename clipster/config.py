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
    #: Show a short notification window on startup (off by default; unused on tray start).
    show_startup_notification: bool = False
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
    #: Browser name for yt-dlp ``cookiesfrombrowser`` (empty = off).
    cookies_from_browser: str = ""
    #: Path to a Netscape cookies.txt for yt-dlp; empty = unused. Never log contents.
    cookies_file: str = ""
    #: User acknowledged cookie / ToS risk; required before cookies are passed to yt-dlp.
    cookies_risk_acknowledged: bool = False
    #: UTC ISO timestamp when cookie risk was acknowledged (empty when not acknowledged).
    cookies_risk_acknowledged_at: str = ""
    #: Ask once whether a desktop shortcut should be created.
    ask_desktop_shortcut: bool = True
    #: Start YouTube Clipster automatically when the user logs in.
    autostart: bool = False
    #: Hours between two yt-dlp update checks; ``0`` checks on every start.
    update_check_hours: int = 24
    #: Console log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
    log_level: str = "INFO"
    #: Word appended to Discover searches (for example ``lyrics``); empty disables it.
    discover_search_suffix: str = "lyrics"
    #: Keep only Discover results whose title contains the search suffix.
    discover_require_suffix: bool = True
    #: How Discover finds tracks: ``search``, ``related``, ``deezer``, or ``listenbrainz``.
    discover_mode: str = "related"
    #: Maximum number of Discover results shown in the list.
    discover_max_results: int = 40
    #: How many search hits to request per seed title from the download history.
    discover_results_per_seed: int = 6
    #: Stop collecting Discover seeds once at least this many usable seeds exist.
    discover_min_folder_seeds: int = 5
    #: When history + likes + download-dir seeds are still sparse, scan common music folders.
    discover_disk_scan_enabled: bool = True
    #: Request more Discover songs when this many tracks remain after the current one.
    discover_extend_remaining: int = 3
    #: How many extra songs to fetch when the playlist is topped up.
    discover_extend_count: int = 8
    #: Stream video inside the Streaming player when a video backend is available.
    discover_play_video: bool = False
    #: Audio-stage visualizer mode (see :mod:`clipster.visualizer`).
    discover_visualizer: str = "pulse"
    #: Accepted revision of the general terms of use (empty = not accepted).
    terms_app_version: str = ""
    #: UTC ISO timestamp when the general terms were accepted.
    terms_app_accepted_at: str = ""
    #: Accepted revision of the Streaming-specific terms (empty = not accepted).
    terms_streaming_version: str = ""
    #: UTC ISO timestamp when the Streaming terms were accepted.
    terms_streaming_accepted_at: str = ""

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
        from .visualizer import normalize_visualizer

        config.discover_visualizer = normalize_visualizer(config.discover_visualizer)
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
