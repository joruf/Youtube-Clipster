"""Talking to a phone plugged into this computer, over ``adb``.

Used by the "Install on Android" wizard: find the phone, say what still has to
be tapped on it, hand the program over, and finish the setup inside Termux.

``adb shell`` runs as the ``shell`` user and cannot write into Termux's private
home. Official GitHub Termux is **debuggable**, so the preferred path is:

1. ``adb push`` the archive to ``/data/local/tmp``
2. ``run-as com.termux cp`` it into Termux's home
3. type only ``bash ~/clipster-phone-setup.sh`` into Termux

That avoids ``/sdcard/Download``, which often returns *Permission denied* until
the user grants all-files access. Shared storage remains the fallback when
``run-as`` is unavailable (Play Store builds).

Nothing here touches the interface; the wizard does the talking.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from . import paths
from .logging_setup import get_logger
from .shortcuts import _no_window

log = get_logger(__name__)

#: Where the bundle is put on the phone: readable by Termux, no root needed.
REMOTE_DIR = "/sdcard/Download"

#: Staging directory the ``shell`` user can write and Termux can read.
STAGING_DIR = "/data/local/tmp"

#: Termux app private home (only reachable via ``run-as`` when debuggable).
TERMUX_HOME = "/data/data/com.termux/files/home"

#: Name of the archive handed to the phone.
BUNDLE_NAME = "youtube-clipster-android.tar.gz"

#: Bootstrap script pushed next to the archive; only this short path is typed.
SETUP_SCRIPT_NAME = "clipster-phone-setup.sh"

#: What winget calls Google's Android SDK platform tools, which contain adb.
#: Windows has no distribution repository to take adb from, so this is the only
#: automatic route there - and it comes with Google's own licence.
WINGET_PACKAGE = "Google.PlatformTools"

#: Where those terms can be read before accepting them.
SDK_TERMS_URL = "https://developer.android.com/studio/terms"

#: Official Termux build to offer when the Play Store package is detected.
#: Prefer the apt-android-7 GitHub build (Android 7+). Play Store Termux is
#: discontinued. Note: Termux still ships with targetSdk 28; Android 14+ may
#: show a one-time "built for an older Android" notice — that is Termux, not
#: Clipster, and is safe to dismiss.
TERMUX_GITHUB_VERSION = "v0.119.0-beta.3"
TERMUX_GITHUB_APK_NAME = (
    "termux-app_{0}+apt-android-7-github-debug_universal.apk"
).format(TERMUX_GITHUB_VERSION)
TERMUX_GITHUB_APK_URL = (
    "https://github.com/termux/termux-app/releases/download/"
    "{0}/{1}"
).format(TERMUX_GITHUB_VERSION, TERMUX_GITHUB_APK_NAME)
TERMUX_GITHUB_PAGE = "https://github.com/termux/termux-app/releases"

#: Directories that must never travel: private data, build leftovers, history.
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                        ".mypy_cache", "node_modules", ".idea", ".vscode"})

#: Files that must never travel - the configuration holds the remote token.
_SKIP_FILES = frozenset({"config.json", "history.json", "discover_taste.json",
                         "youtube-clipster.log"})

#: ``adb push`` prints its progress like ``[ 42%] /sdcard/Download/...``.
_PROGRESS = re.compile(r"\[\s*(\d{1,3})%\]")

#: Pulls ``versionName=...`` out of ``dumpsys package``.
_VERSION_NAME = re.compile(r"versionName=(\S+)")

#: The app that has to be on screen before anything is typed into it.
TERMUX_PACKAGE = "com.termux"

#: Pulls ``com.termux/com.termux.app.TermuxActivity`` out of a dumpsys line.
_PACKAGE = re.compile(r"([a-zA-Z][\w.]*\.[\w.]+)/[\w.$]+")

#: What a device line of ``adb devices`` means for the user.
STATE_READY = "device"
STATE_UNAUTHORISED = "unauthorized"
STATE_OFFLINE = "offline"

#: Android keycodes used while preparing Termux for typing.
KEYCODE_BACK = 4
KEYCODE_ENTER = 66

#: Pause after Termux reaches the foreground so MIUI finishes settling focus.
TERMUX_FOCUS_PAUSE = 0.8


@dataclass(frozen=True)
class Device:
    """One phone as ``adb`` sees it."""

    serial: str
    state: str
    model: str = ""

    @property
    def ready(self) -> bool:
        """Return whether files can be pushed to it right now."""
        return self.state == STATE_READY

    @property
    def needs_confirmation(self) -> bool:
        """Return whether the phone is waiting for the USB debugging prompt."""
        return self.state == STATE_UNAUTHORISED

    def describe(self) -> str:
        """Return a short human readable label."""
        name = self.model or self.serial
        return "{0} ({1})".format(name, self.state)


def adb_path() -> Optional[str]:
    """Return the ``adb`` executable, or ``None`` when it is not installed.

    :return: An absolute path, or ``None``.
    """
    found = shutil.which("adb")
    if found:
        return found
    if paths.IS_WINDOWS:
        # The platform tools are often unpacked next to the user's downloads
        # rather than put on PATH.
        for candidate in (Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"
                          / "platform-tools" / "adb.exe",
                          Path(os.environ.get("PROGRAMFILES", "")) / "platform-tools" / "adb.exe"):
            if candidate.is_file():
                return str(candidate)
        # Freshly installed by winget: on PATH for new processes, not for this one.
        for installed in _winget_locations():
            if installed.is_file():
                return str(installed)
    return None


def _winget_locations() -> List[Path]:
    """Return where winget puts the platform tools.

    Portable packages land in winget's own package directory and are reached
    through a links folder that is added to ``PATH`` - but not to the ``PATH``
    this already-running process inherited. So after an install the executable
    has to be looked for where it actually is.

    :return: Candidate ``adb.exe`` paths, newest-looking last.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
    try:
        found = sorted(packages.glob("Google.PlatformTools*/**/adb.exe"))
    except OSError:  # pragma: no cover - unreadable directory
        return []
    return found


def adb_install_plan() -> Tuple[str, str]:
    """Return how ``adb`` would be installed on this system.

    Kept free of side effects so the wizard can show the exact command before
    anything happens, and so every platform's answer is testable.

    :return: ``(kind, command)``. ``kind`` is ``package`` for a distribution
        package, ``winget`` for Google's platform tools on Windows, or
        ``manual`` when nothing can do it automatically.
    """
    if paths.IS_WINDOWS:
        if shutil.which("winget"):
            return "winget", "winget install --exact --id {0}".format(WINGET_PACKAGE)
        return "manual", ""
    from .installer import detect_package_manager

    manager = detect_package_manager()
    if manager is None:
        return "manual", ""
    package = manager.package_for("adb")
    if not package:
        return "manual", ""
    return "package", "{0} {1}".format(" ".join(manager.install), package)


def install_adb(accept_licence: bool = False,
                on_output: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """Install ``adb``, after the caller has asked the user.

    This does not ask - the window does, because it has to show what will be run
    before it runs. Two very different things happen depending on the platform:

    * On Linux, macOS and Termux ``adb`` comes from the distribution's own
      repository, which already redistributes it under the Apache 2.0 licence.
      Nothing extra has to be agreed to.
    * On Windows there is no such repository, so winget fetches Google's Android
      SDK platform tools. Those carry Google's own licence agreement, and
      accepting it silently on someone's behalf is not this program's business -
      hence ``accept_licence``, which the window only sets once the user has
      seen the terms and clicked.

    :param accept_licence: Whether the user accepted Google's SDK terms. Required
        on Windows, irrelevant everywhere else.
    :param on_output: Called with each output line while the install runs.
    :return: ``(success, message)``.
    """
    kind, command = adb_install_plan()
    if kind == "manual":
        return False, "No package manager on this system can install adb."

    if kind == "winget":
        if not accept_licence:
            return False, "Google's SDK terms were not accepted."
        argv = ["winget", "install", "--exact", "--id", WINGET_PACKAGE,
                "--source", "winget", "--accept-source-agreements",
                # The user accepted this in the window; see the note above.
                "--accept-package-agreements", "--disable-interactivity"]
        from .installer import run_command

        result = run_command(argv, echo=False, on_output=on_output, timeout=1800.0)
        if result.ok:
            return True, adb_path() or WINGET_PACKAGE
        return False, result.tail() or "winget failed"

    from .installer import install_system_packages

    # The window already asked; the hook must not ask a second time.
    result = install_system_packages(["adb"], graphical=True, on_output=on_output,
                                     confirm=lambda packages, shown: True)
    if result.ok:
        return True, adb_path() or "adb"
    if result.declined:  # pragma: no cover - the window never declines here
        return False, "Declined."
    return False, result.tail() or "The package manager failed."


def parse_devices(output: str) -> List[Device]:
    """Turn the output of ``adb devices -l`` into device objects.

    Kept separate from running the command so every state a phone can be in is
    testable without a phone.

    :param output: Everything ``adb devices -l`` printed.
    :return: One entry per line that described a device.
    """
    devices: List[Device] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        for extra in parts[2:]:
            if extra.startswith("model:"):
                model = extra.split(":", 1)[1].replace("_", " ")
        devices.append(Device(serial=serial, state=state, model=model))
    return devices


def devices() -> List[Device]:
    """Return the phones currently plugged in.

    :return: The devices, or an empty list when adb is missing or silent.
    """
    adb = adb_path()
    if not adb:
        return []
    try:
        finished = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True,
                                  timeout=20, **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("adb devices failed: %s", exc)
        return []
    return parse_devices(finished.stdout)


def bundle_members(root: Path) -> List[Path]:
    """Return every file that belongs in the archive.

    :param root: The checkout to package.
    :return: Absolute paths, in a stable order.
    """
    chosen: List[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & _SKIP_DIRS or path.name in _SKIP_FILES:
            continue
        chosen.append(path)
    return chosen


def make_bundle(root: Path, target: Path,
                on_progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """Pack the checkout into a ``.tar.gz`` for the phone.

    The configuration and the history stay behind: the configuration holds the
    remote control token, which has no business travelling to another device.

    :param root: The checkout to package.
    :param target: The archive to write.
    :param on_progress: Called with ``(done, total)`` per file.
    :return: The written archive.
    """
    members = bundle_members(root)
    total = len(members)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        for index, path in enumerate(members, start=1):
            archive.add(path, arcname=str(Path(root.name) / path.relative_to(root)))
            if on_progress is not None:
                on_progress(index, total)
    log.info("Android bundle written: %s (%s files, %s bytes)",
             target, total, target.stat().st_size)
    return target


def setup_script_body(bundle_name: str = BUNDLE_NAME, remote_dir: str = REMOTE_DIR,
                      accept_terms: bool = True, *, in_home: bool = False) -> str:
    """Return the bootstrap script that Termux will run.

    The PC only types a short ``bash …/clipster-phone-setup.sh`` line; this is
    what that script contains. Long ``;`` / ``&&`` chains are never sent through
    ``input text``.

    :param bundle_name: Name of the transferred archive.
    :param remote_dir: Where it was put (shared storage). Ignored when
        ``in_home`` is true.
    :param accept_terms: Whether to pass ``--accept-terms`` (already confirmed on
        the PC).
    :param in_home: Whether the archive already sits in Termux's home (``run-as``
        path). Skips ``termux-setup-storage`` and shared-storage paths.
    :return: Script text ending with a newline.
    """
    flags = " --accept-terms" if accept_terms else ""
    if in_home:
        return (
            "#!/data/data/com.termux/files/usr/bin/bash\n"
            "# Written by YouTube Clipster - safe to delete after install.\n"
            "set -u\n"
            "BUNDLE=\"{bundle}\"\n"
            "pkg install -y tar || exit 1\n"
            "tar -xzf \"$HOME/$BUNDLE\" -C \"$HOME\" || exit 1\n"
            "cd \"$HOME/youtube-clipster\" || exit 1\n"
            "exec bash install-android.sh{flags}\n"
        ).format(bundle=bundle_name, flags=flags)
    remote = remote_dir.rstrip("/")
    return (
        "#!/data/data/com.termux/files/usr/bin/bash\n"
        "# Written by YouTube Clipster - safe to delete after install.\n"
        "set -u\n"
        "REMOTE=\"{remote}\"\n"
        "BUNDLE=\"{bundle}\"\n"
        "termux-setup-storage || true\n"
        "pkg install -y tar || exit 1\n"
        "tar -xzf \"$REMOTE/$BUNDLE\" -C ~ || exit 1\n"
        "cd ~/youtube-clipster || exit 1\n"
        "exec bash install-android.sh{flags}\n"
    ).format(remote=remote, bundle=bundle_name, flags=flags)


def write_setup_script(target: Path, bundle_name: str = BUNDLE_NAME,
                       remote_dir: str = REMOTE_DIR,
                       accept_terms: bool = True,
                       in_home: bool = False) -> Path:
    """Write the bootstrap script to ``target``.

    :param target: Local path for the script.
    :param bundle_name: Name of the archive on the phone.
    :param remote_dir: Directory on the phone that holds the archive.
    :param accept_terms: Whether the install may skip the interactive terms prompt.
    :param in_home: Write the home-based script (no ``/sdcard``).
    :return: The written path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        setup_script_body(bundle_name=bundle_name, remote_dir=remote_dir,
                          accept_terms=accept_terms, in_home=in_home),
        encoding="utf-8",
    )
    try:
        target.chmod(target.stat().st_mode | 0o111)
    except OSError:  # pragma: no cover - Windows may ignore execute bits
        pass
    return target


def launch_command(remote_dir: str = REMOTE_DIR,
                   script_name: str = SETUP_SCRIPT_NAME,
                   in_home: bool = False) -> str:
    """Return the short line typed into Termux.

    :param remote_dir: Where the bootstrap script was put on shared storage.
    :param script_name: Name of that script.
    :param in_home: Whether the script lives in Termux's home (``~/…``).
    :return: A single short shell command.
    """
    if in_home:
        return "bash ~/{0}".format(script_name)
    return "bash {0}/{1}".format(remote_dir.rstrip("/"), script_name)


def install_command(bundle_name: str = BUNDLE_NAME, remote_dir: str = REMOTE_DIR,
                    accept_terms: bool = True) -> str:
    """Return the fallback one-liner for pasting by hand.

    Prefer :func:`launch_command` after the bootstrap script has been pushed.
    This longer form still unpacks without that script, for emergency use.

    :param bundle_name: Name of the transferred archive.
    :param remote_dir: Where it was put.
    :param accept_terms: Whether to pass ``--accept-terms``.
    :return: A single shell command.
    """
    remote = remote_dir.rstrip("/")
    flags = " --accept-terms" if accept_terms else ""
    return (
        "termux-setup-storage; pkg install -y tar && "
        "tar -xzf {0}/{1} -C ~ && "
        "cd ~/youtube-clipster && bash install-android.sh{2}"
    ).format(remote, bundle_name, flags)


def push(bundle: Path, serial: str = "",
         on_progress: Optional[Callable[[int], None]] = None,
         remote_dir: str = REMOTE_DIR) -> Tuple[bool, str]:
    """Copy a file to the phone's shared storage.

    :param bundle: The local file to send.
    :param serial: Which device, when several are plugged in.
    :param on_progress: Called with the percentage adb reports.
    :param remote_dir: Target directory on the phone.
    :return: ``(success, message)``.
    """
    adb = adb_path()
    if not adb:
        return False, "adb is not installed"
    if not bundle.is_file():
        return False, "the archive is missing"
    remote = "{0}/{1}".format(remote_dir.rstrip("/"), bundle.name)
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["push", str(bundle), remote]
    log.info("Transferring to the phone: %s", " ".join(command[1:]))
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)

    tail: List[str] = []
    if process.stdout is not None:
        for line in process.stdout:
            text = line.strip()
            if text:
                tail.append(text)
                del tail[:-5]
            match = _PROGRESS.search(text)
            if match and on_progress is not None:
                on_progress(max(0, min(100, int(match.group(1)))))
    process.wait()
    if process.returncode == 0:
        if on_progress is not None:
            on_progress(100)
        return True, remote
    return False, " | ".join(tail) or "adb push failed"


def transfer(bundle: Path, setup_script: Path, serial: str = "",
             on_progress: Optional[Callable[[int], None]] = None,
             remote_dir: str = REMOTE_DIR) -> Tuple[bool, str, bool]:
    """Copy the archive and the bootstrap script onto the phone.

    Prefers Termux's private home via ``run-as`` (no storage permission). Falls
    back to shared storage when that is unavailable.

    Progress is reported across both pushes (0–100).

    :param bundle: The packed checkout.
    :param setup_script: The short script Termux will run.
    :param serial: Which device, when several are plugged in.
    :param on_progress: Called with overall percentage.
    :param remote_dir: Shared-storage fallback directory.
    :return: ``(success, message, in_home)``. ``message`` is the script path to
        type; ``in_home`` says whether :func:`launch_command` needs ``in_home``.
    """
    def scale(start: int, end: int) -> Callable[[int], None]:
        def report(percent: int) -> None:
            if on_progress is not None:
                span = max(0, end - start)
                on_progress(start + int(span * max(0, min(100, percent)) / 100))
        return report

    if termux_run_as_available(serial):
        ok, message = push_into_termux(bundle, serial=serial,
                                       on_progress=scale(0, 90))
        if not ok:
            return False, message, True
        ok, message = push_into_termux(setup_script, serial=serial,
                                       on_progress=scale(90, 100))
        if not ok:
            return False, message, True
        return True, message, True

    ok, message = push(bundle, serial=serial, on_progress=scale(0, 90),
                       remote_dir=remote_dir)
    if not ok:
        return False, message, False
    ok, message = push(setup_script, serial=serial, on_progress=scale(90, 100),
                       remote_dir=remote_dir)
    if not ok:
        return False, message, False
    return True, message, False


def termux_run_as_available(serial: str = "") -> bool:
    """Return whether ``run-as com.termux`` can write into Termux's home.

    Official GitHub / F-Droid Termux builds are debuggable; Play Store builds are
    not. Without ``run-as``, files must go through shared storage.

    :param serial: Which device, when several are plugged in.
    :return: Whether a trivial write via ``run-as`` succeeded.
    """
    marker = "{0}/.clipster-run-as-check".format(TERMUX_HOME)
    code, _ = _adb_shell(
        ["run-as", TERMUX_PACKAGE, "sh", "-c",
         "echo ok > {0} && rm -f {0}".format(marker)],
        serial=serial, timeout=15.0,
    )
    return code == 0


def push_into_termux(local: Path, serial: str = "",
                     on_progress: Optional[Callable[[int], None]] = None
                     ) -> Tuple[bool, str]:
    """Copy ``local`` into Termux's home via ``/data/local/tmp`` and ``run-as``.

    Avoids ``/sdcard``, which Termux cannot read until all-files access is
    granted (common source of *Permission denied* on Xiaomi/HyperOS).

    :param local: File on this computer.
    :param serial: Which device, when several are plugged in.
    :param on_progress: Optional percentage callback.
    :return: ``(success, absolute path inside Termux home or error)``.
    """
    if not local.is_file():
        return False, "the file is missing"
    staged = "{0}/clipster-{1}".format(STAGING_DIR.rstrip("/"), local.name)
    home_path = "{0}/{1}".format(TERMUX_HOME.rstrip("/"), local.name)

    adb = adb_path()
    if not adb:
        return False, "adb is not installed"
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["push", str(local), staged]
    log.info("Staging for Termux: %s", " ".join(command[1:]))
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)

    tail: List[str] = []
    if process.stdout is not None:
        for line in process.stdout:
            text = line.strip()
            if text:
                tail.append(text)
                del tail[:-5]
            match = _PROGRESS.search(text)
            if match and on_progress is not None:
                on_progress(max(0, min(90, int(match.group(1)))))
    process.wait()
    if process.returncode != 0:
        return False, " | ".join(tail) or "adb push failed"

    code, output = _adb_shell(
        ["run-as", TERMUX_PACKAGE, "cp", staged, home_path],
        serial=serial, timeout=60.0,
    )
    _adb_shell(["rm", "-f", staged], serial=serial, timeout=10.0)
    if code != 0:
        return False, output or "run-as cp failed"
    if on_progress is not None:
        on_progress(100)
    return True, home_path


def open_termux(serial: str = "") -> bool:
    """Bring Termux to the front on the phone, so the last step can be typed.

    :param serial: Which device, when several are plugged in.
    :return: Whether the request was accepted.
    """
    adb = adb_path()
    if not adb:
        return False
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "monkey", "-p", TERMUX_PACKAGE, "-c",
                "android.intent.category.LAUNCHER", "1"]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=20,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Termux could not be started: %s", exc)
        return False
    return finished.returncode == 0


def foreground_app(serial: str = "") -> str:
    """Return the package name of the app currently on screen.

    Asked before anything is typed. Keystrokes go to whatever has focus, so
    without this check a command could land in a chat window.

    :param serial: Which device, when several are plugged in.
    :return: The package name, or an empty string when it cannot be determined.
    """
    adb = adb_path()
    if not adb:
        return ""
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "dumpsys", "window"]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=20,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("The foreground app could not be determined: %s", exc)
        return ""
    for line in finished.stdout.splitlines():
        if "mCurrentFocus" not in line and "mFocusedApp" not in line:
            continue
        match = _PACKAGE.search(line)
        if match:
            return match.group(1)
    return ""


def typeable(text: str) -> bool:
    """Return whether ``text`` can be typed safely with ``input text``.

    ``input text`` reads ``%s`` as a space, and the text has to survive being
    single-quoted for the shell on the phone. Anything carrying a quote or a
    percent sign is refused rather than typed wrongly - a half-typed command is
    worse than none.

    :param text: The command to type.
    :return: Whether it can be sent as is.
    """
    return bool(text) and "'" not in text and "%" not in text and "\n" not in text


def type_text(text: str, serial: str = "") -> bool:
    """Type ``text`` into whatever is on screen on the phone.

    :param text: The text to type. Must pass :func:`typeable`.
    :param serial: Which device, when several are plugged in.
    :return: Whether adb accepted it.
    """
    adb = adb_path()
    if not adb or not typeable(text):
        return False
    command = [adb]
    if serial:
        command += ["-s", serial]
    # Single-quoted for the phone's shell; spaces as %s, which is what input
    # text expects and what survives every Android version reliably.
    command += ["shell", "input text '{0}'".format(text.replace(" ", "%s"))]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("The text could not be typed: %s", exc)
        return False
    return finished.returncode == 0


def press_key(keycode: int, serial: str = "") -> bool:
    """Send one Android keyevent to the phone.

    :param keycode: The Android key code.
    :param serial: Which device, when several are plugged in.
    :return: Whether adb accepted it.
    """
    adb = adb_path()
    if not adb:
        return False
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "input", "keyevent", str(int(keycode))]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Key %s could not be pressed: %s", keycode, exc)
        return False
    return finished.returncode == 0


def press_enter(serial: str = "") -> bool:
    """Press the return key on the phone.

    :param serial: Which device, when several are plugged in.
    :return: Whether adb accepted it.
    """
    return press_key(KEYCODE_ENTER, serial)


def dismiss_shade(serial: str = "") -> None:
    """Close the notification shade if it is stealing focus.

    ``input text`` goes to whatever is focused; with the shade open that is not
    Termux. A Back keyevent is harmless when the shade is already closed.

    :param serial: Which device, when several are plugged in.
    :return: None
    """
    press_key(KEYCODE_BACK, serial)
    time.sleep(0.25)


def wait_for_termux(serial: str = "", timeout: float = 20.0, poll: float = 0.5) -> bool:
    """Wait until Termux is the app on screen.

    :param serial: Which device, when several are plugged in.
    :param timeout: How long to wait, in seconds.
    :param poll: Seconds between two looks.
    :return: Whether Termux got there.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if foreground_app(serial).startswith(TERMUX_PACKAGE):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.1, poll))


def run_on_phone(command: str, serial: str = "",
                 on_status: Optional[Callable[[str], None]] = None,
                 focus_pause: float = TERMUX_FOCUS_PAUSE,
                 open_timeout: float = 20.0) -> Tuple[bool, str]:
    """Open Termux on the phone and type the command into it.

    Prefer :func:`launch_command` here: long install lines with ``;`` / ``&&``
    often fail on MIUI and similar skins. The unpacking lives in the bootstrap
    script on shared storage instead.

    The foreground check is not politeness: ``input text`` goes to whatever has
    focus, so if Termux never comes up, this refuses rather than typing a shell
    command into whatever app happens to be open.

    :param command: The line to run on the phone.
    :param serial: Which device, when several are plugged in.
    :param on_status: Called with a short progress key: ``opening``, ``typing``.
    :param focus_pause: Seconds to wait after Termux is focused (MIUI settle).
    :param open_timeout: How long to wait for Termux to reach the foreground.
    :return: ``(success, reason)``. The reason names what failed, or is empty.
    """
    if not typeable(command):
        return False, "untypeable"
    if on_status is not None:
        on_status("opening")
    dismiss_shade(serial)
    if not open_termux(serial):
        return False, "termux_missing"
    if not wait_for_termux(serial, timeout=open_timeout):
        return False, "termux_not_open"
    if focus_pause > 0:
        time.sleep(focus_pause)
    if not foreground_app(serial).startswith(TERMUX_PACKAGE):
        return False, "termux_not_open"
    if on_status is not None:
        on_status("typing")
    if not type_text(command, serial):
        return False, "typing_failed"
    if not press_enter(serial):
        return False, "typing_failed"
    log.info("The install command was typed into Termux on the phone.")
    return True, ""


def termux_installed(serial: str = "") -> bool:
    """Return whether the Termux package is present on the phone.

    :param serial: Which device, when several are plugged in.
    :return: Whether ``pm path`` found the package.
    """
    adb = adb_path()
    if not adb:
        return False
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "pm", "path", TERMUX_PACKAGE]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=20,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Termux package check failed: %s", exc)
        return False
    return finished.returncode == 0 and "package:" in (finished.stdout or "")


def termux_version_name(serial: str = "") -> str:
    """Return Termux's ``versionName``, or an empty string when unknown.

    :param serial: Which device, when several are plugged in.
    :return: The version string from ``dumpsys package``.
    """
    adb = adb_path()
    if not adb:
        return ""
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "dumpsys", "package", TERMUX_PACKAGE]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Termux version could not be read: %s", exc)
        return ""
    match = _VERSION_NAME.search(finished.stdout or "")
    return match.group(1) if match else ""


def termux_is_play_store(serial: str = "") -> bool:
    """Return whether the installed Termux looks like the Play Store build.

    That build is discontinued and cannot install packages correctly.

    :param serial: Which device, when several are plugged in.
    :return: Whether the version name points at Play Store Termux.
    """
    version = termux_version_name(serial).lower()
    return "googleplay" in version


def download_termux_apk(target: Path,
                       url: str = TERMUX_GITHUB_APK_URL,
                       on_status: Optional[Callable[[str], None]] = None) -> Path:
    """Download the official Termux APK to ``target``.

    :param target: Local path to write.
    :param url: APK URL.
    :param on_status: Optional progress label callback.
    :return: The written path.
    :raises OSError: When the download fails.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if on_status is not None:
        on_status("downloading")
    try:
        urllib.request.urlretrieve(url, str(target))
    except (urllib.error.URLError, OSError) as exc:
        raise OSError("Termux APK download failed: {0}".format(exc)) from exc
    if not target.is_file() or target.stat().st_size < 1000:
        raise OSError("Termux APK download produced an empty file")
    return target


def _adb_shell(args: List[str], serial: str = "", timeout: float = 30.0
               ) -> Tuple[int, str]:
    """Run ``adb shell …`` and return ``(returncode, combined output)``.

    :param args: Arguments after ``shell``.
    :param serial: Which device, when several are plugged in.
    :param timeout: Seconds before giving up.
    :return: Exit code and stdout+stderr.
    """
    adb = adb_path()
    if not adb:
        return 1, "adb is not installed"
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell"] + list(args)
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return finished.returncode, ((finished.stdout or "") + "\n" + (finished.stderr or "")).strip()


def is_verification_failure(message: str) -> bool:
    """Return whether ``adb install`` was blocked by the phone's USB install guard.

    Common on Xiaomi/HyperOS when *Install via USB* is off; also Play Protect.

    :param message: The failure text from :func:`install_apk`.
    :return: Whether this is that class of failure.
    """
    text = (message or "").upper()
    return "INSTALL_FAILED_VERIFICATION_FAILURE" in text or "INSTALL_CANCELED_BY_USER" in text


def push_apk_for_manual_install(apk: Path, serial: str = "",
                                remote_dir: str = REMOTE_DIR,
                                remote_name: str = "termux-github.apk"
                                ) -> Tuple[bool, str]:
    """Copy an APK to shared storage so the user can tap Install in Files.

    :param apk: Local APK.
    :param serial: Which device, when several are plugged in.
    :param remote_dir: Target directory on the phone.
    :param remote_name: Filename on the phone.
    :return: ``(success, remote path or error)``.
    """
    if not apk.is_file():
        return False, "the APK is missing"
    # push() uses the local basename; copy/rename into a temp name when needed.
    if apk.name == remote_name:
        return push(apk, serial=serial, remote_dir=remote_dir)
    import tempfile
    workspace = Path(tempfile.mkdtemp(prefix="clipster-apk-"))
    staged = workspace / remote_name
    try:
        shutil.copy2(apk, staged)
        return push(staged, serial=serial, remote_dir=remote_dir)
    finally:
        try:
            staged.unlink(missing_ok=True)
            workspace.rmdir()
        except OSError:  # pragma: no cover
            pass


def open_file_manager(serial: str = "") -> bool:
    """Bring a file manager to the front so the user can open the APK.

    :param serial: Which device, when several are plugged in.
    :return: Whether a launcher request was accepted.
    """
    # Xiaomi / HyperOS global file explorer (component name differs from the package).
    for package, activity in (
        ("com.mi.android.globalFileexplorer",
         "com.android.fileexplorer.FileExplorerTabActivity"),
        ("com.google.android.documentsui",
         "com.android.documentsui.files.FilesActivity"),
        ("com.android.documentsui",
         "com.android.documentsui.files.FilesActivity"),
    ):
        code, _ = _adb_shell(
            ["am", "start", "-n", "{0}/{1}".format(package, activity)],
            serial=serial,
        )
        if code == 0:
            return True
    # Last resort: whatever handles the MAIN/LAUNCHER intent for a known file app.
    for package in ("com.mi.android.globalFileexplorer", "com.android.vending"):
        adb = adb_path()
        if not adb:
            return False
        command = [adb]
        if serial:
            command += ["-s", serial]
        command += ["shell", "monkey", "-p", package, "-c",
                    "android.intent.category.LAUNCHER", "1"]
        try:
            finished = subprocess.run(command, capture_output=True, text=True, timeout=20,
                                      **_no_window())
        except (OSError, subprocess.SubprocessError):
            continue
        if finished.returncode == 0:
            return True
    return False


def wait_until_termux_installed(serial: str = "", timeout: float = 180.0,
                                poll: float = 2.0,
                                on_tick: Optional[Callable[[], None]] = None) -> bool:
    """Wait until Termux appears on the phone (e.g. after a manual APK install).

    :param serial: Which device, when several are plugged in.
    :param timeout: How long to wait, in seconds.
    :param poll: Seconds between checks.
    :param on_tick: Called each poll while still waiting.
    :return: Whether Termux showed up in time.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if termux_installed(serial):
            return True
        if on_tick is not None:
            on_tick()
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.2, poll))


def uninstall_package(package: str, serial: str = "") -> Tuple[bool, str]:
    """Remove a package from the phone with ``adb uninstall``.

    Needed before installing GitHub Termux over a Play Store build: the two are
    signed differently, so ``adb install -r`` refuses the update.

    :param package: Android package name.
    :param serial: Which device, when several are plugged in.
    :return: ``(success, message)``.
    """
    adb = adb_path()
    if not adb:
        return False, "adb is not installed"
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["uninstall", package]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=120,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = ((finished.stdout or "") + "\n" + (finished.stderr or "")).strip()
    if finished.returncode == 0:
        return True, output.splitlines()[-1] if output else "Success"
    return False, output or "adb uninstall failed"


def install_apk(apk: Path, serial: str = "") -> Tuple[bool, str]:
    """Install an APK on the phone with ``adb install -r``.

    :param apk: Local APK path.
    :param serial: Which device, when several are plugged in.
    :return: ``(success, message)``.
    """
    adb = adb_path()
    if not adb:
        return False, "adb is not installed"
    if not apk.is_file():
        return False, "the APK is missing"
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["install", "-r", str(apk)]
    log.info("Installing APK on the phone: %s", apk.name)
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=300,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = ((finished.stdout or "") + "\n" + (finished.stderr or "")).strip()
    if finished.returncode == 0 and "Success" in output.replace("\r", ""):
        return True, output.splitlines()[-1] if output else "Success"
    # Some adb builds only print "Success" on stdout with code 0.
    if finished.returncode == 0:
        return True, output.splitlines()[-1] if output else "Success"
    return False, output or "adb install failed"


def install_official_termux(serial: str = "",
                            workspace: Optional[Path] = None,
                            on_status: Optional[Callable[[str], None]] = None,
                            replace_existing: bool = False,
                            keep_apk: bool = False
                            ) -> Tuple[bool, str, Optional[Path]]:
    """Download the GitHub Termux APK and install it on the phone.

    On Xiaomi/HyperOS ``adb install`` often fails with
    ``INSTALL_FAILED_VERIFICATION_FAILURE`` until *Install via USB* is enabled.
    In that case the APK is also copied to ``/sdcard/Download`` for a manual
    tap-install, and the third return value is the local APK path when
    ``keep_apk`` is true so the caller can retry without re-downloading.

    :param serial: Which device, when several are plugged in.
    :param workspace: Directory for the download; a temp folder when omitted.
    :param on_status: Progress keys: ``downloading``, ``uninstalling``,
        ``installing``, ``manual_apk``.
    :param replace_existing: When true, uninstall ``com.termux`` first (required
        when replacing the Play Store build).
    :param keep_apk: Leave the downloaded APK on disk for a later retry.
    :return: ``(success, message, apk_path or None)``.
    """
    import tempfile

    own_dir = workspace is None and not keep_apk
    root = workspace or Path(tempfile.mkdtemp(prefix="clipster-termux-"))
    apk = root / "termux-github.apk"
    try:
        if not apk.is_file() or apk.stat().st_size < 1000:
            download_termux_apk(apk, on_status=on_status)
        if replace_existing and termux_installed(serial):
            if on_status is not None:
                on_status("uninstalling")
            ok, message = uninstall_package(TERMUX_PACKAGE, serial=serial)
            if not ok:
                return False, message, apk if keep_apk else None
        if on_status is not None:
            on_status("installing")
        ok, message = install_apk(apk, serial=serial)
        if ok:
            return True, message, apk if keep_apk else None
        if is_verification_failure(message):
            if on_status is not None:
                on_status("manual_apk")
            pushed, remote = push_apk_for_manual_install(apk, serial=serial)
            open_file_manager(serial)
            detail = message
            if pushed:
                detail = "{0}\n\nAPK on phone: {1}".format(message, remote)
            return False, "verification_blocked:{0}".format(detail), apk
        return False, message, apk if keep_apk else None
    except OSError as exc:
        return False, str(exc), apk if (keep_apk and apk.is_file()) else None
    finally:
        if not keep_apk:
            try:
                apk.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
            if own_dir:
                try:
                    root.rmdir()
                except OSError:  # pragma: no cover
                    pass


def wait_for_device(timeout: float = 60.0, poll: float = 1.0,
                    on_state: Optional[Callable[[List[Device]], None]] = None) -> List[Device]:
    """Watch for a phone until one is ready or the time runs out.

    :param timeout: How long to keep looking, in seconds.
    :param poll: Seconds between two looks.
    :param on_state: Called with every list of devices seen.
    :return: The devices at the end.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    seen: List[Device] = []
    while True:
        seen = devices()
        if on_state is not None:
            on_state(seen)
        if any(device.ready for device in seen) or time.monotonic() >= deadline:
            return seen
        time.sleep(max(0.1, poll))


def summarise(found: Iterable[Device]) -> Tuple[str, Optional[Device]]:
    """Say what the current situation means for the user.

    :param found: What :func:`devices` returned.
    :return: ``(state key, the usable device or None)``. The key is one of
        ``no_adb``, ``none``, ``unauthorised``, ``offline``, ``ready``.
    """
    listed = list(found)
    if adb_path() is None:
        return "no_adb", None
    ready = next((device for device in listed if device.ready), None)
    if ready is not None:
        return "ready", ready
    if any(device.needs_confirmation for device in listed):
        return "unauthorised", None
    if any(device.state == STATE_OFFLINE for device in listed):
        return "offline", None
    return "none", None
