"""The prompt primitive and the marshalling onto the Tk thread."""

from __future__ import annotations

import threading
import time

import pytest

from clipster.bridge import Prompt, TkBridge


# ----------------------------------------------------------------------
# Prompt - the worker thread waits, the Tk thread answers
# ----------------------------------------------------------------------
def test_an_answer_releases_the_waiting_worker() -> None:
    prompt = Prompt()
    assert not prompt.answered
    threading.Timer(0.02, lambda: prompt.answer({"format": "mp4"})).start()
    assert prompt.wait(poll=0.01) == {"format": "mp4"}
    assert prompt.answered


def test_cancelling_answers_with_none() -> None:
    prompt = Prompt()
    threading.Timer(0.02, prompt.cancel).start()
    assert prompt.wait(poll=0.01) is None


def test_an_abort_event_ends_the_wait() -> None:
    """Shutting down must never leave a worker hanging on a question."""
    prompt = Prompt()
    abort = threading.Event()
    threading.Timer(0.02, abort.set).start()
    assert prompt.wait(abort, poll=0.01) is None


def test_any_of_several_abort_events_is_enough() -> None:
    prompt = Prompt()
    quitting, cancelled = threading.Event(), threading.Event()
    threading.Timer(0.02, cancelled.set).start()
    assert prompt.wait(quitting, cancelled, poll=0.01) is None


def test_the_first_answer_wins() -> None:
    prompt = Prompt()
    prompt.answer("first")
    prompt.answer("second")
    assert prompt.wait(poll=0.01) == "first"


def test_cancelling_after_an_answer_changes_nothing() -> None:
    prompt = Prompt()
    prompt.answer("kept")
    prompt.cancel()
    assert prompt.wait(poll=0.01) == "kept"


def test_an_already_answered_prompt_returns_at_once() -> None:
    prompt = Prompt()
    prompt.answer("value")
    started = time.monotonic()
    assert prompt.wait(poll=5.0) == "value"
    assert time.monotonic() - started < 1.0


# ----------------------------------------------------------------------
# TkBridge
# ----------------------------------------------------------------------
@pytest.mark.gui
class TestTkBridge:
    """Needs a Tk interpreter to own the main thread."""

    @pytest.fixture()
    def root(self):
        import tkinter as tk

        window = tk.Tk()
        window.withdraw()
        try:
            yield window
        finally:
            window.destroy()

    def test_the_creating_thread_is_the_gui_thread(self, root) -> None:
        bridge = TkBridge(root)
        assert bridge.on_gui_thread()

    def test_a_call_from_the_gui_thread_runs_directly(self, root) -> None:
        bridge = TkBridge(root)
        bridge.start()
        try:
            assert bridge.call(lambda: 21 * 2) == 42
        finally:
            bridge.stop()

    def test_a_worker_call_reaches_the_gui_thread(self, root) -> None:
        bridge = TkBridge(root)
        bridge.start()
        seen: dict = {}

        def from_worker() -> None:
            seen["result"] = bridge.call(lambda: threading.current_thread().name)

        worker = threading.Thread(target=from_worker)
        worker.start()
        deadline = time.monotonic() + 5.0
        while worker.is_alive() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        worker.join(timeout=2.0)
        bridge.stop()
        assert seen.get("result") == "MainThread"

    def test_a_worker_call_after_stop_is_refused(self, root) -> None:
        """Otherwise the worker would block forever on a dead queue."""
        bridge = TkBridge(root)
        bridge.start()
        bridge.stop()
        outcome: dict = {}

        def probe() -> None:
            try:
                bridge.call(lambda: None)
                outcome["result"] = "no error"
            except RuntimeError as exc:
                outcome["result"] = str(exc)

        worker = threading.Thread(target=probe)
        worker.start()
        worker.join(timeout=5.0)
        assert "result" in outcome
        assert outcome["result"] != "no error"

    def test_posting_after_stop_is_silently_dropped(self, root) -> None:
        bridge = TkBridge(root)
        bridge.start()
        bridge.stop()
        assert bridge.post(lambda: None) is None

    def test_an_exception_in_a_queued_task_does_not_kill_the_bridge(self, root) -> None:
        bridge = TkBridge(root)
        bridge.start()
        survived: list = []
        try:
            bridge.post(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            bridge.post(lambda: survived.append(True))
            deadline = time.monotonic() + 3.0
            while not survived and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
        finally:
            bridge.stop()
        assert survived, "the bridge stopped after the first failing task"
