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

import collections
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
from . import updater

log = get_logger(__name__)


class ClipsterApp:
    """Watches the clipboard and drives one download at a time.

    Links copied while a download runs are not dropped: they queue up and start
    one after another.
    """

    #: Upper bound for the waiting list, so a stuck download cannot pile up.
    MAX_QUEUE = 20

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
        #: URLs whose worker thread is still running.
        self._active: Dict[str, threading.Thread] = {}
        #: Only one question may be on screen at a time - the window is single.
        self._asking = False
        self._quitting = False
        self._tray_active = False
        self._minimize_hint_shown = False
        self._cancel_events: Dict[str, threading.Event] = {}
        self._quit_event = threading.Event()
        self._forced_format = ""
        #: Set when the user asked for a download that already exists.
        self._force_redownload = False
        #: The run the navigation window currently belongs to.
        self._nav_owner = ""
        #: An update check or installation is in flight.
        self._checking_update = False
        #: Set once an update was installed, so the shutdown restarts us.
        self._restart_after_update = False
        #: Links copied while a download runs, processed one after another.
        self._queue: "collections.deque[tuple]" = collections.deque()

        self.gui.on_quit = self.request_quit
        self.gui.on_nav_closed = self._nav_closed
        self.gui.on_view_closed = self._view_closed
        self.gui.on_play_entry = self._play_entry
        self.gui.on_delete_entry = self._delete_entry
        self.gui.on_reveal_entry = self._reveal_entry
        self.gui.on_clear_history = self._clear_history
        self.gui.on_open_folder = self._open_download_folder
        self.gui.on_submit_url = self._submit_url
        self.gui.on_save_settings = self._save_settings
        self.gui.on_check_updates = self._check_updates
        self.gui.on_install_update = self._install_update
        self.gui.on_open_result = self._open_result
        self.gui.on_reveal_result = self._reveal_result
        self.gui.build_windows()

    @property
    def _busy(self) -> bool:
        """Return ``True`` while the pipeline cannot take another link right now."""
        if self._asking:
            # The navigation window shows a question; a second one would replace it.
            return True
        return len(self._active) >= self._parallel_limit()

    def _parallel_limit(self) -> int:
        """Return how many downloads may run at the same time."""
        if not self.config.parallel_downloads:
            return 1
        return max(1, self.config.max_parallel_downloads)

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
            if self.config.start_minimized:
                log.info("Started in the system tray.")
                return
            self.gui.show_view()
            return

        if self.config.use_tray:
            log.warning("No system tray available - showing the view window instead.")
        elif self.config.start_minimized:
            log.warning("Without a tray icon the window is the only way to quit - showing it.")
        self.gui.show_view()

    def _post_start(self) -> None:
        """Run the one-off tasks that need a live event loop."""
        if self.config.show_startup_notification and not self.tray.notify(self.messages["started"]):
            self.gui.toast(self.messages["started"])
        self._sync_autostart()
        self._maybe_offer_desktop_shortcut()
        if self.config.check_updates and updater.due(self.config.update_check_hours):
            self._check_updates(announce=False)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def _check_updates(self, announce: bool = True) -> None:
        """Ask GitHub for the newest commit, without blocking the interface.

        :param announce: Show the result even when nothing is new; the startup
            check stays quiet unless there is something to report.
        :return: None
        """
        if self._checking_update:
            return
        self._checking_update = True
        self.gui.show_update_state(self.messages["update_checking"], False, busy=True)

        def work() -> None:
            """Talk to GitHub off the interface thread."""
            info = updater.check()
            self.bridge.post(self._update_checked, info, announce)

        threading.Thread(target=work, name="clipster-update-check", daemon=True).start()

    def _update_checked(self, info: "updater.UpdateInfo", announce: bool) -> None:
        """Show what the check found.

        :param info: The result of :func:`clipster.updater.check`.
        :param announce: Report "nothing new" and failures as well.
        :return: None
        """
        self._checking_update = False
        if not info.known:
            text = self.messages.format("update_failed", details=info.error or "?")
            self.gui.show_update_state(text, False)
            if announce:
                log.warning("%s", text)
            return
        if info.available:
            text = self.messages.format("update_available", summary=info.summary or info.remote)
            self.gui.show_update_state(text, True)
            log.info("%s", text)
            if not self.tray.notify(text):
                self.gui.toast(text)
            return
        text = self.messages.format("update_current", commit=info.remote)
        self.gui.show_update_state(text, False)
        if announce:
            log.info("%s", text)

    def _install_update(self) -> None:
        """Fetch the new version and restart, once the user confirmed."""
        if self._checking_update:
            return
        self._checking_update = True
        self.gui.show_update_state(self.messages["update_installing"], False, busy=True)

        def work() -> None:
            """Do the fetching off the interface thread."""
            ok, message = updater.apply()
            self.bridge.post(self._update_applied, ok, message)

        threading.Thread(target=work, name="clipster-update", daemon=True).start()

    def _update_applied(self, ok: bool, message: str) -> None:
        """Restart when the update worked, report the reason when it did not.

        :param ok: Whether the new version was fetched.
        :param message: Output of the update, for the log and the user.
        :return: None
        """
        self._checking_update = False
        if not ok:
            text = self.messages.format("update_error", details=message)
            log.error("%s", text)
            self.gui.show_update_state(text, True)
            self.gui.show_error(self.messages["error_title"], text)
            return
        log.info("Update installed: %s", message)
        self._restart_after_update = True
        self.request_quit()

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
        self._queue.clear()
        self._cancel_all()
        try:
            self.gui.root.quit()
        except Exception:  # pragma: no cover - interpreter already gone
            pass

    def _shutdown(self) -> None:
        """Release every resource after the event loop has ended."""
        self._quitting = True
        self._quit_event.set()
        self._cancel_all()
        for worker in list(self._active.values()):
            if worker.is_alive():
                worker.join(timeout=5.0)
        self.tray.stop()
        self.bridge.stop()
        self.gui.destroy()
        log.info("%s", self.messages["stopped"])
        if self._restart_after_update:
            # Last thing before the process ends: the windows and the lock are
            # gone, so the new instance can take over cleanly.
            updater.restart()

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
            # Point at whatever this backend actually supports: a click on the
            # icon when it reacts to one, the menu otherwise.
            key = "tray_minimized_click" if self.tray.has_default_action else "tray_minimized"
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
        log.info("Download requested from the view window.")
        self._enqueue(target, media_format)

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

    def _play_entry(self, entry: HistoryEntry) -> None:
        """Play the file of a table row with the system's default player."""
        target = entry.file_path()
        if target is None:
            self.gui.show_error(self.messages["error_title"], self.messages["history_missing"])
            self.gui.render_history(self.history.entries)
            return
        shortcuts.open_path(target)

    def _delete_entry(self, entry: HistoryEntry) -> None:
        """Delete the downloaded file and drop the row from the list.

        An entry whose file is already gone is simply removed - that is the only
        way to clear a failed attempt out of the list.

        :param entry: The entry the user wants gone.
        :return: None
        """
        target = entry.file_path()
        if target is not None:
            try:
                target.unlink()
                log.info("Deleted %s", target)
            except OSError as exc:
                log.error("Could not delete %s: %s", target, exc)
                self.gui.show_error(
                    self.messages["error_title"],
                    self.messages.format("history_delete_failed", details=exc),
                )
                return
        self.history.remove(entry)
        self.gui.render_history(self.history.entries)

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
        # Watched even while a download runs, otherwise a link copied in the
        # meantime would be lost for good.
        current = self.clipboard.read()
        if current != self._last_seen:
            self._last_seen = current
            url = extract_youtube_url(current)
            if url:
                log.info("New YouTube link detected in the clipboard.")
                log.debug("Target URL: %s", url)
                self._enqueue(url, "")
        self.gui.root.after(self.config.poll_interval_ms(), self._poll_clipboard)

    def _enqueue(self, url: str, media_format: str, force: bool = False) -> None:
        """Start the download, or line it up behind the running one.

        :param url: The canonical YouTube URL.
        :param media_format: Forced format, empty to ask the user.
        :param force: Download again even when the file is already there.
        :return: None
        """
        if not self._busy:
            self._forced_format = media_format
            self._force_redownload = force
            self._start_worker(url)
            return
        if url in self._active and not force:
            log.debug("%s is downloading right now - ignored.", url)
            return
        if any(queued == url for queued, _, _ in self._queue):
            log.debug("%s is already waiting - ignored.", url)
            return
        if len(self._queue) >= self.MAX_QUEUE:
            log.warning("The waiting list is full (%s); %s was dropped.", self.MAX_QUEUE, url)
            return
        # The flag travels with the entry, otherwise a deliberate "download
        # again" would be met with "already downloaded" once its turn comes.
        self._queue.append((url, media_format, force))
        log.info("A download is running - %s links are now waiting.", len(self._queue))
        self._set_status(self.messages.format("status_queued", count=len(self._queue)))

    def _start_next(self) -> None:
        """Take the next waiting link, if there is one."""
        if self._quitting or self._busy or not self._queue:
            return
        url, media_format, force = self._queue.popleft()
        log.info("Starting the next waiting download (%s left).", len(self._queue))
        self._forced_format = media_format
        self._force_redownload = force
        self._start_worker(url)

    def _start_worker(self, url: str) -> None:
        """Open the navigation window and hand the URL to the pipeline.

        :param url: The YouTube URL to process.
        :return: None
        """
        nav = self.gui.nav
        if nav is None:  # pragma: no cover - windows are built in __init__
            return
        self._asking = True
        self._nav_owner = url
        self._cancel_events[url] = threading.Event()
        self._set_status(self.messages["status_working"])
        nav.begin(self.messages["link_received"])
        worker = threading.Thread(target=self._handle_url, args=(url,),
                                  name="clipster-download", daemon=True)
        self._active[url] = worker
        worker.start()

    def _finish_worker(self, url: str, status_key: str, **kwargs: object) -> None:
        """Retire one finished run and let the next link in.

        :param url: The URL whose run ended.
        :param status_key: Translation key for the tray tooltip.
        :param kwargs: Placeholder values for the status text.
        :return: None
        """
        self._active.pop(url, None)
        self._cancel_events.pop(url, None)
        if self._nav_owner == url:
            self._nav_owner = ""
            # A run that ends before its question was answered - a metadata
            # error, a cancel - would otherwise leave the lock set for good.
            self._asking = False
        self._forced_format = ""
        self._force_redownload = False
        # Re-sync so clearing the clipboard cannot re-trigger the same link.
        self._last_seen = self.clipboard.read()
        self._set_status(self.messages.format(status_key, **kwargs))
        if self._queue:
            # A short delay lets the user see the result before the next
            # question replaces it.
            self.gui.root.after(1200, self._start_next)

    def _cancel_all(self) -> None:
        """Signal every running download to stop."""
        for event in list(self._cancel_events.values()):
            event.set()

    def _cancel_of(self, url: str) -> threading.Event:
        """Return the cancel flag of one run, creating it when missing.

        :param url: The URL of the run.
        :return: Its cancel event.
        """
        return self._cancel_events.setdefault(url, threading.Event())

    def _nav_post(self, url: str, method: str, *args: object) -> None:
        """Call a navigation window method, but only for the owning run.

        :param url: The URL of the run that wants to draw.
        :param method: Name of the :class:`~clipster.navwindow.NavWindow` method.
        :param args: Arguments for that method.
        :return: None
        """
        nav = self.gui.nav
        if nav is None or not self._owns_nav(url):
            return
        self.bridge.post(getattr(nav, method), *args)

    def _owns_nav(self, url: str) -> bool:
        """Return ``True`` when this run may write to the navigation window.

        With several downloads in flight the single window belongs to the most
        recently started one; the others keep going and report into the list.

        :param url: The URL of the run.
        :return: Whether the window may be touched.
        """
        return self._nav_owner == url

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
            self._nav_post(url, "set_status", self.messages["fetching_metadata"])
            self._nav_post(url, "set_percent", None)

            try:
                info = self.downloader.fetch_info(url)
            except MetadataError as exc:
                self.bridge.post(self._question_answered)
                details = _short_error(exc)
                log.error("Video information could not be loaded: %s", details)
                message = self.messages.format("error_metadata", details=details)
                self._nav_post(url, "finish", message, STATUS_FAILED)
                self._store(url=url, status=STATUS_FAILED, error=message, error_kind="metadata")
                self.bridge.post(self._finish_worker, url, "status_failed")
                return

            if self._aborted(url):
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

            existing = self.history.find_download(url, media_format)
            if existing is not None and not self._force_redownload:
                log.info("%s is already downloaded - offering the existing file.", existing.name)
                self.bridge.post(self._offer_existing, nav, info, existing, url, media_format)
                return

            self._run_download(url, info, media_format, language)
        except Exception:  # pragma: no cover - defensive, keeps the app alive
            log.exception("Unexpected error while processing the link")
            self._nav_post(url, "finish", self.messages["error_title"], STATUS_FAILED)
            self.bridge.post(self._finish_worker, url, "status_failed")

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
            self.bridge.post(self._question_answered)
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
        cancel = self._cancel_of(info.url)
        answer = prompt.wait(self._quit_event, cancel, nav.cancel_event)
        self.bridge.post(self._question_answered)
        if not isinstance(answer, dict):
            return None
        if not answer.get("language"):
            # No track was offered or "best available" was picked: resolve it
            # here so the download never relies on yt-dlp guessing.
            answer["language"] = self._auto_language(info)
        return answer

    def _offer_existing(self, nav: Any, info: VideoInfo, entry: HistoryEntry,
                        url: str, media_format: str) -> None:
        """Show the file that is already there instead of fetching it again.

        :param nav: The navigation window.
        :param info: The fetched video metadata.
        :param entry: The earlier download.
        :param url: The YouTube URL, for the "download again" button.
        :param media_format: The chosen format, likewise.
        :return: None
        """
        target = entry.file_path()
        if target is None:  # vanished between the lookup and now
            self._enqueue(url, media_format, force=True)
            return

        def again() -> None:
            """Start the same download once more, on the user's request."""
            nav.hide()
            self._enqueue(url, media_format, force=True)

        detail = "{0}  ·  {1}".format(entry.name, format_size(entry.size)) if entry.size else entry.name
        nav.already_downloaded(info.title, target, detail, again)
        self._finish_worker(url, "status_done", title=_trim(entry.name, 55))

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
        cancel_event = self._cancel_of(url)
        if self._owns_nav(url):
            self.bridge.call(nav.set_headline, info.title)
            self.bridge.call(nav.show_progress, media_format, info.duration or 0)

        def on_progress(progress: Progress) -> None:
            """Mirror a downloader progress update into the navigation window."""
            if self._owns_nav(url) and nav.cancel_event.is_set():
                cancel_event.set()
            self._nav_post(url, "set_status", self._phase_text(progress, media_format), progress.detail)
            self._nav_post(url, "set_percent", progress.percent)

        watcher = threading.Thread(
            target=_mirror_event,
            args=(nav.cancel_event if self._owns_nav(url) else threading.Event(), cancel_event),
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
            self._nav_post(url, "finish", message, STATUS_FAILED)
            self._store(
                url=url,
                title=info.title,
                media_format=media_format,
                duration=info.duration or 0,
                status=STATUS_FAILED,
                error=message,
                error_kind=exc.kind,
            )
            self.bridge.post(self._finish_worker, url, "status_failed")
            return
        finally:
            cancel_event.set()

        name = result.name if result is not None else info.title
        size = _file_size(result)
        self._nav_post(
            url,
            "finish",
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
        self.bridge.post(self._finish_worker, url, "status_done", title=_trim(name, 55))

    def _question_answered(self) -> None:
        """Release the interactive lock so the next link may be picked up."""
        self._asking = False
        if self._queue:
            self.gui.root.after(200, self._start_next)

    def _cancel_run(self, url: str, info: Optional[VideoInfo] = None, media_format: str = "") -> None:
        """Record a canceled run and reset the state machine.

        :param url: The YouTube URL.
        :param info: The metadata, when it was already known.
        :param media_format: The chosen format, when it was already known.
        :return: None
        """
        self._nav_post(url, "finish", self.messages["progress_canceled"], STATUS_CANCELED)
        self._store(
            url=url,
            title=info.title if info is not None else "",
            media_format=media_format,
            duration=(info.duration or 0) if info is not None else 0,
            status=STATUS_CANCELED,
        )
        self.bridge.post(self._finish_worker, url, "status_canceled")

    def _store(self, **fields: object) -> None:
        """Queue one history entry for storage on the Tk thread.

        :param fields: Field values for :class:`~clipster.history.HistoryEntry`.
        :return: None
        """
        entry = HistoryEntry(**fields)  # type: ignore[arg-type]
        if not entry.name:
            entry.name = entry.title or entry.url
        self.bridge.post(self._record, entry)

    def _aborted(self, url: str) -> bool:
        """Return ``True`` when this run should stop (quit or cancel pressed).

        :param url: The URL of the run.
        :return: Whether it must not continue.
        """
        if self._quitting or self._quit_event.is_set():
            return True
        nav = self.gui.nav
        if nav is not None and self._owns_nav(url) and nav.cancel_event.is_set():
            return True
        return self._cancel_of(url).is_set()

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
