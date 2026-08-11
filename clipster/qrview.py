"""Turning text into a QR code, for a Tk canvas or a web page.

Two callers, one encoder:

* the Phone page and the share dialog draw onto a Tk canvas, so the address or
  the song can be scanned off the screen instead of typed;
* the phone interface asks for the same thing as an SVG, because a browser has
  no canvas of ours to draw on.

Both are built from :func:`qr_matrix` as rectangles rather than an image: that
needs no Pillow, stays crisp at any size, and follows the palette like every
other widget.
"""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional
from xml.sax.saxutils import quoteattr

from .logging_setup import get_logger

log = get_logger(__name__)

#: Quiet zone around the code, in modules. The specification asks for four.
QUIET_ZONE = 4


def qr_matrix(text: str) -> Optional[List[List[bool]]]:
    """Encode ``text`` as a matrix of dark and light modules.

    :param text: The content, usually the address of the phone interface.
    :return: The matrix, or ``None`` when the optional package is missing.
    """
    if not text:
        return None
    try:
        import qrcode
    except ImportError:
        log.debug("qrcode is not installed - no QR code can be drawn")
        return None
    try:
        code = qrcode.QRCode(border=0)
        code.add_data(text)
        code.make(fit=True)
        return [[bool(module) for module in row] for row in code.get_matrix()]
    except Exception:  # pragma: no cover - a broken qrcode install must not crash the page
        log.debug("The QR code could not be generated", exc_info=True)
        return None


def draw_qr(canvas: tk.Canvas, text: str, size: int, dark: str = "#000000",
            light: str = "#ffffff") -> bool:
    """Draw a QR code for ``text`` filling ``size`` pixels of ``canvas``.

    The canvas is cleared first, so the same canvas can be redrawn whenever the
    address changes.

    :param canvas: The canvas to draw on.
    :param text: The content to encode.
    :param size: The intended edge length in pixels.
    :param dark: Colour of the dark modules.
    :param light: Colour of the light modules and the quiet zone.
    :return: ``True`` when a code was drawn.
    """
    canvas.delete("all")
    matrix = qr_matrix(text)
    if matrix is None:
        return False

    modules = len(matrix) + 2 * QUIET_ZONE
    # Whole pixels per module: a fractional size makes the scanner's job harder.
    scale = max(1, size // modules)
    edge = scale * modules
    canvas.configure(width=edge, height=edge)
    canvas.create_rectangle(0, 0, edge, edge, fill=light, outline=light)

    for row, line in enumerate(matrix):
        for start, end in _runs(line):
            x1 = (start + QUIET_ZONE) * scale
            x2 = (end + QUIET_ZONE) * scale
            y1 = (row + QUIET_ZONE) * scale
            canvas.create_rectangle(x1, y1, x2, y1 + scale, fill=dark, outline=dark)
    return True


def _runs(line: List[bool]) -> List[tuple]:
    """Return the dark stretches of one matrix row as ``(start, end)`` pairs.

    Consecutive dark modules become one rectangle instead of many, which keeps
    both the canvas item count and the SVG small.

    :param line: One row of the matrix.
    :return: Half-open column ranges that are dark.
    """
    found: List[tuple] = []
    start: Optional[int] = None
    for column in range(len(line) + 1):
        filled = column < len(line) and line[column]
        if filled and start is None:
            start = column
        elif not filled and start is not None:
            found.append((start, column))
            start = None
    return found


def qr_svg(text: str, *, scale: int = 8, dark: str = "#000000",
           light: str = "#ffffff") -> Optional[str]:
    """Return a QR code for ``text`` as a standalone SVG document.

    Used by the phone interface, which cannot draw on a Tk canvas.  The image
    scales without loss, so one answer serves a phone and a desktop browser.

    :param text: The content to encode.
    :param scale: Pixels per module; the quiet zone is added around it.
    :param dark: Colour of the dark modules.
    :param light: Colour of the light modules and the quiet zone.
    :return: The SVG source, or ``None`` when the optional package is missing.
    """
    matrix = qr_matrix(text)
    if matrix is None:
        return None
    step = max(1, int(scale))
    modules = len(matrix) + 2 * QUIET_ZONE
    edge = modules * step
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" '
        'viewBox="0 0 {0} {1}" shape-rendering="crispEdges" role="img">'.format(edge, edge),
        '<rect width="{0}" height="{0}" fill={1}/>'.format(edge, quoteattr(light)),
    ]
    for row, line in enumerate(matrix):
        for start, end in _runs(line):
            parts.append(
                '<rect x="{0}" y="{1}" width="{2}" height="{3}" fill={4}/>'.format(
                    (start + QUIET_ZONE) * step,
                    (row + QUIET_ZONE) * step,
                    (end - start) * step,
                    step,
                    quoteattr(dark),
                )
            )
    parts.append("</svg>")
    return "".join(parts)
