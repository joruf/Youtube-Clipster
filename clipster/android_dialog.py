"""The "Install on Android" window: from plugging in the phone to the transfer.

Four steps, each with its own state, so it is always clear what the program is
waiting for and what has to be tapped on the phone:

1. is ``adb`` there,
2. is a phone plugged in and has USB debugging been confirmed on it,
3. pack the program up and transfer it, with progress,
4. the one line that has to run inside Termux - which cannot be done from here,
   because ``adb`` may not enter Termux's private storage.

Every call into ``adb`` happens on a worker thread, so a phone that takes its
time never freezes the window. Those threads never touch Tk: they put their
result into a queue that the Tk thread drains on a timer. Calling ``after`` from
another thread looks like it works and then raises "main thread is not in main
loop" - tkinter is not thread safe.
"""

from __future__ import annotations

import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional

from . import android, paths, theme
from .i18n import Messages
from .logging_setup import get_logger
from .theme import PAD, PAD_SMALL

log = get_logger(__name__)

#: How often the phone is looked for while the window waits, in milliseconds.
WATCH_MS = 1500

#: How often results from the worker threads are collected, in milliseconds.
PUMP_MS = 80

#: The four steps, in order.
STEPS = ("adb", "device", "transfer", "finish")


class AndroidDialog:
    """Walks the user through putting the program onto a phone."""

    def __init__(self, master: tk.Misc, messages: Messages, palette: theme.Palette,
                 fonts: dict, on_copy: Callable[[str], None]) -> None:
        """
        :param master: The parent window.
        :param messages: The active translation table.
        :param palette: The colour scheme.
        :param fonts: The theme font map.
        :param on_copy: Puts a string on the clipboard.
        """
        self.messages = messages
        self.palette = palette
        self.fonts = fonts
        self._on_copy = on_copy
        self._device: Optional[android.Device] = None
        self._watch_job: Optional[str] = None
        self._pump_job: Optional[str] = None
        self._busy = False
        self._closed = False
        #: Filled by the worker threads, drained on the Tk thread only.
        self._results: "queue.Queue" = queue.Queue()

        self.window = tk.Toplevel(master)
        self.window.title(messages["android_title"])
        self.window.configure(background=palette.base)
        self.window.minsize(620, 520)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.transient(master.winfo_toplevel())

        self._labels: Dict[str, ttk.Label] = {}
        self._build()
        self._centre()
        self._pump()
        self.refresh_device()

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    def _build(self) -> None:
        """Create the four step cards and the buttons."""
        frame = ttk.Frame(self.window, style="TFrame", padding=PAD)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=self.messages["android_title"], style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=self.messages["android_intro"], style="Muted.TLabel",
                  wraplength=560, justify="left").pack(anchor="w", pady=(2, PAD))

        # 1 - adb
        card = self._card(frame, "android_step_adb")
        self._labels["adb"] = self._status_label(card)
        self._adb_hint = ttk.Label(card, text="", style="Panel.Muted.TLabel",
                                   wraplength=520, justify="left")
        self._adb_hint.pack(anchor="w", pady=(2, 0))

        # 2 - the phone
        card = self._card(frame, "android_step_device")
        self._labels["device"] = self._status_label(card)
        ttk.Label(card, text=self.messages["android_device_help"], style="Panel.Muted.TLabel",
                  wraplength=520, justify="left").pack(anchor="w", pady=(2, 0))

        # 3 - the transfer
        card = self._card(frame, "android_step_transfer")
        self._labels["transfer"] = self._status_label(card)
        self._progress = ttk.Progressbar(card, mode="determinate", maximum=100, length=520)
        self._progress.pack(fill="x", pady=(PAD_SMALL, 0))

        # 4 - what is left to do on the phone
        card = self._card(frame, "android_step_finish")
        self._labels["finish"] = self._status_label(card)
        self._command = tk.Text(card, height=3, wrap="word", relief="flat",
                                background=self.palette.elevated, foreground=self.palette.text,
                                insertbackground=self.palette.text, font=self.fonts["small"])
        self._command.insert("1.0", android.install_command())
        self._command.configure(state="disabled")
        self._command.pack(fill="x", pady=(PAD_SMALL, 0))
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(PAD_SMALL, 0))
        ttk.Button(row, text=self.messages["android_copy_command"], style="Row.TButton",
                   command=self._copy_command).pack(side="left")
        self._termux_button = ttk.Button(row, text=self.messages["android_open_termux"],
                                        style="Row.TButton", command=self._open_termux)
        self._termux_button.pack(side="left", padx=(PAD_SMALL, 0))

        buttons = ttk.Frame(frame, style="TFrame")
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text=self.messages["android_close"],
                   command=self.close).pack(side="right")
        self._start_button = ttk.Button(buttons, text=self.messages["android_transfer"],
                                       style="Accent.TButton", command=self.start_transfer)
        self._start_button.pack(side="right", padx=(0, PAD_SMALL))
        ttk.Button(buttons, text=self.messages["android_recheck"],
                   command=self.refresh_device).pack(side="left")

    def _card(self, master: tk.Misc, title_key: str) -> ttk.LabelFrame:
        """Return one titled card."""
        card = ttk.LabelFrame(master, text=self.messages[title_key],
                              style="Card.TLabelframe", padding=PAD)
        card.pack(fill="x", pady=(0, PAD_SMALL))
        return card

    def _status_label(self, master: tk.Misc) -> ttk.Label:
        """Return the status line of a card."""
        label = ttk.Label(master, text="", style="Panel.TLabel", wraplength=520,
                          justify="left")
        label.pack(anchor="w")
        return label

    def _centre(self) -> None:
        """Put the window in the upper third of the screen."""
        try:
            self.window.update_idletasks()
            width = max(620, self.window.winfo_reqwidth())
            height = max(520, self.window.winfo_reqheight())
            x = max(0, (self.window.winfo_screenwidth() - width) // 2)
            y = max(0, (self.window.winfo_screenheight() - height) // 3)
            self.window.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))
        except tk.TclError:  # pragma: no cover - window already gone
            pass

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _set(self, step: str, text: str, style: str = "Panel.TLabel") -> None:
        """Update one step's status line."""
        label = self._labels.get(step)
        if label is None:
            return
        try:
            label.configure(text=text, style=style)
        except tk.TclError:  # pragma: no cover - window already gone
            pass

    def refresh_device(self) -> None:
        """Look for the phone, without blocking the window."""
        if self._closed or self._busy:
            return
        self._set("device", self.messages["android_looking"])

        def work() -> None:
            found = android.devices()
            state, device = android.summarise(found)
            self._post(self._show_state, state, device, found)

        threading.Thread(target=work, name="clipster-adb-scan", daemon=True).start()

    def _show_state(self, state: str, device: Optional[android.Device],
                    found: List[android.Device]) -> None:
        """Render what the scan found and arm the next look."""
        self._device = device
        if state == "no_adb":
            self._set("adb", self.messages["android_adb_missing"], "Panel.Danger.TLabel")
            self._adb_hint.configure(text=self._adb_install_hint())
            self._set("device", self.messages["android_adb_first"], "Panel.Muted.TLabel")
        else:
            self._set("adb", self.messages.format("android_adb_found",
                                                  path=android.adb_path() or "adb"),
                      "Panel.Success.TLabel")
            self._adb_hint.configure(text="")
            if state == "ready" and device is not None:
                self._set("device", self.messages.format("android_device_ready",
                                                         device=device.describe()),
                          "Panel.Success.TLabel")
            elif state == "unauthorised":
                self._set("device", self.messages["android_device_unauthorised"],
                          "Panel.Warning.TLabel")
            elif state == "offline":
                self._set("device", self.messages["android_device_offline"],
                          "Panel.Warning.TLabel")
            else:
                self._set("device", self.messages["android_device_none"], "Panel.Muted.TLabel")

        ready = state == "ready"
        try:
            self._start_button.configure(state="normal" if ready else "disabled")
            self._termux_button.configure(state="normal" if ready else "disabled")
        except tk.TclError:  # pragma: no cover
            pass
        # Keep looking while the phone is not ready yet, so plugging it in or
        # tapping the prompt is picked up without the user pressing anything.
        self._arm_watch(not ready)

    def _arm_watch(self, keep_looking: bool) -> None:
        """Schedule the next scan, or stop scanning."""
        if self._watch_job is not None:
            try:
                self.window.after_cancel(self._watch_job)
            except tk.TclError:  # pragma: no cover
                pass
            self._watch_job = None
        if keep_looking and not self._closed and not self._busy:
            try:
                self._watch_job = self.window.after(WATCH_MS, self.refresh_device)
            except tk.TclError:  # pragma: no cover
                pass

    def _adb_install_hint(self) -> str:
        """Return how to install adb on this system."""
        if paths.IS_WINDOWS:
            return self.messages["android_adb_windows"]
        from .installer import detect_package_manager

        manager = detect_package_manager()
        if manager is None:
            return self.messages["android_adb_manual"]
        return self.messages.format("android_adb_command",
                                    command="{0} {1}".format(" ".join(manager.install), "adb"))

    # ------------------------------------------------------------------
    # Doing it
    # ------------------------------------------------------------------
    def start_transfer(self) -> None:
        """Pack the program up and copy it to the phone."""
        if self._busy or self._device is None:
            return
        self._busy = True
        self._arm_watch(False)
        device = self._device
        self._set("transfer", self.messages["android_packing"])
        self._progress.configure(value=0)
        try:
            self._start_button.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass

        def work() -> None:
            workspace = Path(tempfile.mkdtemp(prefix="clipster-android-"))
            bundle = workspace / android.BUNDLE_NAME
            try:
                android.make_bundle(paths.PROJECT_ROOT, bundle)
                self._post(self._set, "transfer", self.messages["android_sending"])
                ok, message = android.push(
                    bundle, serial=device.serial,
                    on_progress=lambda percent: self._post(self._progress_to, percent),
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("The transfer failed")
                ok, message = False, str(exc)
            finally:
                try:
                    bundle.unlink(missing_ok=True)
                    workspace.rmdir()
                except OSError:  # pragma: no cover - leftover temp dir is harmless
                    pass
            self._post(self._transfer_done, ok, message)

        threading.Thread(target=work, name="clipster-adb-push", daemon=True).start()

    def _progress_to(self, percent: int) -> None:
        """Move the bar."""
        try:
            self._progress.configure(value=max(0, min(100, int(percent))))
        except tk.TclError:  # pragma: no cover
            pass

    def _transfer_done(self, ok: bool, message: str) -> None:
        """Report the outcome and unlock the window."""
        self._busy = False
        try:
            self._start_button.configure(state="normal")
        except tk.TclError:  # pragma: no cover
            pass
        if ok:
            self._progress_to(100)
            self._set("transfer", self.messages.format("android_sent", target=message),
                      "Panel.Success.TLabel")
            self._set("finish", self.messages["android_finish_ready"], "Panel.Success.TLabel")
        else:
            self._set("transfer", self.messages.format("android_send_failed", details=message),
                      "Panel.Danger.TLabel")
            self._arm_watch(True)

    def _copy_command(self) -> None:
        """Put the Termux command on the clipboard."""
        self._on_copy(android.install_command())
        self._set("finish", self.messages["android_copied"], "Panel.Success.TLabel")

    def _open_termux(self) -> None:
        """Bring Termux to the front on the phone."""
        serial = self._device.serial if self._device is not None else ""

        def work() -> None:
            ok = android.open_termux(serial)
            self._post(self._set, "finish",
                       self.messages["android_termux_opened"] if ok
                       else self.messages["android_termux_failed"],
                       "Panel.Success.TLabel" if ok else "Panel.Warning.TLabel")

        threading.Thread(target=work, name="clipster-adb-termux", daemon=True).start()

    # ------------------------------------------------------------------
    def _post(self, function: Callable[..., Any], *args: Any) -> None:
        """Hand something to the Tk thread from a worker.

        Queued rather than scheduled: ``after`` may only be called from the
        thread that owns the interpreter.

        :param function: What to run on the Tk thread.
        :param args: Its positional arguments.
        :return: None
        """
        if self._closed:
            return
        self._results.put((function, args))

    def _pump(self) -> None:
        """Run whatever the workers left behind, on the Tk thread."""
        if self._closed:
            return
        while True:
            try:
                function, args = self._results.get_nowait()
            except queue.Empty:
                break
            try:
                function(*args)
            except Exception:  # pragma: no cover - one result must not stop the rest
                log.exception("An adb result could not be shown")
        try:
            self._pump_job = self.window.after(PUMP_MS, self._pump)
        except tk.TclError:  # pragma: no cover - window already gone
            self._pump_job = None

    def close(self) -> None:
        """Stop looking and tear the window down."""
        self._closed = True
        self._arm_watch(False)
        if self._pump_job is not None:
            try:
                self.window.after_cancel(self._pump_job)
            except tk.TclError:  # pragma: no cover
                pass
            self._pump_job = None
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
