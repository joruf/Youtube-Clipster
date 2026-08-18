"""Running without a graphical interface, driven by the remote interface.

Made for a machine that has no screen to speak of: a server, a Raspberry Pi, or
Android through Termux. The download pipeline, the history and the remote
interface all work unchanged - what falls away are the two windows and the tray.

Two substitutes make that possible, without touching the hundred places in
:mod:`clipster.app` that reach for the interface:

* :class:`HeadlessRoot` provides the ``after`` / ``after_cancel`` / ``mainloop``
  that Tk's root would, so the existing :class:`~clipster.bridge.TkBridge` and
  every scheduled callback keep working. Callbacks run on one thread, exactly as
  they do inside Tk's event loop, so the code that relies on that stays correct.
* :class:`HeadlessGui` answers every call the application makes to its interface
  and does nothing visible with it. ``view`` is ``None``, which the application
  already handles, and the navigation window becomes a recorder whose state the
  remote interface can read.

What genuinely cannot work here is the interactive question for format and audio
track - there is nobody to ask. Headless downloads therefore always carry their
format with them, which is exactly what a request from a phone does anyway.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .logging_setup import get_logger

log = get_logger(__name__)

#: How long the loop sleeps when nothing is due, in seconds.
_IDLE_TICK = 0.05


class HeadlessRoot:
    """A stand-in for the Tk root: timers and one event loop, no window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: List[Tuple[float, int, Callable[[], None]]] = []
        self._counter = itertools.count()
        self._cancelled: set = set()
        self._running = False
        self._wake = threading.Event()
        #: The thread the loop runs on; callbacks are only ever run there.
        self.loop_thread: Optional[int] = None

    # ------------------------------------------------------------------
    # The parts of Tk's root the application uses
    # ------------------------------------------------------------------
    def after(self, delay_ms: int, callback: Optional[Callable[..., Any]] = None,
              *args: Any) -> str:
        """Schedule ``callback`` in ``delay_ms`` milliseconds.

        :param delay_ms: Delay in milliseconds.
        :param callback: What to run; ``None`` only sleeps, like Tk's ``after``.
        :param args: Positional arguments for the callback.
        :return: A handle for :meth:`after_cancel`.
        """
        handle = "after#{0}".format(next(self._counter))
        if callback is None:
            return handle
        due = time.monotonic() + max(0, int(delay_ms)) / 1000.0
        with self._lock:
            heapq.heappush(self._queue, (due, int(handle.split("#")[1]),
                                         lambda: callback(*args)))
        self._wake.set()
        return handle

    def after_cancel(self, handle: str) -> None:
        """Drop a scheduled callback.

        :param handle: What :meth:`after` returned.
        :return: None
        """
        if not handle:
            return
        try:
            number = int(str(handle).split("#")[1])
        except (IndexError, ValueError):
            return
        with self._lock:
            self._cancelled.add(number)

    def mainloop(self) -> None:
        """Run scheduled callbacks until :meth:`quit` is called."""
        self._running = True
        self.loop_thread = threading.get_ident()
        log.info("Running without a graphical interface.")
        while self._running:
            wait = self._run_due()
            if not self._running:
                break
            self._wake.wait(timeout=wait)
            self._wake.clear()

    def quit(self) -> None:
        """Stop the loop after the current callback."""
        self._running = False
        self._wake.set()

    # ------------------------------------------------------------------
    def _run_due(self) -> float:
        """Run everything that is due and return how long to wait next.

        :return: Seconds until the next callback, or the idle tick.
        """
        while True:
            with self._lock:
                if not self._queue:
                    return _IDLE_TICK
                due, number, callback = self._queue[0]
                if number in self._cancelled:
                    heapq.heappop(self._queue)
                    self._cancelled.discard(number)
                    continue
                remaining = due - time.monotonic()
                if remaining > 0:
                    return min(remaining, _IDLE_TICK)
                heapq.heappop(self._queue)
            try:
                callback()
            except Exception:  # pragma: no cover - one bad callback must not stop the loop
                log.exception("A scheduled callback failed")


class HeadlessNav:
    """Records what the navigation window would have shown.

    The remote interface reads the progress out of the application's own
    snapshot, so nothing here has to be displayed - but the values are kept, so a
    log line or a future caller can see where a download stands.
    """

    def __init__(self) -> None:
        #: Set by the application to abort a download; never set from here.
        self.cancel_event = threading.Event()
        self.headline = ""
        self.status = ""
        self.detail = ""
        self.percent: Optional[float] = None
        self.phase = ""
        self.result: Optional[Any] = None

    def begin(self, text: str) -> None:
        """A link arrived and is being looked at."""
        self.cancel_event = threading.Event()
        self.headline = text
        self.status = text
        self.percent = None
        self.result = None

    def ask(self, prompt: Any, title: str, duration: int, languages: Any,
            default_format: str, ask_language: bool, original: str) -> None:
        """Nobody can answer here, so the question is declined at once.

        The application then cancels that run. A headless download has to arrive
        with its format already chosen - which is what a request from a phone
        does.
        """
        log.warning("A download needs a format but nothing can ask here: %s", title)
        try:
            prompt.cancel()
        except Exception:  # pragma: no cover - defensive
            log.debug("The prompt could not be cancelled", exc_info=True)

    def set_headline(self, title: str) -> None:
        """Remember the title being worked on."""
        self.headline = title

    def show_progress(self, media_format: str, duration: int) -> None:
        """Remember that the download itself started."""
        self.phase = media_format

    def set_status(self, text: str, detail: str = "") -> None:
        """Remember the current phase text."""
        self.status = text
        self.detail = detail

    def set_percent(self, percent: Optional[float]) -> None:
        """Remember the current percentage."""
        self.percent = percent

    def finish(self, text: str, status: str) -> None:
        """Remember the outcome."""
        self.status = text
        self.phase = status
        log.info("Download finished: %s (%s)", text, status)

    def already_downloaded(self, title: str, path: Any, detail: str,
                           on_again: Callable[[], None]) -> None:
        """Note that the file was there already; the offer is not taken."""
        self.status = detail
        self.result = path
        log.info("Already downloaded: %s", title)

    def result_path(self) -> Optional[Any]:
        """Return the file of the last finished download, when known."""
        return self.result

    def hide(self) -> None:
        """Nothing is on screen, so nothing has to be hidden."""


class HeadlessGui:
    """Answers everything the application asks of its interface, silently."""

    def __init__(self, messages: Any, config: Any, download_dir: Any,
                 accept_terms: bool = False) -> None:
        """
        :param messages: The active translation table.
        :param config: The live configuration.
        :param download_dir: The download directory.
        :param accept_terms: Answer a terms question with yes. Only ever set from
            an explicit command line switch - a legal confirmation is not
            something a program may give on the user's behalf.
        """
        self.messages = messages
        self.config = config
        self.download_dir = download_dir
        self.root = HeadlessRoot()
        self.nav = HeadlessNav()
        #: There is no view window; the application already handles ``None``.
        self.view = None
        self._accept_terms = bool(accept_terms)
        self._entries: List[Any] = []

        # The callbacks the application installs; unused without windows, but it
        # sets them unconditionally.
        self.on_quit: Optional[Callable[[], None]] = None
        self.on_nav_closed: Optional[Callable[[], None]] = None
        self.on_view_closed: Optional[Callable[[], None]] = None
        self.on_play_entry: Optional[Callable[..., None]] = None
        self.on_delete_entry: Optional[Callable[..., None]] = None
        self.on_hide_entry: Optional[Callable[..., None]] = None
        self.on_retry_entry: Optional[Callable[..., None]] = None
        self.on_reveal_entry: Optional[Callable[..., None]] = None
        self.on_clear_history: Optional[Callable[[], None]] = None
        self.on_open_folder: Optional[Callable[[], None]] = None
        self.on_submit_url: Optional[Callable[..., None]] = None
        self.on_save_settings: Optional[Callable[[], None]] = None
        self.on_check_updates: Optional[Callable[[], None]] = None
        self.on_install_update: Optional[Callable[[], None]] = None
        self.on_open_result: Optional[Callable[[], None]] = None
        self.on_reveal_result: Optional[Callable[[], None]] = None
        self.on_discover_refresh: Optional[Callable[[], None]] = None
        self.on_discover_download: Optional[Callable[..., None]] = None
        self.on_discover_extend: Optional[Callable[..., None]] = None
        self.on_discover_like: Optional[Callable[..., None]] = None
        self.on_discover_dislike: Optional[Callable[..., None]] = None
        self.on_show_terms: Optional[Callable[[], None]] = None
        self.on_phone_apply: Optional[Callable[..., dict]] = None
        self.on_phone_new_token: Optional[Callable[[], dict]] = None
        self.on_phone_state: Optional[Callable[[], dict]] = None

    # ------------------------------------------------------------------
    def build_windows(self) -> None:
        """There are no windows to build."""

    def destroy(self) -> None:
        """Stop the event loop; there is nothing else to tear down."""
        self.root.quit()

    def render_history(self, entries: List[Any]) -> None:
        """Keep the list, so it can be logged or inspected."""
        self._entries = list(entries)

    def show_error(self, title: str, text: str) -> None:
        """Report an error to the log, which is all there is here."""
        log.error("%s: %s", title, text)

    def toast(self, text: str) -> None:
        """Report a short notice to the log."""
        log.info("%s", text)

    def show_update_state(self, text: str, offer_install: bool, busy: bool = False) -> None:
        """Report an update state to the log."""
        log.info("Update: %s", text)

    def show_view(self, page: Optional[str] = None) -> None:
        """There is no window to show."""

    def hide_view(self) -> None:
        """There is no window to hide."""

    def view_visible(self) -> bool:
        """No window is ever visible."""
        return False

    def ask_yes_no(self, title: str, text: str) -> bool:
        """Nobody is here to answer, so the answer is no.

        :param title: The question's title.
        :param text: The question.
        :return: Always ``False``.
        """
        log.info("Not asking without an interface (answered no): %s", text)
        return False

    def ask_terms_acceptance(self, title_key: str = "", body_key: str = "") -> bool:
        """Accept the terms only when the command line said so.

        :param title_key: Translation key of the title.
        :param body_key: Translation key of the body.
        :return: Whether the terms count as accepted.
        """
        if self._accept_terms:
            log.info("Terms accepted through --accept-terms (%s).", title_key or "app")
            return True
        log.error("The terms of use have not been accepted. Start once with a window, "
                  "or pass --accept-terms to confirm them here.")
        return False

    def show_terms_document(self, *args: Any, **kwargs: Any) -> None:
        """There is no window to show a document in."""

    def status(self) -> Dict[str, Any]:
        """Return what the navigation window would be showing.

        :return: The recorded headline, status text and percentage.
        """
        return {
            "headline": self.nav.headline,
            "status": self.nav.status,
            "detail": self.nav.detail,
            "percent": self.nav.percent,
            "entries": len(self._entries),
        }
