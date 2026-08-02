"""Dependency detection and automatic installation.

This module is the Python replacement for ``linux/lib/installer.sh`` and the
``:check_ytdlp`` / ``:check_ffmpeg`` labels of the old Windows batch file.  It
only uses the standard library so that it can run *before* anything is
installed, on the plain system interpreter.

What it takes care of:

============  ==================================================================
Component     How it is provided
============  ==================================================================
Python        Version check only - Python cannot install itself.
tkinter       Linux: distribution package; Windows/macOS: part of the installer.
venv          ``python -m venv`` into the application data directory.
yt-dlp        ``pip install -U yt-dlp`` inside that virtual environment.
FFmpeg        Linux/macOS: package manager; Windows: official ZIP download.
mpv           Optional (in-tab Streaming video). Linux/macOS: package manager;
              Windows: hint only — copy ``mpv.exe`` next to bundled FFmpeg.
Clipboard     Linux only: ``xclip`` or ``wl-clipboard``.
JS runtime    Linux only, optional: ``quickjs`` (helps some yt-dlp extractors).
============  ==================================================================
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import config as config_module
from . import dependencies
from . import paths
from .logging_setup import get_logger
from .shortcuts import _no_window

log = get_logger(__name__)

MIN_PYTHON = dependencies.MINIMUM_PYTHON

FFMPEG_WINDOWS_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
)

_DOWNLOAD_USER_AGENT = "Mozilla/5.0 (compatible; YouTubeClipster/2.0; +https://github.com/joruf/youtube-clipster)"


# ----------------------------------------------------------------------
# Result objects
# ----------------------------------------------------------------------
@dataclass
class Step:
    """Outcome of a single dependency check."""

    name: str
    ok: bool
    detail: str = ""
    changed: bool = False
    hint: str = ""

    def render(self) -> str:
        """Return a one-line human readable summary of this step."""
        mark = "OK" if self.ok else "MISSING"
        suffix = " - {0}".format(self.detail) if self.detail else ""
        return "[{0}] {1}{2}".format(mark, self.name, suffix)


@dataclass
class InstallReport:
    """Collected outcome of a full dependency run."""

    steps: List[Step] = field(default_factory=list)

    def add(self, step: Step) -> Step:
        """Record a step and log it.

        :param step: The finished step.
        :return: The same step, for convenience.
        """
        self.steps.append(step)
        if step.ok:
            log.info("%s", step.render())
        else:
            log.error("%s", step.render())
            if step.hint:
                log.error("       %s", step.hint)
        return step

    @property
    def ok(self) -> bool:
        """Return ``True`` when every step succeeded."""
        return all(step.ok for step in self.steps)

    @property
    def failures(self) -> List[Step]:
        """Return all failed steps."""
        return [step for step in self.steps if not step.ok]

    @property
    def changed(self) -> bool:
        """Return ``True`` when something was actually installed or updated."""
        return any(step.changed for step in self.steps)


# ----------------------------------------------------------------------
# Package manager abstraction
# ----------------------------------------------------------------------
@dataclass
class PackageManager:
    """A distribution package manager and its package names."""

    name: str
    refresh: Optional[List[str]]
    install: List[str]
    packages: Dict[str, str]

    def package_for(self, key: str) -> Optional[str]:
        """Return the distribution package name for a logical component."""
        return self.packages.get(key)


_PACKAGE_MANAGERS: List[PackageManager] = [
    PackageManager(
        name="apt-get",
        refresh=["apt-get", "update"],
        install=["apt-get", "install", "-y"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "tk": "python3-tk",
            "venv": "python3-venv",
            "pip": "python3-pip",
            "xclip": "xclip",
            "wl-clipboard": "wl-clipboard",
            "appindicator": "python3-gi gir1.2-ayatanaappindicator3-0.1",
            "js": "quickjs",
            "python": "python3",
        },
    ),
    PackageManager(
        name="dnf",
        refresh=None,
        install=["dnf", "install", "-y"],
        packages={
            "ffmpeg": "ffmpeg-free",
            "mpv": "mpv",
            "tk": "python3-tkinter",
            "venv": "python3-libs",
            "pip": "python3-pip",
            "xclip": "xclip",
            "wl-clipboard": "wl-clipboard",
            "appindicator": "python3-gobject libayatana-appindicator-gtk3",
            "js": "quickjs",
            "python": "python3",
        },
    ),
    PackageManager(
        name="pacman",
        refresh=["pacman", "-Sy", "--noconfirm"],
        install=["pacman", "-S", "--needed", "--noconfirm"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "tk": "tk",
            "venv": "python",
            "pip": "python-pip",
            "xclip": "xclip",
            "wl-clipboard": "wl-clipboard",
            "appindicator": "python-gobject libayatana-appindicator",
            "js": "quickjs",
            "python": "python",
        },
    ),
    PackageManager(
        name="zypper",
        refresh=["zypper", "--non-interactive", "refresh"],
        install=["zypper", "--non-interactive", "install"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "tk": "python3-tk",
            "venv": "python3-venv",
            "pip": "python3-pip",
            "xclip": "xclip",
            "wl-clipboard": "wl-clipboard",
            "appindicator": "python3-gobject libayatana-appindicator3-1",
            "js": "quickjs",
            "python": "python3",
        },
    ),
    PackageManager(
        name="apk",
        refresh=["apk", "update"],
        install=["apk", "add"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "tk": "python3-tkinter",
            "venv": "python3",
            "pip": "py3-pip",
            "xclip": "xclip",
            "wl-clipboard": "wl-clipboard",
            "appindicator": "py3-gobject3",
            "js": "quickjs",
            "python": "python3",
        },
    ),
    PackageManager(
        name="brew",
        refresh=None,
        install=["brew", "install"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "tk": "python-tk",
            "venv": "python",
            "pip": "python",
            "js": "quickjs",
            "python": "python",
        },
    ),
]

_detected_manager: Optional[PackageManager] = None
_manager_detected = False


def detect_package_manager() -> Optional[PackageManager]:
    """Return the first package manager found in ``PATH`` (cached)."""
    global _detected_manager, _manager_detected
    if _manager_detected:
        return _detected_manager
    _manager_detected = True
    for manager in _PACKAGE_MANAGERS:
        if shutil.which(manager.name):
            _detected_manager = manager
            log.debug("Detected package manager: %s", manager.name)
            return manager
    log.debug("No supported package manager found.")
    return None


def _is_root() -> bool:
    """Return ``True`` when the process already has administrative rights."""
    if paths.IS_WINDOWS:
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0  # type: ignore[attr-defined]


def _privileged(command: Sequence[str]) -> Optional[List[str]]:
    """Prefix ``command`` with ``sudo`` when necessary.

    :param command: The command to run as administrator.
    :return: The runnable command, or ``None`` when privileges are unreachable.
    """
    if _is_root() or (command and command[0] == "brew"):
        return list(command)
    if shutil.which("sudo"):
        return ["sudo", "-p", "[sudo] password for %u (YouTube Clipster setup): "] + list(command)
    return None


def run_command(command: Sequence[str], echo: bool = True, timeout: Optional[float] = 1800.0) -> "CommandResult":
    """Run a command, stream its output and capture it.

    :param command: The argument vector to execute.
    :param echo: Mirror the child output on this process' stderr.
    :param timeout: Abort after this many seconds.
    :return: Exit code and combined output.
    """
    printable = " ".join(str(part) for part in command)
    log.debug("Running: %s", printable)
    lines: List[str] = []
    try:
        # Under pythonw.exe (desktop double-click) every child would otherwise
        # flash a console window during venv / pip / package installs.
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            **_no_window(),
        )
    except OSError as exc:
        log.debug("Command %s could not be started: %s", printable, exc)
        return CommandResult(returncode=127, output=str(exc))

    deadline = None if timeout is None else time.monotonic() + timeout
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            line = raw.rstrip("\r\n")
            lines.append(line)
            if echo and line:
                print("    {0}".format(line), file=sys.stderr, flush=True)
            if deadline is not None and time.monotonic() > deadline:
                process.kill()
                lines.append("Timed out after {0:.0f}s".format(timeout or 0))
                break
        process.wait(timeout=30)
    except Exception as exc:  # pragma: no cover - defensive
        process.kill()
        lines.append(str(exc))
    return CommandResult(returncode=process.returncode or 0, output="\n".join(lines))


@dataclass
class CommandResult:
    """Exit code and combined output of an external command."""

    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        """Return ``True`` when the command exited with status 0."""
        return self.returncode == 0

    def tail(self, lines: int = 4) -> str:
        """Return the last ``lines`` non-empty output lines."""
        content = [line for line in self.output.splitlines() if line.strip()]
        return " | ".join(content[-lines:])


def _package_names(manager: PackageManager, keys: Sequence[str]) -> List[str]:
    """Resolve logical component keys to distribution package names.

    A single component may need more than one package (PyGObject *and* the
    AppIndicator typelib, for instance), so mappings may hold several
    whitespace separated names.

    :param manager: The detected package manager.
    :param keys: Logical component keys.
    :return: The package names, in order and without duplicates.
    """
    names: List[str] = []
    for key in keys:
        mapped = manager.package_for(key)
        if not mapped:
            continue
        for name in mapped.split():
            if name not in names:
                names.append(name)
    return names


def install_system_packages(keys: Sequence[str], refresh: bool = True) -> CommandResult:
    """Install one or more logical components via the distribution package manager.

    :param keys: Logical component names such as ``ffmpeg`` or ``tk``.
    :param refresh: Update the package index first (``apt-get update``, ...).
    :return: The result of the install command.
    """
    manager = detect_package_manager()
    if manager is None:
        return CommandResult(returncode=1, output="No supported package manager found")

    packages = _package_names(manager, keys)
    if not packages:
        return CommandResult(returncode=1, output="No package mapping for {0}".format(", ".join(keys)))

    if refresh and manager.refresh:
        refresh_cmd = _privileged(manager.refresh)
        if refresh_cmd is not None:
            run_command(refresh_cmd, timeout=600.0)

    install_cmd = _privileged(manager.install + packages)
    if install_cmd is None:
        return CommandResult(
            returncode=1,
            output="'sudo' is not available - please run: {0}".format(" ".join(manager.install + packages)),
        )
    log.info("Installing system package(s): %s", " ".join(packages))
    return run_command(install_cmd)


def manual_install_hint(keys: Sequence[str]) -> str:
    """Return a copy-pasteable manual install command for the given components."""
    manager = detect_package_manager()
    if manager is None:
        return "Please install manually: {0}".format(", ".join(keys))
    packages = _package_names(manager, keys)
    if not packages:
        return "Please install manually: {0}".format(", ".join(keys))
    prefix = "" if _is_root() or manager.name == "brew" else "sudo "
    return "Run manually: {0}{1} {2}".format(prefix, " ".join(manager.install), " ".join(packages))


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------
def _system_key(name: str, fallback: str) -> str:
    """Return the package-manager key declared for a system dependency.

    :param name: The :attr:`clipster.dependencies.SystemDependency.name`.
    :param fallback: Key used when the manifest has no entry.
    :return: The logical component key.
    """
    entry = dependencies.find(name)
    return (entry.system_key if entry is not None else "") or fallback


def check_python() -> Step:
    """Verify that the running interpreter is new enough."""
    found = "{0}.{1}.{2}".format(*sys.version_info[:3])
    required = "{0}.{1}".format(*MIN_PYTHON)
    if sys.version_info[:2] < MIN_PYTHON:
        return Step(
            name="Python >= {0}".format(required),
            ok=False,
            detail="found {0}".format(found),
            hint="Install a newer Python from https://www.python.org/downloads/",
        )
    return Step(name="Python", ok=True, detail=found)


def check_tkinter(auto_install: bool = True) -> Step:
    """Verify that ``tkinter`` is importable, installing it on Linux if needed.

    :param auto_install: Allow installing the distribution package.
    :return: The finished step.
    """
    if _tkinter_available(sys.executable):
        return Step(name="tkinter (GUI)", ok=True)

    if paths.IS_WINDOWS:
        return Step(
            name="tkinter (GUI)",
            ok=False,
            detail="missing in this Python installation",
            hint=(
                "Re-run the Python installer from python.org and enable "
                "'tcl/tk and IDLE', or install Python from the Microsoft Store."
            ),
        )

    key = _system_key("tkinter", "tk")
    if not auto_install:
        return Step(name="tkinter (GUI)", ok=False, detail="missing", hint=manual_install_hint([key]))

    result = install_system_packages([key])
    if _tkinter_available(sys.executable):
        return Step(name="tkinter (GUI)", ok=True, detail="installed", changed=True)
    return Step(
        name="tkinter (GUI)",
        ok=False,
        detail=result.tail(),
        hint=manual_install_hint([key]),
    )


def _tkinter_available(interpreter: str) -> bool:
    """Return ``True`` when ``interpreter`` can import tkinter."""
    if Path(interpreter).resolve() == Path(sys.executable).resolve():
        try:
            import tkinter  # noqa: F401
        except Exception:
            return False
        return True
    result = run_command([interpreter, "-c", "import tkinter"], echo=False, timeout=60.0)
    return result.ok


def _venv_options() -> List[str]:
    """Return the ``python -m venv`` flags to create the environment with.

    On Linux the system site packages are included on purpose: PyGObject
    (``gi``) cannot be installed with pip, and without it pystray falls back to
    its X11 backend, which cannot show a menu at all - leaving the tray icon
    without a way to quit.  The environment's own packages still take
    precedence in ``sys.path``, so the freshly installed yt-dlp is not shadowed
    by an older system copy.

    :return: The flags for the venv module.
    """
    if paths.IS_LINUX:
        return ["--system-site-packages"]
    return []


def _enable_system_site_packages(target: Path) -> bool:
    """Let an existing environment see the system packages.

    Environments created by older versions were closed off, so PyGObject stays
    invisible and the tray icon ends up without a menu.  Flipping the flag in
    ``pyvenv.cfg`` is exactly what ``--system-site-packages`` does and avoids
    rebuilding the environment - it takes effect the next time it starts.

    :param target: The virtual environment directory.
    :return: ``True`` when the file was actually changed.
    """
    if not paths.IS_LINUX:
        return False
    config_file = target / "pyvenv.cfg"
    try:
        lines = config_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    changed = False
    result = []
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "include-system-site-packages" and value.strip().lower() != "true":
            result.append("include-system-site-packages = true")
            changed = True
        else:
            result.append(line)
    if not changed:
        return False
    try:
        config_file.write_text("\n".join(result) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("Could not update %s: %s", config_file, exc)
        return False
    log.info("Enabled system site packages in %s so the tray menu can work.", config_file)
    return True


def ensure_venv(recreate: bool = False) -> Step:
    """Create the managed virtual environment if it does not exist yet.

    :param recreate: Delete an existing environment first.
    :return: The finished step.
    """
    target = paths.venv_dir()
    interpreter = paths.venv_python()

    if recreate and target.exists():
        log.warning("Removing existing virtual environment %s", target)
        shutil.rmtree(target, ignore_errors=True)

    if interpreter.exists():
        repaired = _enable_system_site_packages(target)
        return Step(
            name="Virtual environment",
            ok=True,
            detail="system site packages enabled" if repaired else str(target),
            changed=repaired,
        )

    paths.ensure_install_dir()
    log.info("Creating virtual environment in %s ...", target)
    result = run_command([sys.executable, "-m", "venv"] + _venv_options() + [str(target)], timeout=600.0)

    if not result.ok and not paths.IS_WINDOWS:
        # Debian and Ubuntu ship venv/ensurepip as a separate package.
        log.warning("Creating the virtual environment failed - trying to install the venv package...")
        install_system_packages(["venv", "pip"])
        shutil.rmtree(target, ignore_errors=True)
        result = run_command([sys.executable, "-m", "venv"] + _venv_options() + [str(target)], timeout=600.0)

    if interpreter.exists():
        run_command(
            [str(interpreter), "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "pip"],
            timeout=900.0,
        )
        return Step(name="Virtual environment", ok=True, detail="created", changed=True)

    return Step(
        name="Virtual environment",
        ok=False,
        detail=result.tail(),
        hint=manual_install_hint(["venv", "pip"]),
    )


def _pip_usable(interpreter: Path) -> bool:
    """Return ``True`` when ``interpreter`` has a working pip."""
    return run_command([str(interpreter), "-m", "pip", "--version"], echo=False, timeout=120.0).ok


def ensure_pip_usable(interpreter: Path) -> bool:
    """Repair a broken pip installation, rebuilding the venv as a last resort.

    An interrupted first run can leave truncated byte code behind, which makes
    every later ``pip`` call fail with an import error.

    :param interpreter: The interpreter whose pip should work.
    :return: ``True`` when pip is usable afterwards.
    """
    if _pip_usable(interpreter):
        return True

    log.warning("pip in %s is not working - repairing it...", interpreter)
    run_command([str(interpreter), "-m", "ensurepip", "--upgrade", "--default-pip"], timeout=600.0)
    if _pip_usable(interpreter):
        log.info("pip repaired.")
        return True

    if interpreter == paths.venv_python():
        log.warning("Rebuilding the virtual environment from scratch...")
        ensure_venv(recreate=True)
        return _pip_usable(interpreter)
    return False


def _ytdlp_version(interpreter: Path) -> Optional[str]:
    """Return the yt-dlp version installed for ``interpreter``, or ``None``."""
    result = run_command(
        [str(interpreter), "-c", "import yt_dlp,sys;sys.stdout.write(yt_dlp.version.__version__)"],
        echo=False,
        timeout=120.0,
    )
    if not result.ok:
        return None
    version = result.output.strip().splitlines()[-1].strip() if result.output.strip() else ""
    return version or None


def _update_due(update_check_hours: int) -> bool:
    """Return ``True`` when the yt-dlp update check is due again."""
    if update_check_hours <= 0:
        return True
    state = config_module.load_state()
    last = state.get("last_ytdlp_check")
    if not isinstance(last, (int, float)):
        return True
    return (time.time() - float(last)) >= update_check_hours * 3600


def _mark_update_checked() -> None:
    """Remember that yt-dlp was just checked for updates."""
    state = config_module.load_state()
    state["last_ytdlp_check"] = time.time()
    config_module.save_state(state)


def ensure_ytdlp(interpreter: Optional[Path] = None, force_update: bool = False, update_check_hours: int = 24) -> Step:
    """Install or update yt-dlp inside the target interpreter.

    :param interpreter: Interpreter to install into; defaults to the managed venv.
    :param force_update: Always run an update, ignoring the throttle.
    :param update_check_hours: Minimum hours between two update checks.
    :return: The finished step.
    """
    python = interpreter or paths.venv_python()
    current = _ytdlp_version(python)

    if current and not force_update and not _update_due(update_check_hours):
        return Step(name="yt-dlp", ok=True, detail=current)

    if not ensure_pip_usable(python):
        return Step(
            name="yt-dlp",
            ok=False,
            detail="pip is not available",
            hint="Delete {0} and start again, or run --reinstall.".format(paths.venv_dir()),
        )

    log.info("%s yt-dlp ...", "Updating" if current else "Installing")
    result = run_command(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--disable-pip-version-check",
            "yt-dlp",
        ],
        timeout=1800.0,
    )
    new_version = _ytdlp_version(python)

    if new_version:
        _mark_update_checked()
        if current == new_version:
            return Step(name="yt-dlp", ok=True, detail=new_version)
        return Step(name="yt-dlp", ok=True, detail="{0} installed".format(new_version), changed=True)

    if current:
        # Offline or PyPI unreachable, but a working copy is already present.
        return Step(name="yt-dlp", ok=True, detail="{0} (update failed: {1})".format(current, result.tail(2)))

    return Step(
        name="yt-dlp",
        ok=False,
        detail=result.tail(),
        hint="Run manually: \"{0}\" -m pip install -U yt-dlp".format(python),
    )


def _specs_present(interpreter: Path, modules: Sequence[str]) -> bool:
    """Check whether importable modules exist without actually importing them.

    ``import pystray`` already fails when no tray *backend* is usable, which is
    a runtime condition and not a reason to reinstall the package - so only the
    module spec is probed here.

    :param interpreter: The interpreter to ask.
    :param modules: Module names to look for.
    :return: ``True`` when every module is installed.
    """
    code = "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(m) for m in {0!r}) else 1)".format(
        list(modules)
    )
    return run_command([str(interpreter), "-c", code], echo=False, timeout=120.0).ok


def tray_modules() -> List[str]:
    """Return the pip packages needed for the system tray on this platform.

    Taken from the optional entries of :mod:`clipster.dependencies`.
    """
    return dependencies.optional_pip_packages(dependencies.current_platform())


def ensure_tray_support(interpreter: Optional[Path] = None, auto_install: bool = True) -> Step:
    """Install the optional system tray dependencies.

    The tray is a convenience feature: this step never reports a failure, it
    only records that the program will run without a tray icon.

    :param interpreter: Interpreter to install into; defaults to the managed venv.
    :param auto_install: Install the packages instead of only reporting them.
    :return: The finished step (always ``ok``).
    """
    python = interpreter or paths.venv_python()
    required = dependencies.optional_pip_modules(dependencies.current_platform())

    if _specs_present(python, required):
        return Step(name="System tray", ok=True, detail="pystray")

    if not auto_install:
        return Step(
            name="System tray",
            ok=True,
            detail="not installed - the program runs without a tray icon",
            hint="Run manually: \"{0}\" -m pip install {1}".format(python, " ".join(tray_modules())),
        )

    if not ensure_pip_usable(python):
        return Step(name="System tray", ok=True, detail="skipped - pip is not available")

    log.info("Installing the system tray support ...")
    result = run_command(
        [str(python), "-m", "pip", "install", "--upgrade", "--disable-pip-version-check"] + tray_modules(),
        timeout=1800.0,
    )
    if _specs_present(python, required):
        return Step(name="System tray", ok=True, detail="pystray installed", changed=True)

    return Step(
        name="System tray",
        ok=True,
        detail="unavailable ({0})".format(result.tail(1)),
        hint="The program runs without a tray icon. Install manually: "
        "\"{0}\" -m pip install {1}".format(python, " ".join(tray_modules())),
    )


def ensure_tray_menu(interpreter: Optional[Path] = None, auto_install: bool = True) -> Step:
    """Install PyGObject so the tray icon can show a context menu.

    pystray's X11 backend can display an icon but no menu, which would leave the
    tray without a quit entry.  PyGObject cannot be installed with pip, so it has
    to come from the distribution.  Never fails: without it the program simply
    reports that the icon has no menu.

    :param interpreter: The interpreter that runs the GUI; defaults to the venv.
        Probing the system interpreter instead would report success while the
        environment the program actually runs in still cannot see PyGObject.
    :param auto_install: Install the packages instead of only reporting them.
    :return: The finished step (always ``ok``).
    """
    if not paths.IS_LINUX:
        return Step(name="Tray menu", ok=True, detail="native")
    python = interpreter or paths.venv_python()
    if not python.exists():
        python = Path(sys.executable)
    if _module_importable(python, "gi"):
        return Step(name="Tray menu", ok=True, detail="PyGObject")

    key = _system_key("Tray menu support", "appindicator")
    if not auto_install:
        return Step(
            name="Tray menu",
            ok=True,
            detail="PyGObject missing - the tray icon will have no menu",
            hint=manual_install_hint([key]),
        )

    install_system_packages([key])
    if _module_importable(python, "gi"):
        return Step(name="Tray menu", ok=True, detail="PyGObject installed", changed=True)
    return Step(
        name="Tray menu",
        ok=True,
        detail="PyGObject unavailable - the tray icon will have no menu",
        hint=manual_install_hint([key]),
    )


def _module_importable(interpreter: Path, module: str) -> bool:
    """Return ``True`` when ``interpreter`` can import ``module``.

    :param interpreter: The interpreter to ask.
    :param module: The module name to try.
    :return: Whether the import succeeds.
    """
    return run_command([str(interpreter), "-c", "import " + module], echo=False, timeout=60.0).ok


def find_ffmpeg() -> Optional[Path]:
    """Return the FFmpeg executable to use, preferring the private install."""
    bundled = paths.bundled_ffmpeg_exe()
    if bundled.is_file():
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    if paths.IS_WINDOWS:
        found = shutil.which("ffmpeg.exe")
        if found:
            return Path(found)
    return None


def ensure_ffmpeg(auto_install: bool = True) -> Step:
    """Make sure an FFmpeg binary is available.

    :param auto_install: Allow downloading or installing FFmpeg.
    :return: The finished step.
    """
    existing = find_ffmpeg()
    if existing is not None:
        return Step(name="FFmpeg", ok=True, detail=str(existing))

    key = _system_key("FFmpeg", "ffmpeg")
    if not auto_install:
        hint = (
            "Download from https://ffmpeg.org/download.html"
            if paths.IS_WINDOWS
            else manual_install_hint([key])
        )
        return Step(name="FFmpeg", ok=False, detail="missing", hint=hint)

    if paths.IS_WINDOWS:
        error = _install_ffmpeg_windows()
        found = find_ffmpeg()
        if found is not None:
            return Step(name="FFmpeg", ok=True, detail="{0} (downloaded)".format(found), changed=True)
        return Step(
            name="FFmpeg",
            ok=False,
            detail=error or "download failed",
            hint="Download ffmpeg manually and copy ffmpeg.exe to {0}".format(paths.bundled_ffmpeg_bin()),
        )

    result = install_system_packages([key])
    found = find_ffmpeg()
    if found is not None:
        return Step(name="FFmpeg", ok=True, detail="installed", changed=True)
    return Step(name="FFmpeg", ok=False, detail=result.tail(), hint=manual_install_hint([key]))


def find_mpv() -> Optional[Path]:
    """Return the mpv executable to use, preferring the private install."""
    bundled = paths.bundled_mpv_exe()
    if bundled.is_file():
        return bundled
    found = shutil.which("mpv")
    if found:
        return Path(found)
    if paths.IS_WINDOWS:
        found = shutil.which("mpv.exe")
        if found:
            return Path(found)
    return None


def ensure_mpv(auto_install: bool = True) -> Step:
    """Offer mpv for in-tab Streaming video (optional).

    Never fails the setup — Audio mode and downloads work without mpv; Video
    mode then falls back to audio with a status hint.

    :param auto_install: Allow installing mpv via the system package manager.
    :return: The finished step (always ``ok``).
    """
    existing = find_mpv()
    if existing is not None:
        return Step(name="mpv (video, optional)", ok=True, detail=str(existing))

    key = _system_key("mpv", "mpv")
    if paths.IS_WINDOWS:
        hint = "Download mpv from https://mpv.io/installation/ and copy mpv.exe to {0}".format(
            paths.bundled_ffmpeg_bin()
        )
        return Step(
            name="mpv (video, optional)",
            ok=True,
            detail="not installed - Video mode needs mpv for in-window picture",
            hint=hint,
        )

    if not auto_install:
        return Step(
            name="mpv (video, optional)",
            ok=True,
            detail="not installed",
            hint=manual_install_hint([key]),
        )

    result = install_system_packages([key])
    found = find_mpv()
    if found is not None:
        return Step(name="mpv (video, optional)", ok=True, detail="installed", changed=True)
    return Step(
        name="mpv (video, optional)",
        ok=True,
        detail=result.tail() or "not installed",
        hint=manual_install_hint([key]),
    )


def _download(url: str, target: Path) -> Optional[str]:
    """Download ``url`` to ``target`` with a simple progress indicator.

    :param url: The source URL.
    :param target: Destination file.
    :return: ``None`` on success, otherwise the error message.
    """
    request = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed, trusted URLs
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            last_report = -1
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if total:
                        percent = int(done * 100 / total)
                        if percent // 5 != last_report // 5:
                            last_report = percent
                            print(
                                "    downloading... {0}% ({1:.1f} MiB)".format(percent, done / 1048576),
                                file=sys.stderr,
                                flush=True,
                            )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return str(exc)
    return None


def _install_ffmpeg_windows() -> Optional[str]:
    """Download and unpack an FFmpeg build into the application data directory.

    :return: ``None`` on success, otherwise the last error message.
    """
    last_error: Optional[str] = None
    with tempfile.TemporaryDirectory(prefix="clipster-ffmpeg-") as tmp:
        temp_dir = Path(tmp)
        archive = temp_dir / "ffmpeg.zip"
        for url in FFMPEG_WINDOWS_URLS:
            log.info("Downloading FFmpeg from %s ...", url)
            last_error = _download(url, archive)
            if last_error is not None:
                log.warning("Download failed: %s", last_error)
                continue
            try:
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(temp_dir / "extracted")
            except (zipfile.BadZipFile, OSError) as exc:
                last_error = str(exc)
                log.warning("Archive could not be extracted: %s", exc)
                continue

            executables = list((temp_dir / "extracted").rglob("ffmpeg.exe"))
            if not executables:
                last_error = "ffmpeg.exe not found in the archive"
                continue

            source_bin = executables[0].parent
            target_bin = paths.bundled_ffmpeg_bin()
            try:
                target_bin.mkdir(parents=True, exist_ok=True)
                for item in source_bin.iterdir():
                    if item.is_file():
                        shutil.copy2(item, target_bin / item.name)
            except OSError as exc:
                last_error = str(exc)
                continue
            log.info("FFmpeg installed in %s", target_bin)
            return None
    return last_error


def ensure_clipboard_tool(auto_install: bool = True) -> Step:
    """Make sure a clipboard helper exists (Linux only).

    :param auto_install: Allow installing ``xclip`` / ``wl-clipboard``.
    :return: The finished step.
    """
    if not paths.IS_LINUX:
        return Step(name="Clipboard access", ok=True, detail="built into the OS")

    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    preferred = "wl-clipboard" if wayland else "xclip"
    binaries = {"wl-clipboard": "wl-paste", "xclip": "xclip"}

    for package, binary in binaries.items():
        if shutil.which(binary):
            return Step(name="Clipboard access", ok=True, detail=binary)

    if not auto_install:
        return Step(
            name="Clipboard access",
            ok=False,
            detail="xclip / wl-clipboard missing",
            hint=manual_install_hint([preferred]),
        )

    install_system_packages([preferred])
    if shutil.which(binaries[preferred]):
        return Step(name="Clipboard access", ok=True, detail="{0} installed".format(preferred), changed=True)

    # Tk can still read the clipboard, so this must not stop the program.
    return Step(
        name="Clipboard access",
        ok=True,
        detail="fallback: Tk clipboard",
        hint=manual_install_hint([preferred]),
    )


def ensure_js_runtime(auto_install: bool = True) -> Step:
    """Install an optional JavaScript runtime that some yt-dlp extractors use.

    Never fails the setup - yt-dlp works without it for most videos.

    :param auto_install: Allow installing ``quickjs``.
    :return: The finished step (always ``ok``).
    """
    for binary in ("deno", "node", "qjs"):
        if shutil.which(binary):
            return Step(name="JavaScript runtime (optional)", ok=True, detail=binary)

    if not paths.IS_LINUX or not auto_install:
        return Step(name="JavaScript runtime (optional)", ok=True, detail="not installed")

    install_system_packages([_system_key("JavaScript runtime", "js")], refresh=False)
    if shutil.which("qjs"):
        return Step(name="JavaScript runtime (optional)", ok=True, detail="quickjs installed", changed=True)
    return Step(name="JavaScript runtime (optional)", ok=True, detail="not installed")


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------
def bootstrap(
    auto_install: bool = True,
    use_venv: bool = True,
    force_update: bool = False,
    recreate_venv: bool = False,
    update_check_hours: int = 24,
    on_progress: Optional[Callable[[str], None]] = None,
) -> InstallReport:
    """Run every dependency check and install what is missing.

    :param auto_install: Install missing components instead of only reporting them.
    :param use_venv: Manage a private virtual environment for yt-dlp.
    :param force_update: Always check yt-dlp for a newer release.
    :param recreate_venv: Delete and rebuild the virtual environment.
    :param update_check_hours: Minimum hours between two yt-dlp update checks.
    :param on_progress: Optional UI/console callback with a short status line.
    :return: The collected report.
    """
    report = InstallReport()
    paths.ensure_install_dir()

    def note(message: str) -> None:
        log.info("%s", message)
        if on_progress is not None:
            try:
                on_progress(message)
            except Exception:  # pragma: no cover - UI must not abort setup
                log.debug("Bootstrap progress callback failed", exc_info=True)

    note("Checking Python...")
    if not report.add(check_python()).ok:
        return report

    note("Checking tkinter...")
    report.add(check_tkinter(auto_install=auto_install))

    interpreter = Path(sys.executable)
    if use_venv:
        note("Preparing private virtual environment...")
        venv_step = report.add(ensure_venv(recreate=recreate_venv))
        if not venv_step.ok:
            return report
        interpreter = paths.venv_python()

    note("Checking yt-dlp...")
    report.add(
        ensure_ytdlp(
            interpreter=interpreter,
            force_update=force_update,
            update_check_hours=update_check_hours,
        )
    )
    note("Checking FFmpeg...")
    report.add(ensure_ffmpeg(auto_install=auto_install))
    note("Checking mpv (optional, for in-tab video)...")
    report.add(ensure_mpv(auto_install=auto_install))
    note("Checking clipboard helper...")
    report.add(ensure_clipboard_tool(auto_install=auto_install))
    note("Checking tray menu support...")
    report.add(ensure_tray_menu(interpreter=interpreter, auto_install=auto_install))
    note("Checking system tray...")
    report.add(ensure_tray_support(interpreter=interpreter, auto_install=auto_install))
    note("Checking JavaScript runtime...")
    report.add(ensure_js_runtime(auto_install=auto_install))
    note("Dependency check finished.")
    return report
