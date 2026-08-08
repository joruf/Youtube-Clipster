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
from .clip import ClipRange
from .clip import output_template as clip_output_template
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
    "cookies-from-browser",
    "http error 429",
    "too many requests",
    "has blocked",
    "blocked your",
    "rate-limit",
    "rate limit",
)

#: Drop yt-dlp wiki / support URLs from UI details (full text stays in the log).
_URL_IN_ERROR_RE = re.compile(r"https?://\S+", re.IGNORECASE)

#: Only unambiguous wordings belong here - a wrong "the disk is full" is worse
#: than the generic message, so a bare "write error" is deliberately absent.
_DISK_FULL_PATTERNS = (
    "no space left on device",
    "errno 28",
    "not enough space",
    "disk full",
    "disk quota exceeded",
)

_UNAVAILABLE_PATTERNS = (
    "video unavailable",
    "private video",
    "this video is not available",
    "removed by the uploader",
    "not available in your country",
    "members-only",
)

#: YouTube handed out a media URL and then refused to serve it.  This is not a
#: bot check and not a missing video: the metadata was read fine, only the
#: transfer of that one stream was rejected.
_FORBIDDEN_PATTERNS = (
    "http error 403",
    "403: forbidden",
    "403 forbidden",
)

#: Audio preference the Streaming relay already proves fetchable.  YouTube signs
#: the WebM/Opus stream through a player response whose media URLs the default
#: clients cannot always redeem, while the m4a one is handed out plainly - which
#: is exactly why a track can play in the Streaming tab and still fail to
#: download.  Retried first because it changes the least.
_M4A_FIRST = "ba[ext=m4a]/ba[acodec^=mp4a]"

#: Player clients tried after a 403.  Each one gets its own set of signed media
#: URLs, so a stream the default client cannot fetch often comes back usable
#: from one of these.  Ordered by how rarely they need extra proof.
_RETRY_PLAYER_CLIENTS = ("tv", "ios", "web_safari")

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
            "yt-dlp is not installed. Run 'python3 run.py --update' "
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
    #: Rough download size in bytes as reported by yt-dlp, ``0`` when unknown.
    filesize: int = 0

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
    :return: ``diskfull``, ``bot``, ``unavailable``, ``forbidden`` or ``generic``.
    """
    lowered = (message or "").lower()
    if any(pattern in lowered for pattern in _DISK_FULL_PATTERNS):
        return "diskfull"
    if any(pattern in lowered for pattern in _BOT_PATTERNS):
        return "bot"
    if any(pattern in lowered for pattern in _UNAVAILABLE_PATTERNS):
        return "unavailable"
    if any(pattern in lowered for pattern in _FORBIDDEN_PATTERNS):
        return "forbidden"
    return "generic"


def cookies_are_configured(config: Config) -> bool:
    """Return ``True`` when cookies would actually be passed to yt-dlp.

    Requires both risk acknowledgement and a browser or cookies.txt path.

    :param config: Active user configuration.
    :return: Whether yt-dlp would receive cookie options.
    """
    if not config.cookies_risk_acknowledged:
        return False
    return bool(config.cookies_from_browser.strip() or config.cookies_file.strip())


def sanitize_error_detail(message: str, limit: int = 200) -> str:
    """Return a compact single-line detail for the UI (no URLs).

    :param message: Raw yt-dlp or exception text.
    :param limit: Maximum characters after cleanup.
    :return: Short detail suitable for a status line.
    """
    text = _URL_IN_ERROR_RE.sub("", message or "")
    text = " ".join(text.split()).strip(" :-")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def user_facing_ytdlp_error(
    message: str,
    messages: Messages,
    *,
    cookies_configured: bool = False,
    context: str = "download",
) -> str:
    """Map a raw yt-dlp error to a short localized UI string.

    Bot / cookie failures never dump wiki URLs into the status line; the full
    text belongs in the log only.

    :param message: Raw error text (or a short sentinel such as ``no_player``).
    :param messages: Active translation table.
    :param cookies_configured: Whether cookies were already set in Settings.
    :param context: ``download``, ``playback``, ``discover``, or ``metadata``.
    :return: Localized message for the user.
    """
    kind = classify_error(message)
    if kind == "bot":
        if context == "playback":
            key = (
                "discover_playback_bot_with_cookies"
                if cookies_configured
                else "discover_playback_bot"
            )
        elif context == "discover":
            key = (
                "discover_blocked_with_cookies"
                if cookies_configured
                else "discover_blocked"
            )
        else:
            key = "error_bot_with_cookies" if cookies_configured else "error_bot_detected"
        return messages[key]
    if kind == "unavailable":
        return messages["error_unavailable"]
    if kind == "forbidden":
        return messages["error_forbidden"]
    if kind == "diskfull":
        return messages.format("error_disk_full", details=sanitize_error_detail(message))
    detail = sanitize_error_detail(message) or "?"
    if context == "playback":
        return messages.format("discover_playback_failed_detail", details=detail)
    if context == "discover":
        return messages.format("discover_failed", details=detail)
    if context == "metadata":
        return messages.format("error_metadata", details=detail)
    return messages.format("error_generic", details=detail)


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
        if self.config.cookies_risk_acknowledged:
            browser = self.config.cookies_from_browser.strip().lower()
            if browser:
                # yt-dlp expects a tuple; never log cookie values — only the browser name.
                options["cookiesfrombrowser"] = (browser,)
                log.debug("yt-dlp cookiesfrombrowser=%s", browser)
            cookie_path = self.config.cookies_file.strip()
            if cookie_path:
                options["cookiefile"] = str(Path(cookie_path).expanduser())
                log.debug("yt-dlp cookiefile configured (path only, contents not logged)")
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
            filesize=_estimated_size(info),
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

    @staticmethod
    def _section_options(section: ClipRange) -> Dict[str, Any]:
        """Build the yt-dlp options that cut one section out of the video.

        ``download_ranges`` is documented as a callback that is asked for the
        sections of a video, so a plain function is handed over instead of
        yt-dlp's own ``download_range_func`` helper - one internal less that an
        update could move.

        :param section: The piece the user asked for.
        :return: The options fragment to merge into the download options.
        """
        chapter: Dict[str, Any] = {"start_time": section.start}
        if section.end is not None:
            chapter["end_time"] = section.end
        return {
            "download_ranges": lambda info, ydl: [dict(chapter)],
            # Cut where the user asked instead of at the nearest keyframe, which
            # is the whole point of naming a second in the first place.
            "force_keyframes_at_cuts": True,
        }

    def _forbidden_retries(self, media_format: str, language: str) -> List[Dict[str, Any]]:
        """Return option patches to try after YouTube answered 403.

        The order matters: change the format first, because that is the known
        difference between a track that plays and the same track failing to
        download.  Only then ask a different player client, which costs another
        round trip to YouTube.

        :param media_format: ``mp3`` or ``mp4``.
        :param language: Preferred audio language code, empty for "best".
        :return: One options fragment per attempt, in the order to try them.
        """
        language_filter = "[language^={0}]".format(language) if language else ""
        variants: List[Dict[str, Any]] = []

        if media_format == "mp3":
            preferred = (
                ["ba{0}[ext=m4a]".format(language_filter)] if language_filter else []
            )
            audio = "/".join(preferred + [_M4A_FIRST, "ba", "best"])
            variants.append({"format": audio})
        else:
            # Progressive first - one already muxed file, which is the same kind
            # of stream the Streaming tab plays and the one YouTube hands out
            # without the extra proof its split DASH formats now want.
            variants.append({"format": "b[ext=mp4]/b/bv*[ext=mp4]+ba[ext=m4a]/bv*+ba"})

        for client in _RETRY_PLAYER_CLIENTS:
            variants.append(
                {"extractor_args": {"youtube": {"player_client": [client]}}}
            )
        return variants

    def download(
        self,
        url: str,
        media_format: str,
        language: str = "",
        on_progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        duration: Optional[int] = None,
        estimated_size: int = 0,
        section: Optional[ClipRange] = None,
    ) -> Optional[Path]:
        """Download ``url`` as MP3 or MP4, or only one section of it.

        :param url: The YouTube URL.
        :param media_format: ``mp3`` or ``mp4``.
        :param language: Preferred audio language code, empty for "best".
        :param on_progress: Callback that receives :class:`Progress` updates.
        :param cancel_event: Set this event to abort the running download.
        :param duration: Video length in seconds; enables a real percentage
            while ffmpeg converts or merges.
        :param estimated_size: Expected download size in bytes; used to refuse
            the download up front when the disk cannot hold it.
        :param section: Cut this piece out instead of taking the whole video.
        :return: The path of the finished file, if yt-dlp reported one.
        :raises DownloadCanceled: When ``cancel_event`` was set.
        :raises DownloadFailed: When yt-dlp reported an error.
        """
        try:
            youtube_dl = _import_yt_dlp()
        except MetadataError as exc:
            raise DownloadFailed(str(exc), "generic") from exc

        if section is not None:
            # From here on the section is the file: ffmpeg reports its progress
            # against that length, and only that part has to fit on the disk.
            length = section.length
            if length and duration and duration > 0:
                estimated_size = int(estimated_size * min(1.0, length / duration))
            if length:
                duration = int(length)

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

        watcher = _FfmpegProgressWatcher(
            path=target_dir / ".youtube-clipster-progress.txt", duration=duration, emit=emit
        )

        def postprocessor_hook(status: Dict[str, Any]) -> None:
            """Report post-processing phases (conversion / merging)."""
            check_cancel()
            name = str(status.get("postprocessor") or "")
            state = status.get("status")
            info = status.get("info_dict")
            if isinstance(info, dict) and isinstance(info.get("filepath"), str):
                produced["path"] = info["filepath"]

            if "ExtractAudio" in name or "VideoConvertor" in name:
                phase = "converting"
            elif "Merger" in name:
                phase = "merging"
            elif name in ("MoveFiles", "MoveFilesAfterDownload"):
                return
            else:
                phase = "postprocessing"

            if state == "started":
                emit(Progress(phase=phase, percent=None))
                if phase in ("converting", "merging"):
                    # ffmpeg now starts writing to the progress file.
                    watcher.start(phase)
            else:
                watcher.stop()

        options = self._base_options()
        options.update(self._format_selector(media_format, language))
        template = self.config.output_template
        if section is not None:
            options.update(self._section_options(section))
            template = clip_output_template(template, section)
        options.update(
            {
                "paths": {"home": str(target_dir)},
                "outtmpl": template,
                "restrictfilenames": self.config.restrict_filenames,
                "windowsfilenames": paths.IS_WINDOWS,
                "overwrites": False,
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [postprocessor_hook],
                # Every ffmpeg post-processor reports its position to this file.
                "postprocessor_args": {"ffmpeg": watcher.ffmpeg_args()},
            }
        )

        # A download writes the source and then the converted file, so roughly
        # twice the estimated size has to fit. Failing here beats downloading
        # eighty megabytes and only then running out of room during conversion.
        if estimated_size > 0:
            available = free_space(target_dir)
            needed = int(estimated_size * 2.2)
            if 0 < available < needed:
                message = (
                    "Not enough space in {0}: about {1} needed, {2} free "
                    "(No space left on device)"
                ).format(target_dir, _bytes_text(needed), _bytes_text(available))
                log.error("%s", message)
                raise DownloadFailed(message, "diskfull")

        log.info(
            "Starting download: format=%s language=%s section=%s target=%s",
            media_format,
            language or "best",
            section.label() if section is not None else "whole video",
            target_dir,
        )
        emit(Progress(phase="preparing", percent=0.0))

        # The first attempt is what the caller asked for; the rest only ever run
        # after a 403 and are dropped the moment anything else goes wrong.
        attempts = [options] + [
            _merged_options(options, patch)
            for patch in self._forbidden_retries(media_format, language)
        ]
        try:
            for number, attempt in enumerate(attempts):
                try:
                    with youtube_dl(attempt) as ydl:
                        ydl.download([url])
                    if number:
                        log.info("Retry %s succeeded after a 403.", number)
                    break
                except DownloadCanceled:
                    log.warning("Download canceled by the user.")
                    raise
                except Exception as exc:
                    if canceled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                        log.warning("Download canceled by the user.")
                        raise DownloadCanceled() from exc
                    message = str(exc)
                    kind = classify_error(message)
                    last = number == len(attempts) - 1
                    if kind == "forbidden" and not last:
                        log.warning(
                            "YouTube refused the stream (403); retrying with %s.",
                            _describe_patch(attempts[number + 1], options),
                        )
                        _discard(produced["path"])
                        produced["path"] = None
                        emit(Progress(phase="preparing", percent=0.0))
                        continue
                    log.error("Download failed (%s): %s", kind, message)
                    _discard(produced["path"])
                    raise DownloadFailed(message, kind) from exc
        finally:
            watcher.cleanup()

        emit(Progress(phase="finished", percent=100.0))
        result = produced["path"]
        if result:
            log.info("Download finished: %s", result)
            return Path(result)
        log.info("Download finished.")
        return None


class _FfmpegProgressWatcher:
    """Turns ffmpeg's ``-progress`` output into percentages.

    yt-dlp offers no progress for its post-processors: ``real_run_ffmpeg`` waits
    for ffmpeg to exit and only then returns its output.  ffmpeg itself can write
    machine readable progress to a file though, and that file is passed in via
    ``postprocessor_args`` - so this watcher just polls it.  No yt-dlp internals
    are touched, which keeps the whole thing safe across yt-dlp updates.
    """

    #: How often the progress file is read, in seconds.
    INTERVAL = 0.3

    def __init__(self, path: Path, duration: Optional[int], emit: ProgressCallback) -> None:
        """
        :param path: File ffmpeg writes its progress to.
        :param duration: Video length in seconds, used as the 100 % mark.
        :param emit: Callback that receives the :class:`Progress` updates.
        """
        self.path = path
        self.duration = duration if isinstance(duration, int) and duration > 0 else 0
        self._emit = emit
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._phase = "converting"

    def start(self, phase: str) -> None:
        """Begin watching for one post-processor run.

        :param phase: ``converting`` or ``merging``, forwarded in the updates.
        :return: None
        """
        self.stop()
        self._phase = phase
        self._stop = threading.Event()
        # Each post-processor appends to the same file, so start from scratch to
        # keep the "last value wins" parsing correct.
        try:
            self.path.write_text("", encoding="utf-8")
        except OSError:
            pass
        self._thread = threading.Thread(target=self._run, name="clipster-ffmpeg-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop watching and forget the thread."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def cleanup(self) -> None:
        """Stop watching and remove the progress file."""
        self.stop()
        try:
            self.path.unlink()
        except OSError:
            pass

    def ffmpeg_args(self) -> List[str]:
        """Return the ffmpeg arguments that enable the progress output."""
        return ["-progress", str(self.path), "-nostats"]

    def _run(self) -> None:
        """Poll the progress file until ffmpeg is done."""
        while not self._stop.wait(self.INTERVAL):
            seconds = self._read_position()
            if seconds is None:
                continue
            percent: Optional[float] = None
            if self.duration:
                percent = max(0.0, min(99.0, seconds * 100.0 / self.duration))
            self._safe_emit(Progress(phase=self._phase, percent=percent, detail=self._detail(seconds)))

    def _detail(self, seconds: float) -> str:
        """Return an ``M:SS / M:SS`` line so the remaining work is visible.

        :param seconds: Media position ffmpeg has reached.
        :return: The detail line, or just the position when the length is unknown.
        """
        if self.duration:
            return "{0} / {1}".format(_clock(seconds), _clock(self.duration))
        return _clock(seconds)

    def _read_position(self) -> Optional[float]:
        """Return the media position ffmpeg last reported, in seconds.

        :return: The position, or ``None`` while ffmpeg has not reported one.
        """
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # ffmpeg keeps appending blocks, so the last usable value is the current
        # one. Note that out_time_ms actually holds microseconds.
        for line in reversed(text.splitlines()):
            key, _, value = line.partition("=")
            if key not in ("out_time_us", "out_time_ms"):
                continue
            value = value.strip()
            if not value or value == "N/A":
                continue
            try:
                return int(value) / 1_000_000.0
            except ValueError:
                continue
        return None

    def _safe_emit(self, progress: Progress) -> None:
        """Forward an update without ever letting a UI error stop ffmpeg."""
        try:
            self._emit(progress)
        except Exception:  # pragma: no cover - defensive
            log.debug("Conversion progress callback failed", exc_info=True)


def _estimated_size(info: Dict[str, Any]) -> int:
    """Return yt-dlp's size estimate for a video in bytes.

    :param info: The raw metadata dictionary from yt-dlp.
    :return: The size, or ``0`` when yt-dlp does not know it.
    """
    for key in ("filesize", "filesize_approx"):
        value = info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    # Fall back to the largest single format, which is what a merge would pull.
    largest = 0
    for stream in info.get("formats") or []:
        if not isinstance(stream, dict):
            continue
        for key in ("filesize", "filesize_approx"):
            value = stream.get(key)
            if isinstance(value, (int, float)) and value > largest:
                largest = int(value)
    return largest


def free_space(target: Path) -> int:
    """Return the free bytes on the file system holding ``target``.

    :param target: A directory on the file system to measure.
    :return: Free bytes, or ``0`` when it cannot be determined.
    """
    try:
        return shutil.disk_usage(str(target)).free
    except OSError:
        return 0


def _clock(seconds: float) -> str:
    """Return ``M:SS`` or ``H:MM:SS`` for a number of seconds.

    :param seconds: The amount of time.
    :return: The formatted value.
    """
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{0}:{1:02d}:{2:02d}".format(hours, minutes, secs)
    return "{0}:{1:02d}".format(minutes, secs)


def _merged_options(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``base`` with ``patch`` applied, without touching either.

    ``extractor_args`` is a nested mapping, so replacing it wholesale would drop
    every setting the patch does not mention; it is merged one level deeper.

    :param base: The options the download started with.
    :param patch: What one retry wants to change.
    :return: A new options dictionary.
    """
    merged = dict(base)
    for key, value in patch.items():
        if key != "extractor_args" or not isinstance(value, dict):
            merged[key] = value
            continue
        nested = {
            name: dict(args) if isinstance(args, dict) else args
            for name, args in (merged.get(key) or {}).items()
        }
        for name, args in value.items():
            current = dict(nested.get(name) or {})
            current.update(args)
            nested[name] = current
        merged[key] = nested
    return merged


def _describe_patch(attempt: Dict[str, Any], base: Dict[str, Any]) -> str:
    """Return a log-friendly summary of how ``attempt`` differs from ``base``.

    :param attempt: The options of the retry about to run.
    :param base: The options of the first attempt.
    :return: A short phrase such as ``player client tv``.
    """
    if attempt.get("format") != base.get("format"):
        return "format {0}".format(attempt.get("format"))
    clients = ((attempt.get("extractor_args") or {}).get("youtube") or {}).get("player_client")
    if clients:
        return "player client {0}".format(", ".join(str(item) for item in clients))
    return "unchanged options"


def _discard(path: Optional[str]) -> None:
    """Delete a half-finished intermediate file after a failed download.

    yt-dlp removes the downloaded source once the conversion succeeded; when it
    fails the source stays behind and can easily be a hundred megabytes.

    :param path: The file yt-dlp reported, or ``None``.
    :return: None
    """
    if not path:
        return
    target = Path(path)
    try:
        if target.is_file():
            size = target.stat().st_size
            target.unlink()
            log.info("Removed the leftover source file %s (%s).", target.name, _bytes_text(size))
    except OSError as exc:
        log.debug("Could not remove %s: %s", target, exc)


def _bytes_text(size: int) -> str:
    """Return a compact human readable byte count.

    :param size: The amount in bytes.
    :return: For example ``143.2 MB``.
    """
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return "{0:.0f} {1}".format(value, unit) if unit == "B" else "{0:.1f} {1}".format(value, unit)
        value /= 1024.0
    return "{0:.1f} GB".format(value)


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
