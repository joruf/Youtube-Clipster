"""The small popup that explains a widget while the pointer rests on it.

Shared by the Streaming page and the download list, so both look and behave the
same.  A tip can be re-worded after it was attached (:meth:`Tooltip.set_text`),
which the download list needs: whether a name is cut off - and therefore worth
explaining at all - only becomes clear when the column is laid out.

Every function here must be called from the Tk main thread.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional, Tuple


def _screen_bounds(root: tk.Misc) -> Tuple[int, int, int, int]:
    """Return the usable screen area as ``(left, top, right, bottom)``.

    :param root: Any widget of the window the tip belongs to.
    :return: The virtual root geometry, or the screen size as a fallback.
    """
    try:
        left = int(root.winfo_vrootx())
        top = int(root.winfo_vrooty())
        width = int(root.winfo_vrootwidth())
        height = int(root.winfo_vrootheight())
        if width > 1 and height > 1:
            return left, top, left + width, top + height
    except (tk.TclError, TypeError, ValueError):
        pass
    try:
        return 0, 0, int(root.winfo_screenwidth()), int(root.winfo_screenheight())
    except (tk.TclError, TypeError, ValueError):
        return 0, 0, 1920, 1080


class Tooltip:
    """A popup bound to one widget, shown while the pointer rests on it."""

    def __init__(self, widget: tk.Misc, text: str, *, background: str, foreground: str) -> None:
        """
        :param widget: The widget the tip explains.
        :param text: What to show; an empty text disables the tip.
        :param background: Popup background colour.
        :param foreground: Popup text colour.
        """
        self.widget = widget
        self.text = text
        self.background = background
        self.foreground = foreground
        self._window: Optional[tk.Toplevel] = None

        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        """Replace what the tip says, or switch it off with an empty text.

        :param text: The new text; empty means "nothing to explain".
        :return: None
        """
        if text == self.text:
            return
        self.text = text
        if not text:
            self._hide()

    def _hide(self, _event: Optional[tk.Event] = None) -> None:
        """Take the popup off the screen."""
        window, self._window = self._window, None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:  # pragma: no cover - already gone
                pass

    def _show(self, _event: Optional[tk.Event] = None) -> None:
        """Place the popup next to the widget, inside the screen."""
        if self._window is not None or not self.text:
            return
        try:
            anchor_x = int(self.widget.winfo_rootx())
            anchor_y = int(self.widget.winfo_rooty())
            anchor_w = int(self.widget.winfo_width())
            anchor_h = int(self.widget.winfo_height())
            root = self.widget.winfo_toplevel()
        except (tk.TclError, TypeError, ValueError):  # pragma: no cover - unmapped
            return
        screen_left, screen_top, screen_right, screen_bottom = _screen_bounds(root)
        margin = 8

        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:  # pragma: no cover - platform dependent
            pass
        window.configure(background=self.background)
        # Keep long names and URLs readable without a huge horizontal popup.
        wrap = max(180, min(420, screen_right - screen_left - 2 * margin))
        tk.Label(
            window,
            text=self.text,
            background=self.background,
            foreground=self.foreground,
            justify="left",
            padx=8,
            pady=4,
            borderwidth=1,
            relief="solid",
            wraplength=wrap,
        ).pack()
        window.update_idletasks()
        try:
            tip_w = int(window.winfo_reqwidth())
            tip_h = int(window.winfo_reqheight())
        except (tk.TclError, TypeError, ValueError):  # pragma: no cover - defensive
            tip_w, tip_h = 200, 28

        # Prefer below the widget, right-aligned to its right edge when near the
        # right border so tips in the last column stay fully visible.
        x = anchor_x
        y = anchor_y + anchor_h + 6
        if x + tip_w > screen_right - margin:
            x = anchor_x + anchor_w - tip_w
        if x < screen_left + margin:
            x = screen_left + margin
        if x + tip_w > screen_right - margin:
            x = max(screen_left + margin, screen_right - margin - tip_w)

        if y + tip_h > screen_bottom - margin:
            y = anchor_y - tip_h - 6
        if y < screen_top + margin:
            y = screen_top + margin
        if y + tip_h > screen_bottom - margin:
            y = max(screen_top + margin, screen_bottom - margin - tip_h)

        window.geometry("+{0}+{1}".format(int(x), int(y)))
        self._window = window


def attach(widget: tk.Misc, text: str, *, background: str, foreground: str) -> Tooltip:
    """Give ``widget`` a tooltip.

    :param widget: The widget the tip explains.
    :param text: What to show; an empty text attaches a tip that stays silent
        until :meth:`Tooltip.set_text` gives it something to say.
    :param background: Popup background colour.
    :param foreground: Popup text colour.
    :return: The tip, so its text can be changed later.
    """
    return Tooltip(widget, text, background=background, foreground=foreground)
