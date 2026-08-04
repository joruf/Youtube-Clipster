#!/usr/bin/env python3
"""Capture an anonymized screenshot of the phone interface for the docs.

The counterpart of ``capture_screenshots.py``, which grabs the Tk windows with
PIL.  The phone interface is a web page, so it needs a browser instead: the real
server is started on a free port with fixture data and photographed in a headless
Chromium at phone size.

No network and no personal paths are involved.  Run it under a display::

    python3 tools/capture_phone_screenshot.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_FILE = ROOT / "docs" / "images" / "phone.png"

#: Browsers that can take a screenshot from the command line.
_BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")

#: A phone viewport; doubled by the device scale factor.
_WIDTH = 390
_HEIGHT = 844


def _find_browser() -> Optional[str]:
    """Return the first usable headless browser.

    :return: The executable, or ``None`` when none is installed.
    """
    for name in _BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def _fixture_history(media: Path) -> List[Any]:
    """Return a believable download list that contains nothing personal.

    :param media: Directory the fake media file is written to.
    :return: A list of :class:`clipster.history.HistoryEntry`.
    """
    from clipster.history import STATUS_CANCELED, STATUS_FAILED, STATUS_OK, HistoryEntry

    song = media / "Some Artist - A Song.mp3"
    song.write_bytes(b"\0" * 4_700_000)
    video = media / "Another Artist - Live Session.mp4"
    video.write_bytes(b"\0" * 1024)
    return [
        HistoryEntry(name=song.name, path=str(song), title="A Song", url="https://youtu.be/aaa",
                     media_format="mp3", size=4_700_000, duration=214,
                     finished_at="2026-08-04T12:30:00", status=STATUS_OK),
        HistoryEntry(name=video.name, path=str(video), title="Live Session",
                     url="https://youtu.be/bbb", media_format="mp4", size=88_400_000,
                     duration=4663, finished_at="2026-08-03T22:05:00", status=STATUS_OK),
        HistoryEntry(name="Third Artist - Rare Take", url="https://youtu.be/ccc",
                     media_format="mp3", finished_at="2026-08-03T19:40:00",
                     status=STATUS_FAILED, error_kind="bot",
                     error="Sign in to confirm you are not a bot"),
        HistoryEntry(name="Fourth Artist - Long Mix", url="https://youtu.be/ddd",
                     media_format="mp4", duration=7200, finished_at="2026-08-02T09:15:00",
                     status=STATUS_CANCELED, error="Canceled"),
    ]


class _FixtureApp:
    """Stands in for the running application, with a fixed download list."""

    def __init__(self, entries: List[Any]) -> None:
        """
        :param entries: The download list to serve.
        """
        self._entries = entries
        self.history = self

    @property
    def entries(self) -> List[Any]:
        """Return the download list, newest first."""
        return list(self._entries)

    def find_by_id(self, identifier: str) -> Optional[Any]:
        """Look one entry up by its id."""
        return next((e for e in self._entries if e.identifier() == identifier), None)

    def remote_status(self) -> Dict[str, Any]:
        """Return one download in progress, so the bar is visible."""
        return {
            "active": [{"url": "https://youtu.be/eee", "title": "Fifth Artist - New Single",
                        "format": "mp3", "phase": "converting", "percent": 63.0,
                        "detail": "2.1 MB/s"}],
            "queued": 2,
            "parallel": 1,
        }


def main() -> int:
    """Start the server, photograph the page and write the PNG.

    :return: The process exit code.
    """
    browser = _find_browser()
    if browser is None:
        print("No Chromium or Chrome found - install one of: " + ", ".join(_BROWSERS))
        return 1

    from clipster.webapi import RemoteApi
    from clipster.webserver import RemoteServer

    workspace = Path(tempfile.mkdtemp(prefix="clipster-shot-"))
    token = "screenshot-token"
    server = RemoteServer(RemoteApi(_FixtureApp(_fixture_history(workspace))),
                          token=token, bind="127.0.0.1", port=0)
    if not server.start():
        print("The server did not start.")
        return 1

    address = "http://127.0.0.1:{0}/?token={1}".format(server.port, token)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    print("capturing", address, flush=True)
    try:
        subprocess.run(
            [browser, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=2",
             "--window-size={0},{1}".format(_WIDTH, _HEIGHT),
             # Long enough for the page to fetch the list and the status once.
             "--virtual-time-budget=4000",
             "--screenshot={0}".format(OUT_FILE), address],
            check=False, capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("The browser did not finish in time.")
        return 1
    finally:
        server.stop()
        shutil.rmtree(workspace, ignore_errors=True)

    if not OUT_FILE.is_file():
        print("No screenshot was written.")
        return 1
    print("wrote {0} ({1} bytes)".format(OUT_FILE, OUT_FILE.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
