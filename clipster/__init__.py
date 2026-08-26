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

#: Marketing version, ``major.minor.patch``.
APP_VERSION = "2.3.0"
#: Build number, raised on every release.  Android needs a monotonic integer for
#: ``versionCode``, and having one number that only ever counts up also tells two
#: builds of the same version apart - which a bug report needs and
#: ``2.1.0`` alone cannot give.
APP_BUILD = 7
#: What the user sees: ``2.1.0 (4)``.  One string, so no window, page or APK can
#: drift into showing a different version from the others.
APP_VERSION_FULL = "{0} ({1})".format(APP_VERSION, APP_BUILD)

APP_AUTHOR = "Joachim Ruf"
#: The author's website, shown on the about page.
APP_WEBSITE = "https://www.loresoft.de"
#: Source repository; matches the ``origin`` remote of this checkout.
APP_URL = "https://github.com/joruf/youtube-clipster"
APP_TITLE = "{0} - v{1}".format(APP_NAME, APP_VERSION_FULL)
#: Title bar of every window, on every platform.  The version belongs here and
#: not only on the About page: a screenshot of a window is what a bug report
#: usually carries, and it has to say which build it came from.
APP_WINDOW_TITLE = "{0} {1}".format(APP_SHORT_NAME, APP_VERSION_FULL)

__version__ = APP_VERSION

__all__ = [
    "APP_NAME",
    "APP_SHORT_NAME",
    "APP_VERSION",
    "APP_BUILD",
    "APP_VERSION_FULL",
    "APP_AUTHOR",
    "APP_WEBSITE",
    "APP_URL",
    "APP_TITLE",
    "APP_WINDOW_TITLE",
    "__version__",
]
