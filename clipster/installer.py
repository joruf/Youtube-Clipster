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
yt-dlp        ``pip install -U yt-dlp[default]`` (includes yt-dlp-ejs).
FFmpeg        Linux/macOS: package manager; Windows: official ZIP download.
mpv           Optional (in-tab Streaming video). Linux/macOS: package manager;
              Windows: hint only, copy ``mpv.exe`` next to bundled FFmpeg.
Clipboard     Linux only: ``xclip`` or ``wl-clipboard``.
JS runtime    Linux only, optional: ``quickjs`` / node / deno (YouTube signatures).
============  ==================================================================
"""

from __future__ import annotations

import os
import shlex
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
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config as config_module
from . import dependencies
from . import paths
from .logging_setup import get_logger
from .shortcuts import _no_window

log = get_logger(__name__)

MIN_PYTHON = dependencies.MINIMUM_PYTHON

#: Exit code for "the user said no". Distinct from any real failure, so a
#: declined install is never reported as something that went wrong.
DECLINED = 125

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
    #: Components this platform genuinely does not have, so there is nothing to
    #: install. Declared rather than simply left out of ``packages``, so that an
    #: accidental omission still shows up as a missing mapping.
    unsupported: Tuple[str, ...] = ()

    def package_for(self, key: str) -> Optional[str]:
        """Return the distribution package name for a logical component."""
        return self.packages.get(key)

    def supports(self, key: str) -> bool:
        """Return whether this platform has such a component at all.

        :param key: The logical component name.
        :return: ``False`` only for what was explicitly declared missing.
        """
        return key not in self.unsupported


_PACKAGE_MANAGERS: List[PackageManager] = [
    PackageManager(
        # Termux on Android. First in the list and gated on is_termux(), because
        # FreeBSD also has a "pkg" that means something entirely different.
        name="pkg",
        refresh=["pkg", "update", "-y"],
        install=["pkg", "install", "-y"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "adb": "android-tools",
            "tk": "python-tkinter",
            # venv and pip ship with Termux's python package.
            "venv": "python",
            "pip": "python-pip",
            # No X11 here; the clipboard comes from termux-api instead.
            "xclip": "termux-api",
            "wl-clipboard": "termux-api",
            "js": "nodejs",
            "python": "python",
        },
        # Android has no system tray and no AppIndicator to put a menu into.
        unsupported=("appindicator",),
    ),
    PackageManager(
        name="apt-get",
        refresh=["apt-get", "update"],
        install=["apt-get", "install", "-y"],
        packages={
            "ffmpeg": "ffmpeg",
            "mpv": "mpv",
            "adb": "adb",
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
            "adb": "android-tools",
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
            "adb": "android-tools",
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
            "adb": "android-tools",
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
            "adb": "android-tools",
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
            "adb": "android-platform-tools",
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
        if manager.name == "pkg" and not paths.is_termux():
            continue                        # FreeBSD's pkg is a different thing
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
    if _is_root() or (command and command[0] in ("brew", "pkg")):
        # Termux installs into the user's own prefix, Homebrew likewise.
        return list(command)
    if shutil.which("sudo"):
        return ["sudo", "-p", "[sudo] password for %u (YouTube Clipster setup): "] + list(command)
    return None


def _has_display() -> bool:
    """Return whether a graphical session is available to ask for a password in."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _sudo_without_password() -> bool:
    """Return whether ``sudo`` would run right now without asking anything.

    True for root-less setups with ``NOPASSWD`` and for a still-valid timestamp
    from an earlier authentication.

    :return: Whether ``sudo -n`` succeeds.
    """
    if not shutil.which("sudo"):
        return False
    try:
        finished = subprocess.run(["sudo", "-n", "true"], capture_output=True,
                                  timeout=15, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return finished.returncode == 0


def privileged_script(commands: Sequence[Sequence[str]], graphical: bool = False) -> Optional[List[str]]:
    """Return one command that runs several commands as administrator.

    Authenticating once for the whole batch matters for the graphical path: a
    password prompt per package manager call would mean two dialogs for one
    button. The commands run in order, each best effort, and the exit status is
    the last one's - which is what "refresh the index, then install" needs.

    ``graphical`` picks a way to ask for the password that does not need a
    terminal. ``sudo -p`` writes its prompt to the tty; with no tty it simply
    fails, so a window must use ``pkexec`` (which brings its own dialog) or an
    authentication that needs no password at all.

    :param commands: The argument vectors to run, in order.
    :param graphical: Whether there is no terminal to type a password into.
    :return: The runnable command, or ``None`` when privileges are unreachable.
    """
    vectors = [list(command) for command in commands if command]
    if not vectors:
        return None

    if len(vectors) == 1 and (_is_root() or vectors[0][0] in ("brew", "pkg")):
        return vectors[0]

    script = "; ".join(" ".join(shlex.quote(part) for part in vector) for vector in vectors)
    shell = shutil.which("sh") or "/bin/sh"

    # Termux installs into the user's own prefix, Homebrew likewise - no
    # escalation needed, and asking for one would fail on Android.
    if _is_root() or all(vector[0] in ("brew", "pkg") for vector in vectors):
        return [shell, "-c", script]

    if not graphical:
        if shutil.which("sudo"):
            return ["sudo", "-p", "[sudo] password for %u (YouTube Clipster setup): ",
                    shell, "-c", script]
        return None

    # A window has no tty, so a package that decides to ask something would wait
    # for an answer that can never come - with the button greyed out and no way
    # out. On a terminal such a question is answerable, so this is only done here.
    script = "export DEBIAN_FRONTEND=noninteractive; " + script

    # Cheapest first: an already-valid sudo timestamp needs no dialog at all.
    if _sudo_without_password():
        return ["sudo", "-n", shell, "-c", script]
    pkexec = shutil.which("pkexec")
    if pkexec and _has_display():
        return [pkexec, shell, "-c", script]
    return None


def run_command(command: Sequence[str], echo: bool = True, timeout: Optional[float] = 1800.0,
                on_output: Optional[Callable[[str], None]] = None) -> "CommandResult":
    """Run a command, stream its output and capture it.

    :param command: The argument vector to execute.
    :param echo: Mirror the child output on this process' stderr.
    :param timeout: Abort after this many seconds.
    :param on_output: Called with every non-empty output line, as it arrives, so
        a window can show what the package manager is doing.
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
            if on_output is not None and line.strip():
                try:
                    on_output(line)
                except Exception:  # pragma: no cover - a listener must not kill the install
                    log.debug("An output listener raised", exc_info=True)
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

    @property
    def declined(self) -> bool:
        """Return ``True`` when the user refused the install, rather than it failing."""
        return self.returncode == DECLINED

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


#: Signature of a "may I install this?" question: it is handed the package names
#: and the exact command, and answers whether to go ahead.
InstallConfirm = Callable[[List[str], str], bool]

#: Asked before every system package install that does not bring its own
#: question. A module-level hook rather than an argument on all fifteen
#: ``ensure_*`` functions: they all end up in one place anyway, and the setup
#: scripts must keep working untouched. ``None`` means install without asking.
_confirm_hook: Optional[InstallConfirm] = None


def set_install_confirm(hook: Optional[InstallConfirm]) -> None:
    """Install the question asked before system packages are installed.

    :param hook: What to ask, or ``None`` to install without asking.
    :return: None
    """
    global _confirm_hook
    _confirm_hook = hook


def _interactive() -> bool:
    """Return whether there is a terminal a question could be answered on.

    Under ``pythonw.exe`` the standard streams are ``None``, which is why this
    checks for the object before asking it anything.

    :return: Whether stdin and stdout are both a tty.
    """
    for stream in (sys.stdin, sys.stdout):
        if stream is None:
            return False
        try:
            if not stream.isatty():
                return False
        except (AttributeError, ValueError):    # closed or a dummy stream
            return False
    return True


def console_confirm(packages: List[str], command: str) -> bool:
    """Ask on the terminal whether these packages may be installed.

    Unattended runs cannot answer, and they were started precisely to install
    things - so with no terminal this says yes rather than hanging on a question
    nobody will ever see. Pass ``assume_yes`` through instead of relying on that
    when a script wants to be explicit.

    :param packages: The package names about to be installed.
    :param command: The command that would run, for the user to judge.
    :return: Whether to install.
    """
    if not _interactive():
        log.info("No terminal to ask on; installing %s unattended.", " ".join(packages))
        return True
    print("\n  The following has to be installed: {0}".format(" ".join(packages)), file=sys.stderr)
    print("  Command: {0}".format(command), file=sys.stderr)
    try:
        answer = input("  Install it now? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    return answer.strip().lower() in ("y", "yes", "j", "ja")


def install_system_packages(keys: Sequence[str], refresh: bool = True,
                            graphical: bool = False,
                            on_output: Optional[Callable[[str], None]] = None,
                            confirm: Optional[InstallConfirm] = None) -> CommandResult:
    """Install one or more logical components via the distribution package manager.

    :param keys: Logical component names such as ``ffmpeg`` or ``tk``.
    :param refresh: Update the package index first (``apt-get update``, ...).
    :param graphical: Set when no terminal is attached, so the password is asked
        for in a window instead of on a tty that does not exist.
    :param on_output: Called with every output line, for a live log in a window.
    :param confirm: Asked before anything is installed; nothing happens unless it
        answers yes. ``None`` installs without asking, which is what the
        unattended setup scripts rely on.
    :return: The result of the install command.
    """
    manager = detect_package_manager()
    if manager is None:
        return CommandResult(returncode=1, output="No supported package manager found")

    packages = _package_names(manager, keys)
    if not packages:
        return CommandResult(returncode=1, output="No package mapping for {0}".format(", ".join(keys)))

    install = manager.install + packages
    confirm = confirm or _confirm_hook
    if confirm is not None and not confirm(packages, " ".join(install)):
        log.info("The user declined to install: %s", " ".join(packages))
        return CommandResult(returncode=DECLINED,
                             output="Declined - nothing was installed.")

    batch: List[Sequence[str]] = []
    if refresh and manager.refresh:
        batch.append(manager.refresh)
    batch.append(install)

    command = privileged_script(batch, graphical=graphical)
    if command is None:
        return CommandResult(
            returncode=1,
            output="Administrator rights are unavailable - please run: {0}".format(" ".join(install)),
        )
    log.info("Installing system package(s): %s", " ".join(packages))
    return run_command(command, echo=not graphical, on_output=on_output)


def manual_install_hint(keys: Sequence[str]) -> str:
    """Return a copy-pasteable manual install command for the given components."""
    manager = detect_package_manager()
    if manager is None:
        return "Please install manually: {0}".format(", ".join(keys))
    packages = _package_names(manager, keys)
    if not packages:
        return "Please install manually: {0}".format(", ".join(keys))
    prefix = "" if _is_root() or manager.name in ("brew", "pkg") else "sudo "
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


def _ejs_present(interpreter: Path) -> bool:
    """Return ``True`` when the YouTube challenge solver package is installed.

    yt-dlp can import without it, but YouTube signature solving then fails and
    downloads collapse into 403 or a false DRM report.

    :param interpreter: The interpreter to ask.
    :return: Whether ``yt_dlp_ejs`` is importable.
    """
    return run_command(
        [str(interpreter), "-c", "import yt_dlp_ejs"],
        echo=False,
        timeout=120.0,
    ).ok


def _ytdlp_pip_spec() -> str:
    """Return the pip name used to install yt-dlp, including extras."""
    item = dependencies.find_pip("yt-dlp")
    return item.pip_spec() if item is not None else "yt-dlp[default]"


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

    Also installs the ``default`` extra (yt-dlp-ejs) when it is missing, even
    if the yt-dlp version itself is current.  Without that package YouTube
    signature solving fails and downloads look like 403 or DRM errors.

    :param interpreter: Interpreter to install into; defaults to the managed venv.
    :param force_update: Always run an update, ignoring the throttle.
    :param update_check_hours: Minimum hours between two update checks.
    :return: The finished step.
    """
    python = interpreter or paths.venv_python()
    current = _ytdlp_version(python)
    ejs_ok = _ejs_present(python)
    spec = _ytdlp_pip_spec()

    if (
        current
        and ejs_ok
        and not force_update
        and not _update_due(update_check_hours)
    ):
        return Step(name="yt-dlp", ok=True, detail=current)

    if not ensure_pip_usable(python):
        return Step(
            name="yt-dlp",
            ok=False,
            detail="pip is not available",
            hint="Delete {0} and start again, or run --reinstall.".format(paths.venv_dir()),
        )

    if current and not ejs_ok:
        log.info("Installing the YouTube challenge solver (yt-dlp-ejs) ...")
    else:
        log.info("%s yt-dlp ...", "Updating" if current else "Installing")
    result = run_command(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--disable-pip-version-check",
            spec,
        ],
        timeout=1800.0,
    )
    new_version = _ytdlp_version(python)
    ejs_now = _ejs_present(python)

    if new_version:
        _mark_update_checked()
        if not ejs_ok and ejs_now:
            return Step(
                name="yt-dlp",
                ok=True,
                detail="{0}, challenge solver installed".format(new_version),
                changed=True,
            )
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
        hint="Run manually: \"{0}\" -m pip install -U {1}".format(python, spec),
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

    Taken from the optional entries of :mod:`clipster.dependencies` that belong
    to the tray - not every optional package, or a missing QR code library would
    be reported as a broken tray.
    """
    return dependencies.optional_pip_packages(dependencies.current_platform(),
                                              dependencies.TRAY_FEATURE_KEYS)


def ensure_tray_support(interpreter: Optional[Path] = None, auto_install: bool = True) -> Step:
    """Install the optional system tray dependencies.

    The tray is a convenience feature: this step never reports a failure, it
    only records that the program will run without a tray icon.

    :param interpreter: Interpreter to install into; defaults to the managed venv.
    :param auto_install: Install the packages instead of only reporting them.
    :return: The finished step (always ``ok``).
    """
    python = interpreter or paths.venv_python()
    required = dependencies.optional_pip_modules(dependencies.current_platform(),
                                                 dependencies.TRAY_FEATURE_KEYS)

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


def qr_modules() -> List[str]:
    """Return the pip packages behind the phone setup QR code."""
    return dependencies.optional_pip_packages(dependencies.current_platform(),
                                              dependencies.QR_FEATURE_KEYS)


def ensure_qr_support(interpreter: Optional[Path] = None, auto_install: bool = True) -> Step:
    """Install the optional package that draws the phone setup QR code.

    Pure convenience: without it ``--phone-setup`` prints the address as text
    instead of as a code to scan, so this step never fails the setup.

    :param interpreter: Interpreter to install into; defaults to the managed venv.
    :param auto_install: Install the package instead of only reporting it.
    :return: The finished step (always ``ok``).
    """
    python = interpreter or paths.venv_python()
    required = dependencies.optional_pip_modules(dependencies.current_platform(),
                                                 dependencies.QR_FEATURE_KEYS)
    if not required:
        return Step(name="Phone QR code", ok=True, detail="not needed on this platform")
    if _specs_present(python, required):
        return Step(name="Phone QR code", ok=True, detail="qrcode")
    if not auto_install:
        return Step(name="Phone QR code", ok=True, detail="not installed",
                    hint="Run manually: \"{0}\" -m pip install {1}".format(
                        python, " ".join(qr_modules())))
    if not ensure_pip_usable(python):
        return Step(name="Phone QR code", ok=True, detail="skipped - pip is not available")

    log.info("Installing the QR code support ...")
    result = run_command(
        [str(python), "-m", "pip", "install", "--upgrade", "--disable-pip-version-check"] + qr_modules(),
        timeout=600.0,
    )
    if _specs_present(python, required):
        return Step(name="Phone QR code", ok=True, detail="qrcode installed", changed=True)
    return Step(
        name="Phone QR code",
        ok=True,
        detail="unavailable ({0})".format(result.tail(1)),
        hint="The phone setup then shows the address as text. Install manually: "
             "\"{0}\" -m pip install {1}".format(python, " ".join(qr_modules())),
    )


def _is_at_least(version: Tuple[int, ...], minimum: Tuple[int, ...]) -> bool:
    """Return ``True`` when ``version`` meets ``minimum``.

    :param version: Parsed components, possibly shorter than ``minimum``.
    :param minimum: Required version.
    :return: Whether ``version`` is new enough.
    """
    padded = version + (0,) * max(0, len(minimum) - len(version))
    return padded >= minimum


def _parse_dotted_version(text: str) -> Tuple[int, ...]:
    """Return the leading numeric components of a version string.

    :param text: For example ``v22.21.1`` or ``2.3.0 (stable)``.
    :return: ``(22, 21, 1)``, or an empty tuple when nothing numeric is there.
    """
    cleaned = text.strip()
    if cleaned[:1] in "vV":
        cleaned = cleaned[1:]
    parts: List[int] = []
    for item in cleaned.replace("-", ".").split("."):
        digits = ""
        for char in item:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _exe_version(path: Path, arguments: Sequence[str]) -> str:
    """Return the combined stdout/stderr of ``path`` with ``arguments``.

    :param path: Executable to run.
    :param arguments: Arguments such as ``["-v"]``.
    :return: Combined output, or an empty string when the process cannot run.
    """
    try:
        result = subprocess.run(
            [str(path), *list(arguments)],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()


def _nvm_node_binaries() -> List[Path]:
    """Return Node binaries installed by nvm, even when nvm is not on PATH.

    A desktop shortcut and the login autostart do not source ``~/.nvm/nvm.sh``,
    so :func:`shutil.which` never sees those copies.  yt-dlp needs Node 22+,
    which Ubuntu's ``/usr/bin/node`` often is not.

    :return: Existing, executable ``node`` paths.
    """
    found: List[Path] = []
    nvm_dir = os.environ.get("NVM_DIR") or str(Path.home() / ".nvm")
    unix_root = Path(nvm_dir) / "versions" / "node"
    if unix_root.is_dir():
        for binary in unix_root.glob("*/bin/node"):
            if binary.is_file() and os.access(binary, os.X_OK):
                found.append(binary)
    nvm_home = os.environ.get("NVM_HOME")
    if nvm_home:
        windows_root = Path(nvm_home)
        if windows_root.is_dir():
            for binary in windows_root.glob("v*/node.exe"):
                if binary.is_file():
                    found.append(binary)
    return found


def find_js_runtime() -> Optional[Tuple[str, str]]:
    """Return a JavaScript engine yt-dlp can actually use for YouTube.

    yt-dlp 2026.x refuses Node below 22, Deno below 2.3 and Bun below 1.2.11.
    Passing an older ``/usr/bin/node`` makes signature solving fail silently
    and downloads collapse into 403 or a false DRM report.

    :return: ``(yt-dlp runtime name, absolute path)``, or ``None``.
    """
    return _discover_js_runtime()


def _discover_js_runtime() -> Optional[Tuple[str, str]]:
    """Discover a supported JS engine on PATH and in nvm.

    :return: ``(yt-dlp runtime name, absolute path)``, or ``None``.
    """
    seen = set()
    node_candidates: List[Path] = []
    for name in ("node", "nodejs"):
        located = shutil.which(name)
        if located:
            path = Path(located).resolve()
            if path not in seen:
                seen.add(path)
                node_candidates.append(path)
    for binary in _nvm_node_binaries():
        try:
            path = binary.resolve()
        except OSError:
            continue
        if path not in seen:
            seen.add(path)
            node_candidates.append(path)

    best: Optional[Tuple[Tuple[int, ...], Path]] = None
    for path in node_candidates:
        version = _parse_dotted_version(_exe_version(path, ["-v"]))
        if _is_at_least(version, (22, 0, 0)) and (best is None or version > best[0]):
            best = (version, path)
    if best is not None:
        return ("node", str(best[1]))

    deno = shutil.which("deno")
    if deno:
        output = _exe_version(Path(deno), ["--version"])
        version = _parse_dotted_version(output.split("deno", 1)[-1] if output else "")
        if _is_at_least(version, (2, 3, 0)):
            return ("deno", str(Path(deno).resolve()))

    bun = shutil.which("bun")
    if bun:
        version = _parse_dotted_version(_exe_version(Path(bun), ["--version"]))
        if _is_at_least(version, (1, 2, 11)):
            return ("bun", str(Path(bun).resolve()))

    qjs = shutil.which("qjs")
    if qjs:
        return ("quickjs", str(Path(qjs).resolve()))
    return None


def ensure_js_runtime(auto_install: bool = True) -> Step:
    """Install an optional JavaScript runtime that yt-dlp-ejs uses.

    Never fails the setup.  YouTube signature solving needs both this engine
    and the yt-dlp-ejs package; without them some formats come back 403.

    :param auto_install: Allow installing ``quickjs``.
    :return: The finished step (always ``ok``).
    """
    found = find_js_runtime()
    if found is not None:
        name, path = found
        return Step(
            name="JavaScript runtime (optional)",
            ok=True,
            detail="{0} ({1})".format(name, path),
        )

    if not paths.IS_LINUX or not auto_install:
        return Step(name="JavaScript runtime (optional)", ok=True, detail="not installed")

    install_system_packages([_system_key("JavaScript runtime", "js")], refresh=False)
    found = find_js_runtime()
    if found is not None:
        name, path = found
        return Step(
            name="JavaScript runtime (optional)",
            ok=True,
            detail="{0} installed ({1})".format(name, path),
            changed=True,
        )
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
    need_gui: bool = True,
    ask: bool = False,
) -> InstallReport:
    """Run every dependency check and install what is missing.

    :param auto_install: Install missing components instead of only reporting them.
    :param use_venv: Manage a private virtual environment for yt-dlp.
    :param force_update: Always check yt-dlp for a newer release.
    :param recreate_venv: Delete and rebuild the virtual environment.
    :param update_check_hours: Minimum hours between two yt-dlp update checks.
    :param on_progress: Optional UI/console callback with a short status line.
    :param need_gui: Whether windows will be opened. Without them tkinter, the
        tray and the clipboard helpers are not needed - which is what makes a
        server, a Raspberry Pi or Termux on Android workable.
    :param ask: Ask on the terminal before installing a system package. Without a
        terminal the question cannot be answered, so it installs as before -
        otherwise the unattended setup scripts would stall on it.
    :return: The collected report.
    """
    report = InstallReport()
    paths.ensure_install_dir()
    if ask:
        set_install_confirm(console_confirm)

    def finish(collected: InstallReport) -> InstallReport:
        """Drop the question hook again - it is process wide."""
        if ask:
            set_install_confirm(None)
        return collected

    def note(message: str) -> None:
        log.info("%s", message)
        if on_progress is not None:
            try:
                on_progress(message)
            except Exception:  # pragma: no cover - UI must not abort setup
                log.debug("Bootstrap progress callback failed", exc_info=True)

    note("Checking Python...")
    if not report.add(check_python()).ok:
        return finish(report)

    if need_gui:
        note("Checking tkinter...")
        report.add(check_tkinter(auto_install=auto_install))
    else:
        log.info("[OK] tkinter - not needed without windows")

    interpreter = Path(sys.executable)
    if use_venv:
        note("Preparing private virtual environment...")
        venv_step = report.add(ensure_venv(recreate=recreate_venv))
        if not venv_step.ok:
            return finish(report)
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
    if need_gui:
        note("Checking clipboard helper...")
        report.add(ensure_clipboard_tool(auto_install=auto_install))
        note("Checking tray menu support...")
        report.add(ensure_tray_menu(interpreter=interpreter, auto_install=auto_install))
        note("Checking system tray...")
        report.add(ensure_tray_support(interpreter=interpreter, auto_install=auto_install))
    else:
        # No clipboard to watch and no tray to sit in without a desktop.
        log.info("[OK] Clipboard access, tray - not needed without windows")
    note("Checking QR code support (optional, for the phone setup)...")
    report.add(ensure_qr_support(interpreter=interpreter, auto_install=auto_install))
    note("Checking JavaScript runtime...")
    report.add(ensure_js_runtime(auto_install=auto_install))
    note("Dependency check finished.")
    return finish(report)
