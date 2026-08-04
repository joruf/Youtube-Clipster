"""A vertically scrollable container.

Shared by the download table and the Phone page.  It lives in its own module so
both can use it without importing each other.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class Scroller(ttk.Frame):
    """A vertically scrollable container for the table rows."""

    def __init__(self, master: tk.Misc, palette: theme.Palette) -> None:
        """
        :param master: The parent widget.
        :param palette: The colour scheme.
        """
        super().__init__(master, style="Panel.TFrame")
        # The scrollbar sits beside the whole column, so anything packed into
        # `stack` - the heading strip included - is exactly as wide as the rows.
        self._scrollbar = ttk.Scrollbar(self, orient="vertical")
        self._scrollbar.pack(side="right", fill="y")
        #: Pack the heading strip in here; the canvas fills what is left.
        self.stack = ttk.Frame(self, style="Panel.TFrame")
        self.stack.pack(side="left", fill="both", expand=True)
        # A real child of `stack`, not merely packed into it: with `in_` the
        # canvas stays a child of the scroller and `stack` covers it.
        self._canvas = tk.Canvas(
            self.stack, background=palette.panel, highlightthickness=0, borderwidth=0, takefocus=0
        )
        self._canvas.pack(side="bottom", fill="both", expand=True)
        self._scrollbar.configure(command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        #: Rows are added to this frame.
        self.body = ttk.Frame(self._canvas, style="Panel.TFrame")
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(self._window, width=e.width))
        self.bind_wheel(self)
        self.bind_wheel(self.body)
        self.bind_wheel(self._canvas)

    def bind_wheel(self, widget: tk.Misc) -> None:
        """Attach mouse wheel scrolling for every platform.

        :param widget: The widget to bind.
        :return: None
        """
        widget.bind("<MouseWheel>", lambda e: self._scroll(-1 if getattr(e, "delta", 0) > 0 else 1), add="+")
        widget.bind("<Button-4>", lambda _e: self._scroll(-1), add="+")
        widget.bind("<Button-5>", lambda _e: self._scroll(1), add="+")

    def bind_wheel_tree(self, widget: tk.Misc) -> None:
        """Bind the wheel on a widget and every descendant.

        :param widget: The root of the subtree.
        :return: None
        """
        self.bind_wheel(widget)
        for child in widget.winfo_children():
            self.bind_wheel_tree(child)

    def _scroll(self, direction: int) -> None:
        """Scroll by three rows.

        :param direction: ``-1`` up, ``1`` down.
        :return: None
        """
        self._canvas.yview_scroll(direction * 3, "units")

    def clear(self) -> None:
        """Remove every row."""
        for child in self.body.winfo_children():
            child.destroy()

    def to_top(self) -> None:
        """Scroll back to the first row."""
        self._canvas.yview_moveto(0.0)


