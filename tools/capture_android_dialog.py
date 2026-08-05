#!/usr/bin/env python3
"""Capture the "Install on Android" window for the docs.

A fake ``adb`` on PATH reports a ready phone, so the screenshot shows the state
that matters without a phone being plugged in - and nothing real is touched.

    python3 tools/capture_android_dialog.py
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_FILE = ROOT / "docs" / "images" / "android-install.png"

#: What the fake adb reports; a plausible phone, nothing personal.
_FAKE_DEVICE = "R58M12ABCDE            device usb:1-3 product:a52q model:SM_A525F"


def _fake_adb(directory: Path) -> None:
    """Write a fake adb into ``directory`` and put it first on PATH."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "adb"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *devices*) echo 'List of devices attached'; echo '{0}';;\n"
        "esac\n".format(_FAKE_DEVICE),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def main() -> int:
    """Build the window, photograph it and write the PNG.

    :return: The process exit code.
    """
    workspace = Path(tempfile.mkdtemp(prefix="clipster-android-shot-"))
    _fake_adb(workspace / "bin")

    from clipster import i18n, theme
    from clipster.android_dialog import AndroidDialog

    root = tk.Tk()
    theme.apply(root)
    root.geometry("1x1+0+0")            # mapped, but out of the way
    dialog = AndroidDialog(root, i18n.load("en"), theme.PALETTE, theme.fonts(),
                           on_copy=lambda text: None)
    window = dialog.window
    window.attributes("-topmost", True)
    window.deiconify()
    window.lift()
    window.focus_force()
    window.wait_visibility()
    for _ in range(60):
        root.update()
        time.sleep(0.03)

    def shoot() -> None:
        """Grab the window once the scan has reported the phone."""
        time.sleep(1.8)
        from PIL import ImageGrab

        x, y = window.winfo_rootx(), window.winfo_rooty()
        width, height = window.winfo_width(), window.winfo_height()
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(str(OUT_FILE))
        root.after(0, root.quit)

    root.after(200, lambda: threading.Thread(target=shoot, daemon=True).start())
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
