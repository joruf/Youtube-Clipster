"""The small navigation window that appears when a link is copied.

It walks one download from A to Z in a single compact window: choose format and
audio track, watch the progress, see the result.  The large
:class:`clipster.viewwindow.ViewWindow` is a separate window and is never needed
for a download.

Every method must be called from the Tk main thread - worker threads go through
:class:`clipster.bridge.TkBridge`.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional

from . import APP_SHORT_NAME, clip, theme
from .bridge import Prompt
from .history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, format_duration
from .i18n import Messages
from .logging_setup import get_logger
from .theme import PAD, PAD_SMALL

log = get_logger(__name__)

#: Marks shown in front of the result line.
_MARKS = {STATUS_OK: "✓", STATUS_FAILED: "✕", STATUS_CANCELED: "–"}

#: Width of the window in pixels.
_WIDTH = 460


def _shorten(text: str, limit: int = 90) -> str:
    """Return ``text`` truncated to ``limit`` characters with an ellipsis."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


class NavWindow:
    """The compact "one download" window."""

    def __init__(
        self,
        master: tk.Misc,
        messages: Messages,
        palette: theme.Palette,
        icon: Optional[tk.PhotoImage],
        on_close: Callable[[], None],
        on_open_file: Callable[[], None],
        on_open_folder: Callable[[], None],
    ) -> None:
        """
        :param master: The hidden Tk root.
        :param messages: The active translation table.
        :param palette: The colour scheme.
        :param icon: Window icon, or ``None``.
        :param on_close: Called when the user closes or dismisses the window.
        :param on_open_file: Called by the "open file" button of the result view.
        :param on_open_folder: Called by the "folder" button of the result view.
        """
        self.messages = messages
        self.palette = palette
        self.fonts = theme.fonts()
        #: Set when the user presses cancel; the downloader watches it.
        self.cancel_event = threading.Event()

        self._on_close = on_close
        self._on_open_file = on_open_file
        self._on_open_folder = on_open_folder
        self._prompt: Optional[Prompt] = None
        self._bar_mode = "determinate"
        self._result_path: Optional[Path] = None
        self._auto_close_job: Optional[str] = None

        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.title(APP_SHORT_NAME)
        self.window.configure(background=palette.base)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._closed)
        if icon is not None:
            try:
                self.window.iconphoto(False, icon)
            except tk.TclError:  # pragma: no cover
                pass

        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        """Create the fixed parts: headline, meta line, and the swap areas."""
        outer = ttk.Frame(self.window, padding=PAD, style="TFrame")
        outer.pack(fill="both", expand=True)

        self._headline = ttk.Label(
            outer, text="", style="Bold.TLabel", wraplength=_WIDTH - 2 * PAD, justify="left"
        )
        self._headline.pack(anchor="w", fill="x")

        self._meta = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=_WIDTH - 2 * PAD, justify="left")
        self._meta.pack(anchor="w", fill="x", pady=(2, 0))

        #: Format and audio track selectors live here.
        self._form = ttk.Frame(outer, style="TFrame")

        self._value = tk.DoubleVar(value=0.0)
        self._bar = ttk.Progressbar(outer, orient="horizontal", mode="determinate", variable=self._value)

        self._status = ttk.Label(outer, text="", wraplength=_WIDTH - 2 * PAD, justify="left")
        self._detail = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=_WIDTH - 2 * PAD, justify="left")

        self._buttons = ttk.Frame(outer, style="TFrame")

        self._format = tk.StringVar(value="mp3")
        self._language = tk.StringVar(value="")
        self._clip_start = tk.StringVar(value="")
        self._clip_end = tk.StringVar(value="")
        #: Length of the video being asked about, for validating the section.
        self._clip_duration = 0
        #: The hint below the section fields; also carries its error message.
        self._clip_hint: Optional[ttk.Label] = None

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------
    def begin(self, headline: str) -> None:
        """Show the window at the start of a run.

        :param headline: A "link received" placeholder until the title is known.
        :return: None
        """
        self.cancel_event = threading.Event()
        self._prompt = None
        self._result_path = None
        self._cancel_auto_close()
        self._headline.configure(text=_shorten(headline), style="Bold.TLabel")
        self._meta.configure(text="")
        self._status.configure(text=self.messages["fetching_metadata"], style="TLabel")
        self._detail.configure(text="")
        self._clear_form()
        self._clear_buttons()
        self._status.pack(anchor="w", fill="x", pady=(PAD_SMALL, 0))
        self.set_percent(None)
        self._show()

    def _show(self) -> None:
        """Place the window near the bottom right corner and raise it."""
        try:
            self.window.update_idletasks()
            width = max(_WIDTH, self.window.winfo_reqwidth())
            height = self.window.winfo_reqheight()
            x = max(0, self.window.winfo_screenwidth() - width - 48)
            y = max(0, self.window.winfo_screenheight() - height - 96)
            self.window.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))
            self.window.deiconify()
            self.window.lift()
            self.window.attributes("-topmost", True)
            self.window.after(600, lambda: self._drop_topmost())
        except tk.TclError:  # pragma: no cover
            pass

    def _drop_topmost(self) -> None:
        """Stop forcing the window above everything else."""
        try:
            self.window.attributes("-topmost", False)
        except tk.TclError:  # pragma: no cover
            pass

    def _resize(self) -> None:
        """Grow or shrink the window to its current content."""
        try:
            self.window.update_idletasks()
            width = max(_WIDTH, self.window.winfo_reqwidth())
            self.window.geometry("{0}x{1}".format(width, self.window.winfo_reqheight()))
        except tk.TclError:  # pragma: no cover
            pass

    def hide(self) -> None:
        """Hide the window and stop the progress animation."""
        self._cancel_auto_close()
        self._stop_bar()
        self._bar.pack_forget()
        try:
            self.window.withdraw()
        except tk.TclError:  # pragma: no cover
            pass

    def visible(self) -> bool:
        """Return ``True`` while the window is on screen."""
        try:
            return self.window.state() != "withdrawn"
        except tk.TclError:  # pragma: no cover
            return False

    def _closed(self) -> None:
        """Handle the window close box: cancel whatever is pending."""
        self.cancel_event.set()
        if self._prompt is not None:
            prompt, self._prompt = self._prompt, None
            prompt.cancel()
        self._on_close()

    # ------------------------------------------------------------------
    # Content helpers
    # ------------------------------------------------------------------
    def _clear_form(self) -> None:
        """Remove the selector widgets."""
        for child in self._form.winfo_children():
            child.destroy()
        self._clip_hint = None
        self._form.pack_forget()

    def _clear_buttons(self) -> None:
        """Remove the button row."""
        for child in self._buttons.winfo_children():
            child.destroy()
        self._buttons.pack_forget()

    def set_headline(self, text: str) -> None:
        """Replace the bold first line.

        :param text: The new headline.
        :return: None
        """
        self._headline.configure(text=_shorten(text))

    def set_status(self, text: str, detail: str = "") -> None:
        """Update the status line and the muted detail line below it.

        :param text: The new status text.
        :param detail: Optional extra line (speed, ETA, ...).
        :return: None
        """
        self._status.configure(text=text, style="TLabel")
        self._detail.configure(text=detail)
        if detail and not self._detail.winfo_ismapped():
            self._detail.pack(anchor="w", fill="x")

    def set_percent(self, value: Optional[float]) -> None:
        """Set the bar to a percentage, or to the indeterminate animation.

        :param value: 0-100, or ``None`` for "unknown duration".
        :return: None
        """
        if not self._bar.winfo_ismapped():
            self._bar.pack(fill="x", pady=(PAD_SMALL, PAD_SMALL))
        if value is None:
            if self._bar_mode != "indeterminate":
                self._bar.configure(mode="indeterminate")
                self._bar.start(15)
                self._bar_mode = "indeterminate"
            return
        if self._bar_mode != "determinate":
            self._stop_bar()
        self._value.set(max(0.0, min(100.0, value)))

    def _stop_bar(self) -> None:
        """Switch the bar back to determinate mode."""
        if self._bar_mode == "indeterminate":
            try:
                self._bar.stop()
            except tk.TclError:  # pragma: no cover
                pass
        self._bar.configure(mode="determinate")
        self._bar_mode = "determinate"

    # ------------------------------------------------------------------
    # The one question: format and audio track together
    # ------------------------------------------------------------------
    def ask(
        self,
        prompt: Prompt,
        title: str,
        duration: int,
        languages: List[str],
        default_format: str = "mp3",
        ask_language: bool = True,
        original: str = "",
    ) -> None:
        """Ask for format, audio track and section in one step.

        The answer handed to ``prompt`` is a dict with the keys ``format``,
        ``language`` and ``section`` (a :class:`clipster.clip.ClipRange` or
        ``None`` for the whole video), or ``None`` when the user cancels.

        :param prompt: The prompt the worker thread is waiting on.
        :param title: The video title.
        :param duration: Video length in seconds, ``0`` when unknown.
        :param languages: Available audio track languages, the original first.
        :param default_format: Preselected format (``mp3`` or ``mp4``).
        :param ask_language: Offer the audio track selector at all.
        :param original: Code of the track the video was published with.
        :return: None
        """
        self._prompt = prompt
        self._clear_form()
        self._clear_buttons()
        self._stop_bar()
        self._bar.pack_forget()
        self._detail.pack_forget()
        self._status.pack_forget()

        self._headline.configure(text=_shorten(title))
        meta = [format_duration(duration)] if duration else []
        self._meta.configure(text="  ·  ".join(meta) if meta else "")

        self._format.set(default_format if default_format in ("mp3", "mp4") else "mp3")
        self._form.pack(fill="x", pady=(PAD, 0))
        self._form.columnconfigure(1, weight=1)

        labels = {
            "mp3": self.messages["format_mp3"],
            "mp4": self.messages["format_mp4"],
        }
        self._format_labels = labels
        ttk.Label(self._form, text=self.messages["nav_format"], style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, PAD_SMALL), pady=(0, 6)
        )
        format_box = ttk.Combobox(
            self._form,
            state="readonly",
            values=[labels["mp3"], labels["mp4"]],
            font=self.fonts["body"],
        )
        format_box.set(labels[self._format.get()])
        format_box.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        format_box.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._format.set("mp3" if format_box.get() == labels["mp3"] else "mp4"),
        )

        offer_language = ask_language and len(languages) > 1
        if offer_language:
            self._language.set("")
            values = [self.messages["lang_best"]]
            for code in languages:
                label = self.messages.language_label(code)
                if code == original:
                    label = self.messages.format("lang_original", language=label)
                values.append(label)
            codes = [""] + list(languages)
            ttk.Label(self._form, text=self.messages["nav_audio"], style="Muted.TLabel").grid(
                row=1, column=0, sticky="w", padx=(0, PAD_SMALL)
            )
            language_box = ttk.Combobox(
                self._form, state="readonly", values=values, font=self.fonts["body"]
            )
            language_box.current(0)
            language_box.grid(row=1, column=1, sticky="ew")
            language_box.bind(
                "<<ComboboxSelected>>",
                lambda _e: self._language.set(codes[language_box.current()] if language_box.current() >= 0 else ""),
            )
        else:
            self._language.set("")

        self._build_section_fields(duration)

        self._buttons.pack(fill="x", pady=(PAD, 0))
        download = ttk.Button(
            self._buttons, text=self.messages["button_download"], style="Accent.TButton", command=self._submit
        )
        download.pack(side="right")
        ttk.Button(self._buttons, text=self.messages["button_cancel"], command=self._cancel_question).pack(
            side="right", padx=(0, PAD_SMALL)
        )
        download.focus_set()
        self.window.bind("<Return>", lambda _e: self._submit())
        self.window.bind("<Escape>", lambda _e: self._cancel_question())
        self._resize()

    def _build_section_fields(self, duration: int) -> None:
        """Add the two "from" and "to" fields below the selectors.

        Both are empty by default, which means the whole video - the section is
        an extra the user reaches for, never something to dismiss first.

        :param duration: Video length in seconds, ``0`` when unknown.
        :return: None
        """
        self._clip_duration = max(0, int(duration or 0))
        self._clip_start.set("")
        self._clip_end.set("")

        ttk.Label(self._form, text=self.messages["nav_clip"], style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, PAD_SMALL), pady=(6, 0)
        )
        row = ttk.Frame(self._form, style="TFrame")
        row.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        ttk.Entry(row, textvariable=self._clip_start, width=8, font=self.fonts["body"]).pack(side="left")
        ttk.Label(row, text="–", style="Muted.TLabel").pack(side="left", padx=PAD_SMALL)
        ttk.Entry(row, textvariable=self._clip_end, width=8, font=self.fonts["body"]).pack(side="left")

        # Across both columns: kept inside the value column the hint would make
        # the whole window as wide as one line of it.
        self._clip_hint = ttk.Label(
            self._form,
            text=self.messages["nav_clip_hint"],
            style="Muted.TLabel",
            wraplength=_WIDTH - 2 * PAD,
            justify="left",
        )
        self._clip_hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _show_clip_error(self, key: str) -> None:
        """Replace the hint below the section fields with a problem.

        :param key: One of the ``clip_error_*`` message keys.
        :return: None
        """
        if self._clip_hint is None:  # pragma: no cover - the fields carry it
            return
        self._clip_hint.configure(text=self.messages[key], style="Danger.TLabel")
        self._resize()

    def _submit(self) -> None:
        """Hand format, audio track and section to the waiting worker.

        A section that cannot work is reported in place of the hint and the
        question stays open; nothing is silently dropped or rounded into
        something the user did not ask for.
        """
        if self._prompt is None:
            return
        section, error = clip.parse_range(
            self._clip_start.get(), self._clip_end.get(), self._clip_duration
        )
        if error:
            self._show_clip_error(error)
            return
        prompt, self._prompt = self._prompt, None
        answer: Dict[str, Any] = {
            "format": self._format.get(),
            "language": self._language.get(),
            "section": section,
        }
        self._clear_form()
        self._clear_buttons()
        self._unbind_keys()
        prompt.answer(answer)

    def _cancel_question(self) -> None:
        """Answer the pending question with "canceled"."""
        self.cancel_event.set()
        prompt, self._prompt = self._prompt, None
        self._clear_form()
        self._clear_buttons()
        self._unbind_keys()
        if prompt is not None:
            prompt.cancel()

    def _unbind_keys(self) -> None:
        """Drop the Return/Escape shortcuts of the question step."""
        for sequence in ("<Return>", "<Escape>"):
            try:
                self.window.unbind(sequence)
            except tk.TclError:  # pragma: no cover
                pass

    def cancel_pending(self) -> bool:
        """Cancel a question that is still waiting for an answer.

        :return: ``True`` when a question was actually pending.
        """
        if self._prompt is None:
            return False
        self._cancel_question()
        return True

    def question_pending(self) -> bool:
        """Return ``True`` while a question waits for the user."""
        return self._prompt is not None

    # ------------------------------------------------------------------
    # Progress and result
    # ------------------------------------------------------------------
    def show_progress(self, media_format: str, duration: int) -> None:
        """Switch to the downloading view.

        :param media_format: ``mp3`` or ``mp4``, shown in the meta line.
        :param duration: Video length in seconds, ``0`` when unknown.
        :return: None
        """
        self._clear_form()
        self._clear_buttons()
        meta = [media_format.upper()]
        if duration:
            meta.append(format_duration(duration))
        self._meta.configure(text="  ·  ".join(meta))
        if not self._status.winfo_ismapped():
            self._status.pack(anchor="w", fill="x", pady=(PAD_SMALL, 0))
        self.set_percent(0.0)
        if not self._detail.winfo_ismapped():
            self._detail.pack(anchor="w", fill="x")
        self._buttons.pack(fill="x", pady=(PAD_SMALL, 0))
        ttk.Button(self._buttons, text=self.messages["button_cancel"], command=self._request_cancel).pack(
            side="right"
        )
        self._resize()

    def _request_cancel(self) -> None:
        """Flag the running download as canceled."""
        self.cancel_event.set()
        self._status.configure(text=self.messages["progress_canceled"], style="Warning.TLabel")
        for child in self._buttons.winfo_children():
            try:
                child.state(["disabled"])  # type: ignore[attr-defined]
            except (AttributeError, tk.TclError):  # pragma: no cover
                pass

    def finish(self, text: str, status: str, detail: str = "", path: Optional[Path] = None) -> None:
        """Show the result and the closing buttons.

        :param text: The result text.
        :param status: ``ok``, ``failed`` or ``canceled``.
        :param detail: Optional muted second line.
        :param path: The downloaded file, enables the open buttons.
        :return: None
        """
        self._clear_form()
        self._clear_buttons()
        self._stop_bar()
        self._bar.pack_forget()
        self._result_path = path

        style = {
            STATUS_OK: "Success.TLabel",
            STATUS_FAILED: "Danger.TLabel",
            STATUS_CANCELED: "Warning.TLabel",
        }.get(status, "TLabel")
        if not self._status.winfo_ismapped():
            self._status.pack(anchor="w", fill="x", pady=(PAD_SMALL, 0))
        self._status.configure(text="{0} {1}".format(_MARKS.get(status, ""), text).strip(), style=style)
        self._detail.configure(text=detail)
        if detail and not self._detail.winfo_ismapped():
            self._detail.pack(anchor="w", fill="x")

        self._buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(self._buttons, text=self.messages["nav_close"], style="Accent.TButton",
                   command=self._closed).pack(side="right")
        if path is not None:
            ttk.Button(self._buttons, text=self.messages["history_play"], command=self._on_open_file).pack(
                side="right", padx=(0, PAD_SMALL)
            )
            ttk.Button(self._buttons, text=self.messages["history_folder"], command=self._on_open_folder).pack(
                side="right", padx=(0, PAD_SMALL)
            )
        self._resize()
        if status == STATUS_OK:
            # Brief success flash, then dismiss so Streaming / Downloads stay in focus.
            self._auto_close_job = self.window.after(1500, self._auto_close)

    def _cancel_auto_close(self) -> None:
        """Cancel a pending auto-close after a successful download."""
        if self._auto_close_job is None:
            return
        try:
            self.window.after_cancel(self._auto_close_job)
        except tk.TclError:  # pragma: no cover
            pass
        self._auto_close_job = None

    def _auto_close(self) -> None:
        """Hide the navigation window after a successful download."""
        self._auto_close_job = None
        self._on_close()

    def already_downloaded(self, title: str, path: Path, detail: str,
                           on_again: Callable[[], None]) -> None:
        """Report that this video is already on disk instead of fetching it again.

        :param title: The video title.
        :param path: The file that already exists.
        :param detail: Muted second line, usually name and size.
        :param on_again: Called when the user wants it downloaded once more.
        :return: None
        """
        self._clear_form()
        self._clear_buttons()
        self._stop_bar()
        self._bar.pack_forget()
        self._result_path = path

        self._headline.configure(text=_shorten(title))
        if not self._status.winfo_ismapped():
            self._status.pack(anchor="w", fill="x", pady=(PAD_SMALL, 0))
        self._status.configure(text=self.messages["nav_already"], style="Success.TLabel")
        self._detail.configure(text=detail)
        if not self._detail.winfo_ismapped():
            self._detail.pack(anchor="w", fill="x")

        self._buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(self._buttons, text=self.messages["nav_close"], style="Accent.TButton",
                   command=self._closed).pack(side="right")
        ttk.Button(self._buttons, text=self.messages["history_play"],
                   command=self._on_open_file).pack(side="right", padx=(0, PAD_SMALL))
        ttk.Button(self._buttons, text=self.messages["history_folder"],
                   command=self._on_open_folder).pack(side="right", padx=(0, PAD_SMALL))
        ttk.Button(self._buttons, text=self.messages["nav_download_again"],
                   command=on_again).pack(side="left")
        self._resize()

    def result_path(self) -> Optional[Path]:
        """Return the file of the last finished download, if any."""
        return self._result_path

    def destroy(self) -> None:
        """Tear the window down."""
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
