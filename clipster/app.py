"""The clipboard monitor and the download pipeline.

This is the Python version of the main event loop that used to live in
``linux/youtube-clipster.sh`` and in the ``:loop`` label of the Windows batch
file - now identical on every platform.

The program lives in the system tray.  Copying a YouTube link opens the small
:class:`clipster.navwindow.NavWindow`, which asks for format and audio track and
then shows the progress.  Every outcome is appended to the
:class:`clipster.history.History` that the large
:class:`clipster.viewwindow.ViewWindow` displays.
"""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from . import APP_SHORT_NAME, APP_TITLE, paths, shortcuts
from .bridge import Prompt, TkBridge
from .clipboard import Clipboard
from .config import Config
from .downloader import (
    DownloadCanceled,
    Downloader,
    DownloadFailed,
    MetadataError,
    Progress,
    VideoInfo,
    extract_youtube_url,
)
from .gui import Gui
from .history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, History, HistoryEntry, format_size
from .i18n import Messages
from .logging_setup import get_logger
from .tray import TrayIcon

log = get_logger(__name__)


class ClipsterApp:
    """Watches the clipboard and drives one download at a time."""

    def __init__(self, config: Config, messages: Messages) -> None:
        """
        :param config: The active user configuration.
        :param messages: The active translation table.
        """
        self.config = config
        self.messages = messages
        self.download_dir = config.resolved_download_dir()

        self.history = History(limit=config.history_limit).load()
        self.gui = Gui(messages, config, self.download_dir)
        self.bridge = TkBridge(self.gui.root)
        self.clipboard = Clipboard(self.gui.root)
        self.downloader = Downloader(config, messages)
        self.tray = TrayIcon(
            messages=messages,
            icon_path=paths.icon_file(),
            on_show=lambda: self.bridge.post(self._show_view),
            on_open_folder=lambda: self.bridge.post(self._open_download_folder),
            on_quit=lambda: self.bridge.post(self.request_quit),
        )

        self._last_seen = ""
        self._busy = False
        self._quitting = False
        self._tray_active = False
        self._minimize_hint_shown = False
        self._worker: Optional[threading.Thread] = None
        self._cancel_event: Optional[threading.Event] = None
        self._quit_event = threading.Event()
        self._forced_format = ""

        self.gui.on_quit = self.request_quit
        self.gui.on_nav_closed = self._nav_closed
        self.gui.on_view_closed = self._view_closed
        self.gui.on_open_entry = self._open_entry
        self.gui.on_reveal_entry = self._reveal_entry
        self.gui.on_clear_history = self._clear_history
        self.gui.on_open_folder = self._open_download_folder
        self.gui.on_submit_url = self._submit_url
        self.gui.on_save_settings = self._save_settings
        self.gui.on_open_result = self._open_result
        self.gui.on_reveal_result = self._reveal_result
        self.gui.build_windows()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def run(self) -> int:
        """Start the GUI event loop and monitor the clipboard until quit.

        :return: The process exit code.
        """
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._install_signal_handlers()
        self.bridge.start()
        self.gui.render_history(self.history.entries)

        if self.config.use_tray:
            self._tray_active = self.tray.start()
        self._apply_initial_visibility()

        # Ignore whatever is already in the clipboard at startup.
        self._last_seen = self.clipboard.read()

        log.info("%s", self.messages["separator"])
        log.info("%s", APP_TITLE)
        log.info("%s", self.messages["separator"])
        log.info("%s", self.messages["started"])
        log.info("Download folder: %s", self.download_dir)
        log.info("Download history: %s entries (%s)", len(self.history), self.history.path)

        self.gui.root.after(200, self._post_start)
        self.gui.root.after(self.config.poll_interval_ms(), self._poll_clipboard)

        try:
            self.gui.root.mainloop()
        except KeyboardInterrupt:  # pragma: no cover - console interrupt
            log.info("Interrupted.")
        finally:
            self._shutdown()
        return 0

    def _install_signal_handlers(self) -> None:
        """Quit cleanly on Ctrl+C and on SIGTERM."""
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            handler = getattr(signal, name, None)
            if handler is None:
                continue
            try:
                signal.signal(handler, self._on_signal)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass

    def _on_signal(self, signum: int, _frame: object) -> None:
        """Translate a POSIX signal into a normal quit request."""
        log.info("Signal %s received - shutting down.", signum)
        self.request_quit()

    def _apply_initial_visibility(self) -> None:
        """Decide whether the view window is shown at startup.

        Without a working tray icon the window is the only way to quit the
        program, so it is forced visible in that case.
        """
        if self._tray_active:
            if self.config.start_minimized or not self.config.show_status_window:
                log.info("Started in the system tray.")
                return
            self.gui.show_view()
            return

        if self.config.use_tray:
            log.warning("No system tray available - showing the view window instead.")
        if not self.config.show_status_window:
            log.warning("'show_status_window' is off but there is no tray icon - showing it anyway.")
        self.gui.show_view()

    def _post_start(self) -> None:
        """Run the one-off tasks that need a live event loop."""
        if self.config.show_startup_notification and not self.tray.notify(self.messages["started"]):
            self.gui.toast(self.messages["started"])
        self._sync_autostart()
        self._maybe_offer_desktop_shortcut()

    def _sync_autostart(self) -> None:
        """Make the autostart entry match the configuration."""
        try:
            if self.config.autostart != shortcuts.autostart_enabled():
                shortcuts.set_autostart(self.config.autostart)
        except Exception as exc:  # pragma: no cover - desktop specific
            log.debug("Autostart could not be synchronised: %s", exc)

    def _maybe_offer_desktop_shortcut(self) -> None:
        """Ask once whether a desktop launcher should be created."""
        if not self.config.ask_desktop_shortcut or shortcuts.desktop_shortcut_exists():
            return
        accepted = self.gui.ask_yes_no(self.messages["shortcut_title"], self.messages["shortcut_question"])
        self.config.ask_desktop_shortcut = False
        self.config.save()
        if not accepted:
            log.info("The user declined the desktop shortcut.")
            return
        try:
            created = shortcuts.create_desktop_shortcut()
        except OSError as exc:
            log.error("Desktop shortcut could not be created: %s", exc)
            self.gui.show_error(
                self.messages["shortcut_title"],
                self.messages.format("shortcut_failed", details=exc),
            )
            return
        self.gui.toast(self.messages.format("shortcut_created", path=created))

    def request_quit(self) -> None:
        """Ask the application to shut down (safe from any thread)."""
        if self._quitting:
            return
        self._quitting = True
        self._quit_event.set()
        if self._cancel_event is not None:
            self._cancel_event.set()
        try:
            self.gui.root.quit()
        except Exception:  # pragma: no cover - interpreter already gone
            pass

    def _shutdown(self) -> None:
        """Release every resource after the event loop has ended."""
        self._quitting = True
        self._quit_event.set()
        if self._cancel_event is not None:
            self._cancel_event.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=5.0)
        self.tray.stop()
        self.bridge.stop()
        self.gui.destroy()
        log.info("%s", self.messages["stopped"])

    # ------------------------------------------------------------------
    # Window callbacks
    # ------------------------------------------------------------------
    def _show_view(self) -> None:
        """Bring the view window up from the tray."""
        self.gui.show_view()

    def _nav_closed(self) -> None:
        """The navigation window was closed or dismissed."""
        nav = self.gui.nav
        if nav is not None:
            nav.hide()
        if not self._tray_active and not self.gui.view_visible():
            # Nothing else is on screen and there is no tray icon to come back
            # to, so closing the last window has to end the program.
            self.request_quit()

    def _view_closed(self) -> None:
        """The view window was closed: hide it, or quit without a tray."""
        if not self._tray_active:
            self.request_quit()
            return
        self.gui.hide_view()
        log.debug("View window hidden - the program keeps running in the tray.")
        if not self._minimize_hint_shown:
            self._minimize_hint_shown = True
            # Without a tray menu the icon click is the only way back, so say so.
            key = "tray_minimized" if self.tray.has_menu else "tray_minimized_no_menu"
            if not self.tray.notify(self.messages[key]):
                self.gui.toast(self.messages[key])

    def _save_settings(self) -> None:
        """Persist the configuration edited in the settings page."""
        self.config.save()
        self.history.limit = max(1, self.config.history_limit)
        self.download_dir = self.config.resolved_download_dir()
        self.gui.download_dir = self.download_dir
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Download folder %s could not be created: %s", self.download_dir, exc)
        self._sync_autostart()
        self.gui.render_history(self.history.entries)
        log.info("Settings saved to %s", self.config.path)

    def _submit_url(self, url: str, media_format: str) -> None:
        """Start a download for a URL typed into the toolbar.

        :param url: The pasted URL.
        :param media_format: ``mp3`` or ``mp4`` from the toolbar selector.
        :return: None
        """
        target = extract_youtube_url(url)
        if not target:
            self.gui.show_error(self.messages["error_title"], self.messages["error_not_a_link"])
            return
        if self._busy:
            self.gui.show_error(self.messages["error_title"], self.messages["error_busy"])
            return
        log.info("Download requested from the view window.")
        self._forced_format = media_format
        self._start_worker(target)

    # ------------------------------------------------------------------
    # The download list
    # ------------------------------------------------------------------
    def _record(self, entry: HistoryEntry) -> None:
        """Append one finished run to the history and refresh the table.

        :param entry: The entry to store.
        :return: None
        """
        self.history.add(entry)
        self.gui.render_history(self.history.entries)

    def _open_entry(self, entry: HistoryEntry) -> None:
        """Play or open the file of a table row."""
        target = entry.file_path()
        if target is None:
            self.gui.show_error(self.messages["error_title"], self.messages["history_missing"])
            self.gui.render_history(self.history.entries)
            return
        shortcuts.open_path(target)

    def _reveal_entry(self, entry: HistoryEntry) -> None:
        """Open the folder of a table row and select the file."""
        target = entry.file_path()
        if target is None:
            self.gui.show_error(self.messages["error_title"], self.messages["history_missing"])
            self.gui.render_history(self.history.entries)
            return
        shortcuts.reveal_path(target, self.config.file_manager)

    def _open_result(self) -> None:
        """Open the file the navigation window just finished."""
        nav = self.gui.nav
        target = nav.result_path() if nav is not None else None
        if target is not None:
            shortcuts.open_path(target)

    def _reveal_result(self) -> None:
        """Open the folder of the file the navigation window just finished."""
        nav = self.gui.nav
        target = nav.result_path() if nav is not None else None
        if target is not None:
            shortcuts.reveal_path(target, self.config.file_manager)

    def _clear_history(self) -> None:
        """Empty the download list (the files stay where they are)."""
        self.history.clear()
        self.gui.render_history(self.history.entries)
        log.info("Download list cleared.")

    # ------------------------------------------------------------------
    # Clipboard monitoring
    # ------------------------------------------------------------------
    def _poll_clipboard(self) -> None:
        """Check the clipboard for a new YouTube link and reschedule itself."""
        if self._quitting:
            return
        if not self._busy:
            current = self.clipboard.read()
            if current != self._last_seen:
                self._last_seen = current
                url = extract_youtube_url(current)
                if url:
                    log.info("New YouTube link detected in the clipboard.")
                    log.debug("Target URL: %s", url)
                    self._forced_format = ""
                    self._start_worker(url)
        self.gui.root.after(self.config.poll_interval_ms(), self._poll_clipboard)

    def _start_worker(self, url: str) -> None:
        """Open the navigation window and hand the URL to the pipeline.

        :param url: The YouTube URL to process.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover - windows are built in __init__
            return
        self._busy = True
        self._set_status(self.messages["status_working"])
        nav.begin(self.messages["link_received"])
        self._cancel_event = threading.Event()
        self._worker = threading.Thread(target=self._handle_url, args=(url,), name="clipster-download", daemon=True)
        self._worker.start()

    def _finish_worker(self, status_key: str, **kwargs: object) -> None:
        """Reset the state machine after a finished pipeline run.

        :param status_key: Translation key for the tray tooltip.
        :param kwargs: Placeholder values for the status text.
        :return: None
        """
        self._cancel_event = None
        self._worker = None
        self._forced_format = ""
        # Re-sync so clearing the clipboard cannot re-trigger the same link.
        self._last_seen = self.clipboard.read()
        self._busy = False
        self._set_status(self.messages.format(status_key, **kwargs))

    def _set_status(self, text: str) -> None:
        """Mirror the current status into the tray tooltip.

        :param text: The status text.
        :return: None
        """
        self.tray.set_tooltip("{0}\n{1}".format(APP_SHORT_NAME, text))

    # ------------------------------------------------------------------
    # Download pipeline (background thread)
    # ------------------------------------------------------------------
    def _handle_url(self, url: str) -> None:
        """Run metadata lookup, the question and the download for one URL.

        Executed in a worker thread; every GUI access goes through the bridge.

        :param url: The YouTube URL.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover
            return
        try:
            self.bridge.post(nav.set_status, self.messages["fetching_metadata"])
            self.bridge.post(nav.set_percent, None)

            try:
                info = self.downloader.fetch_info(url)
            except MetadataError as exc:
                details = _short_error(exc)
                log.error("Video information could not be loaded: %s", details)
                message = self.messages.format("error_metadata", details=details)
                self.bridge.post(nav.finish, message, STATUS_FAILED)
                self._store(url=url, status=STATUS_FAILED, error=message, error_kind="metadata")
                self.bridge.post(self._finish_worker, "status_failed")
                return

            if self._aborted():
                self._cancel_run(url, info)
                return

            answer = self._ask(nav, info)
            if not answer:
                log.warning("Download canceled by the user in the navigation window.")
                self._cancel_run(url, info)
                return
            media_format = answer.get("format") or "mp3"
            language = answer.get("language") or ""
            log.debug("Selected format: %s, audio track: %s", media_format, language or "best")

            self._run_download(url, info, media_format, language)
        except Exception:  # pragma: no cover - defensive, keeps the app alive
            log.exception("Unexpected error while processing the link")
            self.bridge.post(nav.finish, self.messages["error_title"], STATUS_FAILED)
            self.bridge.post(self._finish_worker, "status_failed")

    def _auto_language(self, info: VideoInfo) -> str:
        """Return the audio track to use without asking the user.

        With a single track that track is picked explicitly instead of leaving
        the choice to yt-dlp.  With several tracks the original one wins, which
        is what "always use the default language" means.

        :param info: The fetched video metadata.
        :return: A language code, or an empty string when nothing is known.
        """
        if len(info.audio_languages) == 1:
            return info.audio_languages[0]
        return info.original_language()

    def _ask(self, nav: Any, info: VideoInfo) -> Optional[Dict[str, str]]:
        """Ask for format and audio track, or skip when nothing to decide.

        The track question only appears when the video really offers several
        languages and the user asked to be prompted; otherwise the track is
        resolved by :meth:`_auto_language`.

        :param nav: The navigation window.
        :param info: The fetched video metadata.
        :return: ``{"format": ..., "language": ...}``, or ``None`` on cancel.
        """
        if self._forced_format:
            # Started from the view window toolbar, which already picked a format.
            return {"format": self._forced_format, "language": self._auto_language(info)}
        prompt = Prompt()
        self.bridge.post(
            nav.ask,
            prompt,
            info.title,
            info.duration or 0,
            info.audio_languages,
            self.config.default_format,
            self.config.ask_audio_language,
            info.original_language(),
        )
        cancel = self._cancel_event or threading.Event()
        answer = prompt.wait(self._quit_event, cancel, nav.cancel_event)
        if not isinstance(answer, dict):
            return None
        if not answer.get("language"):
            # No track was offered or "best available" was picked: resolve it
            # here so the download never relies on yt-dlp guessing.
            answer["language"] = self._auto_language(info)
        return answer

    def _run_download(self, url: str, info: VideoInfo, media_format: str, language: str) -> None:
        """Download the video and report the outcome.

        :param url: The YouTube URL.
        :param info: The fetched video metadata.
        :param media_format: ``mp3`` or ``mp4``.
        :param language: Preferred audio language code, empty for best.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover
            return
        cancel_event = self._cancel_event or threading.Event()
        self.bridge.call(nav.set_headline, info.title)
        self.bridge.call(nav.show_progress, media_format, info.duration or 0)

        def on_progress(progress: Progress) -> None:
            """Mirror a downloader progress update into the navigation window."""
            if nav.cancel_event.is_set():
                cancel_event.set()
            self.bridge.post(nav.set_status, self._phase_text(progress, media_format), progress.detail)
            self.bridge.post(nav.set_percent, progress.percent)

        watcher = threading.Thread(
            target=_mirror_event,
            args=(nav.cancel_event, cancel_event),
            name="clipster-cancel-watch",
            daemon=True,
        )
        watcher.start()

        try:
            result: Optional[Path] = self.downloader.download(
                url=url,
                media_format=media_format,
                language=language,
                on_progress=on_progress,
                cancel_event=cancel_event,
                duration=info.duration,
                estimated_size=info.filesize,
            )
        except DownloadCanceled:
            cancel_event.set()
            self._cancel_run(url, info, media_format)
            return
        except DownloadFailed as exc:
            cancel_event.set()
            message = self._error_text(exc)
            log.error("Download failed: %s", _short_error(exc))
            self.bridge.post(nav.finish, message, STATUS_FAILED)
            self._store(
                url=url,
                title=info.title,
                media_format=media_format,
                duration=info.duration or 0,
                status=STATUS_FAILED,
                error=message,
                error_kind=exc.kind,
            )
            self.bridge.post(self._finish_worker, "status_failed")
            return
        finally:
            cancel_event.set()

        name = result.name if result is not None else info.title
        size = _file_size(result)
        self.bridge.post(
            nav.finish,
            self.messages["nav_done"],
            STATUS_OK,
            "{0}  ·  {1}".format(name, format_size(size)) if size else name,
            result,
        )
        self._store(
            url=url,
            title=info.title,
            name=name,
            path=str(result) if result is not None else "",
            media_format=media_format,
            size=size,
            duration=info.duration or 0,
            status=STATUS_OK,
        )

        if self.config.clear_clipboard_after_download:
            self.clipboard.clear()
        if self.config.open_folder_after_download:
            shortcuts.open_folder(self.download_dir, self.config.file_manager)
        if self.config.open_view_after_download:
            self.bridge.post(self.gui.show_view, "downloads")

        log.info("%s (%s)", self.messages["progress_finished"], name)
        self.bridge.post(self._finish_worker, "status_done", title=_trim(name, 55))

    def _cancel_run(self, url: str, info: Optional[VideoInfo] = None, media_format: str = "") -> None:
        """Record a canceled run and reset the state machine.

        :param url: The YouTube URL.
        :param info: The metadata, when it was already known.
        :param media_format: The chosen format, when it was already known.
        :return: None
        """
        nav = self.gui.nav
        if nav is not None:
            self.bridge.post(nav.finish, self.messages["progress_canceled"], STATUS_CANCELED)
        self._store(
            url=url,
            title=info.title if info is not None else "",
            media_format=media_format,
            duration=(info.duration or 0) if info is not None else 0,
            status=STATUS_CANCELED,
        )
        self.bridge.post(self._finish_worker, "status_canceled")

    def _store(self, **fields: object) -> None:
        """Queue one history entry for storage on the Tk thread.

        :param fields: Field values for :class:`~clipster.history.HistoryEntry`.
        :return: None
        """
        entry = HistoryEntry(**fields)  # type: ignore[arg-type]
        if not entry.name:
            entry.name = entry.title or entry.url
        self.bridge.post(self._record, entry)

    def _aborted(self) -> bool:
        """Return ``True`` when the run should stop (quit or cancel pressed)."""
        if self._quitting or self._quit_event.is_set():
            return True
        nav = self.gui.nav
        if nav is not None and nav.cancel_event.is_set():
            return True
        return self._cancel_event is not None and self._cancel_event.is_set()

    def _phase_text(self, progress: Progress, media_format: str) -> str:
        """Return the localized status text for a progress phase.

        :param progress: The reported progress.
        :param media_format: ``mp3`` or ``mp4``, used in the conversion text.
        :return: The status line for the navigation window.
        """
        if progress.phase == "downloading":
            return self.messages["progress_downloading"]
        if progress.phase == "converting":
            return self.messages.format("progress_converting", format=media_format.upper())
        if progress.phase == "merging":
            return self.messages["progress_merging"]
        if progress.phase == "finished":
            return self.messages["progress_finished"]
        if progress.phase == "preparing":
            return self.messages["progress_preparing"]
        return self.messages["progress_postprocessing"]

    def _error_text(self, error: DownloadFailed) -> str:
        """Return the localized message for a failed download.

        :param error: The raised error.
        :return: The message shown to the user and stored in the history.
        """
        if error.kind == "diskfull":
            return self.messages.format("error_disk_full", details=_short_error(error))
        if error.kind == "bot":
            return self.messages["error_bot_detected"]
        if error.kind == "unavailable":
            return self.messages["error_unavailable"]
        return self.messages.format("error_generic", details=_short_error(error))

    def _open_download_folder(self) -> None:
        """Open the configured download folder in the file manager."""
        shortcuts.open_folder(self.download_dir, self.config.file_manager)


def _mirror_event(source: threading.Event, target: threading.Event) -> None:
    """Set ``target`` as soon as ``source`` is set.

    :param source: The event to watch (the window's cancel button).
    :param target: The event to set (the downloader's cancel flag).
    :return: None
    """
    while not target.is_set():
        if source.wait(timeout=0.2):
            target.set()
            return


def _file_size(target: Optional[Path]) -> int:
    """Return the size of ``target`` in bytes, or ``0`` when unknown.

    :param target: The downloaded file.
    :return: The size in bytes.
    """
    if target is None:
        return 0
    try:
        return target.stat().st_size
    except OSError:
        return 0


def _trim(text: str, limit: int) -> str:
    """Return ``text`` shortened to ``limit`` characters with an ellipsis.

    :param text: The text to shorten.
    :param limit: Maximum number of characters.
    :return: The shortened text.
    """
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _short_error(error: BaseException, limit: int = 300) -> str:
    """Return a compact single-line representation of an exception.

    :param error: The exception to describe.
    :param limit: Maximum number of characters.
    :return: The shortened message.
    """
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
