"""The dark colour scheme and every ttk style used by the application.

All colours live here - no widget module may hardcode a hex value.  The look
follows a dark, modern downloader UI: near-black surfaces, a single red accent
for primary actions and badges, and muted grey for secondary text.

``apply`` must run once on the Tk root before any widget is created, because
ttk styles are global to the interpreter.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict

from .logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Palette:
    """Every colour the interface uses."""

    #: Window background, the darkest surface.
    base: str = "#15161a"
    #: Cards, sidebar and table background.
    panel: str = "#1d1f25"
    #: Inputs, hovered rows, raised areas.
    elevated: str = "#272a32"
    #: Pressed / active state of elevated surfaces.
    active: str = "#31353f"
    #: Hairlines and separators.
    border: str = "#383c46"
    #: Primary text.
    text: str = "#e9eaee"
    #: Secondary text, metadata, disabled labels.
    muted: str = "#8b909c"
    #: Text on top of the accent colour.
    on_accent: str = "#ffffff"
    #: The one accent colour: primary buttons, badges, progress.
    accent: str = "#e5322d"
    #: Hovered accent.
    accent_hover: str = "#f2443f"
    #: Pressed accent.
    accent_active: str = "#c8241f"
    #: Secondary action colour (format selector).
    info: str = "#3b74e8"
    #: Finished downloads.
    success: str = "#3fb950"
    #: Canceled downloads.
    warning: str = "#d6a119"
    #: Failed downloads.
    danger: str = "#f2564b"

    def status_colour(self, status: str) -> str:
        """Return the accent colour for a history status.

        :param status: ``ok``, ``failed`` or ``canceled``.
        :return: The hex colour to use.
        """
        return {"ok": self.success, "failed": self.danger, "canceled": self.warning}.get(status, self.muted)


#: The single palette instance used everywhere.
PALETTE = Palette()

#: Corner padding used consistently across windows.
PAD = 14
#: Tighter padding inside rows and toolbars.
PAD_SMALL = 8


def fonts() -> Dict[str, tkfont.Font]:
    """Return the named fonts of the interface.

    Must be called after a Tk root exists.

    :return: Mapping of role name to font.
    """
    base = tkfont.nametofont("TkDefaultFont")
    family = base.actual("family")
    size = max(9, int(base.actual("size")) or 10)
    return {
        "body": tkfont.Font(family=family, size=size),
        "bold": tkfont.Font(family=family, size=size, weight="bold"),
        "small": tkfont.Font(family=family, size=max(8, size - 1)),
        "small_bold": tkfont.Font(family=family, size=max(8, size - 1), weight="bold"),
        "title": tkfont.Font(family=family, size=size + 5, weight="bold"),
        "heading": tkfont.Font(family=family, size=size + 1, weight="bold"),
        "badge": tkfont.Font(family=family, size=max(7, size - 2), weight="bold"),
    }


def apply(root: tk.Misc, palette: Palette = PALETTE) -> Palette:
    """Configure every ttk style for the dark scheme.

    :param root: The Tk root window.
    :param palette: The colours to use.
    :return: The applied palette, for convenience.
    """
    style = ttk.Style(root)
    # "clam" is the only built-in theme that lets every colour be overridden.
    if "clam" in style.theme_names():
        style.theme_use("clam")
    else:  # pragma: no cover - exotic Tk build
        log.debug("The clam theme is unavailable; the dark scheme may look off.")

    face = fonts()
    try:
        root.configure(background=palette.base)  # type: ignore[call-arg]
    except tk.TclError:  # pragma: no cover
        pass
    root.option_add("*Font", face["body"])

    # --- surfaces -----------------------------------------------------
    style.configure(".", background=palette.base, foreground=palette.text, font=face["body"])
    style.configure("TFrame", background=palette.base)
    style.configure("Panel.TFrame", background=palette.panel)
    style.configure("Elevated.TFrame", background=palette.elevated)
    style.configure("Sidebar.TFrame", background=palette.panel)
    style.configure("Toolbar.TFrame", background=palette.panel)

    # --- labels -------------------------------------------------------
    for name, background in (("", palette.base), ("Panel.", palette.panel), ("Elevated.", palette.elevated)):
        style.configure(name + "TLabel", background=background, foreground=palette.text)
        style.configure(name + "Muted.TLabel", background=background, foreground=palette.muted, font=face["small"])
        style.configure(name + "Bold.TLabel", background=background, foreground=palette.text, font=face["bold"])
        style.configure(name + "Title.TLabel", background=background, foreground=palette.text, font=face["title"])
        style.configure(
            name + "Heading.TLabel", background=background, foreground=palette.text, font=face["heading"]
        )
        for role, colour in (
            ("Success", palette.success),
            ("Danger", palette.danger),
            ("Warning", palette.warning),
            ("Accent", palette.accent),
        ):
            style.configure(name + role + ".TLabel", background=background, foreground=colour)

    # --- buttons ------------------------------------------------------
    style.configure(
        "TButton",
        background=palette.elevated,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.elevated,
        darkcolor=palette.elevated,
        focuscolor=palette.accent,
        borderwidth=1,
        relief="flat",
        padding=(12, 7),
    )
    style.map(
        "TButton",
        background=[("disabled", palette.panel), ("pressed", palette.active), ("active", palette.active)],
        foreground=[("disabled", palette.muted)],
        bordercolor=[("active", palette.accent)],
    )

    style.configure(
        "Accent.TButton",
        background=palette.accent,
        foreground=palette.on_accent,
        bordercolor=palette.accent,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        borderwidth=0,
        relief="flat",
        padding=(16, 8),
        font=face["bold"],
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", palette.elevated),
            ("pressed", palette.accent_active),
            ("active", palette.accent_hover),
        ],
        foreground=[("disabled", palette.muted)],
    )

    style.configure(
        "Info.TButton",
        background=palette.info,
        foreground=palette.on_accent,
        bordercolor=palette.info,
        lightcolor=palette.info,
        darkcolor=palette.info,
        borderwidth=0,
        relief="flat",
        padding=(14, 8),
        font=face["bold"],
    )
    style.map(
        "Info.TButton",
        background=[("disabled", palette.elevated), ("pressed", palette.info), ("active", "#5288f2")],
        foreground=[("disabled", palette.muted)],
    )

    # Small, quiet buttons used in table rows.
    style.configure(
        "Row.TButton",
        background=palette.elevated,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.elevated,
        darkcolor=palette.elevated,
        borderwidth=1,
        relief="flat",
        padding=(6, 3),
        font=face["small"],
    )
    style.map(
        "Row.TButton",
        background=[("disabled", palette.panel), ("pressed", palette.active), ("active", palette.active)],
        foreground=[("disabled", palette.muted)],
        bordercolor=[("active", palette.accent)],
    )

    # Larger transport controls for the Streaming player bar.
    style.configure(
        "Player.TButton",
        background=palette.elevated,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.elevated,
        darkcolor=palette.elevated,
        borderwidth=1,
        relief="flat",
        padding=(PAD_SMALL + 4, PAD_SMALL),
        font=face["heading"],
    )
    style.map(
        "Player.TButton",
        background=[("disabled", palette.panel), ("pressed", palette.active), ("active", palette.active)],
        foreground=[("disabled", palette.muted)],
        bordercolor=[("active", palette.accent)],
    )
    style.configure(
        "PlayerAccent.TButton",
        background=palette.accent,
        foreground=palette.on_accent,
        bordercolor=palette.accent,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        borderwidth=0,
        relief="flat",
        padding=(PAD, PAD_SMALL + 2),
        font=face["heading"],
    )
    style.map(
        "PlayerAccent.TButton",
        background=[
            ("disabled", palette.elevated),
            ("pressed", palette.accent_active),
            ("active", palette.accent_hover),
        ],
        foreground=[("disabled", palette.muted)],
    )

    # Sidebar entries behave like flat toggles.
    style.configure(
        "Sidebar.TButton",
        background=palette.panel,
        foreground=palette.muted,
        borderwidth=0,
        relief="flat",
        anchor="w",
        padding=(12, 8),
    )
    style.map(
        "Sidebar.TButton",
        background=[("pressed", palette.elevated), ("active", palette.elevated)],
        foreground=[("active", palette.text)],
    )
    style.configure(
        "SidebarSelected.TButton",
        background=palette.elevated,
        foreground=palette.text,
        borderwidth=0,
        relief="flat",
        anchor="w",
        padding=(12, 8),
        font=face["bold"],
    )
    style.map("SidebarSelected.TButton", background=[("active", palette.active)])

    # Menu-like buttons in the window header.
    style.configure(
        "Menu.TButton",
        background=palette.panel,
        foreground=palette.muted,
        borderwidth=0,
        relief="flat",
        padding=(12, 6),
    )
    style.map(
        "Menu.TButton",
        background=[("pressed", palette.elevated), ("active", palette.elevated)],
        foreground=[("active", palette.text)],
    )
    style.configure(
        "MenuSelected.TButton",
        background=palette.panel,
        foreground=palette.text,
        borderwidth=0,
        relief="flat",
        padding=(12, 6),
        font=face["bold"],
    )
    style.map("MenuSelected.TButton", background=[("active", palette.elevated)])

    # --- inputs -------------------------------------------------------
    style.configure(
        "TEntry",
        fieldbackground=palette.elevated,
        foreground=palette.text,
        insertcolor=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        borderwidth=1,
        relief="flat",
        padding=(10, 8),
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", palette.panel), ("readonly", palette.panel)],
        bordercolor=[("focus", palette.accent)],
        foreground=[("disabled", palette.muted)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=palette.elevated,
        background=palette.elevated,
        foreground=palette.text,
        arrowcolor=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.elevated,
        darkcolor=palette.elevated,
        borderwidth=1,
        relief="flat",
        padding=(8, 6),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette.elevated), ("disabled", palette.panel)],
        foreground=[("disabled", palette.muted)],
        bordercolor=[("focus", palette.accent)],
        arrowcolor=[("disabled", palette.muted)],
    )
    # The dropdown list is a plain Tk listbox and is styled through options.
    root.option_add("*TCombobox*Listbox.background", palette.elevated)
    root.option_add("*TCombobox*Listbox.foreground", palette.text)
    root.option_add("*TCombobox*Listbox.selectBackground", palette.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", palette.on_accent)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # clam's indicator element takes indicatorbackground (the box) and
    # indicatorforeground (the mark) - "indicatorcolor" is silently ignored,
    # which leaves the default light box on the dark background.
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(
            name,
            background=palette.panel,
            foreground=palette.text,
            indicatorbackground=palette.elevated,
            indicatorforeground=palette.on_accent,
            indicatorsize=11,
            upperbordercolor=palette.border,
            lowerbordercolor=palette.border,
            focuscolor=palette.accent,
            padding=(0, 4),
        )
        style.map(
            name,
            indicatorbackground=[
                ("disabled", palette.panel),
                ("selected", "!disabled", palette.accent),
                ("active", "!selected", palette.active),
            ],
            indicatorforeground=[("disabled", palette.muted)],
            upperbordercolor=[("active", palette.accent), ("selected", palette.accent)],
            lowerbordercolor=[("active", palette.accent), ("selected", palette.accent)],
            foreground=[("disabled", palette.muted)],
            background=[("active", palette.panel)],
        )

    style.configure("TSpinbox", fieldbackground=palette.elevated, foreground=palette.text,
                    bordercolor=palette.border, arrowcolor=palette.text, borderwidth=1, relief="flat",
                    padding=(6, 6))

    # --- separators, scrollbars, progress -----------------------------
    style.configure("TSeparator", background=palette.border)

    style.configure(
        "Vertical.TScrollbar",
        background=palette.panel,
        troughcolor=palette.base,
        bordercolor=palette.base,
        arrowcolor=palette.muted,
        borderwidth=0,
        relief="flat",
        width=11,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("pressed", palette.accent), ("active", palette.active)],
    )

    style.configure(
        "TProgressbar",
        background=palette.accent,
        troughcolor=palette.elevated,
        bordercolor=palette.elevated,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        borderwidth=0,
        thickness=8,
    )
    style.configure(
        "Thin.TProgressbar",
        background=palette.accent,
        troughcolor=palette.elevated,
        bordercolor=palette.elevated,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        borderwidth=0,
        thickness=4,
    )

    # --- notebook (settings tabs) -------------------------------------
    style.configure("TNotebook", background=palette.base, bordercolor=palette.border, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=palette.panel,
        foreground=palette.muted,
        bordercolor=palette.border,
        padding=(16, 8),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", palette.base), ("active", palette.elevated)],
        foreground=[("selected", palette.text)],
    )

    style.configure("Card.TLabelframe", background=palette.panel, bordercolor=palette.border,
                    lightcolor=palette.panel, darkcolor=palette.panel, borderwidth=1, relief="solid")
    style.configure("Card.TLabelframe.Label", background=palette.panel, foreground=palette.muted,
                    font=face["small_bold"])

    return palette
