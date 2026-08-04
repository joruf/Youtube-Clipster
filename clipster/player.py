"""In-tab Streaming playback for Discover.

Resolves a media URL with yt-dlp and plays it inside Clipster: video is
embedded with mpv (``--wid``) when available; otherwise audio plays with no
separate window (mpv ``--no-video`` or ffplay ``-nodisp``).  Windowed ffplay
embedding is avoided because it often opens an extra OS window.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import paths
from .discover import DiscoverTrack
from .downloader import _import_yt_dlp
from .logging_setup import get_logger
from .shortcuts import _no_window
from .spectrum import (
    EQ_BAR_COUNT,
    SAMPLE_RATE,
    SPECTRUM_INTERVAL_SEC,
    WAVEFORM_SAMPLES,
    WINDOW_SAMPLES,
    band_levels_from_pcm,
    pcm_s16le_mono_to_floats,
)
from .visualizer import normalize_visualizer, peak_of_samples, rms_of_samples, visualizer_needs_pcm

log = get_logger(__name__)

BACKEND_NONE = ""
BACKEND_MPV = "mpv"
BACKEND_FFPLAY = "ffplay"
BACKEND_AUDIO = "audio"

#: Drop a prefetched URL after this many seconds (YouTube links expire).
_CACHE_MAX_AGE_SEC = 25 * 60


def _find_player(name: str) -> Optional[str]:
    """Return an absolute path to ``mpv`` / ``ffplay``, including the bundled FFmpeg bin.

    Windows installs put ``ffplay.exe`` next to the privately downloaded ffmpeg;
    ``shutil.which`` alone would miss it when that folder is not on ``PATH``.

    :param name: ``mpv`` or ``ffplay`` (without extension).
    :return: Executable path, or ``None``.
    """
    bundled = {
        "ffplay": paths.bundled_ffplay_exe(),
        "mpv": paths.bundled_mpv_exe(),
    }.get(name)
    if bundled is not None and bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    if paths.IS_WINDOWS:
        found = shutil.which("{0}.exe".format(name))
        if found:
            return found
    return None


def _find_ffmpeg() -> Optional[str]:
    """Return an absolute path to ``ffmpeg``, including the bundled bin."""
    bundled = paths.bundled_ffmpeg_exe()
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    if paths.IS_WINDOWS:
        found = shutil.which("ffmpeg.exe")
        if found:
            return found
    return None


def video_embed_available() -> bool:
    """Return ``True`` when in-tab video embedding can be attempted.

    Real embedding needs ``mpv --wid``.  ffplay's ``SDL_WINDOWID`` often opens a
    separate OS window instead of painting into Clipster, so it does not count.
    """
    return bool(_find_player("mpv"))


def _popen(
    cmd: List[str],
    *,
    env: Optional[Dict[str, str]] = None,
    stdin: Any = None,
    stdout: Any = None,
) -> subprocess.Popen:
    """Start a player subprocess without flashing a console on Windows."""
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.DEVNULL if stdout is None else stdout,
        "stderr": subprocess.DEVNULL,
    }
    kwargs.update(_no_window())
    if env is not None:
        kwargs["env"] = env
    if stdin is not None:
        kwargs["stdin"] = stdin
    # Unbuffered pipes keep binary media flowing on Windows (default block
    # buffering can stall the stream feeder until the buffer fills).
    if kwargs.get("stdin") is subprocess.PIPE or kwargs.get("stdout") is subprocess.PIPE:
        kwargs["bufsize"] = 0
    return subprocess.Popen(cmd, **kwargs)


def format_stream_rate(bps: float) -> str:
    """Return a short ``KB/s`` label for a byte rate.

    :param bps: Bytes per second.
    :return: Display text such as ``128 KB/s``.
    """
    if bps <= 0:
        return "— KB/s"
    kb = bps / 1000.0
    if kb >= 100:
        return "{0:.0f} KB/s".format(kb)
    if kb >= 10:
        return "{0:.1f} KB/s".format(kb)
    return "{0:.1f} KB/s".format(kb)


def watch_url(track: DiscoverTrack) -> str:
    """Return the normal watch URL for ``track``."""
    return track.url or "https://www.youtube.com/watch?v={0}".format(track.video_id)


@dataclass
class PlayStartResult:
    """Outcome of starting one Discover track."""

    track: Optional[DiscoverTrack] = None
    backend: str = BACKEND_NONE
    error: str = ""


@dataclass
class _CachedStream:
    """One prefetched direct media URL."""

    url: str
    prefer_video: bool
    resolved_at: float


#: Format for a stream a phone browser has to play. m4a first, because Safari
#: plays AAC but not the Opus-in-WebM that "bestaudio" usually picks.
BROWSER_AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"


def resolve_stream_url(
    page_url: str,
    base_options: Optional[Dict[str, Any]] = None,
    *,
    prefer_video: bool = True,
    format_selector: str = "",
) -> str:
    """Return a direct media URL for ``page_url`` via yt-dlp.

    :param page_url: Canonical YouTube watch URL.
    :param base_options: Optional yt-dlp options from the downloader.
    :param prefer_video: Prefer a progressive A+V stream for the embedded panel.
    :param format_selector: Overrides the format entirely, for callers that need
        a specific container - see :data:`BROWSER_AUDIO_FORMAT`.
    :return: A playable HTTP(S) media URL.
    :raises RuntimeError: When no stream URL can be resolved.
    """
    youtube_dl = _import_yt_dlp()
    opts = dict(base_options or {})
    opts["quiet"] = True
    opts["no_warnings"] = True
    opts["skip_download"] = True
    opts["noplaylist"] = True
    if format_selector:
        opts["format"] = format_selector
    elif prefer_video:
        # Progressive file works best for simple players; otherwise bestaudio.
        opts["format"] = "best[height<=720][protocol^=http]/bestaudio/best"
    else:
        opts["format"] = "bestaudio/best"
    with youtube_dl(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)
    if not isinstance(info, dict):
        raise RuntimeError("No stream metadata")
    direct = info.get("url")
    if isinstance(direct, str) and direct.startswith("http"):
        return direct
    requested = info.get("requested_formats")
    if isinstance(requested, list):
        for item in requested:
            if isinstance(item, dict):
                candidate = item.get("url")
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate
    formats = info.get("formats")
    if isinstance(formats, list):
        for item in reversed(formats):
            if not isinstance(item, dict):
                continue
            candidate = item.get("url")
            if isinstance(candidate, str) and candidate.startswith("http"):
                return candidate
    raise RuntimeError("No playable stream URL")


class DiscoverPlayer:
    """Playlist + in-tab playback for the Streaming page."""

    def __init__(self) -> None:
        self._tracks: List[DiscoverTrack] = []
        self._index = -1
        self._playing = False
        self._backend = BACKEND_NONE
        self._started_at = 0.0
        self._seek_base = 0.0
        self._duration = 0.0
        self._stream_url: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._options_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._generation = 0
        self._lock = threading.Lock()
        self._embed_wid: Optional[int] = None
        self._prefer_video = False
        self._stream_cache: Dict[str, _CachedStream] = {}
        self._prefetch_token = 0
        self._feeder_stop = threading.Event()
        self._rate_lock = threading.Lock()
        self._rate_bps = 0.0
        self._ipc_path: Optional[str] = None
        self._viz_mode = "waveform"
        self._pcm_wanted = visualizer_needs_pcm(self._viz_mode)
        self._pcm_stop = threading.Event()
        self._pcm_lock = threading.Lock()
        self._pcm_analyser: Optional[subprocess.Popen] = None
        self._spectrum_levels: List[float] = [0.0] * EQ_BAR_COUNT
        self._waveform: List[float] = [0.0] * WAVEFORM_SAMPLES
        self._rms_level = 0.0
        self._peak_level = 0.0
        self._energy_level = 0.0
        self._viz_mpv: Optional[subprocess.Popen] = None

    def set_options_provider(self, provider: Callable[[], Dict[str, Any]]) -> None:
        """Install a callback that returns live yt-dlp options."""
        self._options_provider = provider

    @property
    def tracks(self) -> List[DiscoverTrack]:
        """Return the current playlist."""
        return list(self._tracks)

    @property
    def index(self) -> int:
        """Return the current track index, or ``-1`` when idle."""
        return self._index

    @property
    def current(self) -> Optional[DiscoverTrack]:
        """Return the current track, or ``None``."""
        if 0 <= self._index < len(self._tracks):
            return self._tracks[self._index]
        return None

    @property
    def playing(self) -> bool:
        """Return ``True`` while playback was started and not paused."""
        return self._playing

    @property
    def backend(self) -> str:
        """Return the last playback backend name."""
        return self._backend

    @property
    def process_running(self) -> bool:
        """Return ``True`` when the player process is still alive."""
        return self._process is not None and self._process.poll() is None

    def set_playlist(self, tracks: List[DiscoverTrack]) -> None:
        """Replace the playlist and stop current playback."""
        self.stop()
        self._tracks = list(tracks)
        self._index = 0 if self._tracks else -1
        self.clear_stream_cache()

    def append_tracks(self, tracks: List[DiscoverTrack]) -> int:
        """Append tracks to the playlist without stopping playback.

        :param tracks: New tracks to add.
        :return: How many tracks were appended.
        """
        before = len(self._tracks)
        existing = {track.video_id for track in self._tracks if track.video_id}
        for track in tracks:
            if track.video_id and track.video_id in existing:
                continue
            self._tracks.append(track)
            if track.video_id:
                existing.add(track.video_id)
        if self._index < 0 and self._tracks:
            self._index = 0
        return len(self._tracks) - before

    def clear_stream_cache(self) -> None:
        """Drop every prefetched stream URL."""
        with self._lock:
            self._prefetch_token += 1
            self._stream_cache.clear()

    def cached_stream(self, video_id: str) -> Optional[str]:
        """Return a still-valid cached stream URL for ``video_id``, if any."""
        if not video_id:
            return None
        with self._lock:
            entry = self._stream_cache.get(video_id)
            if entry is None:
                return None
            if time.monotonic() - entry.resolved_at > _CACHE_MAX_AGE_SEC:
                self._stream_cache.pop(video_id, None)
                return None
            return entry.url

    def prefetch(self, index: int, *, prefer_video: bool = True) -> None:
        """Resolve the stream URL for playlist ``index`` in the background.

        :param index: Playlist index to warm up.
        :param prefer_video: Match the format choice used for playback.
        """
        if index < 0 or index >= len(self._tracks):
            return
        track = self._tracks[index]
        with self._lock:
            if track.video_id:
                entry = self._stream_cache.get(track.video_id)
                if (
                    entry is not None
                    and entry.prefer_video == prefer_video
                    and time.monotonic() - entry.resolved_at <= _CACHE_MAX_AGE_SEC
                ):
                    return
            self._prefetch_token += 1
            token = self._prefetch_token
        opts = self._options()
        target = watch_url(track)
        title = track.title
        video_id = track.video_id

        def worker() -> None:
            try:
                stream = resolve_stream_url(target, opts, prefer_video=prefer_video)
            except Exception as exc:
                log.debug("Prefetch failed for '%s': %s", title, exc)
                return
            with self._lock:
                if token != self._prefetch_token:
                    return
                key = video_id or target
                self._stream_cache[key] = _CachedStream(
                    url=stream,
                    prefer_video=prefer_video,
                    resolved_at=time.monotonic(),
                )
            log.info("Prefetched stream for '%s'", title)

        threading.Thread(target=worker, name="clipster-stream-prefetch", daemon=True).start()

    def play(
        self,
        index: Optional[int] = None,
        *,
        options: Optional[Dict[str, Any]] = None,
        embed_wid: Optional[int] = None,
        prefer_video: bool = False,
    ) -> PlayStartResult:
        """Start embedded playback of ``index`` or the current track.

        :param index: Optional playlist index.
        :param options: yt-dlp options for stream resolution.
        :param embed_wid: Tk ``winfo_id()`` of the in-tab video host.
        :param prefer_video: Resolve a video stream and try in-tab embedding.
        :return: Start result with backend / error.
        """
        if index is not None:
            if index < 0 or index >= len(self._tracks):
                return PlayStartResult(error="invalid index")
            self._index = index
        track = self.current
        if track is None:
            return PlayStartResult(error="empty playlist")
        self.stop(clear_state=False)
        self._prefer_video = bool(prefer_video)
        self._embed_wid = embed_wid if self._prefer_video else None
        self._seek_base = 0.0
        track_duration = float(track.duration or 0)
        self._duration = track_duration
        generation = self._bump_generation()
        result = self._start_track(
            track,
            options or self._options(),
            embed_wid=self._embed_wid,
            prefer_video=self._prefer_video,
            start_at=0.0,
        )
        with self._lock:
            if generation != self._generation:
                self._kill_process()
                return PlayStartResult(track=track, error="canceled")
        if result.backend != BACKEND_NONE:
            self._playing = True
            self._started_at = time.monotonic()
            self._sync_pcm_analyser()
        return result

    def play_async(
        self,
        index: int,
        *,
        on_done: Callable[[PlayStartResult], None],
        options: Optional[Dict[str, Any]] = None,
        embed_wid: Optional[int] = None,
        prefer_video: bool = False,
    ) -> None:
        """Resolve and start embedded playback on a worker thread.

        :param index: Playlist index.
        :param on_done: Called with the start result (marshal to the UI thread).
        :param options: Optional yt-dlp options.
        :param embed_wid: Tk host window id for video embedding.
        :param prefer_video: Resolve a video stream and try in-tab embedding.
        """
        opts = dict(options or self._options())

        def worker() -> None:
            try:
                result = self.play(
                    index,
                    options=opts,
                    embed_wid=embed_wid,
                    prefer_video=prefer_video,
                )
            except Exception as exc:
                log.exception("Streaming playback failed")
                result = PlayStartResult(error=str(exc))
            on_done(result)

        threading.Thread(target=worker, name="clipster-streaming-play", daemon=True).start()

    def track_finished(self) -> bool:
        """Return ``True`` once when the player process ends naturally."""
        if not self._playing:
            return False
        if self._process is None:
            return False
        if self._process.poll() is None:
            return False
        code = self._process.returncode
        self._process = None
        self._playing = False
        self._backend = BACKEND_NONE
        self._stream_url = None
        self._stop_pcm_analyser()
        self._stop_viz_mpv()
        self._clear_pcm_levels()
        log.info("Streaming track finished (exit %s)", code)
        return True

    def position(self) -> float:
        """Return the estimated playback position in seconds."""
        if self._playing and self.process_running:
            elapsed = self._seek_base + (time.monotonic() - self._started_at)
            if self._duration > 0:
                return max(0.0, min(elapsed, self._duration))
            return max(0.0, elapsed)
        return max(0.0, self._seek_base)

    def duration(self) -> float:
        """Return the track length in seconds when known."""
        if self._duration > 0:
            return self._duration
        track = self.current
        return float(track.duration) if track and track.duration else 0.0

    def seek(self, seconds: float) -> bool:
        """Jump to ``seconds`` by restarting the current stream.

        :param seconds: Target position from the start of the track.
        :return: ``True`` when playback was restarted at that position.
        """
        if not self._stream_url or self.current is None:
            return False
        target = max(0.0, float(seconds))
        total = self.duration()
        if total > 0:
            target = min(target, max(0.0, total - 0.25))
        embed_wid = self._embed_wid
        stream = self._stream_url
        prefer_embed = self._prefer_video and bool(embed_wid) and self._backend in (
            BACKEND_MPV,
            BACKEND_FFPLAY,
        )
        self._kill_process()
        self._seek_base = target
        if prefer_embed and embed_wid:
            result = self._start_embedded(stream, embed_wid, start_at=target)
            if result.backend == BACKEND_NONE:
                result = self._start_audio_only(stream, start_at=target)
        else:
            result = self._start_audio_only(stream, start_at=target)
        if result.backend == BACKEND_NONE:
            self._playing = False
            self._backend = BACKEND_NONE
            return False
        self._playing = True
        self._started_at = time.monotonic()
        self._stream_url = stream
        self._sync_pcm_analyser()
        return True

    def ensure_playing(self, *, grace_sec: float = 2.5) -> str:
        """Return the active backend.

        :param grace_sec: Kept for API compatibility.
        :return: Current backend while playing.
        """
        del grace_sec
        return self._backend if self._playing else BACKEND_NONE

    def stream_rate_bps(self) -> float:
        """Return the current stream download rate in bytes per second."""
        if not self._playing or not self.process_running:
            return 0.0
        ipc_rate = self._mpv_cache_speed()
        if ipc_rate is not None:
            return max(0.0, ipc_rate)
        with self._rate_lock:
            return max(0.0, float(self._rate_bps))

    def set_visualizer_mode(self, mode: str) -> None:
        """Remember the stage visualizer mode and sync the PCM analyser."""
        self._viz_mode = normalize_visualizer(mode)
        self._pcm_wanted = visualizer_needs_pcm(self._viz_mode)
        self._sync_pcm_analyser()
        if self._viz_mode != "visualizer":
            self._stop_viz_mpv()

    def spectrum_levels(self) -> List[float]:
        """Return the latest equalizer band levels in ``[0, 1]``."""
        with self._pcm_lock:
            return list(self._spectrum_levels)

    def waveform_samples(self) -> List[float]:
        """Return the latest mono waveform ring buffer (approx ``[-1, 1]``)."""
        with self._pcm_lock:
            return list(self._waveform)

    def rms_level(self) -> float:
        """Return the latest RMS loudness in ``[0, 1]``."""
        with self._pcm_lock:
            return float(self._rms_level)

    def peak_level(self) -> float:
        """Return the latest peak amplitude in ``[0, 1]``."""
        with self._pcm_lock:
            return float(self._peak_level)

    def energy_level(self) -> float:
        """Return smoothed broadband energy for the pulse ring (``[0, 1]``)."""
        with self._pcm_lock:
            return float(self._energy_level)

    def pcm_analysis_active(self) -> bool:
        """Return ``True`` when a live PCM analyser process is running."""
        proc = self._pcm_analyser
        return proc is not None and proc.poll() is None

    def can_seek(self) -> bool:
        """Return ``True`` when the current stream can jump to a new position."""
        return bool(self._stream_url) and self.current is not None

    def pause(self) -> None:
        """Stop playback but keep the current index, stream and position."""
        if self._playing and self.process_running:
            self._seek_base = self.position()
        self._kill_process()
        self._playing = False
        self._backend = BACKEND_NONE

    def next(self) -> Optional[DiscoverTrack]:
        """Move to the next track index without starting playback."""
        if not self._tracks:
            return None
        nxt = self._index + 1
        if nxt >= len(self._tracks):
            return None
        self._index = nxt
        return self.current

    def previous(self) -> Optional[DiscoverTrack]:
        """Move to the previous track index without starting playback."""
        if not self._tracks:
            return None
        prv = max(0, self._index - 1)
        self._index = prv
        return self.current

    def stop(self, clear_state: bool = True) -> None:
        """Terminate the player process.

        :param clear_state: When ``True``, also mark playback as stopped.
        :return: None
        """
        self._bump_generation()
        self._kill_process()
        if clear_state:
            self._playing = False
            self._backend = BACKEND_NONE
            self._stream_url = None
            self._seek_base = 0.0

    def _options(self) -> Dict[str, Any]:
        """Return yt-dlp options from the provider, or an empty dict."""
        if self._options_provider is None:
            return {}
        try:
            return dict(self._options_provider() or {})
        except Exception:
            log.debug("Streaming options provider failed", exc_info=True)
            return {}

    def _bump_generation(self) -> int:
        """Invalidate in-flight play starts and return the new generation id."""
        with self._lock:
            self._generation += 1
            return self._generation

    def _kill_process(self) -> None:
        """Terminate a running player process."""
        self._stop_feeder()
        self._stop_pcm_analyser()
        self._stop_viz_mpv()
        self._cleanup_ipc()
        if self._process is None:
            return
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.terminate()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except Exception:
            try:
                self._process.kill()
            except OSError:
                pass
        self._process = None
        with self._rate_lock:
            self._rate_bps = 0.0
        self._clear_pcm_levels()

    def _clear_pcm_levels(self) -> None:
        """Reset spectrum / waveform / loudness snapshots to silence."""
        with self._pcm_lock:
            self._spectrum_levels = [0.0] * EQ_BAR_COUNT
            self._waveform = [0.0] * WAVEFORM_SAMPLES
            self._rms_level = 0.0
            self._peak_level = 0.0
            self._energy_level = 0.0

    def _stop_pcm_analyser(self) -> None:
        """Stop the side-car ffmpeg PCM analyser, if any."""
        self._pcm_stop.set()
        proc = self._pcm_analyser
        self._pcm_analyser = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass

    def _stop_viz_mpv(self) -> None:
        """Stop a secondary mpv visualizer window, if any."""
        proc = self._viz_mpv
        self._viz_mpv = None
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass

    def _sync_pcm_analyser(self) -> None:
        """Start or stop the PCM side-car to match playback and mode."""
        want = bool(
            self._pcm_wanted
            and self._playing
            and self.process_running
            and self._stream_url
            and self._backend == BACKEND_AUDIO
        )
        if want:
            if not self.pcm_analysis_active():
                self._start_pcm_analyser(self._stream_url or "")
        else:
            self._stop_pcm_analyser()
            if not self._playing:
                self._clear_pcm_levels()

    def _start_pcm_analyser(self, stream: str) -> bool:
        """Decode ``stream`` to PCM for visualization only (does not drive audio).

        :return: ``True`` when the analyser was started.
        """
        if not stream:
            return False
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            return False
        self._stop_pcm_analyser()
        self._pcm_stop = threading.Event()
        cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-i",
            stream,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        try:
            self._pcm_analyser = _popen(cmd, stdout=subprocess.PIPE)
        except OSError as exc:
            log.warning("PCM analyser ffmpeg failed: %s", exc)
            self._pcm_analyser = None
            return False
        pcm_out = getattr(self._pcm_analyser, "stdout", None)
        if pcm_out is None:
            self._stop_pcm_analyser()
            return False
        window = bytearray()
        bytes_window = WINDOW_SAMPLES * 2
        max_keep = max(bytes_window * 2, WAVEFORM_SAMPLES * 2)
        stop_event = self._pcm_stop

        def worker() -> None:
            last_analyse = 0.0
            energy = 0.0
            try:
                while not stop_event.is_set():
                    chunk = pcm_out.read(8192)
                    if not chunk:
                        break
                    window.extend(chunk)
                    if len(window) > max_keep:
                        del window[:-max_keep]
                    samples = pcm_s16le_mono_to_floats(bytes(window[-WAVEFORM_SAMPLES * 2 :]))
                    rms = rms_of_samples(samples)
                    peak = peak_of_samples(samples)
                    energy = energy * 0.72 + max(rms, peak) * 0.28
                    with self._pcm_lock:
                        if samples:
                            padded = list(samples)
                            while len(padded) < WAVEFORM_SAMPLES:
                                padded.insert(0, 0.0)
                            self._waveform = padded[-WAVEFORM_SAMPLES:]
                        self._rms_level = rms
                        self._peak_level = peak
                        self._energy_level = max(0.0, min(1.0, energy))
                    now = time.monotonic()
                    if now - last_analyse < SPECTRUM_INTERVAL_SEC:
                        continue
                    if len(window) < bytes_window:
                        continue
                    last_analyse = now
                    frame = bytes(window[-bytes_window:])
                    levels = band_levels_from_pcm(frame, previous=None)
                    with self._pcm_lock:
                        self._spectrum_levels = list(levels)
            except (OSError, ValueError) as exc:
                log.debug("PCM analyser stopped: %s", exc)
            finally:
                if stop_event.is_set():
                    self._clear_pcm_levels()

        threading.Thread(target=worker, name="clipster-pcm-viz", daemon=True).start()
        log.info("PCM visualizer analyser started")
        return True

    def start_mpv_visualizer(self, stream: Optional[str] = None) -> bool:
        """Open a muted secondary mpv window with lavfi showcqt, if possible.

        :param stream: Optional URL; defaults to the current stream.
        :return: ``True`` when the window process was started.
        """
        self._stop_viz_mpv()
        url = stream or self._stream_url
        mpv = _find_player("mpv")
        if not url or not mpv:
            return False
        cmd = [
            mpv,
            "--no-terminal",
            "--force-window=immediate",
            "--keep-open=no",
            "--geometry=640x360",
            "--title=Clipster Visualizer",
            "--volume=0",
            "--af=lavfi=[showcqt]",
            url,
        ]
        try:
            self._viz_mpv = _popen(cmd)
        except OSError as exc:
            log.warning("mpv visualizer window failed: %s", exc)
            self._viz_mpv = None
            return False
        log.info("mpv lavfi visualizer window started")
        return True

    def stop_mpv_visualizer(self) -> None:
        """Close the secondary mpv visualizer window."""
        self._stop_viz_mpv()

    def _stop_feeder(self) -> None:
        """Signal the HTTP stream feeder thread to exit."""
        self._feeder_stop.set()

    def _cleanup_ipc(self) -> None:
        """Remove a leftover mpv IPC socket path."""
        path = self._ipc_path
        self._ipc_path = None
        if not path or paths.IS_WINDOWS:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    def _prepare_ipc_path(self) -> str:
        """Return a fresh IPC endpoint path for mpv."""
        self._cleanup_ipc()
        if paths.IS_WINDOWS:
            path = r"\\.\pipe\clipster-mpv-{0}".format(os.getpid())
        else:
            path = os.path.join(
                tempfile.gettempdir(),
                "clipster-mpv-{0}.sock".format(os.getpid()),
            )
            try:
                os.unlink(path)
            except OSError:
                pass
        self._ipc_path = path
        return path

    def _mpv_send(self, command: List[Any], *, expect_reply: bool = True) -> Optional[Any]:
        """Send one command to mpv over its IPC channel.

        The single place that talks to mpv: POSIX uses a unix socket, Windows a
        named pipe whose open/readline can block, so there it runs on a worker
        with a deadline and never stalls the caller.

        :param command: The mpv command, e.g. ``["set_property", "volume", 50]``.
        :param expect_reply: Whether the ``data`` field of the answer is wanted.
        :return: The answer's ``data``, ``True`` for an accepted command without
            a reply, or ``None`` when mpv could not be reached.
        """
        path = self._ipc_path
        if not path or self._backend not in (BACKEND_MPV, BACKEND_AUDIO):
            return None
        payload = (json.dumps({"command": command}) + "\n").encode("utf-8")
        try:
            if paths.IS_WINDOWS:
                holder: Dict[str, Any] = {"raw": None}

                def _query() -> None:
                    try:
                        with open(path, "r+b", buffering=0) as pipe:
                            pipe.write(payload)
                            holder["raw"] = pipe.readline()
                    except Exception:
                        holder["raw"] = None

                worker = threading.Thread(target=_query, name="clipster-mpv-ipc", daemon=True)
                worker.start()
                worker.join(0.25)
                if worker.is_alive() or holder["raw"] is None:
                    return None
                raw = holder["raw"]
            else:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(0.25)
                try:
                    sock.connect(path)
                    sock.sendall(payload)
                    raw = sock.recv(4096)
                finally:
                    sock.close()
            data = json.loads(raw.decode("utf-8", errors="replace").split("\n", 1)[0])
            if not isinstance(data, dict) or data.get("error") not in (None, "success"):
                return None
            return data.get("data") if expect_reply else True
        except Exception:
            log.debug("mpv did not answer %s", command[0] if command else "?", exc_info=True)
            return None

    def volume(self) -> Optional[int]:
        """Return the player volume in percent, or ``None`` when unknown.

        :return: 0-100, or ``None`` without a controllable backend.
        """
        value = self._mpv_send(["get_property", "volume"])
        if isinstance(value, (int, float)):
            return max(0, min(100, int(round(float(value)))))
        return None

    def set_volume(self, percent: int) -> bool:
        """Set the player volume.

        :param percent: 0-100; anything outside is clamped.
        :return: ``True`` when mpv accepted it.
        """
        wanted = max(0, min(100, int(percent)))
        return self._mpv_send(["set_property", "volume", wanted], expect_reply=False) is True

    def volume_controllable(self) -> bool:
        """Return whether the volume can be changed at all right now.

        ``ffplay`` takes its volume only at start, so with that backend the
        answer is no - and the interface has to say so instead of offering a
        slider that does nothing.
        """
        return bool(self._ipc_path) and self._backend in (BACKEND_MPV, BACKEND_AUDIO)

    def _mpv_cache_speed(self) -> Optional[float]:
        """Query mpv ``cache-speed`` via IPC, or ``None`` when unavailable."""
        value = self._mpv_send(["get_property", "cache-speed"])
        return float(value) if isinstance(value, (int, float)) else None

    def _start_feeder(self, stream: str, dest_stdin: Any) -> None:
        """Feed HTTP ``stream`` bytes into ``dest_stdin`` while measuring rate."""
        self._feeder_stop.clear()
        with self._rate_lock:
            self._rate_bps = 0.0
        if dest_stdin is None:
            return
        user_agent = "Mozilla/5.0"
        try:
            opts = self._options()
            agent = (
                opts.get("http_headers", {}).get("User-Agent")
                if isinstance(opts.get("http_headers"), dict)
                else None
            )
            if not agent:
                agent = opts.get("user_agent")
            if isinstance(agent, str) and agent.strip():
                user_agent = agent.strip()
        except Exception:
            pass

        def worker() -> None:
            total = 0
            last_t = time.monotonic()
            last_b = 0
            try:
                request = Request(stream, headers={"User-Agent": user_agent})
                with urlopen(request, timeout=30) as response:
                    while not self._feeder_stop.is_set():
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        now = time.monotonic()
                        elapsed = now - last_t
                        if elapsed >= 0.35:
                            with self._rate_lock:
                                self._rate_bps = (total - last_b) / elapsed
                            last_t = now
                            last_b = total
                        try:
                            dest_stdin.write(chunk)
                            dest_stdin.flush()
                        except (BrokenPipeError, OSError, ValueError):
                            break
            except (URLError, OSError, ValueError) as exc:
                log.debug("Stream feeder stopped: %s", exc)
            finally:
                try:
                    dest_stdin.close()
                except OSError:
                    pass
                with self._rate_lock:
                    if self._feeder_stop.is_set():
                        self._rate_bps = 0.0

        threading.Thread(target=worker, name="clipster-stream-feed", daemon=True).start()

    def _take_cached_stream(self, track: DiscoverTrack, *, prefer_video: bool) -> Optional[str]:
        """Pop a matching cached stream for ``track``, or ``None``."""
        keys = [key for key in (track.video_id, watch_url(track)) if key]
        with self._lock:
            for key in keys:
                entry = self._stream_cache.pop(key, None)
                if entry is None:
                    continue
                if time.monotonic() - entry.resolved_at > _CACHE_MAX_AGE_SEC:
                    continue
                if entry.prefer_video != prefer_video:
                    continue
                return entry.url
        return None

    def _start_track(
        self,
        track: DiscoverTrack,
        options: Dict[str, Any],
        *,
        embed_wid: Optional[int],
        prefer_video: bool,
        start_at: float = 0.0,
    ) -> PlayStartResult:
        """Resolve a stream and play it inside the Streaming tab."""
        want_video = bool(prefer_video)
        target = watch_url(track)
        stream = self._take_cached_stream(track, prefer_video=want_video)
        if stream:
            log.info("Using prefetched stream for '%s'", track.title)
        else:
            try:
                stream = resolve_stream_url(target, options, prefer_video=want_video)
            except Exception as exc:
                log.warning("Stream resolve failed for '%s': %s", track.title, exc)
                return PlayStartResult(track=track, error=str(exc))

        self._stream_url = stream
        self._seek_base = max(0.0, float(start_at))
        if want_video and embed_wid:
            embedded = self._start_embedded(stream, embed_wid, start_at=self._seek_base)
            if embedded.backend != BACKEND_NONE:
                embedded.track = track
                return embedded

        # Keep audio inside the app process - still no second window.
        audio = self._start_audio_only(stream, start_at=self._seek_base)
        if audio.backend != BACKEND_NONE:
            audio.track = track
            if want_video and embed_wid and not audio.error:
                audio.error = "video_embed_unavailable"
            return audio

        self._stream_url = None
        return PlayStartResult(
            track=track,
            error="no_player",
        )

    def _start_embedded(self, stream: str, embed_wid: int, *, start_at: float = 0.0) -> PlayStartResult:
        """Attach a video player to the Tk host window id.

        Uses mpv ``--wid`` so the picture is drawn inside Clipster (letterboxed /
        centered in the stage).  ffplay is not used here: ``SDL_WINDOWID`` is
        unreliable and often opens a second window.
        """
        mpv = _find_player("mpv")
        if not mpv:
            return PlayStartResult(error="embed_failed")
        ipc = self._prepare_ipc_path()
        cmd = [
            mpv,
            "--wid={0}".format(int(embed_wid)),
            "--no-terminal",
            "--idle=no",
            "--keep-open=no",
            "--force-window=no",
            "--keepaspect=yes",
            "--panscan=0",
            "--input-default-bindings=no",
            "--osc=no",
            "--input-ipc-server={0}".format(ipc),
        ]
        if start_at > 0.05:
            cmd.append("--start={0}".format(start_at))
        cmd.append(stream)
        try:
            self._process = _popen(cmd)
            self._backend = BACKEND_MPV
            log.info("Streaming embedded via mpv (wid=%s start=%.1f)", embed_wid, start_at)
            return PlayStartResult(backend=BACKEND_MPV)
        except OSError as exc:
            log.warning("Embedded mpv failed: %s", exc)
            self._process = None
            self._cleanup_ipc()
            return PlayStartResult(error="embed_failed")

    def _start_audio_only(self, stream: str, *, start_at: float = 0.0) -> PlayStartResult:
        """Play audio without opening any video window."""
        mpv = _find_player("mpv")
        if mpv:
            result = self._start_mpv_audio(mpv, stream, start_at=start_at)
            if result.backend:
                return result

        ffplay = _find_player("ffplay")
        if ffplay:
            # SDL_VIDEODRIVER=dummy stops stray windows on Linux; on Windows it
            # can prevent ffplay from starting at all, so skip it there.
            return self._start_ffplay_audio(
                ffplay,
                stream,
                use_dummy_video=not paths.IS_WINDOWS,
                start_at=start_at,
            )
        return PlayStartResult(error="no_player")

    def _start_mpv_audio(
        self,
        mpv: str,
        stream: str,
        *,
        start_at: float = 0.0,
    ) -> PlayStartResult:
        """Launch mpv for audio-only playback (stream piped to stdin)."""
        # An IPC socket for audio too, not only for the embedded video: without
        # it the volume of the usual case - listening to music - cannot be
        # changed at all, neither here nor by remote control.
        ipc = self._prepare_ipc_path()
        cmd = [
            mpv,
            "--no-video",
            "--no-terminal",
            "--idle=no",
            "--force-window=no",
            "--input-ipc-server={0}".format(ipc),
        ]
        if start_at > 0.05:
            cmd.append("--start={0}".format(start_at))
        cmd.append("-")
        try:
            self._process = _popen(cmd, stdin=subprocess.PIPE)
            self._backend = BACKEND_AUDIO
            player_in = getattr(self._process, "stdin", None)
            self._start_feeder(stream, player_in)
            log.info("Streaming audio-only via mpv (piped, start=%.1f)", start_at)
            return PlayStartResult(backend=BACKEND_AUDIO)
        except OSError as exc:
            log.warning("Audio mpv failed: %s", exc)
            self._process = None
            return PlayStartResult(error="no_player")

    def _start_ffplay_audio(
        self,
        ffplay: str,
        stream: str,
        *,
        use_dummy_video: bool,
        start_at: float = 0.0,
    ) -> PlayStartResult:
        """Launch ffplay for audio-only playback.

        :param ffplay: Absolute path to ffplay.
        :param stream: Direct media URL.
        :param use_dummy_video: Set ``SDL_VIDEODRIVER=dummy`` (Linux-friendly;
            some Windows builds need this off).
        :param start_at: Optional seek offset in seconds.
        """
        env = os.environ.copy()
        if use_dummy_video:
            env["SDL_VIDEODRIVER"] = "dummy"
        else:
            env.pop("SDL_VIDEODRIVER", None)

        cmd = [ffplay, "-autoexit", "-nodisp", "-vn", "-loglevel", "quiet"]
        if start_at > 0.05:
            cmd.extend(["-ss", "{0}".format(start_at)])
        cmd.extend(["-i", "pipe:0"])
        try:
            self._cleanup_ipc()
            self._process = _popen(cmd, env=env, stdin=subprocess.PIPE)
            self._backend = BACKEND_AUDIO
            player_in = getattr(self._process, "stdin", None)
            self._start_feeder(stream, player_in)
            log.info(
                "Streaming audio-only via ffplay (piped, dummy_video=%s start=%.1f)",
                use_dummy_video,
                start_at,
            )
            return PlayStartResult(backend=BACKEND_AUDIO)
        except OSError as exc:
            log.warning("Audio ffplay failed (dummy_video=%s): %s", use_dummy_video, exc)
            self._process = None
            return PlayStartResult(error="no_player")
