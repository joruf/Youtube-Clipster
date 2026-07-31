"""The declarative dependency definition of the application.

Everything the program needs to run is described here as data, not as code:
which Python packages go into the private environment, which system packages
provide them per distribution, and whether a missing piece is fatal or merely
degrades a feature.

:mod:`clipster.installer` reads this table, works out what is missing and
installs it.  Adding a new requirement means adding an entry here - the
installer itself does not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

#: Requirement is mandatory - the program cannot start without it.
LEVEL_REQUIRED = "required"
#: Requirement enables a feature; without it the program degrades gracefully.
LEVEL_OPTIONAL = "optional"


@dataclass(frozen=True)
class PipDependency:
    """A Python package installed into the private virtual environment."""

    #: Name used on the pip command line.
    package: str
    #: Module name used to detect whether it is already installed.
    module: str
    #: :data:`LEVEL_REQUIRED` or :data:`LEVEL_OPTIONAL`.
    level: str = LEVEL_REQUIRED
    #: Minimum version for ``requirements.txt``; empty means "any".
    minimum: str = ""
    #: Platforms this applies to; empty means every platform.
    platforms: Tuple[str, ...] = ()
    #: What breaks without it, in English, for logs and hints.
    feature: str = ""
    #: Message key used to show :attr:`feature` translated in the UI.
    feature_key: str = ""
    #: Keep it up to date on every run (only yt-dlp needs this).
    auto_update: bool = False

    def applies_to(self, platform: str) -> bool:
        """Return ``True`` when this dependency is needed on ``platform``.

        :param platform: ``windows``, ``macos`` or ``linux``.
        :return: Whether the entry applies.
        """
        return not self.platforms or platform in self.platforms

    def requirement(self) -> str:
        """Return the ``requirements.txt`` line for this dependency."""
        spec = "{0}>={1}".format(self.package, self.minimum) if self.minimum else self.package
        if self.platforms == ("linux",):
            return spec + '; sys_platform == "linux"'
        if self.platforms == ("windows",):
            return spec + '; sys_platform == "win32"'
        return spec


@dataclass(frozen=True)
class SystemDependency:
    """A tool or library that has to come from the operating system."""

    #: Identifier used in log output and reports.
    name: str
    #: Executable to look for in ``PATH``; empty when detection is custom.
    executable: str = ""
    #: Python module that proves it is present (used for tkinter).
    module: str = ""
    #: :data:`LEVEL_REQUIRED` or :data:`LEVEL_OPTIONAL`.
    level: str = LEVEL_REQUIRED
    #: Platforms this applies to; empty means every platform.
    platforms: Tuple[str, ...] = ()
    #: Logical component key resolved to a package name by
    #: :data:`clipster.installer.PackageManager.packages` - the per-distribution
    #: names live there, so they are not duplicated here.
    system_key: str = ""
    #: Any of these executables satisfies the requirement instead.
    alternatives: Tuple[str, ...] = ()
    #: What breaks without it, in English, for logs and hints.
    feature: str = ""
    #: Message key used to show :attr:`feature` translated in the UI.
    feature_key: str = ""
    #: Hint shown when it cannot be installed automatically.
    hint: str = ""

    def applies_to(self, platform: str) -> bool:
        """Return ``True`` when this dependency is needed on ``platform``.

        :param platform: ``windows``, ``macos`` or ``linux``.
        :return: Whether the entry applies.
        """
        return not self.platforms or platform in self.platforms


#: Minimum Python version the code needs.
MINIMUM_PYTHON = (3, 8)

#: Python packages installed into the private virtual environment.
PIP_DEPENDENCIES: Tuple[PipDependency, ...] = (
    PipDependency(
        package="yt-dlp",
        module="yt_dlp",
        level=LEVEL_REQUIRED,
        minimum="2024.1.1",
        feature="downloading videos and reading video metadata",
        feature_key="dep_ytdlp",
        auto_update=True,
    ),
    PipDependency(
        package="pystray",
        module="pystray",
        level=LEVEL_OPTIONAL,
        minimum="0.19",
        feature="the system tray icon",
        feature_key="dep_tray",
    ),
    PipDependency(
        package="Pillow",
        module="PIL",
        level=LEVEL_OPTIONAL,
        minimum="9.0",
        feature="the system tray icon",
        feature_key="dep_tray",
    ),
    PipDependency(
        package="python-xlib",
        module="Xlib",
        level=LEVEL_OPTIONAL,
        minimum="0.33",
        platforms=("linux",),
        feature="the system tray icon on X11 without PyGObject",
        feature_key="dep_xlib",
    ),
)

#: Tools and libraries that have to come from the operating system.
SYSTEM_DEPENDENCIES: Tuple[SystemDependency, ...] = (
    SystemDependency(
        name="tkinter",
        module="tkinter",
        level=LEVEL_REQUIRED,
        system_key="tk",
        feature="the whole user interface",
        feature_key="dep_tkinter",
        hint="On Windows re-run the Python installer and enable \"tcl/tk and IDLE\".",
    ),
    SystemDependency(
        name="FFmpeg",
        executable="ffmpeg",
        level=LEVEL_REQUIRED,
        system_key="ffmpeg",
        feature="converting to MP3 and merging video with audio",
        feature_key="dep_ffmpeg",
        hint="Windows downloads a build automatically instead of using a package manager.",
    ),
    SystemDependency(
        name="Clipboard helper",
        executable="xclip",
        alternatives=("xclip", "xsel", "wl-paste"),
        level=LEVEL_OPTIONAL,
        platforms=("linux",),
        system_key="xclip",
        feature="reliable clipboard reading (Tk is used as a fallback)",
        feature_key="dep_clipboard",
    ),
    SystemDependency(
        name="Tray menu support",
        module="gi",
        alternatives=(),
        level=LEVEL_OPTIONAL,
        platforms=("linux",),
        system_key="appindicator",
        feature="the tray icon's context menu - pystray's X11 fallback has none",
        feature_key="dep_appindicator",
        hint="Without PyGObject the tray icon cannot show a menu; quit from the view window.",
    ),
    SystemDependency(
        name="JavaScript runtime",
        executable="qjs",
        alternatives=("qjs", "node", "deno"),
        level=LEVEL_OPTIONAL,
        system_key="js",
        feature="a few yt-dlp extractors that need to run JavaScript",
        feature_key="dep_js",
    ),
)


def find(name: str) -> Optional[SystemDependency]:
    """Return the system dependency with ``name``, or ``None``.

    :param name: The :attr:`SystemDependency.name` to look for.
    :return: The entry, if it exists.
    """
    for item in SYSTEM_DEPENDENCIES:
        if item.name == name:
            return item
    return None


def find_pip(package: str) -> Optional[PipDependency]:
    """Return the pip dependency for ``package``, or ``None``.

    :param package: The :attr:`PipDependency.package` to look for.
    :return: The entry, if it exists.
    """
    for item in PIP_DEPENDENCIES:
        if item.package == package:
            return item
    return None


def optional_pip_packages(platform: str) -> List[str]:
    """Return the pip package names of every optional dependency.

    :param platform: ``windows``, ``macos`` or ``linux``.
    :return: Package names in declaration order.
    """
    return [item.package for item in pip_dependencies(platform, LEVEL_OPTIONAL)]


def optional_pip_modules(platform: str) -> List[str]:
    """Return the import names of every optional dependency.

    :param platform: ``windows``, ``macos`` or ``linux``.
    :return: Module names in declaration order.
    """
    return [item.module for item in pip_dependencies(platform, LEVEL_OPTIONAL)]


def pip_dependencies(platform: str, level: Optional[str] = None) -> List[PipDependency]:
    """Return the pip dependencies for a platform.

    :param platform: ``windows``, ``macos`` or ``linux``.
    :param level: Restrict to :data:`LEVEL_REQUIRED` or :data:`LEVEL_OPTIONAL`.
    :return: The matching entries in declaration order.
    """
    return [
        item
        for item in PIP_DEPENDENCIES
        if item.applies_to(platform) and (level is None or item.level == level)
    ]


def system_dependencies(platform: str, level: Optional[str] = None) -> List[SystemDependency]:
    """Return the system dependencies for a platform.

    :param platform: ``windows``, ``macos`` or ``linux``.
    :param level: Restrict to :data:`LEVEL_REQUIRED` or :data:`LEVEL_OPTIONAL`.
    :return: The matching entries in declaration order.
    """
    return [
        item
        for item in SYSTEM_DEPENDENCIES
        if item.applies_to(platform) and (level is None or item.level == level)
    ]


def current_platform() -> str:
    """Return the platform key used by the tables in this module."""
    from . import paths

    if paths.IS_WINDOWS:
        return "windows"
    if paths.IS_MACOS:
        return "macos"
    return "linux"


def requirements_text() -> str:
    """Render the pip table as the content of ``requirements.txt``.

    :return: The full file content, including the explanatory header.
    """
    lines = [
        "# Runtime dependencies of Loresoft YouTube Clipster.",
        "#",
        "# Generated from clipster/dependencies.py - edit that table, not this file.",
        "# run.py installs these automatically into a private virtual",
        "# environment. Install them manually only if you manage the environment",
        "# yourself:  pip install -r requirements.txt",
        "#",
        "# Not listed here because they are not Python packages:",
        "#   * FFmpeg  - installed by the bootstrapper (package manager / ZIP download)",
        "#   * tkinter - part of CPython (Linux: install the python3-tk package)",
        "",
    ]
    for level, heading in (
        (LEVEL_REQUIRED, None),
        (LEVEL_OPTIONAL, "# Optional - without these the program still runs, only the named"),
    ):
        entries = [item for item in PIP_DEPENDENCIES if item.level == level]
        if not entries:
            continue
        if heading:
            lines.extend(["", heading, "# feature is unavailable."])
        lines.extend(item.requirement() for item in entries)
    return "\n".join(lines) + "\n"
