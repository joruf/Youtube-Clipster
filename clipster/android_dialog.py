"""The "Install on Android" window: a numbered step-by-step wizard.

Eight steps appear one at a time. Completed steps show a checkmark; the active
step expands with full instructions. Every call into ``adb`` happens on a worker
thread; results are queued and drained on the Tk thread via :meth:`_pump`.
"""

from __future__ import annotations

import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Set

from . import android, paths, theme
from .i18n import Messages
from .logging_setup import get_logger
from .theme import PAD, PAD_SMALL

log = get_logger(__name__)

WATCH_MS = 1500
PUMP_MS = 80

#: Wizard steps in order.
WIZARD_STEPS = ("adb", "developer", "connect", "usb_install", "terms", "termux", "setup", "done")

#: Backward-compatible aliases for status labels used by tests and older code.
_LABEL_ALIASES = {"device": "connect", "transfer": "setup", "finish": "done"}

RUN_STATUS = {
    "opening": "android_run_opening",
    "typing": "android_run_typing",
}

RUN_FAILURES = {
    "untypeable": "android_run_failed_untypeable",
    "termux_missing": "android_run_failed_termux_missing",
    "termux_not_open": "android_run_failed_termux_not_open",
    "typing_failed": "android_run_failed_typing_failed",
}

TERMUX_STATUS = {
    "downloading": "android_termux_downloading",
    "uninstalling": "android_termux_uninstalling",
    "installing": "android_termux_installing",
    "manual_apk": "android_termux_manual_apk",
}


class AndroidDialog:
    """Walks the user through putting the program onto a phone."""

    def __init__(self, master: tk.Misc, messages: Messages, palette: theme.Palette,
                 fonts: dict, on_copy: Callable[[str], None]) -> None:
        self.messages = messages
        self.palette = palette
        self.fonts = fonts
        self._on_copy = on_copy
        self._device: Optional[android.Device] = None
        self._watch_job: Optional[str] = None
        self._pump_job: Optional[str] = None
        self._busy = False
        self._closed = False
        self._adb_error = ""
        self._adb_ready = False
        self._device_ready = False
        self._terms_accepted = False
        self._termux_ready = False
        self._completed: Set[str] = set()
        self._current_step = WIZARD_STEPS[0]
        #: Whether the last transfer put files into Termux's home (not /sdcard).
        self._launch_in_home = False
        self._termux_action_queue: "queue.Queue[str]" = queue.Queue(maxsize=1)
        self._termux_choice_queue: "queue.Queue[bool]" = queue.Queue(maxsize=1)
        self._results: "queue.Queue" = queue.Queue()

        self.window = tk.Toplevel(master)
        self.window.title(messages["android_title"])
        self.window.configure(background=palette.base)
        self.window.minsize(620, 520)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.transient(master.winfo_toplevel())

        self._labels: Dict[str, ttk.Label] = {}
        self._step_headers: Dict[str, ttk.Label] = {}
        self._step_cards: Dict[str, ttk.LabelFrame] = {}
        self._step_bodies: Dict[str, ttk.Frame] = {}
        self._build()
        self._centre()
        self._refresh_wizard()
        self._pump()
        self.refresh_device()

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    def _build(self) -> None:
        """Create the wizard steps and buttons."""
        outer = ttk.Frame(self.window, style="TFrame", padding=PAD)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=self.messages["android_title"], style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=self.messages["android_intro"], style="Muted.TLabel",
                  wraplength=560, justify="left").pack(anchor="w", pady=(2, PAD))

        self._steps_container = ttk.Frame(outer, style="TFrame")
        self._steps_container.pack(fill="both", expand=True)

        self._build_step_adb()
        self._build_step_developer()
        self._build_step_connect()
        self._build_step_usb_install()
        self._build_step_terms()
        self._build_step_termux()
        self._build_step_setup()
        self._build_step_done()
        self._build_hidden_fallbacks(outer)

        buttons = ttk.Frame(outer, style="TFrame")
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text=self.messages["android_close"],
                   command=self.close).pack(side="right")
        self._start_button = ttk.Button(buttons, text=self.messages["android_install_now"],
                                        style="Accent.TButton", command=self.start_install)
        self._start_button.pack(side="right", padx=(0, PAD_SMALL))
        self._primary_button = ttk.Button(buttons, text=self.messages["android_wizard_next"],
                                          style="Accent.TButton", command=self._on_primary)
        self._primary_button.pack(side="right", padx=(0, PAD_SMALL))
        self._recheck_button = ttk.Button(buttons, text=self.messages["android_recheck"],
                                          command=self.refresh_device)
        self._recheck_button.pack(side="left")
        # Every wizard title key must stay in the locale files (i18n grep check).
        _ = (
            self.messages["android_wizard_adb_title"],
            self.messages["android_wizard_developer_title"],
            self.messages["android_wizard_connect_title"],
            self.messages["android_wizard_usb_install_title"],
            self.messages["android_wizard_terms_title"],
            self.messages["android_wizard_termux_title"],
            self.messages["android_wizard_setup_title"],
            self.messages["android_wizard_done_title"],
        )

    def _wizard_card(self, step: str, title_key: str) -> ttk.LabelFrame:
        """Return one wizard step card (header + expandable body)."""
        card = ttk.LabelFrame(self._steps_container, text="", style="Card.TLabelframe",
                              padding=PAD)
        header = ttk.Label(card, text=self.messages[title_key], style="Panel.TLabel",
                           wraplength=520, justify="left")
        header.pack(anchor="w")
        body = ttk.Frame(card, style="Panel.TFrame")
        self._step_headers[step] = header
        self._step_cards[step] = card
        self._step_bodies[step] = body
        return card

    def _build_step_adb(self) -> None:
        card = self._wizard_card("adb", "android_wizard_adb_title")
        body = self._step_bodies["adb"]
        ttk.Label(body, text=self.messages["android_wizard_adb_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))
        self._labels["adb"] = self._status_label(body)
        self._adb_hint = ttk.Label(body, text="", style="Panel.Muted.TLabel",
                                   wraplength=520, justify="left")
        self._adb_hint.pack(anchor="w", pady=(2, 0))
        self._adb_row = ttk.Frame(body, style="Panel.TFrame")
        self._adb_button = ttk.Button(self._adb_row, text=self.messages["android_adb_install"],
                                      style="Row.TButton", command=self._install_adb)
        self._adb_button.pack(side="left")

    def _build_step_developer(self) -> None:
        card = self._wizard_card("developer", "android_wizard_developer_title")
        body = self._step_bodies["developer"]
        ttk.Label(body, text=self.messages["android_wizard_developer_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))

    def _build_step_connect(self) -> None:
        card = self._wizard_card("connect", "android_wizard_connect_title")
        body = self._step_bodies["connect"]
        ttk.Label(body, text=self.messages["android_wizard_connect_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))
        self._labels["device"] = self._status_label(body)

    def _step_title_key(self, step: str) -> str:
        keys = {
            "adb": "android_wizard_adb_title",
            "developer": "android_wizard_developer_title",
            "connect": "android_wizard_connect_title",
            "usb_install": "android_wizard_usb_install_title",
            "terms": "android_wizard_terms_title",
            "termux": "android_wizard_termux_title",
            "setup": "android_wizard_setup_title",
            "done": "android_wizard_done_title",
        }
        return keys[step]

    def _step_title(self, step: str) -> str:
        return self.messages[self._step_title_key(step)]

    def _build_step_usb_install(self) -> None:
        card = self._wizard_card("usb_install", self._step_title_key("usb_install"))
        body = self._step_bodies["usb_install"]
        ttk.Label(body, text=self.messages["android_wizard_usb_install_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))
        ttk.Label(body, text=self.messages["android_wizard_usb_install_note"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))

    def _build_step_terms(self) -> None:
        card = self._wizard_card("terms", "android_wizard_terms_title")
        body = self._step_bodies["terms"]
        terms_body = self.messages["terms_app_body"]
        if len(terms_body) > 900:
            terms_body = terms_body[:899] + "…"
        ttk.Label(body, text=self.messages["android_wizard_terms_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, PAD_SMALL))
        ttk.Label(body, text=terms_body, style="Panel.Muted.TLabel",
                  wraplength=520, justify="left").pack(anchor="w")

    def _build_step_termux(self) -> None:
        card = self._wizard_card("termux", "android_wizard_termux_title")
        body = self._step_bodies["termux"]
        ttk.Label(body, text=self.messages["android_wizard_termux_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, PAD_SMALL))
        warning = ttk.Label(body, text=self.messages["android_wizard_termux_warning"],
                            style="Panel.TLabel", wraplength=520, justify="left")
        warning.pack(anchor="w", pady=(0, PAD_SMALL))
        self._termux_status = self._status_label(body)
        self._termux_play_row = ttk.Frame(body, style="Panel.TFrame")
        ttk.Label(self._termux_play_row, text=self.messages["android_termux_play_found"],
                  style="Panel.Warning.TLabel", wraplength=500, justify="left").pack(
                      anchor="w", pady=(0, PAD_SMALL))
        ttk.Label(self._termux_play_row,
                  text=self.messages.format("android_wizard_termux_play_prompt",
                                            url=android.TERMUX_GITHUB_PAGE),
                  style="Panel.Muted.TLabel", wraplength=500, justify="left").pack(
                      anchor="w", pady=(0, PAD_SMALL))
        play_btns = ttk.Frame(self._termux_play_row, style="Panel.TFrame")
        play_btns.pack(anchor="w")
        ttk.Button(play_btns, text=self.messages["android_wizard_termux_play_yes"],
                   style="Row.TButton",
                   command=lambda: self._answer_termux_choice(True)).pack(side="left")
        ttk.Button(play_btns, text=self.messages["android_wizard_termux_play_no"],
                   style="Row.TButton",
                   command=lambda: self._answer_termux_choice(False)).pack(
                       side="left", padx=(PAD_SMALL, 0))
        self._termux_usb_row = ttk.Frame(body, style="Panel.TFrame")
        ttk.Label(self._termux_usb_row, text=self.messages["android_termux_usb_blocked"],
                  style="Panel.Warning.TLabel", wraplength=500, justify="left").pack(
                      anchor="w", pady=(0, PAD_SMALL))
        ttk.Label(self._termux_usb_row, text=self.messages["android_wizard_termux_usb_prompt"],
                  style="Panel.Muted.TLabel", wraplength=500, justify="left").pack(
                      anchor="w", pady=(0, PAD_SMALL))
        usb_btns = ttk.Frame(self._termux_usb_row, style="Panel.TFrame")
        usb_btns.pack(anchor="w")
        ttk.Button(usb_btns, text=self.messages["android_wizard_termux_usb_retry"],
                   style="Row.TButton",
                   command=lambda: self._answer_termux_action("retry")).pack(side="left")
        ttk.Button(usb_btns, text=self.messages["android_wizard_termux_usb_manual"],
                   style="Row.TButton",
                   command=lambda: self._answer_termux_action("wait_manual")).pack(
                       side="left", padx=(PAD_SMALL, 0))
        ttk.Button(usb_btns, text=self.messages["android_wizard_termux_usb_cancel"],
                   style="Row.TButton",
                   command=lambda: self._answer_termux_action("abort")).pack(
                       side="left", padx=(PAD_SMALL, 0))
        self._termux_action_row = ttk.Frame(body, style="Panel.TFrame")
        self._termux_install_button = ttk.Button(
            self._termux_action_row, text=self.messages["android_wizard_termux_install"],
            style="Row.TButton", command=self._start_termux_install)
        self._termux_install_button.pack(side="left")
        self._termux_retry_button = ttk.Button(
            self._termux_action_row, text=self.messages["android_wizard_termux_retry"],
            style="Row.TButton", command=self._start_termux_install)
        self._termux_retry_button.pack(side="left", padx=(PAD_SMALL, 0))
        self._termux_confirm_button = ttk.Button(
            self._termux_action_row, text=self.messages["android_wizard_termux_confirm"],
            style="Row.TButton", command=self._confirm_termux_installed)
        self._termux_confirm_button.pack(side="left", padx=(PAD_SMALL, 0))
        self._termux_button = ttk.Button(
            self._termux_action_row, text=self.messages["android_open_termux"],
            style="Row.TButton", command=self._open_termux)
        self._termux_button.pack(side="left", padx=(PAD_SMALL, 0))

    def _build_step_setup(self) -> None:
        card = self._wizard_card("setup", "android_wizard_setup_title")
        body = self._step_bodies["setup"]
        ttk.Label(body, text=self.messages["android_wizard_setup_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))
        self._labels["transfer"] = self._status_label(body)
        self._progress = ttk.Progressbar(body, mode="determinate", maximum=100, length=520)
        self._progress.pack(fill="x", pady=(PAD_SMALL, 0))

    def _build_step_done(self) -> None:
        card = self._wizard_card("done", "android_wizard_done_title")
        body = self._step_bodies["done"]
        ttk.Label(body, text=self.messages["android_wizard_done_body"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(
                      anchor="w", pady=(2, 0))
        self._labels["finish"] = self._status_label(body)

    def _build_hidden_fallbacks(self, master: tk.Misc) -> None:
        """Widgets kept for tests and manual fallback; not shown in the wizard."""
        hidden = ttk.Frame(master, style="TFrame")
        ttk.Label(hidden, text=self.messages["android_finish_hint"],
                  style="Panel.Muted.TLabel", wraplength=520, justify="left").pack(anchor="w")
        self._command = tk.Text(hidden, height=2, wrap="word", relief="flat",
                                background=self.palette.elevated, foreground=self.palette.text,
                                insertbackground=self.palette.text, font=self.fonts["small"])
        self._command.insert("1.0", android.launch_command())
        self._command.configure(state="disabled")
        self._command.pack(fill="x")
        row = ttk.Frame(hidden, style="Panel.TFrame")
        row.pack(fill="x")
        self._run_button = ttk.Button(row, text=self.messages["android_run_on_phone"],
                                      style="Row.TButton", command=self._run_on_phone)
        self._run_button.pack(side="left")
        ttk.Button(row, text=self.messages["android_copy_command"], style="Row.TButton",
                   command=self._copy_command).pack(side="left", padx=(PAD_SMALL, 0))

    def _status_label(self, master: tk.Misc) -> ttk.Label:
        label = ttk.Label(master, text="", style="Panel.TLabel", wraplength=520,
                          justify="left")
        label.pack(anchor="w")
        return label

    def _centre(self) -> None:
        try:
            self.window.update_idletasks()
            width = max(620, self.window.winfo_reqwidth())
            height = max(520, self.window.winfo_reqheight())
            x = max(0, (self.window.winfo_screenwidth() - width) // 2)
            y = max(0, (self.window.winfo_screenheight() - height) // 3)
            self.window.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))
        except tk.TclError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # Wizard navigation
    # ------------------------------------------------------------------
    def _step_index(self, step: str) -> int:
        return WIZARD_STEPS.index(step)

    def _is_step_reachable(self, step: str) -> bool:
        idx = self._step_index(step)
        if idx == 0:
            return True
        for prior in WIZARD_STEPS[:idx]:
            if prior not in self._completed:
                return False
        return True

    def _refresh_wizard(self) -> None:
        """Show completed headers, expand the active step, hide the rest."""
        current_idx = self._step_index(self._current_step)
        for idx, step in enumerate(WIZARD_STEPS):
            card = self._step_cards[step]
            header = self._step_headers[step]
            body = self._step_bodies[step]
            title = self._step_title(step)
            if step in self._completed:
                card.pack(fill="x", pady=(0, PAD_SMALL))
                header.configure(text="✓  {0}".format(title), style="Panel.Muted.TLabel")
                body.pack_forget()
            elif step == self._current_step:
                card.pack(fill="x", pady=(0, PAD_SMALL))
                header.configure(text=title, style="Panel.TLabel")
                body.pack(fill="x", anchor="w", pady=(PAD_SMALL, 0))
            elif idx < current_idx:
                card.pack_forget()
            else:
                card.pack(fill="x", pady=(0, PAD_SMALL))
                header.configure(text=title, style="Panel.Muted.TLabel")
                body.pack_forget()
        self._update_primary_button()
        self._update_termux_controls()

    def _complete_current(self) -> None:
        self._completed.add(self._current_step)
        idx = self._step_index(self._current_step)
        if idx + 1 < len(WIZARD_STEPS):
            self._current_step = WIZARD_STEPS[idx + 1]
        self._refresh_wizard()
        self._on_step_entered(self._current_step)

    def _on_step_entered(self, step: str) -> None:
        if step == "connect":
            self.refresh_device()
        elif step == "setup":
            self.start_install()

    def _on_primary(self) -> None:
        if self._busy:
            return
        step = self._current_step
        if step == "adb":
            if self._adb_ready:
                self._complete_current()
            elif android.adb_install_plan()[0] != "manual":
                self._install_adb()
        elif step == "developer":
            self._complete_current()
        elif step == "connect":
            if self._device_ready:
                self._complete_current()
            else:
                self.refresh_device()
        elif step == "usb_install":
            self._complete_current()
        elif step == "terms":
            self._accept_terms()
        elif step == "termux":
            if self._termux_ready:
                self._complete_current()
            else:
                self._start_termux_install()
        elif step == "done":
            self.close()

    def _accept_terms(self) -> None:
        self._terms_accepted = True
        self._complete_current()

    def _update_primary_button(self) -> None:
        step = self._current_step
        text = self.messages["android_wizard_next"]
        state = "normal"
        if step == "adb":
            text = (self.messages["android_wizard_next"] if self._adb_ready
                    else self.messages["android_adb_install"])
            state = "normal" if self._adb_ready or android.adb_install_plan()[0] != "manual" else "disabled"
        elif step == "developer":
            text = self.messages["android_wizard_developer_done"]
        elif step == "connect":
            text = (self.messages["android_wizard_next"] if self._device_ready
                    else self.messages["android_recheck"])
        elif step == "usb_install":
            text = self.messages["android_wizard_usb_install_done"]
        elif step == "terms":
            text = self.messages["android_wizard_terms_accept"]
        elif step == "termux":
            if self._termux_ready:
                text = self.messages["android_wizard_next"]
            elif self._busy:
                text = self.messages["android_wizard_termux_install"]
                state = "disabled"
            else:
                text = self.messages["android_wizard_termux_install"]
        elif step == "setup":
            text = self.messages["android_install_now"]
            state = "disabled" if self._busy else "normal"
        elif step == "done":
            text = self.messages["android_close"]

        try:
            show_primary = step != "setup"
            if show_primary:
                self._primary_button.pack(side="right", padx=(0, PAD_SMALL))
                self._primary_button.configure(text=text, state=state)
            else:
                self._primary_button.pack_forget()
            self._recheck_button.pack_forget()
            if step == "connect":
                self._recheck_button.pack(side="left")
            setup_visible = step == "setup" and not self._busy
            if step == "setup":
                if setup_visible:
                    self._start_button.pack(side="right", padx=(0, PAD_SMALL))
                else:
                    self._start_button.pack_forget()
                self._start_button.configure(
                    state="normal" if self._device_ready and not self._busy else "disabled")
            else:
                self._start_button.pack_forget()
        except tk.TclError:  # pragma: no cover
            pass

    def _update_termux_controls(self) -> None:
        on_termux = self._current_step == "termux" and not self._busy
        try:
            for row in (self._termux_play_row, self._termux_usb_row):
                row.pack_forget()
            if not on_termux:
                self._termux_action_row.pack_forget()
                return
            self._termux_action_row.pack(anchor="w", pady=(PAD_SMALL, 0))
            self._termux_install_button.pack(side="left")
            self._termux_retry_button.pack_forget()
            self._termux_confirm_button.pack_forget()
            self._termux_button.configure(
                state="normal" if self._device_ready else "disabled")
        except tk.TclError:  # pragma: no cover
            pass

    def _show_termux_play_prompt(self) -> None:
        def show() -> None:
            try:
                self._termux_play_row.pack(anchor="w", pady=(PAD_SMALL, 0))
            except tk.TclError:  # pragma: no cover
                pass
        self._post(show)

    def _hide_termux_play_prompt(self) -> None:
        def hide() -> None:
            try:
                self._termux_play_row.pack_forget()
            except tk.TclError:  # pragma: no cover
                pass
        self._post(hide)

    def _show_termux_usb_prompt(self) -> None:
        def show() -> None:
            try:
                self._termux_usb_row.pack(anchor="w", pady=(PAD_SMALL, 0))
                self._termux_install_button.pack_forget()
                self._termux_retry_button.pack(side="left")
                self._termux_confirm_button.pack(side="left", padx=(PAD_SMALL, 0))
            except tk.TclError:  # pragma: no cover
                pass
        self._post(show)

    def _hide_termux_usb_prompt(self) -> None:
        def hide() -> None:
            try:
                self._termux_usb_row.pack_forget()
            except tk.TclError:  # pragma: no cover
                pass
        self._post(hide)

    def _answer_termux_choice(self, yes: bool) -> None:
        try:
            self._termux_choice_queue.put_nowait(yes)
        except queue.Full:  # pragma: no cover
            pass
        self._hide_termux_play_prompt()

    def _answer_termux_action(self, action: str) -> None:
        try:
            self._termux_action_queue.put_nowait(action)
        except queue.Full:  # pragma: no cover
            pass
        if action != "wait_manual":
            self._hide_termux_usb_prompt()

    def _confirm_termux_installed(self) -> None:
        if self._busy or self._device is None:
            return
        serial = self._device.serial

        def work() -> None:
            if android.termux_installed(serial):
                self._post(self._termux_install_done, True)
            else:
                self._post(self._set_termux_status,
                           self.messages["android_termux_missing"], "Panel.Warning.TLabel")

        threading.Thread(target=work, name="clipster-termux-check", daemon=True).start()

    def _set_termux_status(self, text: str, style: str = "Panel.TLabel") -> None:
        try:
            self._termux_status.configure(text=text, style=style)
        except tk.TclError:  # pragma: no cover
            pass

    def _termux_install_done(self, ok: bool) -> None:
        self._busy = False
        self._hide_termux_usb_prompt()
        if ok:
            self._termux_ready = True
            self._set_termux_status(self.messages["android_termux_installed"],
                                      "Panel.Success.TLabel")
            self._complete_current()
        self._refresh_wizard()
        self._arm_watch(True)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _set(self, step: str, text: str, style: str = "Panel.TLabel") -> None:
        label = self._labels.get(step)
        if label is None:
            if step in _LABEL_ALIASES.values():
                for alias, target in _LABEL_ALIASES.items():
                    if target == step:
                        label = self._labels.get(alias)
                        break
        if label is None:
            return
        try:
            label.configure(text=text, style=style)
        except tk.TclError:  # pragma: no cover
            pass

    def refresh_device(self) -> None:
        if self._closed or self._busy:
            return
        if self._current_step == "connect" or "connect" in self._completed:
            self._set("device", self.messages["android_looking"])

        def work() -> None:
            found = android.devices()
            state, device = android.summarise(found)
            self._post(self._show_state, state, device, found)

        threading.Thread(target=work, name="clipster-adb-scan", daemon=True).start()

    def _show_state(self, state: str, device: Optional[android.Device],
                    found: List[android.Device]) -> None:
        self._device = device
        self._adb_ready = state != "no_adb"
        self._device_ready = state == "ready" and device is not None

        if state == "no_adb":
            if self._adb_error:
                self._set("adb", self.messages.format("android_adb_install_failed",
                                                      details=self._adb_error),
                          "Panel.Danger.TLabel")
            else:
                self._set("adb", self.messages["android_adb_missing"], "Panel.Danger.TLabel")
            self._adb_hint.configure(text=self._adb_install_hint())
            self._show_adb_button(android.adb_install_plan()[0] != "manual")
            self._set("device", self.messages["android_adb_first"], "Panel.Muted.TLabel")
        else:
            self._adb_error = ""
            self._set("adb", self.messages.format("android_adb_found",
                                                  path=android.adb_path() or "adb"),
                      "Panel.Success.TLabel")
            self._adb_hint.configure(text="")
            self._show_adb_button(False)
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

        try:
            self._termux_button.configure(state="normal" if self._device_ready else "disabled")
            self._run_button.configure(state="normal" if self._device_ready else "disabled")
        except tk.TclError:  # pragma: no cover
            pass
        self._update_primary_button()
        keep_looking = (self._current_step == "connect" or
                        (self._current_step == "termux" and not self._termux_ready)) and not self._device_ready
        self._arm_watch(keep_looking and state != "no_adb")

    def _arm_watch(self, keep_looking: bool) -> None:
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
        kind, command = android.adb_install_plan()
        if kind == "manual":
            return (self.messages["android_adb_windows"] if paths.IS_WINDOWS
                    else self.messages["android_adb_manual"])
        return self.messages.format("android_adb_command", command=command)

    def _show_adb_button(self, visible: bool) -> None:
        try:
            if visible and self._current_step == "adb":
                self._adb_row.pack(anchor="w", pady=(PAD_SMALL, 0))
                self._adb_button.configure(state="disabled" if self._busy else "normal")
            else:
                self._adb_row.pack_forget()
        except tk.TclError:  # pragma: no cover
            pass

    def _install_adb(self) -> None:
        if self._busy:
            return
        kind, command = android.adb_install_plan()
        if kind == "manual":
            self._set("adb", self.messages["android_adb_manual"], "Panel.Warning.TLabel")
            self._show_adb_button(False)
            return
        if not self._ask_install(kind, command):
            self._adb_hint.configure(text=self.messages["android_adb_declined"])
            return

        self._busy = True
        self._adb_error = ""
        self._arm_watch(False)
        try:
            self._adb_button.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass
        self._set("adb", self.messages["android_adb_installing"], "Panel.TLabel")
        self._adb_hint.configure(text=command)
        accepted = kind == "winget"

        def work() -> None:
            ok, message = android.install_adb(
                accept_licence=accepted,
                on_output=lambda line: self._post(self._adb_output, line),
            )
            self._post(self._adb_installed, ok, message)

        threading.Thread(target=work, name="clipster-adb-install", daemon=True).start()

    def _ask_install(self, kind: str, command: str) -> bool:
        from tkinter import messagebox

        if kind == "winget":
            question = self.messages.format("android_adb_ask_windows", command=command,
                                            url=android.SDK_TERMS_URL)
        else:
            question = self.messages.format("android_adb_ask", command=command)
        try:
            return bool(messagebox.askyesno(self.messages["android_adb_install"], question,
                                            parent=self.window))
        except tk.TclError:  # pragma: no cover
            return False

    def _adb_output(self, line: str) -> None:
        text = line.strip()
        if len(text) > 90:
            text = text[:89] + "…"
        self._adb_hint.configure(text=text)

    def _adb_installed(self, ok: bool, message: str) -> None:
        self._busy = False
        if ok:
            self._set("adb", self.messages.format("android_adb_installed", path=message),
                      "Panel.Success.TLabel")
            self._adb_hint.configure(text="")
            self._show_adb_button(False)
        else:
            self._adb_error = message
            self._set("adb", self.messages.format("android_adb_install_failed", details=message),
                      "Panel.Danger.TLabel")
            self._adb_hint.configure(text=self._adb_install_hint())
            self._show_adb_button(True)
        self.refresh_device()
        self._refresh_wizard()

    # ------------------------------------------------------------------
    # Termux install (step 6)
    # ------------------------------------------------------------------
    def _start_termux_install(self) -> None:
        if self._busy or self._device is None:
            return
        self._busy = True
        self._arm_watch(False)
        self._update_primary_button()
        serial = self._device.serial
        self._set_termux_status(self.messages["android_termux_downloading"])

        def work() -> None:
            ok = self._ensure_official_termux(serial)
            self._post(self._termux_install_done, ok)

        threading.Thread(target=work, name="clipster-termux-install", daemon=True).start()

    def _ensure_official_termux(self, serial: str) -> bool:
        replace = False
        if android.termux_installed(serial) and android.termux_is_play_store(serial):
            self._post(self._set_termux_status,
                       self.messages["android_termux_play_found"], "Panel.Warning.TLabel")
            self._post(self._show_termux_play_prompt)
            if not self._ask_replace_termux_sync():
                self._post(self._set_termux_status,
                           self.messages["android_termux_play_kept"], "Panel.Warning.TLabel")
                return False
            replace = True
        elif android.termux_installed(serial):
            return True
        else:
            self._post(self._set_termux_status,
                       self.messages["android_termux_missing"], "Panel.Warning.TLabel")

        workspace = Path(tempfile.mkdtemp(prefix="clipster-termux-"))
        apk_path: Optional[Path] = None
        try:
            self._post(self._set_termux_status, self.messages["android_termux_downloading"])
            ok, message, apk_path = android.install_official_termux(
                serial,
                workspace=workspace,
                on_status=lambda key: self._post(self._show_termux_status, key),
                replace_existing=replace,
                keep_apk=True,
            )
            while not ok:
                if message.startswith("verification_blocked:") or android.is_verification_failure(message):
                    self._post(self._set_termux_status,
                               self.messages["android_termux_usb_blocked"],
                               "Panel.Warning.TLabel")
                    self._post(self._show_termux_usb_prompt)
                    action = self._ask_usb_install_blocked_sync()
                    self._post(self._hide_termux_usb_prompt)
                    if action == "abort":
                        self._post(self._set_termux_status,
                                   self.messages["android_termux_usb_aborted"],
                                   "Panel.Warning.TLabel")
                        return False
                    if action == "wait_manual":
                        self._post(self._set_termux_status,
                                   self.messages["android_termux_waiting_manual"])
                        if android.wait_until_termux_installed(
                            serial, timeout=300.0, poll=2.0,
                            on_tick=lambda: self._post(
                                self._set_termux_status,
                                self.messages["android_termux_waiting_manual"]),
                        ):
                            ok = True
                            break
                        self._post(self._set_termux_status,
                                   self.messages["android_termux_wait_timeout"],
                                   "Panel.Danger.TLabel")
                        return False
                    if apk_path is not None and apk_path.is_file():
                        self._post(self._set_termux_status,
                                   self.messages["android_termux_installing"])
                        ok, message = android.install_apk(apk_path, serial=serial)
                        if ok:
                            break
                        if android.is_verification_failure(message):
                            android.push_apk_for_manual_install(apk_path, serial=serial)
                            android.open_file_manager(serial)
                            message = "verification_blocked:{0}".format(message)
                            continue
                    else:
                        ok, message, apk_path = android.install_official_termux(
                            serial, workspace=workspace, keep_apk=True,
                            on_status=lambda key: self._post(self._show_termux_status, key),
                        )
                        continue
                self._post(self._set_termux_status,
                           self.messages.format("android_termux_install_failed",
                                                details=message),
                           "Panel.Danger.TLabel")
                return False

            self._post(self._set_termux_status,
                       self.messages["android_termux_installed"], "Panel.Success.TLabel")
            return True
        finally:
            try:
                if apk_path is not None:
                    apk_path.unlink(missing_ok=True)
                workspace.rmdir()
            except OSError:  # pragma: no cover
                pass

    def _ask_replace_termux_sync(self) -> bool:
        try:
            return bool(self._termux_choice_queue.get(timeout=600))
        except queue.Empty:  # pragma: no cover
            return False

    def _ask_usb_install_blocked_sync(self) -> str:
        try:
            return str(self._termux_action_queue.get(timeout=600))
        except queue.Empty:  # pragma: no cover
            return "abort"

    def _show_termux_status(self, status: str) -> None:
        key = TERMUX_STATUS.get(status)
        if key is not None:
            self._set_termux_status(self.messages[key])

    # ------------------------------------------------------------------
    # Setup (step 7) — pack, push, run
    # ------------------------------------------------------------------
    def start_install(self) -> None:
        if self._busy or self._device is None:
            return
        if not self._terms_accepted:
            return

        serial = self._device.serial
        self._busy = True
        self._arm_watch(False)
        self._progress.configure(value=0)
        self._update_primary_button()
        self._set("transfer", self.messages["android_packing"])
        self._set("finish", self.messages["android_run_opening"])

        def work() -> None:
            self._post(self._set, "transfer", self.messages["android_packing"])
            workspace = Path(tempfile.mkdtemp(prefix="clipster-android-"))
            bundle = workspace / android.BUNDLE_NAME
            script = workspace / android.SETUP_SCRIPT_NAME
            in_home = False
            try:
                in_home = android.termux_run_as_available(serial)
                android.make_bundle(paths.PROJECT_ROOT, bundle)
                android.write_setup_script(script, accept_terms=True, in_home=in_home)
                self._post(self._set, "transfer", self.messages["android_sending"])
                ok, message, in_home = android.transfer(
                    bundle, script, serial=serial,
                    on_progress=lambda percent: self._post(self._progress_to, percent),
                )
            except Exception as exc:  # pragma: no cover
                log.exception("The transfer failed")
                ok, message, in_home = False, str(exc), False
            finally:
                for path in (bundle, script):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:  # pragma: no cover
                        pass
                try:
                    workspace.rmdir()
                except OSError:  # pragma: no cover
                    pass
            if not ok:
                self._post(self._install_failed, "transfer",
                           self.messages.format("android_send_failed", details=message))
                return

            self._launch_in_home = in_home
            self._post(self._set_command_text, android.launch_command(in_home=in_home))
            self._post(self._progress_to, 100)
            self._post(self._set, "transfer",
                       self.messages.format("android_sent", target=message),
                       "Panel.Success.TLabel")
            self._post(self._set, "finish", self.messages["android_run_opening"])
            ok, reason = android.run_on_phone(
                android.launch_command(in_home=in_home),
                serial,
                on_status=lambda key: self._post(self._show_run_status, key),
            )
            self._post(self._install_run_done, ok, reason)

        threading.Thread(target=work, name="clipster-android-install", daemon=True).start()

    start_transfer = start_install

    def _progress_to(self, percent: int) -> None:
        try:
            self._progress.configure(value=max(0, min(100, int(percent))))
        except tk.TclError:  # pragma: no cover
            pass

    def _install_aborted(self, message: str) -> None:
        self._busy = False
        self._set("finish", message, "Panel.Warning.TLabel")
        self._refresh_wizard()
        self._arm_watch(True)

    def _install_failed(self, step: str, message: str) -> None:
        self._busy = False
        self._set(step, message, "Panel.Danger.TLabel")
        self._refresh_wizard()
        self._arm_watch(True)

    def _install_run_done(self, ok: bool, reason: str) -> None:
        self._busy = False
        try:
            self._run_button.configure(state="normal")
        except tk.TclError:  # pragma: no cover
            pass
        if ok:
            self._set("finish", self.messages["android_run_started"], "Panel.Success.TLabel")
            if "setup" not in self._completed:
                self._completed.add("setup")
            self._current_step = "done"
            self._completed.add("done")
        else:
            key = RUN_FAILURES.get(reason, "android_run_failed")
            self._set("finish", self.messages[key], "Panel.Warning.TLabel")
        self._refresh_wizard()
        self._arm_watch(not ok)

    def _set_command_text(self, text: str) -> None:
        try:
            self._command.configure(state="normal")
            self._command.delete("1.0", "end")
            self._command.insert("1.0", text)
            self._command.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass

    def _copy_command(self) -> None:
        self._on_copy(android.launch_command(in_home=self._launch_in_home))
        self._set("finish", self.messages["android_copied"], "Panel.Success.TLabel")

    def _run_on_phone(self) -> None:
        if self._busy or self._device is None:
            return
        if not self._terms_accepted and not self._ask_app_terms():
            self._set("finish", self.messages["android_terms_declined"], "Panel.Warning.TLabel")
            return
        serial = self._device.serial
        in_home = self._launch_in_home or android.termux_run_as_available(serial)
        self._launch_in_home = in_home
        command = android.launch_command(in_home=in_home)
        self._set_command_text(command)
        self._busy = True
        self._arm_watch(False)
        try:
            self._run_button.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass
        self._set("finish", self.messages["android_run_opening"])

        def work() -> None:
            workspace = Path(tempfile.mkdtemp(prefix="clipster-android-script-"))
            script = workspace / android.SETUP_SCRIPT_NAME
            try:
                android.write_setup_script(script, accept_terms=True, in_home=in_home)
                if in_home:
                    android.push_into_termux(script, serial=serial)
                else:
                    android.push(script, serial=serial)
            except Exception:  # pragma: no cover
                log.exception("Could not refresh the setup script on the phone")
            finally:
                try:
                    script.unlink(missing_ok=True)
                    workspace.rmdir()
                except OSError:  # pragma: no cover
                    pass
            ok, reason = android.run_on_phone(
                command, serial,
                on_status=lambda key: self._post(self._show_run_status, key),
            )
            self._post(self._run_done, ok, reason)

        threading.Thread(target=work, name="clipster-adb-type", daemon=True).start()

    def _ask_app_terms(self) -> bool:
        from tkinter import messagebox

        body = self.messages["terms_app_body"]
        if len(body) > 900:
            body = body[:899] + "…"
        question = "{0}\n\n{1}\n\n{2}".format(
            self.messages["terms_app_title"],
            body,
            self.messages["android_terms_ask"],
        )
        try:
            return bool(messagebox.askyesno(self.messages["terms_app_title"], question,
                                            parent=self.window))
        except tk.TclError:  # pragma: no cover
            return False

    def _show_run_status(self, status: str) -> None:
        key = RUN_STATUS.get(status)
        if key is not None:
            self._set("finish", self.messages[key])

    def _run_done(self, ok: bool, reason: str) -> None:
        self._install_run_done(ok, reason)

    def _open_termux(self) -> None:
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
        if self._closed:
            return
        self._results.put((function, args))

    def _pump(self) -> None:
        if self._closed:
            return
        while True:
            try:
                function, args = self._results.get_nowait()
            except queue.Empty:
                break
            try:
                function(*args)
            except Exception:  # pragma: no cover
                log.exception("An adb result could not be shown")
        try:
            self._pump_job = self.window.after(PUMP_MS, self._pump)
        except tk.TclError:  # pragma: no cover
            self._pump_job = None

    def close(self) -> None:
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
