"""Talking to a phone plugged into this computer, over ``adb``.

Used by the "Install on Android" wizard: find the phone, say what still has to
be tapped on it, hand the program over, and show how far the transfer got.

One constraint shapes the whole flow: **adb cannot write into Termux.**
``adb shell`` runs as the ``shell`` user, and Termux keeps its home inside its own
private app storage, which no other user may enter. So the transfer goes to the
shared storage every app can read, and the last step - unpacking and installing -
happens inside Termux itself. That is one line to run there, and it cannot be
avoided from this side.

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

#: Directories that must never travel: private data, build leftovers, history.
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                        ".mypy_cache", "node_modules", ".idea", ".vscode"})

#: Files that must never travel - the configuration holds the remote token.
_SKIP_FILES = frozenset({"config.json", "history.json", "discover_taste.json",
                         "youtube-clipster.log"})

#: ``adb push`` prints its progress like ``[ 42%] /sdcard/Download/...``.
_PROGRESS = re.compile(r"\[\s*(\d{1,3})%\]")

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
    return None


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
    command += ["shell", "monkey", "-p", "com.termux", "-c",
                "android.intent.category.LAUNCHER", "1"]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=20,
                                  **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Termux could not be started: %s", exc)
        return False
    return finished.returncode == 0


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
