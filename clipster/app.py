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
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    cookies_are_configured,
    extract_youtube_url,
    user_facing_ytdlp_error,
)
from .discover import (
    DiscoverOutcome,
    DiscoverTrack,
    discover_tracks,
    resolve_discover_seeds,
    seed_from_track,
)
from .discover_taste import DiscoverTaste, VOTE_DOWN
from .gui import Gui
from .history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, History, HistoryEntry, format_size
from .i18n import Messages
from .logging_setup import get_logger
from .terms import (
    accept_app_terms,
    accept_streaming_terms,
    app_terms_accepted,
    streaming_terms_accepted,
)
from .tray import TrayIcon
from . import updater

log = get_logger(__name__)

#: The download started right away.
SUBMIT_STARTED = "started"
#: Accepted, but waiting behind the running downloads.
SUBMIT_QUEUED = "queued"
#: This URL is downloading right now.
SUBMIT_RUNNING = "running"
#: This URL is already on the waiting list.
SUBMIT_WAITING = "waiting"
#: The waiting list is full, the link was dropped.
SUBMIT_FULL = "full"
#: The file exists already; nothing was started.
SUBMIT_EXISTS = "exists"
#: Not a YouTube link.
SUBMIT_INVALID = "invalid"
#: Not a format this program produces.
SUBMIT_FORMAT = "format"
#: The program is shutting down and takes nothing new.
SUBMIT_CLOSING = "closing"

#: The formats a submission may ask for.
MEDIA_FORMATS = ("mp3", "mp4")


@dataclass(frozen=True)
class SubmitResult:
    """What became of a download request that did not come from the clipboard.

    Returned to callers that owe somebody an answer - the remote interface has
    to turn this into an HTTP status, so "it was ignored" is not good enough.
    """

    #: One of the ``SUBMIT_*`` constants.
    state: str
    #: The canonical URL, empty when it could not be recognised.
    url: str = ""
    #: The matching history entry, set for :data:`SUBMIT_EXISTS`.
    entry_id: str = ""
    #: Number of links waiting, set for :data:`SUBMIT_QUEUED`.
    position: int = 0

    @property
    def accepted(self) -> bool:
        """Return ``True`` when a download was started or queued."""
        return self.state in (SUBMIT_STARTED, SUBMIT_QUEUED)


def internet_available(*, timeout: float = 1.0) -> bool:
    """Return whether a brief TCP probe to a public DNS host succeeds.

    Used to skip auto-Discover when the machine is clearly offline. Failures
    are treated as offline — callers must not raise.
    """
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((host, 53), timeout=timeout):
                return True
        except OSError:
            continue
    return False


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
        self.taste = DiscoverTaste().load()
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
        #: Latest progress per running URL. Written by the download threads and
        #: read by the remote interface from its own thread, hence the lock.
        self._progress: Dict[str, Dict[str, Any]] = {}
        self._progress_lock = threading.Lock()
        #: The remote interface, created on demand in :meth:`run`.
        self._remote: Any = None

        self.gui.on_quit = self.request_quit
        self.gui.on_nav_closed = self._nav_closed
        self.gui.on_view_closed = self._view_closed
        self.gui.on_play_entry = self._play_entry
        self.gui.on_delete_entry = self._delete_entry
        self.gui.on_hide_entry = self._hide_entry
        self.gui.on_reveal_entry = self._reveal_entry
        self.gui.on_clear_history = self._clear_history
        self.gui.on_open_folder = self._open_download_folder
        self.gui.on_submit_url = self._submit_url
        self.gui.on_save_settings = self._save_settings
        self.gui.on_check_updates = self._check_updates
        self.gui.on_install_update = self._install_update
        self.gui.on_open_result = self._open_result
        self.gui.on_reveal_result = self._reveal_result
        self.gui.on_discover_refresh = self._discover_refresh
        self.gui.on_discover_download = self._discover_download
        self.gui.on_discover_extend = self._discover_extend
        self.gui.on_discover_like = self._discover_like
        self.gui.on_discover_dislike = self._discover_dislike
        self.gui.on_show_terms = self._show_terms
        self.gui.on_phone_apply = self.apply_remote_settings
        self.gui.on_phone_new_token = self.regenerate_remote_token
        self.gui.on_phone_state = self.remote_state
        self.gui.build_windows()
        if self.gui.view is not None and self.gui.view.discover is not None:
            self.gui.view.discover.player.set_options_provider(self.downloader._base_options)
            self.gui.view.discover.ensure_terms = self._ensure_streaming_terms

        self._discover_cancel = threading.Event()
        self._discover_busy = False
        self._discover_extending = False
        #: Auto Find-Similar already ran (or was skipped) for this process.
        self._auto_discover_done = False
        #: Tk ``after`` id for the deferred auto Discover start, if any.
        self._auto_discover_job: Optional[str] = None
        #: Ignore tray "show" callbacks until startup visibility has been applied
        #: (some backends fire activate while the icon is created).
        self._tray_show_armed = False

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
        self._tray_show_armed = True

        # Ignore whatever is already in the clipboard at startup.
        self._last_seen = self.clipboard.read()

        log.info("%s", self.messages["separator"])
        log.info("%s", APP_TITLE)
        log.info("%s", self.messages["separator"])
        log.info("%s", self.messages["started"])
        log.info("Download folder: %s", self.download_dir)
        log.info("Download history: %s entries (%s)", len(self.history), self.history.path)
        self.start_remote()

        self.gui.root.after(200, self._post_start)
        self.gui.root.after(self.config.poll_interval_ms(), self._poll_clipboard)

        try:
            self.gui.root.mainloop()
        except KeyboardInterrupt:  # pragma: no cover - console interrupt
            log.info("Interrupted.")
        finally:
            self._shutdown()
        return 0

    # ------------------------------------------------------------------
    # The phone interface
    # ------------------------------------------------------------------
    def start_remote(self) -> bool:
        """Start the phone interface, if the user switched it on.

        Imported here and not at the top of the module: somebody who never turns
        this on should not pay for the import, and the program has to keep
        starting even if the interface cannot.

        :return: ``True`` when it is listening.
        """
        if not self.config.remote_enabled or self._remote is not None:
            return False
        from .webapi import RemoteApi
        from .webserver import LOOPBACK_ADDRESSES, RemoteServer, new_token

        if not self.config.remote_token:
            # Without a secret anybody on the network could start downloads.
            self.config.remote_token = new_token()
            self.config.save()
            log.info("A new token for the phone interface was generated.")
        server = RemoteServer(
            RemoteApi(self),
            token=self.config.remote_token,
            bind=self.config.remote_bind,
            port=self.config.remote_port,
        )
        if not server.start():
            return False
        self._remote = server
        # The complete address including the token: the token only lives in
        # config.json, so without this line nobody can work out what to type.
        log.info("Open this on your phone: %s", self.remote_url())
        if self.config.remote_bind in LOOPBACK_ADDRESSES:
            log.info('Only this machine can reach it - set "remote_bind" to '
                     '"0.0.0.0" in %s to let a phone in.', self.config.path)
        return True

    def stop_remote(self) -> None:
        """Stop the phone interface if it is running."""
        if self._remote is None:
            return
        self._remote.stop()
        self._remote = None
        log.info("The phone interface was stopped.")

    def remote_state(self) -> Dict[str, Any]:
        """Describe the phone interface for the Phone page.

        Everything the page needs in one call, so it can be polled cheaply
        without reaching into the server.

        :return: Settings, whether it listens, the address and the last contact.
        """
        state: Dict[str, Any] = {
            "enabled": self.config.remote_enabled,
            "bind": self.config.remote_bind,
            "port": self.config.remote_port,
            "token": self.config.remote_token,
            "running": self._remote is not None and self._remote.running,
            "url": self.remote_url(),
            "contacts": 0,
            "last_contact": "",
        }
        if self._remote is not None:
            state["port"] = self._remote.port
            api = getattr(self._remote, "_api", None)
            if api is not None and hasattr(api, "contact_info"):
                state.update(api.contact_info())
        return state

    def apply_remote_settings(self, enabled: bool, bind: str, port: int) -> Dict[str, Any]:
        """Save the phone interface settings and restart it accordingly.

        :param enabled: Whether the interface should serve.
        :param bind: The interface to listen on.
        :param port: The TCP port to listen on.
        :return: The new :meth:`remote_state`.
        """
        from .webserver import new_token

        self.config.remote_enabled = bool(enabled)
        self.config.remote_bind = bind
        self.config.remote_port = int(port)
        if enabled and not self.config.remote_token:
            self.config.remote_token = new_token()
        self.config.save()

        # Always stop first: bind address and port may both have changed, and a
        # running server cannot be reconfigured in place.
        self.stop_remote()
        if self.config.remote_enabled:
            self.start_remote()
        return self.remote_state()

    def regenerate_remote_token(self) -> Dict[str, Any]:
        """Replace the token, which locks out every phone paired so far.

        :return: The new :meth:`remote_state`.
        """
        from .webserver import new_token

        self.config.remote_token = new_token()
        self.config.save()
        log.info("A new token for the phone interface was generated.")
        was_running = self._remote is not None
        self.stop_remote()
        if was_running or self.config.remote_enabled:
            self.start_remote()
        return self.remote_state()

    def remote_url(self) -> str:
        """Return the address a phone can open, token included.

        Meant for display on the PC - the log prints it, and the view window
        will show it as a QR code.  While the server is bound to loopback the
        network address is *not* returned: it would look inviting and then
        refuse every connection.

        :return: The URL, or an empty string while the interface is off.
        """
        if self._remote is None:
            return ""
        from .webserver import phone_url

        return phone_url(self.config.remote_bind, self._remote.port, self.config.remote_token)

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
        program, so it is forced visible in that case.  Tray + start_minimized
        must not deiconify the Streaming/view window — only the tray icon
        (and an optional toast) belong on screen.
        """
        if self._tray_active:
            if self.config.start_minimized:
                self.gui.hide_view()
                log.info("Started in the system tray.")
                return
            self.gui.show_view()
            return

        if self.config.use_tray:
            log.warning("No system tray available - showing the view window instead.")
        elif self.config.start_minimized:
            log.warning("Without a tray icon the window is the only way to quit - showing it.")
        self.gui.show_view()

    def _keep_tray_start_hidden(self) -> None:
        """Re-hide the view after modal startup dialogs when tray-start is on."""
        if self._tray_active and self.config.start_minimized:
            self.gui.hide_view()

    def _post_start(self) -> None:
        """Run the one-off tasks that need a live event loop."""
        if not self._ensure_app_terms():
            return
        # Terms (or other modals) must not leave Streaming open behind them.
        self._keep_tray_start_hidden()
        # No OS balloon / toast for "started" — especially noisy when tray-minimized.
        self._sync_autostart()
        self._maybe_offer_desktop_shortcut()
        self._keep_tray_start_hidden()
        if self.config.check_updates and updater.due(self.config.update_check_hours):
            self._check_updates(announce=False)
        # Auto Find Similar only when Streaming terms were already accepted —
        # never force the Streaming terms modal at tray boot.
        self._maybe_schedule_auto_discover()

    def _ensure_app_terms(self) -> bool:
        """Require acceptance of the general terms before normal startup continues."""
        if app_terms_accepted(self.config):
            return True
        accepted = self.gui.ask_terms_acceptance(
            title_key="terms_app_title",
            body_key="terms_app_body",
        )
        if not accepted:
            log.info("User declined the general terms of use.")
            self.request_quit()
            return False
        accept_app_terms(self.config)
        log.info("User accepted the general terms of use (v%s).", self.config.terms_app_version)
        return True

    def _ensure_streaming_terms(self) -> bool:
        """Require Streaming-specific terms before Discover actions run."""
        if streaming_terms_accepted(self.config):
            return True
        accepted = self.gui.ask_terms_acceptance(
            title_key="terms_streaming_title",
            body_key="terms_streaming_body",
        )
        if not accepted:
            log.info("User declined the Streaming terms of use.")
            page = self.gui.view.discover if self.gui.view is not None else None
            if page is not None:
                page.set_status(self.messages["terms_streaming_declined"], "warn")
            else:
                self.gui.toast(self.messages["terms_streaming_declined"])
            return False
        accept_streaming_terms(self.config)
        log.info(
            "User accepted the Streaming terms of use (v%s).",
            self.config.terms_streaming_version,
        )
        # First acceptance outside an in-flight refresh: queue auto Discover once.
        # Defer so a concurrent Find Similar can claim the slot first.
        try:
            self.gui.root.after(0, self._maybe_schedule_auto_discover)
        except Exception:
            pass
        return True

    def _maybe_schedule_auto_discover(self) -> None:
        """Schedule a one-shot auto Find Similar when Streaming terms are ready."""
        if self._auto_discover_done or self._discover_busy or self._quitting:
            return
        if not streaming_terms_accepted(self.config):
            return
        self._auto_discover_done = True
        try:
            self._auto_discover_job = self.gui.root.after(400, self._run_auto_discover)
        except Exception:
            self._auto_discover_done = False
            self._auto_discover_job = None

    def _cancel_auto_discover_job(self) -> None:
        """Drop a pending auto-Discover timer so teardown cannot fire it."""
        job = self._auto_discover_job
        self._auto_discover_job = None
        if not job:
            return
        try:
            self.gui.root.after_cancel(job)
        except Exception:
            pass

    def _run_auto_discover(self) -> None:
        """Start Discover in the background without forcing the view open."""
        self._auto_discover_job = None
        if self._quitting or self._discover_busy:
            return
        if not streaming_terms_accepted(self.config):
            return
        if not internet_available():
            log.info("Skipping auto Discover — no network.")
            return
        # Do not show the view when tray-minimized; refresh updates the page quietly.
        self._discover_refresh(require_terms=False)

    def _show_terms(self) -> None:
        """Open the combined terms documents from the About page."""
        self.gui.show_terms_document(
            title_key="terms_app_title",
            body_keys=("terms_app_body", "terms_streaming_body"),
        )

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
        self._discover_cancel.set()
        self._cancel_auto_discover_job()
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
        self._discover_cancel.set()
        self._cancel_auto_discover_job()
        self._cancel_all()
        # Before the bridge stops: a request still in flight has to be able to
        # get its answer, rather than blocking on a bridge that is already gone.
        self.stop_remote()
        if self.gui.view is not None and self.gui.view.discover is not None:
            self.gui.view.discover.destroy_player()
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
        if not self._tray_show_armed:
            log.debug("Ignoring tray show before startup visibility is ready.")
            return
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

    def submit_remote(self, url: str, media_format: str, force: bool = False) -> SubmitResult:
        """Take a download request from outside the program.

        Everything below touches the navigation window and the shared
        ``_forced_format`` state, which only the GUI thread may do - so the call
        marshals itself instead of trusting every future caller to remember
        that.  Two requests arriving at once therefore cannot race.

        A remote caller is never asked anything, so the format has to come with
        the request; the audio track is resolved by :meth:`_auto_language`, the
        same way the view window toolbar does it.

        :param url: The URL as it arrived, not necessarily canonical.
        :param media_format: ``mp3`` or ``mp4``.
        :param force: Download again even when the file is already there.
        :return: What happened, ready to be turned into an answer.
        :raises RuntimeError: When the GUI bridge is no longer running.
        """
        if not self.bridge.on_gui_thread():
            return self.bridge.call(self.submit_remote, url, media_format, force)
        if self._quitting:
            return SubmitResult(SUBMIT_CLOSING)
        if media_format not in MEDIA_FORMATS:
            return SubmitResult(SUBMIT_FORMAT)
        target = extract_youtube_url(url)
        if not target:
            return SubmitResult(SUBMIT_INVALID)
        if not force:
            existing = self.history.find_download(target, media_format)
            if existing is not None:
                # Answered right away rather than starting a run that would end
                # in the "already downloaded" dialog on the desktop, which
                # nobody sitting at the phone can see.
                return SubmitResult(SUBMIT_EXISTS, url=target, entry_id=existing.identifier())
        log.info("Download requested remotely: %s as %s", target, media_format)
        state = self._enqueue(target, media_format, force=force)
        return SubmitResult(state, url=target, position=len(self._queue))

    def _discover_refresh(self, *, require_terms: bool = True) -> None:
        """Search for related songs on a worker thread.

        Seeds are resolved on the worker (history → likes → download folder →
        bounded disk scan) so a disk walk never freezes the UI.
        """
        if require_terms and not self._ensure_streaming_terms():
            return
        if self._discover_busy:
            return
        view = self.gui.view
        page = view.discover if view is not None else None
        if page is None:
            return
        self._auto_discover_done = True
        mode = page.selected_mode()
        self.config.discover_mode = mode
        self.config.save()
        self._discover_cancel.clear()
        self._discover_busy = True
        page.set_busy(True, self.messages["discover_loading"])
        page.begin_discover()
        exclude = set(self.taste.excluded_ids()) | page.video_ids()
        history_snapshot = list(self.history.entries)
        liked_snapshot = self.taste.liked_seeds()
        download_dir = self.download_dir
        min_seeds = self.config.discover_min_folder_seeds
        disk_scan = bool(self.config.discover_disk_scan_enabled)

        def on_progress(current: int, total: int, title: str) -> None:
            self.bridge.post(page.show_progress, current, total, title)

        def on_batch(tracks: List[DiscoverTrack]) -> None:
            self.bridge.post(self._discover_batch, list(tracks))

        def worker() -> None:
            try:
                seeds, _source = resolve_discover_seeds(
                    history_snapshot,
                    download_dir,
                    liked_entries=liked_snapshot,
                    min_folder_seeds=min_seeds,
                    disk_scan_enabled=disk_scan,
                )
                seeds = self.taste.merge_seeds(seeds, prefer_liked=False)
                if not seeds:
                    self.bridge.post(self._discover_no_seeds)
                    return
                outcome = discover_tracks(
                    seeds,
                    self.config,
                    mode=mode,
                    base_options=self.downloader._base_options(),
                    cancel_check=self._discover_cancel.is_set,
                    progress=on_progress,
                    on_batch=on_batch,
                    exclude_ids=exclude,
                )
            except Exception as exc:
                log.exception("Discover search failed")
                self.bridge.post(self._discover_failed, str(exc))
                return
            self.bridge.post(self._discover_ready, outcome)

        threading.Thread(target=worker, name="clipster-discover", daemon=True).start()

    def _discover_no_seeds(self) -> None:
        """Show the empty-seeds state after a background seed resolve."""
        self._discover_busy = False
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is None:
            return
        page.set_busy(False)
        page.show_empty("discover_no_seeds")

    def _discover_batch(self, tracks: List[DiscoverTrack]) -> None:
        """Append tracks as soon as a seed batch arrives during Find Similar."""
        if not self._discover_busy:
            return
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is None or not tracks:
            return
        filtered = self.taste.filter_tracks(tracks)
        if not filtered:
            return
        page.append_tracks(filtered, update_status=False)

    def _discover_ready(self, outcome: DiscoverOutcome) -> None:
        """Show Discover results and status on the UI thread."""
        self._discover_busy = False
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is None:
            return
        outcome.tracks = self.taste.filter_tracks(outcome.tracks)
        # Keep rows already streamed in; only append anything still missing
        # (e.g. suffix-relaxed fallback that arrives at the end).
        known = page.video_ids()
        missing = [track for track in outcome.tracks if track.video_id not in known]
        if missing:
            page.append_tracks(missing, update_status=False)
        queue_count = len(page._tracks) if page._tracks else len(outcome.tracks)
        status, level = self._discover_status_text(outcome, queue_count=queue_count)
        page.finish_discover(status=status, level=level)

    def _discover_like(self, track: DiscoverTrack) -> None:
        """Remember a thumbs-up and load more songs like this one."""
        self.taste.like(track)
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is not None:
            page.set_status(self.messages["discover_liked"], "ok")
        if self._discover_busy or self._discover_extending:
            return
        self._discover_extend(track)

    def _discover_dislike(self, track: DiscoverTrack) -> None:
        """Remember a thumbs-down, drop the track, and skip ahead."""
        self.taste.dislike(track)
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is None:
            return
        # Drop near-duplicates first so play_next does not land on another dislike.
        for item in list(page._tracks):
            if item.video_id and item.video_id != track.video_id and self.taste.is_blocked(item):
                page.remove_track(item.video_id, play_next=False)
        page.remove_track(track.video_id, play_next=True)
        page.set_status(self.messages["discover_disliked"], "info")

    def _discover_extend(self, track: DiscoverTrack) -> None:
        """Fetch more related songs from ``track`` and append them to the list."""
        if self._discover_busy or self._discover_extending:
            # Keep _extend_requested / resume-after-extend so a later finish can continue.
            return
        view = self.gui.view
        page = view.discover if view is not None else None
        if page is None:
            return
        if self.taste.vote_for(track.video_id) == VOTE_DOWN:
            page.mark_extend_idle()
            return
        mode = page.selected_mode()
        exclude = page.video_ids() | self.taste.excluded_ids()
        batch = max(1, int(self.config.discover_extend_count))
        self._discover_extending = True

        def worker() -> None:
            try:
                outcome = discover_tracks(
                    [seed_from_track(track)],
                    self.config,
                    mode=mode,
                    base_options=self.downloader._base_options(),
                    cancel_check=self._discover_cancel.is_set,
                    exclude_ids=exclude,
                    limit=batch,
                )
            except Exception as exc:
                log.exception("Discover extend failed")
                self.bridge.post(self._discover_extend_failed, str(exc))
                return
            self.bridge.post(self._discover_extend_ready, outcome)

        threading.Thread(target=worker, name="clipster-discover-extend", daemon=True).start()

    def _discover_extend_ready(self, outcome: DiscoverOutcome) -> None:
        """Append topped-up Discover tracks on the UI thread."""
        self._discover_extending = False
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is None:
            return
        page.set_loading(False)
        outcome.tracks = self.taste.filter_tracks(outcome.tracks)
        if outcome.blocked and not outcome.tracks:
            if outcome.error_summary:
                log.warning("Discover extend blocked by YouTube: %s", outcome.error_summary)
            page.mark_extend_idle()
            page.set_status(self.messages["discover_blocked"], "error")
            return
        added = page.append_tracks(outcome.tracks)
        if added:
            page.set_status(
                self.messages.format(
                    "discover_extended",
                    added=added,
                    count=len(page._tracks),
                ),
                "ok",
            )
        elif outcome.blocked:
            page.set_status(self.messages["discover_blocked_partial"], "warn")
        else:
            page.mark_extend_idle()
            page.set_status(self.messages["discover_extend_empty"], "warn")

    def _discover_extend_failed(self, details: str) -> None:
        """Show a Discover top-up error on the UI thread."""
        self._discover_extending = False
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is not None:
            page.mark_extend_idle()
            page.set_loading(False)
            page.set_status(
                user_facing_ytdlp_error(
                    details,
                    self.messages,
                    cookies_configured=cookies_are_configured(self.config),
                    context="discover",
                ),
                "error",
            )

    def _discover_status_text(
        self,
        outcome: DiscoverOutcome,
        *,
        queue_count: Optional[int] = None,
    ) -> Tuple[str, str]:
        """Build the Status-box message for a finished Discover run.

        :param outcome: Finished Discover search result.
        :param queue_count: Optional live queue size when batches already filled the list.
        """
        count = len(outcome.tracks) if queue_count is None else max(0, int(queue_count))
        has_tracks = count > 0 or bool(outcome.tracks)
        cookies_on = cookies_are_configured(self.config)
        if outcome.blocked and not has_tracks:
            if outcome.error_summary:
                log.warning("Discover blocked by YouTube: %s", outcome.error_summary)
            key = "discover_blocked_with_cookies" if cookies_on else "discover_blocked"
            return self.messages[key], "error"
        if outcome.blocked and has_tracks:
            base = self.messages.format("discover_results", count=count or len(outcome.tracks))
            return "{0} — {1}".format(base, self.messages["discover_blocked_partial"]), "warn"
        genre_note = ""
        if outcome.detected_genres:
            genre_note = self.messages.format(
                "discover_genres_detected",
                genres=", ".join(outcome.detected_genres),
            )
        if outcome.suffix_relaxed and has_tracks:
            base = self.messages.format("discover_results", count=count or len(outcome.tracks))
            ending = self.config.discover_search_suffix.strip() or "lyrics"
            note = self.messages.format("discover_suffix_relaxed", suffix=ending)
            parts = [base, note]
            if genre_note:
                parts.append(genre_note)
            return " — ".join(parts), "warn"
        if has_tracks:
            base = self.messages.format("discover_results", count=count or len(outcome.tracks))
            parts = [base]
            if genre_note:
                parts.append(genre_note)
            if outcome.genre_adapted:
                parts.append(self.messages["discover_genre_adapted"])
            return " — ".join(parts), "ok"
        if outcome.raw_hits and self.config.discover_require_suffix:
            ending = self.config.discover_search_suffix.strip() or "lyrics"
            return self.messages.format("discover_filtered_empty", suffix=ending, count=outcome.raw_hits), "warn"
        if outcome.warnings:
            detail = outcome.error_summary or outcome.warnings[0]
            return (
                user_facing_ytdlp_error(
                    detail,
                    self.messages,
                    cookies_configured=cookies_on,
                    context="discover",
                ),
                "error",
            )
        if outcome.canceled:
            return self.messages["discover_canceled"], "warn"
        return self.messages["discover_empty"], "warn"

    def _discover_failed(self, details: str) -> None:
        """Show a Discover error on the UI thread."""
        self._discover_busy = False
        page = self.gui.view.discover if self.gui.view is not None else None
        if page is not None:
            page.show_error(details)

    def _discover_download(self, track: DiscoverTrack) -> None:
        """Queue an automatic download with the configured defaults.

        Format and language dialogs are skipped: the default format from Settings
        is used and the original / best audio track is chosen automatically.
        """
        if not self._ensure_streaming_terms():
            return
        target = extract_youtube_url(track.url)
        if not target:
            self.gui.show_error(self.messages["error_title"], self.messages["error_not_a_link"])
            return
        media_format = self.config.default_format if self.config.default_format in ("mp3", "mp4") else "mp3"
        log.info("Discover auto-download: %s (%s)", track.title, media_format)
        # Stay on Streaming — do not jump to the Downloads tab.
        self.gui.show_view("discover")
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

    def _remove_entry_and_file(self, entry: HistoryEntry) -> str:
        """Delete the downloaded file and drop the row from the list.

        An entry whose file is already gone is simply removed - that is the only
        way to clear a failed attempt out of the list.

        :param entry: The entry to get rid of.
        :return: An error description, empty when it worked.
        """
        target = entry.file_path()
        if target is not None:
            try:
                target.unlink()
                log.info("Deleted %s", target)
            except OSError as exc:
                log.error("Could not delete %s: %s", target, exc)
                return str(exc)
        self.history.remove(entry)
        self.gui.render_history(self.history.entries)
        return ""

    def _delete_entry(self, entry: HistoryEntry) -> None:
        """Delete a row on request of the view window, reporting in a dialog.

        :param entry: The entry the user wants gone.
        :return: None
        """
        problem = self._remove_entry_and_file(entry)
        if problem:
            self.gui.show_error(
                self.messages["error_title"],
                self.messages.format("history_delete_failed", details=problem),
            )

    def delete_remote(self, entry: HistoryEntry) -> bool:
        """Delete a row on request of the remote interface.

        Marshals itself like :meth:`submit_remote`, and reports a failure as a
        return value instead of a dialog: nobody is sitting at the PC to close
        one, and the phone needs the answer.

        :param entry: The entry to get rid of.
        :return: ``True`` when the file and the row are gone.
        :raises RuntimeError: When the GUI bridge is no longer running.
        """
        if not self.bridge.on_gui_thread():
            return bool(self.bridge.call(self.delete_remote, entry))
        return not self._remove_entry_and_file(entry)

    def _hide_entry(self, entry: HistoryEntry) -> None:
        """Remove ``entry`` from the Downloads list but keep the file on disk.

        :param entry: The row to hide.
        :return: None
        """
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

    def _enqueue(self, url: str, media_format: str, force: bool = False) -> str:
        """Start the download, or line it up behind the running one.

        :param url: The canonical YouTube URL.
        :param media_format: Forced format, empty to ask the user.
        :param force: Download again even when the file is already there.
        :return: One of the ``SUBMIT_*`` states, so callers that have to answer
            somebody - the remote interface - can say what actually happened.
        """
        if not self._busy:
            self._forced_format = media_format
            self._force_redownload = force
            self._start_worker(url)
            return SUBMIT_STARTED
        if url in self._active and not force:
            log.debug("%s is downloading right now - ignored.", url)
            return SUBMIT_RUNNING
        if any(queued == url for queued, _, _ in self._queue):
            log.debug("%s is already waiting - ignored.", url)
            return SUBMIT_WAITING
        if len(self._queue) >= self.MAX_QUEUE:
            log.warning("The waiting list is full (%s); %s was dropped.", self.MAX_QUEUE, url)
            return SUBMIT_FULL
        # The flag travels with the entry, otherwise a deliberate "download
        # again" would be met with "already downloaded" once its turn comes.
        self._queue.append((url, media_format, force))
        log.info("A download is running - %s links are now waiting.", len(self._queue))
        self._set_status(self.messages.format("status_queued", count=len(self._queue)))
        return SUBMIT_QUEUED

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
        with self._progress_lock:
            self._progress.pop(url, None)
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
                log.error("Video information could not be loaded: %s", exc)
                message = user_facing_ytdlp_error(
                    str(exc),
                    self.messages,
                    cookies_configured=cookies_are_configured(self.config),
                    context="metadata",
                )
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

    def _record_progress(self, url: str, title: str, media_format: str, progress: Progress) -> None:
        """Remember the latest progress of one run for the remote interface.

        Called from the download thread; the remote interface reads the result
        from its own thread, so the dictionary is guarded.

        :param url: The URL being downloaded.
        :param title: The video title.
        :param media_format: ``mp3`` or ``mp4``.
        :param progress: The update just received from the downloader.
        :return: None
        """
        with self._progress_lock:
            self._progress[url] = {
                "url": url,
                "title": title,
                "format": media_format,
                "phase": progress.phase,
                "percent": progress.percent,
                "detail": progress.detail,
            }

    def remote_status(self) -> Dict[str, Any]:
        """Return what is happening right now, for the remote interface.

        Deliberately readable without the GUI bridge: the phone polls this every
        couple of seconds, and routing that through the Tk queue would put
        needless load on the event loop.

        :return: ``{"active": [...], "queued": int, "parallel": int}``
        """
        with self._progress_lock:
            active = [dict(item) for url, item in self._progress.items() if url in self._active]
        return {
            "active": active,
            "queued": len(self._queue),
            "parallel": self._parallel_limit(),
        }

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
            self._record_progress(url, info.title, media_format, progress)

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
            # Keep Streaming visible when the download was started from there.
            page = "downloads"
            if self.gui.view is not None and self.gui.view.current_page == "discover":
                page = "discover"
            self.bridge.post(self.gui.show_view, page)

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
        return user_facing_ytdlp_error(
            str(error),
            self.messages,
            cookies_configured=cookies_are_configured(self.config),
            context="download",
        )

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
