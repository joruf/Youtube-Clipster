"""The Phone page of the view window: the whole setup, inside the program.

Everything the console wizard does, but as a page: switch the interface on,
choose who may reach it, see the address as a QR code to scan, watch whether a
phone actually got through, and read what is left to do on the phone.

The page never touches the server itself - it asks the application through the
callbacks it was given, and re-reads the state it gets back.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, Optional

from . import theme
from .config import Config
from .i18n import Messages
from .logging_setup import get_logger
from .qrview import draw_qr
from .scroller import Scroller
from .theme import PAD, PAD_SMALL

log = get_logger(__name__)

#: How often the page refreshes the state while it is visible, in milliseconds.
REFRESH_MS = 2000

#: Edge length of the QR code, in pixels.
QR_SIZE = 240

#: The two choices behind ``remote_bind``, in the order they are offered.
BIND_LOCAL = "127.0.0.1"
BIND_EVERYWHERE = "0.0.0.0"


class PhonePage(ttk.Frame):
    """Sets up and monitors the phone interface."""

    def __init__(
        self,
        master: tk.Misc,
        messages: Messages,
        palette: theme.Palette,
        config: Config,
        fonts: dict,
        on_apply: Callable[[bool, str, int], Dict[str, Any]],
        on_new_token: Callable[[], Dict[str, Any]],
        on_state: Callable[[], Dict[str, Any]],
        on_copy: Callable[[str], None],
        on_firewall_hint: Callable[[int], Any],
    ) -> None:
        """
        :param master: Parent widget.
        :param messages: Translation table.
        :param palette: Colour scheme.
        :param config: Live configuration.
        :param fonts: Theme font map.
        :param on_apply: Save settings and restart the interface; returns the state.
        :param on_new_token: Replace the token; returns the state.
        :param on_state: Return the current state of the interface.
        :param on_copy: Put a string on the clipboard.
        :param on_firewall_hint: Return ``(description, command)`` for a port.
        """
        super().__init__(master, style="TFrame", padding=PAD)
        self.messages = messages
        self.palette = palette
        self.config = config
        self.fonts = fonts
        self._on_apply = on_apply
        self._on_new_token = on_new_token
        self._on_state = on_state
        self._on_copy = on_copy
        self._on_firewall_hint = on_firewall_hint

        self._enabled = tk.BooleanVar(value=config.remote_enabled)
        self._bind = tk.StringVar(value=config.remote_bind)
        self._port = tk.StringVar(value=str(config.remote_port))
        self._token_visible = False
        self._refresh_job: Optional[str] = None
        self._last_url = ""
        self._last_contact = ""

        self._build()

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    def _build(self) -> None:
        """Create the page."""
        ttk.Label(self, text=self.messages["page_phone"], style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text=self.messages["phone_intro"], style="Muted.TLabel",
                  wraplength=760, justify="left").pack(anchor="w", pady=(2, PAD))

        # Scrollable: at the smallest allowed window size the cards are taller
        # than the page, and a clipped card is worse than a scrollbar.
        self._scroller = Scroller(self, self.palette)
        self._scroller.pack(fill="both", expand=True)
        columns = ttk.Frame(self._scroller.body, style="Panel.TFrame")
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=1)
        columns.columnconfigure(1, weight=0)

        left = ttk.Frame(columns, style="Panel.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD_SMALL))
        right = ttk.Frame(columns, style="Panel.TFrame")
        right.grid(row=0, column=1, sticky="n")
        # The steps run under both columns: side by side they stay short, and
        # the space next to the code would be wasted otherwise.
        below = ttk.Frame(columns, style="Panel.TFrame")
        below.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))

        self._build_connection(left)
        self._build_code(right)
        self._build_steps(below)
        # The wheel has to work over the cards too, not only over empty space.
        self._scroller.bind_wheel_tree(columns)
        self.refresh()

    def _build_connection(self, master: tk.Misc) -> None:
        """Create the settings and status card."""
        card = ttk.LabelFrame(master, text=self.messages["phone_connection"],
                              style="Card.TLabelframe", padding=PAD)
        card.pack(fill="x")

        ttk.Checkbutton(card, text=self.messages["phone_enable"], variable=self._enabled,
                        style="TCheckbutton", command=self._apply).pack(anchor="w")
        ttk.Label(card, text=self.messages["phone_enable_hint"], style="Panel.Muted.TLabel",
                  wraplength=420, justify="left").pack(anchor="w", pady=(2, PAD_SMALL))

        ttk.Label(card, text=self.messages["phone_reach"], style="Panel.Muted.TLabel").pack(anchor="w")
        for value, label in ((BIND_LOCAL, "phone_reach_local"), (BIND_EVERYWHERE, "phone_reach_network")):
            ttk.Radiobutton(card, text=self.messages[label], value=value, variable=self._bind,
                            style="TRadiobutton", command=self._apply).pack(anchor="w")

        port_row = ttk.Frame(card, style="Panel.TFrame")
        port_row.pack(fill="x", pady=(PAD_SMALL, 0))
        ttk.Label(port_row, text=self.messages["phone_port"], style="Panel.Muted.TLabel").pack(side="left")
        entry = ttk.Entry(port_row, textvariable=self._port, width=8, font=self.fonts["body"])
        entry.pack(side="left", padx=(PAD_SMALL, PAD_SMALL))
        entry.bind("<Return>", lambda _event: self._apply())
        ttk.Button(port_row, text=self.messages["phone_apply"], style="Row.TButton",
                   command=self._apply).pack(side="left")

        self._status = ttk.Label(card, text="", style="Panel.TLabel", wraplength=420,
                                 justify="left", font=self.fonts["body"])
        self._status.pack(anchor="w", pady=(PAD, 0))

        self._firewall = ttk.Label(card, text="", style="Panel.Muted.TLabel", wraplength=420,
                                   justify="left")
        self._firewall.pack(anchor="w", pady=(PAD_SMALL, 0))
        self._firewall_row = ttk.Frame(card, style="Panel.TFrame")
        self._firewall_row.pack(fill="x", pady=(2, 0))
        self._firewall_command = ttk.Label(self._firewall_row, text="", style="Panel.TLabel",
                                           font=self.fonts["body"], wraplength=330,
                                           justify="left")
        self._firewall_command.pack(side="left")
        self._firewall_copy = ttk.Button(self._firewall_row, text=self.messages["phone_copy"],
                                         style="Row.TButton",
                                         command=lambda: self._copy(self._firewall_command.cget("text")))
        self._firewall_copy.pack(side="right")

        token_row = ttk.Frame(card, style="Panel.TFrame")
        token_row.pack(fill="x", pady=(PAD, 0))
        ttk.Label(token_row, text=self.messages["phone_token"], style="Panel.Muted.TLabel").pack(side="left")
        self._token = ttk.Label(token_row, text="", style="Panel.TLabel")
        self._token.pack(side="left", padx=(PAD_SMALL, 0))
        self._token_button = ttk.Button(token_row, text=self.messages["phone_token_show"],
                                        style="Row.TButton", command=self._toggle_token)
        self._token_button.pack(side="right")
        ttk.Button(token_row, text=self.messages["phone_token_new"], style="Row.TButton",
                   command=self._new_token).pack(side="right", padx=(0, PAD_SMALL))

    def _build_code(self, master: tk.Misc) -> None:
        """Create the QR code card."""
        card = ttk.LabelFrame(master, text=self.messages["phone_scan"],
                              style="Card.TLabelframe", padding=PAD)
        card.pack(fill="x")

        self._canvas = tk.Canvas(card, width=QR_SIZE, height=QR_SIZE, highlightthickness=0,
                                 background=self.palette.panel, bd=0)
        self._canvas.pack()
        self._code_hint = ttk.Label(card, text="", style="Panel.Muted.TLabel", wraplength=QR_SIZE,
                                    justify="center")
        self._code_hint.pack(pady=(PAD_SMALL, 0))
        self._address = ttk.Label(card, text="", style="Panel.Muted.TLabel", wraplength=QR_SIZE,
                                  justify="center")
        self._address.pack()
        self._copy_button = ttk.Button(card, text=self.messages["phone_copy_address"],
                                       style="Row.TButton",
                                       command=lambda: self._copy(self._last_url))
        self._copy_button.pack(pady=(PAD_SMALL, 0))

    def _build_steps(self, master: tk.Misc) -> None:
        """Create the card explaining what is left to do on the phone."""
        card = ttk.LabelFrame(master, text=self.messages["phone_on_the_phone"],
                              style="Card.TLabelframe", padding=PAD)
        card.pack(fill="x")
        keys = ("phone_step_scan", "phone_step_install", "phone_step_share")
        for column in range(len(keys)):
            card.columnconfigure(column, weight=1, uniform="steps")
        for column, key in enumerate(keys):
            ttk.Label(card, text=self.messages[key], style="Panel.Muted.TLabel",
                      wraplength=280, justify="left").grid(
                row=0, column=column, sticky="nw",
                padx=(0 if column == 0 else PAD_SMALL, 0))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _port_number(self) -> int:
        """Return the port from the entry, falling back to the stored one."""
        try:
            value = int(str(self._port.get()).strip())
        except (TypeError, ValueError):
            return self.config.remote_port
        return value if 1 <= value <= 65535 else self.config.remote_port

    def _apply(self) -> None:
        """Hand the current selection to the application."""
        port = self._port_number()
        self._port.set(str(port))
        state = self._on_apply(bool(self._enabled.get()), str(self._bind.get()), port)
        self._render(state)

    def _new_token(self) -> None:
        """Replace the token after warning what that costs."""
        from tkinter import messagebox

        if not messagebox.askyesno(self.messages["phone_token_new"],
                                   self.messages["phone_token_new_warning"],
                                   parent=self.winfo_toplevel()):
            return
        self._token_visible = True
        self._render(self._on_new_token())

    def _toggle_token(self) -> None:
        """Show or hide the token."""
        self._token_visible = not self._token_visible
        self.refresh()

    def _copy(self, text: str) -> None:
        """Put ``text`` on the clipboard and say so."""
        if not text:
            return
        self._on_copy(text)
        self._code_hint.configure(text=self.messages["phone_copied"])

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Read the state from the application and render it."""
        try:
            state = self._on_state()
        except Exception:  # pragma: no cover - the page must never take the window down
            log.debug("The phone interface state could not be read", exc_info=True)
            return
        self._render(state)

    def _render(self, state: Dict[str, Any]) -> None:
        """Show one state.

        :param state: The dictionary from :meth:`clipster.app.ClipsterApp.remote_state`.
        :return: None
        """
        self._enabled.set(bool(state.get("enabled")))
        bind = str(state.get("bind") or BIND_LOCAL)
        self._bind.set(bind if bind in (BIND_LOCAL, BIND_EVERYWHERE) else BIND_EVERYWHERE)
        self._port.set(str(state.get("port") or self.config.remote_port))

        self._render_status(state)
        self._render_firewall(state)
        self._render_token(state)
        self._render_code(state)

    def _render_status(self, state: Dict[str, Any]) -> None:
        """Show whether the interface listens, and whether a phone was there."""
        if not state.get("enabled"):
            self._status.configure(text=self.messages["phone_status_off"], style="Panel.Muted.TLabel")
            return
        if not state.get("running"):
            self._status.configure(text=self.messages["phone_status_failed"], style="Panel.Danger.TLabel")
            return
        contact = str(state.get("last_contact") or "")
        if contact:
            text = self.messages.format("phone_status_contact", when=contact.replace("T", " "))
            self._status.configure(text=text, style="Panel.Success.TLabel")
        else:
            self._status.configure(text=self.messages["phone_status_waiting"], style="Panel.TLabel")

    def _render_firewall(self, state: Dict[str, Any]) -> None:
        """Show the firewall situation, and the command that would open the port."""
        if not state.get("enabled") or str(state.get("bind")) == BIND_LOCAL:
            self._firewall.configure(text="")
            self._firewall_command.configure(text="")
            self._firewall_row.pack_forget()
            return
        try:
            description, command = self._on_firewall_hint(int(state.get("port") or 0))
        except Exception:  # pragma: no cover - probing must never break the page
            log.debug("The firewall could not be examined", exc_info=True)
            return
        self._firewall.configure(text=description)
        self._firewall_command.configure(text=command)
        if command:
            self._firewall_row.pack(fill="x", pady=(2, 0))
        else:
            self._firewall_row.pack_forget()

    def _render_token(self, state: Dict[str, Any]) -> None:
        """Show the token, masked unless it was revealed."""
        token = str(state.get("token") or "")
        if not token:
            self._token.configure(text=self.messages["phone_token_none"])
            self._token_button.configure(text=self.messages["phone_token_show"])
            return
        if self._token_visible:
            self._token.configure(text=token)
            self._token_button.configure(text=self.messages["phone_token_hide"])
        else:
            self._token.configure(text="•" * 12)
            self._token_button.configure(text=self.messages["phone_token_show"])

    def _render_code(self, state: Dict[str, Any]) -> None:
        """Draw the QR code for the current address."""
        url = str(state.get("url") or "")
        if url != self._last_url:
            self._last_url = url
            drawn = draw_qr(self._canvas, url, QR_SIZE,
                            dark=self.palette.base, light="#ffffff") if url else False
            if not url:
                self._canvas.delete("all")
                self._code_hint.configure(text=self.messages["phone_code_off"])
            elif not drawn:
                self._code_hint.configure(text=self.messages["phone_code_missing"])
            else:
                self._code_hint.configure(text=self.messages["phone_code_hint"])
            self._address.configure(text=url.split("?")[0] if url else "")
        self._copy_button.configure(state="normal" if url else "disabled")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def start_polling(self) -> None:
        """Refresh regularly while the page is visible."""
        self.stop_polling()
        self.refresh()
        self._refresh_job = self.after(REFRESH_MS, self._poll)

    def _poll(self) -> None:
        """One refresh, then schedule the next."""
        # A refresh queued just before the window went away would run against
        # destroyed widgets, which Tk does not survive gracefully.
        try:
            if not self.winfo_exists():
                self._refresh_job = None
                return
        except tk.TclError:  # pragma: no cover - interpreter already gone
            self._refresh_job = None
            return
        self.refresh()
        self._refresh_job = self.after(REFRESH_MS, self._poll)

    def stop_polling(self) -> None:
        """Stop refreshing; the page is not on screen."""
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except tk.TclError:  # pragma: no cover - window already gone
                pass
            self._refresh_job = None
