"""yt-dlp integration.

Replaces ``linux/lib/downloader.sh`` and the ``:download`` label of the old
Windows batch file.  Instead of parsing the console output of a yt-dlp binary,
the Python API is used directly, which gives exact progress values on every
platform.
"""

from __future__ import annotations

import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import paths
from .config import Config
from .i18n import Messages
from .installer import find_ffmpeg
from .logging_setup import get_logger

log = get_logger(__name__)

#: Patterns that isolate the 11 character video id, most specific first.  The
#: query based ones are order independent, so a shared link that puts ``list``
#: or ``si`` before ``v`` still resolves.
_VIDEO_ID_PATTERNS = (
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})", re.IGNORECASE),
    re.compile(r"youtube\.com/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})", re.IGNORECASE),
    re.compile(r"youtube\.com/watch\?[^\s]*?\bv=([A-Za-z0-9_-]{11})", re.IGNORECASE),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})", re.IGNORECASE),
)

#: Every recognised link is reduced to this canonical form.
_CANONICAL_URL = "https://www.youtube.com/watch?v={0}"

_BOT_PATTERNS = (
    "confirm you are not a bot",
    "confirm you're not a bot",
    "sign in to confirm",
    "http error 429",
    "too many requests",
)

_UNAVAILABLE_PATTERNS = (
    "video unavailable",
    "private video",
    "this video is not available",
    "removed by the uploader",
    "not available in your country",
    "members-only",
)

_TITLE_SANITIZE_RE = re.compile(r"[\r\n\t]+")


def extract_video_id(text: str) -> Optional[str]:
    """Return the YouTube video id contained in ``text``.

    :param text: Arbitrary clipboard content.
    :return: The 11 character id, or ``None`` when the text holds no link.
    """
    if not text or "youtu" not in text.lower():
        return None
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def extract_youtube_url(text: str) -> Optional[str]:
    """Return a canonical watch URL for the YouTube link in ``text``.

    The link is normalised to ``https://www.youtube.com/watch?v=<id>``.  That
    drops ``list``/``index``/``si`` parameters, so a video copied out of a
    playlist or a radio mix is downloaded on its own and the same video copied
    twice in different forms is recognised as the same link.

    :param text: Arbitrary clipboard content.
    :return: The canonical URL, or ``None`` when the text holds no YouTube link.
    """
    video_id = extract_video_id(text)
    return _CANONICAL_URL.format(video_id) if video_id else None


class DownloadCanceled(Exception):
    """Raised internally when the user cancels a running download."""


class MetadataError(Exception):
    """Raised when the video information could not be fetched."""


#: yt-dlp's name for each engine, keyed by the executable we look for.
_JS_RUNTIMES = (("qjs", "quickjs"), ("node", "node"), ("deno", "deno"))


def _js_runtime() -> Optional[str]:
    """Return the JavaScript engine yt-dlp should use, or ``None``.

    :return: ``quickjs``, ``node``, ``deno`` or ``None`` when none is installed.
    """
    for executable, name in _JS_RUNTIMES:
        if shutil.which(executable):
            return name
    return None


def _import_yt_dlp() -> Any:
    """Import ``yt_dlp`` lazily and turn a missing package into a clear error.

    :return: The ``yt_dlp.YoutubeDL`` class.
    :raises MetadataError: When yt-dlp is not installed in this interpreter.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise MetadataError(
            "yt-dlp is not installed. Run 'python3 youtube-clipster.py --update' "
            "(or '--reinstall') to set up the environment. Details: {0}".format(exc)
        ) from exc
    return YoutubeDL


class DownloadFailed(Exception):
    """Raised when yt-dlp could not finish the download."""

    def __init__(self, message: str, kind: str = "generic") -> None:
        """
        :param message: The raw error text from yt-dlp.
        :param kind: ``bot``, ``unavailable`` or ``generic``.
        """
        super().__init__(message)
        self.kind = kind


@dataclass
class AudioTrack:
    """One selectable audio track of a video."""

    #: Language code as reported by yt-dlp, e.g. ``de`` or ``en-US``.
    code: str
    #: ``True`` for the track the video was originally published with.
    original: bool = False


@dataclass
class VideoInfo:
    """The subset of yt-dlp metadata the UI needs."""

    url: str
    title: str
    duration: Optional[int] = None
    uploader: str = ""
    #: Language codes, the original track first.
    audio_languages: List[str] = field(default_factory=list)
    #: The same tracks with the "is the original" flag kept.
    audio_tracks: List[AudioTrack] = field(default_factory=list)

    def original_language(self) -> str:
        """Return the code of the original audio track, or an empty string."""
        for track in self.audio_tracks:
            if track.original:
                return track.code
        return ""


@dataclass
class Progress:
    """One progress update handed to the GUI."""

    #: ``preparing``, ``downloading``, ``converting``, ``merging`` or ``finished``.
    phase: str
    #: 0-100, or ``None`` for an indeterminate state.
    percent: Optional[float] = None
    #: Optional extra text (speed, ETA, ...).
    detail: str = ""


ProgressCallback = Callable[[Progress], None]


class _YtDlpLogger:
    """Adapter that routes yt-dlp log output into the application logger."""

    def debug(self, message: str) -> None:
        """Handle a yt-dlp debug (or plain console) message."""
        text = message.lstrip()
        if text.startswith("[debug] "):
            log.debug("%s", text[8:])
        elif text:
            log.debug("%s", text)

    def info(self, message: str) -> None:
        """Handle a yt-dlp info message."""
        log.debug("%s", message)

    def warning(self, message: str) -> None:
        """Handle a yt-dlp warning."""
        log.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        """Handle a yt-dlp error."""
        log.error("yt-dlp: %s", message)


def classify_error(message: str) -> str:
    """Classify a yt-dlp error message.

    :param message: The raw error text.
    :return: ``bot``, ``unavailable`` or ``generic``.
    """
    lowered = (message or "").lower()
    if any(pattern in lowered for pattern in _BOT_PATTERNS):
        return "bot"
    if any(pattern in lowered for pattern in _UNAVAILABLE_PATTERNS):
        return "unavailable"
    return "generic"


class Downloader:
    """Fetches metadata and downloads audio or video with yt-dlp."""

    def __init__(self, config: Config, messages: Messages) -> None:
        """
        :param config: The active user configuration.
        :param messages: The active translation table.
        """
        self.config = config
        self.messages = messages
        self._ffmpeg_location = self._resolve_ffmpeg_location()

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_ffmpeg_location() -> Optional[str]:
        """Return the directory yt-dlp should look for FFmpeg in."""
        bundled = paths.bundled_ffmpeg_exe()
        if bundled.is_file():
            return str(bundled.parent)
        found = find_ffmpeg()
        return str(found.parent) if found else None

    def _base_options(self) -> Dict[str, Any]:
        """Return the yt-dlp options shared by metadata and download calls."""
        options: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": self.config.no_playlist,
            "logger": _YtDlpLogger(),
            "nocheckcertificate": False,
            "retries": 5,
            "fragment_retries": 5,
            "ignoreerrors": False,
            # Asking two player clients makes YouTube hand out the dubbed audio
            # tracks and the full format list far more reliably.
            "extractor_args": {"youtube": {"player_client": ["default", "web_embedded"]}},
        }
        runtime = _js_runtime()
        if runtime:
            # Signature decryption needs a JavaScript engine. yt-dlp defaults to
            # deno alone, so the engine that is actually installed has to be
            # named. The API wants {runtime: {config}}, not a list.
            options["js_runtimes"] = {runtime: {}}
        if self.config.user_agent.strip():
            options["http_headers"] = {"User-Agent": self.config.user_agent.strip()}
        if self._ffmpeg_location:
            options["ffmpeg_location"] = self._ffmpeg_location
        return options

    # ------------------------------------------------------------------
    def fetch_info(self, url: str) -> VideoInfo:
        """Load title and available audio tracks for ``url``.

        :param url: The YouTube URL.
        :return: The collected metadata.
        :raises MetadataError: When yt-dlp could not read the video.
        """
        youtube_dl = _import_yt_dlp()

        options = self._base_options()
        options["skip_download"] = True
        # Only title and track list are needed here; a failing format selection
        # must not hide the metadata - the download reports that error properly.
        options["ignore_no_formats_error"] = True

        log.debug("Fetching metadata for %s", url)
        try:
            with youtube_dl(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # yt-dlp raises many different error types
            raise MetadataError(str(exc)) from exc

        if not isinstance(info, dict):
            raise MetadataError("yt-dlp returned no usable metadata")
        entries = info.get("entries")
        if isinstance(entries, list) and entries:
            first = entries[0]
            if isinstance(first, dict):
                info = first

        raw_title = str(info.get("title") or self.messages["fallback_title"])
        title = _TITLE_SANITIZE_RE.sub(" ", raw_title).strip() or self.messages["fallback_title"]
        tracks = self._audio_tracks(info)
        log.info("Title: %s", title)
        if tracks:
            log.debug(
                "Available audio tracks: %s",
                ", ".join("{0}{1}".format(t.code, " (original)" if t.original else "") for t in tracks),
            )

        return VideoInfo(
            url=url,
            title=title,
            duration=info.get("duration") if isinstance(info.get("duration"), int) else None,
            uploader=str(info.get("uploader") or ""),
            audio_languages=[track.code for track in tracks],
            audio_tracks=tracks,
        )

    @staticmethod
    def _audio_tracks(info: Dict[str, Any]) -> List["AudioTrack"]:
        """Return the audio tracks of a video, the original one first.

        YouTube dubs are ordinary audio formats that differ only in their
        ``language``.  yt-dlp marks the track the video was published with by
        giving it the highest ``language_preference``, which is the only
        reliable way to tell an original track from a dub - so it is kept and
        used to sort, instead of listing the languages alphabetically.

        :param info: The raw metadata dictionary from yt-dlp.
        :return: One entry per language, original first, then alphabetical.
        """
        best: Dict[str, int] = {}
        for stream in info.get("formats") or []:
            if not isinstance(stream, dict):
                continue
            if stream.get("acodec") in (None, "none"):
                continue
            language = stream.get("language")
            if not isinstance(language, str):
                continue
            code = language.strip()
            if not code or code.lower() in ("none", "null", "na", "und"):
                continue
            preference = stream.get("language_preference")
            preference = preference if isinstance(preference, int) else -1
            if code not in best or preference > best[code]:
                best[code] = preference

        if not best:
            return []
        highest = max(best.values())
        # Only call a track "original" when it actually stands out; a video with
        # a single track or with no preference data has no dubs to distinguish.
        distinguishable = len(set(best.values())) > 1
        tracks = [
            AudioTrack(code=code, original=distinguishable and preference == highest)
            for code, preference in best.items()
        ]
        tracks.sort(key=lambda track: (not track.original, track.code))
        return tracks

    # ------------------------------------------------------------------
    def _format_selector(self, media_format: str, language: str) -> Dict[str, Any]:
        """Build the yt-dlp format string and post-processors.

        :param media_format: ``mp3`` or ``mp4``.
        :param language: Preferred audio language code, empty for "best".
        :return: The options fragment to merge into the download options.
        """
        language_filter = "[language^={0}]".format(language) if language else ""

        if media_format == "mp3":
            preferred = ["ba{0}".format(language_filter)] if language_filter else []
            return {
                "format": "/".join(preferred + ["ba", "best"]),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "0",
                    }
                ],
            }

        # Each entry is tried in order; the language specific ones come first.
        preferred = []
        if language_filter:
            preferred = [
                "bv*[ext=mp4]+ba{0}[ext=m4a]".format(language_filter),
                "bv*+ba{0}".format(language_filter),
            ]
        return {
            "format": "/".join(preferred + ["bv*[ext=mp4]+ba[ext=m4a]", "b[ext=mp4]", "bv*+ba", "b"]),
            "merge_output_format": "mp4",
        }

    def download(
        self,
        url: str,
        media_format: str,
        language: str = "",
        on_progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Optional[Path]:
        """Download ``url`` as MP3 or MP4.

        :param url: The YouTube URL.
        :param media_format: ``mp3`` or ``mp4``.
        :param language: Preferred audio language code, empty for "best".
        :param on_progress: Callback that receives :class:`Progress` updates.
        :param cancel_event: Set this event to abort the running download.
        :return: The path of the finished file, if yt-dlp reported one.
        :raises DownloadCanceled: When ``cancel_event`` was set.
        :raises DownloadFailed: When yt-dlp reported an error.
        """
        try:
            youtube_dl = _import_yt_dlp()
        except MetadataError as exc:
            raise DownloadFailed(str(exc), "generic") from exc

        target_dir = self.config.resolved_download_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        produced: Dict[str, Optional[str]] = {"path": None}
        canceled = threading.Event()

        def emit(progress: Progress) -> None:
            """Forward a progress update to the GUI callback."""
            if on_progress is not None:
                try:
                    on_progress(progress)
                except Exception:  # pragma: no cover - never break a download on UI errors
                    log.debug("Progress callback failed", exc_info=True)

        def check_cancel() -> None:
            """Abort the download when the caller requested cancellation."""
            if cancel_event is not None and cancel_event.is_set():
                canceled.set()
                raise DownloadCanceled()

        def progress_hook(status: Dict[str, Any]) -> None:
            """Translate a yt-dlp progress dict into a :class:`Progress`."""
            check_cancel()
            state = status.get("status")
            if state == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                done = status.get("downloaded_bytes") or 0
                percent: Optional[float] = None
                if isinstance(total, (int, float)) and total > 0:
                    percent = max(0.0, min(99.0, done * 100.0 / float(total)))
                emit(Progress(phase="downloading", percent=percent, detail=_speed_text(status)))
            elif state == "finished":
                filename = status.get("filename")
                if isinstance(filename, str):
                    produced["path"] = filename
                emit(Progress(phase="postprocessing", percent=None))

        def postprocessor_hook(status: Dict[str, Any]) -> None:
            """Report post-processing phases (conversion / merging)."""
            check_cancel()
            name = str(status.get("postprocessor") or "")
            state = status.get("status")
            info = status.get("info_dict")
            if isinstance(info, dict) and isinstance(info.get("filepath"), str):
                produced["path"] = info["filepath"]
            if state != "started":
                return
            if "ExtractAudio" in name or "VideoConvertor" in name:
                emit(Progress(phase="converting", percent=None))
            elif "Merger" in name:
                emit(Progress(phase="merging", percent=None))
            elif name not in ("MoveFiles", "MoveFilesAfterDownload"):
                emit(Progress(phase="postprocessing", percent=None))

        options = self._base_options()
        options.update(self._format_selector(media_format, language))
        options.update(
            {
                "paths": {"home": str(target_dir)},
                "outtmpl": self.config.output_template,
                "restrictfilenames": self.config.restrict_filenames,
                "windowsfilenames": paths.IS_WINDOWS,
                "overwrites": False,
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [postprocessor_hook],
            }
        )

        log.info("Starting download: format=%s language=%s target=%s", media_format, language or "best", target_dir)
        emit(Progress(phase="preparing", percent=0.0))

        try:
            with youtube_dl(options) as ydl:
                ydl.download([url])
        except DownloadCanceled:
            log.warning("Download canceled by the user.")
            raise
        except Exception as exc:
            if canceled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                log.warning("Download canceled by the user.")
                raise DownloadCanceled() from exc
            message = str(exc)
            kind = classify_error(message)
            log.error("Download failed (%s): %s", kind, message)
            raise DownloadFailed(message, kind) from exc

        emit(Progress(phase="finished", percent=100.0))
        result = produced["path"]
        if result:
            log.info("Download finished: %s", result)
            return Path(result)
        log.info("Download finished.")
        return None


def _speed_text(status: Dict[str, Any]) -> str:
    """Return a short "speed - ETA" string for the progress window.

    :param status: A yt-dlp progress dictionary.
    :return: For example ``3.4 MiB/s - 00:42``.
    """
    parts: List[str] = []
    speed = status.get("speed")
    if isinstance(speed, (int, float)) and speed > 0:
        parts.append("{0:.1f} MiB/s".format(speed / 1048576))
    eta = status.get("eta")
    if isinstance(eta, (int, float)) and eta > 0:
        parts.append("{0:02d}:{1:02d}".format(int(eta) // 60, int(eta) % 60))
    return " - ".join(parts)
