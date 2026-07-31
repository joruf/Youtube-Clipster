#!/usr/bin/env python3
"""Loresoft YouTube Clipster - installer and launcher for Linux and Windows.

Run this file to start the program.  It is intentionally dependency free and
compatible with old interpreters so it can report a useful error instead of a
``SyntaxError`` on outdated systems.  It

1. verifies the Python version,
2. installs everything that is missing (tkinter, venv, yt-dlp, FFmpeg,
   clipboard helpers),
3. restarts itself inside the managed virtual environment,
4. starts the clipboard monitor.

Usage::

    python3 run.py              # install what is missing, then start
    python3 run.py --check      # only run the dependency check
    python3 run.py --help       # all options

Author:  Joachim Ruf, Loresoft.de
License: GPLv3 - the author's name must be credited upon publication and modification.
"""

import os
import sys

MIN_PYTHON = (3, 8)


def _fail(message):
    """Print an error and exit with status 1.

    :param message: The text shown to the user.
    :return: Never returns.
    """
    sys.stderr.write("[ERROR] " + message + "\n")
    if os.name == "nt":
        # Keep the console window open when started with a double click.
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
    sys.exit(1)


def _main():
    """Verify the interpreter, then hand over to :mod:`clipster.cli`.

    :return: The process exit code.
    """
    if sys.version_info[:2] < MIN_PYTHON:
        _fail(
            "Python %d.%d or newer is required, but %s is running.\n"
            "        Download: https://www.python.org/downloads/"
            % (MIN_PYTHON[0], MIN_PYTHON[1], ".".join(str(part) for part in sys.version_info[:3]))
        )

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    try:
        from clipster.cli import bootstrap_main
    except ImportError as exc:
        _fail(
            "The 'clipster' package could not be imported (%s).\n"
            "        Make sure run.py stays next to the 'clipster' folder." % exc
        )
        return 1

    return bootstrap_main()


if __name__ == "__main__":
    sys.exit(_main())
