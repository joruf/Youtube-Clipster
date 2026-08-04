#!/usr/bin/env python3
"""Capture an anonymized screenshot of the Phone page for the docs.

Like ``capture_screenshots.py`` this grabs a real Tk widget with PIL, but it
builds the page on its own so the state shown is fixed: switched on, reachable
from the network, and with a phone that has already been in touch.

The token is invented; nothing here comes from a real installation.

    python3 tools/capture_phone_page.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_FILE = ROOT / "docs" / "images" / "phone-page.png"

#: Big enough for the whole page without a scrollbar. Taller than the window's
#: own minimum on purpose: the screenshot should show every card at once.
_WIDTH = 1060
_HEIGHT = 780

#: Obviously fake, and the same length as a real one.
_TOKEN = "ExampleTokenExampleTokenEx00"


def _fixture_state() -> Dict[str, Any]:
    """Return the state the screenshot should show."""
    return {
        "enabled": True,
        "bind": "0.0.0.0",
        "port": 8733,
        "token": _TOKEN,
        "running": True,
        "url": "http://192.168.1.42:8733/?token=" + _TOKEN,
        "contacts": 4,
        "last_contact": "2026-08-04T18:41:07",
    }


def main() -> int:
    """Build the page, photograph it and write the PNG.

    :return: The process exit code.
    """
    from clipster import i18n, theme
    from clipster.config import Config
    from clipster.phone_page import PhonePage

    workspace = Path(tempfile.mkdtemp(prefix="clipster-phone-shot-"))
    config = Config.load(workspace / "config.json")
    config.remote_enabled = True
    config.remote_bind = "0.0.0.0"
    config.remote_port = 8733
    config.remote_token = _TOKEN
    config.save()

    state = _fixture_state()
    root = tk.Tk()
    theme.apply(root)
    root.title("YouTube Clipster")
    root.configure(background=theme.PALETTE.base)
    page = PhonePage(
        root,
        messages=i18n.load(config.language or "en"),
        palette=theme.PALETTE,
        config=config,
        fonts=theme.fonts(),
        on_apply=lambda enabled, bind, port: state,
        on_new_token=lambda: state,
        on_state=lambda: state,
        on_copy=lambda text: None,
        on_firewall_hint=lambda port: (
            "ufw is active and will block the port until it is allowed.",
            "sudo ufw allow {0}/tcp".format(port),
        ),
    )
    page.pack(fill="both", expand=True)
    root.geometry("{0}x{1}".format(_WIDTH, _HEIGHT))
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update_idletasks()
    root.update()

    def shoot() -> None:
        """Grab the window once it has settled."""
        time.sleep(0.4)
        from PIL import ImageGrab

        x, y = root.winfo_rootx(), root.winfo_rooty()
        width, height = root.winfo_width(), root.winfo_height()
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(str(OUT_FILE))
        root.after(0, root.quit)

    root.after(900, lambda: threading.Thread(target=shoot, daemon=True).start())
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass

    if not OUT_FILE.is_file():
        print("No screenshot was written.")
        return 1
    print("wrote {0} ({1} bytes)".format(OUT_FILE, OUT_FILE.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
