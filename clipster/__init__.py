"""Loresoft YouTube Clipster.

A cross-platform (Linux / Windows / macOS), clipboard-driven YouTube downloader.

The package is deliberately importable with the standard library alone, so the
bootstrap installer (``run.py``) can reuse :mod:`clipster.installer`
before any third-party dependency exists.  Modules that need ``yt-dlp`` or
``tkinter`` import them lazily.

Author:  Joachim Ruf, Loresoft.de
License: GPLv3 - the author's name must be credited upon publication and modification.
"""

APP_NAME = "Loresoft YouTube Clipster"
APP_SHORT_NAME = "YouTube Clipster"
APP_VERSION = "2.0.0"
APP_AUTHOR = "Joachim Ruf"
#: The author's website, shown on the about page.
APP_WEBSITE = "https://www.loresoft.de"
#: Source repository; matches the ``origin`` remote of this checkout.
APP_URL = "https://github.com/joruf/youtube-clipster"
APP_TITLE = "{0} - v{1}".format(APP_NAME, APP_VERSION)

__version__ = APP_VERSION

__all__ = [
    "APP_NAME",
    "APP_SHORT_NAME",
    "APP_VERSION",
    "APP_AUTHOR",
    "APP_WEBSITE",
    "APP_URL",
    "APP_TITLE",
    "__version__",
]
