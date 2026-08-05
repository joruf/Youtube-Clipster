"""Talking to a phone plugged into this computer, over ``adb``.

Used by the "Install on Android" wizard: find the phone, say what still has to
be tapped on it, hand the program over, and show how far the transfer got.

One constraint shapes the whole flow: **adb cannot write into Termux.**
``adb shell`` runs as the ``shell`` user, and Termux keeps its home inside its own
private app storage, which no other user may enter. So the transfer goes to the
shared storage every app can read, and the last step - unpacking and installing -
has to happen inside Termux itself.

*Where* that line runs cannot be moved. Who types it can: :func:`run_on_phone`
brings Termux to the front and sends the command as keystrokes, so nobody has to
key a 130-character shell command into a phone keyboard. Keystrokes go to whatever
holds the focus, which is why nothing is ever typed without checking that Termux
really is the app on screen.

Nothing here touches the interface; the wizard does the talking.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from . import paths
from .logging_setup import get_logger
from .shortcuts import _no_window

log = get_logger(__name__)

#: Where the bundle is put on the phone: readable by Termux, no root needed.
REMOTE_DIR = "/sdcard/Download"

#: Name of the archive handed to the phone.
BUNDLE_NAME = "youtube-clipster-android.tar.gz"

#: What winget calls Google's Android SDK platform tools, which contain adb.
#: Windows has no distribution repository to take adb from, so this is the only
#: automatic route there - and it comes with Google's own licence.
WINGET_PACKAGE = "Google.PlatformTools"

#: Where those terms can be read before accepting them.
SDK_TERMS_URL = "https://developer.android.com/studio/terms"

#: Directories that must never travel: private data, build leftovers, history.
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                        ".mypy_cache", "node_modules", ".idea", ".vscode"})

#: Files that must never travel - the configuration holds the remote token.
_SKIP_FILES = frozenset({"config.json", "history.json", "discover_taste.json",
                         "youtube-clipster.log"})

#: ``adb push`` prints its progress like ``[ 42%] /sdcard/Download/...``.
_PROGRESS = re.compile(r"\[\s*(\d{1,3})%\]")

#: The app that has to be on screen before anything is typed into it.
TERMUX_PACKAGE = "com.termux"

#: Pulls ``com.termux/com.termux.app.TermuxActivity`` out of a dumpsys line.
_PACKAGE = re.compile(r"([a-zA-Z][\w.]*\.[\w.]+)/[\w.$]+")

#: What a device line of ``adb devices`` means for the user.
STATE_READY = "device"
STATE_UNAUTHORISED = "unauthorized"
STATE_OFFLINE = "offline"


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


def push(bundle: Path, serial: str = "",
         on_progress: Optional[Callable[[int], None]] = None,
         remote_dir: str = REMOTE_DIR) -> Tuple[bool, str]:
    """Copy the archive to the phone's shared storage.

    :param bundle: The archive to send.
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
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["push", str(bundle), "{0}/{1}".format(remote_dir.rstrip("/"), bundle.name)]
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
        return True, "{0}/{1}".format(remote_dir.rstrip("/"), bundle.name)
    return False, " | ".join(tail) or "adb push failed"


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


def press_enter(serial: str = "") -> bool:
    """Press the return key on the phone.

    :param serial: Which device, when several are plugged in.
    :return: Whether adb accepted it.
    """
    adb = adb_path()
    if not adb:
        return False
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "input", "keyevent", "66"]      # KEYCODE_ENTER
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Return could not be pressed: %s", exc)
        return False
    return finished.returncode == 0


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
                 on_status: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """Open Termux on the phone and type the command into it.

    This exists so nobody has to key a 130-character shell command into a phone
    keyboard. It is still Termux that runs it - that part cannot move to the PC,
    see the module docstring - but the typing can.

    The foreground check is not politeness: ``input text`` goes to whatever has
    focus, so if Termux never comes up, this refuses rather than typing a shell
    command into whatever app happens to be open.

    :param command: The line to run on the phone.
    :param serial: Which device, when several are plugged in.
    :param on_status: Called with a short progress key: ``opening``, ``typing``.
    :return: ``(success, reason)``. The reason names what failed, or is empty.
    """
    if not typeable(command):
        return False, "untypeable"
    if on_status is not None:
        on_status("opening")
    if not open_termux(serial):
        return False, "termux_missing"
    if not wait_for_termux(serial):
        return False, "termux_not_open"
    if on_status is not None:
        on_status("typing")
    if not type_text(command, serial):
        return False, "typing_failed"
    if not press_enter(serial):
        return False, "typing_failed"
    log.info("The install command was typed into Termux on the phone.")
    return True, ""


def install_command(bundle_name: str = BUNDLE_NAME, remote_dir: str = REMOTE_DIR) -> str:
    """Return the one line to run inside Termux.

    This step cannot be done from here: ``adb`` may not enter Termux's private
    storage, so Termux has to fetch the archive out of the shared folder itself.

    :param bundle_name: Name of the transferred archive.
    :param remote_dir: Where it was put.
    :return: A single shell command.
    """
    return (
        "termux-setup-storage; pkg install -y tar && "
        "tar -xzf {0}/{1} -C ~ && "
        "cd ~/youtube-clipster && bash install-android.sh"
    ).format(remote_dir.rstrip("/"), bundle_name)


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
