"""Marshalling of GUI calls from worker threads onto the Tk main thread.

Tk is not thread-safe: every widget call has to happen in the thread that
created the interpreter.  The download pipeline however runs in a background
thread so the UI stays responsive.  :class:`TkBridge` bridges the two.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional

from .logging_setup import get_logger

log = get_logger(__name__)


class Prompt:
    """A question the worker thread asks and the Tk thread answers.

    The whole download flow happens inside one window, so a question is not a
    modal dialog that returns a value but a panel that stays on screen until a
    button is pressed.  The worker posts the panel and blocks on :meth:`wait`;
    the button handler calls :meth:`answer` on the Tk thread.

    ``None`` is the answer for "canceled" - :meth:`wait` also returns ``None``
    when an abort event fires, so shutting down never leaves a stuck worker.
    """

    __slots__ = ("_event", "_value")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._value: Any = None

    @property
    def answered(self) -> bool:
        """Return ``True`` once an answer (or a cancel) has been recorded."""
        return self._event.is_set()

    def answer(self, value: Any) -> None:
        """Record the user's choice and release the waiting worker.

        :param value: The chosen value, or ``None`` for "canceled".
        :return: None
        """
        if self._event.is_set():
            return
        self._value = value
        self._event.set()

    def cancel(self) -> None:
        """Record "canceled" and release the waiting worker."""
        self.answer(None)

    def wait(self, *abort_events: threading.Event, poll: float = 0.2) -> Any:
        """Block until the question is answered or an abort event fires.

        :param abort_events: Events that count as a cancel (quit, cancel button).
        :param poll: Seconds between two abort checks.
        :return: The answer, or ``None`` when canceled or aborted.
        """
        while not self._event.wait(timeout=poll):
            for event in abort_events:
                if event is not None and event.is_set():
                    return None
        return self._value


class _Task:
    """A single callable queued for execution on the Tk thread."""

    __slots__ = ("func", "args", "kwargs", "done", "result", "error", "wait")

    def __init__(self, func: Callable[..., Any], args: tuple, kwargs: dict, wait: bool) -> None:
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.wait = wait
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        """Execute the callable and capture its result or exception."""
        try:
            self.result = self.func(*self.args, **self.kwargs)
        except BaseException as exc:  # noqa: BLE001 - forwarded to the caller
            self.error = exc
            if not self.wait:
                log.exception("Queued GUI task failed")
        finally:
            self.done.set()


class TkBridge:
    """Runs callables on the Tk main thread from any other thread."""

    def __init__(self, root: Any, interval_ms: int = 30) -> None:
        """
        :param root: The Tk root window whose event loop drains the queue.
        :param interval_ms: Polling interval of the queue drain.
        """
        self._root = root
        self._interval = interval_ms
        self._queue: "queue.Queue[_Task]" = queue.Queue()
        self._owner_thread: Optional[int] = None
        self._running = False

    def start(self) -> None:
        """Begin draining the queue; must be called from the Tk thread."""
        self._owner_thread = threading.get_ident()
        self._running = True
        self._drain()

    def stop(self) -> None:
        """Stop draining and release every waiting caller."""
        self._running = False
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            task.error = RuntimeError("GUI bridge stopped")
            task.done.set()

    def _drain(self) -> None:
        """Execute all queued tasks, then reschedule itself."""
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            task.run()
        if self._running:
            try:
                self._root.after(self._interval, self._drain)
            except Exception:  # pragma: no cover - interpreter already destroyed
                self._running = False

    def on_gui_thread(self) -> bool:
        """Return ``True`` when the caller already runs on the Tk thread."""
        return self._owner_thread is not None and threading.get_ident() == self._owner_thread

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``func`` on the Tk thread and wait for its result.

        :param func: The callable to execute.
        :param args: Positional arguments for ``func``.
        :param kwargs: Keyword arguments for ``func``.
        :return: Whatever ``func`` returned.
        :raises RuntimeError: When the bridge was stopped before execution.
        """
        if self.on_gui_thread():
            return func(*args, **kwargs)
        if not self._running:
            raise RuntimeError("GUI bridge is not running")
        task = _Task(func, args, kwargs, wait=True)
        self._queue.put(task)
        task.done.wait()
        if task.error is not None:
            raise task.error
        return task.result

    def post(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Schedule ``func`` on the Tk thread without waiting for the result.

        :param func: The callable to execute.
        :param args: Positional arguments for ``func``.
        :param kwargs: Keyword arguments for ``func``.
        :return: None
        """
        if self.on_gui_thread():
            _Task(func, args, kwargs, wait=False).run()
            return
        if not self._running:
            log.debug("Dropping GUI task %s - bridge stopped", getattr(func, "__name__", func))
            return
        self._queue.put(_Task(func, args, kwargs, wait=False))
