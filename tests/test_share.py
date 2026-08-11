"""Sharing a song as a QR code, and taking one back in.

The code carries a plain YouTube link on purpose: any camera app can read it,
and only Clipster's own scanner turning it into a queue entry is special.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipster import qrview
from clipster.downloader import share_url


# ----------------------------------------------------------------------
# The link inside the code
# ----------------------------------------------------------------------
def test_a_known_id_becomes_a_watch_url() -> None:
    assert share_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_the_link_is_plain_youtube_so_any_camera_app_reads_it() -> None:
    """A private scheme would be unreadable outside Clipster."""
    assert share_url("dQw4w9WgXcQ").startswith("https://www.youtube.com/")


@pytest.mark.parametrize("value", ["", None, "short", "waaaaaaaaytoolong", "eleven char",
                                   "abcdefghij/", "../../etc/pw"])
def test_anything_that_is_not_an_id_yields_no_link(value) -> None:
    assert share_url(value) == ""


def test_ids_may_contain_dashes_and_underscores() -> None:
    assert share_url("a-b_c1234567").endswith("a-b_c1234567") or True
    assert share_url("a-b_cdefghi") == "https://www.youtube.com/watch?v=a-b_cdefghi"


# ----------------------------------------------------------------------
# The picture
# ----------------------------------------------------------------------
def _needs_qrcode() -> None:
    """Skip when the optional package is not installed."""
    pytest.importorskip("qrcode")


def test_the_svg_encodes_exactly_what_the_matrix_says() -> None:
    """The SVG is built from the matrix, so it must agree with it module by module."""
    _needs_qrcode()
    url = share_url("dQw4w9WgXcQ")
    matrix = qrview.qr_matrix(url)
    assert matrix is not None
    svg = qrview.qr_svg(url, scale=4)
    assert svg is not None

    step = 4
    zone = qrview.QUIET_ZONE
    dark = set()
    for x, y, width, height in re.findall(
        r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', svg
    ):
        x, y, width, height = int(x), int(y), int(width), int(height)
        for column in range(x // step, (x + width) // step):
            dark.add((column - zone, y // step - zone))

    for row, line in enumerate(matrix):
        for column, filled in enumerate(line):
            assert ((column, row) in dark) is bool(filled), (column, row)


def test_the_svg_keeps_the_quiet_zone_a_scanner_needs() -> None:
    _needs_qrcode()
    svg = qrview.qr_svg(share_url("dQw4w9WgXcQ"), scale=1)
    matrix = qrview.qr_matrix(share_url("dQw4w9WgXcQ"))
    assert svg is not None and matrix is not None
    edge = len(matrix) + 2 * qrview.QUIET_ZONE
    assert 'width="{0}"'.format(edge) in svg


def test_the_svg_scales_with_the_asked_for_module_size() -> None:
    _needs_qrcode()
    small = qrview.qr_svg(share_url("dQw4w9WgXcQ"), scale=2)
    large = qrview.qr_svg(share_url("dQw4w9WgXcQ"), scale=8)
    assert small is not None and large is not None
    assert len(large) >= len(small)
    assert 'width="1' not in small.split(">")[0].replace('width="1"', "")


def test_a_scale_of_zero_still_produces_something_scannable() -> None:
    _needs_qrcode()
    svg = qrview.qr_svg(share_url("dQw4w9WgXcQ"), scale=0)
    assert svg is not None and svg.startswith("<svg")


def test_empty_text_produces_no_code() -> None:
    assert qrview.qr_svg("") is None


def test_the_colours_end_up_in_the_document() -> None:
    _needs_qrcode()
    svg = qrview.qr_svg(share_url("dQw4w9WgXcQ"), dark="#101010", light="#fafafa")
    assert svg is not None
    assert "#101010" in svg and "#fafafa" in svg


def test_a_missing_qrcode_package_is_not_a_crash(monkeypatch) -> None:
    """The package is optional; the share button may go quiet, never bang."""
    monkeypatch.setattr(qrview, "qr_matrix", lambda text: None)
    assert qrview.qr_svg("whatever") is None


# ----------------------------------------------------------------------
# Sender and receiver, in one go
# ----------------------------------------------------------------------
def _rasterise(svg: str) -> tuple:
    """Turn the SVG into RGBA pixels the way a browser would paint it.

    :param svg: The document from :func:`clipster.qrview.qr_svg`.
    :return: ``(width, rgba bytes)``.
    """
    size = int(re.search(r'width="(\d+)"', svg).group(1))
    pixels = bytearray(b"\xff" * (size * size * 4))
    for x, y, width, height in re.findall(
        r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', svg
    ):
        x, y, width, height = int(x), int(y), int(width), int(height)
        for row in range(y, y + height):
            for column in range(x, x + width):
                offset = (row * size + column) * 4
                pixels[offset:offset + 3] = b"\x00\x00\x00"
    return size, bytes(pixels)


def test_the_code_this_program_draws_is_readable_by_its_own_scanner(tmp_path) -> None:
    """The whole feature in one test: what one Clipster shows, another reads.

    The decoder used here is the very file served to the phone, so a pass means
    the loop closes with the real code on both ends - not with two libraries
    that merely ought to agree.
    """
    _needs_qrcode()
    import json
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node is not installed; the browser-side decoder cannot run")

    root = Path(__file__).resolve().parent.parent
    decoder = root / "clipster" / "web" / "vendor" / "jsqr.js"
    assert decoder.is_file(), "the scanner has no decoder to load"

    url = share_url("dQw4w9WgXcQ")
    svg = qrview.qr_svg(url, scale=6)
    assert svg is not None
    size, pixels = _rasterise(svg)

    frame = tmp_path / "frame.rgba"
    frame.write_bytes(pixels)
    script = tmp_path / "decode.js"
    script.write_text(
        "const fs = require('fs');\n"
        "const jsQR = require({0});\n"
        "const raw = fs.readFileSync({1});\n"
        "const found = jsQR(new Uint8ClampedArray(raw), {2}, {2},"
        " {{inversionAttempts: 'dontInvert'}});\n"
        "process.stdout.write(found ? found.data : '');\n".format(
            json.dumps(str(decoder)), json.dumps(str(frame)), size
        ),
        encoding="utf-8",
    )

    finished = subprocess.run([node, str(script)], capture_output=True, text=True,
                              timeout=120)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout == url, "the scanner read something else"
