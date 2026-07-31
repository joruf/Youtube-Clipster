#!/usr/bin/env python3
"""Generate the application logo in every format the app and the OS need.

The mark is a download glyph: a chunky downward triangle over a baseline bar,
in the accent red on a near-black rounded tile.  Downward triangle instead of
an arrow keeps the "play" association of a media tool.

Run from the project root::

    python3 tools/make_logo.py

Writes ``assets/icons/youtube-clipster.svg`` (vector source),
``youtube-clipster.png`` (512 px, used by Tk) and ``youtube-clipster.ico``
(multi-size, used by Windows).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icons"

#: Kept in sync with clipster.theme.Palette.
BASE = "#15161a"
BORDER = "#383c46"
ACCENT = "#e5322d"
ACCENT_DARK = "#c8241f"

#: Everything is expressed as a fraction of the canvas, so any size works.
TILE_INSET = 0.045
TILE_RADIUS = 0.22
# The triangle is stroked with a round pen that grows it by CORNER_RADIUS on
# every side, so the raw coordinates are inset by that much.
TRIANGLE_TOP = 0.275
TRIANGLE_BOTTOM = 0.575
TRIANGLE_HALF_WIDTH = 0.195
CORNER_RADIUS = 0.030
BAR_TOP = 0.675
BAR_BOTTOM = 0.775
BAR_HALF_WIDTH = 0.235
BAR_RADIUS = 0.05

#: Sizes stored inside the .ico file.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
#: Supersampling factor for smooth edges.
SCALE = 8


def _rounded_polygon(draw: ImageDraw.ImageDraw, points: List[Tuple[float, float]], radius: float, fill: str) -> None:
    """Draw a polygon whose corners are rounded by ``radius``.

    Pillow has no rounded polygon.  The same trick the SVG uses works here: fill
    the polygon and stroke its outline with a round-jointed pen of ``2*radius``,
    which grows the shape by ``radius`` and rounds every corner.  Painting
    circles on the vertices instead would make them bulge outwards.

    :param draw: The drawing context.
    :param points: Polygon corners.
    :param radius: Corner radius in pixels.
    :param fill: Fill colour.
    :return: None
    """
    draw.polygon(points, fill=fill)
    draw.line(list(points) + [points[0]], fill=fill, width=int(radius * 2), joint="curve")


def render(size: int) -> Image.Image:
    """Render the logo at ``size`` pixels.

    :param size: Edge length of the square image.
    :return: The rendered RGBA image.
    """
    big = size * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    inset = big * TILE_INSET
    draw.rounded_rectangle(
        (inset, inset, big - inset, big - inset),
        radius=big * TILE_RADIUS,
        fill=BASE,
        outline=BORDER,
        width=max(1, int(big * 0.012)),
    )

    centre = big / 2.0
    corner = big * CORNER_RADIUS
    _rounded_polygon(
        draw,
        [
            (centre - big * TRIANGLE_HALF_WIDTH, big * TRIANGLE_TOP),
            (centre + big * TRIANGLE_HALF_WIDTH, big * TRIANGLE_TOP),
            (centre, big * TRIANGLE_BOTTOM),
        ],
        radius=corner,
        fill=ACCENT,
    )

    draw.rounded_rectangle(
        (
            centre - big * BAR_HALF_WIDTH,
            big * BAR_TOP,
            centre + big * BAR_HALF_WIDTH,
            big * BAR_BOTTOM,
        ),
        radius=big * BAR_RADIUS,
        fill=ACCENT_DARK,
    )

    return image.resize((size, size), Image.LANCZOS)


SVG = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Loresoft YouTube Clipster - logo source. Regenerate the raster files with
     tools/make_logo.py after changing this file. -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <title>YouTube Clipster</title>
  <rect x="23" y="23" width="466" height="466" rx="113" ry="113"
        fill="{base}" stroke="{border}" stroke-width="6"/>
  <path d="M 140 125 H 372 L 256 310 Z"
        fill="{accent}" stroke="{accent}" stroke-width="28"
        stroke-linejoin="round" stroke-linecap="round"/>
  <rect x="136" y="346" width="240" height="51" rx="25" ry="25" fill="{accent_dark}"/>
</svg>
""".format(base=BASE, border=BORDER, accent=ACCENT, accent_dark=ACCENT_DARK)


def main() -> int:
    """Write the SVG source and the raster files.

    :return: Process exit code.
    """
    OUT.mkdir(parents=True, exist_ok=True)

    svg_path = OUT / "youtube-clipster.svg"
    svg_path.write_text(SVG, encoding="utf-8")
    print("wrote", svg_path.relative_to(ROOT))

    master = render(512)
    png_path = OUT / "youtube-clipster.png"
    master.save(png_path)
    print("wrote", png_path.relative_to(ROOT), master.size)

    ico_path = OUT / "youtube-clipster.ico"
    master.save(ico_path, sizes=[(s, s) for s in ICO_SIZES])
    print("wrote", ico_path.relative_to(ROOT), ICO_SIZES)

    # A small preview sheet makes it easy to check the shape at real sizes.
    preview_sizes = (16, 24, 32, 48, 64, 128)
    gap = 12
    width = sum(preview_sizes) + gap * (len(preview_sizes) + 1)
    sheet = Image.new("RGBA", (width, max(preview_sizes) + gap * 2), (32, 34, 40, 255))
    x = gap
    for size in preview_sizes:
        sheet.alpha_composite(render(size), (x, (sheet.height - size) // 2))
        x += size + gap
    preview_path = OUT / "youtube-clipster-preview.png"
    sheet.save(preview_path)
    print("wrote", preview_path.relative_to(ROOT), sheet.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
